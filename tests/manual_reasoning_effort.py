"""Walk the model-aware reasoning controls in Chromium, using only loopback requests."""

import json
import logging
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from browser_acceptance import (
    FIRST,
    create_app,
    fixture_api,
    repo_setup,
    serve,
    wait_for,
)
from flask import jsonify, request
from manual_settings import save
from playwright.sync_api import expect, sync_playwright

from deck import aider_config
from deck.sessions import VT

LEVELS_TAG = "effort-fixture:levels"
COMPAT_MODEL = "openai/" + LEVELS_TAG
NO_THINKING_TAG = "effort-fixture:off"
NO_LEVELS_TAG = "effort-fixture:unknown"
CLOUD = "openai/gpt-5.4"
TERRA = "openai/gpt-5.6-terra"


def provider_fixture():
    app = fixture_api([])
    app.config.update(
        CHAT_SETTINGS=[],
        MODEL_LOOKUPS=[],
        MODEL_DETAILS={
            LEVELS_TAG: {
                "capabilities": ["thinking"],
                "thinking": {"values": ["low", "high"]},
            },
            NO_THINKING_TAG: {
                "capabilities": ["completion"],
                "thinking": {"values": [False]},
            },
            NO_LEVELS_TAG: {"capabilities": ["completion", "thinking"]},
        },
    )

    @app.post("/api/show")
    def model_details():
        name = request.get_json()["model"]
        app.config["MODEL_LOOKUPS"].append(name)
        if name in app.config["MODEL_DETAILS"]:
            return jsonify(app.config["MODEL_DETAILS"][name])
        return jsonify(error="Unknown fixture model"), 404

    @app.get("/api/tags")
    def installed_models():
        # The same fixture serves OpenAI-compatible chat at /v1. Include its
        # runnable tags for the installation check performed before a launch.
        names = [*app.config["MODEL_DETAILS"], *(m.split("/", 1)[1] + ":latest" for m in (FIRST, CLOUD, TERRA))]
        return jsonify(models=[{"name": name} for name in names])

    @app.before_request
    def capture_effort():
        if request.path == "/v1/chat/completions":
            body = request.get_json()
            app.config["CHAT_SETTINGS"].append(
                (body["model"], body.get("reasoning_effort"), body.get("temperature"))
            )

    return app


def choose_model(page, model):
    search = page.get_by_role("searchbox", name="Filter settings")
    search.fill("Main model")
    field = page.locator("#field-model")
    field.fill(model)
    sent = save(page, field)
    assert sent["key"] == "model" and sent["value"] == model, (model, sent)
    expect(field).to_have_value(model)
    search.fill("reasoning-effort")


def options(field):
    return field.locator("option").evaluate_all(
        "items => items.filter(x => x.value && !x.disabled).map(x => x.value)"
    )


def check_effort_source_and_older_server(page, row):
    model_lookup = "**/config/reasoning-effort?*"
    page.route(model_lookup, lambda route: route.fulfill(status=404, json={}))
    choose_model(page, TERRA)
    manual = row.get_by_role("textbox", name="Reasoning effort", exact=True)
    status = row.locator("#reasoning-effort-status")
    expect(status).to_contain_text("This Deck server predates model capability lookup")
    expect(manual).to_be_editable()
    manual.fill("max")
    expect(manual).to_have_value("max")
    expect(status.get_by_role("link")).to_have_count(0)
    page.unroute(model_lookup)
    row.get_by_role("button", name="Check levels again").click()
    field = row.locator("select#field-reasoning-effort")
    expect(field).to_have_value("max")
    assert options(field) == ["none", "low", "medium", "high", "xhigh", "max"]
    source = status.get_by_role("link")
    terra_docs = "https://developers.openai.com/api/docs/models/gpt-5.6-terra"
    expect(source).to_have_attribute("href", terra_docs)
    # Confirm the actual link opens the exact URL without contacting the public site.
    page.context.route(
        terra_docs, lambda route: route.fulfill(status=200, body="Source link smoke")
    )
    try:
        with page.expect_popup() as opened:
            source.click()
        expect(opened.value).to_have_url(terra_docs)
        opened.value.close()
    finally:
        page.context.unroute(terra_docs)
    choose_model(page, CLOUD)
    expect(source).to_have_attribute(
        "href", "https://developers.openai.com/api/docs/models/gpt-5.4"
    )
    print(
        "PASS model-specific source links and manual fallback/retry on an older Deck server",
        flush=True,
    )


