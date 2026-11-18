"""Drive the installed Aider through a Windows ConPTY. No Aider imports."""

from __future__ import annotations

import copy
import re
import secrets as random_secrets
import shutil
import subprocess
import threading
from collections import deque
from pathlib import Path

from .attachments import Attachments
from .registry import FORMATS

MODES = {"code", "ask", "architect", "context", "help"}
PROMPT_NAMES = MODES | FORMATS.keys() | {"multi"}
FILE_COMMANDS = {"/add", "/drop", "/read-only", "/reset", "/load"}
STICKY_COMMANDS = {f"/chat-mode {mode}": mode for mode in MODES}
READOUTS = {"/ls": "files", "/tokens": "tokens", "/settings": "settings"}
BUFFER_SIZE = 262144


def build_launch_args(config, executable="aider"):
    """Architect and --edit-format target one argparse destination in Aider."""
    args = [executable, "--no-gui"]
    if config.get("model"):
        args.extend(["--model", str(config["model"])])
    if config.get("architect"):
        args.append("--architect")
    else:
        fmt = config.get("edit-format") or "diff"
        if fmt not in FORMATS:
            raise ValueError("Choose a supported Code edit format before starting.")
        args.extend(["--edit-format", fmt])
    editor = config.get("editor-edit-format") or "editor-diff"
    if editor not in FORMATS:
        raise ValueError("Choose a supported architect editor format before starting.")
    args.extend(["--editor-edit-format", editor])
    args.append("--auto-commits" if config.get("auto-commits") else "--no-auto-commits")
    if config.get("analytics-disable", True) or not config.get("analytics"):
        args.append("--no-analytics")
    return args


def format_message(text, once=None):
    if once is not None and once not in MODES:
        raise ValueError("Unknown chat mode.")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Enter a message first.")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if re.search(r"[\x00-\x08\x0b-\x1f\x7f]", text):
        raise ValueError("The composer cannot send terminal control characters.")
    if once:
        text = f"/{once} {text}"
    if "\n" in text or re.fullmatch(r"\{[A-Za-z0-9]*", text):
        # Aider requires an alphanumeric tag. A bare closing brace in a code
        # block must never terminate the message and run following lines.
        tag = "deck" + random_secrets.token_hex(6)
        while tag + "}" in text.splitlines():
            tag += "x"
        text = f"{{{tag}\n{text}\n{tag}}}"
    return text


class Redactor:
    """Keep a possible secret prefix private until the next PTY read."""

    def __init__(self, values=()):
        self.pending = ""
        self.set(values)

    def set(self, values):
        self.values = sorted(
            {v for v in values if isinstance(v, str) and v}, key=len, reverse=True
        )

    def feed(self, data, final=False):
        data = self.pending + data
        self.pending = ""
        if not self.values:
            return data
        # Rich/ConPTY may insert styling or line wrapping inside a credential.
        # Match visible characters, then mask the corresponding raw byte span.
        # Keep an unfinished control sequence for the next read.
        tail = ""
        partial = re.search(r"\x1b(?:\[[0-?]*[ -/]*|\][^\x07\x1b]*)?$", data)
        if partial and not final:
            tail, data = data[partial.start() :], data[: partial.start()]
        visible, indices = [], []
        last = 0
        for token in TRANSPORT.finditer(data):
            visible.append(data[last : token.start()])
            indices.extend(range(last, token.start()))
            last = token.end()
        visible.append(data[last:])
        indices.extend(range(last, len(data)))
        text = "".join(visible)
        keep = len(data)
        spans = []
        for secret in self.values:
            value = secret.replace("\r", "").replace("\n", "")
            if not value:
                continue
            at = text.find(value)
            while at >= 0:
                spans.append((indices[at], indices[at + len(value) - 1] + 1))
                at = text.find(value, at + len(value))
            if not final:
                at = text.find(value[0], max(0, len(text) - len(value) + 1))
                while at >= 0:
                    if value.startswith(text[at:]):
                        keep = min(keep, indices[at])
                        break
                    at = text.find(value[0], at + 1)
        mask, last = [], 0
        for left, right in sorted(spans):
            if right <= last:
                continue
            if left >= last:
                mask.extend((data[last:left], "••••"))
            last = right
        # A completed match can overlap a held prefix. Never release its tail.
        keep = max(keep, last)
        mask.append(data[last:keep])
        self.pending = data[keep:] + tail
        return "".join(mask)


