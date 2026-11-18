import os
from unittest.mock import Mock

import pytest

from app import create_app
from deck import aider_config as cfg
from deck import model_catalog as catalog


@pytest.fixture
def setup(tmp_path, monkeypatch):
    home, root = tmp_path / "home", tmp_path / "repo"
    home.mkdir()
    root.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for key in list(os.environ):
        if key.startswith(("AIDER_", "OPENAI_", "OLLAMA_", "ANTHROPIC_", "GOOGLE_", "GEMINI_",
                           "GROQ_", "MISTRAL_", "COHERE_", "OPENROUTER_", "DEEPSEEK_")):
            monkeypatch.delenv(key)
    app = create_app(tmp_path / "state.json", home)
    wid = app.extensions["deck"].state.open(root)
    return root, app.test_client(), f"/api/{wid}/config"


def test_catalogs_read_no_cloud_accounts_and_keep_keys_private(setup, monkeypatch):
    root, client, url = setup
    secret = "synthetic-catalog-fixture"
    (root / ".env").write_text("GOOGLE_API_KEY=" + secret + "\n", encoding="utf-8")
    (root / cfg.MODELS).write_text(f"- name: {secret}\n- name: custom/acme\n- name: aider/extra_params\n")
    service = Mock()
    service.status.return_value = {"running": True, "installed": [
        {"name": "local:latest"}, {"name": "hosted:cloud", "remote": True}]}
    monkeypatch.setattr(catalog, "service_at", lambda endpoint: service)
    response = client.get(url + "/model-candidates")
    assert response.status_code == 200 and secret not in response.text
    groups = {g["label"]: g["models"] for g in response.json["groups"]}
    assert groups["Installed Ollama"] == ["ollama_chat/local:latest"]
    assert groups["Workspace models"] == ["custom/acme"]
    assert all(groups[label] for label in ("OpenAI", "Anthropic", "Google Gemini", "Groq",
                                            "DeepSeek", "Mistral", "Cohere", "OpenRouter"))
    warnings = response.json["warnings"]
    assert all(m not in warnings for m in groups["Google Gemini"] + groups["Installed Ollama"])
    assert "OPENAI_API_KEY" in warnings["openai/gpt-5.4"]
    assert "COHERE_API_KEY" in warnings[groups["Cohere"][0]]
    assert len(service.mock_calls) == 1


def test_inline_model_keys_and_aliases_match_the_selected_model(setup):
    root, _, _ = setup
    (root / cfg.CONF).write_text("alias: [tester:openai/test]\napi-key: [GROQ=fixture-key]\n")
    (root / cfg.MODELS).write_text(
        "- name: openai/test\n  extra_params:\n    api_key: os.environ/TEST_MODEL_AUTH\n"
        "- name: anthropic/test\n  extra_params:\n    api_key: fixture-inline\n")
    before = catalog.model_access(root)
    assert before.key_warning("groq/test") == before.key_warning("anthropic/test") == ""
    assert before.key_warning("tester") and before.key_warning("openai/other")
    assert before.key_warning("ollama_chat/qwen3:8b") == ""
    assert "OLLAMA_API_KEY" in before.key_warning("ollama_chat/demo:cloud")
    (root / ".env").write_text("TEST_MODEL_AUTH=fixture-variable\nOLLAMA_API_KEY=fixture-key\n")
    after = catalog.model_access(root)
    assert after.key_warning("tester") == after.key_warning("ollama_chat/demo:cloud") == ""
    assert after.key_warning("openai/other")


def test_model_pages_handle_aliases_compatible_local_routes_and_unknown_models(setup):
    root, client, url = setup
    (root / cfg.CONF).write_text("alias: [bolt:ollama_chat/nemotron-3.5-bolt:e4b]\n")

    def link(model):
        response = client.post(url + "/model-catalog", json={"model": model})
        assert response.status_code == 200
        return response.json["link"]

    assert link("openai/gpt-5.4")["url"].endswith("/models/gpt-5.4")
    assert link("bolt")["url"] == "https://ollama.com/library/nemotron-3.5-bolt"
    assert link("ollama_chat/team/private-model:v5")["url"] == "https://ollama.com/search"
    for name in ("custom/internal-tester", "openai/unknown-private", "https://example.net/"):
        assert link(name) is None
    (root / ".env").write_text("OPENAI_API_BASE=http://127.0.0.1:11434/v1\n")
    assert link("openai/gpt-oss:20b")["url"] == "https://ollama.com/library/gpt-oss"
    assert not catalog.model_access(root).key_warning("openai/gpt-oss:20b")
