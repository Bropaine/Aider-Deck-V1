"""Workspace names, ignore rules, and validated file arguments for Aider."""

from pathlib import Path, PureWindowsPath
import os
import subprocess
from pathspec import GitIgnoreSpec


def relative_path(root, value, *, glob=False):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid workspace path.")
    value = value.replace("\\", "/")
    parts = value.split("/")
    if (
        PureWindowsPath(value).anchor
        or any(p == ".." or p.rstrip(". ").lower() == ".git" for p in parts)
        or any(c in value for c in ':"><|')
        or value.startswith("~")
        or any(
            p
            and p not in (".", "..")
            and (p.endswith((" ", ".")) or PureWindowsPath(p).is_reserved())
            for p in parts
        )
    ):
        raise ValueError("Choose a path within the workspace.")
    if not glob and any(c in value for c in "*?"):
        raise ValueError("Expected a file or directory name.")
    return value


def confined(root, value):
    value = relative_path(root, value)
    path = (root / value).resolve()
    if not path.is_relative_to(root) or any(
        s.casefold() == ".git" for s in path.relative_to(root).parts
    ):
        raise ValueError("Path leaves the workspace.")
    return path


def ignored_names(root, names, *, include_tracked=False, ignorefile=None):
    ignored = set()
    if names:
        result = subprocess.run(
            ["git", "check-ignore", *(["--no-index"] if include_tracked else []), "--stdin", "-z"],
            cwd=root,
            input="\0".join(names).encode() + b"\0",
            capture_output=True,
        )
        ignored.update(result.stdout.decode("utf-8", "replace").split("\0"))
    if ignorefile is None:
        ignorefile = confined(root, ".aiderignore")
    rules = GitIgnoreSpec.from_lines(
        ignorefile.read_text(encoding="utf-8-sig").splitlines()
        if ignorefile.is_file()
        else []
    )
    return ignored | {name for name in names if rules.match_file(name)}


def tree(root, directory, show_ignored):
    path = confined(root, directory)
    children = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))
    children = [
        p for p in children if p.name.lower() != ".git" and p.resolve().is_relative_to(root)
    ]
    names = [p.relative_to(root).as_posix() for p in children]
    ignored = ignored_names(root, [n + ("/" if p.is_dir() else "") for n, p in zip(names, children)])
    rows = []
    for p, name in zip(children, names):
        directory = p.is_dir()
        muted = name + ("/" if directory else "") in ignored
        if show_ignored or not muted:
            rows.append(
                dict(path=name, name=p.name, directory=directory, ignored=bool(muted))
            )
    return rows


def file_command(root, action, pattern, *, attached=None, allow_missing_read_only=True):
    if action not in ("add", "read-only", "drop"):
        raise ValueError("Unknown file action.")
    pattern = relative_path(root, pattern, glob=True)
    if not pattern.strip():
        raise ValueError("Choose a file or a glob.")
    literal = root / pattern
    if (any(c in pattern for c in "*?[") and not literal.is_file()
            and not (action == "drop" and literal in (attached or {}))):
        # Expand and validate every match before handing a single line to Aider.
        # This also prevents Aider resolving the pattern against a parent Git root.
        matches = sorted(p for p in root.glob(pattern) if p.is_file() and
                         ".git" not in (s.lower() for s in p.relative_to(root).parts))
        ignored = ignored_names(root, [p.relative_to(root).as_posix() for p in matches])
        matches = [p for p in matches if p.relative_to(root).as_posix() not in ignored]
    else:
        matches = [root / pattern]
    if not matches:
        raise ValueError("No matching files.")
    return _files_line(root, action, (path.relative_to(root).as_posix() for path in matches),
                       attached, allow_missing_read_only)


def selected_files_command(root, action, names, *, attached=None, allow_missing_read_only=True):
    if action not in ("add", "read-only", "drop"):
        raise ValueError("Unknown file action.")
    if not isinstance(names, list) or not names:
        raise ValueError("Select one or more workspace files.")
    return _files_line(root, action, names, attached, allow_missing_read_only, from_chat=action == "drop")


def chat_file_paths(repo, pane):
    if pane["stale"]:
        return {}
    return {
        Path(os.path.abspath(repo / row["path"])): row["read_only"]
        for row in pane["data"]
    }


def _files_line(root, action, names, attached, allow_missing_read_only, *, from_chat=False):
    paths = {}
    for name in names:
        resolved = confined(root, name)
        if from_chat and attached is not None and resolved not in attached:
            raise ValueError("Refresh Files in chat and select the attached files again.")
        if not resolved.is_file() and not (
            action == "drop" and resolved in (attached or {}) and not resolved.exists()
        ):
            raise ValueError("Choose existing files or a file glob.")
        paths[resolved] = None
    if action == "drop":
        return _drop_line(paths, attached or {}, allow_missing_read_only)
    return "/" + action + " " + " ".join(f'"{path}"' for path in paths)


def _drop_line(paths, attached, allow_missing_read_only):
    # Aider 0.86 first substring-matches filenames (even absolute ones).
    # /./ can't occur in its canonical attachment names and forces exact
    # samefile/path matching. Hard-linked read-only aliases are a native
    # samefile match as well, so handle those before sending any command.
    unselected = [p for p in attached if p not in paths]
    reported = {p: str(p) for p in attached}
    args = []
    for path in paths:
        if path.is_file():
            info = path.stat()
            if info.st_nlink > 1:
                for other in unselected:
                    if not attached[other] or other.drive != path.drive:
                        continue
                    try:
                        linked = path.samefile(other)
                    except FileNotFoundError:
                        continue
                    if linked:
                        raise ValueError("Aider would also drop a read-only hard link. Select both attachments.")
        elif attached.get(path):
            # A deleted read-only file cannot pass samefile(). Aider can only
            # match it by its original spelling; disallow collateral matches.
            if not allow_missing_read_only:
                raise ValueError("Wait for Aider to finish before dropping a deleted read-only attachment.")
            name = reported[path]
            if any(name.casefold() in str(p).casefold() for p in unselected):
                raise ValueError("Aider would also drop another attachment with this path prefix. Select both files.")
            args.append(name)
            continue
        args.append(f"{path.parent.as_posix()}/./{path.name}")
    return "/drop " + " ".join(f'"{arg}"' for arg in args)