def preview_registration(page):
    with page.expect_response("**/config/preview", timeout=50000) as response:
        page.get_by_role("dialog").get_by_role(
            "button", name="Preview YAML diff"
        ).click()
    assert response.value.ok, response.value.text()
    return response.value.json()["diff"]


def check_registration_checkbox(page, project):
    """The ordinary registration flow can enable a saved effort in one confirmed write."""
    register = page.locator("#notices").get_by_role("button", name="Register…")
    dialog = page.get_by_role("dialog")
    include = dialog.get_by_role(
        "checkbox", name="Declare reasoning effort support", exact=True
    )
    path = project / ".aider.model.settings.yml"
    before = path.read_bytes()
    register.click()
    expect(include).to_be_checked()
    include.uncheck()
    diff = preview_registration(page)
    assert "reasoning_effort" not in diff.split(f"+- name: {CLOUD}\n", 1)[1]
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    assert path.read_bytes() == before
    register.click()
    expect(include).to_be_checked()
    dialog.get_by_label("Model default edit format").select_option("patch")
    preview_registration(page)
    expect(dialog).to_contain_text("reasoning_effort")
    # Hold the resulting config refresh. Registration must finish redrawing
    # Settings before a user can type a new model into the underlying form.
    held = []
    pattern = "**/config"
    def hold_refresh(route):
        held.append(route)
        page.evaluate("window.registrationRefreshing = true")
    page.route(pattern, hold_refresh)
    try:
        dialog.get_by_role("button", name="Confirm write", exact=True).click()
        page.wait_for_function("window.registrationRefreshing")
        expect(dialog).to_be_visible(timeout=1500)
    finally:
        page.unroute(pattern, hold_refresh)
        for route in held:
            route.continue_()
    expect(dialog).not_to_be_visible()
    entries = aider_config.parse(path.read_text(encoding="utf-8"), path.name)
    saved = next(item for item in entries if item["name"] == CLOUD)
    assert (
        saved["edit_format"] == "patch"
        and "reasoning_effort" in saved["accepts_settings"]
    )
    print("PASS optional launch effort in normal model registration", flush=True)


def check_ollama_compatible(page, session, provider):
    select = page.locator("select#field-reasoning-effort")
    query_count = provider.config["MODEL_LOOKUPS"].count(LEVELS_TAG)
    choose_model(page, COMPAT_MODEL)
    page.locator("#reasoning-effort-target").select_option("launch")
    expect(select).to_be_enabled()
    assert options(select) == ["low", "high"]
    assert provider.config["MODEL_LOOKUPS"].count(LEVELS_TAG) > query_count
    select.select_option("high")
    save(page, select)

    # This workspace already registered this model. Set its temperature through the raw editor.
    name = ".aider.model.settings.yml"
    page.get_by_role("button", name=name, exact=True).click()
    dialog = page.get_by_role("dialog")
    editor = dialog.get_by_role("textbox", name=name, exact=True)
    original = editor.input_value()
    entry = f"- name: {COMPAT_MODEL}\n"
    assert original.count(entry) == 1
    editor.fill(
        original.replace(entry, entry + "  extra_params:\n    temperature: 0.2\n")
    )
    with page.expect_response(
        lambda r: r.url.endswith("/config/raw/" + name) and r.request.method == "PUT"
    ) as model_write:
        dialog.get_by_role("button", name="Save file").click()
    assert model_write.value.ok
    expect(dialog).not_to_be_visible()
    page.locator("#start").click()
    dialog.get_by_role("button", name="Restart session", exact=True).click()
    wait_for(
        "new Aider process loaded the registered Ollama compatibility model",
        lambda: session.snapshot()["model"] == COMPAT_MODEL,
    )
    page.locator("#prompt").fill("Confirm the local model settings.")
    page.locator("#send").click()
    wait_for(
        "Ollama-compatible request carried the selected effort and numeric temperature",
        lambda: (
            (LEVELS_TAG, "high", 0.2) in provider.config["CHAT_SETTINGS"]
            and "DECK_ANSWER_OK" in session.read_since(None, timeout=0)[1]
        ),
    )
    page.locator("#reasoning-effort-target").select_option("live")
    expect(select).to_be_enabled()
    assert options(select) == ["low", "high"]
    select.select_option("low")
    select.locator("xpath=ancestor::div[@class='setting-row']").get_by_role(
        "button", name="Apply live"
    ).click()
    wait_for(
        "Aider switched the named local effort on the running model",
        lambda: "Set reasoning effort to low" in session.read_since(None, timeout=0)[1],
    )
    page.locator("#prompt").fill("Confirm the changed local effort.")
    page.locator("#send").click()
    wait_for(
        "Ollama-compatible live effort changed and temperature stayed at 0.2",
        lambda: (LEVELS_TAG, "low", 0.2) in provider.config["CHAT_SETTINGS"],
    )