class OutputRing:
    def __init__(self, offset=0):
        self.chunks = deque()
        self.start = self.end = offset

    def append(self, chunk):
        if not chunk:
            return
        self.chunks.append(chunk)
        self.end += len(chunk)
        excess = self.end - self.start - BUFFER_SIZE
        while excess > 0:
            first = self.chunks.popleft()
            cut = min(excess, len(first))
            if cut < len(first):
                self.chunks.appendleft(first[cut:])
            self.start += cut
            excess -= cut

    def read(self, cursor):
        reset = cursor is None or not self.start <= cursor <= self.end
        start = self.start if reset else cursor
        if start == self.end:
            return self.end, "", reset
        # Walk from newest output to avoid joining the replay buffer on each keystroke.
        parts, needed = [], self.end - start
        for chunk in reversed(self.chunks):
            parts.append(chunk[-needed:])
            needed -= len(chunk)
            if needed <= 0:
                break
        return self.end, "".join(reversed(parts)), reset


def view(data):
    return {"data": data, "raw": "", "stale": True}


TOKEN_ROW = re.compile(r"^\s*\$\s*[\d.]+\s+([\d,]+)\s*(.*)$")
TOKEN_MODEL = re.compile(r"Approximate\s+context\s+window\s+usage\s+for\s+(.+?),\s+in\s+tokens:", re.S)


def _parse_tokens(lines, attached):
    data = {"file_rows": []}
    # Rich wraps wide rows at spaces, and occasionally splits a long filename
    # mid-word. Recover its exact spelling from the native /ls when unambiguous.
    filenames = {}
    for item in attached or ():
        label = item["path"] + (" (read-only)" if item["read_only"] else "")
        filenames.setdefault("".join(label.split()), []).append(item)

    header = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        row = TOKEN_ROW.fullmatch(line)
        if not row:
            total = re.fullmatch(r"([\d,]+) tokens (total|max context window size)", line)
            if total:
                data["used" if total[2] == "total" else "max"] = int(total[1].replace(",", ""))
            if "system" not in data:
                header.append(line)
            continue
        count, label = int(row[1].replace(",", "")), row[2].strip()
        fragments = [label]
        while i < len(lines):
            next_line = lines[i].strip()
            if (not next_line or next_line.startswith(("$", "="))
                    or re.match(r"-?[\d,]+ tokens\b", next_line)):
                break
            fragments.append(next_line)
            i += 1
        label = " ".join(fragments).strip()
        compact = " ".join(label.split())
        if compact == "tokens total":
            data["used"] = count
        elif compact == "system messages":
            data["system"] = count
        elif compact in ("chat history", "chat history use /clear to clear"):
            data["history"] = count
        elif compact in ("repository map", "repository map use --map-tokens to resize"):
            data["repo_map"] = count
        elif label.endswith(" /drop to remove"):
            data["files"] = data.get("files", 0) + count
            filename = label.removesuffix(" /drop to remove").rstrip()
            candidates = filenames.get("".join(filename.split()), ())
            exact = [item for item in candidates if filename == (
                item["path"] + (" (read-only)" if item["read_only"] else "")
            )]
            if len(exact or candidates) == 1:
                data["file_rows"].append({**(exact or candidates)[0], "tokens": count})
            elif not candidates and len(fragments) == 1:
                data["file_rows"].append(dict(path=filename.removesuffix(" (read-only)"),
                                              read_only=filename.endswith(" (read-only)"), tokens=count))
    match = TOKEN_MODEL.search(" ".join(header))
    if match:
        data["model"] = "".join(match[1].split())
    return data if "used" in data else None


def parse_readout(kind, raw, *, attached=None):
    lines = raw.splitlines()
    if kind == "settings":
        if "Option settings:" not in (line.strip() for line in lines):
            return None
        pairs = re.findall(r"(?m)^\s*- ([\w-]+):[ \t]*(.*)$", raw)
        return {k.replace("_", "-"): v.strip() for k, v in pairs} or None
    if kind == "files":
        group = None
        seen, rows = False, []
        for line in lines:
            label = line.strip()
            if label == "No files in chat, git repo, or read-only list.":
                return []
            if label in (
                "Read-only files:",
                "Files in chat:",
                "Repo files not in the chat:",
            ):
                group, seen = label, True
            elif group and label and re.match(r"^\s{2,}\S", line):
                if group != "Repo files not in the chat:":
                    rows.append(
                        {"path": label, "read_only": group == "Read-only files:"}
                    )
            elif label:
                group = None
        return rows if seen else None
    if kind == "tokens":
        return _parse_tokens(lines, attached)
    return None


