"""Verify saved-model visibility, catalog links and launch/live selection with real Aider."""

import logging
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

from browser_acceptance import (
    FIRST,
    KEY,
    SECOND,
    create_app,
    fixture_api,
    repo_setup,
    serve,
    wait_for,
)
from flask import Flask, jsonify, request
from manual_settings import save
from playwright.sync_api import expect, sync_playwright
from ruamel.yaml import YAML

LOCAL = "ollama_chat/nemotron-3.5-bolt:e4b"
DISCOVERED = "ollama_chat/deck-fixture-extra:tiny"
CUSTOM_LOCAL = "ollama_chat/my-private-profile:latest"
CLOUD = "openai/gpt-5.4"
CUSTOM = "acme/fixture-custom"
LIBRARY_PAGE = "https://ollama.com/library/nemotron-3.5-bolt"

PORTALS = {
    "OPENAI": "https://platform.openai.com/",
    "ANTHROPIC": "https://platform.claude.com/",
    "GEMINI": "https://aistudio.google.com/",
    "DEEPSEEK": "https://api-docs.deepseek.com/guides/codex",
    "COHERE": "https://docs.cohere.com/docs/get-started-installation",
    "GROQ": "https://console.groq.com/docs/quickstart",
    "MISTRAL": "https://docs.mistral.ai/",
    "OPENROUTER": "https://openrouter.ai/docs/quickstart",
}


def check_provider_portals(page):
    def key_row(name):
        return page.locator(".key-row").filter(has=page.get_by_text(name, exact=True))

    for provider, url in PORTALS.items():
        link = key_row(f"{provider}_API_KEY").get_by_role("link")
        expect(link).to_be_visible()
        expect(link).to_have_attribute("href", url)
    for name in ("OPENAI_API_BASE", "ACME_API_KEY"):
        expect(key_row(name)).to_be_visible()
        expect(key_row(name).get_by_role("link")).to_have_count(0)
    with page.expect_popup() as opened:
        key_row("ANTHROPIC_API_KEY").get_by_role("link").click()
    expect(opened.value).to_have_url(PORTALS["ANTHROPIC"])
    opened.value.close()


def local_ollama():
    fixture = Flask("model-picker-ollama-fixture")

    @fixture.get("/api/tags")
    def installed():
        return jsonify(
            models=[
                {"name": model.split("/", 1)[1], "size": 512}
                for model in (LOCAL, DISCOVERED)
            ]
        )

    @fixture.get("/api/ps")
    def running():
        return jsonify(models=[])

    @fixture.post("/api/show")
    def details():
        return jsonify(
            capabilities=["completion", "thinking"],
            details={"family": "nemotron"},
            model_info={},
        )

    return fixture


@contextmanager
def deck_browser(browser, base, repo):
    app = create_app(base / "state.json", base / "prompts")
    client = app.test_client()
    wid = client.post("/api/workspaces", json={"path": str(repo)}).json["id"]
    try:
        with serve(app) as url:
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            # A click on a documentation link opens the actual URL in a tab, served locally by Playwright.
            context.route(
                "https://**",
                lambda route: route.fulfill(status=200, body="Documentation fixture"),
            )
            page = context.new_page()
            page.set_default_timeout(12000)
            try:
                page.goto(url)
                yield page, app.extensions["deck"].session(wid)
            finally:
                context.close()
    finally:
        app.extensions["deck"].close()


def model_row(page):
    page.locator("#model-badge").click()
    field = page.locator("#field-model")
    expect(field).to_be_visible()
    return field.locator("xpath=ancestor::div[contains(@class, 'setting-row')][1]")


def mutation(page, action):
    with page.expect_response(
        lambda response: (
            response.url.endswith("/config/saved-models")
            and response.request.method == "POST"
        )
    ) as reply:
        action()
    assert reply.value.ok, reply.value.text()


def option(picker, name):
    return picker.locator("option").filter(
        has_text=re.compile("^" + re.escape(name) + "$")
    )


def manager(row):
    heading = row.get_by_text("Manage saved models", exact=True)
    area = heading.locator("xpath=ancestor::details[1]")
    if not area.evaluate("details => details.open"):
        heading.click()
    return area


