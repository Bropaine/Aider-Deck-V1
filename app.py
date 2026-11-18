"""Aider Deck. One local Flask server owns each workspace's long-lived PTY."""

import argparse
import atexit
import json
import os
from pathlib import Path
import string
import subprocess
import sys
from datetime import datetime, timezone
from threading import RLock
import time
from urllib.parse import urlsplit
from urllib.request import urlopen
import webbrowser

from flask import Flask, abort, jsonify, render_template, request
from flask_sock import Sock
from simple_websocket import ConnectionClosed
from werkzeug.exceptions import HTTPException
from werkzeug.serving import make_server

from deck import aider_config as config
from deck import envfile, files, git_service as git
from deck import reasoning_capabilities
from deck.attachment_context import attachment_warning, context_budget, text_batch_estimate
from deck.attachment_validation import validated_files
from deck.config_routes import config_bp
from deck.git_safety_routes import safety_bp
from deck.model_install import install_bp, require_installed
from deck.ollama_routes import live_context, ollama_bp
from deck.sessions import AiderSession
from deck.state import State

APP_ROOT = Path(__file__).resolve().parent


class Deck:
    def __init__(self, state_path, prompts):
        self.state = State(state_path, prompts)
        self.sessions = {}
        self.locks = {}
        self.guard = RLock()
        self.baselines = {}
        self.loaded_models = {}
        self.reasoning_routes = {}
        self.branch_cache = {}

    def lock(self, wid):
        with self.guard:
            return self.locks.setdefault(wid, RLock())

    def workspace(self, wid):
        return Path(self.state.entry(wid)["path"])

    def session(self, wid):
        root = self.workspace(wid)
        with self.guard:
            if wid not in self.sessions:
                self.sessions[wid] = AiderSession(root)
            return self.sessions[wid]

    def start(self, wid, restart, *, expected_model=None):
        with self.lock(wid):
            root = self.workspace(wid)
            session = self.session(wid)
            if session.snapshot()["status"] == "live" and not restart:
                return
            config.validate_interactive_launch(root)
            options = config.launch_values(root)
            effort_routes = reasoning_capabilities.routing(root, options)
            signature = config.config_fingerprint(root)
            models = config.registrations(root)
            format_errors = config.model_format_errors(models, options.get("alias"))
            if issue := format_errors.get(options.get("model")):
                raise ValueError(issue)
            secrets = config.secret_values(root)
            environment, launch_files = config.launch_environment(
                root, options.get("architect")
            )
            previous = (
                session
                if expected_model and session.snapshot()["status"] == "live"
                else None
            )
            if previous:
                session = AiderSession(root)
                session.resize(previous.cols, previous.rows)
            try:
                (session.restart if restart and not previous else session.start)(
                    options,
                    environment,
                    secrets,
                    model_format_errors=format_errors,
                    launch_files=launch_files,
                )
                if expected_model:
                    try:
                        session.wait_for_model_or_question(expected_model)
                        if previous:
                            previous.close()
                    except Exception:
                        session.close()
                        raise
                if previous:
                    with self.guard:
                        self.sessions[wid] = session
            except BaseException:
                if launch_files:
                    launch_files.cleanup()
                raise
            self.baselines[wid] = signature
            self.loaded_models[wid] = models
            self.reasoning_routes[wid] = effort_routes

    def branch(self, wid):
        stamp, info = self.branch_cache.get(wid, (0, {}))
        if time.monotonic() - stamp >= 5:
            try:
                state = git.status(self.workspace(wid), with_log=False)
                info = {k: state[k] for k in ("branch", "dirty")}
            except git.GitError:
                info = dict(branch=None, dirty=None)
            self.branch_cache[wid] = time.monotonic(), info
        return info

    def close(self):
        for session in list(self.sessions.values()):
            session.close()


def data():
    obj = request.get_json()
    if not isinstance(obj, dict):
        abort(400, "Expected a JSON object.")
    return obj


def clean_text(root, text):
    return config.replace_secrets(text, config.secret_values(root))


