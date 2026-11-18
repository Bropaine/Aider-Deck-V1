import json
from unittest.mock import Mock

import pytest

from deck import reasoning_capabilities as effort


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    for names in effort.BASE_VARS.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    return root


@pytest.mark.parametrize(
    ("model", "levels"),
    [
        ("openai/gpt-5.4", ["none", "low", "medium", "high", "xhigh"]),
        ("openai/gpt-5.6-terra", ["none", "low", "medium", "high", "xhigh", "max"]),
        ("gpt-5.2-2025-12-11", ["none", "low", "medium", "high", "xhigh"]),
        ("anthropic/claude-opus-4-6", ["low", "medium", "high", "max"]),
        ("claude-opus-4-7", ["low", "medium", "high", "xhigh", "max"]),
        ("gemini/gemini-3-pro-preview", ["low", "high"]),
        ("gemini/gemini-3-flash-preview", ["minimal", "low", "medium", "high"]),
        ("openai/gpt-4.1", []),
    ],
)
def test_exact_documented_models(workspace, model, levels):
    result = effort.discover(model, effort.routing(workspace, {}))
    assert result["model"] == model
    assert result["resolved_model"] == model
    assert result["status"] == ("known" if levels else "unsupported")
    assert result["levels"] == levels
    assert result["source_url"].startswith("https://")


@pytest.mark.parametrize(
    "model",
    [
        "",
        "openai/gpt-5.4-unknown-date",
        "openrouter/openai/gpt-5.4",
        "azure/gpt-5.4",
        "gemini/future-name",
        "gpt-5.6-terra-tuned",
        "my-deployment",
    ],
)
def test_manual_for_unknown_models_even_if_name_resembles_known_one(workspace, model):
    result = effort.discover(model, effort.routing(workspace, {}))
    assert (result["status"], result["levels"]) == ("unknown", [])


def test_numeric_config_and_conflicting_provider_routes_fall_back(workspace):
    (workspace / ".env").write_text(
        "OPENAI_BASE_URL=https://api.openai.com/v1\nOPENAI_API_BASE=http://localhost:4545\n",
        encoding="utf-8",
    )
    routes = effort.routing(workspace, {})
    assert effort.discover(42, routes)["status"] == "unknown"
    assert effort.discover("openai/gpt-5.4", routes)["status"] == "unknown"


@pytest.mark.parametrize(
    ("api_base", "library", "host"),
    [
        ("http://localhost:11434/v1", "", ""),
        ("http://[::1]:4201/v1/", "http://[::1]:4201", ""),
        ("http://localhost:4202/v1", "", "http://127.0.0.1:4202"),
        ("http://localhost:4203/v1", "http://127.0.0.1:4203", "http://127.0.0.1:4204"),
    ],
)
def test_local_openai_route_checks_exact_tag(workspace, monkeypatch, api_base, library, host):
    monkeypatch.setenv("OPENAI_API_BASE", api_base)
    monkeypatch.setenv("OLLAMA_API_BASE", library)
    monkeypatch.setenv("OLLAMA_HOST", host)
    get = Mock(return_value={"thinking": {"values": ["light", "deep"]}})
    monkeypatch.setattr(effort.OllamaService, "_get", get)
    found = effort.discover("openai/ollama/model:tag", effort.routing(workspace, {}))
    assert (found["status"], found["levels"]) == ("known", ["light", "deep"])
    assert found["source_url"] == effort.OLLAMA_COMPAT_DOC
    assert "accepts these values" in found["message"]
    get.assert_called_once_with("show", {"model": "ollama/model:tag"}, timeout=2)


@pytest.mark.parametrize(
    "api_base",
    [
        "http://localhost:4204/v1",
        "http://localhost/v1",
        "http://[::1]:11434/v1",
        "http://0.0.0.0:11434/v1",
        "http://localhost:11434",
        "http://localhost:11434/proxy/v1",
        "http://localhost:11434/v1/anything",
        "http://localhost:11434/v1?model=x",
        "https://localhost:11434/v1",
        "http://127.0.0.2:11434/v1",
    ],
)
def test_other_openai_gateways_get_manual_entry_without_contact(workspace, monkeypatch, api_base):
    monkeypatch.setenv("OPENAI_BASE_URL", api_base)
    get = Mock()
    monkeypatch.setattr(effort.OllamaService, "_get", get)
    result = effort.discover("openai/gpt-5.4", effort.routing(workspace, {}))
    assert (result["status"], result["source"]) == ("unknown", "Manual entry")
    get.assert_not_called()