# CSI sequences, OSC titles and two-character escape sequences.
VT = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()].[\s]?|[@-Z\\-_])"
)
TRANSPORT = re.compile(VT.pattern + r"|[\r\n]")
ROLE_LINE = re.compile(r"^(Mainmodel|Model|Editormodel):([^\s]+)with([\w-]+)editformat(?:,|$)")
IGNORED_EFFORT = re.compile(r"Warning:([^\s']+?)doesnotsupport'reasoning_effort',ignoring\.")
PROMPT = re.compile(r"^([\w-]*)(?: ?(multi))?>[ \t]*(.*)$")
# Aider's confirm_ask renders this suffix in both its plain and fancy editors.
# Test for the suffix so punctuation and the optional (A)ll/(D)on't choices
# in the question can vary across versions.
CONFIRM_PROMPT = re.compile(r"\(Y\)es/\(N\)o.*\[[^\]\r\n]{1,16}\]:\s*$", re.IGNORECASE)
TOKEN_COUNT = r"(?:\d{1,3}(?:,\d{3}){1,4}|\d{1,12})(?:\.\d{1,3})?[kKmM]?"
FOOTER = re.compile(
    rf"^Tokens:({TOKEN_COUNT})sent,(?:{TOKEN_COUNT}cache(?:write|hit),)*"
    rf"({TOKEN_COUNT})received\b"
)


def _footer_count(text):
    multiplier = {"k": 1000, "m": 1000000}.get(text[-1].lower(), 1)
    number = text.rstrip("kKmM").replace(",", "")
    return round(float(number) * multiplier)


# xterm responds to ConPTY capability queries via the same onData stream as
# keystrokes. Passing these reports back through pywinpty.write() can insert
# their literal bytes into Aider's *next user prompt*, including after replay.
TERMINAL_REPORT = re.compile(
    r"\x1b(?:\[[?>]?[0-9;]*(?:[Rcnt]|[IO])|\][^\x07\x1b]*(?:\x07|\x1b\\))"
)
# Horizontal navigation cannot create text in an empty prompt.
CURSOR_MOVEMENT = re.compile(r"(?:\x1b(?:\[[\d;]*[CDFH]|O[CDFH])|[\x01\x05])+")


