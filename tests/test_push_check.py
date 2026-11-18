import os
from pathlib import Path
import subprocess
import sys

import pytest

from deck import push_check as check


def git(repo, *args, data=None, fail=True):
    result = subprocess.run(["git", *args], cwd=repo, input=data, capture_output=True)
    if fail:
        result.check_returncode()
    return result


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo with spaces"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Deck Tester")
    git(root, "config", "user.email", "test@example.invalid")
    return root


def commit(repo, name, data, message="change"):
    (repo / name).write_bytes(data)
    git(repo, "add", "--", name)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip().decode()


def update(oid, base=None, name="refs/heads/main"):
    return f"HEAD {oid} {name} {base or '0' * len(oid)}\n".encode()


def marker():
    return "a7B" + "9bN45cOWje8e2VHW"


def test_native_hook_stops_removed_secret_before_local_push(repo, tmp_path):
    origin = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(origin))
    git(repo, "remote", "add", "origin", str(origin))
    assert check.preview(repo, [])["commits_scanned"] == 0
    assert check.install_hook(repo)["enabled"] and check.install_hook(repo)["enabled"]
    (repo / ".env").write_text("CUSTOM_API_KEY=" + marker() + "\n")
    initial = commit(repo, "notes.md", b"safe\n")
    git(repo, "push", "-u", "origin", "main")
    assert check.preview(repo)["commits_scanned"] == 0
    leaked = commit(repo, "notes.md", b"documentation\n" + marker().encode() + b"\n")
    commit(repo, "notes.md", b"clean again\n")
    preview = check.preview(repo)
    assert preview["commits_scanned"] == 2
    assert any(
        item["commit"] == leaked[:12] and item["line"] == 2
        for item in preview["findings"]
    )
    push = git(repo, "push", "origin", "main", fail=False)
    assert (
        push.returncode
        and b"notes.md:2" in push.stderr
        and leaked[:12].encode() in push.stderr
    )
    assert marker().encode() not in push.stderr + push.stdout
    assert (
        git(origin, "rev-parse", "refs/heads/main").stdout.strip().decode() == initial
    )
    assert not git(repo, "diff", "--cached", "--name-only").stdout


def test_new_refs_force_updates_and_deletions(repo):
    safe = commit(repo, "safe.txt", b"ordinary")
    unsafe = commit(repo, "other.txt", marker().encode())
    # A new branch using earlier history needs an independent check even if
    # its HEAD, assumed clean in the editor, was never the current branch.
    git(repo, "checkout", "--detach", safe)
    result = check.check_updates(
        repo, [update(unsafe), update(safe, "f" * 40)], [marker()]
    )
    assert result["commits_scanned"] == 2 and any(
        f["path"] == "other.txt" for f in result["findings"]
    )
    assert (
        check.check_updates(repo, [update("0" * 40, unsafe)], [])["commits_scanned"]
        == 0
    )
    assert check.check_updates(repo, [update(safe, unsafe)], [])["findings"] == []
    with pytest.raises(ValueError):
        check.check_updates(repo, [b"not a hook record"], [])


def test_annotated_tag_direct_blob_tree_commit_metadata_and_ref_name(repo):
    benign = commit(repo, "notes.md", b"ordinary", message=marker())
    git(repo, "tag", "-a", "annotated", "-m", marker())
    tag = git(repo, "rev-parse", "refs/tags/annotated").stdout.strip().decode()
    raw_blob = (
        git(repo, "hash-object", "-w", "--stdin", data=marker().encode())
        .stdout.strip()
        .decode()
    )
    tree = (
        git(repo, "mktree", data=f"100644 blob {raw_blob}\tname.txt\n".encode())
        .stdout.strip()
        .decode()
    )
    result = check.check_updates(
        repo,
        [
            update(tag, benign, "refs/tags/annotated"),
            update(tree),
            update(raw_blob, name="refs/tags/" + marker()),
        ],
        [marker()],
    )
    assert {f["path"] for f in result["findings"]} >= {
        "(tag metadata)",
        "(destination ref name)",
        "name.txt",
        "(pushed blob)",
    }
    assert marker() not in str(result)
    assert any(
        f["path"] == "(commit metadata)"
        for f in check.preview(repo, [marker()])["findings"]
    )


def test_merge_resolution_and_separate_parent_history(repo):
    commit(repo, "base.md", b"base")
    git(repo, "checkout", "-b", "other")
    side = commit(repo, "feature.md", marker().encode())
    commit(repo, "feature.md", b"redacted")
    git(repo, "checkout", "main")
    commit(repo, "main.md", b"main")
    git(repo, "merge", "--no-commit", "other")
    merge = commit(repo, "resolution.md", marker().encode(), message="merge")
    result = check.preview(repo, [marker()])
    assert {(f["path"], f["commit"]) for f in result["findings"]} >= {
        ("feature.md", side[:12]),
        ("resolution.md", merge[:12]),
    }


def test_submodule_path_with_secret_is_sanitized(repo):
    tip = commit(repo, "stable.txt", b"contents")
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{tip},nested-{marker()}")
    git(repo, "commit", "-m", "add pointer")
    result = check.preview(repo, [marker()])
    assert any(f["path"] == "(submodule path)" for f in result["findings"])
    assert marker() not in str(result)


def test_hook_install_respects_existing_custom_and_linked_worktrees(repo, tmp_path):
    commit(repo, "readme.txt", b"sample")
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_bytes(b"existing hook")
    with pytest.raises(ValueError, match="already exists"):
        check.install_hook(repo)
    assert hook.read_bytes() == b"existing hook"
    hook.unlink()
    git(repo, "config", "core.hooksPath", "../shared")
    with pytest.raises(ValueError, match="custom"):
        check.install_hook(repo)
    assert not (tmp_path / "shared").exists()
    git(repo, "config", "--unset", "core.hooksPath")
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "--detach", str(linked))
    assert check.install_hook(linked)["enabled"]
    assert check.hook_status(repo)["enabled"]
    assert hook.read_bytes().startswith(b"#!/bin/sh\n") and os.access(hook, os.X_OK)


def test_standalone_hook_refuses_unreadable_update_without_echo(repo):
    script = Path(check.__file__).resolve()
    result = subprocess.run(
        [sys.executable, "-I", str(script), "hook"],
        cwd=repo,
        input=marker().encode(),
        capture_output=True,
    )
    assert (
        result.returncode != 0
        and marker().encode() not in result.stderr + result.stdout
    )