def check_guarded_launch(page, session, project, chats):
    """Reproduce the native ignored-setting warning, then repair the exact model."""
    choose_model(page, TERRA)
    manual = page.locator("input#field-reasoning-effort")
    manual.fill("high")
    save(page, manual)
    dialog = page.get_by_role("dialog")

    def restart():
        old_pid = session.proc.pid
        page.locator("#start").click()
        dialog.get_by_role("button", name="Restart session", exact=True).click()
        wait_for(
            "restarted Aider announced Terra",
            lambda: (
                session.snapshot()["model"] == TERRA and session.proc.pid != old_pid
            ),
        )

    def sees_warning():
        text = VT.sub("", session.read_since(None, timeout=0)[1])
        return "doesnotsupport'reasoning_effort',ignoring." in "".join(text.split())

    def ask(expected):
        count = len(chats)
        page.locator("#prompt").fill("Confirm the Terra launch setting.")
        page.locator("#send").click()
        wait_for(
            f"Terra request carried reasoning effort {expected!r}",
            lambda: (
                any(
                    model == TERRA.removeprefix("openai/") and effort == expected
                    for model, effort, _ in chats[count:]
                )
                and "DECK_ANSWER_OK" in session.read_since(None, timeout=0)[1]
            ),
        )

    restart()
    wait_for("reproduced Aider's reasoning_effort warning", sees_warning)
    search = page.get_by_role("searchbox", name="Filter settings")
    settings = page.get_by_role("button", name="Settings", exact=True)
    headings = page.locator("#inspector-content details.group > summary")
    # A typed filter with the same spelling as the warning shortcut is an owner choice.
    expect(search).to_have_value("reasoning-effort")
    search.fill("")
    all_groups = headings.all_text_contents()
    assert "Main model" in all_groups and "Repo map" in all_groups
    ask(None)
    review = page.locator("#notices").get_by_role(
        "button", name="Review reasoning effort"
    )
    page.get_by_role("button", name="Git", exact=True).click()
    review.click()
    expect(search).to_have_value("reasoning-effort")
    search.fill("map-tokens")
    restart()
    expect(settings).to_have_attribute("aria-current", "page")
    expect(search).to_have_value("map-tokens")
    expect(page.locator("#field-map-tokens")).to_be_visible()
    expect(headings).to_have_text(["Repo map"])
    search.fill("")
    review.click()
    expect(search).to_have_value("reasoning-effort")
    expect(page.locator("#field-map-tokens")).to_have_count(0)
    target = page.locator("#reasoning-effort-target")
    target.select_option("live")
    manual.fill("high")
    page.get_by_role("button", name="Apply live", exact=True).click()
    wait_for(
        "Aider accepted the same setting live",
        lambda: (
            "Set reasoning effort to high" in session.read_since(None, timeout=0)[1]
        ),
    )
    ask("high")
    target.select_option("launch")
    pid = session.proc.pid
    path = project / ".aider.model.settings.yml"
    original = path.read_bytes()
    config = project / ".aider.conf.yml"
    original_config = config.read_bytes()
    enable = page.get_by_role(
        "button", name="Enable reasoning effort for this model…", exact=True
    )
    enable.click()
    expect(dialog).to_contain_text("reasoning_effort")
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    assert path.read_bytes() == original and session.proc.pid == pid
    enable.click()
    dialog.get_by_role("button", name="Confirm write", exact=True).click()
    expect(dialog).not_to_be_visible()
    models = aider_config.parse(path.read_text(encoding="utf-8"), path.name)
    model = next(item for item in models if item["name"] == TERRA)
    assert model["accepts_settings"] == ["thinking_tokens", "reasoning_effort"]
    assert model["edit_format"] == "diff" and session.proc.pid == pid
    assert config.read_bytes() == original_config
    document_start = page.evaluate("performance.timeOrigin")
    restart()
    assert not sees_warning()
    expect(settings).to_have_attribute("aria-current", "page")
    expect(search).to_have_value("")
    expect(headings).to_have_text(all_groups)
    assert page.evaluate("performance.timeOrigin") == document_start
    print("PASS restart restored every Settings group after warning review and preserved a typed filter", flush=True)
    ask("high")
    expect(review).to_be_hidden()

    # A new entry also sets the owner's chosen Code format, after the preview.
    new_model = "openai/deck-new-registration"
    choose_model(page, new_model)
    enable.click()
    dialog.get_by_label("Model default edit format").select_option("patch")
    preview_registration(page)
    expect(dialog).to_contain_text("reasoning_effort")
    expect(dialog).to_contain_text("patch")
    pid = session.proc.pid
    dialog.get_by_role("button", name="Confirm write", exact=True).click()
    expect(dialog).not_to_be_visible()
    entries = aider_config.parse(path.read_text(encoding="utf-8"), path.name)
    added = next(item for item in entries if item["name"] == new_model)
    assert added["edit_format"] == "patch" and added["accepts_settings"] == [
        "reasoning_effort"
    ]
    assert session.proc.pid == pid and session.snapshot()["model"] == TERRA
    print(
        "PASS created a new model registration with the selected format and effort support",
        flush=True,
    )


