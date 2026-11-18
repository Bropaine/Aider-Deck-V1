"""Session-owned files staged outside the repository for Aider's /read-only."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile

from .attachment_validation import MAX_FILES, MAX_TOTAL_BYTES
from . import git_service as git


@dataclass
class Attachment:
    id: str
    name: str
    size: int
    directory: tempfile.TemporaryDirectory
    path: Path
    status: str = "attaching"
    error: str = ""
    command: str = ""
    dispatched: bool = False

    def public(self):
        return dict(id=self.id, name=self.name, size=self.size, status=self.status, error=self.error)


class Attachments:
    """Accessed under the owning session's lock; only a fresh /ls confirms changes."""

    def __init__(self, workspace):
        self.workspace = workspace
        self.repo = workspace
        self.generation = secrets.token_hex(16)
        self._review_key = secrets.token_bytes(32)
        self.items = {}

    def rows(self):
        return [item.public() for item in self.items.values()]

    def require_session(self, generation):
        if generation != self.generation:
            raise ValueError("The Aider session changed. Choose the files again.")

    def check_quota(self, uploads):
        if len(self.items) + len(uploads) > MAX_FILES:
            raise ValueError(f"Attach up to {MAX_FILES} files per session.")
        if sum(i.size for i in self.items.values()) + sum(len(b) for _, b in uploads) > MAX_TOTAL_BYTES:
            raise ValueError("Attachments exceed the 4 MiB session limit.")

    def confirmation_for(self, uploads, review):
        # The browser holds the files during review. Bind the acknowledgement to
        # exactly these bytes, this session, its attachments, and the shown budget.
        fingerprints = [(name, hashlib.sha256(content).hexdigest()) for name, content in uploads]
        selection = [self.generation, fingerprints, self.rows(), review]
        encoded = json.dumps(selection, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.blake2s(encoded, key=self._review_key).hexdigest()

    def stage(self, uploads):
        self.check_quota(uploads)
        if not self.items:
            try:
                self.repo = git.repository(self.workspace)
            except git.GitError:
                self.repo = self.workspace
        candidates = [tempfile.gettempdir(), *(os.getenv(key) for key in ("TMPDIR", "TEMP", "TMP"))]
        if local := os.getenv("LOCALAPPDATA"):
            candidates.append(Path(local) / "Temp")
        parents = [Path(value).resolve() for value in candidates if value]
        parent = next((p for p in parents if p.is_dir() and os.access(p, os.W_OK)
                       and not p.is_relative_to(self.workspace) and not p.is_relative_to(self.repo)), None)
        if parent is None:
            raise ValueError("Choose a system temporary directory outside this workspace and repository.")
        staged = []
        try:
            for name, content in uploads:
                directory = tempfile.TemporaryDirectory(dir=parent, prefix="deck-")
                path = Path(directory.name) / name
                if path.suffix.lower() in (".png", ".jpg", ".jpeg"):
                    # Aider recognizes image extensions case-sensitively.
                    path = path.with_suffix(path.suffix.lower())
                item = Attachment(secrets.token_hex(12), name, len(content), directory, path)
                staged.append(item)
                path.write_bytes(content)
        except BaseException:
            for item in staged:
                item.directory.cleanup()
            raise
        command = "/read-only " + " ".join(f'"{item.path}"' for item in staged)
        for item in staged:
            item.command = command
            self.items[item.id] = item
        return command, staged

    def forget(self, item):
        item.directory.cleanup()
        self.items.pop(item.id, None)

    def close(self):
        for item in list(self.items.values()):
            self.forget(item)
        self.generation = secrets.token_hex(16)

    def dispatched(self, command):
        for item in self.items.values():
            if item.command == command:
                item.dispatched = True

    def interrupted(self):
        # Ctrl+C clears the PTY queue. A subsequent /ls resolves both an input
        # that reached Aider before the interrupt and one which was cancelled.
        for item in self.items.values():
            if item.command:
                item.dispatched = True

    def _spellings(self, item):
        spellings = {str(item.path)}
        for base in (self.repo, self.workspace):
            try:
                spellings.add(os.path.relpath(item.path, base))
            except ValueError:  # Windows drive-relative paths require one volume.
                pass
        return spellings

    def unwrap(self, raw):
        # Rich/ConPTY wrap long names and suppress some spaces at line breaks.
        # Restore only exact paths we issued, within the native read-only list.
        if not self.items:
            return raw
        lines = raw.splitlines()
        first = next((n + 1 for n, line in enumerate(lines) if line.strip() == "Read-only files:"), None)
        if first is None:
            return raw
        other_headers = {"Files in chat:", "Repo files not in the chat:"}
        end = next((n for n in range(first, len(lines)) if lines[n].strip() in other_headers), len(lines))
        part = "\n".join(lines[first:end])
        indices = [i for i, char in enumerate(part) if not char.isspace()]
        flat = "".join(part[i] for i in indices).replace("\\", "/")
        changes = []
        for item in self.items.values():
            for spelling in sorted(self._spellings(item), key=len, reverse=True):
                needle = "".join(spelling.split()).replace("\\", "/")
                offset = flat.find(needle)
                if offset < 0:
                    continue
                lo, hi = indices[offset], indices[offset + len(needle) - 1] + 1
                line_start = part.rfind("\n", 0, lo) + 1
                if part[line_start:lo].strip() or part[hi:].partition("\n")[0].strip():
                    continue
                changes.append((line_start, hi, "  " + spelling))
                break
        for lo, hi, replacement in sorted(changes, reverse=True):
            part = part[:lo] + replacement + part[hi:]
        return "\n".join([*lines[:first], part, *lines[end:]])

    def owns(self, row):
        return any(row["path"] in self._spellings(item) for item in self.items.values())

    def observe(self, rows):
        names = {row["path"] for row in rows or () if row["read_only"]}
        for item in list(self.items.values()):
            if item.command and not item.dispatched:
                continue
            if rows is None:
                if item.command:
                    item.status = "failed"
                    item.error = "Aider's file list was incomplete. Refresh Files in chat or remove this attachment."
                continue
            present = bool(self._spellings(item) & names)
            if item.status == "removing" or item.command.startswith("/drop "):
                if not present:
                    self.forget(item)
                    continue
                item.status, item.error = "failed", "Aider still lists this attachment. Try removing it again."
            elif present:
                item.status, item.error = "attached", ""
            elif item.status in ("attaching", "failed"):
                item.status = "failed"
                item.error = ("Aider did not attach this image. Check the model's image support and the terminal."
                              if item.path.suffix in (".png", ".jpg", ".jpeg")
                              else "Aider did not attach this file. Check the terminal and choose the file again.")
                item.directory.cleanup()
            else:
                self.forget(item)  # The owner dropped it from Aider's terminal.
                continue
            item.command, item.dispatched = "", False