def visibility(page, row, name, enabled):
    box = manager(row).get_by_role(
        "checkbox", name="Show " + name + " in picker", exact=True
    )
    mutation(page, lambda: box.set_checked(enabled))
    expect(box).to_be_checked(checked=enabled)
    expect(option(row.locator("#saved-model-picker"), name)).to_have_count(
        1 if enabled else 0
    )


def assert_catalogs(page, row):
    picker = row.locator("#saved-model-picker")
    for name, address in (
        (LOCAL, LIBRARY_PAGE),
        (CUSTOM_LOCAL, "https://ollama.com/search"),
        (CLOUD, "https://developers.openai.com/api/docs/models/gpt-5.4"),
    ):
        picker.select_option(name)
        expect(row.locator("#field-model")).to_have_value(name)
        link = row.locator(f'a[href="{address}"]')
        expect(link).to_be_visible()
        with page.expect_popup() as opened:
            link.click()
        expect(opened.value).to_have_url(address)
        opened.value.close()
    picker.select_option(CUSTOM)
    expect(row.locator("#field-model")).to_have_value(CUSTOM)
    expect(row.locator('a[href^="https://"]:visible')).to_have_count(0)


def exercise(page, session, repo, calls):
    config = repo / ".aider.conf.yml"
    original = config.read_bytes()
    page.locator("#start").click()
    wait_for(
        "initial live model announcement", lambda: session.snapshot()["model"] == FIRST
    )
    pid = session.proc.pid
    row = model_row(page)
    picker = row.locator("#saved-model-picker")
    field = row.locator("#field-model")
    expect(option(picker, FIRST)).to_have_count(1)
    for name in (LOCAL, CUSTOM_LOCAL, CLOUD, CUSTOM, SECOND):
        field.fill(name)
        mutation(page, row.get_by_role("button", name="Save model", exact=True).click)
        expect(option(picker, name)).to_have_count(1)

    choices = manager(row)
    # A catalog model and an actually installed fixture tag can be pinned without typing a name.
    anthropic = choices.get_by_role(
        "checkbox", name=re.compile(r"^Show anthropic\/.+ in picker$")
    )
    expect(anthropic.first).to_be_visible()
    candidate = (
        anthropic.first.get_attribute("aria-label")
        .removeprefix("Show ")
        .removesuffix(" in picker")
    )
    expect(anthropic.first).to_be_disabled()
    expect(choices).to_contain_text("ANTHROPIC_API_KEY")
    expect(
        choices.get_by_role(
            "checkbox", name=re.compile(r"^Show gemini\/.+ in picker$")
        ).first
    ).to_be_visible()
    choices.get_by_role("button", name="Open Keys", exact=True).first.click()
    check_provider_portals(page)
    key_row = page.locator(".key-row").filter(has_text="ANTHROPIC_API_KEY").first
    test_key = "local-fixture-anthropic-only"
    key_row.locator("input").fill(test_key)
    with page.expect_response(
        lambda response: (
            response.url.endswith("/config/keys") and response.request.method == "POST"
        )
    ) as reply:
        key_row.get_by_role("button", name="Save", exact=True).click()
    assert reply.value.ok, reply.value.text()
    assert test_key not in reply.value.text()
    row = model_row(page)
    expect(
        manager(row).get_by_role(
            "checkbox", name="Show " + candidate + " in picker", exact=True
        )
    ).to_be_enabled()
    for name in (candidate, DISCOVERED):
        expect(option(picker, name)).to_have_count(0)
        visibility(page, row, name, True)
        visibility(page, row, name, False)
    visibility(page, row, name=FIRST, enabled=False)
    page.reload()
    row = model_row(page)
    expect(option(picker, FIRST)).to_have_count(0)
    visibility(page, row, name=FIRST, enabled=True)
    assert_catalogs(page, row)
    assert config.read_bytes() == original
    assert session.proc.pid == pid and session.snapshot()["model"] == FIRST
    assert not calls
    print(
        "PASS typed and discovered cloud/local bookmarks, visibility on reload, verified catalog destinations",
        flush=True,
    )

    picker.select_option(SECOND)
    expect(field).to_have_value(SECOND)
    assert config.read_bytes() == original and session.snapshot()["model"] == FIRST
    row.get_by_role("button", name="Apply live", exact=True).click()
    wait_for(
        "live selection confirmed by actual Aider",
        lambda: session.snapshot()["model"] == SECOND,
    )
    assert session.proc.pid == pid and config.read_bytes() == original
    prompt = page.locator("#prompt")
    prompt.fill("DECK_BOOKMARK_SMOKE: reply to verify the active model.")
    page.locator("#send").click()
    wait_for(
        "selected live model answered through the local fixture",
        lambda: calls == [SECOND.split("/", 1)[1]],
    )

    picker.select_option(LOCAL)
    save(page, field)
    assert YAML(typ="safe").load(config)["model"] == LOCAL
    assert session.proc.pid == pid and session.snapshot()["model"] == SECOND
    mutation(
        page,
        manager(row).get_by_role("button", name="Remove " + SECOND, exact=True).click,
    )
    expect(option(picker, SECOND)).to_have_count(0)
    visibility(page, row, CUSTOM, False)
    assert session.proc.pid == pid and session.snapshot()["model"] == SECOND
    assert YAML(typ="safe").load(config)["model"] == LOCAL
    print(
        "PASS selecting, applying live, saving for next launch and removing the live bookmark have independent effects",
        flush=True,
    )


