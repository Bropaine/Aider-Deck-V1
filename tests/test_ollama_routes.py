from unittest.mock import Mock

import pytest

from app import create_app
from deck import aider_config as cfg
from deck import ollama_routes
from deck.sessions import AiderSession
from deck.state import State


def test_context_uses_live_model_and_its_launch_endpoint(desk, monkeypatch):
    client, url, repo, service, deck = desk
    model = "ollama_chat/demo:latest"
    (repo / ".env").write_text("OLLAMA_API_BASE=http://127.0.0.1:28440\n")
    (repo / cfg.MODELS).write_text(
        "- name: ollama/another\n  extra_params:\n    api_base: https://example.org\n"
    )
    wid = url.split("/")[2]
    session = deck.session(wid)
    monkeypatch.setattr(session, "start", Mock())
    deck.start(wid, restart=False)
    (repo / ".env").write_text("OLLAMA_API_BASE=http://127.0.0.1:29999\n")
    session.status = "live"
    service.context_window.return_value = 4096
    state = "/api/" + wid + "/state"
    session.obs.data["model"] = model
    assert client.get(state).json["local_context"] == {"model": model, "window": 4096}
    assert set(client.application.extensions["ollama_services"]) == {"http://127.0.0.1:28440"}
    service.context_window.assert_called_once_with("demo:latest")
    session.obs.data["model"] = "ollama/another"
    assert client.get(state).json["local_context"] == {"model": "ollama/another", "window": None}
    session.obs.data["model"] = "openai/hosted"
    assert client.get(state).json["local_context"] is None
    service.context_window.assert_called_once()


def test_local_openai_context_requires_matching_ollama_route_and_uses_tag_metadata(desk, monkeypatch):
    client, url, repo, service, deck = desk
    wid = url.split("/")[2]
    session = deck.session(wid)
    monkeypatch.setattr(session, "start", Mock())
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    (repo / ".env").write_text(
        "OLLAMA_API_BASE=http://127.0.0.1:29111\nOPENAI_API_BASE=http://localhost:29111/v1\n"
    )
    (repo / cfg.MODELS).write_text(
        "- name: openai/other\n  extra_params:\n    api_base: http://localhost:29112/v1\n"
    )
    deck.start(wid, restart=False)
    session.status = "live"
    session.obs.data["model"] = "openai/demo:small"
    service.context_window.return_value = None
    service.model_context_limit.return_value = {"model_capacity": 65536}
    state = "/api/" + wid + "/state"
    assert client.get(state).json["local_context"] == {
        "model": "openai/demo:small", "window": None, "model_capacity": 65536
    }
    service.model_context_limit.assert_called_once_with("demo:small")
    session.obs.data["model"] = "openai/other"
    assert client.get(state).json["local_context"] is None
    assert set(client.application.extensions["ollama_services"]) == {"http://127.0.0.1:29111"}


@pytest.fixture
def desk(tmp_path, monkeypatch):
    repo, home = tmp_path / "repo", tmp_path / "home"
    repo.mkdir()
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    for name in (
        "AIDER_MODEL",
        "AIDER_WEAK_MODEL",
        "AIDER_EDITOR_MODEL",
        "AIDER_ENV_FILE",
        "OLLAMA_API_BASE",
        "OLLAMA_HOST",
    ):
        monkeypatch.delenv(name, raising=False)
    app = create_app(tmp_path / "state.json", home)
    deck = app.extensions["deck"]
    wid = deck.state.open(repo)
    service = Mock(endpoint="http://127.0.0.1:11434")
    service.status.return_value = dict(
        endpoint=service.endpoint, running=False, installed=[], loaded=[], job=None
    )
    service.start.return_value = {**service.status.return_value, "running": True}
    service.load.return_value = dict(id="load", state="running")
    service.wait.return_value = dict(
        id="load", state="succeeded", message="Model loaded"
    )
    service.pull.return_value = dict(id="pull", state="running", action="pull")
    monkeypatch.setattr(ollama_routes, "OllamaService", lambda endpoint: service)
    yield app.test_client(), "/api/" + wid + "/ollama", repo, service, deck
    deck.close()


def test_saved_names_are_persistent_scoped_and_independent_of_service(desk, tmp_path):
    client, url, repo, service, deck = desk
    assert client.post(url + "/tags", json={"tag": "demo"}).json["saved"] == [
        "demo:latest"
    ]
    assert client.post(url + "/tags", json={"tag": "ollama_chat/demo:latest"}).json[
        "saved"
    ] == ["demo:latest"]
    assert client.post(url + "/tags", json={"tag": "other:small"}).json["saved"] == [
        "demo:latest",
        "other:small",
    ]
    assert client.post(url + "/tags", json={"tag": "bad tag"}).status_code == 400
    restored = State(deck.state.file, tmp_path)
    assert restored.ollama_tags(url.split("/")[2]) == ["demo:latest", "other:small"]
    assert deck.state.ollama_tags(deck.state.open(tmp_path)) == []
    assert client.delete(url + "/tags", json={"tag": "demo"}).json["saved"] == [
        "other:small"
    ]
    assert not service.mock_calls and list(repo.iterdir()) == []


