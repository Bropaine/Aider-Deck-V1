import pytest
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

from app import create_app


@pytest.fixture
def client(tmp_path):
    return create_app(tmp_path / "state.json", tmp_path).test_client()


def test_first_launch_creates_prompt_library_and_preserves_existing_prompts(tmp_path):
    data_dir = tmp_path / "Application Support" / "Aider-Deck"
    prompt_dir = data_dir / "prompts"
    state_path = data_dir / "deck-state.json"
    client = create_app(state_path, prompt_dir).test_client()
    assert prompt_dir.is_dir()
    wid = client.post("/api/workspaces", json={"path": str(tmp_path)}).json["id"]
    assert client.get(f"/api/{wid}/prompts").json == {
        "folder": str(prompt_dir.resolve()), "prompts": [],
    }

    prompt = prompt_dir / "review.md"
    prompt.write_text("Review this change.\n", encoding="utf-8")
    client = create_app(state_path, prompt_dir).test_client()
    assert client.get(f"/api/{wid}/prompts").json["prompts"][0]["name"] == prompt.name
    assert prompt.read_text(encoding="utf-8") == "Review this change.\n"


def test_loopback_origin_and_json_are_required_for_writes(client, tmp_path):
    payload = {"path": str(tmp_path)}
    assert (
        client.post(
            "/api/workspaces", json=payload, headers={"Origin": "https://example.org"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/workspaces", json=payload, headers={"Host": "example.org:5177"}
        ).status_code
        == 403
    )
    assert client.post("/api/workspaces", data=payload).status_code == 415
    assert client.get("/api/workspaces").json["active"] is None


def test_open_only_registers_workspace_and_invalid_config_can_be_repaired(
    client, tmp_path
):
    wid = client.post("/api/workspaces", json={"path": str(tmp_path)}).json["id"]
    assert client.get(f"/api/{wid}/state").json["status"] == "idle"
    assert not (tmp_path / ".aider.conf.yml").exists()
    config_path = tmp_path / ".aider.conf.yml"
    config_path.write_text("model: [bad\n")
    state = client.get(f"/api/{wid}/state").json
    assert state["config_error"] and state["registration"]["registered"] is None
    assert client.get(f"/api/{wid}/config").json["issues"]
    assert client.get(f"/api/{wid}/config/raw/.aider.conf.yml").status_code == 200


@pytest.mark.parametrize("invalid", ["", "  ", None])
def test_blank_prompt_folder_preserves_preference(client, tmp_path, invalid):
    wid = client.post("/api/workspaces", json={"path": str(tmp_path)}).json["id"]
    assert (
        client.post(f"/api/{wid}/prefs", json={"prompt_library": invalid}).status_code
        == 400
    )
    assert client.get(f"/api/{wid}/state").json["prefs"]["prompt_library"] == str(
        tmp_path
    )


@pytest.mark.parametrize("action", ["add", "read-only"])
def test_grouped_workspace_files_are_literal_atomic_and_deduplicated(
    client, tmp_path, action
):
    nested = tmp_path / "nested"
    nested.mkdir()
    first = nested / "two words.txt"
    second = tmp_path / "square[1].txt"
    unselected = tmp_path / "square1.txt"
    for path in (first, second, unselected):
        path.touch()
    wid = client.post("/api/workspaces", json={"path": str(tmp_path)}).json["id"]
    assert client.get(f"/api/{wid}/state").json["supports_file_groups"] is True
    session = client.application.extensions["deck"].session(wid)
    session.command = Mock()
    url = f"/api/{wid}/files"
    invalid = (
        None,
        "square[1].txt",
        [],
        [None],
        ["nested"],
        ["../outside"],
        ["missing.txt"],
        ["*.txt"],
        ["square?.txt"],
        [".git/config"],
        ["x\n/exit"],
    )
    for bad in invalid:
        names = ["square[1].txt", *bad] if isinstance(bad, list) and bad else bad
        assert (
            client.post(url, json=dict(action=action, paths=names)).status_code == 400
        )
    for extra in ({"action": "drop"}, {"from_chat": True}, {"path": "square1.txt"}):
        body = {"action": action, "paths": ["square[1].txt"], **extra}
        assert client.post(url, json=body).status_code == 400
    session.command.assert_not_called()

    names = ["nested/two words.txt", "square[1].txt", r"nested\two words.txt"]
    assert client.post(url, json=dict(action=action, paths=names)).status_code == 200
    session.command.assert_called_once_with(f'/{action} "{first}" "{second}"')


def test_drop_group_uses_chat_paths_in_a_nested_workspace(client, tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    work = repo / "module"
    work.mkdir()
    names = ["one file.txt", "one file.txt.bak", "note[1].md", "note1.md"]
    for name in names:
        (work / name).touch()
    wid = client.post("/api/workspaces", json={"path": str(work)}).json["id"]
    assert client.get(f"/api/{wid}/state").json["supports_group_drop"] is True
    session = client.application.extensions["deck"].session(wid)
    session.command = Mock()
    chat = session.obs.data["files"]
    chat.update(
        stale=False,
        data=[
            dict(path="module/" + name, read_only=i >= 2)
            for i, name in enumerate(names)
        ],
    )
    url = f"/api/{wid}/files"
    targets = ["module/one file.txt", "module/note[1].md", "module/one file.txt"]
    assert client.post(url, json=dict(action="drop", paths=targets)).status_code == 200
    expected = (
        f'/drop "{work.as_posix()}/./one file.txt" "{work.as_posix()}/./note[1].md"'
    )
    session.command.assert_called_once_with(expected)
    session.command.reset_mock()
    for invalid in (
        [],
        ["module"],
        ["module/*.md"],
        ["module/note*"],
        ["one file.txt"],
        ["../elsewhere"],
        targets + [".git/config"],
    ):
        assert (
            client.post(url, json=dict(action="drop", paths=invalid)).status_code == 400
        )
    chat["stale"] = True
    assert client.post(url, json=dict(action="drop", paths=targets)).status_code == 400
    session.command.assert_not_called()


def test_drop_missing_attachments_and_read_only_prefix_ambiguity(client, tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    wid = client.post("/api/workspaces", json={"path": str(tmp_path)}).json["id"]
    session = client.application.extensions["deck"].session(wid)
    session.command = Mock()
    chat, url = session.obs.data["files"], f"/api/{wid}/files"
    missing = tmp_path / "gone [1].txt"
    chat.update(stale=False, data=[dict(path=missing.name, read_only=False)])
    assert (
        client.post(
            url, json=dict(action="drop", path=missing.name, from_chat=True)
        ).status_code
        == 200
    )
    session.command.assert_called_once_with(
        f'/drop "{tmp_path.as_posix()}/./{missing.name}"'
    )
    session.command.reset_mock()
    chat["data"][0]["read_only"] = True
    blocked = client.post(url, json=dict(action="drop", paths=[missing.name]))
    assert blocked.status_code == 400 and "Wait for Aider" in blocked.json["error"]
    session.command.assert_not_called()
    session.status = "live"
    session.obs.ready = True
    assert (
        client.post(url, json=dict(action="drop", paths=[missing.name])).status_code
        == 200
    )
    session.command.assert_called_once_with(f'/drop "{missing}"')
    session.command.reset_mock()
    sibling = missing.with_suffix(".txt.bak")
    sibling.touch()
    chat["data"].append(dict(path=sibling.name, read_only=False))
    blocked = client.post(url, json=dict(action="drop", paths=[missing.name]))
    assert blocked.status_code == 400 and "prefix" in blocked.json["error"]
    session.command.assert_not_called()
    assert (
        client.post(
            url, json=dict(action="drop", paths=[missing.name, sibling.name])
        ).status_code
        == 200
    )
    session.command.assert_called_once_with(
        f'/drop "{missing}" "{tmp_path.as_posix()}/./{sibling.name}"'
    )


def test_drop_detects_unselected_readonly_hardlink(client, tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    original, alias = tmp_path / "first", tmp_path / "alias"
    original.touch()
    alias.touch()
    # Some Windows sandboxes prohibit creating hardlinks. Give both regular
    # fixture files the same file identity and link count for this unit test.
    stat_fields = list(original.stat())
    stat_fields[3] = 2
    link_info = os.stat_result(stat_fields)
    real_stat = Path.stat

    def stat(path, *args, **kwargs):
        return (
            link_info if path in (original, alias) else real_stat(path, *args, **kwargs)
        )

    monkeypatch.setattr(Path, "stat", stat)
    wid = client.post("/api/workspaces", json={"path": str(tmp_path)}).json["id"]
    session = client.application.extensions["deck"].session(wid)
    session.command = Mock()
    session.obs.data["files"].update(
        stale=False,
        data=[dict(path="first", read_only=False), dict(path="alias", read_only=True)],
    )
    url = f"/api/{wid}/files"
    blocked = client.post(url, json=dict(action="drop", paths=["first"]))
    assert blocked.status_code == 400 and "hard link" in blocked.json["error"]
    session.command.assert_not_called()
    assert (
        client.post(url, json=dict(action="drop", paths=["first", "alias"])).status_code
        == 200
    )
    assert session.command.call_count == 1
