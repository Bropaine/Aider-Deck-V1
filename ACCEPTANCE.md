# Aider Deck acceptance results

Tested on Windows with Aider 0.86.2, Python 3.12.10, pywinpty 3.1.2, Git 2.33.0.windows.2, and Chromium. The §15 browser run used real Aider, ConPTY and Git with a local model fixture. Hosted checks used the workspace's saved model, endpoint and API key. All edits took place in disposable repositories.

The hosted Ask test answered **7630** for 7354 + 276 and left the tracked file untouched. After a switch to Code, Aider changed the one-line scratch file from `before` to `after`. In a fresh repository, hosted Help returned `/ls`, Context identified `value.txt`, and Architect announced the owner's saved editor format, `whole`. Its editor applied the requested change and Git showed it. Provider requests contained only the synthetic tasks, scratch file and Aider's own prompts.

## Required automated checks

All **345 Python tests** passed, including attachment-budget checks, live model role parsing and the native Git-hook check against a disposable local remote. All **73 JavaScript tests** passed, including upload warnings, live model role readouts, model picker visibility, context estimates, Settings search and layout preferences.

| Requirement | Result and evidence |
| --- | --- |
| Commented YAML round trip | Passed. Save and default removal retain comments and unrelated options. Revision checks reject stale preview confirmations. |
| Dotenv round trip and masking | Passed. Tests cover comments, quoting, ordering, masked serialization and short secrets. A malformed config can be replaced through a revision-checked editor without exposing its contents. |
| Git porcelain parsing | Staged, unstaged, untracked and renamed paths appeared in the expected groups. Tests covered staging, unstaging before the first commit, discard confirmation, commit history and historical config diff protection. A diverged branch reported one commit ahead and one behind. After deletion of its upstream, the Git inspector still returned history and changes. |
| Settings registry completeness | All 80 §8 options returned a label, provenance and control definition. Tests saved all 79 editable settings, checked their values in workspace YAML and read each one back. The remaining option provides the live version check. |
| Launch builder and flag order | Code supplied `--edit-format`. Architect supplied `--architect --editor-edit-format`. Aider's installed option parser returned `architect` for `--edit-format diff --architect` and `diff` for the reverse order. A real Architect launch announced `architect` and `editor-diff`. |

## Browser and installed-Aider checks

| §15 workflow | Observed result |
| --- | --- |
| Open workspace, start, inspect Effective settings | The folder picker opened a scratch workspace. Starting produced a live `diff` announcement. The Effective settings action obtained a launch `edit_format: diff` through `/settings`. |
| Sticky Ask and Code | Ask displayed the fixture's answer, used the rotated dummy key, and left the tracked file untouched. After registration and restart, Code returned to `diff`. |
| One-shot Ask | The provider received the question once. Aider answered and the sticky mode remained Code. |
| Tree → read-only → editable → drop | All three states appeared in successive real `/ls` readouts. |
| Apply live model | Switched to a second registered model with default `patch`. The browser badge showed `patch`, the new model appeared in fresh `/settings` output, and the launch format remained `diff`. Switching back restored `diff`. |
| Save key and restart | The Keys API and rendered page concealed the saved dummy key. The restart notice appeared. Aider subsequently authenticated to the local fixture with the replacement value. |
| Edit, inspect diff, stage, commit | Aider applied the fixture edit to `target.txt`. The Git panel displayed `+after`, staging put that file in the real index, and the UI commit appeared in both the panel and command-line Git history. |
| Close browser and reconnect | A new browser page received the buffered transcript and retained composer history. The real Aider PID stayed the same. |
| Load and send a prepared prompt | Loading a Markdown prompt filled the composer. Sending delivered one message containing the original lines, indentation, and literal braces. The full run generated four provider requests for four submitted messages. |

The run also deliberately reproduced the original failure. The unregistered notice persisted, the Code button required acknowledgement, and Aider announced `whole` on returning from Ask despite its `diff` launch setting. Confirming the preview added the model to the workspace model settings file. Only an explicit restart loaded the new default.

