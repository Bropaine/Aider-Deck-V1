import base64
import os
import subprocess
import textwrap
from unittest.mock import Mock

import pytest

from app import create_app
from deck.attachment_validation import MAX_FILES, MAX_TOTAL_BYTES
from deck.sessions import AiderSession


@pytest.fixture
def live(tmp_path):
    root = tmp_path / "workspace"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    session = AiderSession(root)
    session.status, session.proc = "live", Mock()
    yield session
    session.attachments.close()


def listing(session, raw):
    session.obs.begin("files")
    for line in raw.splitlines():
        session.obs.line(line)
    session.obs.finish()


def dispatch(session):
    session.obs.ready = True
    session._dispatch()
    return session.proc.write.call_args.args[0].rstrip("\r")


def test_readonly_copy_and_detach_wait_for_native_file_list(live):
    name = "meeting notes [1].md"
    rows = live.attach(live.attachments.generation, [(name, b"first"), (name, b"second")])
    first, second = live.attachments.items.values()
    assert first.path.name == second.path.name == name and first.path != second.path
    assert first.path.read_bytes() == b"first" and second.path.read_bytes() == b"second"
    assert not first.path.is_relative_to(live.root)
    assert [p.name for p in live.root.iterdir()] == [".git"]
    assert [r["status"] for r in rows] == ["attaching", "attaching"]
    # A stale /ls from before Aider received our command cannot discard new files.
    listing(live, "No files in chat, git repo, or read-only list.")
    assert all(i.status == "attaching" for i in (first, second))
    with pytest.raises(ValueError, match="before sending"):
        live.send("first question")
    assert dispatch(live) == f'/read-only "{first.path}" "{second.path}"'
    assert dispatch(live) == "/ls"
    names = []
    for item in (first, second):
        try:
            native = os.path.relpath(item.path, live.root)
        except ValueError:
            native = str(item.path)
        names.append("\n".join(textwrap.wrap("  " + native, 26)))
    listing(live, "Read-only files:\n" + "\n".join(names) + "\nFiles in chat:\n  main.py")
    assert [(r["name"], r["status"]) for r in live.snapshot()["attachments"]] == [(name, "attached")] * 2
    assert live.snapshot()["files"]["data"] == [dict(path="main.py", read_only=False)]
    live.send("first question")
    assert dispatch(live) == "first question"
    assert live.remove_attachment(live.attachments.generation, first.id)[0]["status"] == "removing"
    assert first.path.exists() and second.path.exists()
    assert dispatch(live) == f'/drop "{first.path.parent.as_posix()}/./{name}"'
    assert dispatch(live) == "/ls"
    listing(live, "Read-only files:\n" + names[1])
    assert not first.path.parent.exists() and second.path.exists()
    assert [r["id"] for r in live.snapshot()["attachments"]] == [second.id]
    # A native /drop or /reset also releases the file outside the workspace.
    listing(live, "No files in chat, git repo, or read-only list.")
    assert live.snapshot()["attachments"] == [] and not second.path.parent.exists()


def test_rejected_image_and_incomplete_listing_surface_failure(live):
    generation = live.attachments.generation
    live.attach(generation, [("Shot.PNG", b"fixture")])
    item, = live.attachments.items.values()
    assert item.path.name == "Shot.png" and item.name == "Shot.PNG"
    dispatch(live)
    dispatch(live)
    listing(live, "incomplete report")
    assert item.status == "failed" and item.path.exists()
    with pytest.raises(ValueError, match="before sending"):
        live.send("/ask check")
    listing(live, "No files in chat, git repo, or read-only list.")
    assert "image support" in item.error and not item.path.exists()
    assert live.remove_attachment(generation, item.id) == []
    assert live.remove_attachment(generation, item.id) == []


def test_cancel_busy_add_and_close_clean_up(live):
    generation = live.attachments.generation
    live.attach(generation, [("a.py", b"1")])
    first, = live.attachments.items.values()
    live.write("\x03")
    assert list(live.pending) == ["/ls"]
    assert dispatch(live) == "/ls"
    listing(live, "No files in chat, git repo, or read-only list.")
    assert first.status == "failed" and not first.path.exists()
    live.remove_attachment(generation, first.id)
    live.attach(generation, [("a.py", b"2")])
    second, = live.attachments.items.values()
    live.proc.isalive.return_value = False
    live.close()
    assert live.snapshot()["attachments"] == [] and not second.path.parent.exists()
    assert live.attachments.generation != generation


def test_queue_failure_rolls_back_and_limits_count_existing(live):
    generation = live.attachments.generation
    live.pending.extend(["/settings"] * 32)
    with pytest.raises(ValueError, match="queue is full"):
        live.attach(generation, [("one.md", b"1")])
    assert not live.attachments.items
    live.pending.clear()
    live.attach(generation, [(f"{i}.md", b"x") for i in range(MAX_FILES - 1)])
    with pytest.raises(ValueError, match="per session"):
        live.attach(generation, [("one.md", b"x"), ("two.md", b"x")])
    with pytest.raises(ValueError, match="session limit"):
        live.attach(generation, [("one.md", b"x" * MAX_TOTAL_BYTES)])


