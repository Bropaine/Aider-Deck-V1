"""Keep Deck's private runtime data out of Aider's normal repository view."""

from pathlib import Path

from pathspec import GitIgnoreSpec


ROOT = Path(__file__).resolve().parents[1]


def test_private_files_and_runtime_paths_are_ignored():
    rules = GitIgnoreSpec.from_lines((ROOT / ".aiderignore").read_text().splitlines())
    paths = [
        ".env", "demo/.env.local", ".env.example", "keys/client.PEM",
        "keys/client.key", "client.P12", "id_rsa.pub", "nested/SECRET-notes.txt",
        "MyCredentials/list.txt", "AuthTokens/debug.json", ".aider.chat.history.md",
        ".aider.input.history", ".aider.tags.cache.v4/cache.db", ".aider.conf.yml",
        ".aider.model.settings.yml", ".aider.model.metadata.json", "deck-state.json",
        "deck-state-5179.json", "deck-state.json.12abc.tmp", "deck-state-5179.json.12abc.tmp",
        "tmph6_83q1a", "prompts/notes.md", ".tmp/recording.txt", "logs/session.log",
        ".codex/auth.json", ".codex/history.jsonl", ".codex/sessions/a.jsonl",
        ".codex/archived_sessions/a.jsonl", ".codex/log/session.txt", ".codex/tmp/file",
        ".codex/state.sqlite-wal", ".git/config", ".venv/bin/python", "venv/scripts/aider.exe",
        "deck/__pycache__/files.pyc", "node_modules/pkg/index.js", ".pytest_cache/v/cache/nodeids",
        ".ruff_cache/version/entry",
    ]
    assert [path for path in paths if not rules.match_file(path)] == []


def test_build_inputs_remain_available_to_aider():
    rules = GitIgnoreSpec.from_lines((ROOT / ".aiderignore").read_text().splitlines())
    paths = [
        "app.py", "deck/files.py", "static/app.js", "templates/index.html",
        "tests/test_aiderignore.py", "README.md", "AGENTS.md", "ACCEPTANCE.md",
        ".codex/config.toml", "requirements.txt", "presets.json", "run.bat",
        ".aiderignore", ".gitignore", ".gitattributes", "docs/example.yml",
        "tests/fixtures/.aider.model.settings.yml", "tests/fixtures/.aider.model.metadata.json",
    ]
    assert [path for path in paths if rules.match_file(path)] == []
