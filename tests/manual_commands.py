"""Run the command palette and live settings in a disposable Windows Aider session.

Use .venv\\Scripts\\python tests/manual_commands.py. Install Playwright's Chromium
test browser as documented in README.md. Provider and web requests stay on loopback.
"""

import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path

from flask import Response
from playwright.sync_api import expect, sync_playwright

from browser_acceptance import FIRST, SECOND, create_app, fixture_api, repo_setup, serve


def eventually(label, condition, diagnostics, timeout=32):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if condition():
            print("PASS", label, flush=True)
            return
        time.sleep(0.12)
    raise AssertionError(f"{label} timed out\n{diagnostics()}")


def build_repo(repo, endpoint):
    git = repo_setup(repo, endpoint)
    # Replace only the external clipboard library in this one child. The
    # installed Aider command and its context serializer still run as shipped.
    clipboard = repo.parent / "clipboard_fixture"
    clipboard.mkdir()
    (clipboard / "pyperclip.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "root = Path(__file__).parent\n"
        "if '--no-gui' in sys.argv:\n"
        "    (root/'aider-loaded-adapter').touch()\n"
        "class PyperclipException(Exception): pass\n"
        "def copy(text): (root/'copied.md').write_text(text, encoding='utf-8')\n"
        "def paste(): return ''\n",
        encoding="utf-8",
    )
    with (repo / ".env").open("a", encoding="utf-8") as envfile:
        envfile.write(f"PYTHONPATH='{clipboard.as_posix()}'\n")
    config = repo / ".aider.conf.yml"
    text = (
        config.read_text()
        .replace("map-tokens: 0", "map-tokens: 256")
        .replace("auto-commits: false", "auto-commits: true")
    )
    config.write_text(
        text + "disable-playwright: true\ncheck-model-accepts-settings: false\n"
        "auto-lint: false\n"
        'lint-cmd: ["python: python fixture_lint.py"]\n'
        "test-cmd: python fixture_test.py\n"
    )
    (repo / "fixture_lint.py").write_text(
        'from pathlib import Path\nPath(".lint-ran").touch()\n'
    )
    (repo / "fixture_test.py").write_text(
        'from pathlib import Path\nPath(".test-ran").touch()\nprint("deck configured test passed")\n'
    )
    (repo / "example.py").write_text("def metadata_probe():\n    return 1\n")
    (repo / "notes").mkdir()
    (repo / "notes" / "group [1].txt").write_text("Selected reference.\n")
    (repo / "notes" / "group 1.txt").write_text("Unselected sibling.\n")
    (repo / ".gitignore").write_text(
        ".aider*\n.env\n.test-ran\n.lint-ran\nrestore.deck\n"
    )
    git(
        "add", ".gitignore", "example.py", "fixture_lint.py", "fixture_test.py", "notes"
    )
    git("commit", "-qm", "Scratch commands fixture")
    return git