class Observations:
    def __init__(self, attachments=None):
        self.attachments = attachments
        self.data = dict(
            model=None,
            weak_model=None,
            active_format=None,
            editor_model=None,
            editor_format=None,
            ignored_effort_model=None,
            mode=None,
            usage=None,
            reply_tokens=None,
            files=view([]),
            settings=view({}),
            tokens=view({}),
        )
        self.partial = self.escape = ""
        self.capture = None
        self.lines = deque()
        self.raw_size = 0
        self.truncated = False
        self.announcement = False
        self.startup_warning = ""
        self.model_line = ""
        self.wants_files = False
        self.multiline = False
        self.bracketed = None
        self.ready = False
        self.have_chat_prompt = False
        self.question = None
        self.question_rows = deque(maxlen=8)
        self.fresh_editor = False
        self.context_text = ""
        self.context_prefilled = False
        self.waiting_reply = False
        self.footer_prefix = ""

    def submitted(self, text):
        command = text.split(maxsplit=1)[0] if text else ""
        if command in FILE_COMMANDS or command in {"/model", "/chat-mode", "/clear"}:
            self.invalidate_tokens()
        self.waiting_reply = not text.startswith("/") or command in {
            "/ask",
            "/code",
            "/architect",
            "/help",
            "/context",
            "/lint",
            "/test",
            "/run",
            "/web",
            "/commit",
        }

    def invalidate_tokens(self):
        self.data["tokens"]["stale"] = True
        self.data["reply_tokens"] = None

    def track_context(self, text):
        # The multi-line sender envelopes messages in Aider's tagged braces.
        if text.startswith("{deck") and "\n" in text:
            text = text.partition("\n")[2].rpartition("\n")[0]
        command, _, argument = text.partition(" ")
        self.context_text = argument.strip() if command == "/context" else ""
        self.context_prefilled = False

    def begin(self, kind):
        self.capture = kind
        self.lines.clear()
        self.raw_size = 0
        self.truncated = False
        self.data[kind]["stale"] = True

    def finish(self):
        if not self.capture:
            return
        raw = "\n".join(self.lines).strip("\r\n")
        files = self.data["files"]
        listing = self.capture == "files" and self.attachments
        result = None if self.truncated else parse_readout(
            self.capture, listing.unwrap(raw) if listing else raw,
            attached=files["data"] if not files["stale"] else None
        )
        if listing:
            listing.observe(result)
        pane = self.data[self.capture]
        if self.capture == "files" and result is not None and result != pane["data"]:
            self.invalidate_tokens()
        pane.update(raw=raw, stale=result is None)
        if result is not None:
            pane["data"] = result
            if (self.capture == "tokens" and result.get("model") and self.data["model"]
                    and result["model"] != self.data["model"]):
                pane["stale"] = True
        self.capture = None

    def line(self, line):
        if self.context_prefilled:
            # Continuation lines of the default can each have their own '>'.
            # A manual keystroke or a Deck submission releases this ownership.
            return
        if self.observe_question(line, complete=True):
            return
        line = line.rstrip()
        if self.startup_warning is not None:
            if line.strip().startswith("Aider v") or self.have_chat_prompt:
                self.startup_warning = None
            elif line.strip().startswith("Warning:") or self.startup_warning:
                # Rich and ConPTY may wrap both the model ID and individual words.
                fragment = "".join(line.split())
                self.startup_warning = fragment if fragment.startswith("Warning:") else self.startup_warning + fragment
                match = IGNORED_EFFORT.fullmatch(self.startup_warning)
                if match:
                    self.data["ignored_effort_model"] = match[1]
                if match or not fragment or len(self.startup_warning) > 512:
                    self.startup_warning = ""
        prompt = PROMPT.fullmatch(line.lstrip())
        if prompt and (not prompt[1] or prompt[1] in PROMPT_NAMES):
            self.footer_prefix = ""
            argument = prompt[3].strip()
            self.multiline = bool(prompt[2]) or prompt[1] == "multi"
            prefilled = bool(
                self.fresh_editor
                and argument
                and self.context_text
                and self.context_text.splitlines()[0]
                .expandtabs()
                .startswith(argument.expandtabs())
            )
            self.fresh_editor = False
            if not argument or prefilled:
                # Model text can contain an isolated ">". prompt-toolkit enables
                # bracketed paste for the actual editor and disables it for output.
                if self.bracketed is False:
                    return
                self.finish_roles()
                self.finish()
                self.ready = True
                self.have_chat_prompt = True
                self.question = None
                self.context_prefilled = prefilled
                if prompt[1] in MODES:
                    self.data["mode"] = prompt[1]
                return
            self.ready = False
            if self.bracketed is False:
                return
            if argument.startswith("/context ") and not self.context_text:
                self.track_context(argument)
            self.submitted(argument)
            if argument.split(maxsplit=1)[0] in FILE_COMMANDS:
                self.wants_files = True
            if argument in READOUTS:
                self.begin(READOUTS[argument])
            return
        if line.strip().startswith("Aider v"):
            self.footer_prefix = ""
            self.question = None
            self.announcement = True
            self.model_line = ""
            self.data.update(
                model=None,
                weak_model=None,
                active_format=None,
                mode=None,
                editor_model=None,
                editor_format=None,
            )
        elif self.announcement:
            self.observe_role(line)
        self.observe_usage(line)
        if self.capture:
            self.lines.append(line)
            self.raw_size += len(line) + 1
            while self.raw_size > BUFFER_SIZE and self.lines:
                self.truncated = True
                self.raw_size -= 1 + len(self.lines.popleft())

    def finish_roles(self):
        # A weak ID has no trailing delimiter. Wait for the next announcement
        # section or prompt so a wrapped first half never becomes a live ID.
        if self.model_line.startswith("Weakmodel:") and self.data["model"]:
            self.data["weak_model"] = self.model_line[len("Weakmodel:"):] or None
        self.model_line = ""
        self.announcement = False

    def observe_role(self, line):
        if line.lstrip().startswith(("Git repo:", "Repo-map:", "Option settings:")):
            self.finish_roles()
            return
        compact = "".join(line.split())
        if compact.startswith(("Model:", "Mainmodel:", "Editormodel:", "Weakmodel:")):
            self.model_line = compact
        elif self.model_line:
            self.model_line += compact
        else:
            return  # A wrapped suffix can sit between the Main and Weak rows.
        if len(self.model_line) > 1024:
            self.model_line = ""
            return
        match = ROLE_LINE.match(self.model_line)
        if not match:
            return
        self.model_line = ""
        role, model, edit_format = match.groups()
        if role == "Editormodel":
            self.data.update(editor_model=model, editor_format=edit_format)
            return
        if self.data["tokens"]["data"].get("model") != model:
            self.data["tokens"]["stale"] = True
        reply = self.data["reply_tokens"]
        if reply and reply["model"] != model:
            self.data["reply_tokens"] = None
        live = edit_format if edit_format.lower() not in {"none", "unset", "unknown"} else None
        # Aider 0.86.2 get_announcements uses "Model:" specifically when main
        # and weak share the same object. "Main model:" precedes a distinct role.
        self.data.update(model=model, weak_model=model if role == "Model" else None,
                         active_format=live, mode=(live if live in MODES else "code") if live else None,
                         editor_model=None, editor_format=None)

    def observe_usage(self, line):
        fragment = line.strip()
        if fragment.startswith("Tokens:"):
            self.footer_prefix = fragment
        elif self.footer_prefix and fragment:
            self.footer_prefix += " " + fragment
        else:
            self.footer_prefix = ""
        # Rich may wrap between fields, and ConPTY can split a word or number
        # across physical rows. Leave the terminal stream itself untouched.
        if footer := FOOTER.match("".join(self.footer_prefix.split())):
            self.data["usage"] = self.footer_prefix
            self.footer_prefix = ""
            if self.waiting_reply:
                self.data["tokens"]["stale"] = True
                model = self.data["model"]
                if model:
                    # Aider rounds large counts. Cache fields describe parts of
                    # the provider's accounting; adding them could count them twice.
                    used = _footer_count(footer[1]) + _footer_count(footer[2])
                    self.data["reply_tokens"] = {"model": model, "used": used}
                self.waiting_reply = False
        elif len(self.footer_prefix) > 200:
            self.footer_prefix = ""
        if line.startswith("Cost:") and self.data["usage"]:
            self.data["usage"] += " " + line.strip()

    def observe_question(self, line, complete=False):
        if self.bracketed is False:
            return False
        if not line.strip():
            if complete:
                self.question_rows.clear()
            return False
        # prompt-toolkit wraps long questions mid-word onto physical rows.
        # Reassemble only the editor's recent rows and prefer the current row
        # alone when it already holds a complete question.
        joined = "".join(self.question_rows) + line
        if complete:
            self.question_rows.append(line[-1024:])
        if CONFIRM_PROMPT.search(joined):
            self.question = (
                line if "?" in line and CONFIRM_PROMPT.search(line) else joined
            ).strip()
            self.ready = False
            return True
        return False

    def feed(self, data):
        data = self.escape + data
        self.escape = ""
        # Redraws can split an escape sequence across reads.
        index = data.rfind("\x1b")
        if (
            index >= 0
            and not VT.match(data, index)
            and not data[index:].startswith("\x1b\\")
        ):
            self.escape, data = data[index:][:4096], data[:index]

        def replace_escape(match):
            seq = match[0]
            return (
                "\n"
                if seq.endswith("J") or (seq.startswith("\x1b[") and seq.endswith("H"))
                else ""
            )

        # Preserve the ordering of editor open/close events with respect to
        # complete lines, even if one read spans the output of a command.
        for piece in re.split(r"(\x1b\[\?2004[hl])", data):
            if piece in ("\x1b[?2004h", "\x1b[?2004l"):
                self.bracketed = piece.endswith("h")
                self.fresh_editor = self.bracketed
                self.question_rows.clear()
            elif piece:
                self._plain(VT.sub(replace_escape, piece).replace("\r", ""))

    def _plain(self, piece):
        text = self.partial + piece
        lines = text.split("\n")
        self.partial = lines.pop()[-BUFFER_SIZE:]
        for line in lines:
            self.line(line)
        # input() in Aider's plain terminal prints the prompt without newline.
        if self.bracketed is None and re.fullmatch(
            r"(?:[\w-]+ ?)?(?:multi)?> +", self.partial
        ):
            self.line(self.partial)
        elif self.partial:
            self.observe_question(self.partial)


