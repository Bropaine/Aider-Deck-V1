import subprocess
from uuid import uuid4

import pytest

from app import create_app
from deck import git_safety_routes
from deck.scan_values import workspace_values


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def safety_workspace(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Local Tester")
    git(repo, "config", "user.email", "tester@example.invalid")
    client = create_app(tmp_path / "state.json", tmp_path).test_client()
    wid = client.post("/api/workspaces", json={"path": str(repo)}).json["id"]
    return client, repo, f"/api/{wid}/git"


def test_preview_reads_staged_bytes_and_outgoing_history(safety_workspace):
    client, repo, url = safety_workspace
    value = uuid4().hex
    (repo / ".env").write_text(f"LOCAL_API_KEY='{value}'\n")
    file = repo / "notes.txt"
    file.write_text(f"line one\n{value}\n")
    git(repo, "add", "notes.txt")
    file.write_text("clean working copy\n")
    preview = client.post(url + "/safety/check", json={"scope": "staged"})
    assert preview.status_code == 200
    assert value not in preview.get_data(as_text=True)
    assert any(f["path"] == "notes.txt" and f["line"] == 2 for f in preview.json["findings"])
    assert client.get(url + "/safety").json["revision"] == preview.json["revision"]
    # Local commits retain their existing semantics; the protection runs before pushing.
    assert client.post(url + "/commit", json={"message": "work in progress"}).status_code == 200
    git(repo, "add", "notes.txt")
    git(repo, "commit", "-qm", "revise notes")
    outgoing = client.post(url + "/safety/check", json={"scope": "outgoing"})
    assert outgoing.status_code == 200
    assert value not in outgoing.get_data(as_text=True)
    assert outgoing.json["commits_scanned"] == 2 and outgoing.json["findings"]
    assert outgoing.json["coverage"] and outgoing.json["revision"] != preview.json["revision"]


def test_enable_is_repo_local_and_reading_status_leaves_hook_off(safety_workspace):
    client, _, url = safety_workspace
    before = client.get(url + "/safety")
    assert before.status_code == 200 and before.json["hook"]["can_enable"]
    assert not before.json["hook"]["enabled"]
    for _ in range(2):
        enabled = client.post(url + "/safety/enable", json={})
        assert enabled.status_code == 200 and enabled.json["hook"]["enabled"]
    assert client.get(url + "/safety").json["hook"]["enabled"]
    assert client.post(url + "/safety/check", json={"scope": "push"}).status_code == 400


def test_changing_the_same_staged_path_invalidates_preview(safety_workspace, monkeypatch):
    client, repo, url = safety_workspace
    f = repo / "file.txt"
    f.write_text("first")
    git(repo, "add", "file.txt")
    original = git_safety_routes.staged_scan.scan_staged

    def with_restage(*args):
        result = original(*args)
        f.write_text("second")
        git(repo, "add", "file.txt")
        return result

    monkeypatch.setattr(git_safety_routes.staged_scan, "scan_staged", with_restage)
    response = client.post(url + "/safety/check", json={"scope": "staged"})
    assert response.status_code == 409 and "again" in response.json["error"]


def test_known_values_use_the_existing_dotenv_parser(safety_workspace, monkeypatch):
    _, repo, _ = safety_workspace
    value = uuid4().hex
    monkeypatch.setenv("TEMP_SCAN_SOURCE", value)
    (repo / ".env").write_text(
        'LOCAL_API_KEY="${TEMP_SCAN_SOURCE}"\n'
        f"HTTP_AUTHORIZATION='Bearer {value}'\n"
        "OLLAMA_API_BASE=http://127.0.0.1:11434\n"
    )
    values = workspace_values(repo)
    assert value in values and "Bearer " + value in values
    assert "http://127.0.0.1:11434" not in values