Additional checks exercised workspace switching, first-run preset confirmation, offline xterm loading, responsive layout, and keyboard input after replay. The terminal tests cover ANSI-split secrets, short and wrapped announcements, stale output, tagged multiline messages, and terminal device reports. The last check prevents replayed terminal queries from becoming accidental prompts.

## Additional paths from the user manual

The external attachment, saved model and missing-download workflows have dedicated Chromium smoke runs. Each uses real Aider with local fixtures.

| Workflow | Observed behavior |
| --- | --- |
| External prompt attachments | The picker attached multiple files, and dragging added Markdown, PNG and JPEG. The draft survived. Only an explicit Send reached the model fixture, with the exact selected content. Removing an attachment excluded it from the request. A same-named repository file stayed intact, Git stayed clean, and restarting cleared the temporary copies. A text-only model declined an image. |
| Remaining context for text uploads | Chromium attached a text file larger than 256 KiB on a local fixture with a 524,288-token window. After switching to a 16,384-token window, one chapter fit and the pair triggered a warning against the remaining tokens. Near-limit and unknown usage also prompted. Cancel preserved the draft and created no temporary files. Attach anyway created the reviewed copies. A real Ollama run verified the warning against 7,967 remaining tokens. |
| Saved model picker | Workspace bookmarks and their visibility survived a reload and a fresh Deck instance. The browser enabled an installed Ollama tag and a cloud entry. A missing Anthropic key kept the entry disabled until a synthetic key was saved. Choosing a name, applying it live and saving it for next launch produced three distinct outcomes. |
| Provider references | Chromium checked the links under all eight standard key fields, opened a portal in a new tab, and found no provider link on the endpoint or custom variable. The picker offered the published Nemotron and OpenAI references and a general Ollama library link for a custom tag. Popup tests intercepted navigation locally. |
| Local model download gate | The picker identified a missing tag. Saving it for next launch retained the current process. Apply live and a confirmed restart both stopped before switching. A fixture pull failed, then succeeded on retry. Aider switched only after another explicit Apply live. |
| Editing after model registration | With the configuration refresh held open, the registration dialog stayed visible until the refreshed controls were ready. The next model save carried the exact typed ID. Local and hosted reasoning controls subsequently reported the selected models’ levels. |
| Terminal and Composer layout | On desktop, hiding either side panel increased the Terminal and Composer widths. A native Windows console probe confirmed the new rows and columns. Mouse and keyboard resizing preserved the draft, PID and attached file. Reload restored the layout, the MODEL shortcut reopened the Inspector, and both toggles worked at tablet and phone widths. |
| Side panel widths | At 1440 pixels, narrowing Explorer by 60 pixels and Inspector by 80 pixels increased the native terminal from 100 to 119 columns with both panels open. Each drag kept the other panel’s width. Keyboard resizing, individual reset, hide/show, reload and narrow-screen layout passed in Chromium with the same Aider process. |