def test_local_openai_route_per_model_override_and_unavailable_tag(workspace, monkeypatch):
    monkeypatch.setenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    monkeypatch.setenv("OLLAMA_API_BASE", "http://localhost:4200")
    (workspace / effort.cfg.MODELS).write_text(
        "- name: openai/gpt-5.4\n  extra_params:\n    api_base: http://127.0.0.1:4200/v1\n",
        encoding="utf-8",
    )
    get = Mock(side_effect=effort.OllamaError("Model not installed"))
    monkeypatch.setattr(effort.OllamaService, "_get", get)
    routes = effort.routing(workspace, {})
    result = effort.discover("openai/gpt-5.4", routes)
    assert (result["status"], result["levels"]) == ("unknown", [])
    assert "installed" in result["message"]
    assert effort.discover("openai/gpt-5.2", routes)["source"] == "OpenAI model catalog"
    get.assert_called_once_with("show", {"model": "gpt-5.4:latest"}, timeout=2)


@pytest.mark.parametrize(("values", "status"), [([True, False], "unknown"), ([False], "unsupported")])
def test_local_openai_boolean_models_do_not_invent_named_levels(workspace, monkeypatch, values, status):
    monkeypatch.setenv("OPENAI_API_BASE", "http://localhost:11434/v1")
    monkeypatch.setattr(effort.OllamaService, "_get", Mock(return_value={"thinking": {"values": values}}))
    found = effort.discover("openai/toggle", effort.routing(workspace, {}))
    assert (found["status"], found["levels"]) == (status, [])
    if status == "unknown":
        assert "aliases enable thinking" in found["message"]


def test_resolved_endpoint_and_alias_are_frozen_without_credentials(workspace):
    (workspace / ".env").write_text("OPENAI_BASE_URL=https://proxy.invalid/v1\n", encoding="utf-8")
    options = {"alias": ["primary:openai/gpt-5.4", "openai/gpt-5.4:gemini/gemini-3-pro-preview"]}
    first = effort.routing(workspace, options)
    (workspace / ".env").write_text("OPENAI_BASE_URL=https://api.openai.com:443/v1/\n", encoding="utf-8")
    second = effort.routing(workspace, options)
    assert "proxy.invalid" not in repr(first)
    assert effort.discover("primary", first)["status"] == "unknown"
    selected = effort.discover("primary", second)
    assert selected["resolved_model"] == "openai/gpt-5.4"
    assert selected["levels"] == ["none", "low", "medium", "high", "xhigh"]
    assert effort.discover("openai/gpt-5.4", second, resolve_alias=False)["levels"] == selected["levels"]
    assert effort.discover("openai/gpt-5.4", second)["levels"] == ["low", "high"]


@pytest.mark.parametrize(
    ("base_variable", "model", "base"),
    [
        ("OPENAI_API_BASE", "openai/gpt-5.4", "https://api.openai.com.example.org/v1"),
        ("OPENAI_BASE_URL", "gpt-5.4", "https://api.openai.com:8443/v1"),
        ("ANTHROPIC_BASE_URL", "claude-opus-4-7", "https://example.org/v1"),
        ("GEMINI_API_BASE", "gemini/gemini-3-flash-preview", "http://127.0.0.1:8080/v1"),
    ],
)
def test_custom_base_is_never_given_an_official_catalog(workspace, base_variable, model, base):
    (workspace / ".env").write_text(f"{base_variable}={base}\n", encoding="utf-8")
    assert effort.discover(model, effort.routing(workspace, {}))["status"] == "unknown"


def test_ollama_uses_global_override_above_model_above_environment(workspace, monkeypatch):
    (workspace / ".env").write_text("OLLAMA_API_BASE=http://localhost:3321\n", encoding="utf-8")
    settings = workspace / "custom.yml"
    cfg = {"model-settings-file": str(settings), "alias": ["my-model:ollama_chat/mytag:q4"]}
    text = "- name: ollama_chat/mytag:q4\n  extra_params:\n    api_base: http://localhost:3322\n"
    settings.write_text(text, encoding="utf-8")
    local = Mock(return_value={"thinking": {"values": ["slow", "deep", "slow"]}})
    monkeypatch.setattr(effort.OllamaService, "_get", local)
    model_first = effort.routing(workspace, cfg)
    assert model_first.overrides["ollama_chat/mytag:q4"] == "http://127.0.0.1:3322"
    settings.write_text(
        text + "- name: aider/extra_params\n  extra_params:\n    base_url: http://localhost:3323\n",
        encoding="utf-8",
    )
    global_first = effort.routing(workspace, cfg)
    service = Mock(wraps=effort.OllamaService)
    monkeypatch.setattr(effort, "OllamaService", service)
    assert effort.discover("my-model", model_first)["levels"] == ["slow", "deep"]
    assert effort.discover("my-model", global_first)["levels"] == ["slow", "deep"]
    assert [call.args for call in service.call_args_list] == [
        ("http://127.0.0.1:3322",),
        ("http://127.0.0.1:3323",),
    ]
    local.assert_called_with("show", {"model": "mytag:q4"}, timeout=2)


