"""Exercise public-repository ignore rules using Git's real staging behavior."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]

PRIVATE = {
    ".env", ".env.example", "config/.env.prod.example", "demo/.ENV.Local",
    "certs/client.PEM", "certs/client.key", "client.P12", "certs/key.pfx", "putty.PpK",
    "ssh/id_rsa.backup", "ssh/ID_ed25519", "ssh/id_ecdsa.old", "ssh/id_dsa",
    "secrets.txt", "private/CredentialStore/data.json", "github-Token.txt",
    ".aider.input.history", ".aider.chat.history.md", ".aider.tags.cache.v4/cache.db",
    ".aider.conf.yml", ".aider.model.settings.yml", ".aider.model.metadata.json",
    "deck-state.json", "deck-state-5179.json", "deck-state.json.tmpname.tmp",
    "deck-state-5179.json.tmpname.tmp", "tmpa7k8_b1z",
    "prompts/private.md", "prompts/nested/session.txt", "logs/session.log",
    ".tmp/session/transcript.txt", ".codex/auth.json", ".codex/history.jsonl",
    ".codex/sessions/run.jsonl",
}
PUBLIC = {
    ".gitignore", ".aiderignore", ".codex/config.toml", "prompts/.gitkeep",
    "app.py", "deck/files.py", "static/app.js", "templates/index.html",
    "tests/test_gitignore.py", "README.md", "requirements.txt", "presets.json",
    "docs/example.yml", "tests/fixtures/.aider.model.settings.yml",
    "tests/fixtures/.aider.model.metadata.json",
}


@pytest.mark.parametrize(("case_sensitive", "add_arg"), [(False, "."), (True, "-A")])
def test_regular_git_add_leaves_private_files_out(tmp_path, case_sensitive, add_arg):
    # An inherited index or a user's global ignore file could hide a regression.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

    def git(*args, data=None, check=True):
        return subprocess.run(
            ["git", "-c", f"core.excludesFile={os.devnull}", *args],
            cwd=tmp_path, env=env, input=data, capture_output=True, check=check,
        )

    git("init", "--quiet", "--template=")
    git("config", "core.ignoreCase", "false" if case_sensitive else "true")
    shutil.copyfile(ROOT / ".gitignore", tmp_path / ".gitignore")
    for name in (PRIVATE | PUBLIC) - {".gitignore"}:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("disposable test fixture\n", encoding="utf-8")

    candidates = b"".join(name.encode() + b"\0" for name in sorted(PRIVATE | PUBLIC))
    ignored = git("check-ignore", "-z", "--stdin", data=candidates).stdout
    assert set(ignored.decode().split("\0")[:-1]) == PRIVATE
    assert git("add", "--", ".env", check=False).returncode != 0
    git("add", add_arg)
    staged = git("ls-files", "-z").stdout
    assert set(staged.decode().split("\0")[:-1]) == PUBLIC
