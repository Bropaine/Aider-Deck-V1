"""Chromium/Aider smoke for missing local models and owner-initiated installation."""

import json
import logging
import os
from pathlib import Path
import tempfile

from flask import Response, jsonify, request
from playwright.sync_api import expect, sync_playwright

from browser_acceptance import FIRST, SECOND, create_app, fixture_api, repo_setup, serve, wait_for


def main():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    installed = {FIRST.split("/")[1] + ":latest"}
    downloaded = SECOND.split("/")[1] + ":latest"
    pulls = []
    fixture = fixture_api([])

    @fixture.get("/api/tags")
    def tags():
        return jsonify(models=[dict(name=name, size=30) for name in installed])

    @fixture.get("/api/ps")
    def ps():
        return jsonify(models=[])

    @fixture.post("/api/show")
    def show():
        return jsonify(capabilities=["completion"])

    @fixture.post("/api/pull")
    def pull():
        assert request.json == {"model": downloaded, "stream": True}
        pulls.append(downloaded)
        if len(pulls) == 1:
            event = {"error": "Fixture download interrupted"}
        else:
            installed.add(downloaded)
            event = {"status": "success"}
        return Response(json.dumps(event) + "\n", mimetype="application/x-ndjson")

    with tempfile.TemporaryDirectory(prefix="deck-model-gate-") as tmp, serve(fixture) as endpoint:
        base = Path(tmp)
        home = base / "home"
        home.mkdir()
        for key in list(os.environ):
            if key.startswith(("AIDER_", "OPENAI_", "OLLAMA_", "ANTHROPIC_")) or "API_KEY" in key:
                del os.environ[key]
        os.environ.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home),
                          NO_PROXY="127.0.0.1,localhost", HTTP_PROXY="http://127.0.0.1:1",
                          HTTPS_PROXY="http://127.0.0.1:1", LITELLM_LOCAL_MODEL_COST_MAP="True")
        repo = base / "repo"
        repo_setup(repo, endpoint)
        with (repo / ".env").open("a") as dotenv:
            dotenv.write("OLLAMA_API_BASE=" + endpoint + "\n")
        app = create_app(base / "state.json", home)
        deck = app.extensions["deck"]
        wid = deck.state.open(repo)
        app.test_client().post(f"/api/{wid}/config/saved-models", json={"action": "save", "model": SECOND})
        try:
            with serve(app) as url, sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=os.getenv("DECK_CHROME"))
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.set_default_timeout(20000)
                page.on("pageerror", lambda exc: print("Browser:", exc, flush=True))
                page.goto(url)
                page.locator("#start").click()
                session = deck.session(wid)
                wait_for("first model started", lambda: session.snapshot()["model"] == FIRST)
                original = session.proc
                page.locator("#model-badge").click()
                field = page.locator("#field-model")
                row = page.locator(".setting-row").filter(has=field)
                notice = row.locator(".model-install-notice")
                field.fill("ollama_chat/typed:small")
                expect(notice).to_contain_text("typed:small is not installed")
                page.locator("#saved-model-picker").select_option(SECOND)
                expect(notice).to_contain_text(downloaded + " is not installed")
                assert not pulls
                with page.expect_response(lambda response: response.url.endswith("/config/setting")) as write:
                    row.get_by_role("button", name="Save for next launch", exact=True).click()
                assert write.value.ok, write.value.json()
                assert write.value.request.post_data_json["value"] == SECOND, write.value.request.post_data_json
                wait_for("missing model saved for launch", lambda: f"model: {SECOND}" in (repo / ".aider.conf.yml").read_text())
                page.locator("#start").click()
                page.get_by_role("dialog").get_by_role("button", name="Restart session").click()
                dialog = page.get_by_role("dialog")
                expect(dialog).to_contain_text(downloaded + " is not installed")
                assert session.proc is original and session.snapshot()["model"] == FIRST and not pulls
                dialog.get_by_role("button", name="Open model settings").click()
                row.get_by_role("button", name="Apply live").click()
                expect(notice).to_contain_text("Live session")
                assert session.snapshot()["model"] == FIRST and not pulls
                notice.get_by_role("button", name="Download through Ollama").click()
                expect(notice).to_contain_text("Fixture download interrupted")
                notice.get_by_role("button", name="Download through Ollama").click()
                expect(notice).to_contain_text(downloaded + " is installed")
                assert len(pulls) == 2 and session.proc is original and session.snapshot()["model"] == FIRST
                row.get_by_role("button", name="Apply live").click()
                wait_for("installed model chosen explicitly", lambda: session.snapshot()["model"] == SECOND)
                assert session.proc is original
                browser.close()
        finally:
            deck.close()
    print("Passed: missing typed and saved models, guarded restart and live change, failed download, retry, explicit live switch.")


if __name__ == "__main__":
    main()