def create_app(state_path=None, prompt_dir=None):
    app = Flask(__name__)
    app.config.update(
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,
        SOCK_SERVER_OPTIONS={"max_message_size": 2 * 1024 * 1024},
    )
    prompt_library = Path(prompt_dir or APP_ROOT / "prompts")
    prompt_library.mkdir(parents=True, exist_ok=True)
    deck = Deck(
        Path(state_path or APP_ROOT / "deck-state.json"),
        prompt_library,
    )
    app.extensions["deck"] = deck
    app.extensions["ollama_services"] = {}
    app.extensions["ollama_activations"] = set()
    atexit.register(deck.close)
    sock = Sock(app)
    app.register_blueprint(config_bp)
    app.register_blueprint(safety_bp)
    app.register_blueprint(ollama_bp)
    app.register_blueprint(install_bp)

    @app.before_request
    def local_client():
        # Prevent DNS rebinding, cross-origin forms and WebSocket hijacking.
        host = urlsplit("//" + request.host).hostname
        if host not in ("localhost", "127.0.0.1"):
            abort(403)
        origin = request.headers.get("Origin")
        if origin and (
            urlsplit(origin).netloc != request.host
            or urlsplit(origin).scheme not in ("http", "https")
        ):
            abort(403)
        if request.method in ("POST", "PUT", "DELETE", "PATCH") and not request.is_json:
            abort(415, "Use a JSON request.")

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(Exception)
    def failed(exc):
        if isinstance(exc, HTTPException):
            return jsonify(error=exc.description), exc.code
        if isinstance(
            exc, (OSError, ValueError, RuntimeError, subprocess.SubprocessError)
        ):
            message = str(exc)
            wid = (request.view_args or {}).get("wid")
            if wid:
                try:
                    message = clean_text(deck.workspace(wid), message)
                except Exception:
                    message = "Could not read workspace configuration."
            return jsonify(error=message or "Operation failed."), 400
        return jsonify(error="Unexpected server error."), 500

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return {"app": "aider-deck", "status": "ready"}

    @app.route("/api/workspaces", methods=["GET", "POST"])
    def workspaces():
        def row(wid):
            session = deck.sessions.get(wid)
            return dict(
                id=wid,
                path=str(deck.workspace(wid)),
                status=session.snapshot()["status"] if session else "idle",
            )

        if request.method == "POST":
            path = data().get("path")
            if not isinstance(path, str) or not path.strip():
                abort(400, "Enter a workspace path.")
            return row(deck.state.open(path))
        with deck.state.guard:
            return dict(
                workspaces=[
                    row(wid) for wid in reversed(deck.state.data["workspaces"])
                ],
                active=deck.state.data["active"],
            )

    @app.get("/api/browse")
    def browse():
        directory = (
            Path(request.args.get("path") or APP_ROOT).expanduser().resolve(strict=True)
        )
        if not directory.is_dir():
            abort(400, "Choose a directory.")
        dirs = sorted(
            (p for p in directory.iterdir() if p.is_dir() and p.name != ".git"),
            key=lambda p: p.name.casefold(),
        )
        drives = (
            [d + ":/" for d in string.ascii_uppercase if os.path.isdir(d + ":/")]
            if os.name == "nt"
            else ["/"]
        )
        return dict(
            path=str(directory),
            parent=str(directory.parent),
            dirs=[dict(name=p.name, path=str(p)) for p in dirs],
            drives=drives,
        )

    @app.get("/api/<wid>/state")
    def workspace_state(wid):
        root = deck.workspace(wid)
        with deck.lock(wid):
            snapshot = deck.session(wid).snapshot()
            model = snapshot.get("model")
            issue = None
            try:
                reg = config.registration(root, model)
                limit = config.model_limit(root, model) if model else None
                pending = snapshot["status"] == "live" and deck.baselines.get(
                    wid
                ) != config.config_fingerprint(root)
            except (ValueError, OSError):
                reg = dict(model=model, registered=None, edit_format=None, path=None)
                limit, pending = None, None
                issue = "Workspace configuration could not be read. Inspect the files in Settings."
            loaded = deck.loaded_models.get(wid, {}).get(model)
            reg["loaded_at_start"] = bool(loaded and loaded.get("registered"))
            reg["loaded_format"] = loaded.get("edit_format") if loaded else None
            runner = live_context(snapshot, deck.reasoning_routes.get(wid))
            return {
                **snapshot,
                **deck.state.entry(wid),
                "supports_file_groups": True,
                "supports_group_drop": True,
                "supports_ollama": True,
                "supports_model_install": True,
                "supports_attachment_context": True,
                "registration": reg,
                "max_input_tokens": limit,
                "local_context": runner,
                "context_budget": context_budget(snapshot, limit, runner),
                "git": deck.branch(wid),
                "pending": pending,
                "config_error": issue,
            }

    @app.post("/api/<wid>/session/<action>")
    def session_control(wid, action):
        if action not in ("start", "restart"):
            abort(404)
        with deck.lock(wid):
            if action == "restart" or deck.session(wid).snapshot()["status"] != "live":
                require_installed(deck, wid, "launch", expected=data().get("install_route"))
            deck.start(wid, action == "restart")
        return dict(ok=True)

    @app.post("/api/<wid>/prefs")
    def preferences(wid):
        deck.workspace(wid)
        changes = data()
        if "enter_sends" in changes and not isinstance(changes["enter_sends"], bool):
            abort(400, "Invalid composer preference.")
        if "prompt_library" in changes:
            selected = changes["prompt_library"]
            if not isinstance(selected, str) or not selected.strip():
                abort(400, "Choose an existing prompt folder.")
            folder = Path(selected).expanduser().resolve(strict=True)
            if not folder.is_dir():
                abort(400, "Choose an existing folder.")
            changes["prompt_library"] = str(folder)
        if changes.keys() - {"enter_sends", "prompt_library"}:
            abort(400, "Unknown preference.")
        deck.state.preferences(wid, changes)
        return dict(ok=True)

    @app.post("/api/<wid>/mode")
    def mode(wid):
        deck.session(wid).mode(data().get("mode"))
        return dict(ok=True)

    @app.post("/api/<wid>/interrupt")
    def interrupt(wid):
        deck.session(wid).write("\x03")
        return dict(ok=True)

    @app.post("/api/<wid>/send")
    def send(wid):
        body = data()
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            abort(400, "Enter a message.")
        first = text.strip().split(maxsplit=1)[0].lower()
        if first in ("/clear", "/exit", "/quit", "/reset"):
            abort(400, "Use the confirmed Session menu action.")
        if first in ("/add", "/drop", "/read-only"):
            abort(400, "Use the file actions or type directly into Aider.")
        with deck.lock(wid):
            session = deck.session(wid)
            with session.lock:
                if "attachment_ids" in body or "attachment_session" in body:
                    session.attachments.require_session(body.get("attachment_session"))
                    if body.get("attachment_ids") != list(session.attachments.items):
                        raise ValueError("The attachments changed. Review them and send again.")
                session.send(text, body.get("once"))
        deck.state.remember(wid, clean_text(deck.workspace(wid), text))
        return dict(ok=True)

    @app.post("/api/<wid>/attachments")
    def attach_files(wid):
        body = data()
        with deck.lock(wid):
            root, session = deck.workspace(wid), deck.session(wid)
            snapshot = session.snapshot()
            generation, model = body.get("session"), snapshot.get("model")
            session.attachments.require_session(generation)
            if snapshot["status"] != "live":
                abort(400, "Start a live Aider session first.")
            if "model" in body and body["model"] != model:
                abort(409, "The live model changed. Choose the files again.")
            uploads = validated_files(root, body.get("files"))
            estimate, has_images = text_batch_estimate(uploads)
            limit = runner = None
            if model and estimate is not None:
                try:
                    limit = config.model_limit(root, model)
                except (ValueError, OSError):
                    pass  # Show the available sources even with malformed local metadata.
                runner = live_context(snapshot, deck.reasoning_routes.get(wid))
            with session.lock:
                # Terminal input and replies can change the live model and usage
                # while checking the files, reading metadata or waiting for review.
                snapshot = session.snapshot()
                session.attachments.require_session(generation)
                if snapshot["status"] != "live" or model != snapshot.get("model"):
                    abort(409, "The active session or model changed. Choose the files again.")
                session.attachments.check_quota(uploads)
                budget = context_budget(snapshot, limit, runner)
                changing = snapshot["input_state"] != "ready" or bool(session.pending) or any(
                    item["status"] != "attached" for item in snapshot["attachments"]
                )
                warning = attachment_warning(budget, estimate, has_images, changing)
                if warning:
                    warning["confirmation"] = session.attachments.confirmation_for(uploads, warning)
                if "context_confirmation" in body:
                    if not warning or warning["confirmation"] != body["context_confirmation"]:
                        abort(409, "The files or available context changed. Choose the files again to review them.")
                elif warning:
                    if "model" not in body:
                        abort(409, "Refresh Deck to review the attachment context warning.")
                    return dict(context_review=warning)
                rows = session.attach(generation, uploads)
        return dict(attachments=rows)

    @app.post("/api/<wid>/attachments/<identifier>/remove")
    def remove_attachment(wid, identifier):
        with deck.lock(wid):
            rows = deck.session(wid).remove_attachment(data().get("session"), identifier)
        return dict(attachments=rows)

    @app.post("/api/<wid>/command")
    def command(wid):
        body = data()
        line = body.get("line", "")
        if (
            not isinstance(line, str)
            or not line.startswith("/")
            or any(ord(c) < 32 for c in line)
        ):
            abort(400, "Enter one Aider command.")
        first = line.split(maxsplit=1)[0]
        if first in ("/clear", "/reset", "/exit") and body.get("confirmed") is not True:
            abort(400, "Confirm the session action.")
        if first not in set(
            "/settings /ls /tokens /map /map-refresh /diff /undo /lint /test /run /git /web /copy-context /save /load /clear /reset /exit /commit /models /multiline-mode".split()
        ):
            abort(400, "Use the dedicated control or the terminal for this command.")
        deck.session(wid).command(line)
        return dict(ok=True)

    @app.get("/api/<wid>/tree")
    def tree(wid):
        return dict(
            entries=files.tree(
                deck.workspace(wid),
                request.args.get("path", ""),
                request.args.get("ignored") in ("1", "true"),
            )
        )

    @app.post("/api/<wid>/files")
    def file_action(wid):
        body = data()
        root = deck.workspace(wid)
        action = body.get("action")
        session = deck.session(wid)
        grouped = "paths" in body
        from_chat = bool(body.get("from_chat"))
        if grouped and ("path" in body or (from_chat and action != "drop")):
            raise ValueError("Send workspace-relative paths for a file selection.")
        selection = body.get("paths" if grouped else "path")
        if from_chat or action == "drop":
            try:
                repo = git.repository(root)
            except git.GitError:
                repo = root
        if from_chat or (grouped and action == "drop"):
            names = selection if grouped else [selection]
            if isinstance(names, list):
                selection = [
                    files.confined(repo, name).relative_to(root).as_posix()
                    for name in names
                ]
        builder = (
            files.selected_files_command if grouped or from_chat else files.file_command
        )
        if action == "drop":
            with session.lock:
                state = session.snapshot()
                line = builder(
                    root,
                    action,
                    selection,
                    attached=files.chat_file_paths(repo, state["files"]),
                    allow_missing_read_only=state["input_state"] == "ready" and not session.pending,
                )
                session.command(line)
        else:
            session.command(builder(root, action, selection))
        return dict(ok=True)

    @app.get("/api/<wid>/git")
    def git_status(wid):
        root = deck.workspace(wid)
        info = git.status(root)
        deck.branch_cache[wid] = (
            time.monotonic(),
            {k: info[k] for k in ("branch", "dirty")},
        )
        secrets = config.secret_values(root)
        for entry in info["log"]:
            entry["subject"] = config.replace_secrets(entry["subject"], secrets)
        return info

    @app.get("/api/<wid>/git/diff")
    def git_diff(wid):
        root = deck.workspace(wid)
        result = git.diff(
            root,
            request.args.get("path"),
            request.args.get("staged") in ("1", "true"),
            request.args.get("commit"),
        )
        result["diff"] = clean_text(root, result["diff"])
        return result

    @app.post("/api/<wid>/git/<action>")
    def git_change(wid, action):
        body = data()
        root = deck.workspace(wid)
        with deck.lock(wid):
            result = git.change(
                root,
                action,
                body.get("path"),
                body.get("confirmed"),
                body.get("message"),
            )
            deck.branch_cache.pop(wid, None)
            return dict(output=clean_text(root, result))

    def library(wid):
        return Path(deck.state.entry(wid)["prefs"]["prompt_library"]).resolve(
            strict=True
        )

    def prompt_file(wid, name):
        if (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or Path(name).suffix.lower() not in (".md", ".txt")
        ):
            raise ValueError("Use a Markdown or text filename.")
        path = files.confined(library(wid), name)
        if (library(wid) / name).is_symlink():
            raise ValueError("Choose a regular prompt file.")
        return path

    @app.get("/api/<wid>/prompts")
    def prompts(wid):
        folder = library(wid)
        entries = []
        secrets = config.secret_values(deck.workspace(wid))
        for path in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
            if (
                path.is_symlink()
                or not path.is_file()
                or path.suffix.lower() not in (".md", ".txt")
            ):
                continue
            with path.open(encoding="utf-8-sig", errors="replace") as f:
                preview = f.readline(180).strip()
            entries.append(
                dict(
                    name=path.name,
                    preview=config.replace_secrets(preview, secrets),
                    modified=datetime.fromtimestamp(
                        path.stat().st_mtime, timezone.utc
                    ).isoformat(),
                )
            )
        return dict(folder=str(folder), prompts=entries)

    @app.route("/api/<wid>/prompts/item", methods=["GET", "POST", "DELETE"])
    def prompt(wid):
        body = request.args if request.method == "GET" else data()
        path = prompt_file(wid, body.get("name"))
        with deck.lock(wid):
            if request.method == "GET":
                return (
                    dict(
                        name=path.name,
                        text=clean_text(deck.workspace(wid), envfile.read_text(path)),
                    )
                    if path.is_file()
                    else abort(404)
                )
            if request.method == "DELETE":
                if body.get("confirmed") is not True:
                    abort(400, "Confirm prompt deletion.")
                path.unlink()
            else:
                if not isinstance(body.get("text"), str):
                    abort(400, "Enter prompt text.")
                if path.exists() and body.get("overwrite") is not True:
                    abort(409, "A prompt with this name exists.")
                envfile.atomic_write(path, body["text"])
        return dict(ok=True)

    @sock.route("/ws/<wid>")
    def terminal(ws, wid):
        session = deck.session(wid)
        cursor = None
        try:
            while True:
                if deck.sessions.get(wid) is not session:
                    return
                cursor, output, reset = session.read_since(cursor, timeout=0.04)
                if output or reset:
                    ws.send(
                        json.dumps(
                            dict(type="output", data=output, reset=reset, cursor=cursor)
                        )
                    )
                message = ws.receive(timeout=0.03)
                if message:
                    packet = json.loads(message)
                    if packet.get("type") == "resize":
                        session.resize(int(packet["cols"]), int(packet["rows"]))
                    elif packet.get("type") == "input":
                        session.write(packet["data"])
        except (ConnectionClosed, ValueError, KeyError, TypeError, RuntimeError):
            return

    return app