| Controls | What the run verified |
| --- | --- |
| Launch and port selection | Two calls to `run.bat --port` on the same disposable port selected the same URL and served from the original process. Another port uses a separate state file. |
| Terminal | An inherited `TERM=dumb` reproduced the screenshot. With the fix, Chromium focused xterm, moved the caret with arrow keys and Alt-click, and executed a command repaired mid-line. Drag selection, browser copy/paste events, interrupt ordering and reconnect passed. Clipboard tests used isolated data. |
| Startup and draft handling | At a real Aider confirmation about ignoring `.env`, Deck held the last selected mode while waiting for the user's answer. The live badge changed after the coder announcement. Switching modes, including guarded Code, preserved the composer draft. The startup guard was also exercised with a 28-column terminal. |
| Sticky and one-shot modes | All five sticky modes announced their coder and accepted a prompt. Architect waited for approval. Answering Yes sent the plan to a distinct editor model using `editor-diff`, changed the scratch file and returned to Architect. The decline path also passed. One-shot Ask, Code, Architect and Context each delivered a request and returned to Code. The next prompt after a multiline Context request arrived independently. One-shot Help reached Aider's optional help-extras installation question. Declining returned to Code. An indexed Help answer requires the extra package and was outside this run. |
| Model discovery and formats | Installed Aider returned 97 matches for `gpt-4o`, and its version check reported current and latest versions. Aider announced all nine formats offered in Settings, including `udiff-simple` and `editor-diff-fenced`. Invalid choices were rejected at save, registration and live apply. A rejected restart preserved the running process. |
| Architect launch with conflicting settings | An alias list caused Aider's parser to apply the YAML main format after `--architect`. Tests covered that case and a conflicting dotenv setting together with a custom environment file. Architect started with its selected editor format and the custom values reached its child process. Deck's workaround is scoped to the launched process. |
| Existing invalid model settings | A hand-edited unsupported coder default caused native Aider to exit when entering Code or switching model. Deck's controls now identify the affected model and file before a switch or restart. An invalid entry for another model can remain while the owner edits other fields or works with a valid model. |
| Settings widgets | Chromium saved all 79 editable options to real YAML, exercised both theme flags through the shared selector, and used the color pickers and model suggestions. Filtering for the other theme flag retained the active choice. The update button displayed Aider's response in the offline browser run. The online version lookup passed separately. |
| Live settings and quick actions | The browser exercised the six live setting types, including main, weak and editor model changes. All 21 command palette actions reached Aider. Checks included `/diff`, a real `/undo` verified against Git HEAD and file contents, local-page `/web`, lint and test commands, `/save` and `/load`, clipboard export, and confirmed `/clear`, `/reset` and `/exit`. |
| Weak model after a live main-model switch | Starting with one model in both roles, Chromium switched the main model with **Apply live**. The Weak model row and **Effective settings** both displayed the previous model as the live weak model. Its next-launch field stayed blank. A native `/weak-model` change updated the readout and preserved the unsaved text in that field. |
| Model-specific reasoning effort | Chromium selected an Ollama tag with two named levels, an official OpenAI model with five, a model with thinking disabled, and a gateway with manual entry. A previously saved value survived model changes and page reload. Retrying picked up newly available Ollama metadata. Save and Apply live reached different model targets, and real Aider sent the chosen values to the local OpenAI-compatible fixture. |
| Effort documentation and older servers | Chromium opened the source link for Terra and verified that switching to GPT-5.4 changed its destination. A simulated missing capability endpoint displayed the older-server message and a manual field. Retrying restored Terra’s six levels with the typed `max` choice still selected. |
| Settings after reviewing the effort warning | The browser reproduced the stuck filter before the fix. After the fix, confirming the repair and restarting Aider restored every original Settings group on the same tab and page. Typing a filter during review preserved it across a restart, including a search for `reasoning-effort` itself. |
| Saved effort ignored at startup | Real Aider warned for `openai/gpt-5.6-terra` and omitted the effort on the first request. Chromium opened Settings from **Review reasoning effort** and applied `high` in the same session. Cancelling the model-specific YAML preview left the file untouched. Confirming kept the existing PID, Code format, `thinking_tokens` declaration and global model check. After an explicit restart, the first request carried `high` and the warning cleared. Fresh model registration also exercised the checked and unchecked declaration options. All chat requests went to a loopback fixture. |
| Ollama adapter and temperature | Aider 0.86.2 sent `reasoning_effort: high` in the native `/api/chat` test and omitted Ollama’s `think` field. On Ollama’s compatible `/v1` route, the browser saved a named level, edited the model’s temperature to 0.2, and restarted scratch Aider. The fixture received `high` and numeric 0.2. Applying `low` live changed the next request’s effort and preserved the temperature. An isolated real Ollama 0.40.11 server reported boolean-only controls for the installed `nemotron-3-nano:30b` tag. Reading its metadata left the runner unloaded. |
| Keys and workspace controls | All nine named key and endpoint fields and an arbitrary environment variable saved, returned masked and passed presence checks. Workspace switching, saved composer preferences, prompt create/read/overwrite/delete confirmation, directory browsing, file glob expansion and ignored-file visibility were exercised against disposable files. |
| Library, composer and file panels | Chromium exercised save, overwrite, load and delete, with both confirmation outcomes. Enter and Shift+Enter, prompt history and saved preferences survived reload. Raw YAML and JSON edits covered valid changes, parse errors and replacing a damaged file. Saving `.aiderignore` immediately refreshed an expanded directory. Globs used the active ignore rules. |
| Local Ollama setup | In a scratch workspace, Chromium saved an alternate loopback `OLLAMA_API_BASE` through Keys and selected `ollama_chat/nemotron-3.5-bolt:e4b` via the MODEL badge. Real Aider entered Ask and the installed model answered 379 + 824 correctly. The temporary Ollama server stopped at the end of the run. |
| Token reports on request | Across two real Aider replies, the footer estimate changed from 40 to 72. The terminal returned to its prompt both times. Clicking **Context** and typing `/tokens` explicitly each produced the full report. Running `/diff` immediately after a model edit showed the changed line. |
| Local context tracker | A real local Ollama reply filled the bar using its footer and the matching runner’s window. An explicit report supplied per-file estimates. Chromium exercised a 4,096-token runner, a labeled 8,192-token model capacity while the runner allocation was unavailable, and a live switch to an Ollama tag through the OpenAI-compatible endpoint. Tests also covered old reports after a model switch. |
| Pre-push checks | A scratch browser session found the staged dummy key even after the working file was cleaned. An actual Git push to a disposable local bare remote was stopped after a key had been committed and subsequently removed. The remote ref stayed at its previous commit, and a clean push succeeded. Separate tests covered multiple refs, first pushes, force updates, tags, commit metadata, custom hooks, and preserved hook files. A dry scan of all 71 publication-eligible working files in a scratch index produced no findings. |
| Ollama library and download controls | Chromium saved multiple tags, reloaded the workspace, removed a bookmark and kept its installed model. A fixture exercised service failure and refresh, download progress with an unknown size, a 25% progress update, an interrupted download and a successful retry. Cancelling a local-model switch retained the original Aider PID. Confirming started the replacement. An older Deck server displayed an update notice. |
| One-click startup and local inference | Ollama 0.40.11 downloaded `smollm2:135m-instruct-q4_0` (92 MB) into a disposable library. The installed-model list and running-model list confirmed the tag. Aider used the local tag for its main, weak and editor roles, answered a question about the color of the sky, and reported token usage. After stopping the test daemon and exiting Aider, a single **Use in Aider** click started both again and showed the same model in the live badge. The test removed its temporary library. |
| Individual files and groups | Chromium attached editable and read-only files individually and across folders, and exercised clearing, submitting and retrying selections. The grouped API validated the full list before sending one command. Real Aider reported all 360 files in a larger read-only selection. |
| Drop files individually or together | In Chromium and real Aider, **Drop** removed one attachment. **Drop selected** removed a mixed editable/read-only group in one request. Files with spaces and literal brackets were dropped individually by name, and similarly named siblings stayed attached. Clearing checked boxes sent no command. A rejected batch preserved its selections, and the on-disk files matched their original bytes. |
| Drop with an older server | With the grouped-drop capability omitted, the browser sent individual requests. It stopped on ambiguous names, preserved the second workspace's same-named file when switching mid-drop and retried only the outstanding selection. The original session kept its PID. |
| Attaching groups to a session started by an older server | With the old page template and API, refreshing loaded the checkboxes. After a partial failure, the first file was attached and retry sent only the remaining two. Switching workspaces during submission stopped further requests to the previous workspace. Returning restored its outstanding selection and the original Aider PID. |

