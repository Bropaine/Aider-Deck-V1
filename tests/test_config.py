import json
import subprocess
import threading
from pathlib import Path

import pytest
from flask import Flask

from deck import aider_config as cfg
from deck.config_routes import config_bp
from deck.registry import REGISTRY
from deck.state import State


@pytest.fixture()
def space(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = tmp_path / "repo"
    home.mkdir()
    root.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def test_completeness():
    expected = """
    model model-settings-file model-metadata-file alias reasoning-effort thinking-tokens
    verify-ssl timeout edit-format architect auto-accept-architect weak-model editor-model
    editor-edit-format show-model-warnings check-model-accepts-settings max-chat-history-tokens
    cache-prompts cache-keepalive-pings map-tokens map-refresh map-multiplier-no-files
    input-history-file chat-history-file restore-chat-history llm-history-file dark-mode
    light-mode pretty stream code-theme show-diffs user-input-color tool-output-color
    tool-error-color tool-warning-color assistant-output-color completion-menu-color
    completion-menu-bg-color completion-menu-current-color completion-menu-current-bg-color
    git gitignore add-gitignore-files aiderignore subtree-only auto-commits dirty-commits
    attribute-author attribute-committer attribute-commit-message-author
    attribute-commit-message-committer attribute-co-authored-by git-commit-verify commit-prompt
    dry-run skip-sanity-check-repo watch-files lint-cmd auto-lint test-cmd auto-test analytics
    analytics-log analytics-disable analytics-posthog-host analytics-posthog-project-api-key
    chat-language commit-language vim fancy-input multiline suggest-shell-commands detect-urls
    notifications notifications-command encoding line-endings editor check-update
    """.split()
    assert set(expected) <= REGISTRY.keys()
    for key in expected:
        row = REGISTRY[key]
        assert row["key"] == key and row["flag"] == "--" + key
        assert row["type"] in {"text", "toggle", "number", "list", "color", "select"}
        assert {"default", "description", "group", "label", "env", "live"} <= row.keys()


def test_comments_survive_write_and_remove():
    text = '# First\nmodel: "old" # keep this\n# Next\nunknown-option: keep  # trailing\n'
    edited = cfg.update_yaml(text, {"model": "new"})
    assert 'model: "new"' in edited
    removed = cfg.update_yaml(edited, {"model": None}, remove_defaults=True)
    assert cfg.parse(removed, cfg.CONF) == {"unknown-option": "keep"}
    for part in ["# First", "# keep this", "# Next", "# trailing", "unknown-option: keep"]:
        assert part in edited and part in removed


def test_config_and_env_chain(space, monkeypatch):
    home = space.parent / "home"
    cwd = space / "child"
    cwd.mkdir()
    (home / cfg.CONF).write_text("model: base\nmap-tokens: 40\n")
    (space / cfg.CONF).write_text("model: repo\n")
    (cwd / cfg.CONF).write_text("model: child\n")
    (space / ".env").write_text("AIDER_TIMEOUT=1\nAIDER_MODEL=dotenv\n")
    monkeypatch.setenv("AIDER_TIMEOUT", "4")
    assert cfg.effective(cwd)["model"] == ("dotenv", "Env", str(space / ".env"))
    assert cfg.effective(cwd)["timeout"][0] == 1
    assert cfg.effective(cwd)["map-tokens"] == (40, "Home config", str(home / cfg.CONF))
    assert cfg.launch_values(cwd)["auto-commits"] is False


def test_pending_clears_when_effective_values_return_to_launch(space):
    original = cfg.config_fingerprint(space)
    (space / cfg.CONF).write_text("# explanation\nauto-commits: false\n")
    assert cfg.config_fingerprint(space) == original
    (space / cfg.CONF).write_text("auto-commits: 'true'\n")
    assert cfg.launch_values(space)["auto-commits"] is True
    assert cfg.config_fingerprint(space) != original
    (space / cfg.CONF).write_text("{}\n")
    assert cfg.config_fingerprint(space) == original


@pytest.fixture()
def client(space):
    class Deck:
        state = State(space.parent / "state.json", space.parent)

        def workspace(self, wid):
            return space

        def session(self, wid):
            return None

        def lock(self, wid):
            return threading.RLock()
    app = Flask(__name__)
    app.extensions["deck"] = Deck()
    app.extensions["deck"].state.data["workspaces"]["x"] = {
        "path": str(space), "prefs": {}, "history": [],
    }
    app.register_blueprint(config_bp)
    return app.test_client()


def test_registration_requires_current_preview(client, space):
    body = dict(kind="register", model="openai/test-custom", edit_format="diff")
    proposal = client.post("/api/x/config/preview", json=body).get_json()
    path = space / cfg.MODELS
    assert not path.exists()
    assert "+  edit_format: diff" in proposal["diff"]
    assert client.post("/api/x/config/apply", json=body).status_code == 409
    path.write_text("# Concurrent edit\n[]\n")
    assert client.post("/api/x/config/apply", json={**body, "token": proposal["token"]}).status_code == 409
    new = client.post("/api/x/config/preview", json=body).get_json()
    assert client.post("/api/x/config/apply", json={**body, "token": new["token"]}).status_code == 200
    assert cfg.registration(space, body["model"])["edit_format"] == "diff"
    assert "# Concurrent edit" in path.read_text()
    assert client.post("/api/x/config/preview", json=body).status_code == 400


def test_effort_declaration_adds_to_existing_exact_entry_after_review(client, space):
    (space / cfg.CONF).write_text("model: primary\nalias: [primary:openai/terra]\n")
    path = space / cfg.MODELS
    old = (
        "# model notes\n- name: openai/terra\n  edit_format: patch # owner format\n"
        "  accepts_settings: &shared [thinking_tokens] # retained\n  extra_params:\n    temperature: 0.2\n"
        "- name: sibling\n  accepts_settings: *shared\n"
    )
    path.write_text(old)
    body = {"kind": "reasoning-support", "model": "primary", "edit_format": "whole"}
    guard = cfg.reasoning_guard(space, "primary")
    assert guard["can_declare"] and guard["checked"] and guard["declared"] is False and not guard["needs_format"]
    result = client.post("/api/x/config/preview", json=body)
    assert result.status_code == 200, result.json
    proposal = result.json
    assert "reasoning_effort" in proposal["diff"] and path.read_text() == old
    path.write_text(old.replace("0.2", "0.4"))
    assert client.post("/api/x/config/apply", json={**body, "token": proposal["token"]}).status_code == 409
    reviewed = client.post("/api/x/config/preview", json=body).json
    assert client.post("/api/x/config/apply", json={**body, "token": reviewed["token"]}).status_code == 200
    text = path.read_text()
    assert all(note in text for note in ("# model notes", "# owner format", "# retained"))
    changed, other = cfg.parse(text, cfg.MODELS)
    assert changed["accepts_settings"] == ["thinking_tokens", "reasoning_effort"]
    assert other["accepts_settings"] == ["thinking_tokens"]
    assert changed["edit_format"] == "patch" and changed["extra_params"] == {"temperature": 0.4}
    assert cfg.reasoning_guard(space, "primary")["declared"] is True
    assert client.post("/api/x/config/preview", json=body).status_code == 400


@pytest.mark.parametrize("original_accepts", [["thinking_tokens"], ["thinking_tokens", "reasoning_effort"]])
def test_effort_declaration_registers_preserving_native_model_settings(client, space, monkeypatch, original_accepts):
    model = "openai/gpt-5.6-terra"
    (space / cfg.CONF).write_text("model: " + model + "\n")
    monkeypatch.setattr(cfg.shutil, "which", lambda command: str(Path(__file__)))
    native = dict(name=model, edit_format="whole", use_temperature=False, use_repo_map=True,
                  extra_params={"top_k": 12}, accepts_settings=original_accepts)
    monkeypatch.setattr(cfg, "_stock_model_settings", lambda *_: native)
    body = dict(kind="reasoning-support", model=model, edit_format="diff")
    guard = cfg.reasoning_guard(space, model)
    assert guard["needs_format"] and guard["declared"] is None and guard["can_declare"]
    for invalid in ({"model": "old"}, {"edit_format": "invalid"}, {"edit_format": None}):
        assert client.post("/api/x/config/preview", json={**body, **invalid}).status_code == 400
    preview = client.post("/api/x/config/preview", json=body)
    assert preview.status_code == 200, preview.json
    assert not (space / cfg.MODELS).exists()
    assert client.post("/api/x/config/apply", json={**body, "token": preview.json["token"]}).status_code == 200
    assert cfg.parse((space / cfg.MODELS).read_text(), cfg.MODELS) == [
        {**native, "edit_format": "diff", "accepts_settings": ["thinking_tokens", "reasoning_effort"]}
    ]


def test_effort_declaration_respects_inheritance_and_protects_shared_credentials(client, space):
    model = "openai/terra"
    (space / cfg.CONF).write_text("model: " + model + "\n")
    home = space.parent / "home" / cfg.MODELS
    shared = f"- name: {model}\n  use_temperature: false\n  accepts_settings: [thinking_tokens]\n"
    home.write_text(shared)
    body = dict(kind="reasoning-support", model=model)
    reviewed = client.post("/api/x/config/preview", json=body).json
    shared += "  reminder: sys\n"
    home.write_text(shared)
    assert client.post("/api/x/config/apply", json={**body, "token": reviewed["token"]}).status_code == 409
    reviewed = client.post("/api/x/config/preview", json=body).json
    assert client.post("/api/x/config/apply", json={**body, "token": reviewed["token"]}).status_code == 200
    assert home.read_text() == shared
    assert cfg.parse((space / cfg.MODELS).read_text(), cfg.MODELS)[0]["use_temperature"] is False
    (space / cfg.MODELS).unlink()
    home.write_text(shared + "  extra_params:\n    api_key: fixture-credential\n")
    assert not cfg.reasoning_guard(space, model)["can_declare"]
    blocked = client.post("/api/x/config/preview", json=body)
    assert blocked.status_code == 400 and "fixture-credential" not in blocked.text
    assert not (space / cfg.MODELS).exists()


def test_effort_declaration_overriding_file_and_launch_check(client, space):
    model = "openai/terra"
    body = dict(kind="reasoning-support", model=model)
    conf = space / cfg.CONF
    conf.write_text("model: " + model + "\nmodel-settings-file: selected.yaml\n")
    local = space / cfg.MODELS
    local.write_text("- name: " + model + "\n  edit_format: whole\n")
    overriding = space / "selected.yaml"
    overriding.write_text("- name: " + model + "\n  edit_format: patch\n")
    review = client.post("/api/x/config/preview", json=body).json
    assert review["file"] == "selected.yaml"
    assert client.post("/api/x/config/apply", json={**body, "token": review["token"]}).status_code == 200
    assert "accepts_settings" in overriding.read_text() and "accepts_settings" not in local.read_text()
    outside = space.parent / "override.yaml"
    outside.write_text(local.read_text())
    conf.write_text("model: " + model + "\nmodel-settings-file: " + outside.as_posix() + "\n")
    assert not cfg.reasoning_guard(space, model)["can_declare"]
    assert client.post("/api/x/config/preview", json=body).status_code == 400
    conf.write_text("model: " + model + "\ncheck-model-accepts-settings: false\n")
    guard = cfg.reasoning_guard(space, model)
    assert not guard["checked"] and not guard["can_declare"]
    conf.write_text("model: openrouter/provider/model\n")
    guard = cfg.reasoning_guard(space, "openrouter/provider/model")
    assert guard["declared"] and not guard["can_declare"]


def test_effort_preview_retains_crlf_and_omits_unchanged_sensitive_rows(client, space):
    model = "openai/terra"
    (space / cfg.CONF).write_text("model: " + model + "\n")
    path = space / cfg.MODELS
    raw = (f"- name: {model}\r\n  edit_format: diff\r\n"
           "- name: unrelated\r\n  extra_params:\r\n    api_key: synthetic-preserved-value\r\n")
    path.write_bytes(raw.encode())
    body = dict(kind="reasoning-support", model=model)
    review = client.post("/api/x/config/preview", json=body).json
    for private in ("unrelated", "api_key", "synthetic-preserved-value", "edit_format"):
        assert private not in review["diff"]
    assert client.post("/api/x/config/apply", json={**body, "token": review["token"]}).status_code == 200
    result = path.read_bytes()
    assert b"\r\n" in result and b"\n" not in result.replace(b"\r\n", b"")
    assert b"api_key: synthetic-preserved-value\r\n" in result


def test_effort_follows_selected_endpoint_and_validates_writes(space, client, monkeypatch):
    base = "/api/x/config"
    model = "openai/gpt-5.4"
    (space / cfg.CONF).write_text(f"model: {model}\n")
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    query = {"model": model, "target": "launch"}

    def save(value, expected=model):
        return client.post(base + "/setting", json={"key": "reasoning-effort", "value": value, "model": expected})

    listed = client.get(base + "/reasoning-effort", query_string=query)
    assert listed.json["status"] == "known" and "xhigh" in listed.json["levels"]
    assert save("xhigh").status_code == 200
    assert save("invented").status_code == 400
    assert save("low", "outdated-model").status_code == 409
    assert cfg.launch_values(space)["reasoning-effort"] == "xhigh"
    (space / ".env").write_text("OPENAI_API_BASE=http://127.0.0.1:99/v1\n")
    assert client.get(base + "/reasoning-effort", query_string=query).json["status"] == "unknown"
    for value in ("balanced", "3.5", "0"):
        assert save(value).status_code == 200
        assert cfg.launch_values(space)["reasoning-effort"] == value
    for invalid in ("a\n/help", "\x1b", "x" * 65, ["high"], True):
        assert save(invalid).status_code == 400
    assert save(None).status_code == 200 and "reasoning-effort" not in cfg.launch_values(space)


def test_effort_lookup_keeps_live_route_after_workspace_changes(space, monkeypatch):
    from unittest.mock import Mock
    from app import create_app
    from deck import reasoning_capabilities as effort

    local = "ollama_chat/local:latest"
    (space / cfg.CONF).write_text(f"model: {local}\n")
    dotenv = space / ".env"
    dotenv.write_text("OLLAMA_API_BASE=http://127.0.0.1:11435\n")
    app = create_app(space.parent / "state.json", space.parent / "prompts")
    client = app.test_client()
    deck = app.extensions["deck"]
    wid = deck.state.open(space)
    base = f"/api/{wid}/config"
    session = deck.session(wid)
    session.status, session.proc = "live", Mock()
    session.obs.data["model"] = local
    deck.reasoning_routes[wid] = effort.routing(space, cfg.launch_values(space))
    dotenv.write_text("OLLAMA_API_BASE=http://127.0.0.1:11436\n")
    monkeypatch.setattr(effort.OllamaService, "model_details", lambda self, tag, **kw: {
        "thinking": {"values": ["low", "high"] if self.endpoint.endswith("11435") else [False]}
    })
    args = dict(key="reasoning-effort", model=local, value="high")
    try:
        assert client.get(base + "/reasoning-effort?target=launch").json["status"] == "unsupported"
        live = client.get(base + "/reasoning-effort?target=live").json
        assert live["model"] == local and live["levels"] == ["low", "high"]
        assert client.post(base + "/setting", json=args).status_code == 400
        assert client.post(base + "/setting", json={**args, "live": True}).status_code == 200
        assert "reasoning-effort" not in cfg.launch_values(space)
        session.obs.data["model"] = "another"
        assert client.post(base + "/setting", json={**args, "live": True}).status_code == 409
    finally:
        session.status, session.proc = "idle", None
        deck.close()


def test_raw_model_formats_allow_native_modes_and_keep_unrelated_legacy_entries_editable(client, space):
    path = space / cfg.MODELS
    path.write_text("- name: existing\n  edit_format: legacy-coder\n")
    url = "/api/x/config/raw/" + cfg.MODELS
    revision = client.get(url).json["revision"]
    valid = (path.read_text() + "  use_repo_map: true\n"
             + "".join(f"- name: test-{mode}\n  edit_format: {mode}\n  editor_edit_format: editor-diff-fenced\n"
                       for mode in ("ask", "architect", "context", "help", "udiff-simple")))
    assert client.put(url, json=dict(revision=revision, text=valid)).status_code == 200
    revision = client.get(url).json["revision"]
    for key in ("edit_format", "editor_edit_format"):
        invalid = valid + f"- name: new-registration\n  {key}: unknown-coder\n"
        response = client.put(url, json=dict(revision=revision, text=invalid))
        assert response.status_code == 400 and f"unsupported {key}" in response.json["error"]
        assert path.read_text() == valid


def test_architect_environment_preserves_additional_dotenv_without_copying_secret_values(space):
    key = "SYNTHETIC-OVERRIDDEN-KEY"
    (space / ".env").write_text("AIDER_EDIT_FORMAT=whole\nOPENAI_API_KEY=workspace-key\n")
    (space / cfg.CONF).write_text("env-file: custom.env\n")
    (space / "custom.env").write_text(f"OPENAI_API_KEY={key}\n")
    env, files = cfg.launch_environment(space, True)
    try:
        assert env["AIDER_EDIT_FORMAT"] == "architect" and env["OPENAI_API_KEY"] == key
        text = (Path(files.name) / "architect.env").read_text()
        assert "AIDER_EDIT_FORMAT=architect" in text and "OPENAI_API_KEY=${DECK_LAUNCH_" in text
        assert key not in text and "workspace-key" not in text
        assert (space / ".env").read_text() == "AIDER_EDIT_FORMAT=whole\nOPENAI_API_KEY=workspace-key\n"
    finally:
        files.cleanup()


def test_preset_and_validation(client, space):
    p = "/api/x/config"
    assert client.get(p).json["first_run"]
    assert not (space / cfg.CONF).exists()
    payload = {"kind": "preset", "name": "Harbor working profile"}
    proposal = client.post(p + "/preview", json=payload).json
    assert "editor-diff" in proposal["diff"]
    client.post(p + "/apply", json={**payload, "token": proposal["token"]})
    launch = cfg.launch_values(space)
    assert launch["model"] == "openai/gpt-5.6-terra" and launch["edit-format"] == "diff"
    raw = p + "/raw/" + cfg.CONF
    current = client.get(raw).json
    error = client.put(raw, json={"text": "api-key: [secret: !@", "revision": current["revision"]})
    assert error.status_code == 400 and "secret" not in error.get_data(as_text=True)
    assert cfg.launch_values(space) == launch
    meta = p + "/raw/" + cfg.METADATA
    revision = client.get(meta).json["revision"]
    assert client.put(meta, json=dict(revision=revision, text="{")).status_code == 400
    assert client.put(meta, json=dict(revision=revision, text='{"m":{"max_input_tokens":24000}}')).status_code == 200
    assert cfg.model_limit(space, "m") == 24000


def test_raw_secrets_recover_and_never_return(client, space):
    key = "EXAMPLE-PRIVATE-MATERIAL"
    secret_file = ("api-key:\n  - provider=" + key + "\nmodel: example # " + key + "\n")
    (space / cfg.CONF).write_text(secret_file)
    raw = "/api/x/config/raw/" + cfg.CONF
    current = client.get(raw).json
    assert key not in json.dumps(current)
    assert key not in client.post("/api/x/config/preview", json={"kind":"preset","name":"Harbor working profile"}).get_data(as_text=True)
    updated = client.put(raw, json={**current, "text": current["text"].replace("model: example", "model: updated")})
    assert updated.status_code == 200 and key not in updated.get_data(as_text=True)
    actual = (space / cfg.CONF).read_text()
    assert "provider=" + key in actual and "# " + key in actual
    assert "model: updated" in actual


def test_malformed_config_can_be_repaired_without_disclosing_source(client, space):
    secret = "DO-NOT-ECHO-INVALID-CREDENTIAL"
    (space / cfg.CONF).write_text("api-key: [\n  - " + secret)
    base = "/api/x/config"
    result = client.get(base)
    assert result.status_code == 200 and result.json["issues"]
    assert secret not in result.get_data(as_text=True)
    raw = base + "/raw/" + cfg.CONF
    damaged = client.get(raw).json
    assert damaged["invalid"] and damaged["text"] == "" and secret not in str(damaged)
    body = {"revision": damaged["revision"], "text": "model: fixed\n"}
    assert client.put(raw, json=body).status_code == 400
    assert client.put(raw, json={**body, "replace_invalid": True}).status_code == 200
    assert not client.get(base).json["issues"]
    assert cfg.launch_values(space)["model"] == "fixed"


def test_key_source_includes_yaml_provider_override(client, space):
    (space / ".env").write_text("OPENAI_API_KEY=dotenv-deck-secret\n")
    (space / cfg.CONF).write_text("api-key:\n  - OPENAI=yaml-provider-secret\n")
    response = client.get("/api/x/config/keys")
    row = next(item for item in response.json["keys"] if item["name"] == "OPENAI_API_KEY")
    assert row["aider_override"] and cfg.CONF in row["source"]
    assert "yaml-provider-secret" not in response.get_data(as_text=True)
    assert "dotenv-deck-secret" not in response.get_data(as_text=True)
    assert cfg.replace_secrets("yaml-provider-secret", cfg.secret_values(space)) == "••••••••"


@pytest.mark.parametrize("setting", [
    "load: session.cmds", "message: hello", "message-file: next.txt", "msg: hello",
    "commit: true", "test: true", "apply: changes.patch", "update: true",
])
def test_implicit_actions_are_blocked(space, setting):
    (space / cfg.CONF).write_text(setting)
    with pytest.raises(ValueError, match="startup option"):
        cfg.validate_interactive_launch(space)


def test_guard_uses_effective_precedence(space, monkeypatch):
    home = space.parent / "home"
    (home / cfg.CONF).write_text("commit: true\n")
    (space / cfg.CONF).write_text("commit: false\n")
    # Aider 0.86.2 still prints commit: True in this case. configargparse
    # skips false for store_true actions, so it cannot negate the home flag.
    with pytest.raises(ValueError, match="commit"):
        cfg.validate_interactive_launch(space)
    (home / cfg.CONF).write_text("{}\n")
    cfg.validate_interactive_launch(space)
    (space / ".env").write_text("AIDER_COMMIT=true\n")
    with pytest.raises(ValueError, match="commit"):
        cfg.validate_interactive_launch(space)
    (space / ".env").write_text("AIDER_COMMIT=false\nAIDER_ENV_FILE=other.env\n")
    (space / "other.env").write_text("AIDER_LOAD=commands.txt\n")
    with pytest.raises(ValueError, match="load"):
        cfg.validate_interactive_launch(space)


@pytest.mark.parametrize("setting", [
    "api-key: [example={value}]", "analytics-posthog-project-api-key: {value}",
    "set-env: [EXAMPLE_ACCESS_TOKEN={value}]", "set-env: [HTTPS_PROXY=https://{userinfo}@proxy.example]",
])
def test_native_history_secret_guard(space, setting):
    from uuid import uuid4

    value = uuid4().hex
    (space / cfg.CONF).write_text(setting.format(value=value, userinfo=f"tester:{value}"))
    with pytest.raises(ValueError) as error:
        cfg.validate_interactive_launch(space)
    assert value not in str(error.value)
    (space / cfg.CONF).write_text("set-env: [EXAMPLE_REGION=eu-central]\n")
    (space / ".env").write_text(f"EXAMPLE_ACCESS_TOKEN='{value}'\n")
    cfg.validate_interactive_launch(space)


def test_metadata_and_verbose_settings_do_not_record_literal_credentials(space):
    (space / cfg.METADATA).write_text('{"m":{"input_cost_per_token":0.000001}}')
    (space / cfg.MODELS).write_text("- name: m\n  extra_params:\n    api_key: test-literal\n")
    cfg.validate_interactive_launch(space)
    (space / cfg.CONF).write_text("verbose: true\n")
    with pytest.raises(ValueError, match="credential fields") as error:
        cfg.validate_interactive_launch(space)
    assert "test-literal" not in str(error.value)
    (space / cfg.CONF).write_text("{}\n")
    (space / cfg.METADATA).write_text('{"m":{"headers":{"Authorization":"Bearer test-literal"}}}')
    with pytest.raises(ValueError, match="credential fields"):
        cfg.validate_interactive_launch(space)