def open_browser(url):
    if sys.platform == "darwin":
        # Browser startup must not hold the local server before it starts serving.
        try:
            subprocess.Popen(["/usr/bin/open", url])
        except OSError:
            print(f"Open {url} in your browser.", flush=True)
    else:
        webbrowser.open(url)


def main():
    parser = argparse.ArgumentParser(description="Aider Deck local console")
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("AIDER_DECK_PORT", "5177"))
    )
    parser.add_argument(
        "--open", action="store_true", help="Open the local console in your browser."
    )
    parser.add_argument(
        "--data-dir", type=Path, help="Store Deck preferences and prompts outside the app directory."
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Choose a port between 1 and 65535.")
    url = f"http://127.0.0.1:{args.port}"
    if args.open:
        try:
            with urlopen(url + "/api/health", timeout=0.5) as response:
                already_running = (
                    json.loads(response.read(512)).get("app") == "aider-deck"
                )
        except (OSError, ValueError, AttributeError):
            already_running = False
        if already_running:
            open_browser(url)
            return
    # Each server owns its PTYs and a single state writer. Parallel instances
    # must not overwrite one another's recent folders, history or preferences.
    state_name = (
        "deck-state.json" if args.port == 5177 else f"deck-state-{args.port}.json"
    )
    data_dir = args.data_dir.expanduser().resolve() if args.data_dir else APP_ROOT
    data_dir.mkdir(parents=True, exist_ok=True)
    with make_server(
        "127.0.0.1", args.port, create_app(data_dir / state_name, data_dir / "prompts"), threaded=True
    ) as server:
        print(f"Aider Deck is listening at {url}. Press Ctrl+C to stop.", flush=True)
        if args.open:
            open_browser(url)
        server.serve_forever()


if __name__ == "__main__":
    main()