class AiderSession:
    """A session belongs to its workspace, independently of WebSocket clients."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.lock = threading.Condition(threading.RLock())
        self.lifecycle = threading.RLock()
        self.proc = None
        self.status, self.error, self.exit_code = "idle", None, None
        self.launch = {}
        self.model_format_errors = {}
        self.output = OutputRing()
        self.attachments = Attachments(self.root)
        self.obs = Observations(self.attachments)
        self.redactor = Redactor()
        self.pending = deque()
        self.cols, self.rows = 120, 32
        self.typing = False
        self.awaiting_mode = None

    def protect(self, secrets):
        with self.lock:
            self.redactor.set([*self.redactor.values, *secrets])

    def start(self, config, env, secrets=(), *, model_format_errors=None, launch_files=None):
        with self.lifecycle, self.lock:
            if self.status == "live":
                return
            if self.proc is not None and self.proc.isalive():
                raise ValueError(
                    "Aider is still running. Use Restart Session to replace it."
                )
            self.attachments.close()
            args = build_launch_args(config)
            executable = shutil.which("aider")
            if not executable:
                raise ValueError("Aider was not found on the server PATH.")
            try:
                from winpty import Backend, PTY

                proc = PTY(self.cols, self.rows, backend=Backend.ConPTY)
                # Windows CreateProcess takes an executable and one encoded argv.
                # list2cmdline implements the same quoting used by subprocess.
                # The child is attached to Deck's xterm/ConPTY, independently
                # of the server launcher (which may advertise TERM=dumb).
                child_env = {k: v for k, v in env.items() if k.upper() != "TERM"}
                child_env["TERM"] = "xterm-256color"
                block = (
                    "\0".join(
                        f"{key}={value}" for key, value in sorted(child_env.items())
                    )
                    + "\0"
                )
                proc.spawn(
                    executable,
                    cmdline=" " + subprocess.list2cmdline(args[1:]),
                    cwd=str(self.root),
                    env=block,
                )
            except Exception:
                self.status, self.error = (
                    "exited",
                    "Could not start Aider under Windows ConPTY.",
                )
                raise ValueError(self.error) from None
            self.obs = Observations(self.attachments)
            self.model_format_errors = dict(model_format_errors or {})
            # A restart gets a fresh screen; clients with old absolute offsets
            # must still recognize a reset even if the old ring ended at zero.
            self.output = OutputRing(self.output.end + 1)
            self.redactor = Redactor(secrets)
            self.launch = {
                "architect": "--architect" in args,
                "auto-commits": "--auto-commits" in args,
                "editor-edit-format": args[args.index("--editor-edit-format") + 1],
            }
            if "--model" in args:
                safe = Redactor(secrets)
                self.launch["model"] = safe.feed(
                    args[args.index("--model") + 1], final=True
                )
            if "--edit-format" in args:
                self.launch["edit-format"] = args[args.index("--edit-format") + 1]
            if "--no-analytics" in args:
                self.launch["analytics"] = False
            self.proc, self.status, self.error, self.exit_code = (
                proc,
                "live",
                None,
                None,
            )
            self.typing = False
            self.awaiting_mode = None
            self.pending = deque(["/ls"])
            threading.Thread(
                target=self._reader,
                args=(proc, launch_files),
                daemon=True,
                name=f"aider-reader-{proc.pid}",
            ).start()

    def wait_for_model_or_question(self, expected_model, timeout=60):
        """Verify an explicit local launch before handing it to the browser."""
        with self.lock:
            ready = self.lock.wait_for(
                lambda: self.status != "live" or self.obs.data["model"] or self.obs.question,
                timeout=timeout,
            )
            if self.status != "live":
                raise ValueError("Aider exited before announcing the local model. Review the workspace settings.")
            if not ready:
                raise ValueError("Aider did not announce the local model or show a startup question in time.")
            model = self.obs.data["model"]
            if model and model != expected_model:
                raise ValueError(f"Aider announced {model} instead of the selected local model. Check model aliases.")

    def restart(self, config, env, secrets=(), *, model_format_errors=None, launch_files=None):
        build_launch_args(config)
        with self.lifecycle:
            self.close()
            self.start(config, env, secrets, model_format_errors=model_format_errors, launch_files=launch_files)

    def close(self):
        with self.lifecycle:
            with self.lock:
                proc = self.proc
                if proc is None:
                    self.attachments.close()
                    return
                self.status = "restarting"
            # A Windows Python launcher can own another Python process. Stop the
            # entire tree before permitting a replacement to use the workspace.
            if proc.isalive():
                result = subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if result.returncode and proc.isalive():
                    with self.lock:
                        self.status = "live"
                    raise ValueError("Windows could not stop this Aider process.")
            with self.lock:
                proc.cancel_io()
                self.proc = None
                self.status, self.exit_code = "exited", proc.get_exitstatus()
                self.attachments.close()
                self.lock.notify_all()

    def _reader(self, proc, launch_files=None):
        try:
            while True:
                chunk = proc.read(blocking=True)
                with self.lock:
                    if proc is not self.proc:
                        return
                    clean = self.redactor.feed(chunk)
                    self.output.append(clean)
                    self.obs.feed(clean)
                    if self.obs.ready:
                        self.awaiting_mode = None
                    if self.obs.wants_files:
                        self.obs.wants_files = False
                        if "/ls" not in self.pending:
                            self.pending.append("/ls")
                    self._dispatch()
                    self.lock.notify_all()
                if proc.iseof() or (not chunk and not proc.isalive()):
                    break
        except Exception:
            with self.lock:
                if proc is self.proc and proc.isalive() and self.status == "live":
                    self.error = "ConPTY output stopped unexpectedly."
        finally:
            if launch_files:
                launch_files.cleanup()
            with self.lock:
                if proc is self.proc:
                    self.output.append(self.redactor.feed("", final=True))
                    self.obs.finish()
                    self.exit_code = proc.get_exitstatus()
                    if not proc.isalive():
                        self.proc = None
                        self.attachments.close()
                    self.status = "exited"
                    self.lock.notify_all()

    def _require_live(self):
        if self.status != "live" or self.proc is None:
            raise ValueError("Start a live Aider session first.")

    def _dispatch(self):
        if self.status != "live" or not self.obs.ready or self.typing:
            return
        while self.pending:
            text = self.pending.popleft()
            if isinstance(text, tuple):
                text, expected_model = text
                if expected_model != self.obs.data["model"]:
                    self.output.append("\r\n[Deck] Main model changed. Choose its reasoning effort again.\r\n")
                    continue
            break
        else:
            return
        # Aider restores the previous /context request as an editable default.
        # Preserve it for raw typing. An explicit Deck action replaces it at
        # the actual prompt; Ctrl+U kills one line or its preceding newline.
        clear_default = (
            "\x15" * (2 * len(self.obs.context_text.splitlines()) - 1)
            if self.obs.context_prefilled
            else ""
        )
        self.obs.track_context(text)
        if text in STICKY_COMMANDS:
            self.awaiting_mode = STICKY_COMMANDS[text]
        self.obs.submitted(text)
        self.obs.ready = False
        # Prevent re-recognizing an unterminated plain-mode prompt.
        self.obs.partial = ""
        if text in READOUTS:
            self.obs.begin(READOUTS[text])
        terminator = (
            "\x1b\r" if self.obs.multiline and self.obs.bracketed is not None else "\r"
        )
        lines = text.split("\n")
        if self.obs.bracketed and "\t" in text:
            # An unquoted TAB invokes prompt-toolkit's completion. Bracketed
            # paste inserts the original character into the Aider input buffer.
            lines = [
                f"\x1b[200~{line}\x1b[201~" if "\t" in line else line for line in lines
            ]
        self.proc.write(clear_default + terminator.join(lines) + terminator)
        self.attachments.dispatched(text)

    def write(self, data):
        if not isinstance(data, str):
            raise ValueError("Terminal input must be text.")
        data = TERMINAL_REPORT.sub("", data)
        if not data:
            return
        with self.lock:
            self._require_live()
            if (
                self.obs.ready
                and not self.typing
                and not self.obs.context_prefilled
                and CURSOR_MOVEMENT.fullmatch(data)
            ):
                self.proc.write(data)
                return
            if data == "\x03":
                self.pending.clear()
                self.awaiting_mode = None
                if self.attachments.items:
                    self.attachments.interrupted()
                    self.pending.append("/ls")
            self.typing = not data.endswith(("\r", "\n", "\x03"))
            if self.obs.context_prefilled:
                self.obs.context_prefilled = False
                self.obs.context_text = ""
            self.obs.ready = False
            if not self.typing:
                self.obs.question = None
            self.proc.write(data)

    def command(self, line, *, expected_model=None):
        if (
            not isinstance(line, str)
            or not line.startswith("/")
            or re.search(r"[\x00-\x1f\x7f]", line)
        ):
            raise ValueError("Expected a single Aider slash command.")
        with self.lock:
            self._require_live()
            if expected_model is not None and self.obs.data["model"] != expected_model:
                raise ValueError("The main model changed. Choose its reasoning effort again.")
            name, _, value = line.partition(" ")
            if name == "/model" and value.strip():
                self._check_model_format(value.strip())
            elif name == "/code" or (name == "/chat-mode" and value.strip() == "code"):
                self._check_model_format(self.obs.data["model"])
            if len(self.pending) >= 32:
                raise ValueError("Aider's input queue is full.")
            self.pending.append((line, expected_model) if expected_model is not None else line)
            if line.split(maxsplit=1)[0] in FILE_COMMANDS:
                self.pending.append("/ls")
            self._dispatch()

    def _check_model_format(self, model):
        if issue := self.model_format_errors.get(model):
            raise ValueError(issue)

    def mode(self, name):
        if name not in MODES:
            raise ValueError("Unknown chat mode.")
        with self.lock:
            self._require_live()
            # Coalesce consecutive selections without moving a later choice
            # ahead of a message the user already queued.
            if self.pending and self.pending[-1] in STICKY_COMMANDS:
                self.pending.pop()
            self.command(f"/chat-mode {name}")

    def send(self, text, once=None):
        message = format_message(text, once)
        with self.lock:
            self._require_live()
            if any(item.status != "attached" for item in self.attachments.items.values()):
                raise ValueError("Wait for Aider to confirm attachments or remove failed files before sending.")
            if message.startswith("/") and "\n" not in message and "\t" not in message:
                return self.command(message)
            if once == "code":
                self._check_model_format(self.obs.data["model"])
            if len(self.pending) >= 32:
                raise ValueError("Aider's input queue is full.")
            self.pending.append(message)
            self._dispatch()

    def attach(self, generation, uploads):
        with self.lock:
            self._require_live()
            self.attachments.require_session(generation)
            command, staged = self.attachments.stage(uploads)
            try:
                self.command(command)
            except BaseException:
                for item in staged:
                    self.attachments.forget(item)
                raise
            return self.attachments.rows()

    def remove_attachment(self, generation, identifier):
        with self.lock:
            self._require_live()
            self.attachments.require_session(generation)
            item = self.attachments.items.get(identifier)
            if item is None or item.status == "removing":
                return self.attachments.rows()
            if item.status == "failed" and not item.path.exists():
                self.attachments.forget(item)
            else:
                # Bypass Aider's substring match and disambiguate similarly named files.
                command = f'/drop "{item.path.parent.as_posix()}/./{item.path.name}"'
                previous = item.command, item.dispatched, item.status, item.error
                item.command, item.dispatched, item.status, item.error = command, False, "removing", ""
                try:
                    self.command(command)
                except BaseException:
                    item.command, item.dispatched, item.status, item.error = previous
                    raise
            return self.attachments.rows()

    def resize(self, cols, rows):
        with self.lock:
            self.cols = min(600, max(20, int(cols)))
            self.rows = min(250, max(4, int(rows)))
            if self.proc and self.status == "live":
                self.proc.set_size(self.cols, self.rows)

    def read_since(self, cursor=None, timeout=0.25):
        with self.lock:
            if cursor == self.output.end and timeout > 0:
                self.lock.wait(timeout)
            return self.output.read(cursor)

    def snapshot(self):
        with self.lock:
            if self.status != "live":
                input_state = "inactive"
            elif self.obs.question:
                input_state = "waiting"
            elif self.typing:
                input_state = "typing"
            elif self.obs.ready:
                input_state = "ready"
            elif self.obs.have_chat_prompt:
                input_state = "busy"
            else:
                input_state = "starting"
            pending_mode = next(
                (
                    STICKY_COMMANDS[cmd]
                    for cmd in reversed(self.pending)
                    if cmd in STICKY_COMMANDS
                ),
                self.awaiting_mode,
            )
            panes = copy.deepcopy(self.obs.data)
            if self.attachments.items:
                panes["files"]["data"] = [
                    row for row in panes["files"]["data"] if not self.attachments.owns(row)
                ]
            return {
                **panes,
                "supports_attachments": True,
                "attachment_session": self.attachments.generation,
                "attachments": self.attachments.rows(),
                "status": self.status,
                "input_state": input_state,
                "input_prompt": self.obs.question if input_state == "waiting" else None,
                "pending_mode": pending_mode if self.status == "live" else None,
                "exit_code": self.exit_code,
                "error": self.error,
                "launch": dict(self.launch),
                "seq": self.output.end,
                "launch_format": (
                    "architect"
                    if self.launch.get("architect")
                    else self.launch.get("edit-format")
                ),
            }
