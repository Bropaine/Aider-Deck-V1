# Project instructions

## Scope and current state

- This file applies throughout the Pycharm-Aider-UI repository. Read any more
  specific `AGENTS.md` before editing files in its directory.
- This is a newly initialized project. No application code, dependency manifest,
  build command, or test suite exists yet. Update this guidance and `README.md`
  when those are introduced; do not report nonexistent checks as passing.
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

- Run checks appropriate to the changed behavior once the relevant tooling
  exists. For the current setup, validate TOML syntax and run `git diff --check`.
- Document reproducible setup, run, and test commands in `README.md` when added.
- Report what changed, what was verified, and any remaining limitations.
