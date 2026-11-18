from unittest.mock import Mock

import pytest

from app import create_app
from deck import aider_config as cfg
from deck import reasoning_capabilities as effort
from deck.ollama_service import Offline, OllamaError


LOCAL = "ollama_chat/example:tiny"
COMPAT = "openai/example:tiny"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / cfg.CONF).write_text(f"model: {LOCAL}\n")
    (root / ".env").write_text("OLLAMA_API_BASE=http://127.0.0.1:11436\n"
                                "OPENAI_API_BASE=http://127.0.0.1:11436/v1\n")
    app = create_app(tmp_path / "state.json", tmp_path)
    client = app.test_client()
    deck = app.extensions["deck"]
    wid = deck.state.open(root)
    services = {}

    def service(endpoint):
        if endpoint not in services:
            services[endpoint] = Mock()
            services[endpoint].installed_model.return_value = None
            services[endpoint].job.return_value = None
            services[endpoint].pull.return_value = dict(action="pull", model="example:tiny", state="running")
        return services[endpoint]

    monkeypatch.setattr("deck.model_install.service_at", service)
    return client, root, wid, deck, services


def test_selection_missing_download_is_explicit_and_bound_to_route(setup):
    client, root, wid, deck, services = setup
    url = f"/api/{wid}/ollama/model-install"
    payload = dict(target="launch", model=COMPAT)
    result = client.post(url, json=payload).json
    assert result["status"] == "missing" and result["blocked"] and result["tag"] == "example:tiny"
    service, = services.values()
    service.pull.assert_not_called()
    service.start.assert_not_called()
    assert client.post(url, json={**payload, "action": "pull"}).status_code == 409
    assert client.post(url, json={**payload, "route": result["route"], "action": "pull"}).json["job"]["state"] == "running"
    service.pull.assert_called_once_with("example:tiny", strip_provider=False)
    assert client.post(url, json={**payload, "session": "expired"}).status_code == 409
    dotenv = root / ".env"
    dotenv.write_text(dotenv.read_text().replace("11436", "11438"))
    assert client.post(url, json={**payload, "route": result["route"], "action": "pull"}).status_code == 409
    assert len(services) == 1
    assert client.post(url, json={"model": "anthropic/claude-other"}).json["status"] == "other"
    dotenv.write_text("OPENAI_API_BASE=http://127.0.0.1:9000/v1\n")
    assert client.post(url, json=payload).json["status"] == "other"
    dotenv.write_text("OLLAMA_API_BASE=https://remote.example.invalid\n")
    assert client.post(url, json={"model": LOCAL}).json["status"] == "unmanaged"
    assert len(services) == 1


def test_live_and_launch_guards_use_separate_routes_and_preserve_running_session(setup):
    client, root, wid, deck, services = setup
    base = f"/api/{wid}"
    url = base + "/ollama/model-install"
    session = deck.session(wid)
    session.status = "live"
    session.obs.data["model"] = "openai/current"
    session.command = Mock()
    deck.start = Mock()
    (root / cfg.MODELS).write_text(f'- name: {LOCAL}\n  extra_params:\n    api_base: http://127.0.0.1:11437\n')
    deck.reasoning_routes[wid] = effort.routing(root, cfg.launch_values(root))
    (root / cfg.MODELS).write_text(f'- name: {LOCAL}\n  extra_params:\n    api_base: http://127.0.0.1:11438\n')
    live = client.post(url, json=dict(target="live", model=LOCAL)).json
    launch = client.post(url, json=dict(target="launch")).json
    assert live["endpoint"].endswith(":11437") and launch["endpoint"].endswith(":11438")
    body = dict(key="model", value=LOCAL, live=True, install_route=live["route"])
    assert client.post(base + "/config/setting", json=body).status_code == 409
    assert client.post(base + "/session/restart", json={}).status_code == 409
    assert client.post(base + "/config/setting", json=dict(key="model", value=LOCAL)).status_code == 200
    deck.start.assert_not_called()
    session.command.assert_not_called()
    services[live["endpoint"]].installed_model.return_value = dict(remote=False)
    assert client.post(base + "/config/setting", json={**body, "install_route": launch["route"]}).status_code == 409
    assert client.post(base + "/config/setting", json=body).status_code == 200
    session.command.assert_called_once_with("/model " + LOCAL)
    assert client.post(base + "/session/restart", json={}).status_code == 409
    services[launch["endpoint"]].installed_model.return_value = dict(remote=False)
    assert client.post(base + "/session/restart", json={"install_route": launch["route"]}).status_code == 200
    deck.start.assert_called_once_with(wid, True)


def test_offline_unavailable_cloud_and_alias_states(setup):
    client, root, wid, deck, services = setup
    url = f"/api/{wid}/ollama/model-install"
    (root / cfg.CONF).write_text(f'model: local\nalias:\n- local:{LOCAL}\n')
    result = client.post(url, json={}).json
    assert result["model"] == "local" and result["tag"] == "example:tiny"
    service, = services.values()
    service.installed_model.side_effect = Offline("offline")
    result = client.post(url, json={}).json
    assert result["status"] == "offline" and result["blocked"]
    assert client.post(url, json={"action": "pull", "route": result["route"]}).status_code == 400
    service.installed_model.side_effect = OllamaError("api unavailable")
    assert client.post(url, json={}).json["status"] == "unavailable"
    service.installed_model.side_effect = None
    service.installed_model.return_value = {"remote": True}
    assert client.post(url, json={"action": "start", "route": result["route"]}).json["status"] == "cloud"
    service.start.assert_called_once()
    assert client.post(url, json={"action": "pull", "route": result["route"]}).status_code == 400
    service.pull.assert_not_called()
