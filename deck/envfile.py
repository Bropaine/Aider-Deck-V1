"""Preserve dotenv spelling, comments and order, and keep keys write-only."""
import io
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv.parser import parse_stream
from dotenv.variables import parse_variables

KEY_NAMES = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY",
    "OPENROUTER_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY", "COHERE_API_KEY",
    "OPENAI_API_BASE",
)
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SENSITIVE = re.compile(r"(?:api[_-]?key|apikey|secret|password|passwd|credential|authorization|bearer|access[_-]?token|refresh[_-]?token|private[_-]?key|auth[_-]?token|(?:^|[_-])token$)", re.I)
# Aider's budgets are options, not authentication tokens.
def is_sensitive(name):
    name = str(name)
    if re.search(r"(?:cost|price)_per_\w*token$", name, re.I):
        return False
    return bool(SENSITIVE.search(name))


@lru_cache(maxsize=32)
def _git_root(directory, interval):
    p = subprocess.run(["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
                       capture_output=True, timeout=5, encoding="utf-8", errors="replace")
    return Path(p.stdout.strip()).resolve() if p.returncode == 0 else None


def layers(root):
    repo = _git_root(str(root), int(time.monotonic() // 5))
    result = []
    for name, path in [("Home config", Path.home()), ("Repo config", repo), ("Cwd config", root)]:
        if path and all(path != p for _, p in result):
            result.append((name, path))
    return result


def workspace_file(root, filename):
    path = root / filename
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Configuration path would leave the workspace or follow a symbolic link.")
    return path


def read_text(path):
    if not path.exists():
        return ""
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Configuration file exceeds the 8 MiB editor limit.")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return handle.read()


def atomic_write(path, content):
    # Replace in the destination directory so readers see only complete writes.
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8",
                                     newline="") as handle:
        temp = Path(handle.name)
        try:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            handle.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        if path.exists():
            temp.chmod(path.stat().st_mode)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


@dataclass
class Environment:
    values: dict
    sources: dict
    home_names: set
    file_names: set


def environment(root, tolerate_invalid=False, extra=None):
    values = dict(os.environ)
    sources = {key: "Process environment" for key in values}
    home_names, file_names = set(), set()
    paths = [(label, workspace_file(root, ".env") if folder == root else folder / ".env")
             for label, folder in layers(root)]
    if extra:
        path = (root / Path(extra).expanduser()).resolve()
        label = next((label for label, candidate in paths if candidate.resolve() == path), "Additional dotenv")
        paths = [(label, candidate) for label, candidate in paths if candidate.resolve() != path]
        paths.append((label, path))
    for label, path in paths:
        for binding in parse_stream(io.StringIO(read_text(path))):
            if binding.error and not tolerate_invalid:
                raise ValueError(f"Invalid dotenv assignment on line {binding.original.line} of {path}.")
            if binding.key is None:
                continue
            key = binding.key
            file_names.add(key)
            if binding.value is None:
                continue
            values[key] = "".join(atom.resolve(values) for atom in parse_variables(binding.value))
            sources[key] = str(path)
            if label == "Home config":
                home_names.add(key)
    return Environment(values, sources, home_names, file_names)


def child_environment(root):
    return environment(root).values


def mask(value):
    if not value:
        return ""
    return "••••••••" + (str(value)[-4:] if len(str(value)) > 4 else "")


def key_rows(root, env=None):
    env = env or environment(root, tolerate_invalid=True)
    names = set(KEY_NAMES) | env.file_names | {k for k in env.values if k.startswith("AIDER_")}
    return [
        dict(name=k, masked=mask(env.values.get(k)), present=bool(env.values.get(k)),
             source=env.sources.get(k), home_present=k in env.home_names,
             shadows_home=k in env.home_names and env.sources.get(k) != str(Path.home() / ".env"))
        for k in sorted(names)
    ]


# Capture only the RHS so inline comments and the export prefix survive replacement.
ASSIGNMENT = re.compile(
    r"""^(?P<prefix>\s*(?:export[^\S\r\n]+)?(?:'[^\r\n']+'|[\w.-]+)[^\S\r\n]*=[^\S\r\n]*)"""
    r"""(?P<rhs>'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|[^\r\n]*?)"""
    r"""(?P<suffix>(?:[^\S\r\n]+\#[^\r\n]*)?[^\S\r\n]*(?:\r\n|\n|\r)?$)""", re.S)


def write_values(root, replacements):
    path = workspace_file(root, ".env")
    original = read_text(path)
    result = updated_values(original, replacements)
    if result != original:
        atomic_write(path, result)


def updated_values(original, replacements):
    if not isinstance(replacements, dict):
        raise ValueError("Expected environment variable names and replacement values.")
    updates = {}
    for key, value in replacements.items():
        if not NAME.fullmatch(key) or not isinstance(value, str) or "\x00" in value:
            raise ValueError("Invalid environment name or value.")
        if value:
            updates[key] = "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    if not updates:
        return original
    bindings = list(parse_stream(io.StringIO(original)))
    newline = "\r\n" if "\r\n" in original else "\n"
    output, seen = [], set()
    for b in bindings:
        line = b.original.string
        if b.key in updates:
            if b.value is None:
                # A bare variable name is valid dotenv (no assigned value).
                bare = re.match(r"^(\s*(?:export\s+)?(?:'[^'\r\n]+'|[\w.-]+))(.*)$", line, re.S)
                if bare:
                    line = bare[1] + "=" + updates[b.key] + bare[2]
                    seen.add(b.key)
                    output.append(line)
                    continue
            match = ASSIGNMENT.fullmatch(line)
            if not match:
                raise ValueError("The .env assignment could not be updated without losing its comments.")
            line = match["prefix"] + updates[b.key] + match["suffix"]
            seen.add(b.key)
        output.append(line)
    result = "".join(output)
    for key, value in updates.items():
        if key not in seen:
            if result and not result.endswith(("\n", "\r")):
                result += newline
            result += key + "=" + value + newline
    return result
