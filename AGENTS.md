# Project instructions

## Scope and current state

- This file applies throughout the Pycharm-Aider-UI repository. Read any more
  specific `AGENTS.md` before editing files in its directory.
- Aider Deck is a Windows loopback Flask app with a vanilla JS frontend. The
  transport is a real ConPTY. Drive Aider through terminal input and documented
  files. Keep Aider imports and direct provider calls out of the application.
- Treat live coder announcements and launch settings as separate observations.
  Preserve the registration preview, explicit restart, and mode/format regression
  tests when changing model or session handling.
- Work in the current checkout. Use `git rev-parse --show-toplevel` to locate its
  root so commands also work in linked Git worktrees.

## Working practices

- Inspect `git status --short --branch` and relevant files before making changes.
  Preserve unrelated user edits.
- Use PowerShell-compatible commands on Windows and quote paths. Prefer `rg`
  for searches, excluding dependency directories and generated output.
- Keep changes focused on the requested task. Follow established project
  conventions as code is added; the directory name alone does not determine the
  implementation language or framework.
- Keep credentials, local environment files, virtual environments, and tool
  histories out of Git. Add sanitized example configuration when needed.
- Keep shared Codex settings in `.codex/config.toml`. Keep authentication and
  machine-specific settings in the user's Codex home, outside this repository.
- Use the active checkout unless a separate worktree is requested or needed for
  isolation. Never discard changes to make a checkout clean.

## Verification and handoff

- Run `.\.venv\Scripts\python.exe -m pytest -q` and `git diff --check`.
  For changes to the terminal or its browser controls, run the scratch browser
  workflow documented in `README.md`.
- Document reproducible setup, run, and test commands in `README.md` when added.
- Report what changed, what was verified, and any remaining limitations.
