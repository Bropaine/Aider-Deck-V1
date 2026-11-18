"""Exercise every advertised format through real Aider and Deck's HTTP controls."""

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from browser_acceptance import (
    FIRST,
    SECOND,
    create_app,
    fixture_api,
    repo_setup,
    serve,
    wait_for,
)


def exercise(base, endpoint):
    root = base / "repo"
    repo_setup(root, endpoint)
    bad_model = "openai/deck-invalid-registered-model"
    models_file = root / ".aider.model.settings.yml"
    models_file.write_text(
        f"- name: {SECOND}\n  edit_format: ask\n"
        f"- name: {bad_model}\n  edit_format: broken-coder\n"
    )
    with (root / ".aider.conf.yml").open("a") as config:
        config.write(f"alias: ['broken-alias:{bad_model}']\n")
    app = create_app(base / "state.json", base)
    client = app.test_client()
    wid = client.post("/api/workspaces", json={"path": str(root)}).json["id"]
    path = "/api/" + wid
    session = app.extensions["deck"].session(wid)

    def post(route, fields=None, expected=200):
        response = client.post(path + route, json=fields or {})
        assert response.status_code == expected, (route, response.json)
        return response.json

    def announced(fmt):
        snap = session.snapshot()
        return snap["active_format"] == fmt and snap["input_state"] == "ready"

    def setting(key, value, live=False, expected=200):
        return post("/config/setting", dict(key=key, value=value, live=live), expected)

    invalid = "deck-format-does-not-exist"
    try:
        choices = client.get(path + "/config").json["formats"]
        assert {"udiff-simple", "editor-diff-fenced"} <= choices.keys()
        setting("edit-format", "udiff-simple")
        registry = dict(kind="register", model=FIRST, edit_format="editor-diff-fenced")
        preview = post("/config/preview", registry)
        assert "+  edit_format: editor-diff-fenced" in preview["diff"]
        post("/config/apply", {**registry, "token": preview["token"]})
        post("/session/start")
        wait_for(
            "Code launch announces udiff-simple", lambda: announced("udiff-simple")
        )
        for name in choices:
            setting("edit-format", name, live=True)
            wait_for("live /chat-mode " + name, lambda: announced(name))

        post("/mode", dict(mode="ask"))
        wait_for("Ask announced", lambda: announced("ask"))
        post("/mode", dict(mode="code"))
        wait_for(
            "Code restores registered editor-diff-fenced",
            lambda: announced("editor-diff-fenced"),
        )

        pid = session.proc.pid
        for target in (bad_model, "broken-alias"):
            result = setting("model", target, live=True, expected=400)
            assert "unsupported edit_format" in result["error"]
            post("/send", dict(text="/model " + target), 400)
        setting("model", bad_model)
        assert (
            "unsupported edit_format" in post("/session/restart", expected=400)["error"]
        )
        assert session.proc.pid == pid and announced("editor-diff-fenced")
        setting("model", FIRST)
        setting("model", SECOND, live=True)
        wait_for(
            "a registered Ask default is valid on a model switch",
            lambda: announced("ask"),
        )
        setting("model", FIRST, live=True)
        wait_for(
            "return to first registered model", lambda: announced("editor-diff-fenced")
        )
        print(
            "PASS malformed unrelated registration permits healthy launches; selecting it fails before sending or restarting."
        )
        for key in ("edit-format", "editor-edit-format"):
            setting(key, invalid, expected=400)
        setting("edit-format", invalid, live=True, expected=400)
        post(
            "/config/preview",
            {**registry, "model": "openai/other", "edit_format": invalid},
            400,
        )
        conf = root / ".aider.conf.yml"
        original = conf.read_text()
        conf.write_text(
            original.replace("edit-format: udiff-simple", "edit-format: " + invalid)
        )
        post("/session/restart", expected=400)
        assert session.proc.pid == pid and announced("editor-diff-fenced")
        conf.write_text(original)
        print(
            "PASS unsupported format rejected on save, Apply live, registration and restart; previous PTY remained ready."
        )
        cursor = session.output.end
        session.write("/chat-mode " + invalid + "\r")
        wait_for(
            "native /chat-mode rejected an unknown format and kept the active coder",
            lambda: (
                "should be one of these" in session.read_since(cursor, 0)[1]
                and announced("editor-diff-fenced")
            ),
        )

        setting("editor-edit-format", "editor-diff-fenced")
        setting("architect", True)
        post("/session/restart")
        wait_for(
            "Architect launch announces editor-diff-fenced",
            lambda: (
                announced("architect")
                and session.snapshot()["editor_format"] == "editor-diff-fenced"
            ),
        )
        (root / "custom.env").write_text(
            "DECK_FORMAT_SENTINEL=custom-file\nOPENAI_API_KEY=synthetic-custom-key\n"
        )
        with conf.open("a") as config:
            config.write("env-file: custom.env\n")
        with (root / ".env").open("a") as env:
            env.write(
                'AIDER_EDIT_FORMAT=whole\nAIDER_ALIAS=["extra:openai/unused"]\n'
                "DECK_FORMAT_SENTINEL=default-file\n"
            )
        script = root / "inspect_env.py"
        script.write_text(
            "import os\nfrom pathlib import Path\n"
            "assert os.environ['DECK_FORMAT_SENTINEL'] == 'custom-file'\n"
            "assert os.environ['OPENAI_API_KEY'] == 'synthetic-custom-key'\n"
            "Path('verified-environment').touch()\n"
        )
        post("/session/restart")
        wait_for(
            "Architect survives competing dotenv format and append arguments",
            lambda: (
                announced("architect")
                and session.snapshot()["editor_format"] == "editor-diff-fenced"
            ),
        )
        post("/command", dict(line="/run python inspect_env.py"))
        wait_for(
            "custom dotenv retains precedence for keys and ordinary variables",
            lambda: (root / "verified-environment").exists() and announced("architect"),
        )

        # Probe native CLI rejection independently of Deck's preflight, using
        # the same disposable cwd, home and fixture environment as the test.
        probe = subprocess.run(
            [shutil.which("aider"), "--model", FIRST, "--edit-format", invalid],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=25,
            input="n\n",
        )
        assert (
            probe.returncode != 0
            and "invalid choice" in probe.stderr
            and invalid in probe.stderr
        )
        print(
            "PASS native CLI rejected unknown edit format during argument parsing (exit",
            str(probe.returncode) + ").",
        )
        post("/command", dict(line="/exit", confirmed=True))
        wait_for("clean exit", lambda: session.snapshot()["status"] == "exited")
    except AssertionError:
        state = session.snapshot()
        print(
            "Scratch terminal state:",
            {
                k: state[k]
                for k in (
                    "model",
                    "active_format",
                    "editor_format",
                    "status",
                    "input_state",
                )
            },
        )
        print("Scratch terminal output:", ascii(session.read_since(None, 0)[1][-2000:]))
        raise
    finally:
        app.extensions["deck"].close()


if __name__ == "__main__":
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    requests = []
    with (
        tempfile.TemporaryDirectory(prefix="deck-formats-") as td,
        serve(fixture_api(requests)) as endpoint,
    ):
        base = Path(td)
        home = base / "home"
        home.mkdir()
        for key in list(os.environ):
            if key.startswith(("AIDER_", "OPENAI_", "ANTHROPIC_")) or "API_KEY" in key:
                del os.environ[key]
        os.environ.update(
            HOME=str(home),
            USERPROFILE=str(home),
            APPDATA=str(home),
            LOCALAPPDATA=str(home),
            HTTP_PROXY="http://127.0.0.1:1",
            HTTPS_PROXY="http://127.0.0.1:1",
            NO_PROXY="127.0.0.1,localhost",
            LITELLM_LOCAL_MODEL_COST_MAP="True",
            PYTHONUTF8="1",
        )
        exercise(base, endpoint)
        assert not requests, "Format checks should not send any model requests"
