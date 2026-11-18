# Aider-Deck

Aider-Deck is a local web UI for the Aider coding assistant on Windows, Linux, and macOS. Use Ollama or a cloud model, add or drop project files, switch coding modes, and review Git diffs and commits in your browser. Each workspace has a real Aider terminal that stays running when you switch tabs or reconnect.

## First-time setup on Windows

**1. Install Python and Git.** Python 3.12 is the version tested with Deck. Install Python for Windows and Git for Windows, then open a **new PowerShell** window. Both commands below should print a version. If Windows cannot find one, finish its installer, enable its command-line/PATH option and reopen PowerShell. [Python for Windows](https://www.python.org/downloads/windows/) · [Git for Windows](https://git-scm.com/install/windows)

```powershell
python --version
git --version
```

**2. Install Aider.** Run these commands in PowerShell. The first installs Aider's installer, and the second installs the actual `aider` command into its own environment. Open a new PowerShell window afterward and run `aider --version`. If the installer reports a directory to add to PATH, follow that instruction so Deck can find Aider. [Aider installation guide](https://aider.chat/docs/install.html)

```powershell
python -m pip install aider-install
aider-install
```

**3. Get Aider-Deck and start it.** If you already have this repository, open PowerShell in the folder containing `run.bat`. For a fresh clone, copy the repository's HTTPS clone URL from its **Code** menu, then run the following and paste the URL when prompted.

```powershell
git clone (Read-Host 'Paste the Aider-Deck clone URL') Aider-Deck
Set-Location .\Aider-Deck
```

Start Deck from that folder, or double-click **run.bat** in File Explorer.

```powershell
.\run.bat
```

On first launch, Deck creates its own `.venv`, installs the web app's dependencies and opens [Aider-Deck on port 5177](http://127.0.0.1:5177/). Keep the launcher window open while working.

**4. Start your first chat.** Click **Open workspace** and choose the folder containing the project you want help with. For a local model, install Ollama for Windows, then open **Inspector → Ollama**. If the service is stopped, click **Start Ollama**. Click **Refresh Ollama** to list installed tags and **Use in Aider** beside the model you want. If the list is empty, enter a library tag such as `qwen2.5-coder:1.5b`, click **Download model**, wait for **Installed**, then click **Use in Aider**. Select **Ask** and send a short question to verify the setup. [Ollama for Windows](https://docs.ollama.com/windows) · [Example model](https://ollama.com/library/qwen2.5-coder:1.5b)

For a hosted model, open **Keys**. The link under each built-in provider key opens its public API platform or setup guide, where you can sign in or create an account. Save the key in Deck. If your provider requires a custom endpoint, save its base URL there too. Open **MODEL**, choose or type the provider’s model ID, click **Save for next launch**, then **Start session**. A successful chat reply verifies the connection.

## First-time setup on Linux and macOS

Install Git and Python 3.11 or newer with `venv` support, and make sure `python3 --version` and `git --version` work in your terminal. On Linux distributions that package `venv` separately, install the package for your Python version. Clone Aider-Deck and run the launcher from the checkout.

```sh
git clone https://github.com/Bropaine/Aider-Deck.git
cd Aider-Deck
./run.sh
```

The launcher creates `.venv` as needed and installs the web app dependencies, pytest, and the `aider` command there. Open [Aider-Deck on port 5177](http://127.0.0.1:5177/) if the browser does not open automatically. Keep the launcher running, then click **Open workspace** and choose your project. Use **Keys** and **MODEL** to configure a hosted model or **Inspector → Ollama** to select an installed local model.

Use `./run.sh --port 5180` to start on a different port. To start in the foreground without opening a browser after setup, activate the environment and run the app directly.

```sh
. .venv/bin/activate
python app.py
```

## Run from a portable package

Download the archive for your machine from a successful **Portable packages** Actions run. Use `windows-x86_64` on 64-bit Intel or AMD Windows, `linux-x86_64` on 64-bit Intel or AMD Linux with glibc, or `macos-arm64` on Apple Silicon. Install Git separately. The archive includes Python and Deck's web dependencies.

On Windows, extract the ZIP to a writable location and double-click **run.bat**. You can also start it from PowerShell in the extracted folder with `.\run.bat`.

On Linux or macOS, extract the tarball to a writable location and run `./run.sh` from the extracted folder. For example, on Linux:

```sh
tar -xzf aider-deck-linux-x86_64.tar.gz
cd aider-deck-linux-x86_64
./run.sh
```

Deck uses an existing `aider` on your `PATH`. If it finds none, the launcher offers to install the Aider CLI into a private environment inside this copy of Deck. The first install requires internet access. You can skip it and use the rest of Deck. Run `.\run.bat --install-aider` on Windows or `./run.sh --install-aider` on Linux and macOS to opt in later or from a script. Deck reuses the private install on later launches from the same folder. If you move the extracted folder, rerun the installer to recreate its environment at the new location. The launcher serves port 5177 by default and accepts `--port`.

To build and smoke-test the ZIP from a committed checkout on 64-bit Windows, use Python 3.12 with pip, Git and curl:

```powershell
python packaging/build.py windows-x86_64
python packaging/smoke.py dist/aider-deck-windows-x86_64.zip
```

On Linux or macOS, build the tarballs with the same prerequisites:

```sh
python3 packaging/build.py linux-x86_64
python3 packaging/build.py macos-arm64
```

The build writes to `dist/`. Run `python3 packaging/smoke.py dist/aider-deck-linux-x86_64.tar.gz` on Linux or substitute `dist/aider-deck-macos-arm64.tar.gz` on Apple Silicon for a native smoke test.

## Windows installer

Run `AiderDeck-Setup-0.1.0.exe` to install Deck for your Windows account under `%LocalAppData%\Programs\Aider-Deck`. Setup adds an **Aider-Deck** Start Menu shortcut with the app icon, offers a desktop shortcut, and registers an uninstaller. Deck needs Python 3.11 or newer on PATH. The first launch creates the Python environment and installs Deck's dependencies. Follow the setup steps above to install Git and Aider for coding sessions.

Build from a clean checkout so the installer packages only the project's files. If `aider-deck.ico` is missing, run `.\.venv\Scripts\python.exe installer\make_icon.py` from the repository root using the project environment with Pillow installed. Compile `AiderDeck.iss` with [Inno Setup 6](https://jrsoftware.org/isdl.php#stable6). The installer is written to `dist\AiderDeck-Setup-0.1.0.exe`.

## Running an existing installation

### Linux and macOS packages

Both packages use Python 3.11 or newer with `venv` support and `python3` on PATH. The first launch creates the environment and installs the dependencies.

**Linux.** From a clean checkout, run `./packaging/linux/install.sh`. Open **Aider-Deck** from the app grid or run `~/.local/share/aider-deck/run.sh`. Close the server console and run `./packaging/linux/uninstall.sh` from the checkout to remove the installed files, launcher, icons, and Deck data under that install directory.

**macOS.** In Terminal, run `python3 --version`. The DMG needs native Python 3.11 or 3.12 for its bundled Aider setup. If your default Python is newer, Deck can also find `python3.12` or `python3.11`. Open the release DMG, or build it from a clean checkout with `./packaging/macos/build-app.sh` and open `dist/AiderDeck-0.1.0.dmg`. Drag **Aider Deck** to your user’s `~/Applications` folder. On first launch, right-click it in Finder and choose **Open**. If macOS blocks that, use **System Settings → Privacy & Security → Open Anyway**. To uninstall, quit Deck, move the app to the Trash, and remove `~/Library/Application Support/Aider-Deck` to delete its environment and stored Deck data.

If the macOS app closes before the browser opens, run `python3 --version` in Terminal and check `~/Library/Application Support/Aider-Deck/launcher.log` for the startup error. Building the DMG requires Apple’s command-line developer tools (`xcode-select --install`).

### Run from the checkout

Double-click **run.bat** or run `.\run.bat` in the Deck folder. Use `.\run.bat --port 5180` for another port. For a foreground server without opening a browser, run the following from the same folder after the first setup.

```powershell
.\.venv\Scripts\python.exe app.py
```

Each port owns its sessions. Additional ports save their workspace list, preferences and composer history in `deck-state-<port>.json`. Opening the same workspace on another port reads its existing Aider configuration and credentials.

After updating the checkout, a running server still uses the Python code it loaded at startup. If Settings asks you to restart Deck to see the live weak model, or the Composer reports an older server’s 256 KiB limit, open the updated server’s URL. You can keep working sessions on their original port and start the updated code on a free port with `.\run.bat --port <port>`. The same applies to model capability lookup.

A workspace without an Aider config offers the Harbor profile. Previewing it shows the proposed file change. Confirming writes it. Aider handles provider authentication and model requests. Deck's key check verifies presence only.

### Use a local Ollama model from the UI

Open your workspace and choose **Inspector → Ollama**. Install Ollama for Windows once if it is missing. Local chat models work without an API key. [Ollama for Windows](https://docs.ollama.com/windows)

| What you want to do | What to click |
| --- | --- |
| Start with an installed model | Type its tag, such as `nemotron-3.5-bolt:e4b`, or find its saved row. Click **Use in Aider**. Deck starts Ollama if needed, loads the model and starts Aider. Look for **Loaded** beside the tag and the model name in the top bar. |
| See which models are on disk | Click **Start Ollama** if the service is stopped. **Refresh Ollama** updates the list. **Installed** means the model is on disk. **Loaded** means Ollama currently has it in memory. |
| Keep a list of models | Type a name in **Ollama model tag** and click **Save tag**. Repeat for each model. Tags without a version use `:latest`. The list persists for this workspace on this Deck port. **Remove from saved** removes the bookmark and keeps the model on disk. |
| Download and install a model | Enter its Ollama library tag and click **Download model**, or click **Download** beside a saved tag. Deck shows the download stage and byte progress when Ollama supplies a size. When the tag shows **Installed**, click **Use in Aider**. |

**Use in Aider** saves the local endpoint and selects the tag for the workspace's main, weak and Architect editor models. If this workspace already has a running session on this Deck port, **Replace session** confirms the switch. **Cancel** keeps that session. [Aider's Ollama guide](https://aider.chat/docs/llms/ollama.html)

The panel manages a loopback Ollama server, normally at `http://127.0.0.1:11434`. For another local port, open **Keys → Add variable**, save `OLLAMA_API_BASE` with the base URL, such as `http://127.0.0.1:11435`, and refresh the Ollama panel. To switch only the main model on a session already connected to Ollama, open **MODEL**, enter the full Aider name such as `ollama_chat/nemotron-3.5-bolt:e4b`, and choose **Apply live**. [Ollama API](https://docs.ollama.com/api) · [Aider's Ollama guide](https://aider.chat/docs/llms/ollama.html)

If **UNREGISTERED MODEL** appears, **Register…** lets you preview and save the format you want when returning to Code. Restart the session to load the registration.

### Choose which models appear in the picker

Click **MODEL → Manage saved models**. Filter by name or provider and check the models you want in the **Saved models** dropdown. The list includes known IDs from OpenAI, Anthropic, Google Gemini, Cohere, DeepSeek, Groq, Mistral and OpenRouter, alongside your workspace models, Ollama bookmarks and installed local tags. **Refresh models** checks the local library and rereads your choices. Cloud suggestions come from Deck’s bundled catalogs. Your provider controls account access to each model.

| To | In the main model control |
| --- | --- |
| Keep a custom name | Enter the full Aider ID and click **Save model**. Bookmarks and visibility are saved for this workspace on this Deck port. |
| Hide or restore a choice | Uncheck or check **Show … in picker**. **Remove** deletes the bookmark. A published or installed model can still appear among the available choices. |
| Enable a cloud model | Configure its provider key under **Keys**. A missing key keeps the toggle off and the warning names the required variable. Deck checks the effective configuration locally. |
| Set the startup model | Choose a dropdown entry or type its ID, then click **Save for next launch**. The **Next launch** line shows your choice. Clearing the field and saving removes the workspace override. |
| Change a running session | Choose a model and click **Apply live**. The top-bar model badge updates when Aider announces the switch. |

**Which weak model is running?** Open **Settings**, filter for `weak-model`, and read **Live weak model**. Aider uses this role for commit messages and chat summaries. In Aider 0.86.2, switching the main model with **Apply live** carries the current weak model forward. For example, the terminal can announce a local Ollama main model and `openai/gpt-4.1` as the weak model. Deck shows the announced weak model even when the **Next launch** field uses Aider’s default. To change the running weak model yourself, enter its full ID in that row and choose **Apply live**.

For a local Ollama model, the picker checks the exact tag at the configured loopback endpoint. If it is missing, use **Download through Ollama**. If the service is stopped, choose **Start Ollama** first. You can bookmark a missing tag or mark it for next launch. Applying it live or starting that session waits until it is installed. When the download completes, choose **Apply live** or **Start session** yourself.

The link beside the model opens its public reference when Deck recognizes the ID. For example, `ollama_chat/nemotron-3.5-bolt:e4b` opens the Nemotron family page. An unrecognized Ollama tag opens the general model library without sending its name. Local reasoning levels still come from the installed tag. [Nemotron on Ollama](https://ollama.com/library/nemotron-3.5-bolt) · [Ollama model library](https://ollama.com/search)

### Attach files from outside the workspace

With an Aider session running, drag one or more files from Windows Explorer onto the **Composer**, or click the **+** beside the prompt and select them. Wait for **Read-only** beside each filename, write your question, and click **Send**. Selecting files leaves your draft in place. Both local and cloud sessions use the same controls. Sending to a hosted model includes the attachment contents in that provider’s request.

Deck creates temporary read-only copies outside the repository. They stay attached across turns and browser reloads. Click **×** beside an attachment to detach it and delete its temporary copy. Ending or restarting the Aider session cleans up the session’s copies. Your original files stay where you picked them. Text already included in a conversation can remain in its history. If every writable temporary directory is inside the workspace or its Git repository, Deck asks for a temporary directory outside it.

**Will my text files fit?** Deck estimates the tokens for all the UTF-8 text and source files you select together, then compares them with the live model’s remaining context. A large file can attach directly in a roomy chat. A smaller file can trigger a warning late in a long conversation. The model named in the live badge determines the budget. Saving a different model for the next launch takes effect when that model starts.

When the selected text would use **90% or more of the estimated remaining tokens**, Deck shows a warning beside the Composer. For example, a selection estimated at 7,300 tokens triggers a warning with 8,000 left. **Cancel** leaves your draft in place so you can choose fewer files or a shorter excerpt. **Attach anyway** adds the selection as read-only references. You still choose when to send.

Deck also asks when it lacks a current usage estimate or when images in a mixed selection have an unknown token cost. If Ollama reports only the model’s advertised capacity, the warning shows an upper bound because the window loaded on your machine can be smaller. Click **Context** to request a fresh usage estimate from Aider. Deck estimates text locally and allows more tokens for punctuation and non-ASCII text. The model’s tokenizer can give a different count, and your unsent draft and the next reply also need room.

External attachments share a storage allowance of **eight files and 4 MiB total**. Removing a file frees its share. A vision-capable model can also accept still PNG or JPEG images up to 2 MiB and 16 megapixels each. Deck checks filenames against the workspace’s ignore rules and scans the text for recognizable credentials before attaching it. The browser supplies only the filename, so folder-specific rules for the original desktop folder cannot be checked. Review screenshots for private information yourself.

### Add repository files to the chat

Start a session and expand a folder under **Explorer → File tree**.

| What you want to add | What to click |
| --- | --- |
| One editable file | Click its filename. |
| One read-only reference | Click **R** beside its filename. |
| A group of files | Check the boxes beside any files, including files in different folders. Click **Add selected** or **Add selected read-only**. **Clear selection** unchecks the group. |
| Files matching a pattern | Click **＋** beside a folder, enter a pattern such as `*.py`, then click **Add matching**. The **Path or glob pattern** field also accepts a workspace-relative pattern such as `src/**/*.py`. |

Look under **Files in chat** to see what Aider attached. Each file is marked **Editable** or **Read-only**. Use **Make editable**, **Make read-only**, or **Drop** to change its role or remove it from the conversation. Dropping a file leaves it on disk.

### Drop files from the chat

In **Explorer → Files in chat**, click **Drop** beside one filename. For a group, check the boxes beside the attached files and click **Drop selected**. You can mix editable and read-only files. **Clear drop selection** unchecks the boxes. The list updates when Aider confirms which files remain attached.

Dropping stops Aider from attaching the full file on future turns. A tracked file can still appear in the repo map, and earlier messages or saved history can still contain text from it. To attach it again, select it in the file tree. On an older Deck server, refresh the page to load the checkboxes. If it reports that two names could match, use the updated server for an exact match.

### Track how much context your model is using

The **Context** badge fills as Aider reports usage. After a reply, Deck uses the sent and received counts in Aider’s usual footer to estimate how much of the current model’s window that turn used. A tilde (`~`) marks this estimate. Each reply supplies its own count.

Click **Context**, the refresh arrow in **Explorer → Session usage**, or the **/tokens** quick action when you want the full report in the terminal. Deck sends `/tokens` only on request. The report lists **Files**, **History**, **Repo map** and **System** within one shared window. **Files in chat** shows per-file estimates when Aider supplies them. A dash means the breakdown needs a fresh report or Aider omitted that value.

For local models on Ollama’s native or recognized OpenAI-compatible endpoint, Deck first uses the **Ollama loaded window** for the active tag. For example, 2,048 tokens in an 8,192-token window fills the bar to 25%. If Ollama omits the running allocation, the badge labels the limit it can use instead. This can be the model’s configured default, a workspace input limit, its advertised capacity or Aider’s reported limit. A model’s advertised capacity can exceed the window actually loaded. If no source supplies a limit, Deck shows the token count alone. [Ollama running models](https://docs.ollama.com/api/ps) · [Ollama model details](https://docs.ollama.com/api-reference/show-model-details)

### Make room for the Terminal and Composer

On a desktop-width window, drag **Explorer’s right edge to the left** or **Inspector’s left edge to the right**. The Terminal and Composer widen while both panels stay open. Each edge has a short vertical grip halfway down. You can also focus a grip with **Tab** and press **Left** or **Right**. Double-click a grip or press **Enter** while it has focus to restore that panel’s default width.

Use the **Explorer** and **Inspector** controls in the Terminal header to hide either panel completely. Click the same control to bring it back at its chosen width. Opening **MODEL** reveals the Inspector so you can use the picker. In a narrow window the side panels stack below the Terminal, and Deck restores their desktop widths when you widen the window again.

Drag the divider above the Composer up for more writing space or down for more terminal rows. You can also focus the divider with **Tab** and adjust it with **Up** or **Down**. Double-click it or press **Enter** while it has focus to restore automatic sizing. Deck remembers your layout for this server address in the current browser. Resizing keeps the Aider session and the text in your draft.

### Typing in the terminal and switching modes

Click inside the terminal to focus Aider. At the chat prompt, use the left and right arrows or **Alt-click** to move the caret within the current input line. Drag to select terminal text. In the composer, an ordinary click places the caret anywhere in your draft.

If Aider asks a startup question, such as whether it may add `.env` to `.gitignore`, click **Answer in terminal**, type your answer, and press Enter. You can click a mode while Aider waits. Deck shows the pending choice and keeps your composer draft. It sends the switch when Aider reaches its chat prompt, then updates the badge when Aider announces the new coder.

## Active format and model registration

The **Active format** badge follows the coder announcement in the terminal. Before an announcement it reads **UNSET**. Settings displays the launch value separately. Open **Effective settings** to compare the announced coder, the options printed by `/settings`, and the configuration on disk.

In Aider 0.86.2, `/chat-mode code` selects the main model's default format. For example, an unregistered model can launch in `diff`, enter Ask, and return to Code in `whole`. Its launch options still show `diff`. The **UNREGISTERED MODEL** notice links to a preview of the model definition. Choose the intended format, confirm the YAML diff, and explicitly restart to load it. Deck tracks registrations loaded by the process separately from changes saved since launch. Aider announces the resulting format on mode and model switches.

The Harbor profile sets `openai/gpt-5.6-terra`, `diff`, high reasoning effort, automatic commits off, analytics disabled, and `editor-diff` for the Architect editor. Model availability and accepted reasoning settings depend on the configured provider. The Keys and raw model metadata editors let you configure a custom endpoint, model ID, context window, and prices.

If a hand-edited model entry names a coder the installed Aider cannot use, Deck identifies the model and file before starting or switching to it. Correct the format in the model settings editor and restart to load it. Commands typed directly into the terminal use Aider's native behavior.

### Reasoning effort for local and cloud models

Open **Settings**, type `reasoning-effort` in **Filter settings**, and check the model name above the control. **Next launch → Save** writes the workspace setting. When Aider is running, **Live session → Apply live** sends `/reasoning-effort` to the main model it last announced. These targets can use different models or different endpoints for the same model name.

Click the source link under the dropdown to check the provider’s current documentation. For `openai/gpt-5.6-terra`, **OpenAI model catalog** opens Terra’s own model page. Deck’s bundled cloud list reflects its last update, while the linked page can change independently. [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra)

| Selected model | What appears in the control |
| --- | --- |
| Installed Ollama tag | Deck asks the local server for the exact tag’s `thinking.values`. Named levels populate the dropdown. On the native route, a boolean-only response explains that the tag has on/off control, and `[false]` means it has no thinking control. Looking up levels leaves an unloaded model on disk. [Ollama thinking controls](https://docs.ollama.com/capabilities/thinking) · [Show model details](https://docs.ollama.com/api-reference/show-model-details) |
| OpenAI, Anthropic or Gemini on the provider’s own endpoint | For model IDs in Deck’s bundled catalog, the dropdown lists the levels published for that exact ID and links to the source. For example, OpenAI’s GPT-5.2 lists `none`, `low`, `medium`, `high` and `xhigh`. Hosted lookups run offline. [OpenAI model catalog](https://developers.openai.com/api/docs/models/gpt-5.2) · [Claude effort guide](https://platform.claude.com/docs/en/build-with-claude/effort) · [Gemini thinking guide](https://ai.google.dev/gemini-api/docs/thinking) |
| Other cloud models, gateways and custom deployments | Enter the provider’s named or numeric value manually. If the selected Aider model metadata file contains an explicit `capabilities.effort` record for that exact cloud ID, Deck can show its listed choices. The source is labeled **Workspace model metadata**. [Claude model metadata schema](https://platform.claude.com/docs/en/api/models/retrieve) |

If Ollama is offline or a model omits its levels, **Check levels again** retries the lookup. Manual entry accepts values such as `balanced` or `0.5` when your provider supports them. If you change models and the saved effort falls outside the new list, Deck flags the old value. Choose a replacement, or choose **Inherit** and save to remove the workspace override.

**If Aider says it is ignoring `reasoning_effort` at startup**, it skipped your saved effort for that launch. For example, OpenAI supports `low` on `openai/gpt-5.6-terra`, and Aider 0.86.2 still requires the model to declare `reasoning_effort` in its `accepts_settings` list. [GPT-5.6 Terra model reference](https://developers.openai.com/api/docs/models/gpt-5.6-terra) · [Aider’s reasoning settings](https://aider.chat/docs/config/reasoning.html)

- **For the running session**, type `/reasoning-effort low` in the Aider terminal, or choose **Live session → low → Apply live**. Typing `/reasoning-effort` by itself displays Aider’s current value.
- **For future launches**, click **Review reasoning effort** in the startup notice, or filter **Settings** for `reasoning-effort`. Under **Next launch**, click **Enable reasoning effort for this model…**, review the YAML diff and click **Confirm write**. A registered model keeps its existing edit format and other accepted settings. A new registration asks which Code format to use. The change takes effect the next time you start or explicitly restart Aider.
- **When registering a model for the first time**, the **Register…** dialog offers **Declare reasoning effort support** if Deck can verify the saved level for that launch model. Leave it checked to include the declaration in the same preview. Aider’s model check stays enabled.

**Review reasoning effort** temporarily filters Settings to the relevant row. After a successful session restart, the same tab shows all groups again, or returns to the search you had before opening the warning. If you type a new filter during the review, Deck keeps your search through the restart.

**Applying effort with Ollama.** In the tested Aider 0.86.2 native `ollama_chat/` path, Aider sent `reasoning_effort` while Ollama’s native chat API expects `think`. Ollama’s OpenAI-compatible endpoint accepts the field Aider sends. To use it with a tag that lists named levels:

1. In a workspace dedicated to local models, open **Keys**. Set `OPENAI_API_BASE` to `http://127.0.0.1:11434/v1`. Use a local placeholder such as `local-placeholder-unused` for this workspace’s `OPENAI_API_KEY`. For another local port, use the same host and port as your Ollama connection.
2. Under **MODEL**, save `openai/<installed-tag>`, for example `openai/gpt-oss:20b`. Start or restart this workspace’s Aider session.
3. Go to **Settings → Reasoning effort → Live session**, select a level and click **Apply live**. For **Next launch**, Aider’s default **Check model accepts settings** option also requires the model to list `reasoning_effort` under `accepts_settings`. [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility) · [Aider reasoning settings](https://aider.chat/docs/config/reasoning.html)

### Set temperature for one model

Open **Settings → Configuration files → `.aider.model.settings.yml`**. Under the entry with the exact model name, add `temperature` to its existing `extra_params` mapping. For example, the following two lines set a temperature of 0.2 inside that entry.

```yaml
  extra_params:
    temperature: 0.2
```

Click **Save file**, then restart the workspace’s Aider session. The model and provider determine which temperatures they accept. [Aider advanced model settings](https://aider.chat/docs/config/adv-model-settings.html)

## Controls and their mechanisms

| Control | What it reads or sends |
| --- | --- |
| Workspace switcher, recent folders, prompt history, composer preferences | Local directories and `deck-state.json`. Opening a workspace leaves other sessions running. |
| Start in Code | Aider subprocess in the workspace with `--edit-format <format>`. |
| Start in Architect | `--architect --editor-edit-format <editor format>`. Architect and the main edit-format flag share a launch destination. |
| Terminal, resize, interrupt, reconnect | Windows ConPTY via pywinpty. xterm sends keyboard input and dimensions over a same-origin WebSocket. Interrupt writes Ctrl+C. Reattach replays a bounded output buffer. |
| Active format, model, sticky mode | Aider's coder announcements and recognized prompt output. `/settings` also prints the original options. |
| Code, Ask, Architect, Context, Help | `/chat-mode <mode>` for a sticky switch. **Send once** prefixes the message with its mode command. |
| Composer | Plain text or Aider's tagged multiline input. Enter behavior is a Deck preference. Aider's terminal multiline behavior can also be toggled with `/multiline-mode`. |
| In-chat file list and actions | `/ls`, `/add`, `/read-only`, and `/drop`. File actions schedule a fresh listing. |
| File tree, globs, ignored files | Workspace directory names, Git's ignore rules, and `.aiderignore`. Deck resolves file selections inside the workspace. The ignore editor offers `/map-refresh` after saving. |
| Context meter and usage | Aider’s reply footer updates the estimated total. An explicit `/tokens` request adds the detailed breakdown. The meter labels the source of the model’s window in Session usage. |
| Settings registry | Each row lists its YAML key, CLI option, environment variable, and source. Workspace edits use ruamel.yaml to retain existing comments. |
| Apply live | `/model`, `/weak-model`, `/editor-model`, `/reasoning-effort`, `/think-tokens`, or `/chat-mode <format>` for a coder change. Saving the workspace setting and applying it live are separate controls. |
| Model discovery / update check | Server-side `aider --list-models <partial>` and `aider --just-check-update`. Model IDs also accept free text. |
| Ollama | Starts the local Ollama service, lists installed and loaded tags, streams downloads, and preloads the chosen model before launching Aider. Saved tags belong to the workspace. |
| Presets, registration, raw files | `presets.json`, `.aider.conf.yml`, `.aider.model.settings.yml`, and `.aider.model.metadata.json`. Preview confirmation is tied to the file revision. |
| Keys and environment | Reads home, repository, workspace and inherited values. Writes target the workspace `.env`. Saved values are masked. Each recognized provider key has a public API platform or setup link. |
| Branch, changes, diff and history | Git subprocesses for branch, upstream distance, porcelain status, unified diff, numstat, log, and show. Git paths in this panel are relative to the Git repository root. |
| Stage, unstage, discard, commit | `git add`, `git restore --staged` (or removal from the index before the first commit), `git restore`, deletion of a selected untracked file, and `git commit -m`. Discard requires confirmation. |
| Secret checks | Locally inspects staged files or outgoing Git history. An optional native pre-push hook checks the actual refs being pushed even when Deck is closed. |
| Aider quick actions | Send the selected slash command, including `/tokens`, `/map`, `/settings`, `/diff`, `/undo`, `/lint`, `/test`, `/run`, `/git`, `/web`, `/copy-context`, `/save`, and `/load`. Aider's `/undo` acts on its own last commit. See [Known limitations](#known-limitations) for `/diff`. |
| Session menu | Confirmed `/clear`, `/reset`, or `/exit`. Restart replaces the selected workspace's process. |
| Prompt library | Per-workspace path to a directory of UTF-8 Markdown/text files. Load fills the composer. Send submits it. |

## Known limitations

**`/diff` uses the preceding message as its baseline in Aider 0.86.2.** To see the last edit with this command, run it before sending another message or requesting a detailed token report.

To see what changed, open the **Git** tab. Choose **Working diff** for unstaged edits or **Staged diff** for staged edits. If the edit was already committed, select its entry under **Recent commits**.

### Aider's access to private files in this repository

The root [`.aiderignore`](.aiderignore) excludes matching secret filenames, the default prompt library, Deck state, logs and Aider history from the repository map, `/ls` and normal `/add` operations. The rules apply to both local and hosted models. They allow source, tests, build settings and this manual. If you move a prompt library, log or history file to a custom path inside this repository, add that path to the ignore file too.

**Explicit read-only attachments and restored chat history can still enter model context.** In Aider 0.86.2, both the native `/read-only` command and the `--read` launch option loaded ignored dummy content. Deck's **R** control invokes the native command. The **Restore chat history** setting also restored an ignored dummy transcript. Keep private data outside Aider's accessible filesystem or run it in an isolated account with read access only to the intended project files when confidentiality must be enforced.

### Keeping private files out of Git

This project's `.gitignore` excludes dotenv files (including `.env.example`), private keys, Aider transcripts and local model configuration, Deck state, prompt contents and logs from normal staging. The empty `prompts/.gitkeep` is safe to share. Keys saved through Deck belong to the selected workspace's `.env`. When you open another repository in Deck, that repository needs its own Git ignore rules.

Before publishing, open **Git → Secret checks** in the workspace you want to push.

- **Check staged files** reads Git's staged versions, including edits that differ from the file currently on disk. Normal local commits still work.
- **Check before push** previews committed changes since the locally recorded upstream. For a new branch with no upstream, it checks history reachable from the current commit. It can catch a key that was added and removed in two separate outgoing commits.
- **Enable pre-push check** installs the native hook for this repository. A normal Git push then checks the actual outgoing refs, including branch and tag updates. A finding or incomplete scan stops the push. The warning identifies the commit, path, line when known and type of finding. It omits the matched value. Keep the Deck installation and its Python environment available to the hook. The Deck server can be closed.

Review a finding locally. If a key was committed, fix each affected **unpublished** commit before retrying. Deleting the key only in the latest commit leaves the earlier copy in history. Rotate credentials that have already been shared. To check a filename without printing its contents, run `git check-ignore -v --no-index -- .env` and `git ls-files -- .env`. [Git ignore rules](https://git-scm.com/docs/gitignore)

The checker runs locally and needs no model or API key. It checks saved credential values available to the workspace, common key formats, credential assignments and private file paths. Arbitrary sensitive text and secrets inside compressed or encrypted payloads can escape detection. The hook is local to the clone. Existing hooks and custom hooks directories are preserved. Git clients that skip hooks, including `git push --no-verify`, bypass the local warning. [Git hooks](https://git-scm.com/docs/githooks#_pre_push)

From the Deck folder you can also preview history, enable the hook or check its installation for a specified project.

```powershell
.\.venv\Scripts\python.exe -m deck.push_check check --repo "C:\path\to\your\workspace"
.\.venv\Scripts\python.exe -m deck.push_check install --repo "C:\path\to\your\workspace"
.\.venv\Scripts\python.exe -m deck.push_check status --repo "C:\path\to\your\workspace"
```

## Files and lifecycle

`app.py` serves the page, HTTP endpoints and terminal sockets. The `deck/` package contains the session transport and output parser, configuration registry and round-trip editors, Git operations, path checks, and atomic state persistence. `templates/index.html` and `static/` contain the browser UI. Pinned xterm.js and Fit addon assets and their licenses are under `static/vendor/` for offline use.

Keep the server running to preserve terminal processes and the replay buffer. After server shutdown, preferences and composer history remain in `deck-state.json`. Aider manages its own chat and input history independently. Raw terminal input is the installed Aider CLI, including its normal commands and prompts. Structured readouts retain the last observation and offer raw output when a parser cannot recognize a response.

Deck binds only to IPv4 loopback and checks browser origin. The workspace and its Aider configuration are trusted local input. The terminal can execute anything an owner could run in Aider. Windows filesystem permissions protect the plaintext workspace `.env` and prompt history. Deck masks the key APIs and suppresses sensitive Git config diffs, including historical versions.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest -q
node --test tests/*.test.mjs
```

On Linux and macOS, run these checks from the checkout after running `./run.sh` once.

```sh
.venv/bin/python -m pytest tests/ -q
node --test tests/*.test.mjs
```

The Windows browser acceptance script uses an installed Aider and Git with a temporary repository, a disposable browser profile, dummy credentials, and a local HTTP fixture that returns predictable replies to Aider. The edit and commit are real. To repeat it:

```powershell
.\.venv\Scripts\python.exe -m pip install playwright
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) '.tmp\playwright'
.\.venv\Scripts\python.exe -m playwright install chromium --only-shell
.\.venv\Scripts\python.exe tests/browser_acceptance.py
.\.venv\Scripts\python.exe tests/manual_commands.py
.\.venv\Scripts\python.exe tests/manual_modes.py
.\.venv\Scripts\python.exe tests/manual_formats.py
.\.venv\Scripts\python.exe tests/manual_panels.py
.\.venv\Scripts\python.exe tests/manual_settings.py
.\.venv\Scripts\python.exe tests/manual_attachments.py
.\.venv\Scripts\python.exe tests/manual_saved_models.py
.\.venv\Scripts\python.exe tests/manual_model_gate.py
.\.venv\Scripts\python.exe tests/manual_layout.py
.\.venv\Scripts\python.exe tests/manual_reasoning_effort.py
.\.venv\Scripts\python.exe tests/manual_ollama_panel.py
.\.venv\Scripts\python.exe tests/manual_git_safety.py
```

The Ollama panel script uses a local fixture by default and tests saved tags, download progress and retries, one-click startup and session replacement. With Ollama installed, run `.\.venv\Scripts\python.exe tests/manual_ollama_panel.py --real` to download the 92 MB `smollm2:135m-instruct-q4_0` model to a temporary library, ask it a question through Aider, then test one-click startup with the service stopped. This check uses a spare local port and cleans up its library.

With Ollama running and a model already installed, `.\.venv\Scripts\python.exe tests/manual_ollama.py --tag <installed-tag>` tests the **MODEL** and **Keys** controls and a real local reply through the browser. For a custom loopback endpoint, add `--base-url http://127.0.0.1:<port>`.

To verify a saved OpenAI-compatible endpoint and key against the hosted model, run the following command with your workspace path. This opt-in check sends an arithmetic question and a one-line edit to the configured provider through Aider. Normal provider charges can apply. The test edits a temporary Git repository.

```powershell
.\.venv\Scripts\python.exe tests/saved_provider.py --workspace "C:\path\to\your\workspace"
```

Add `--workflow help-context-architect` to exercise sticky Help and Context, then let Architect pass a scratch-file edit to the configured main model acting as editor. This run uses the workspace's saved editor format and makes additional provider requests.

See [acceptance results](ACCEPTANCE.md) for the tested Aider version, checks, and coverage limits.