def exercise(page, app, project, url, endpoint, provider):
    conf = project / ".aider.conf.yml"
    model_lookups, chats = (
        provider.config["MODEL_LOOKUPS"],
        provider.config["CHAT_SETTINGS"],
    )

    def saved_effort():
        return aider_config.parse(conf.read_text(encoding="utf-8"), conf.name).get(
            "reasoning-effort"
        )

    deck = app.extensions["deck"]
    workspace = deck.state.open(project)
    session = deck.session(workspace)
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.route("https://**", lambda route: route.abort())
    page.goto(url)
    settings = page.get_by_role("button", name="Settings", exact=True)
    select = page.locator("select#field-reasoning-effort")
    manual = page.locator("input#field-reasoning-effort")
    search = page.get_by_role("searchbox", name="Filter settings")
    expect(search).to_be_visible()
    row = page.locator(".setting-row").filter(
        has=page.locator("#field-reasoning-effort")
    )
    choose_model(page, "ollama_chat/" + LEVELS_TAG)
    expect(select).to_be_enabled()
    assert options(select) == ["low", "high"] and LEVELS_TAG in model_lookups
    select.select_option("high")
    save(page, select)
    assert saved_effort() == "high"

    check_effort_source_and_older_server(page, row)
    expect(select).to_be_enabled()
    assert options(select) == ["none", "low", "medium", "high", "xhigh"]
    expect(select).to_have_value("high")
    select.select_option("xhigh")
    save(page, select)
    assert saved_effort() == "xhigh"
    check_registration_checkbox(page, project)
    print("PASS exact local levels and official cloud levels survive Save", flush=True)

    choose_model(page, "ollama_chat/" + NO_THINKING_TAG)
    clear_effort = row.get_by_role("button", name="Clear saved effort")
    expect(clear_effort).to_be_enabled()
    expect(select).to_be_disabled()
    assert saved_effort() == "xhigh" and NO_THINKING_TAG in model_lookups
    clear_effort.click()
    wait_for("explicitly removed incompatible override", lambda: saved_effort() is None)
    choose_model(page, "ollama_chat/" + NO_LEVELS_TAG)
    expect(manual).to_be_enabled()
    manual.fill("balanced")
    save(page, manual)
    assert saved_effort() == "balanced" and NO_LEVELS_TAG in model_lookups
    provider.config["MODEL_DETAILS"][NO_LEVELS_TAG] = {
        "thinking": {"values": ["low", "high"]}
    }
    row.get_by_role("button", name="Check levels again").click()
    expect(select).to_be_enabled()
    assert options(select) == ["low", "high"] and saved_effort() == "balanced"
    expect(row.get_by_role("button", name="Save", exact=True)).to_be_disabled()
    print(
        "PASS omitted levels allow manual entry and explicit lack of support disables it",
        flush=True,
    )
    print(
        "PASS retry recovers published levels without changing an existing manual value",
        flush=True,
    )

    # The same official-looking model name can be an unrelated alias at a gateway.
    page.get_by_role("button", name="Keys", exact=True).click()
    base_row = page.locator(".key-row").filter(has_text="OPENAI_API_BASE").first
    base_row.locator("input").fill(endpoint + "/v1")
    with page.expect_response(
        lambda response: (
            response.url.endswith("/config/keys") and response.request.method == "POST"
        )
    ) as set_base:
        base_row.get_by_role("button", name="Save", exact=True).click()
    assert set_base.value.ok
    settings.click()
    choose_model(page, CLOUD)
    expect(manual).to_be_enabled()
    expect(manual).to_have_value("balanced")
    manual.fill("custom-level")
    save(page, manual)
    page.reload()
    search.fill("reasoning-effort")
    expect(manual).to_have_value("custom-level")
    assert not chats, "Capability lookup unexpectedly sent a chat request"
    print(
        "PASS custom gateway with a familiar model ID, manual value persisted after reload",
        flush=True,
    )

    choose_model(page, FIRST)
    page.locator("#start").click()
    wait_for(
        "real Aider announced the scratch model",
        lambda: session.snapshot()["model"] == FIRST,
    )
    page.locator("#prompt").fill("Confirm the launch setting.")
    page.locator("#send").click()
    wait_for(
        "Aider used the saved effort on the first request",
        lambda: (
            any(effort == "custom-level" for _, effort, _ in chats)
            and "DECK_ANSWER_OK" in session.read_since(None, timeout=0)[1]
        ),
    )
    target = page.locator("#reasoning-effort-target")
    expect(target).to_be_visible()
    choose_model(page, "ollama_chat/" + LEVELS_TAG)
    target.select_option("launch")
    expect(select).to_be_enabled()
    assert options(select) == ["low", "high"]
    select.select_option("low")
    save(page, select)
    assert saved_effort() == "low"
    target.select_option("live")
    expect(manual).to_be_enabled()
    manual.fill("custom-live")
    row.get_by_role("button", name="Apply live", exact=True).click()
    wait_for(
        "Aider accepted the manual effort for the live model",
        lambda: (
            "Set reasoning effort to custom-live"
            in session.read_since(None, timeout=0)[1]
        ),
    )
    assert saved_effort() == "low"
    page.locator("#prompt").fill("Reply with OK.")
    page.locator("#send").click()
    wait_for(
        "Aider sent the applied effort to the loopback chat endpoint",
        lambda: (
            any(effort == "custom-live" for _, effort, _ in chats)
            and session.read_since(None, timeout=0)[1].count("DECK_ANSWER_OK") >= 2
        ),
    )
    check_ollama_compatible(page, session, provider)
    check_guarded_launch(page, session, project, chats)
    page.wait_for_load_state("networkidle", timeout=60000)
    assert not errors, errors


