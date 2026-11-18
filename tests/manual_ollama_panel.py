"""Browser smoke for Ollama management. --real pulls a small chat model into a temporary store."""

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import urlopen

from browser_acceptance import create_app, serve, wait_for
from flask import Flask, Response, jsonify, request
from playwright.sync_api import expect, sync_playwright
from ruamel.yaml import YAML
from werkzeug.serving import make_server

FIXTURE = "deck-chat:tiny"
SECOND = "deck-chat:alternate"
REAL = "smollm2:135m-instruct-q4_0"


@contextmanager
def scratch_directory():
    directory = tempfile.TemporaryDirectory(prefix="deck-ollama-panel-")
    try:
        yield Path(directory.name)
    finally:
        # Windows driver and runner cache handles may close just after the
        # managed service and browser processes exit.
        for attempt in range(16):
            try:
                directory.cleanup()
                break
            except PermissionError:
                if attempt == 15:
                    raise
                time.sleep(1)


def fixture_daemon():
    """Disposable executable for the fixture run. Deck starts it through its normal launcher."""
    home = Path.home()
    app = Flask("local-ollama-fixture")
    installed = {SECOND}
    loaded = set()
    pulls = []

    def model_rows(names):
        return [
            {"name": name, "size": 128, "capabilities": ["completion"]}
            for name in sorted(names)
        ]

    @app.get("/api/tags")
    def tags():
        if (home / "unavailable").exists():
            return jsonify(error="Fixture service unavailable"), 503
        return jsonify(models=model_rows(installed))

    @app.get("/api/ps")
    def ps():
        models = model_rows(loaded)
        if not (home / "omit-window").exists():
            for row in models:
                row["context_length"] = 4096 if row["name"] == FIXTURE else 8192
        return jsonify(models=models)

    @app.post("/api/show")
    def show():
        return jsonify(
            capabilities=["completion"],
            model_info={"general.architecture": "llama", "llama.context_length": 8192},
        )

    @app.post("/api/pull")
    def pull():
        data = request.get_json()
        assert data["model"] == FIXTURE and data["stream"] is True
        pulls.append(data["model"])
        first = len(pulls) == 1

        def stream():
            def emit(**fields):
                return json.dumps(fields) + "\n"

            def await_marker(name):
                deadline = time.monotonic() + 40
                while not (home / name).exists():
                    if time.monotonic() > deadline:
                        raise RuntimeError(
                            "Timed out waiting for browser to observe fixture progress"
                        )
                    time.sleep(0.05)

            yield emit(status="pulling manifest")
            await_marker("release-manifest")
            yield emit(
                status="pulling layer",
                digest="sha256:example",
                completed=1024,
                total=4096,
            )
            await_marker("release-layer")
            if first:
                yield emit(error="Interrupted fixture download")
            else:
                installed.add(FIXTURE)
                yield emit(status="success")

        return Response(stream(), mimetype="application/x-ndjson")

    @app.post("/api/generate")
    def generate():
        data = request.get_json()
        assert data["model"] in installed and not data.get("prompt")
        loaded.add(data["model"])
        return jsonify(model=data["model"], response="", done=True, done_reason="load")

    @app.post("/api/chat")
    def chat():
        # LiteLLM sends the Ollama JSON payload without a Content-Type header.
        data = json.loads(request.get_data())
        tag = data["model"]
        assert tag in loaded
        (home / "chat-model").write_text(tag)
        return jsonify(
            model=tag,
            message={"role": "assistant", "content": "DECK_OLLAMA_LOCAL_REPLY"},
            created_at="2026-01-01T00:00:00Z",
            done=True,
            done_reason="stop",
            prompt_eval_count=20,
            eval_count=8,
        )

    server = make_server(
        "127.0.0.1", urlsplit(os.environ["OLLAMA_HOST"]).port, app, threaded=True
    )
    server.serve_forever()


def button(scope, name):
    return scope.get_by_role("button", name=name, exact=True)


def stop_owned_daemon(service, endpoint):
    process = service._process
    assert service.endpoint == endpoint
    if process and process.poll() is None:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        process.wait(timeout=15)


