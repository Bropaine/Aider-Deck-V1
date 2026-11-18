"""Inspect immutable Git objects locally. Findings never include matched content."""

import codecs
import os
import re
import subprocess
from itertools import chain
from pathlib import PurePosixPath


class ScanError(ValueError):
    """A scan could not be completed; its result must not be treated as clean."""


MAX_FINDINGS = 100
BLOCK = 256 * 1024
OID = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})\Z")

# Limit the width of patterns so a constant overlap covers matches at block edges.
PROVIDER = re.compile(
    rb"(?<![A-Za-z0-9])(?:"
    rb"sk-(?:(?:proj-|svcacct-|ant-api[0-9]{2}-)[\w-]{20,256}|[A-Za-z0-9]{20,128})"
    rb"|gh[pousr]_[A-Za-z0-9]{30,255}|github_pat_[A-Za-z0-9_]{40,255}"
    rb"|glpat-[\w-]{16,255}|hf_[A-Za-z0-9]{25,128}"
    rb"|(?:AKIA|ASIA)[A-Z0-9]{16}|AIza[\w-]{35}"
    rb"|xox[baprs]-[A-Za-z0-9-]{15,255}|(?:sk|rk)_live_[A-Za-z0-9]{16,255}"
    rb"|gsk_[A-Za-z0-9]{32,128}|sk-or-v1-[a-fA-F0-9]{40,128}"
    rb")"
)
PRIVATE_KEY = re.compile(
    rb"-----BEGIN (?:[A-Z0-9 ]{1,32} )?PRIVATE KEY-----"
    rb"|-----BEGIN PGP " rb"PRIVATE KEY BLOCK-----"
)
URL_PASSWORD = re.compile(rb"\b[a-z][a-z0-9+.-]{0,20}://[^/\s:@]{1,128}:([^/\s@]{1,512})@", re.I)
ASSIGNMENT = re.compile(
    rb"""(?ix)(?<![\w.-])["']?(?:[a-z][a-z0-9_.-]{0,72}?)?"""
    rb"""(?:api[_-]?key|access[_-]?key|auth[_-]?token|token|client[_-]?secret|secret|passwd|password|pwd)"""
    rb"""["']?[ \t]{0,24}[:=][ \t]{0,24}(?![=])"""
    rb"""(?:"([^"\r\n]{1,384})"|'([^'\r\n]{1,384})'|([a-z0-9_+/.~=-]{8,384}))"""
)
BEARER = re.compile(rb"\bBearer[ \t]+([a-zA-Z0-9_.+/=-]{15,384})", re.I)
RULES = (
    ("Provider credential", PROVIDER),
    ("Private key", PRIVATE_KEY),
    ("Credential in URL", URL_PASSWORD),
    ("Possible credential assignment", ASSIGNMENT),
    ("Possible bearer token", BEARER),
)
MASKED_PATH = "[sensitive filename hidden]"


def _env(**overrides):
    return {**os.environ, "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
            "GIT_ALLOW_PROTOCOL": "", **overrides}


def _git(root, *args, ok=(0,), data=b"", **overrides):
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, input=data, capture_output=True,
            env=_env(**overrides), timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScanError("Git could not complete the local safety check.") from exc
    if result.returncode not in ok:
        raise ScanError("Git could not read the staged objects. Check the index for conflicts or missing objects.")
    return result.stdout


def _id(value):
    if not isinstance(value, str) or not OID.fullmatch(value):
        raise ScanError("Git returned an invalid object identifier.")
    return value


def changed_entries(root, base, tree):
    """List final-side blobs of changed paths; renames are deletion plus addition."""
    if base:
        records = _git(root, "diff-tree", "-r", "--raw", "--no-abbrev", "--no-renames",
                       "--no-commit-id", "--no-ext-diff", "--no-textconv", "--ignore-submodules=none",
                       "--diff-filter=AMT", "-z", _id(base), _id(tree), "--").split(b"\0")
        entries = []
        for header, path in zip(records[::2], records[1::2]):
            _, mode, _, oid, _ = header.split()
            if mode != b"160000":  # A gitlink contains a commit ID in a separate repository.
                entries.append((_id(oid.decode("ascii")), os.fsdecode(path)))
        return entries
    records = _git(root, "ls-tree", "-rz", "--full-tree", _id(tree)).split(b"\0")
    entries = []
    for record in filter(None, records):
        header, path = record.split(b"\t", 1)
        _, kind, oid = header.split()
        if kind == b"blob":
            entries.append((_id(oid.decode("ascii")), os.fsdecode(path)))
    return entries


