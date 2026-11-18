"""Opt-in, billable checks through Aider using a workspace's saved OpenAI-compatible provider.

Run directly with an explicit workspace path. The scratch repo contains only a one-line fixture.
Credentials go into the child's environment. Never print the transcript or the endpoint URL.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deck import aider_config, envfile  # noqa: E402
from deck.sessions import AiderSession  # noqa: E402


def run(workspace, workflow):
    saved, _ = aider_config.provider_environment(workspace)
    options = aider_config.launch_values(workspace)
    model = options.get("model")
    editor_format = options.get("editor-edit-format") or "editor-diff"
    key = saved.values.get("OPENAI_API_KEY")
    endpoint = saved.values.get("OPENAI_API_BASE") or saved.values.get(
        "OPENAI_BASE_URL"
    )
    if (
        not key
        or not endpoint
        or not isinstance(model, str)
        or not model.startswith("openai/")
    ):
        raise ValueError(
            "This probe requires a saved openai/ model, OpenAI key, and explicit endpoint."
        )
    dest = urlsplit(endpoint)
    if (
        dest.username
        or dest.password
        or dest.query
        or dest.fragment
        or not dest.hostname
    ):
        raise ValueError(
            "Expected an API base URL without embedded credentials or query parameters."
        )
    if dest.scheme != "https" and not (
        dest.scheme == "http" and dest.hostname in ("127.0.0.1", "localhost", "::1")
    ):
        raise ValueError("Use an HTTPS or loopback endpoint.")
    with tempfile.TemporaryDirectory(
        prefix="deck-hosted-check-", ignore_cleanup_errors=True
    ) as temp:
        base = Path(temp)
        home, repo = base / "home", base / "repo"
        home.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        target = repo / "value.txt"
        target.write_text("before\n", encoding="utf-8")
        subprocess.run(["git", "add", "value.txt"], cwd=repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Deck Test",
                "-c",
                "user.email=deck@example.invalid",
                "commit",
                "-qm",
                "Scratch input",
            ],
            cwd=repo,
            check=True,
        )
        (repo / ".aider.conf.yml").write_text(
            json.dumps(
                {
                    "model": model,
                    "weak-model": model,
                    "editor-model": model,
                    "editor-edit-format": editor_format,
                    "edit-format": "diff",
                    "check-update": False,
                    "analytics": False,
                    "gitignore": False,
                    "map-tokens": 0,
                    "auto-commits": False,
                    "yes-always": True,
                    "timeout": 35,
                    "show-model-warnings": False,
                    "detect-urls": False,
                    "suggest-shell-commands": False,
                    "stream": False,
                }
            ),
            encoding="utf-8",
        )
        (repo / ".aider.model.settings.yml").write_text(
            json.dumps([{"name": model, "edit_format": "diff"}]), encoding="utf-8"
        )
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if not envfile.is_sensitive(k)
            and not k.startswith(
                ("OPENAI_", "AIDER_", "LITELLM_", "ANTHROPIC_", "OTEL_")
            )
        }
        clean_env.update(
            HOME=str(home),
            USERPROFILE=str(home),
            OPENAI_API_KEY=key,
            OPENAI_API_BASE=endpoint,
            OPENAI_BASE_URL=endpoint,
            LITELLM_LOCAL_MODEL_COST_MAP="True",
        )
        session = AiderSession(repo)

        def wait(label, condition, limit=75, cursor=None):
            until = time.monotonic() + limit
            while time.monotonic() < until:
                _, raw, _ = session.read_since(cursor, timeout=0)
                output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw)
                if condition(output):
                    print("PASS", label, flush=True)
                    return
                for phrase, reason in (
                    ("insufficient_quota", "insufficient quota"),
                    ("invalid_api_key", "invalid API key"),
                    ("incorrect api key", "invalid API key"),
                    ("model_not_found", "model not found or inaccessible"),
                    ("does not exist", "model unavailable"),
                    ("authenticationerror", "authentication error"),
                    ("unsupported_parameter", "unsupported API parameter"),
                    ("badrequesterror", "bad request"),
                    ("ratelimiterror", "rate limited"),
                    ("permissiondeniederror", "permission denied"),
                    ("apiconnectionerror", "provider connection failed"),
                    ("connection error.", "provider connection failed"),
                ):
                    if phrase in output.lower():
                        raise RuntimeError(f"{label}: provider reported {reason}.")
                if session.snapshot()["status"] == "exited":
                    raise RuntimeError(f"{label}: Aider exited before verification.")
                time.sleep(0.2)
            raise RuntimeError(f"{label}: timed out waiting for the observed result.")

        try:
            session.start(
                {
                    "model": model,
                    "edit-format": "diff",
                    "editor-edit-format": editor_format,
                },
                clean_env,
                [key],
            )
            wait(
                "Aider ready in isolated scratch repo",
                lambda _: not session.snapshot()["files"]["stale"],
            )
            if workflow == "ask-code":
                session.mode("ask")
                wait("Ask mode", lambda _: session.snapshot()["mode"] == "ask")
                session.send(
                    "What is 7354 plus 276? Reply with just the integer, without commas."
                )
                wait(
                    "saved provider produced the expected answer", lambda s: "7630" in s
                )
                assert target.read_text() == "before\n"
                session.mode("code")
                wait(
                    "registered Code mode",
                    lambda _: session.snapshot()["active_format"] == "diff",
                )
            session.command("/add value.txt")
            wait(
                "scratch edit target added",
                lambda _: any(
                    row["path"] == "value.txt"
                    for row in session.snapshot()["files"]["data"]
                ),
            )
            if workflow == "help-context-architect":
                for mode, question, expected in [
                    (
                        "help",
                        "Which Aider slash command lists files in the chat? Reply with just the command.",
                        "/ls",
                    ),
                    (
                        "context",
                        "Which tracked file is needed to change the single line of project data? Reply with the filename.",
                        "value.txt",
                    ),
                ]:
                    session.mode(mode)
                    wait(
                        mode + " coder announced",
                        lambda _: session.snapshot()["active_format"] == mode,
                    )
                    cursor, _, _ = session.read_since(None, timeout=0)
                    session.send(question)
                    wait(
                        mode + " returned the expected answer",
                        lambda text: (
                            expected in text
                            and "Tokens:" in text
                            and session.snapshot()["input_state"] == "ready"
                        ),
                        cursor=cursor,
                    )
                    assert target.read_text() == "before\n"
                session.mode("architect")
                wait(
                    "Architect and saved editor format announced",
                    lambda _: (
                        session.snapshot()["active_format"] == "architect"
                        and session.snapshot()["editor_format"] == editor_format
                    ),
                )
            session.send(
                "In value.txt replace the one line 'before' with 'after'. Apply the edit now, with no explanation."
            )
            wait(
                "real provider edit applied and visible to Git",
                lambda _: (
                    target.read_text(encoding="utf-8").strip() == "after"
                    and "value.txt"
                    in subprocess.check_output(
                        ["git", "diff", "--name-only"], cwd=repo, text=True
                    )
                ),
            )
        finally:
            session.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", type=Path, required=True)
    ap.add_argument(
        "--workflow", choices=("ask-code", "help-context-architect"), default="ask-code"
    )
    try:
        args = ap.parse_args()
        run(args.workspace.resolve(strict=True), args.workflow)
    except (ValueError, RuntimeError) as exc:
        # Provider text and keys never go into error output.
        sys.exit(str(exc))
