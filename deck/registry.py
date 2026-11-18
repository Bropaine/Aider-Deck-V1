"""Aider 0.86 option reference. None denotes unset or a model/runtime default."""
FORMATS = {
    "diff": "SEARCH/REPLACE blocks",
    "diff-fenced": "SEARCH/REPLACE with the path inside the fence (Gemini)",
    "udiff": "Simplified unified diff",
    "udiff-simple": "Simple unified diff",
    "patch": "Patch instructions",
    "whole": "Warning: complete file listings can be slow and costly",
    "editor-diff": "Streamlined SEARCH/REPLACE for the architect's editor",
    "editor-diff-fenced": "Streamlined SEARCH/REPLACE with the path inside the fence",
    "editor-whole": "Streamlined complete files for the architect's editor",
}
# Model settings may select one of Aider's non-editing coders as their default.
# "code" is a /chat-mode shortcut for the model default, not a coder itself.
MODEL_DEFAULT_FORMATS = FORMATS.keys() | {"ask", "architect", "help", "context"}
LIVE = {
    "model": "/model", "weak-model": "/weak-model", "editor-model": "/editor-model",
    "reasoning-effort": "/reasoning-effort", "thinking-tokens": "/think-tokens",
    "edit-format": "/chat-mode",
}
CHOICES = {
    "edit-format": list(FORMATS), "editor-edit-format": list(FORMATS),
    "map-refresh": ["auto", "always", "files", "manual"],
    "line-endings": ["platform", "lf", "crlf"],
}
# Defaults describe Aider's option parser, before Deck's explicit launch arguments.
GROUPS = {
    "Main model": [
        ("model", "text", None, "Main model ID. Enter any ID accepted by your provider."),
    ],
    "Model settings": [
        ("model-settings-file", "text", ".aider.model.settings.yml", "Additional YAML model definitions."),
        ("model-metadata-file", "text", ".aider.model.metadata.json", "Additional model context windows and prices."),
        ("alias", "list", None, "One alias:model pair per line."),
        ("reasoning-effort", "text", None, "Choose a reasoning effort for the main model."),
        ("thinking-tokens", "text", None, "Thinking budget for supported models. Zero disables thinking."),
        ("verify-ssl", "toggle", True, "Verify provider TLS certificates."),
        ("timeout", "number", None, "API request timeout in seconds."),
        ("edit-format", "select", None, "Requested Code launch format. The active coder announces its own format."),
        ("architect", "toggle", False, "Start in Architect mode using the selected editor format."),
        ("auto-accept-architect", "toggle", True, "Accept the architect's proposed changes automatically."),
        ("weak-model", "text", None, "Model used for commit messages and chat summaries."),
        ("editor-model", "text", None, "Model used to edit files in Architect mode."),
        ("editor-edit-format", "select", None, "Edit format used by the architect's editor."),
        ("show-model-warnings", "toggle", True, "Show warnings about missing model metadata."),
        ("check-model-accepts-settings", "toggle", True, "Check model support before applying reasoning settings."),
        ("max-chat-history-tokens", "number", None, "Soft token limit before Aider summarizes history."),
    ],
    "Cache": [
        ("cache-prompts", "toggle", False, "Cache provider prompts where supported."),
        ("cache-keepalive-pings", "number", 0, "Number of cache keepalive requests at five-minute intervals."),
    ],
    "Repo map": [
        ("map-tokens", "number", None, "Token budget for the repository map. Zero disables it."),
        ("map-refresh", "select", "auto", "When Aider refreshes its map."),
        ("map-multiplier-no-files", "number", 2, "Map budget multiplier when no files are in chat."),
    ],
    "History files": [
        ("input-history-file", "text", ".aider.input.history", "Input history path, normally in the repository root."),
        ("chat-history-file", "text", ".aider.chat.history.md", "Conversation transcript path, normally in the repository root."),
        ("restore-chat-history", "toggle", False, "Load previous chat messages at startup."),
        ("llm-history-file", "text", None, "Optional log of messages exchanged with the model."),
    ],
    "Output": [
        ("dark-mode", "toggle", False, "Use colors for a dark terminal background."),
        ("light-mode", "toggle", False, "Use colors for a light terminal background."),
        ("pretty", "toggle", True, "Render colored, formatted terminal output."),
        ("stream", "toggle", True, "Stream replies as tokens arrive."),
        ("code-theme", "text", "default", "Pygments syntax highlighting theme: https://pygments.org/styles/"),
        ("show-diffs", "toggle", False, "Print diffs when Aider commits."),
    ],
    "Advanced colors": [
        ("user-input-color", "color", "#00cc00", "Text entered at the Aider prompt."),
        ("tool-output-color", "color", None, "Ordinary Aider output."),
        ("tool-error-color", "color", "#FF2222", "Error messages."),
        ("tool-warning-color", "color", "#FFA500", "Warnings."),
        ("assistant-output-color", "color", "#0088ff", "Assistant replies."),
        ("completion-menu-color", "color", None, "Completion menu text."),
        ("completion-menu-bg-color", "color", None, "Completion menu background."),
        ("completion-menu-current-color", "color", None, "Selected completion text."),
        ("completion-menu-current-bg-color", "color", None, "Selected completion background."),
    ],
    "Git": [
        ("git", "toggle", True, "Use Git repository information in Aider."),
        ("gitignore", "toggle", True, "Allow Aider to add its local files to .gitignore."),
        ("add-gitignore-files", "toggle", False, "Allow adding files ignored by Git."),
        ("aiderignore", "text", ".aiderignore", "Path to Aider-specific exclusion rules."),
        ("subtree-only", "toggle", False, "Limit Aider to the current repository subtree."),
        ("auto-commits", "toggle", True, "Commit model edits automatically. Deck launches with this off unless configured."),
        ("dirty-commits", "toggle", True, "Allow Aider to commit existing changes."),
        ("attribute-author", "toggle", None, "Attribute Aider edits in the Git author name."),
        ("attribute-committer", "toggle", None, "Attribute Aider commits in the committer name."),
        ("attribute-commit-message-author", "toggle", False, "Prefix messages for Aider-authored edits."),
        ("attribute-commit-message-committer", "toggle", False, "Prefix messages for Aider-created commits."),
        ("attribute-co-authored-by", "toggle", True, "Add a Co-authored-by trailer for Aider."),
        ("git-commit-verify", "toggle", False, "Run Git commit hooks for Aider commits."),
        ("commit-prompt", "text", None, "Prompt used to write commit messages."),
        ("dry-run", "toggle", False, "Preview edits without writing them."),
        ("skip-sanity-check-repo", "toggle", False, "Skip repository checks at startup."),
        ("watch-files", "toggle", False, "Watch for AI coding comments in files."),
    ],
    "Fixing": [
        ("lint-cmd", "list", [], "One language: command entry per line."),
        ("auto-lint", "toggle", True, "Lint files after edits."),
        ("test-cmd", "text", None, "Command to run project tests."),
        ("auto-test", "toggle", False, "Run tests after edits."),
    ],
    "Analytics": [
        ("analytics", "toggle", None, "Enable or disable analytics for this Aider session."),
        ("analytics-log", "text", None, "Log Aider analytics events locally."),
        ("analytics-disable", "toggle", False, "Disable Aider analytics permanently. Enabled in the Harbor profile."),
        ("analytics-posthog-host", "text", None, "Host used by Aider for analytics."),
        ("analytics-posthog-project-api-key", "text", None, "Write-only PostHog project key."),
    ],
    "Other": [
        ("chat-language", "text", None, "Language used for chat."),
        ("commit-language", "text", None, "Language used for commit messages."),
        ("vim", "toggle", False, "Use vi-style terminal editing."),
        ("fancy-input", "toggle", True, "Enable completion and prompt history."),
        ("multiline", "toggle", False, "Use Enter for newline and Meta+Enter to submit in Aider."),
        ("suggest-shell-commands", "toggle", True, "Allow Aider to suggest shell commands."),
        ("detect-urls", "toggle", True, "Detect URLs in chat messages."),
        ("notifications", "toggle", False, "Notify when replies are ready."),
        ("notifications-command", "text", None, "Custom local notification command."),
        ("encoding", "text", "utf-8", "Encoding for source files."),
        ("line-endings", "select", "platform", "Line endings used for writes."),
        ("editor", "text", None, "Editor used by Aider's /editor command."),
    ],
    "Upgrading": [
        ("check-update", "toggle", True, "Check for new versions on launch. Use Check now for a manual check."),
    ],
}
REGISTRY = {
    key: dict(key=key, flag="--" + key, env="AIDER_" + key.upper().replace("-", "_"),
              label=key.replace("-", " ").capitalize(), group=group, type=kind,
              default=default, description=description, live=LIVE.get(key),
              choices=CHOICES.get(key, []))
    for group, definitions in GROUPS.items()
    for key, kind, default, description in definitions
}
