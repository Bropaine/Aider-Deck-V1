"""Exercise every Settings row in Chromium against a disposable Deck workspace.

Run with .venv\\Scripts\\python tests/manual_settings.py.
Requires the browser and Aider CLI used by tests/browser_acceptance.py.
"""

import logging
import os
import tempfile
from collections import deque
from pathlib import Path

from browser_acceptance import create_app, serve
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright
from test_manual_controls import alternate_value

from deck import aider_config
from deck.registry import REGISTRY


def save(page, field):
    row = field.locator("xpath=ancestor::div[@class='setting-row']")
    # Track this save's acknowledgement. The toast may already contain the
    # same text from the previous save and redraws may replace the field.
    before = page.locator("#toast").evaluate_handle("el => el.firstChild")
    with (
        page.expect_response("**/config") as refreshed,
        page.expect_response("**/config/setting") as reply,
    ):
        label = "Save for next launch" if field.get_attribute("id") == "field-model" else "Save"
        row.get_by_role("button", name=label, exact=True).click()
    assert reply.value.ok, reply.value.text()
    assert refreshed.value.ok
    try:
        page.wait_for_function(
            "before => document.getElementById('toast').firstChild !== before",
            arg=before,
            polling=100,
            timeout=45000,
        )
        assert (
            "error" not in (page.locator("#toast").get_attribute("class") or "").split()
        )
    except PlaywrightTimeout as exc:
        raise AssertionError(
            f"Settings failed to refresh after saving {reply.value.request.post_data_json['key']}: "
            f"{page.locator('#toast').text_content()}"
        ) from exc
    return reply.value.request.post_data_json


def check_theme(page, config):
    search = page.get_by_role("searchbox", name="Filter settings")
    field = page.get_by_label("Terminal color mode", exact=True)
    search.fill("light-mode")
    field.select_option("light")
    save(page, field)
    page.get_by_text("Saved terminal theme.", exact=True).wait_for()
    assert config().get("light-mode") is True
    assert "dark-mode" not in config()
    search.fill("dark-mode")
    assert field.input_value() == "light", (
        field.input_value(),
        field.locator("..").inner_text(),
        config(),
    )
    field.select_option("dark")
    save(page, field)
    assert config().get("dark-mode") is True
    assert "light-mode" not in config()
    field.select_option("default")
    save(page, field)
    assert {"dark-mode", "light-mode"}.isdisjoint(config())
    assert field.input_value() == "default"


def model_suggestion(page, field):
    with page.expect_response("**/config/models"):
        field.fill("gpt-4o-mini")
    page.wait_for_function("document.querySelector('#list-model')?.options.length > 0")
    ids = page.locator("#list-model option").evaluate_all(
        "nodes => nodes.map(n => n.value)"
    )
    suggestion = next(model for model in ids if "gpt-4o-mini" in model.lower())
    return suggestion


def exercise_rows(page, rows, conf):
    def config():
        return aider_config.parse(conf.read_text(encoding="utf-8"), conf.name)

    check_theme(page, config)
    tested = {"dark-mode", "light-mode"}
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    for row in rows:
        key = row["key"]
        if key in tested:
            continue
        page.get_by_role("searchbox", name="Filter settings").fill(key)
        if key == "check-update":
            with page.expect_response("**/config/update", timeout=60000) as reply:
                page.get_by_role("button", name="Check now").click()
            assert reply.value.ok
            page.locator("#dialog[open]").wait_for()
            assert page.locator("#dialog-title").text_content() == "Aider update check"
            output = reply.value.json()["output"]
            assert page.locator("#dialog-body pre").text_content() == output
            page.get_by_role("button", name="Close dialog").click()
        else:
            field = page.locator("#field-" + key)
            chosen = (
                model_suggestion(page, field)
                if key == "model"
                else alternate_value(row)
            )
            if row["type"] == "toggle":
                field.select_option(str(chosen).lower())
            elif row["type"] == "select" and key != "reasoning-effort":
                field.select_option(chosen)
            elif row["type"] == "color":
                picker = page.get_by_label(row["label"] + " color picker", exact=True)
                picker.fill(chosen)
                assert field.input_value() == chosen
            else:
                field.fill(
                    "\n".join(chosen) if isinstance(chosen, list) else str(chosen)
                )
            save(page, field)
            assert config()[key] == chosen, key
            if key.endswith("api-key"):
                assert field.input_value() == ""
                assert chosen not in page.locator("body").inner_text()
                if key in ("openai-api-key", "anthropic-api-key"):
                    portal = field.locator("xpath=ancestor::div[@class='setting-row']").get_by_role("link")
                    expected = "https://platform.openai.com/" if key == "openai-api-key" else "https://platform.claude.com/"
                    assert portal.get_attribute("href") == expected
            else:
                if field.evaluate("el => el.tagName") == "SELECT":
                    field.select_option("")
                else:
                    field.fill("")
                save(page, field)
                assert key not in config(), key
        tested.add(key)
        print("PASS", key, flush=True)
    assert tested == set(REGISTRY) and not errors, (set(REGISTRY) - tested, errors)
    print(
        f"PASS all {len(tested)} Settings rows, including both theme flags", flush=True
    )


def main():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    with tempfile.TemporaryDirectory(prefix="deck-settings-") as tmp:
        base = Path(tmp)
        home, project, library = (
            base / name for name in ("home", "project", "prompts")
        )
        for folder in (home, project, library):
            folder.mkdir()
        for key in list(os.environ):
            if key.startswith("AIDER_"):
                os.environ.pop(key)
        os.environ.update(
            HOME=str(home),
            USERPROFILE=str(home),
            NO_PROXY="127.0.0.1,localhost",
            HTTP_PROXY="http://127.0.0.1:1",
            HTTPS_PROXY="http://127.0.0.1:1",
            LITELLM_LOCAL_MODEL_COST_MAP="True",
        )
        app = create_app(base / "state.json", library)
        client = app.test_client()
        wid = client.post("/api/workspaces", json={"path": str(project)}).json["id"]
        rows = client.get(f"/api/{wid}/config").json["rows"]
        try:
            with serve(app) as url, sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=os.getenv("DECK_CHROME"))
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                activity = deque(maxlen=16)
                page.on(
                    "response",
                    lambda reply: activity.append(
                        (reply.url.split("/")[-1], reply.status)
                    ),
                )
                page.on(
                    "requestfailed",
                    lambda req: activity.append((req.url.split("/")[-1], req.failure)),
                )
                page.route("https://**", lambda route: route.abort())
                page.goto(url)
                page.get_by_role("button", name="Settings", exact=True).click()
                try:
                    exercise_rows(page, rows, project / ".aider.conf.yml")
                except Exception:
                    print("Last browser requests:", list(activity), flush=True)
                    raise
                browser.close()
        finally:
            app.extensions["deck"].close()


if __name__ == "__main__":
    main()