@pytest.mark.parametrize(
    ("details", "status", "levels"),
    [
        ({"thinking": {"values": ["high", "deep"]}}, "known", ["high", "deep"]),
        ({"thinking": {"values": [False, "low", "high"]}}, "known", ["low", "high"]),
        ({"thinking": {"values": [True, False]}}, "unsupported", []),
        ({"thinking": {"values": [False]}}, "unsupported", []),
        ({"capabilities": ["completion", "thinking"]}, "unknown", []),
        ({"thinking": {"values": []}}, "unknown", []),
        ({"thinking": {"values": ["low", "x\n/model"]}}, "unknown", []),
        ({"thinking": {"values": ["low", 3]}}, "unknown", []),
        ({"thinking": {"values": "low"}}, "unknown", []),
    ],
)
def test_loopback_show_metadata_and_safe_effort_values(workspace, monkeypatch, details, status, levels):
    get = Mock(return_value=details)
    monkeypatch.setattr(effort.OllamaService, "_get", get)
    routes = effort.routing(workspace, {})
    result = effort.discover("ollama/sample", routes)
    assert (result["status"], result["levels"]) == (status, levels)
    get.assert_called_once_with("show", {"model": "sample:latest"}, timeout=2)


def test_unavailable_ollama_with_manual_fallback_no_redirect_or_error_leak(workspace, monkeypatch):
    get = Mock(side_effect=effort.OllamaError("private remote details"))
    monkeypatch.setattr(effort.OllamaService, "_get", get)
    routes = effort.routing(workspace, {})
    result = effort.discover("ollama/test", routes)
    assert result["status"] == "unknown" and "private" not in result["message"]
    get.assert_called_once()
    (workspace / ".env").write_text("OLLAMA_API_BASE=https://remote.example.org\n", encoding="utf-8")
    remote = effort.routing(workspace, {})
    assert effort.discover("ollama/test", remote)["status"] == "unknown"
    assert get.call_count == 1


def test_metadata_override_and_explicit_file_precedence(workspace):
    model = "gateway/deployment"

    def entry(value):
        return {model: {"capabilities": {"effort": value}}}

    (workspace / effort.cfg.METADATA).write_text(json.dumps(entry({"supported": False})), encoding="utf-8")
    custom = workspace / "metadata.json"
    custom.write_text(
        json.dumps(
            entry(
                {
                    "supported": True,
                    "xhigh": {"supported": True},
                    "deep": {"supported": True},
                    "low": {"supported": True},
                    "high": {"supported": False},
                }
            )
        ),
        encoding="utf-8",
    )
    assert effort.discover(model, effort.routing(workspace, {}))["status"] == "unsupported"
    routes = effort.routing(workspace, {"model-metadata-file": str(custom)})
    result = effort.discover(model, routes)
    assert result["source"] == "Workspace model metadata"
    assert result["levels"] == ["low", "xhigh", "deep"]
    custom.write_text(json.dumps({model: {"max_input_tokens": 123}}), encoding="utf-8")
    fresh = effort.routing(workspace, {"model-metadata-file": str(custom)})
    assert effort.discover(model, fresh)["status"] == "unknown"
    assert effort.discover(model, routes) == result


@pytest.mark.parametrize(
    "value",
    [
        {"supported": True},
        {"supported": "yes"},
        {"supported": False, "low": {"supported": True}},
        {"supported": True, "low": True},
        {"supported": True, "bad\nname": {"supported": True}},
        {"supported": True, "low": {"supported": False}},
        [],
    ],
)
def test_invalid_workspace_claims_allow_manual_entry(workspace, value):
    doc = {"openai/gpt-5.4": {"capabilities": {"effort": value}}}
    (workspace / effort.cfg.METADATA).write_text(json.dumps(doc), encoding="utf-8")
    result = effort.discover("openai/gpt-5.4", effort.routing(workspace, {}))
    assert result["source"] == "Workspace model metadata"
    assert (result["status"], result["levels"]) == ("unknown", [])
