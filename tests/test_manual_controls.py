"""Drive the controls in the README through Flask on disposable workspaces."""

import os

import pytest

from app import create_app
from deck import aider_config
from deck.envfile import KEY_NAMES
from deck.registry import REGISTRY
from deck.sessions import build_launch_args


def alternate_value(row):
    """A value distinct from each setting's inherited value in an empty workspace."""
    if row["key"].endswith("api-key"):
        return "synthetic-manual-key-7194"
    if row["type"] == "toggle":
        return not bool(row["configured"])
    if row["type"] == "select":
        return next(v for v in row["choices"] if v != row["configured"])
    if row["type"] == "number":
        return 21
    if row["type"] == "list":
        return ["a:b", "c:d"]
    if row["type"] == "color":
        return "#3488ba"
    return "deck-manual-fixture"


@pytest.fixture
def desk(tmp_path, monkeypatch):
    home, project, library = (tmp_path / n for n in ("home", "project", "library"))
    for folder in (home, project, library):
        folder.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    for key in list(os.environ):
        if key.startswith("AIDER_"):
            monkeypatch.delenv(key)
    app = create_app(tmp_path / "state.json", library)
    client = app.test_client()
    opened = client.post("/api/workspaces", json={"path": str(project)})
    assert opened.status_code == 200
    return client, "/api/" + opened.json["id"], project


@pytest.mark.parametrize("key", sorted(REGISTRY))
def test_each_setting_control(desk, key):
    client, base, project = desk
    initial = client.get(base + "/config").json
    row = next(r for r in initial["rows"] if r["key"] == key)
    assert row["flag"] and row["env"] and row["group"] and row["label"]
    endpoint = base + "/config/setting"
    if key == "check-update":
        assert (
            client.post(endpoint, json={"key": key, "value": False}).status_code == 400
        )
        return
    chosen = alternate_value(row)
    saved = client.post(endpoint, json={"key": key, "value": chosen})
    assert saved.status_code == 200, saved.json
    conf = project / ".aider.conf.yml"
    assert (
        aider_config.parse(conf.read_text(encoding="utf-8"), conf.name)[key] == chosen
    )
    again = client.get(base + "/config")
    assert again.status_code == 200
    result = next(r for r in again.json["rows"] if r["key"] == key)
    if key.endswith("api-key"):
        assert chosen not in again.text
    else:
        assert result["configured"] == chosen
        assert result["source"] in ("Cwd config", "Repo config")
        assert (
            client.post(endpoint, json={"key": key, "value": None}).status_code == 200
        )
        assert key not in aider_config.parse(
            conf.read_text(encoding="utf-8"), conf.name
        )


def test_local_ollama_workspace_from_ui_settings(desk, monkeypatch):
    client, base, root = desk
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    model = "ollama_chat/example:e4b"
    endpoint = "http://127.0.0.1:11435"
    assert client.post(
        base + "/config/keys", json={"values": {"OLLAMA_API_BASE": endpoint}}
    ).status_code == 200
    assert client.post(
        base + "/config/setting", json={"key": "model", "value": model}
    ).status_code == 200
    options = aider_config.launch_values(root)
    argv = build_launch_args(options)
    assert argv[argv.index("--model") + 1] == model
    environment, _ = aider_config.launch_environment(root, architect=False)
    assert environment["OLLAMA_API_BASE"] == endpoint
    assert "OPENAI_API_KEY" not in environment


def test_saved_environment_fields(desk):
    client, base, root = desk
    keys = (*KEY_NAMES, "CUSTOM_SERVICE_KEY")
    values = {key: "test-value-" + str(i) + "-1123" for i, key in enumerate(keys)}
    api = base + "/config/keys"
    assert client.post(api, json={"values": values}).status_code == 200
    listed = client.get(api)
    rows = {r["name"]: r for r in listed.json["keys"]}
    for key, value in values.items():
        assert value not in listed.text
        assert rows[key]["present"] and rows[key]["masked"].endswith("1123")
        assert client.post(api + "/verify", json={"name": key}).json["present"]
    assert client.post(api, json={"values": {"OPENAI_API_KEY": ""}}).status_code == 200
    assert client.post(api + "/verify", json={"name": "OPENAI_API_KEY"}).json["present"]
    assert (
        client.post(api + "/verify", json={"name": "NOT_SET"}).json["present"] is False
    )


def test_prompts_switcher_and_tree_paths(desk, tmp_path):
    client, base, root = desk
    browse = client.get("/api/browse", query_string={"path": str(tmp_path)}).json
    assert str(root) in {d["path"] for d in browse["dirs"]}
    folder = tmp_path / "new prompts"
    folder.mkdir()
    assert (
        client.post(
            base + "/prefs", json={"prompt_library": str(folder), "enter_sends": False}
        ).status_code
        == 200
    )
    assert client.get(base + "/state").json["prefs"]["enter_sends"] is False
    item = base + "/prompts/item"
    name = "example.md"
    text = "Inspect\n{\n  sample\n}"
    assert client.post(item, json={"name": name, "text": text}).status_code == 200
    list_ = client.get(base + "/prompts").json
    assert list_["prompts"][0]["preview"] == "Inspect" and list_["folder"] == str(
        folder
    )
    assert client.get(item, query_string={"name": name}).json["text"] == text
    assert (
        client.post(item, json={"name": name, "text": "replacement"}).status_code == 409
    )
    assert (
        client.post(
            item, json={"name": name, "text": "replacement", "overwrite": True}
        ).status_code
        == 200
    )
    assert client.delete(item, json={"name": name}).status_code == 400
    assert (
        client.delete(item, json={"name": name, "confirmed": True}).status_code == 200
    )
    nested = root / "nested"
    nested.mkdir()
    (nested / "keep.txt").touch()
    (nested / "ignore.txt").touch()
    path = base + "/config/raw/.aiderignore"
    view = client.get(path).json
    assert (
        client.put(
            path, json={"text": "**/ignore.txt\n", "revision": view["revision"]}
        ).status_code
        == 200
    )
    assert [
        f["name"] for f in client.get(base + "/tree?path=nested").json["entries"]
    ] == ["keep.txt"]
    assert {
        f["name"]: f["ignored"]
        for f in client.get(base + "/tree?path=nested&ignored=1").json["entries"]
    } == {"keep.txt": False, "ignore.txt": True}
    assert client.get(base + "/tree?path=..").status_code == 400
    assert client.post("/api/workspaces", json={"path": str(nested)}).status_code == 200
    assert len(client.get("/api/workspaces").json["workspaces"]) == 2
    assert client.get(base + "/state").json["prefs"]["prompt_library"] == str(folder)
