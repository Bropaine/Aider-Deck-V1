"""Local Git history checks for the Git panel and the native pre-push hook."""

import argparse
import io
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile

if not __package__:
    # The installed hook invokes this particular Deck checkout in isolated
    # Python mode, even when Git is pushing from an unrelated repository.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from deck.staged_scan import ScanError, display_path, scan_entries, scan_text
except ImportError:
    if __name__ == "__main__":
        print(
            "Aider Deck's push checker is unavailable. Restore its installation before retrying.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    raise


OID = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
MAX_FINDINGS = 100
MAX_MESSAGE_SIZE = 2 * 1024 * 1024


def _git(repo, *args, data=None, ok=(0,)):
    env = {
        **os.environ,
        "GIT_ALLOW_PROTOCOL": "",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    result = subprocess.run(
        ["git", "--no-pager", *args],
        cwd=repo,
        env=env,
        input=data or b"",
        capture_output=True,
    )
    if result.returncode not in ok:
        # A Git error can quote an entire URL, private ref name or path.
        raise ScanError("Git could not read the local repository for the push check.")
    return result.stdout


def _oid(value):
    if not OID.fullmatch(value):
        raise ScanError("Git supplied an invalid object ID.")
    return value.decode("ascii")


def _root(path):
    value = _git(path, "rev-parse", "--show-toplevel").rstrip(b"\r\n")
    return Path(os.fsdecode(value)).resolve()


def _launcher():
    interpreter = Path(sys.executable).absolute().as_posix()
    script = Path(__file__).resolve().as_posix()
    return (
        "#!/bin/sh\n# Aider Deck local pre-push check\n"
        f"exec {shlex.quote(interpreter)} -I {shlex.quote(script)} hook\n"
    ).encode("utf-8")


def _hook_path(repo):
    value = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(os.fsdecode(value.rstrip(b"\r\n"))) / "hooks" / "pre-push"


def hook_status(root):
    repo = _root(root)
    custom = _git(repo, "config", "--null", "--get", "core.hooksPath", ok=(0, 1))
    if custom:
        return dict(
            enabled=False,
            available=False,
            reason="A custom Git hooks path is configured. Call Deck’s hook from your existing pre-push setup.",
        )
    path = _hook_path(repo)
    linked_directory = path.parent.exists() and bool(
        getattr(path.parent.lstat(), "st_file_attributes", 0)
        & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )
    if linked_directory or path.parent.is_symlink() or path.is_symlink():
        return dict(
            enabled=False,
            available=False,
            reason="Git uses a linked hooks path. Keep the existing hooks and integrate the checker manually.",
        )
    if path.exists():
        expected = _launcher()
        if (
            path.is_file()
            and path.stat().st_size == len(expected)
            and path.read_bytes() == expected
            and os.access(path, os.X_OK)
        ):
            return dict(
                enabled=True,
                available=True,
                reason="The pre-push checker is installed for this repository.",
            )
        return dict(
            enabled=False,
            available=False,
            reason="A pre-push hook already exists. Add the Deck check to it manually.",
        )
    return dict(
        enabled=False,
        available=True,
        reason="The pre-push checker can be installed in this repository.",
    )


def install_hook(root):
    repo = _root(root)
    status = hook_status(repo)
    if status["enabled"]:
        return status
    if not status["available"]:
        raise ScanError(status["reason"])
    hook = _hook_path(repo)
    hook.parent.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=hook.parent, prefix=".deck-install-", delete=False
    ) as f:
        temporary = Path(f.name)
    try:
        temporary.write_bytes(_launcher())
        temporary.chmod(0o755)
        try:
            # Publish a fully written executable and never overwrite another hook.
            if os.name == "nt":
                # Windows rename refuses an existing destination and also works
                # on filesystems where creating hard links is unavailable.
                temporary.rename(hook)
            else:
                os.link(temporary, hook)
        except FileExistsError:
            pass
        except OSError as exc:
            raise ScanError(
                "Git's hooks directory could not install the checker. Check its permissions and filesystem."
            ) from exc
    finally:
        temporary.unlink(missing_ok=True)
    status = hook_status(repo)
    if not status["enabled"]:
        raise ScanError(status["reason"])
    return status


def _values(repo, provided):
    if provided is None:
        from deck.scan_values import workspace_values

        provided = workspace_values(repo)
    return tuple(provided)


def _peel(repo, oid, inspect_tag):
    """Peel every annotated tag, inspecting annotations even on existing commits."""
    while True:
        kind = _git(repo, "cat-file", "-t", oid).strip()
        if kind != b"tag":
            return kind, oid
        size = int(_git(repo, "cat-file", "-s", oid))
        if size > MAX_MESSAGE_SIZE:
            raise ScanError(
                "An annotated tag exceeded the push check's 2 MiB metadata limit."
            )
        raw = _git(repo, "cat-file", "tag", oid)
        inspect_tag(raw, "(tag metadata)", oid)
        prefix, _, value = raw.partition(b"\n")[0].partition(b" ")
        if prefix != b"object":
            raise ScanError("Git supplied an invalid annotated tag.")
        oid = _oid(value)


def _commit_objects(repo, rows):
    """Read the raw commit objects in a batch so messages retain their exact bytes."""
    ids = b"".join(row[0] + b"\n" for row in rows)
    specs = _git(repo, "cat-file", "--batch-check=%(objectsize)", data=ids).splitlines()
    if len(specs) != len(rows) or any(
        not size.isdigit() or int(size) > MAX_MESSAGE_SIZE for size in specs
    ):
        raise ScanError(
            "A commit was unreadable or exceeded the push check's 2 MiB metadata limit."
        )
    stream = io.BytesIO(_git(repo, "cat-file", "--batch", data=ids))
    for (oid, *_), size in zip(rows, specs):
        if stream.readline() != oid + b" commit " + size + b"\n":
            raise ScanError("Git returned an incomplete commit during the push check.")
        raw = stream.read(int(size))
        if len(raw) != int(size) or stream.read(1) != b"\n":
            raise ScanError("Git returned incomplete metadata during the push check.")
        yield _oid(oid), raw


def _file_versions(repo, rows):
    # Each merge is compared to its first parent and all outgoing parents
    # are traversed separately. This includes content written while resolving a merge.
    inputs = b"".join(b" ".join(row[:2]) + b"\n" for row in rows)
    raw = _git(
        repo,
        "diff-tree",
        "--stdin",
        "--root",
        "-r",
        "-z",
        "--raw",
        "--no-renames",
        "--no-abbrev",
        "--diff-filter=AMT",
        data=inputs,
    )
    items = iter(raw.rstrip(b"\0").split(b"\0")) if raw else iter(())
    commit = None
    for header in items:
        if not header.startswith(b":"):
            commit = _oid(header)
            continue
        fields = header[1:].split()
        path = next(items, None)
        if commit is None or len(fields) != 5 or path is None:
            raise ScanError("Git returned an incomplete list of outgoing files.")
        blob = None if fields[1] == b"160000" else _oid(fields[3])
        yield blob, os.fsdecode(path), commit


def _tree_versions(repo, tree):
    listing = _git(repo, "ls-tree", "-r", "-z", tree)
    for entry in listing.rstrip(b"\0").split(b"\0") if listing else ():
        header, _, path = entry.partition(b"\t")
        _, kind, oid = header.split()
        yield _oid(oid) if kind == b"blob" else None, os.fsdecode(path), tree


def _scan(repo, positives, negatives, ref_names, known, coverage):
    findings = []
    truncated = False

    def record(items):
        nonlocal truncated
        for item in items:
            if len(findings) < MAX_FINDINGS:
                findings.append(item)
            else:
                truncated = True
                break

    def inspect_text(raw, label, oid):
        record(dict(row, commit=oid[:12]) for row in scan_text(raw, label, known))

    def collect(entries):
        for blob, path, commit in entries:
            if blob is None:
                # Only the submodule name and object ID travel in this push.
                inspect_text(os.fsencode(path), "(submodule path)", commit)
            else:
                versions.setdefault((blob, path), commit)

    for name, oid in ref_names:
        inspect_text(name, "(destination ref name)", oid)
    tips, excluded, versions = set(), set(), {}
    for tip in sorted(positives):
        kind, oid = _peel(repo, tip, inspect_text)
        if kind == b"commit":
            tips.add(oid)
        elif kind == b"tree":
            collect(_tree_versions(repo, oid))
        elif kind == b"blob":
            versions[(oid, "(pushed blob)")] = oid
        else:
            raise ScanError("Git returned an unsupported outgoing object.")
    for base in sorted(negatives):
        peeled = _git(
            repo, "rev-parse", "--verify", "--quiet", base + "^{commit}", ok=(0, 1, 128)
        ).strip()
        if peeled:
            excluded.add(_oid(peeled))
    rows = []
    if tips:
        revisions = list(sorted(tips)) + ["^" + x for x in sorted(excluded)]
        raw = _git(
            repo,
            "rev-list",
            "--parents",
            "--reverse",
            "--topo-order",
            "--stdin",
            data=("\n".join(revisions) + "\n").encode("ascii"),
        )
        rows = [line.split() for line in raw.splitlines()]
        for start in range(0, len(rows), 32):
            batch = rows[start : start + 32]
            for oid, content in _commit_objects(repo, batch):
                inspect_text(content, "(commit metadata)", oid)
            collect(_file_versions(repo, batch))

    scanned = scan_entries(repo, versions.keys(), known, with_oids=True)
    matched_ids = {entry["oid"] for entry in scanned["findings"]}
    first_seen = {
        (oid, display_path(path, known)): commit
        for (oid, path), commit in versions.items()
        if oid in matched_ids
    }
    for entry in scanned["findings"]:
        entry = dict(entry)
        oid = entry.pop("oid")
        entry["commit"] = first_seen[(oid, entry["path"])][:12]
        record([entry])
    return dict(
        findings=findings,
        files_scanned=scanned["files_scanned"],
        commits_scanned=len(rows),
        truncated=truncated or scanned["truncated"],
        scope="push",
        coverage=coverage,
    )


def preview(root, known_values=None):
    repo = _root(root)
    known = _values(repo, known_values)
    head = _git(
        repo, "rev-parse", "--verify", "--quiet", "HEAD^{commit}", ok=(0, 1)
    ).strip()
    if not head:
        return _scan(
            repo, set(), set(), [], known, "The current branch has no commits."
        )
    base = _git(
        repo, "rev-parse", "--verify", "--quiet", "@{upstream}^{commit}", ok=(0, 1, 128)
    ).strip()
    branch = _git(repo, "symbolic-ref", "-q", "HEAD", ok=(0, 1)).strip()
    history = (
        "Commits on HEAD beyond the locally recorded upstream. The push hook checks the actual destination refs."
        if base
        else "All locally available history reachable from HEAD. No local upstream was available."
    )
    head_oid = _oid(head)
    return _scan(
        repo,
        {head_oid},
        {_oid(base)} if base else set(),
        [(branch, head_oid)] if branch else [],
        known,
        history,
    )


def check_updates(root, lines, known_values=None):
    repo = _root(root)
    tips, excluded, ref_names = set(), set(), []
    for line in lines:
        fields = line.split()
        if len(fields) != 4:
            raise ScanError("Git supplied an invalid pre-push update.")
        _, local, refname, remote = fields
        tip, base = _oid(local), _oid(remote)
        if not tip.strip("0"):
            continue  # A deletion publishes no content.
        tips.add(tip)
        ref_names.append((refname, tip))
        if base.strip("0"):
            excluded.add(base)
    return _scan(
        repo,
        tips,
        excluded,
        ref_names,
        _values(repo, known_values) if tips else (),
        "Outgoing objects from Git's pre-push updates. Unknown remote history is checked conservatively.",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=["check", "hook", "install", "status"],
        default="check",
        nargs="?",
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        if args.action in ("install", "status"):
            status = (install_hook if args.action == "install" else hook_status)(
                args.repo
            )
            print(status["reason"])
            return 0
        result = (
            check_updates(args.repo, sys.stdin.buffer)
            if args.action == "hook"
            else preview(args.repo)
        )
        for item in result["findings"]:
            suffix = f":{item['line']}" if item.get("line") else ""
            print(
                f"{item['commit']}  {item['path']}{suffix}  {item['kind']}",
                file=sys.stderr,
            )
        if result["findings"] or result["truncated"]:
            print(
                "Aider Deck blocked the push check. Review the flagged content in outgoing history.",
                file=sys.stderr,
            )
            return 1
        print(
            f"Aider Deck checked {result['commits_scanned']} commits and {result['files_scanned']} file versions."
        )
        return 0
    except ScanError as exc:
        # ScanError messages are written by Deck and contain no raw Git output.
        print(f"Aider Deck could not complete the push check. {exc}", file=sys.stderr)
        return 2
    except Exception:
        # External errors and child-process stderr can contain private strings.
        print(
            "Aider Deck could not complete the push check. Check the Deck installation and local Git objects before retrying.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
