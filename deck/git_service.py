"""Git operations for the inspector. All paths in status records are repo-relative."""

import os
import re
import subprocess
from pathlib import Path

from .files import confined


class GitError(ValueError):
    pass


def run(root, *args, ok=(0,), data=None):
    proc = subprocess.run(
        [
            "git",
            "--no-pager",
            "--literal-pathspecs",
            "-c",
            "core.quotepath=false",
            *args,
        ],
        cwd=root,
        input=data,
        capture_output=True,
        timeout=60,
    )
    if proc.returncode not in ok:
        raise GitError(
            proc.stderr.decode("utf-8", "replace").strip()
            or proc.stdout.decode("utf-8", "replace").strip()
            or "Git command failed."
        )
    return proc.stdout.decode("utf-8", "replace")


def repository(path):
    return Path(run(path, "rev-parse", "--show-toplevel").strip()).resolve()


def parse_status(raw):
    groups = {"staged": [], "unstaged": [], "untracked": []}
    records = iter(raw.rstrip("\0").split("\0") if raw else [])
    for record in records:
        xy, path = record[:2], record[3:]
        old = next(records) if any(c in xy for c in "RC") else None
        if xy == "??":
            groups["untracked"].append(dict(path=path, code="?", old_path=None))
            continue
        for col, group in zip(xy, ("staged", "unstaged")):
            if col not in " !":
                groups[group].append(dict(path=path, code=col, old_path=old))
    return groups


def status(root, with_log=True):
    root = repository(root)
    rows = parse_status(
        run(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    )
    branch = run(root, "branch", "--show-current").strip()
    data = dict(
        **rows,
        branch=branch or "(detached)",
        dirty=any(rows.values()),
        ahead=None,
        behind=None,
        log=[],
    )
    if not with_log:
        return data
    if run(root, "rev-parse", "--verify", "--quiet", "HEAD", ok=(0, 1)):
        raw = run(root, "log", "-20", "-z", "--format=%H%x00%s%x00%an%x00%ar")
        parts = raw.rstrip("\0").split("\0")
        data["log"] = [
            dict(zip(("hash", "subject", "author", "time"), parts[i : i + 4]))
            for i in range(0, len(parts), 4)
        ]
        # A tracking branch can survive deletion of its upstream ref. Keep the
        # log and working tree available when there is no commit to compare.
        upstream = (
            branch
            and run(
                root,
                "rev-parse",
                "--verify",
                "--quiet",
                "@{upstream}^{commit}",
                ok=(0, 1, 128),
            ).strip()
        )
        if upstream:
            counts = run(
                root, "rev-list", "--left-right", "--count", f"HEAD...{upstream}"
            ).split()
            data["ahead"], data["behind"] = map(int, counts)
    return data


def change(root, action, path, confirmed=False, message=None):
    root = repository(root)
    if action == "commit":
        if not isinstance(message, str) or not message.strip():
            raise ValueError("Enter a commit message.")
        return run(root, "commit", "-m", message)
    if not isinstance(path, str) or not path:
        raise ValueError("Select a file.")
    confined(root, path)
    groups = parse_status(
        run(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    )
    matches = {
        group: next((item for item in rows if item["path"] == path), None)
        for group, rows in groups.items()
    }
    if action == "stage" and (matches["untracked"] or matches["unstaged"]):
        return run(root, "add", "--", path)
    if action == "unstage" and matches["staged"]:
        names = [name for name in (path, matches["staged"]["old_path"]) if name]
        if run(root, "rev-parse", "--verify", "--quiet", "HEAD", ok=(0, 1)):
            return run(root, "restore", "--staged", "--", *names)
        return run(root, "rm", "--cached", "--", *names)
    if action == "discard" and confirmed is True:
        if matches["untracked"]:
            # Unlink the directory entry itself (never follow a file symlink).
            (root / path).unlink()
            return f"Deleted {path}"
        if matches["unstaged"]:
            return run(root, "restore", "--", path)
    raise ValueError(
        "The file state changed, or the action needs confirmation. Refresh Git status."
    )


def secret_file(path):
    base = path.strip('"').replace("\\", "/").split("/")[-1].lower()
    return base.startswith(".env") or base in {
        ".aider.conf.yml",
        ".aider.model.settings.yml",
    }


def diff(root, path=None, staged=False, commit=None):
    root = repository(root)
    if path:
        confined(root, path)
    names = ("--", path) if path else ("--",)
    ok = (0,)
    if commit:
        if not re.fullmatch("[0-9a-fA-F]{7,64}", commit):
            raise ValueError("Invalid commit hash.")
        prefix = ("show", "--format=")
        revisions = (commit,)
    else:
        prefix = ("diff",)
        revisions = ("--cached",) if staged else ()
        if (
            path
            and not staged
            and any(
                item["path"] == path
                for item in parse_status(
                    run(root, "status", "--porcelain=v1", "-z", "--", path)
                )["untracked"]
            )
        ):
            revisions, names, ok = ("--no-index",), ("--", os.devnull, path), (0, 1)

    def inspect(*flags):
        return run(
            root,
            *prefix,
            "--no-ext-diff",
            "--no-textconv",
            *flags,
            *revisions,
            *names,
            ok=ok,
        )

    stat, numstat = inspect("--stat"), inspect("--numstat")
    # Suppress historical configuration hunks as they may contain retired credentials.
    if path and secret_file(path):
        content = (
            "Configuration contents are hidden in Git diffs to protect credentials."
        )
    else:
        raw = inspect("--unified=3")
        chunks = re.split(r"(?=^diff --git )", raw, flags=re.M)
        content = "".join(
            block
            if not any(
                secret_file(line[4:].split("\t")[0])
                for line in block.splitlines()
                if line.startswith(("+++ ", "--- "))
            )
            else block.split("\n", 1)[0] + "\n[configuration contents hidden]\n"
            for block in chunks
        )
    return dict(diff=content, stat=stat, numstat=numstat)