def check_tracker(page, model, endpoint, home, real, app, wid):
    client = app.test_client()
    state_url = f"/api/{wid}/state"
    meter = page.locator("#context-meter")
    usage = page.locator("#usage")
    passive = client.get(state_url).json
    assert passive["tokens"]["stale"]
    assert passive["reply_tokens"]["model"] == model
    expect(meter).to_be_visible(timeout=20000)
    expect(usage).to_contain_text("Last reply · sent + received")
    expect(page.locator("#context")).to_contain_text(f"~{passive['reply_tokens']['used']:,} /")
    tree = page.locator("#tree")
    attachments = page.locator("#chat-files")
    tree.get_by_title("editable.txt", exact=True).click()
    reference = tree.locator(".tree-row").filter(
        has=page.get_by_title("reference.txt", exact=True)
    )
    reference.get_by_title("Add read-only").click()
    expect(attachments.locator(".file-name")).to_have_count(2, timeout=30000)
    page.locator("#refresh-tokens").click()
    estimates = attachments.locator(".file-token-estimate")
    expect(estimates).to_have_count(2, timeout=30000)

    state = client.get(state_url).json
    tokens = state["tokens"]["data"]
    counts = {
        (row["path"], row["read_only"]): row["tokens"] for row in tokens["file_rows"]
    }
    assert set(counts) == {("editable.txt", False), ("reference.txt", True)}
    for (path, _), count in counts.items():
        assert count > 0
        row = attachments.locator(".file-row").filter(
            has=page.get_by_text(path, exact=True)
        )
        expect(row.locator(".file-token-estimate")).to_have_text(f"~{count:,} tokens")

    with urlopen(endpoint + "/api/ps", timeout=5) as response:
        rows = json.load(response)["models"]
    chosen = next(row for row in rows if "ollama_chat/" + row["name"] == model)
    window = chosen.get("context_length")
    expect(usage).to_contain_text("Aider token estimate")
    expect(usage.get_by_text("Largest attachments", exact=True)).to_be_visible()
    largest = usage.locator(".stat-row").filter(
        has=page.get_by_text("reference.txt", exact=True)
    )
    expect(largest).to_contain_text("~" + f"{counts[('reference.txt', True)]:,}")
    if window:
        assert state["local_context"]["model"] == model
        assert state["local_context"]["window"] == window
        expect(meter).to_be_visible()
        expect(page.locator("#context")).to_contain_text(
            f"{tokens['used']:,} / {window:,}"
        )
        expect(usage).to_contain_text("Ollama loaded window")
        remaining = usage.locator(".stat-row").filter(
            has=page.get_by_text("Estimated tokens remaining", exact=True)
        )
        expect(remaining).to_contain_text(f"{max(0, window - tokens['used']):,}")
        assert (
            abs(meter.evaluate("(el) => el.value") - min(1, tokens["used"] / window))
            < 0.000001
        )
        # An actual runner's allocation drives the upload warning too. This
        # selection is cancelled before Aider gets the file or a new prompt.
        free_tokens = max(0, window - tokens["used"])
        sample = b"example " * (3 * (free_tokens + 128) // 8 + 1)
        with page.expect_file_chooser() as chooser:
            page.locator("#attach-files").click()
        with page.expect_response("**/attachments") as response:
            chooser.value.set_files({"name": "reading.txt", "mimeType": "text/plain", "buffer": sample})
        review = response.value.json()["context_review"]
        assert review["model"] == model and review["remaining_tokens"] == free_tokens
        assert review["window_tokens"] == window and review["estimated_tokens"] > free_tokens
        warning = page.get_by_role("region", name="Attachment context warning")
        expect(warning).to_contain_text(model)
        warning.get_by_role("button", name="Cancel", exact=True).click()
        expect(warning).to_be_hidden()
        assert not client.get(state_url).json["attachments"]
        print(f"PASS desktop upload warning used the native model's {free_tokens:,} remaining tokens", flush=True)
    else:
        assert state["local_context"].get("model_default") or state["local_context"].get("model_capacity")
        expect(meter).to_be_visible()
    if not real:
        assert window == 4096
        marker = home / "omit-window"
        marker.touch()
        expect(page.locator("#context")).to_contain_text("/ 8,192", timeout=20000)
        expect(page.locator("#context")).to_contain_text("Ollama model capacity")
        expect(meter).to_be_visible()
        expect(remaining).to_contain_text(f"{max(0, 8192 - tokens['used']):,}")
        marker.unlink()
        expect(page.locator("#context")).to_contain_text("/ 4,096", timeout=20000)
        expect(page.locator("#context")).not_to_contain_text("model capacity")
    print(
        "PASS passive reply meter, explicit per-file estimates, and labeled Ollama window sources",
        flush=True,
    )


def exercise(page, url, app, repo, home, endpoint, real):
    deck = app.extensions["deck"]
    wid = deck.state.open(repo)
    session = deck.session(wid)
    tag = REAL if real else FIXTURE
    model = "ollama_chat/" + tag
    page.goto(url)
    button(page, "Ollama").click()
    panel = page.locator("#ollama-panel")
    status = panel.locator("#ollama-service")
    tag_input = panel.get_by_label("Ollama model tag", exact=True)
    row = panel.locator(f'[data-ollama-tag="{tag}"]')
    expect(status).to_contain_text("stopped")
    button(panel, "Start Ollama").click()
    expect(status).to_contain_text("running", timeout=30000)
    expect(panel).to_contain_text(endpoint)
    print("PASS Start Ollama brought up the scratch endpoint from stopped", flush=True)

    for saved in (tag, SECOND, "scratch:remove-me"):
        tag_input.fill(saved)
        button(panel, "Save tag").click()
        expect(button(panel, "Remove " + saved + " from saved")).to_be_visible()
    page.reload()
    button(page, "Ollama").click()
    for saved in (tag, SECOND, "scratch:remove-me"):
        expect(button(panel, "Remove " + saved + " from saved")).to_be_visible()
    button(panel, "Remove scratch:remove-me from saved").click()
    expect(button(panel, "Remove scratch:remove-me from saved")).to_have_count(0)
    print("PASS three saved tags survive reload and selective removal", flush=True)

    if not real:
        button(panel, "Remove " + SECOND + " from saved").click()
        button(panel, "Save " + SECOND).click()
        expect(button(panel, "Remove " + SECOND + " from saved")).to_be_visible()
        (home / "unavailable").touch()
        button(panel, "Refresh Ollama").click()
        expect(panel).to_contain_text("Fixture service unavailable")
        (home / "unavailable").unlink()
        button(panel, "Refresh Ollama").click()
        expect(status).to_contain_text("running")
        expect(panel).not_to_contain_text("Fixture service unavailable")
        print("PASS local service error displayed and recovered on refresh", flush=True)

    tag_input.fill(tag)
    button(panel, "Download model").click()
    job = panel.locator("#ollama-job")
    progress = job.locator("progress")
    if not real:
        expect(job).to_contain_text("pulling manifest", timeout=12000)
        assert not progress.count() or progress.get_attribute("value") is None
        (home / "release-manifest").touch()
        page.wait_for_function("""() => {
            const p = document.querySelector('#ollama-job progress');
            return p && p.position === 0.25;
        }""")
        (home / "release-layer").touch()
        expect(panel).to_contain_text("Interrupted fixture download")
        button(row, "Download " + tag).click()
    expect(row).to_contain_text("Installed", timeout=180000)
    with urlopen(endpoint + "/api/tags", timeout=3) as response:
        assert tag in [item["name"] for item in json.load(response)["models"]]
    print(
        "PASS requested tag installed by Ollama"
        if real
        else "PASS indeterminate manifest, 25% layer, failed download and successful retry",
        flush=True,
    )

    with page.expect_response("**/ollama/use", timeout=200000) as use_result:
        button(panel, "Use in Aider").click()
    assert use_result.value.ok, use_result.value.text()
    wait_for(
        "one-click use preloaded the model and started the real Aider session",
        lambda: (
            session.snapshot()["input_state"] == "ready"
            and session.snapshot()["model"] == model
        ),
        180,
    )
    with urlopen(endpoint + "/api/ps", timeout=5) as response:
        assert tag in [item["name"] for item in json.load(response)["models"]]
    expect(row).to_contain_text("Loaded")
    expect(page.locator("#model")).to_contain_text(tag)
    state = app.test_client().get(f"/api/{wid}/state").json
    assert state["registration"]["loaded_at_start"] and state["model"] == model
    assert (
        state["launch"]["model"] == model
        and session.snapshot()["active_format"] == "diff"
    )
    assert endpoint in (repo / ".env").read_text()
    configured = YAML(typ="safe").load((repo / ".aider.conf.yml").read_text())
    assert all(
        configured[key] == model for key in ("model", "weak-model", "editor-model")
    )
    pid = session.proc.pid
    button(page.locator("#mode-buttons"), "Ask").click()
    wait_for(
        "Ask coder for selected Ollama tag", lambda: session.snapshot()["mode"] == "ask"
    )
    prompt = "What color is a clear daytime sky? Reply with one word."
    page.locator("#prompt").fill(prompt)
    page.locator("#send").click()

    def answered():
        history = repo / ".aider.chat.history.md"
        if not history.exists():
            return False
        tail = history.read_text(encoding="utf-8").partition("#### " + prompt)[2]
        return session.snapshot()["usage"] and any(
            line.strip() and not line.startswith(">") for line in tail.splitlines()
        )

    wait_for(
        "local chat reply and token usage reached the selected Aider session",
        answered,
        180,
    )
    check_tracker(page, model, endpoint, home, real, app, wid)
    if not real:
        assert (home / "chat-model").read_text() == tag
        assert "DECK_OLLAMA_LOCAL_REPLY" in (repo / ".aider.chat.history.md").read_text(
            encoding="utf-8"
        )
        alternative = panel.locator(f'[data-ollama-tag="{SECOND}"]')
        use = button(alternative, "Use " + SECOND + " in Aider")
        use.click()
        dialog = page.locator("#dialog")
        expect(dialog).to_contain_text("Replace this Aider session?")
        button(dialog, "Cancel").click()
        assert session.proc.pid == pid and session.snapshot()["model"] == model
        use.click()
        with page.expect_response("**/ollama/use", timeout=200000) as replaced:
            button(dialog, "Replace session").click()
        assert replaced.value.ok, replaced.value.text()
        replacement = deck.session(wid)
        wait_for(
            "confirmed replacement attached to a new Aider PID with the alternate model",
            lambda: (
                replacement.proc is not None
                and replacement.proc.pid != pid
                and replacement.snapshot()["input_state"] == "ready"
                and replacement.snapshot()["model"] == "ollama_chat/" + SECOND
            ),
            180,
        )
        page.locator("#refresh-tokens").click()
        expect(page.locator("#context")).to_contain_text("/ 8,192", timeout=20000)
    else:
        models_dir = Path(os.environ["OLLAMA_MODELS"])
        assert list((models_dir / "manifests").rglob(tag.split(":")[1]))
        print(
            "PASS real registry pull, distinct local store, preload, registration, live badge and native chat",
            flush=True,
        )

    # A previously installed, saved model must start from a stopped service
    # without separate Start, Download, or Aider-session actions.
    cold_tag = tag if real else SECOND
    before = deck.session(wid)
    before.command("/exit")
    expect(page.locator("#status")).to_contain_text("Exited", timeout=20000)
    service = app.extensions["ollama_services"][endpoint]
    previous_pid = service._process.pid
    stop_owned_daemon(service, endpoint)
    button(panel, "Refresh Ollama").click()
    expect(status).to_contain_text("stopped", timeout=20000)
    requests = []
    page.on(
        "request",
        lambda req: (
            requests.append(req.url.rsplit("/", 1)[-1])
            if req.method == "POST" and "/ollama/" in req.url
            else None
        ),
    )
    with page.expect_response("**/ollama/use", timeout=200000) as cold:
        button(panel, "Use " + cold_tag + " in Aider").click()
    assert cold.value.ok, cold.value.text()
    assert requests == ["use"]
    assert service._process.pid != previous_pid
    session = deck.session(wid)
    wait_for(
        "single Use from a stopped daemon launched Aider with the installed model",
        lambda: (
            session.snapshot()["input_state"] == "ready"
            and session.snapshot()["model"] == "ollama_chat/" + cold_tag
        ),
        180,
    )
    expect(panel.locator(f'[data-ollama-tag="{cold_tag}"]')).to_contain_text(
        "Loaded", timeout=15000
    )
    expect(page.locator("#model")).to_contain_text(cold_tag)
    if not real:
        current_pid = session.proc.pid
        # Wait for Use's final UI refresh before simulating an older backend.
        expect(button(panel, "Remove " + cold_tag + " from saved")).to_be_enabled()
        page.route(
            "**/ollama",
            lambda route: route.fulfill(status=404, json={"error": "Not Found"}),
        )
        button(panel, "Refresh Ollama").click()
        expect(panel).to_contain_text("Open an updated Deck instance")
        assert (
            session.proc.pid == current_pid and session.snapshot()["status"] == "live"
        )
        print(
            "PASS older Deck reports missing Ollama support and preserves its Aider PID",
            flush=True,
        )
    compatible = "openai/" + cold_tag
    page.locator("#terminal .xterm-screen").click()
    page.keyboard.type("/model " + compatible)
    page.keyboard.press("Enter")
    expect(page.locator("#model")).to_have_text(compatible, timeout=30000)
    page.locator("#refresh-tokens").click()
    expect(page.locator("#context-meter")).to_be_visible(timeout=20000)
    expect(page.locator("#usage")).to_contain_text("Ollama loaded window")
    print("PASS switched OpenAI-compatible Ollama tag meters the matching loaded runner", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real",
        action="store_true",
        help="Download a 92 MB Ollama chat model into a temporary store and run inference",
    )
    args = parser.parse_args()
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{sock.getsockname()[1]}"
    with scratch_directory() as base:
        home, models_dir, repo = [base / name for name in ("home", "models", "repo")]
        home.mkdir()
        models_dir.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        for name in list(os.environ):
            if (
                name.startswith(("AIDER_", "OLLAMA_", "OPENAI_", "ANTHROPIC_"))
                or "API_KEY" in name
            ):
                del os.environ[name]
        os.environ.update(
            HOME=str(home),
            USERPROFILE=str(home),
            APPDATA=str(home),
            OLLAMA_HOST=endpoint,
            OLLAMA_MODELS=str(models_dir),
            OLLAMA_NO_CLOUD="1",
            OLLAMA_CONTEXT_LENGTH="4096",
            CUDA_CACHE_DISABLE="1",
            NO_PROXY="127.0.0.1,localhost,::1",
            LITELLM_LOCAL_MODEL_COST_MAP="True",
        )
        tags = [REAL if args.real else FIXTURE, SECOND]
        (repo / ".aider.conf.yml").write_text(
            "edit-format: diff\nstream: false\ngitignore: false\nmap-tokens: 0\ncheck-update: false\n"
            "analytics: false\nshow-model-warnings: false\nshow-release-notes: false\nyes-always: true\n"
        )
        (repo / ".aider.model.settings.yml").write_text(
            "".join(f"- name: ollama_chat/{t}\n  edit_format: diff\n" for t in tags)
        )
        (repo / ".env").write_text(
            f"OPENAI_API_BASE={endpoint}/v1\nOPENAI_API_KEY=local-smoke-placeholder\n"
        )
        (repo / "editable.txt").write_text(
            "This small editable file belongs to the scratch session.\n" * 3
        )
        (repo / "reference.txt").write_text(
            "This read-only file provides a longer reference.\n" * 9
        )
        subprocess.run(
            ["git", "add", "--", "editable.txt", "reference.txt"], cwd=repo, check=True
        )
        if not args.real:
            os.environ.update(
                HTTPS_PROXY="http://127.0.0.1:1", HTTP_PROXY="http://127.0.0.1:1"
            )
            (home / "serve").write_text(
                f"import sys\nsys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n"
                "from manual_ollama_panel import fixture_daemon\nfixture_daemon()\n"
            )
        app = create_app(base / "state.json", base)
        try:
            with (
                (
                    nullcontext()
                    if args.real
                    else patch("deck.ollama_service._cli", return_value=sys.executable)
                ),
                serve(app) as url,
                sync_playwright() as pw,
            ):
                browser = pw.chromium.launch(executable_path=os.getenv("DECK_CHROME"))
                page = browser.new_page(viewport={"width": 1500, "height": 950})
                page.route("https://**", lambda route: route.abort())
                page.set_default_timeout(12000)
                try:
                    exercise(page, url, app, repo, home, endpoint, args.real)
                except Exception:
                    print(
                        "Ollama panel:",
                        page.locator("#inspector-content").inner_text()[-1600:],
                        flush=True,
                    )
                    raise
                browser.close()
        finally:
            app.extensions["deck"].close()
            for service in app.extensions.get("ollama_services", {}).values():
                stop_owned_daemon(service, endpoint)


if __name__ == "__main__":
    main()
