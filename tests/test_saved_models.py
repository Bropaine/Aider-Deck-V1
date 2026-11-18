import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import create_app
from deck import aider_config
from deck.state import State


@pytest.fixture
def picker(tmp_path, monkeypatch):
    root, home = tmp_path / "repo", tmp_path / "home"
    root.mkdir()
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("AIDER_MODEL", raising=False)
    (root / ".env").write_text("OPENAI_API_KEY=test-value-for-picker-only\n", encoding="utf-8")
    (root / aider_config.CONF).write_text("model: openai/main\n", encoding="utf-8")
    state = tmp_path / "deck-state.json"
    app = create_app(state, tmp_path)
    client = app.test_client()
    wid = app.extensions["deck"].state.open(root)
    return client, "/api/" + wid + "/config", root, app.extensions["deck"], state


def visible(response):
    assert response.status_code == 200, response.json
    return {item["name"]: item["enabled"] for item in response.json["saved_models"]}


def test_legacy_launch_model_and_independent_save_toggle_remove(picker):
    client, url, root, deck, state = picker
    assert visible(client.get(url)) == {"openai/main": True}
    previous = (root / aider_config.CONF).read_bytes()
    local = "ollama_chat/owner/model:8b"
    def edit(action, model, **extra):
        return client.post(url + "/saved-models", json=dict(action=action, model=model, **extra))
    assert visible(edit("save", local)) == {"openai/main": True, local: True}
    assert visible(edit("visibility", local, enabled=False))[local] is False
    assert visible(edit("save", local))[local] is False
    assert visible(edit("visibility", local, enabled=True))[local] is True
    assert visible(edit("remove", "openai/main")) == {local: True}
    assert visible(client.get(url)) == {local: True}
    assert (root / aider_config.CONF).read_bytes() == previous
    reopened = create_app(state, root).test_client()
    assert visible(reopened.get(url + "/saved-models")) == {local: True}
    wid = deck.state.open(root.parent)
    assert visible(client.get(f"/api/{wid}/config")) == {}


def test_successive_launch_saves_remember_names_and_clear_independently(picker):
    client, url, root, _, _ = picker
    for model in ("openai/other", "ollama_chat/coder:latest", None):
        assert client.post(url + "/setting", json={"key": "model", "value": model}).status_code == 200
        assert aider_config.launch_values(root).get("model") == model
    assert visible(client.get(url)) == {
        "openai/main": True, "openai/other": True, "ollama_chat/coder:latest": True,
    }


def test_key_gate_can_save_off_require_key_to_enable_and_refresh(picker, monkeypatch):
    client, url, root, _, _ = picker
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    model = "anthropic/claude-sonnet-4-6"
    def action(verb, **extra):
        return client.post(url + "/saved-models", json=dict(action=verb, model=model, **extra))
    saved = action("save")
    assert visible(saved)[model] is False
    assert "ANTHROPIC_API_KEY" in saved.json["saved_models"][-1]["key_warning"]
    assert action("visibility", enabled=True).status_code == 400
    assert client.post(url + "/setting", json={"key": "model", "value": model}).status_code == 200
    assert aider_config.launch_values(root)["model"] == model
    assert visible(client.get(url))[model] is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-picker-key")
    assert visible(client.get(url))[model] is False
    assert visible(action("visibility", enabled=True))[model] is True
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert visible(client.get(url))[model] is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-picker-key")
    assert visible(client.get(url))[model] is True


def test_invalid_picker_writes_do_not_store_credentials_or_invalid_ids(picker):
    client, url, root, _, state = picker
    before = state.read_bytes()
    for bad in ["", 41, [], "x\n/help", "-flags", "https://user:placeholder@example.com/v1",
                "openai/test-value-for-picker-only", "openai/sk-" + "aB9" * 12]:
        result = client.post(url + "/saved-models", json={"action": "save", "model": bad})
        assert result.status_code == 400
        if isinstance(bad, str) and len(bad) > 6:
            assert bad not in result.get_data(as_text=True)
    for enabled in (0, "false", None):
        result = client.post(url + "/saved-models", json={"action": "visibility", "model": "openai/x", "enabled": enabled})
        assert result.status_code == 400
    assert state.read_bytes() == before


def test_model_search_accepts_private_request_body_and_rejects_accidental_key(picker, monkeypatch):
    client, url, _, _, _ = picker
    calls = []
    def list_models(root, args):
        calls.append(args)
        return 0, "- openai/test-model"
    monkeypatch.setattr("deck.config_routes.aider_info", list_models)
    assert client.post(url + "/models", json={"q": "test-model"}).json == {"models": ["openai/test-model"]}
    assert client.get(url + "/models", query_string={"q": "test-model"}).status_code == 200
    key = "test-value-for-picker-only"
    response = client.post(url + "/models", json={"q": "openai/" + key})
    assert response.status_code == 400 and key not in response.get_data(as_text=True)
    assert calls == [["--list-models", "test-model"]] * 2


def test_save_failure_rolls_back_memory_and_launch_file(picker, monkeypatch):
    client, url, root, deck, state = picker
    config = root / aider_config.CONF
    config.write_bytes(b"\xef\xbb\xbfmodel: openai/main\r\n")
    original, before = config.read_bytes(), state.read_bytes()
    def fail():
        raise OSError("disk full")
    monkeypatch.setattr(deck.state, "_save", fail)
    assert client.post(url + "/saved-models", json={"action": "remove", "model": "openai/main"}).status_code == 400
    assert client.post(url + "/setting", json={"key": "model", "value": "openai/new"}).status_code == 400
    assert config.read_bytes() == original and state.read_bytes() == before
    assert visible(client.get(url)) == {"openai/main": True}


def test_concurrent_individual_bookmarks_are_all_saved(tmp_path):
    store = State(tmp_path / "state.json", tmp_path)
    wid = store.open(tmp_path)
    ids = [f"ollama_chat/model:{i}" for i in range(20)]
    with ThreadPoolExecutor(max_workers=5) as threads:
        list(threads.map(lambda name: store.saved_models(wid, {}, "save", name, True), ids))
    assert set(json.loads(store.file.read_text())["workspaces"][wid]["saved_models"]) == set(ids)
