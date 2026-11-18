import subprocess

import pytest

from deck import git_service as git


def raw(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, check=True
    ).stdout


@pytest.fixture
def repo(tmp_path):
    raw(tmp_path, "init", "-b", "main")
    raw(tmp_path, "config", "user.name", "Deck Test")
    raw(tmp_path, "config", "user.email", "deck@example.invalid")
    for name in ("both.txt", "old name.txt"):
        (tmp_path / name).write_text("before\n", encoding="utf-8")
    raw(tmp_path, "add", ".")
    raw(tmp_path, "commit", "-m", "initial")
    return tmp_path


def test_status_actions_and_history(repo):
    raw(repo, "mv", "old name.txt", "new name.txt")
    tracked = repo / "both.txt"
    tracked.write_text("staged\n")
    git.change(repo, "stage", "both.txt")
    tracked.write_text("unstaged\n")
    (repo / "untouched.txt").write_text("untracked\n")
    data = git.status(repo)
    assert {r["path"] for r in data["staged"]} == {"both.txt", "new name.txt"}
    rename = next(r for r in data["staged"] if r["code"] == "R")
    assert rename["old_path"] == "old name.txt"
    assert [r["path"] for r in data["unstaged"]] == ["both.txt"]
    assert [r["path"] for r in data["untracked"]] == ["untouched.txt"]
    assert "+unstaged" in git.diff(repo, "both.txt")["diff"]
    assert "+staged" in git.diff(repo, "both.txt", staged=True)["diff"]
    assert "+untracked" in git.diff(repo, "untouched.txt")["diff"]
    git.change(repo, "unstage", "new name.txt")
    git.change(repo, "discard", "both.txt", confirmed=True)
    assert tracked.read_text() == "staged\n"
    with pytest.raises(ValueError):
        git.change(repo, "discard", "untouched.txt")
    git.change(repo, "discard", "untouched.txt", confirmed=True)
    assert not (repo / "untouched.txt").exists()
    msg = 'UI commit with "quotes" & literal $text'
    git.change(repo, "commit", None, message=msg)
    newest = git.status(repo)["log"][0]
    assert newest["subject"] == msg
    assert raw(repo, "rev-parse", "HEAD").decode().strip() == newest["hash"]
    assert "+staged" in git.diff(repo, commit=newest["hash"])["diff"]


def test_unborn_branch_unstage(tmp_path):
    raw(tmp_path, "init", "-b", "main")
    (tmp_path / "foo").touch()
    git.change(tmp_path, "stage", "foo")
    git.change(tmp_path, "unstage", "foo")
    assert git.status(tmp_path)["untracked"][0]["path"] == "foo"


def test_tracking_branch_distances_and_missing_upstream(repo):
    raw(repo, "switch", "-c", "tracking")
    raw(repo, "commit", "--allow-empty", "-m", "upstream only")
    raw(repo, "switch", "main")
    raw(repo, "branch", "--set-upstream-to=tracking", "main")
    raw(repo, "commit", "--allow-empty", "-m", "local only")
    summary = git.status(repo)
    assert (summary["ahead"], summary["behind"]) == (1, 1)
    raw(repo, "update-ref", "-d", "refs/heads/tracking")
    summary = git.status(repo)
    assert (summary["ahead"], summary["behind"]) == (None, None)
    assert summary["log"][0]["subject"] == "local only"
    raw(repo, "checkout", "--detach")
    assert git.status(repo)["branch"] == "(detached)"


def test_historical_environment_values_do_not_reach_diff(repo):
    (repo / ".env").write_text("MY_KEY=retired-secret\n")
    raw(repo, "add", "-f", ".env")
    raw(repo, "commit", "-m", "existing config")
    assert (
        "retired-secret"
        not in git.diff(repo, commit=git.status(repo)["log"][0]["hash"])["diff"]
    )