def test_remove_during_queued_upload_and_reply(live):
    generation = live.attachments.generation
    live.attach(generation, [("one.md", b"x")])
    first, = live.attachments.items.values()
    live.remove_attachment(generation, first.id)
    assert dispatch(live).startswith("/read-only ")
    assert dispatch(live) == "/ls"
    listing(live, f"Read-only files:\n  {first.path}")
    assert first.path.exists() and first.status == "removing"
    assert dispatch(live).startswith("/drop ")
    assert dispatch(live) == "/ls"
    listing(live, "No files in chat, git repo, or read-only list.")
    assert not first.path.parent.exists()
    live.attach(generation, [("two.md", b"y")])
    second, = live.attachments.items.values()
    assert dispatch(live).startswith("/read-only ")
    assert dispatch(live) == "/ls"
    listing(live, f"Read-only files:\n  {second.path}")
    live.send("describe two.md")
    live.remove_attachment(generation, second.id)
    assert dispatch(live) == "describe two.md" and second.path.exists()
    assert dispatch(live).startswith("/drop ")
    assert second.path.exists()
    assert dispatch(live) == "/ls"
    listing(live, "No files in chat, git repo, or read-only list.")
    assert not second.path.parent.exists()


def test_temp_directory_must_be_outside_containing_git_repository(live, monkeypatch):
    monkeypatch.setattr("deck.attachments.git.repository", lambda root: root.parent)
    trap = live.root.parent / "temp"
    trap.mkdir()
    monkeypatch.setattr("deck.attachments.tempfile.gettempdir", lambda: str(trap))
    for key in ("TMPDIR", "TEMP", "TMP", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(trap))
    with pytest.raises(ValueError, match="outside"):
        live.attach(live.attachments.generation, [("one.md", b"x")])
    assert not list(trap.iterdir()) and not live.attachments.items


def test_alternate_system_temp_outside_workspace_is_used(live, monkeypatch):
    monkeypatch.setattr("deck.attachments.tempfile.gettempdir", lambda: str(live.root))
    monkeypatch.setenv("TMPDIR", str(live.root.parent))
    live.attach(live.attachments.generation, [("reference.md", b"test")])
    item, = live.attachments.items.values()
    assert item.path.parent.parent == live.root.parent and not item.path.is_relative_to(live.root)


def test_http_generation_origin_and_workspace_isolation(tmp_path):
    client = create_app(tmp_path / "state.json", tmp_path).test_client()
    workspaces = [tmp_path / "one", tmp_path / "two"]
    urls = []
    sessions = []
    for root in workspaces:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        wid = client.post("/api/workspaces", json={"path": str(root)}).json["id"]
        urls.append(f"/api/{wid}/attachments")
        sessions.append(client.application.extensions["deck"].session(wid))
    first, second = sessions
    encoded = base64.b64encode(b"hello").decode()
    body = {"session": first.attachments.generation, "files": [{"name": "hello.txt", "data": encoded}]}
    assert client.post(urls[0], json=body).status_code == 400
    first.status, first.proc = "live", Mock()
    first.obs.ready = True
    first.obs.data.update(model="example", tokens={"stale": False, "data": {"model": "example", "max": 8192, "used": 100}})
    second.status, second.proc = "live", Mock()
    try:
        assert client.post(urls[0], json=body, headers={"Origin": "https://example.com"}).status_code == 403
        assert client.post(urls[0], data=body).status_code == 415
        assert client.post(urls[1], json=body).status_code == 400
        response = client.post(urls[0], json=body)
        assert response.status_code == 200, response.json
        row, = response.json["attachments"]
        assert row["name"] == "hello.txt" and row["status"] == "attaching"
        send = urls[0].removesuffix("attachments") + "send"
        stale = dict(text="keep my draft", attachment_session=body["session"], attachment_ids=[])
        assert "attachments changed" in client.post(send, json=stale).json["error"]
        stale.update(attachment_session=second.attachments.generation, attachment_ids=[row["id"]])
        assert "session changed" in client.post(send, json=stale).json["error"]
        endpoint = urls[0] + "/" + row["id"] + "/remove"
        assert client.post(endpoint, json={"session": second.attachments.generation}).status_code == 400
        assert client.post(endpoint, json={"session": body["session"]}).json["attachments"][0]["status"] == "removing"
        assert [p.name for p in workspaces[0].iterdir()] == [".git"] and not second.attachments.items
        assert "/read-only" not in (tmp_path / "state.json").read_text()
    finally:
        for session in sessions:
            session.proc.isalive.return_value = False
            session.close()