def exercise(app, url, endpoint, web_visits, repo, git, browser):
    page = browser.new_page(viewport={"width": 1500, "height": 950})
    page.route("https://cdn.jsdelivr.net/**", lambda route: route.abort())
    client = app.test_client()
    wid = client.post("/api/workspaces", json={"path": str(repo)}).json["id"]
    session = app.extensions["deck"].session(wid)
    page.goto(url)
    page.set_default_timeout(14000)

    def diagnostic():
        return re.sub(
            r"\x1b\[[0-9;?]*[a-zA-Z]", "", session.read_since(None, timeout=0)[1]
        )[-1600:]

    def active_text(cursor):
        return re.sub(
            r"\x1b\[[0-9;?]*[a-zA-Z]", "", session.read_since(cursor, timeout=0)[1]
        )

    def check(label, probe):
        eventually(label, probe, diagnostic)

    def watch(name, payload, click, predicate):
        cursor = session.read_since(None, timeout=0)[0]
        with page.expect_response(
            lambda r: r.url.endswith(payload) and r.request.method == "POST"
        ) as observed:
            click()
        if not observed.value.ok:
            raise AssertionError(
                f"{name}: {observed.value.status} {observed.value.text()}"
            )
        check(name, lambda: predicate(active_text(cursor)))

    def button(name):
        return page.get_by_role("button", name=name, exact=True)

    def quick(name, argument=None, *, confirm=False, destructive=False, expected=None):
        label = "/" + name

        def dispatch():
            if destructive:
                page.locator("details.menu").filter(
                    has=page.locator("#session-menu")
                ).locator("summary").click()
            elif name not in {"tokens", "settings", "map", "diff"}:
                page.locator("details.menu").filter(
                    has=page.locator("#more-actions")
                ).locator("summary").click()
            button(label + (" …" if argument is not None else "")).click()
            if argument is not None:
                page.locator("#argument-input").fill(argument)
                page.locator("#argument-form").get_by_role(
                    "button", name="Run", exact=True
                ).click()
            if confirm or destructive:
                page.locator("#dialog").get_by_role(
                    "button", name="Send " + label, exact=True
                ).click()

        watch(label, "/command", dispatch, expected)

    def live(key, value, expected):
        def change():
            page.get_by_role("searchbox", name="Filter settings").fill(key)
            field = page.locator("#field-" + key)
            if key == "reasoning-effort":
                page.locator("#reasoning-effort-target").select_option("live")
                expect(field).to_be_enabled()
            if field.evaluate("n=>n.tagName") == "SELECT":
                field.select_option(value)
            else:
                field.fill(value)
            field.locator(
                "xpath=ancestor::div[contains(@class,'setting-row')][1]"
            ).get_by_role("button", name="Apply live", exact=True).click()

        watch("Apply live " + key, "/config/setting", change, expected)

    def contains(value):
        return lambda text: value in text

    page.locator("#start").click()
    check("scratch Aider on ConPTY", lambda: session.snapshot()["model"] == FIRST)
    quick("ls", expected=lambda text: "Repo files not in the chat" in text)
    quick("tokens", expected=contains("tokens total"))
    quick("settings", expected=contains("Option settings:"))
    quick("map", expected=lambda text: "metadata_probe" in text or "example.py" in text)
    quick("map-refresh", expected=contains("map has been refreshed"))
    quick("models", "gpt-4o-mini", expected=contains("Models which match"))
    quick("git", "status --short --branch", expected=contains("## main"))
    quick("run", "echo deck-command-marker", expected=contains("of output to the chat"))
    quick("test", expected=lambda text: (repo / ".test-ran").exists())
    quick(
        "web",
        endpoint + "/manual.txt",
        expected=lambda text: bool(web_visits) and "added to chat" in text,
    )

    def add(name):
        page.locator("#add-path").fill(name)
        page.locator("#add-form button").click()
        check(
            "add " + name,
            lambda: any(r["path"] == name for r in session.snapshot()["files"]["data"]),
        )

    group = ["example.py", "notes/group [1].txt"]
    group_url = f"/api/{wid}/files"
    response = client.post(
        group_url, json=dict(action="add", paths=[group[0], "missing.txt"])
    )
    assert response.status_code == 400
    for action in ("read-only", "add"):
        response = client.post(
            group_url, json=dict(action=action, paths=[*group, group[0]])
        )
        assert response.status_code == 200

        def grouped():
            observed = session.snapshot()["files"]
            return not observed["stale"] and {
                r["path"].replace("\\", "/"): r["read_only"] for r in observed["data"]
            } == dict.fromkeys(group, action == "read-only")

        check("one /" + action + " selects only the chosen files", grouped)
    quick("lint", expected=lambda text: (repo / ".lint-ran").exists())
    quick(
        "save",
        "restore.deck",
        expected=lambda text: (
            (repo / "restore.deck").is_file()
            and "example.py" in (repo / "restore.deck").read_text()
        ),
    )
    quick(
        "reset",
        destructive=True,
        expected=contains("All files dropped and chat history cleared"),
    )
    quick(
        "load",
        "restore.deck",
        expected=lambda text: (
            "Executing: /add" in text
            and any(
                r["path"] == "example.py" for r in session.snapshot()["files"]["data"]
            )
        ),
    )
    quick("clear", destructive=True, expected=contains("All chat history cleared"))
    quick("multiline-mode", expected=contains("multiline"))
    quick("multiline-mode", expected=lambda t: "multi" in t.lower())
    live("reasoning-effort", "high", lambda t: "effort" in t.lower() and "high" in t)
    live("thinking-tokens", "1024", contains("Set thinking token budget to"))
    # Native /model keeps the previous weak model. A blank launch override
    # and an unsaved edit must both survive the live announcements.
    config_bytes = (repo / ".aider.conf.yml").read_bytes()
    page.locator("#model-badge").click()
    live("model", SECOND, contains("Weak model: " + FIRST))
    expect(page.locator("#live-model")).to_contain_text(SECOND)
    search = page.get_by_role("searchbox", name="Filter settings")
    search.fill("weak-model")
    weak = page.locator("#live-weak-model")
    field = page.locator("#field-weak-model")
    expect(weak).to_have_text("Live weak model · " + FIRST)
    expect(field).to_have_value("")
    expect(field.locator("xpath=ancestor::div[contains(@class,'setting-row')][1]")).to_contain_text(
        "Next launch · Aider default"
    )
    button("Effective settings").click()
    detail = page.get_by_role("dialog")
    expect(detail).to_contain_text("Live main model · " + SECOND)
    expect(detail).to_contain_text("Live weak model · " + FIRST)
    page.keyboard.press("Escape")
    draft = "openai/unsaved-weak-model"
    field.fill(draft)
    page.locator("#terminal .xterm-screen").click()
    page.keyboard.type("/weak-model " + SECOND)
    page.keyboard.press("Enter")
    expect(weak).to_have_text("Live weak model · " + SECOND, timeout=15000)
    expect(field).to_have_value(draft)
    expect(page.locator("#model")).to_have_text(SECOND)
    assert (repo / ".aider.conf.yml").read_bytes() == config_bytes
    live("model", FIRST, contains("Weak model: " + SECOND))
    print("PASS main switch kept the observed weak role; its terminal update preserved the unsaved Settings input", flush=True)
    live("weak-model", SECOND, contains("Weak model: " + SECOND))
    live("editor-model", SECOND, lambda t: "Aider v" in t)
    quick("settings", expected=contains("Editor model (" + SECOND + ")"))
    live(
        "edit-format", "patch", lambda t: session.snapshot()["active_format"] == "patch"
    )
    live("edit-format", "diff", lambda t: session.snapshot()["active_format"] == "diff")
    # Aider snapshots HEAD for every input, including /tokens. Create a Git
    # change after the previous Aider input to exercise the native /diff path.
    (repo / "example.py").write_text("def metadata_probe():\n    return 2\n")
    git("add", "example.py")
    git("commit", "-qm", "Change after Aider's last input")
    quick("diff", expected=contains("Diff since"))
    add("target.txt")
    original = git("rev-parse", "HEAD")
    page.locator("#prompt").fill("DECK_EDIT_FIXTURE")
    page.locator("#send").click()
    check(
        "Aider fixture edit produced isolated commit",
        lambda: (
            git("rev-parse", "HEAD") != original
            and (repo / "target.txt").read_text() == "after\n"
        ),
    )
    # A background slash command would move Aider's previous-message baseline
    # past this edit before the owner could inspect it.
    quick("diff", expected=lambda text: "Diff since" in text and "+after" in text)
    quick(
        "undo",
        confirm=True,
        expected=lambda t: (
            git("rev-parse", "HEAD") == original
            and (repo / "target.txt").read_text() == "before\n"
        ),
    )
    (repo / "target.txt").write_text("Manual change\n")
    quick(
        "commit",
        "Manual palette commit",
        expected=lambda t: "Manual palette commit" in git("log", "-1", "--format=%s"),
    )
    clipboard = repo.parent / "clipboard_fixture"
    if not (clipboard / "aider-loaded-adapter").exists():
        raise AssertionError(
            "Clipboard fixture was not loaded. Refusing to use the host clipboard."
        )
    quick("copy-context", expected=contains("Copied code context to clipboard."))
    assert "example.py" in (clipboard / "copied.md").read_text(encoding="utf-8")
    quick(
        "exit",
        destructive=True,
        expected=lambda t: session.snapshot()["status"] == "exited",
    )
    print(
        "PASS native Aider context serialization into isolated clipboard adapter.",
        flush=True,
    )
    page.close()


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    provider = fixture_api([])
    web_visits = []

    @provider.get("/manual.txt")
    def manual_page():
        web_visits.append(True)
        return Response("Aider Deck command check", mimetype="text/plain")

    with (
        tempfile.TemporaryDirectory(
            prefix="deck-commands-", ignore_cleanup_errors=True
        ) as td,
        serve(provider) as endpoint,
    ):
        base = Path(td)
        home = base / "home"
        home.mkdir()
        for name in list(os.environ):
            if (
                name.startswith("AIDER_")
                or "API_KEY" in name
                or name.startswith(("OPENAI_", "ANTHROPIC_"))
            ):
                del os.environ[name]
        os.environ.update(
            HOME=str(home),
            USERPROFILE=str(home),
            NO_PROXY="localhost,127.0.0.1",
            HTTPS_PROXY="http://127.0.0.1:1",
            HTTP_PROXY="http://127.0.0.1:1",
            PYTHONIOENCODING="utf-8",
            LITELLM_LOCAL_MODEL_COST_MAP="True",
        )
        git = build_repo(base / "repo", endpoint)
        app = create_app(base / "state.json", base)
        try:
            with serve(app) as url, sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=os.getenv("DECK_CHROME"))
                exercise(app, url, endpoint, web_visits, base / "repo", git, browser)
                browser.close()
        finally:
            app.extensions["deck"].close()


if __name__ == "__main__":
    main()