def main():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    provider = provider_fixture()
    with (
        tempfile.TemporaryDirectory(prefix="deck-reasoning-") as tmp,
        serve(provider) as endpoint,
    ):
        base = Path(tmp)
        home = base / "home"
        home.mkdir()
        for key in list(os.environ):
            if key.startswith(
                ("AIDER_", "OPENAI_", "ANTHROPIC_", "OLLAMA_", "LITELLM_")
            ):
                del os.environ[key]
        os.environ.update(
            HOME=str(home),
            USERPROFILE=str(home),
            NO_PROXY="127.0.0.1,localhost",
            HTTP_PROXY="http://127.0.0.1:1",
            HTTPS_PROXY="http://127.0.0.1:1",
            LITELLM_LOCAL_MODEL_COST_MAP="True",
            PYTHONIOENCODING="utf-8",
        )
        project = base / "project"
        repo_setup(project, endpoint)
        # Start with the real cloud model catalog, then select the loopback gateway in the UI.
        (project / ".env").write_text(
            f"OLLAMA_API_BASE={endpoint}\nOPENAI_API_KEY={uuid4()}\n"
        )
        (project / ".aider.model.settings.yml").write_text(
            f"- name: {TERRA}\n  edit_format: diff\n  accepts_settings: [thinking_tokens]\n"
            + "".join(
                f"- name: {model}\n  edit_format: diff\n  accepts_settings: [reasoning_effort]\n"
                for model in (FIRST, COMPAT_MODEL)
            )
        )
        meta = project / ".aider.model.metadata.json"
        metadata = json.loads(meta.read_text())
        metadata[TERRA] = metadata[TERRA.removeprefix("openai/")] = metadata[FIRST]
        meta.write_text(json.dumps(metadata))
        app = create_app(base / "state.json", base / "prompts")
        try:
            with serve(app) as url, sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=os.getenv("DECK_CHROME"))
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.set_default_timeout(20000)
                exercise(page, app, project, url, endpoint, provider)
                browser.close()
        finally:
            app.extensions["deck"].close()


if __name__ == "__main__":
    main()
