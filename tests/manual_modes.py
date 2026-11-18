"""Exercise all five sticky and one-shot controls against real Aider and a local fixture."""

import logging
import os
import tempfile
import time
from pathlib import Path

from browser_acceptance import FIRST, KEY, SECOND, fixture_api, repo_setup, serve

from deck.sessions import AiderSession


def exercise(root, home, endpoint, calls):
    git = repo_setup(root, endpoint)
    config = root / ".aider.conf.yml"
    config.write_text(
        config.read_text().replace(
            "yes-always: true",
            f"auto-accept-architect: false\nshow-release-notes: false\neditor-model: {SECOND}",
        )
    )
    (root / ".aider.model.settings.yml").write_text(
        f"- name: {FIRST}\n  edit_format: diff\n"
    )
    (root / ".env").write_text(f"OPENAI_API_BASE={endpoint}/v1\nOPENAI_API_KEY={KEY}\n")
    inherit = {
        "SYSTEMROOT",
        "WINDIR",
        "PATH",
        "TEMP",
        "TMP",
        "COMSPEC",
        "PATHEXT",
        "SYSTEMDRIVE",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
    }
    env = {k: v for k, v in os.environ.items() if k.upper() in inherit}
    env.update(
        USERPROFILE=str(home),
        HOME=str(home),
        APPDATA=str(home),
        LOCALAPPDATA=str(home),
        HTTP_PROXY="http://127.0.0.1:1",
        HTTPS_PROXY="http://127.0.0.1:1",
        NO_PROXY="127.0.0.1,localhost",
        PIP_NO_INDEX="1",
        HF_HUB_OFFLINE="1",
        TERM="dumb",
        LITELLM_LOCAL_MODEL_COST_MAP="True",
        PYTHONUTF8="1",
        OPENAI_API_BASE=endpoint + "/v1",
        OPENAI_API_KEY=KEY,
    )
    s = AiderSession(root)

    def wait(label, check):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if check():
                print("PASS", label, flush=True)
                return
            time.sleep(0.05)
        snap = s.snapshot()
        raise AssertionError(
            f"{label}: {snap['input_state']} {snap['mode']} {snap['input_prompt']!r}\n"
            + ascii(s.read_since(None, 0)[1][-3500:])
        )

    def ready(mode):
        state = s.snapshot()
        return (
            state["input_state"] == "ready"
            and state["mode"] == mode
            and not state["pending_mode"]
        )

    def question(fragment):
        return fragment.lower() in (s.snapshot()["input_prompt"] or "").lower()

    try:
        s.start({"model": FIRST}, env, [KEY])
        wait(
            "initial Code prompt",
            lambda: ready("code") and not s.snapshot()["files"]["stale"],
        )
        for mode in ("ask", "architect", "context", "help", "code"):
            s.mode(mode)
            wait(f"sticky {mode} announced", lambda mode=mode: ready(mode))
            assert s.snapshot()["active_format"] == ("diff" if mode == "code" else mode)
            marker = "DECK_" + mode.upper() + "_STICKY"
            s.send(marker)
            if mode == "architect":
                wait("sticky Architect asks whether to edit", lambda: question("edit"))
                s.write("n\r")
            wait(
                f"sticky {mode} received a reply",
                lambda marker=marker, mode=mode: (
                    any(marker in q and k for q, k in calls) and ready(mode)
                ),
            )
            if mode == "architect":
                s.command("/add target.txt")
                wait(
                    "Architect has editable target.txt",
                    lambda: (
                        s.snapshot()["files"]["data"]
                        == [{"path": "target.txt", "read_only": False}]
                        and ready("architect")
                    ),
                )
                before = len(calls)
                s.send("DECK_APPROVE_ARCHITECT")
                wait(
                    "Architect waits before handing changes to its editor",
                    lambda: question("edit"),
                )
                assert (root / "target.txt").read_text() == "before\n"
                assert len(calls) == before + 1
                assert "DECK_APPROVE_ARCHITECT" in calls[before][0]
                assert s.snapshot()["editor_model"] == SECOND
                assert s.snapshot()["editor_format"] == "editor-diff"
                s.write("y\r")
                wait(
                    "approved editor-diff wrote target.txt and returned to Architect",
                    lambda: (
                        (root / "target.txt").read_text() == "approved\n"
                        and ready("architect")
                    ),
                )
                assert (
                    len(calls) == before + 2
                    and "DECK_EDITOR_HANDOFF" in calls[before + 1][0]
                )
                assert all(authenticated for _, authenticated in calls[before:])
                assert git("diff", "--", "target.txt").endswith("-before\n+approved")
                snap = s.snapshot()
                assert (snap["mode"], snap["active_format"], snap["editor_format"]) == (
                    "architect",
                    "architect",
                    "editor-diff",
                )
                print(
                    "PASS approval used separate main and editor fixture models; live badge is architect",
                    flush=True,
                )
        for mode in ("ask", "code", "architect", "help", "context"):
            message = "DECK_" + mode.upper() + "_ONCE"
            if mode == "context":
                message += "\nSecond line of context."
            s.send(message, mode)
            if mode == "architect":
                wait("Architect asks whether to edit", lambda: question("edit"))
                s.write("n\r")
            if mode == "help":
                wait(
                    "Help uses fixture or asks to install optional extras",
                    lambda message=message: (
                        question("pip install") or any(message in q for q, k in calls)
                    ),
                )
                if question("pip install"):
                    s.write("n\r")
                    wait(
                        "declining optional Help install returns to Code",
                        lambda: ready("code"),
                    )
                    continue
            wait(
                f"one-shot {mode} reached local fixture",
                lambda message=message: any(message in q and k for q, k in calls),
            )
            wait(f"one-shot {mode} returned to sticky Code", lambda: ready("code"))
        s.mode("ask")
        wait("independent Ask selection after Context prefill", lambda: ready("ask"))
        s.send("DECK_AFTER_CONTEXT")
        wait(
            "next composer text delivered independently",
            lambda: any(
                "DECK_AFTER_CONTEXT" in q and "DECK_CONTEXT_ONCE" not in q and k
                for q, k in calls
            ),
        )
        s.command("/exit")
        wait("clean exit", lambda: s.snapshot()["status"] == "exited")
    finally:
        if s.proc:
            s.close()


if __name__ == "__main__":
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    calls = []
    with tempfile.TemporaryDirectory() as temp, serve(fixture_api(calls)) as endpoint:
        base = Path(temp)
        home = base / "home"
        home.mkdir()
        exercise(base / "repo", home, endpoint, calls)