def private_file(path):
    parts = PurePosixPath(path.casefold()).parts
    if not parts:
        return False
    name = parts[-1]
    if (name == ".env" or name.startswith(".env.") or name.endswith((".key", ".pem", ".p12", ".pfx", ".ppk"))
            or name.startswith(("id_rsa", "id_dsa", "id_ed25519", "id_ecdsa"))
            or name in (".aider.conf.yml", ".aider.input.history", ".aider.chat.history.md",
                        ".netrc", "_netrc", "credentials.json", "credentials.yml", "credentials.yaml")):
        return True
    if parts[0] == "prompts":
        return parts != ("prompts", ".gitkeep")
    if parts[0] == ".codex" and len(parts) > 1:
        return parts[1] in {"auth.json", "history.jsonl", "sessions", "archived_sessions", "log", "tmp"} or ".sqlite" in parts[1]
    return (any(part.startswith(".aider.tags.cache") for part in parts)
            or (len(parts) >= 2 and parts[-2:] == (".aws", "credentials"))
            or path.casefold() in (".aider.model.settings.yml", ".aider.model.metadata.json")
            or (len(parts) == 1 and re.fullmatch(r"deck-state(?:-[0-9]+)?\.json(?:\..*\.tmp)?", name)))


def _known_bytes(known_values):
    keys = set()
    for value in known_values:
        if isinstance(value, str) and value:
            for text in {value, value.replace("\r\n", "\n")}:
                keys.add(text.encode("utf-8"))
                if "\n" in text and "\r" not in text:
                    keys.add(text.replace("\n", "\r\n").encode("utf-8"))
    return tuple(keys)


def _placeholder(value):
    value = value.strip().lower()
    return (value in {b"changeme", b"change-me", b"redacted", b"password", b"placeholder"}
            or value.startswith((b"$", b"<", b"{{", b"your_", b"your-", b"example-", b"example_",
                                 b"dummy-", b"dummy_", b"fake-", b"fake_", b"synthetic-", b"process.env.",
                                 b"os.environ", b"os.getenv(", b"config.", b"self.", b"args."))
            or len(set(value)) <= 2)


def _literal(match):
    value = next((v for v in match.groups() if v), b"")
    if _placeholder(value):
        return False
    if match.re == URL_PASSWORD:
        return True
    if match.re == ASSIGNMENT and (match.group(1) or match.group(2)):
        # This is a literal, rather than an environment lookup or a variable.
        # Reject clear code/doc fragments that only resemble a quoted value.
        if re.fullmatch(rb"[a-z][a-z0-9_+.=/!@#$%:-]{7,383}", value):
            return True
    if match.re == ASSIGNMENT and match.group(3):
        # For unquoted values, avoid treating a variable or function as a key.
        if match.end() < len(match.string) and match.string[match.end():match.end()+1] in (b"(", b"[", b"."):
            return False
        if re.fullmatch(rb"\d+(?:\.\d+)?(?:e[+-]?\d+)?", value, re.I):
            return False
        if re.match(rb"(?:[ \t]*(?:\r?\n|#)|$)", match.string[match.end():match.end()+24]):
            if b"-" in value or b"=" in value:
                return len(value) >= 8
    return len(value) >= 8 and (any(48 <= c <= 57 for c in value)
                                 or (len(value) >= 16 and any(65 <= c <= 90 for c in value)
                                     and any(97 <= c <= 122 for c in value)))


def _path_sensitive(raw, keys):
    return next(_positions(raw, keys, len(raw)), None) is not None


def _printable_path(path):
    # Keep arbitrary Git filenames inert even in a terminal running a pre-push hook.
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in path)


def display_path(path, known_values=()):
    if _path_sensitive(os.fsencode(path), _known_bytes(known_values)):
        return MASKED_PATH
    return _printable_path(path)


def _positions(data, keys, end):
    for kind, pattern in RULES:
        for match in pattern.finditer(data):
            if match.start() >= end:
                break
            if pattern.groups and not _literal(match):
                continue
            yield match.start(), kind
    for key in keys:
        pos = data.find(key)
        while 0 <= pos < end:
            yield pos, "Saved credential"
            pos = data.find(key, pos + len(key))