def test_service_controls_are_explicit_and_scope_the_endpoint(desk):
    client, url, repo, service, deck = desk
    assert not client.get(url).json["running"]
    service.start.assert_not_called()
    assert client.post(url + "/start", json={}).json["running"]
    response = client.post(url + "/pull", json={"tag": "demo"})
    assert response.status_code == 202 and response.json["id"] == "pull"
    service.pull.assert_called_once_with("demo:latest")
    assert len(client.application.extensions["ollama_services"]) == 1
    (repo / ".env").write_text("OLLAMA_API_BASE=https://example.org\n")
    assert client.get(url).status_code == 400


def test_one_click_activation_sets_all_three_roles_and_keeps_existing_config(
    desk, monkeypatch
):
    client, url, repo, service, deck = desk
    (repo / ".env").write_text(
        "OPENAI_API_KEY=synthetic-test-value\nAIDER_MODEL=other\n"
    )
    (repo / cfg.CONF).write_text("# keep this setting\nstream: false\n")
    start = Mock()
    monkeypatch.setattr(AiderSession, "start", start)
    announce = Mock()
    monkeypatch.setattr(AiderSession, "wait_for_model_or_question", announce)
    response = client.post(url + "/use", json={"tag": "demo"})
    model = "ollama_chat/demo:latest"
    assert response.status_code == 200 and response.json["model"] == model
    announce.assert_called_once_with(model)
    service.load.assert_called_once_with("demo:latest")
    assert "synthetic-test-value" not in response.text
    options, env, _ = start.call_args.args
    assert all(options[key] == model for key in ollama_routes.ROLES)
    assert (
        env["OPENAI_API_KEY"] == "synthetic-test-value"
        and env["OLLAMA_API_BASE"] == service.endpoint
    )
    assert (
        "# keep this setting" in (repo / cfg.CONF).read_text()
        and options["stream"] is False
    )


def test_failed_load_and_failed_spawn_restore_originals(desk, monkeypatch):
    client, url, repo, service, deck = desk
    config = repo / cfg.CONF
    initial = b"\xef\xbb\xbf# keep bytes\r\nmodel: before\r\n"
    config.write_bytes(initial)
    service.wait.return_value = dict(
        state="failed", error="not available", message="not available"
    )
    response = client.post(url + "/use", json={"tag": "missing"})
    assert response.status_code == 400 and "not available" in response.json["error"]
    assert config.read_bytes() == initial and not (repo / ".env").exists()
    service.wait.return_value = dict(state="succeeded")
    monkeypatch.setattr(
        AiderSession, "start", Mock(side_effect=ValueError("spawn failed"))
    )
    response = client.post(url + "/use", json={"tag": "demo"})
    assert response.status_code == 400 and config.read_bytes() == initial
    assert not (repo / ".env").exists()


def test_activation_rechecks_settings_before_writing(desk, monkeypatch):
    client, url, repo, service, deck = desk

    def owner_edit(*args, **kwargs):
        (repo / cfg.CONF).write_text("model: changed-during-warmup\n")
        return {"state": "succeeded"}

    service.wait.side_effect = owner_edit
    assert client.post(url + "/use", json={"tag": "demo"}).status_code == 409
    assert cfg.launch_values(repo)["model"] == "changed-during-warmup"
    assert not (repo / ".env").exists()


def test_confirmed_restart_stages_new_session_before_replacing_live_one(
    desk, monkeypatch
):
    client, url, repo, service, deck = desk
    wid = url.split("/")[2]
    old = deck.session(wid)
    old.status = "live"
    close_old = Mock()
    monkeypatch.setattr(old, "close", close_old)
    assert client.post(url + "/use", json={"tag": "demo"}).status_code == 409
    service.load.assert_not_called()
    start = Mock(side_effect=ValueError("Could not start Aider"))
    monkeypatch.setattr(AiderSession, "start", start)
    body = dict(tag="demo", restart_confirmed=True)
    assert client.post(url + "/use", json=body).status_code == 400
    close_old.assert_not_called()
    assert deck.session(wid) is old
    start.side_effect = None
    announce = Mock(side_effect=ValueError("Aider exited before announcing the model"))
    monkeypatch.setattr(AiderSession, "wait_for_model_or_question", announce)
    assert client.post(url + "/use", json=body).status_code == 400
    close_old.assert_not_called()
    announce.side_effect = None
    assert client.post(url + "/use", json=body).status_code == 200
    close_old.assert_called_once()
    assert deck.session(wid) is not old


def test_observed_local_startup_requires_the_selected_model_or_a_question(tmp_path):
    session = AiderSession(tmp_path)
    session.status = "live"
    model = "ollama_chat/example:latest"
    with pytest.raises(ValueError, match="in time"):
        session.wait_for_model_or_question(model, timeout=0)
    session.obs.question = "Add .env to .gitignore?"
    session.wait_for_model_or_question(model, timeout=0)
    session.obs.question = None
    session.obs.data["model"] = "other"
    with pytest.raises(ValueError, match="instead"):
        session.wait_for_model_or_question(model, timeout=0)
    session.obs.data["model"] = model
    session.wait_for_model_or_question(model, timeout=0)
    session.status = "exited"
    with pytest.raises(ValueError, match="exited"):
        session.wait_for_model_or_question(model, timeout=0)
