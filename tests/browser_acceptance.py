"""Rehearse the Windows browser workflow using real Aider and a loopback reply fixture.

Run with: .venv\\Scripts\\python tests/browser_acceptance.py
Requires Playwright, an installed Chromium browser, Aider on PATH, and Git.
"""

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from flask import Flask, jsonify, request
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import create_app  # noqa: E402 - the script also runs directly from tests/

FIRST = "openai/deck-acceptance-a"
SECOND = "openai/deck-acceptance-b"
KEY = "deck-fixture-only-4129"
PROMPT = "Inspect these literal lines:\n{\n  nested = true\n}\nEnd of the fixture."


@contextmanager
def serve(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def fixture_api(received):
    provider = Flask("local-reply-fixture")

    @provider.post("/v1/chat/completions")
    def completion():
        body = request.get_json()
        last = next(
            m["content"] for m in reversed(body["messages"]) if m["role"] == "user"
        )
        if isinstance(last, list):
            last = "\n".join(chunk.get("text", "") for chunk in last)
        received.append((last, request.headers.get("Authorization") == "Bearer " + KEY))
        reply = (
            "target.txt\n```text\n<<<<<<< SEARCH\nbefore\n=======\nafter\n>>>>>>> REPLACE\n```"
            if "DECK_EDIT_FIXTURE" in last
            else "DECK_ANSWER_OK"
        )
        model = body["model"].removeprefix("openai/")
        if model == FIRST.removeprefix("openai/") and "DECK_APPROVE_ARCHITECT" in last:
            reply = "Change the line in target.txt from before to approved. DECK_EDITOR_HANDOFF"
        elif model == SECOND.removeprefix("openai/") and "DECK_EDITOR_HANDOFF" in last:
            reply = "target.txt\n```text\n<<<<<<< SEARCH\nbefore\n=======\napproved\n>>>>>>> REPLACE\n```"
        prompt_tokens = 64 if "DECK_ASK_ONCE" in last else 32
        return jsonify(
            id="local-fixture",
            created=int(time.time()),
            object="chat.completion",
            model=body["model"],
            choices=[
                dict(
                    index=0,
                    finish_reason="stop",
                    message=dict(role="assistant", content=reply),
                )
            ],
            usage=dict(prompt_tokens=prompt_tokens, completion_tokens=8, total_tokens=prompt_tokens + 8),
        )

    return provider


def repo_setup(root, endpoint):
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()

    git("config", "user.email", "deck@example.invalid")
    git("config", "user.name", "Aider Deck Test")
    (root / "target.txt").write_text("before\n")
    (root / ".gitignore").write_text(".aider*\n.env\n")
    git("add", "--", "target.txt", ".gitignore")
    git("commit", "-qm", "Initial fixture")
    (root / ".aider.conf.yml").write_text(
        f"model: {FIRST}\nedit-format: diff\nstream: false\ncheck-update: false\n"
        "analytics: false\nauto-commits: false\nmap-tokens: 0\ngitignore: false\n"
        "yes-always: true\nshow-model-warnings: false\ndetect-urls: false\n"
        "suggest-shell-commands: false\n"
    )
    (root / ".aider.model.settings.yml").write_text(
        f"- name: {SECOND}\n  edit_format: patch\n"
    )
    (root / ".env").write_text(
        f"OPENAI_API_BASE={endpoint}/v1\nOPENAI_API_KEY=initial-synthetic-key\n"
    )
    metadata = dict(
        max_tokens=8192,
        max_input_tokens=8192,
        max_output_tokens=1024,
        litellm_provider="openai",
        mode="chat",
        input_cost_per_token=0,
        output_cost_per_token=0,
    )
    (root / ".aider.model.metadata.json").write_text(
        json.dumps({x: metadata for x in (FIRST, SECOND, FIRST[7:], SECOND[7:])})
    )
    return git


def wait_for(label, fn, timeout=28):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if fn():
            print("PASS", label, flush=True)
            return
        time.sleep(0.15)
    raise AssertionError("Timed out: " + label)


def check(page, url, app, root, git, calls, browser):
    client = app.test_client()
    opener = client.post("/api/workspaces", json={"path": str(root)}).get_json()
    wid = opener["id"]
    prefix = "/api/" + wid
    deck = app.extensions["deck"]
    session = deck.session(wid)
    snap = session.snapshot

    def transcript():
        return session.read_since(None, timeout=0)[1]

    def btn(name):
        return page.get_by_role("button", name=name, exact=True)

    page.goto(url, wait_until="domcontentloaded")
    page.set_default_timeout(15000)
    try:
        page.locator("#start").click()
        wait_for(
            "real ConPTY launch with diff", lambda: snap()["active_format"] == "diff"
        )
        page.wait_for_function(
            "document.querySelector('#format').textContent.toLowerCase() === 'diff'"
        )
        assert "UNREGISTERED MODEL" in page.locator("#notices").inner_text().upper()
        btn("Effective settings").click()
        wait_for(
            "Effective settings gets launch diff",
            lambda: snap()["settings"]["data"].get("edit-format") == "diff",
        )
        page.locator("#dialog button[aria-label='Close dialog']").click()
        page.locator("#mode-buttons").get_by_role(
            "button", name="Ask", exact=True
        ).click()
        wait_for("unregistered model enters Ask", lambda: snap()["mode"] == "ask")
        page.locator("#mode-buttons").get_by_role(
            "button", name="Code", exact=True
        ).click()
        btn("Switch to Code").click()
        wait_for(
            "guarded Code switch exposes whole versus launch diff",
            lambda: (
                snap()["active_format"] == "whole" and snap()["launch_format"] == "diff"
            ),
        )
        page.wait_for_function(
            "document.querySelector('#format').textContent.toLowerCase() === 'whole'"
        )
        # Register from the actual preview dialog. The live process still has its old definition.
        page.get_by_role("button", name="Register model", exact=True).first.click()
        btn("Preview YAML diff").click()
        assert FIRST in page.locator("#dialog-body").inner_text()
        btn("Confirm write").click()
        wait_for(
            "model default written after preview",
            lambda: FIRST in (root / ".aider.model.settings.yml").read_text(),
        )
        btn("Keys").click()
        keyrow = page.locator(".key-row").filter(has_text="OPENAI_API_KEY").first
        keyrow.locator("input").fill(KEY)
        keyrow.get_by_role("button", name="Save", exact=True).click()
        wait_for("key saved", lambda: KEY in (root / ".env").read_text())
        assert KEY not in client.get(prefix + "/config/keys").get_data(as_text=True)
        assert KEY not in page.locator("body").inner_text()
        assert client.get(prefix + "/state").json["pending"]
        page.locator("#start").click()
        if page.locator("dialog[open]").count():
            page.locator("#dialog .dialog-actions button").last.click()
        wait_for(
            "registered model and new environment loaded on explicit restart",
            lambda: (
                client.get(prefix + "/state").json["registration"]["loaded_at_start"]
                and snap()["active_format"] == "diff"
            ),
        )
        page.locator("#mode-buttons").get_by_role(
            "button", name="Ask", exact=True
        ).click()
        wait_for("sticky Ask observed", lambda: snap()["mode"] == "ask")
        page.locator("#prompt").fill("DECK_ASK_FIXTURE")
        page.locator("#send").click()
        wait_for(
            "real Aider displays fixture answer and uses rotated key",
            lambda: (
                any("DECK_ASK_FIXTURE" in q and k for q, k in calls)
                and "DECK_ANSWER_OK" in transcript()
                and snap()["reply_tokens"] == {"model": FIRST, "used": 40}
            ),
        )
        page.wait_for_function("document.querySelector('#context').textContent.includes('<1%')")
        assert (root / "target.txt").read_text() == "before\n"
        page.locator("#mode-buttons").get_by_role(
            "button", name="Code", exact=True
        ).click()
        wait_for(
            "return to Code restores registered diff",
            lambda: snap()["mode"] == "code" and snap()["active_format"] == "diff",
        )
        page.locator("#prompt").fill("DECK_ASK_ONCE")
        page.locator("#once").select_option("ask")
        wait_for(
            "one-shot Ask reaches Aider",
            lambda: any("DECK_ASK_ONCE" in q for q, k in calls),
        )
        wait_for(
            "one-shot preserves sticky Code",
            lambda: (
                snap()["mode"] == "code" and transcript().count("DECK_ANSWER_OK") >= 2
            ),
        )
        wait_for(
            "two real Aider replies leave the prompt free with native footer usage",
            lambda: snap()["input_state"] == "ready"
            and snap()["reply_tokens"] == {"model": FIRST, "used": 72},
        )
        assert not snap()["tokens"]["raw"]
        assert "Approximate context window usage" not in transcript()
        page.locator("#context-badge").click()
        wait_for(
            "explicit context refresh prints and parses Aider's detailed report",
            lambda: not snap()["tokens"]["stale"]
            and snap()["tokens"]["data"].get("used", 0) > 0,
        )
        exercise_files(page, snap, btn)
        btn("Settings").click()
        page.locator("#model-badge").click()
        field = page.locator("#field-model")
        field.fill(SECOND)
        field.locator(
            "xpath=ancestor::*[contains(@class,'setting-row')][1]"
        ).get_by_role("button", name="Apply live").click()
        wait_for(
            "live switch to registered model reports actual format",
            lambda: snap()["model"] == SECOND and snap()["active_format"] == "patch",
        )
        page.wait_for_function(
            "document.querySelector('#format').textContent.toLowerCase() === 'patch'"
        )
        btn("Effective settings").click()
        wait_for(
            "new model announced inside fresh /settings",
            lambda: SECOND in snap()["settings"]["raw"],
        )
        page.locator("#dialog button[aria-label='Close dialog']").click()
        field.fill(FIRST)
        field.locator(
            "xpath=ancestor::*[contains(@class,'setting-row')][1]"
        ).get_by_role("button", name="Apply live").click()
        wait_for(
            "return to the registered diff model before edit",
            lambda: snap()["model"] == FIRST and snap()["active_format"] == "diff",
        )
        page.locator("#add-path").fill("target.txt")
        page.locator("#add-form button").click()
        wait_for(
            "editable file ready for Aider edit",
            lambda: any(r["path"] == "target.txt" for r in snap()["files"]["data"]),
        )
        page.locator("#prompt").fill(
            "DECK_EDIT_FIXTURE: change before to after in target.txt"
        )
        page.locator("#send").click()
        wait_for(
            "Aider applied SEARCH/REPLACE edit",
            lambda: (root / "target.txt").read_text() == "after\n",
        )
        btn("Git").click()
        row = page.locator(".change-row").filter(has_text="target.txt").first
        row.get_by_role("button", name="View diff").click()
        page.locator("#dialog-body").get_by_text("+after", exact=True).wait_for()
        page.locator("#dialog button[aria-label='Close dialog']").click()
        row.get_by_role("button", name="Stage", exact=True).click()
        wait_for(
            "git index contains requested edit",
            lambda: git("diff", "--cached", "--name-only") == "target.txt",
        )
        page.locator("#commit-message").fill("Deck acceptance edit")
        btn("Commit staged").click()
        wait_for(
            "UI commit matches actual git log",
            lambda: git("log", "-1", "--format=%s") == "Deck acceptance edit",
        )
        page.get_by_role("button", name=re.compile("Deck acceptance edit")).wait_for()
        pid = session.proc.pid
        page.close()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("https://cdn.jsdelivr.net/**", lambda r: r.abort())
        replay = []

        def observe_socket(ws):
            def frame(payload):
                msg = json.loads(payload)
                if msg.get("reset"):
                    replay.append(msg.get("data", ""))

            ws.on("framereceived", frame)

        page.on("websocket", observe_socket)
        page.goto(url, wait_until="domcontentloaded")
        page.locator("#model").get_by_text(FIRST, exact=True).wait_for()
        wait_for(
            "browser reattaches with original PID and replay",
            lambda: (
                session.proc.pid == pid
                and page.locator(".xterm").count() > 0
                and any("DECK_EDIT_FIXTURE" in text for text in replay)
            ),
        )
        assert client.get(prefix + "/state").json["history"][-1].startswith("DECK_EDIT")
        btn("Prompts").click()
        btn("braces.md").click()
        btn("Load into composer").click()
        assert page.locator("#prompt").input_value() == PROMPT
        page.locator("#send").click()
        wait_for(
            "multiline prompt with literal braces reached Aider intact",
            lambda: any(PROMPT in q for q, k in calls),
        )
        assert len(calls) == 4, (
            f"Expected four submitted prompts, saw {len(calls)} provider requests"
        )
        for key, value in [("architect", True), ("editor-edit-format", "editor-diff")]:
            assert (
                client.post(
                    prefix + "/config/setting", json={"key": key, "value": value}
                ).status_code
                == 200
            )
        assert client.post(prefix + "/session/restart", json={}).status_code == 200
        wait_for(
            "Architect starts with independently announced editor-diff",
            lambda: (
                snap()["active_format"] == "architect"
                and snap()["editor_format"] == "editor-diff"
            ),
        )
        print("PASS browser acceptance complete", flush=True)
    finally:
        if sys.exc_info()[0]:
            print(
                "Fixture messages:", [repr(q[:240]) for q, _ in calls][-12:], flush=True
            )
            print(
                "Snapshot:",
                {
                    k: snap()[k]
                    for k in ["status", "mode", "model", "active_format", "error"]
                },
                flush=True,
            )
            print(
                "Browser:",
                page.locator("body").inner_text()[-1300:]
                if not page.is_closed()
                else "closed",
            )
            print("Terminal tail:", repr(transcript()[-1500:]))
        page.close()


def exercise_files(page, snap, btn):
    row = (
        page.locator("#tree").get_by_text("target.txt", exact=True).first.locator("..")
    )
    row.get_by_title("Add read-only").click()
    wait_for(
        "tree adds read-only",
        lambda: any(r["read_only"] for r in snap()["files"]["data"]),
    )
    page.locator("#chat-files").get_by_role(
        "button", name=re.compile("editable", re.I)
    ).click()
    wait_for(
        "read-only made editable",
        lambda: any(not r["read_only"] for r in snap()["files"]["data"]),
    )
    page.locator("#chat-files").get_by_role(
        "button", name=re.compile("drop", re.I)
    ).click()
    wait_for(
        "file dropped",
        lambda: not snap()["files"]["stale"] and not snap()["files"]["data"],
    )


def check_waiting_terminal(browser, url, app, root, endpoint, calls):
    repo_setup(root, endpoint)
    # Make real Aider ask for permission to add .env to .gitignore on startup.
    (root / ".gitignore").write_text(".aider*\n")
    config = root / ".aider.conf.yml"
    config.write_text(
        config.read_text()
        .replace("gitignore: false", "gitignore: true")
        .replace("yes-always: true\n", "")
    )
    wid = app.test_client().post("/api/workspaces", json={"path": str(root)}).json["id"]
    session = app.extensions["deck"].session(wid)
    snap = session.snapshot
    previous_calls = len(calls)
    draft = "Keep this unsent draft while I change modes."
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.route("https://cdn.jsdelivr.net/**", lambda r: r.abort())
    page.set_default_timeout(20000)
    buttons = page.locator("#mode-buttons")
    try:
        page.goto(url, wait_until="domcontentloaded")
        # Reproduce the desktop launcher's TERM even on an interactive host.
        with patch.dict(os.environ, TERM="dumb"):
            page.locator("#start").click()
            page.locator("#terminal-question").wait_for()
        assert ".env" in page.locator("#terminal-question-text").inner_text()
        assert snap()["input_state"] == "waiting"
        assert "Detected dumb terminal" not in session.read_since(None, timeout=0)[1]
        page.locator("#prompt").fill(draft)
        for mode in ["Context", "Ask"]:
            button = buttons.get_by_role("button", name=mode, exact=True)
            button.click()
            wait_for(
                f"{mode} requested while Aider is waiting",
                lambda requested=mode.lower(): snap()["pending_mode"] == requested,
            )
            page.locator("#mode-request").get_by_text(
                mode + " requested", exact=False
            ).wait_for()
            assert button.get_attribute("aria-pressed") == "false"
            assert snap()["input_state"] == "waiting"
        page.get_by_role("button", name="Answer in terminal").click()
        page.wait_for_function(
            "document.activeElement.matches('.xterm-helper-textarea')"
        )
        page.keyboard.type("n")
        page.keyboard.press("Enter")
        page.locator('#mode-buttons [data-mode="ask"][aria-pressed="true"]').wait_for()
        wait_for(
            "Aider enters the latest requested mode after the human answer",
            lambda: snap()["input_state"] == "ready" and snap()["pending_mode"] is None,
        )
        assert (root / ".gitignore").read_text() == ".aider*\n"
        assert page.locator("#prompt").input_value() == draft
        assert page.locator("#terminal-question").is_hidden()
        for mode in ["Architect", "Context", "Help", "Code"]:
            buttons.get_by_role("button", name=mode, exact=True).click()
            if mode == "Code":
                page.get_by_role("button", name="Switch to Code", exact=True).click()
            page.locator(
                f'#mode-buttons [data-mode="{mode.lower()}"][aria-pressed="true"]'
            ).wait_for()
            assert page.locator("#mode").inner_text() == mode
            assert page.locator("#prompt").input_value() == draft
        exercise_terminal_edits(page, snap)
        assert page.locator("#prompt").input_value() == draft
        assert len(calls) == previous_calls
        print(
            "PASS composer draft survives all mode changes and terminal editing",
            flush=True,
        )
    finally:
        if sys.exc_info()[0]:
            print(
                "Terminal tail:", repr(session.read_since(None, timeout=0)[1][-2200:])
            )
            print(
                "Mode state:",
                {
                    key: snap()[key]
                    for key in ["mode", "input_state", "input_prompt", "pending_mode"]
                },
            )
        page.close()
        session.close()


def exercise_terminal_edits(page, snap):
    page.locator("#terminal .xterm-screen").click()
    page.wait_for_function("document.activeElement.matches('.xterm-helper-textarea')")
    # Use browser clipboard events without inspecting or replacing the Windows clipboard.
    page.locator(".xterm-helper-textarea").evaluate("""el => {
        const data = new DataTransfer();
        data.setData("text/plain", "/settinXgs");
        el.dispatchEvent(new ClipboardEvent("paste", {clipboardData: data, bubbles: true}));
    }""")
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("Backspace")
    page.keyboard.press("End")
    page.keyboard.type("XX")
    # xterm temporarily hides or replaces its cursor while prompt-toolkit repaints.
    bounds = page.wait_for_function("""() => {
        const cursor = document.querySelector("#terminal .xterm-cursor");
        const rect = cursor?.getBoundingClientRect();
        const row = cursor?.closest(".xterm-rows > div");
        return rect?.width && row?.textContent.includes("/settingsXX") && rect.toJSON();
    }""").json_value()
    cell = bounds["width"]

    def expect_caret(left):
        page.wait_for_function(
            """([left, cell]) => {
            const box = document.querySelector("#terminal .xterm-cursor")?.getBoundingClientRect();
            return box?.width && Math.abs(box.x - left) < cell / 2;
        }""",
            arg=[left, cell],
        )

    x = bounds["x"] + cell / 2
    y = bounds["y"] + bounds["height"] / 2
    # Drag across /settings on the live row; copy the selection using xterm's native handler.
    page.mouse.move(x - 11 * cell, y)
    page.mouse.down()
    page.mouse.move(x - 2 * cell, y, steps=9)
    page.mouse.up()
    copied = page.locator(".xterm-helper-textarea").evaluate("""el => {
        const data = new DataTransfer();
        el.dispatchEvent(new ClipboardEvent("copy", {clipboardData: data, bubbles: true}));
        return data.getData("text/plain");
    }""")
    assert copied == "/settings", repr(copied)
    # Click to focus; Alt-click two cells back to edit the first stray X.
    page.mouse.click(x - 2 * cell, y)
    expect_caret(bounds["x"])
    page.keyboard.down("Alt")
    page.mouse.click(x - 2 * cell, y)
    page.keyboard.up("Alt")
    expect_caret(bounds["x"] - 2 * cell)
    page.keyboard.press("Delete")
    page.keyboard.press("Delete")
    page.keyboard.press("Enter")
    wait_for(
        "Aider executes the command edited by cursor keys and mouse",
        lambda: snap()["settings"]["data"].get("edit-format") == "diff",
    )
    assert not snap()["tokens"]["raw"]
    page.keyboard.type("/tokens")
    page.keyboard.press("Enter")
    wait_for(
        "typing /tokens directly in the terminal refreshes Session usage",
        lambda: not snap()["tokens"]["stale"] and snap()["input_state"] == "ready",
    )
    page.keyboard.type("Unsubmitted text")
    wait_for(
        "terminal accepts raw keyboard input", lambda: snap()["input_state"] == "typing"
    )
    page.locator("#interrupt").click()
    wait_for(
        "Interrupt returns to the Aider prompt",
        lambda: snap()["input_state"] == "ready",
    )


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    import platform

    if platform.system() != "Windows":
        raise SystemExit("This acceptance run needs Windows ConPTY.")
    received = []
    with (
        tempfile.TemporaryDirectory(
            prefix="aider-deck-accept-", ignore_cleanup_errors=True
        ) as tmp,
        serve(fixture_api(received)) as endpoint,
    ):
        base = Path(tmp)
        home = base / "home"
        home.mkdir()
        for key in list(os.environ):
            if key.startswith(("AIDER_", "OPENAI_", "ANTHROPIC_")):
                os.environ.pop(key)
        os.environ.update(
            HOME=str(home),
            USERPROFILE=str(home),
            NO_PROXY="127.0.0.1,localhost",
            HTTPS_PROXY="http://127.0.0.1:1",
            HTTP_PROXY="http://127.0.0.1:1",
            LITELLM_LOCAL_MODEL_COST_MAP="True",
            PYTHONIOENCODING="utf-8",
        )
        repo, library = base / "repo", base / "prompts"
        library.mkdir()
        (library / "braces.md").write_text(PROMPT, encoding="utf-8")
        git = repo_setup(repo, endpoint)
        app = create_app(base / "state.json", library)
        try:
            with serve(app) as url, sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=os.getenv("DECK_CHROME"))
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.route("https://cdn.jsdelivr.net/**", lambda r: r.abort())
                check(page, url, app, repo, git, received, browser)
                check_waiting_terminal(
                    browser, url, app, base / "waiting-repo", endpoint, received
                )
                browser.close()
        finally:
            app.extensions["deck"].close()


if __name__ == "__main__":
    main()