def _encoding(head):
    for bom, encoding in ((codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
                          (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16")):
        if head.startswith(bom):
            return encoding
    # Windows tools sometimes create BOM-less UTF-16 text.
    sample = head[:128]
    if len(sample) >= 8:
        for offset, enc in ((1, "utf-16-le"), (0, "utf-16-be")):
            if sample[offset::2].count(0) > len(sample) // 3 and b"\0" not in sample[1-offset::2]:
                return enc
    return None


def _scan_chunks(chunks, path, keys, limit):
    """Return unique finding locations, consuming the stream even after the cap."""
    head = next(chunks, b"")
    encoding = _encoding(head)
    binary = not encoding and b"\0" in head
    decoder = codecs.getincrementaldecoder(encoding)(errors="replace") if encoding else None
    overlap = max(2048, max(map(len, keys), default=0)) + 1
    carry, line, locations = b"", 1, {}
    for chunk in chain((head,), chunks, (None,)):
        if len(locations) >= limit:
            continue
        last = chunk is None
        incoming = chunk or b""
        if decoder:
            incoming = decoder.decode(incoming, final=last).encode()
        data = carry + incoming
        end = len(data) if last else max(0, len(data) - overlap)
        cursor, number = 0, line
        for pos, kind in _positions(data, keys, end):
            if pos < cursor:
                cursor, number = 0, line
            number += data.count(b"\n", cursor, pos)
            cursor = pos
            location = None if binary else number
            locations[location, kind] = {"path": path, "line": location, "kind": kind}
            if len(locations) >= limit:
                break
        line += data.count(b"\n", 0, end)
        carry = data[end:]
    return list(locations.values())


def scan_text(data, path, known_values=()):
    """Check metadata such as a commit message. The caller supplies a safe label."""
    keys = _known_bytes(known_values)
    name = MASKED_PATH if _path_sensitive(os.fsencode(path), keys) else _printable_path(path)
    return _scan_chunks(iter((data,)), name, keys, MAX_FINDINGS)


def _blob_chunks(pipe, size):
    while size:
        chunk = pipe.read(min(size, BLOCK))
        if not chunk:
            raise ScanError("Git returned an incomplete object.")
        size -= len(chunk)
        yield chunk


def scan_entries(root, entries, known_values=(), *, with_oids=False):
    """Read raw Git blobs through one batch process; never follow worktree links."""
    keys = _known_bytes(known_values)
    findings = []
    count = 0
    truncated = False
    try:
        proc = subprocess.Popen(
            ["git", "cat-file", "--batch"], cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=_env(),
        )
    except OSError as exc:
        raise ScanError("Git could not start the local safety check.") from exc
    try:
        for oid, path in entries:
            proc.stdin.write(_id(oid).encode() + b"\n")
            proc.stdin.flush()
            header = proc.stdout.readline(128).split()
            if len(header) != 3 or header[:2] != [oid.encode(), b"blob"] or not header[2].isdigit():
                raise ScanError("A Git blob could not be inspected. The check is incomplete.")
            count += 1
            hidden = _path_sensitive(os.fsencode(path), keys)
            name = MASKED_PATH if hidden else _printable_path(path)
            file_findings = []
            if hidden or private_file(path):
                file_findings.append({"path": name, "line": None, "kind": "Sensitive filename" if hidden else "Private file"})
            chunks = _blob_chunks(proc.stdout, int(header[2]))
            file_findings.extend(_scan_chunks(chunks, name, keys, max(0, MAX_FINDINGS + 1 - len(findings))))
            if proc.stdout.read(1) != b"\n":
                raise ScanError("Git returned an incomplete object.")
            for finding in file_findings:
                if len(findings) >= MAX_FINDINGS:
                    truncated = True
                    break
                if with_oids:
                    finding["oid"] = oid
                findings.append(finding)
        proc.stdin.close()
        if proc.wait(timeout=10):
            raise ScanError("Git did not finish the safety check.")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScanError("Git could not read all files for the safety check.") from exc
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdin.close()
        proc.stdout.close()
    return dict(findings=findings, files_scanned=count, truncated=truncated)


def scan_staged(root, known_values=(), *, index_file=None):
    """Check the exact snapshot represented by the index at the start of this scan."""
    overrides = {"GIT_INDEX_FILE": str(index_file)} if index_file is not None else {}
    tree = _id(_git(root, "write-tree", **overrides).decode().strip())
    base = _git(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}", ok=(0, 1)).decode().strip() or None
    report = scan_entries(root, changed_entries(root, base, tree), known_values)
    return {**report, "tree": tree, "base": base}