Aider's `/diff` compares against the preceding message. An intervening command can advance that baseline. The Git tab's working and staged diffs remained available.

### Root ignore file verification

**Git staging.** In two disposable repositories, Git's own ignore matcher and real `git add .` and `git add -A` operations excluded 34 synthetic private paths and staged 15 expected project paths, including the empty prompt-directory marker. The checks covered case-sensitive and case-insensitive matching, dotenv examples, SSH and certificate keys, Aider configuration and history, prompt contents and Deck state. The regression is in `tests/test_gitignore.py`.

**Aider context.** A scratch Git repository contained tracked dummy `.env` and `.aider.chat.history.md` files alongside `app.py` and `deck/files.py`. Installed Aider 0.86.2 listed and mapped all four before applying this repository's [exact ignore patterns](.aiderignore). With the rules in place, the dummy secrets disappeared from `/ls` and `/map`, and normal `/add` refused them. Aider still mapped both source files and accepted `/add app.py`. The installed ignore matcher also passed 38 exclusion and 25 inclusion checks, including project configuration and build inputs.

A separate check with outbound connections blocked found both dummy markers in Aider's assembled read-only context after explicit attachments. The native `--read` option loaded the dummy environment file, and enabling `--restore-chat-history` restored the ignored dummy conversation. The check used synthetic contents and issued no model requests.

