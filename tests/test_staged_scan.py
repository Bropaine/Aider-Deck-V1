import json
import os
from pathlib import Path
import subprocess

import pytest

from deck import staged_scan as scan


def git(root, *args, data=None, env=None):
    return subprocess.check_output(
        ["git", *args], cwd=root, input=data, env=env, stderr=subprocess.DEVNULL
    )


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "Deck Test")
    git(tmp_path, "config", "user.email", "deck@example.invalid")
    git(tmp_path, "config", "core.autocrlf", "false")
    return tmp_path


def stage(repo, path, data):
    (repo / path).parent.mkdir(exist_ok=True, parents=True)
    (repo / path).write_bytes(data)
    git(repo, "add", "--", path)


def marker():
    return "gh" + "p_" + "a7Kz9Nm" * 6


def test_scan_uses_immutable_index_blobs_through_first_commit_and_rename(repo):
    sample = marker()
    path = "notes ü[1].md"
    stage(repo, path, ("Heading\n" + sample + "\n").encode())
    (repo / path).write_text("already removed in working tree")
    (repo / "untracked").write_text(sample)
    report = scan.scan_staged(repo)
    assert report["base"] is None and not report["truncated"]
    assert report["files_scanned"] == 1
    assert report["tree"] == git(repo, "write-tree").decode().strip()
    assert report["findings"] == [{"path": path, "kind": "Provider credential", "line": 2}]
    assert sample not in json.dumps(report)
    git(repo, "commit", "-m", "sample")
    assert not scan.scan_staged(repo)["findings"]
    git(repo, "mv", path, "public.md")
    assert scan.scan_staged(repo)["findings"][0]["path"] == "public.md"
    git(repo, "rm", "-f", "public.md")
    assert not scan.scan_staged(repo)["findings"]


def test_patterns_and_saved_credentials_do_not_expose_values():
    known = "opaque-" + "coffee-42"
    sample = marker()
    content = ("header\n" + known + "\n"
               + "-----BEGIN " + "RSA PRIVATE KEY-----\n"
               + "CUSTOM_API_" + 'KEY="e6n' + 'YVA5f8wXARh0F"\n'
               + "PASSWORD=" + "my-long-passphrase\n"
               + "Content: Bearer " + sample + "\n"
               + "https://reader:" + "test-2wqK6Mv@host.example/db\n").encode()
    findings = scan.scan_text(content, "record", [known])
    assert {(f["line"], f["kind"]) for f in findings} == {
        (2, "Saved credential"), (3, "Private key"), (4, "Possible credential assignment"),
        (5, "Possible credential assignment"), (6, "Possible bearer token"),
        (6, "Provider credential"), (7, "Credential in URL"),
    }
    assert known not in json.dumps(findings) and sample not in json.dumps(findings)
    assert scan.scan_text(b'api_key = os.getenv("EXAMPLE_API_KEY")\n'
                          b'password = "${FROM_VAULT}"\n'
                          b'api_key: your_api_key_here\n'
                          b'{"input_cost_per_token":0.0000001}', "example.py") == []


def test_binary_utf16_large_cross_block_and_sensitive_path(repo):
    sample = marker()
    stage(repo, "document.txt", ("one\ntwo\n" + sample).encode("utf-16"))
    stage(repo, "binary.dat", b"\x00" + b"-" * (scan.BLOCK - 9) + sample.encode() + b"\x01")
    stage(repo, sample + ".txt", b"nothing inside")
    stage(repo, "prompts/.gitkeep", b"")
    stage(repo, "private/.env.local", b"")
    report = scan.scan_staged(repo)
    assert report["files_scanned"] == 5
    assert {(f["path"], f["line"], f["kind"]) for f in report["findings"]} == {
        ("document.txt", 3, "Provider credential"), ("binary.dat", None, "Provider credential"),
        (scan.MASKED_PATH, None, "Sensitive filename"), ("private/.env.local", None, "Private file"),
    }
    assert sample not in json.dumps(report)
    assert scan.display_path("test\nline.md") == r"test\nline.md"
    assert scan.display_path("folder/owner-deployed-key.txt", ["deployed-key"]) == scan.MASKED_PATH


def test_known_multiline_and_capped_report(repo):
    key = "lineOne" + "\n" + "lineTwo"
    stage(repo, "config", b"prefix\r\n" + key.replace("\n", "\r\n").encode())
    assert scan.scan_staged(repo, [key])["findings"] == [
        {"path": "config", "line": 2, "kind": "Saved credential"}
    ]
    stage(repo, "many.txt", ("\n".join([marker()] * (scan.MAX_FINDINGS + 10))).encode())
    report = scan.scan_staged(repo)
    assert report["truncated"] and len(report["findings"]) == scan.MAX_FINDINGS


def test_unreadable_object_unmerged_index_and_separate_index(repo):
    with pytest.raises(scan.ScanError):
        scan.scan_entries(repo, [("0" * 40, "secret value.txt")])
    stage(repo, "baseline", b"main\n")
    git(repo, "commit", "-m", "first")
    stage(repo, "staged", marker().encode())
    own_index = Path(repo).parent / "scratch.index"
    git(repo, "read-tree", "HEAD", env={**os.environ, "GIT_INDEX_FILE": str(own_index)})
    assert scan.scan_staged(repo, index_file=own_index)["files_scanned"] == 0
    assert scan.scan_staged(repo)["files_scanned"] == 1
    base_blob = git(repo, "rev-parse", "HEAD:baseline").decode().strip()
    git(repo, "update-index", "--index-info", data=(
        f"0 {'0'*40}\tbaseline\n"
        f"100644 {base_blob} 1\tbaseline\n100644 {base_blob} 2\tbaseline\n"
    ).encode())
    with pytest.raises(scan.ScanError, match="conflicts or missing"):
        scan.scan_staged(repo)
