# Pycharm-Aider-UI

Project workspace initialized for local development with Codex. Application
code, dependencies, and run/test commands have not been added yet.

## Open the project

The primary Git working tree is `G:\Pycharm-Aider-UI`. Open this directory in
PyCharm or Codex. From PowerShell, the Codex CLI can be started with:

```powershell
codex -C 'G:\Pycharm-Aider-UI'
```

## Repository setup

- `AGENTS.md` provides repository-wide instructions for coding agents.
- `.codex/config.toml` contains minimal shared Codex settings. Model, reasoning,
  approval, and sandbox choices inherit from the active user/app configuration.
- `.gitignore` excludes local secrets, tool histories, dependencies, and caches.
- `.gitattributes` keeps text line endings consistent across checkouts.

The repository starts on `main` with a local setup commit. No remote is configured.

Codex loads project configuration for trusted projects. If a new Codex session
asks whether to trust this repository, review these files and select the trust
option in the app. See the [official project configuration documentation](https://learn.chatgpt.com/docs/config-file/config-advanced).

## Check the workspace

Run from the repository root:

```powershell
git status --short --branch
git worktree list
git diff --check
```

With Python 3.11 or newer, validate the shared configuration without installing
dependencies:

```powershell
python -c "import pathlib, tomllib; tomllib.loads(pathlib.Path('.codex/config.toml').read_text(encoding='utf-8')); print('Codex TOML valid')"
```

## Optional isolated worktree

The project root is already the primary working tree. When separate task
isolation is needed, run this from the root, choosing a new branch and directory
name for each task:

```powershell
git worktree add '.worktrees/my-task' -b 'codex/my-task' main
codex -C 'G:\Pycharm-Aider-UI\.worktrees\my-task'
```

`.worktrees/` is ignored by the primary checkout. Each linked worktree receives
the committed project instructions and configuration.