In the actual checkout, Aider's matcher excluded both private paths and allowed `app.py`, every Python module in `deck/`, the main JavaScript and HTML, tests, `presets.json`, `README.md` and `.codex/config.toml`. The scratch map used tracked source files because this checkout's new application files were still untracked during the test. The regression checks in `tests/test_aiderignore.py` cover the private paths and build inputs.

<details>
<summary>All 37 patterns verified in the root .aiderignore</summary>

```gitignore
.[eE][nN][vV]
.[eE][nN][vV].*
*.[pP][eE][mM]
*.[kK][eE][yY]
*.[pP]12
[iI][dD]_[rR][sS][aA]*
*[sS][eE][cC][rR][eE][tT]*
*[cC][rR][eE][dD][eE][nN][tT][iI][aA][lL]*
*[tT][oO][kK][eE][nN]*
.aider.chat.history.md
.aider.input.history
.aider.tags.cache*
.aider.conf.yml
/.aider.model.settings.yml
/.aider.model.metadata.json
/deck-state.json
/deck-state.json.*.tmp
/deck-state-[0-9]*.json
/deck-state-[0-9]*.json.*.tmp
/prompts/
*.log
/.tmp/
/tmp[a-z0-9_][a-z0-9_][a-z0-9_][a-z0-9_][a-z0-9_][a-z0-9_][a-z0-9_][a-z0-9_]
/.codex/auth.json
/.codex/history.jsonl
/.codex/sessions/
/.codex/archived_sessions/
/.codex/log/
/.codex/tmp/
/.codex/*.sqlite*
.git/
.venv/
venv/
__pycache__/
node_modules/
.pytest_cache/
.ruff_cache/
```

</details>

## Implementation details that affect interpretation

- Registration means an entry in Aider's searched model settings files. The UI identifies its source and records which model definitions were present at launch. An installed built-in model can still lack a user-file entry.
- The active coder can be `ask`, `architect`, or an editing format. The format badge displays Aider's announcement even when this differs from the format in its option list. Unrecognized readouts retain raw text and an explicit stale state.
- Aider's generic YAML `api-key` and `set-env` options can contain credentials for additional providers. Some options can appear in plaintext in Aider's native history when `/settings` runs. Deck checks for those options before starting and identifies the option to migrate to ordinary `.env` storage. It also checks existing launch configuration for one-shot actions that would execute in place of an interactive session.
- Structured file actions expand file globs inside the selected workspace, filter ignored matches, validate the resulting paths, and send the selected names to Aider. This protects subdirectory workspaces when Aider resolves relative input against the Git root.
- The server owns session processes and the replay buffer. Browser reconnect is supported while the server runs. A server or machine restart requires an explicit new session.

The [README verification section](README.md#verification) lists the commands for the §15 browser run, additional manual paths and opt-in hosted check.