def main():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    calls = []
    fixture = fixture_api([])

    @fixture.before_request
    def capture_chat():
        if request.path == "/v1/chat/completions":
            calls.append(request.get_json()["model"])

    with (
        tempfile.TemporaryDirectory(
            prefix="deck-model-picker-", ignore_cleanup_errors=True
        ) as tmp,
        serve(fixture) as cloud,
        serve(local_ollama()) as ollama,
    ):
        base = Path(tmp)
        home, repo = base / "home", base / "repo"
        for path in (home, base / "prompts"):
            path.mkdir()
        for key in list(os.environ):
            if (
                key.startswith(("AIDER_", "OPENAI_", "OLLAMA_", "ANTHROPIC_"))
                or "API_KEY" in key
            ):
                del os.environ[key]
        os.environ.update(
            HOME=str(home),
            USERPROFILE=str(home),
            APPDATA=str(home),
            NO_PROXY="127.0.0.1,localhost",
            HTTP_PROXY="http://127.0.0.1:1",
            HTTPS_PROXY="http://127.0.0.1:1",
            LITELLM_LOCAL_MODEL_COST_MAP="True",
        )
        repo_setup(repo, cloud)
        (repo / ".env").write_text(
            f"OPENAI_API_BASE={cloud}/v1\nOPENAI_API_KEY={KEY}\nOLLAMA_API_BASE={ollama}\n"
            "ACME_API_KEY=local-fixture-custom-only\n"
        )
        with (repo / ".aider.conf.yml").open("a") as config:
            config.write("show-release-notes: false\n")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=os.getenv("DECK_CHROME"))
            with deck_browser(browser, base, repo) as (page, session):
                exercise(page, session, repo, calls)
            with deck_browser(browser, base, repo) as (page, session):
                row = model_row(page)
                picker = row.locator("#saved-model-picker")
                for name in (FIRST, LOCAL, CLOUD, CUSTOM_LOCAL):
                    expect(option(picker, name)).to_have_count(1)
                for name in (CUSTOM, SECOND, DISCOVERED):
                    expect(option(picker, name)).to_have_count(0)
                expect(
                    manager(row).get_by_role(
                        "checkbox", name="Show " + CUSTOM + " in picker", exact=True
                    )
                ).not_to_be_checked()
                expect(row.locator("#field-model")).to_have_value(LOCAL)
                page.locator("#start").click()
                wait_for(
                    "real Aider launched the separately saved local model",
                    lambda: session.snapshot()["model"] == LOCAL,
                )
                search = page.get_by_role("searchbox", name="Filter settings")
                search.fill("reasoning-effort")
                expect(
                    page.locator(f'#reasoning-effort-status a[href="{LIBRARY_PAGE}"]')
                ).to_be_visible()
            other = base / "other-repo"
            repo_setup(other, cloud)
            with deck_browser(browser, base, other) as (page, _):
                row = model_row(page)
                expect(row.locator("#saved-model-picker option")).to_have_count(2)
                expect(option(row.locator("#saved-model-picker"), FIRST)).to_have_count(
                    1
                )
                expect(option(row.locator("#saved-model-picker"), LOCAL)).to_have_count(
                    0
                )
            assert calls == [SECOND.split("/", 1)[1]]
            browser.close()
        print(
            "PASS persisted list after Deck restart, local model launch, reasoning catalog link and workspace isolation",
            flush=True,
        )


if __name__ == "__main__":
    main()
