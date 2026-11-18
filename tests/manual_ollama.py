"""Smoke-test the documented Deck UI against an installed local Ollama model."""

import argparse
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

from browser_acceptance import create_app, serve, wait_for


def exercise(root, base_url, tag):
    project = root / "project"
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (project / ".aider.conf.yml").write_text(
        "stream: false\ngitignore: false\nmap-tokens: 0\ncheck-update: false\n"
        "analytics: false\nshow-model-warnings: false\nyes-always: true\n",
        encoding="utf-8",
    )
    app = create_app(root / "state.json", root)
    deck = app.extensions["deck"]
    wid = deck.state.open(project)
    session = deck.session(wid)
    model = "ollama_chat/" + tag
    try:
        with serve(app) as url, sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.getenv("DECK_CHROME")
            )
            page = browser.new_page()
            page.route("https://**", lambda route: route.abort())
            page.goto(url)
            page.get_by_role("button", name="Keys", exact=True).click()
            page.get_by_label("Environment variable name").fill("OLLAMA_API_BASE")
            page.get_by_label("Environment variable value").fill(base_url)
            with page.expect_response("**/config/keys") as saved:
                page.get_by_role("button", name="Save variable").click()
            assert saved.value.ok
            page.locator("#model-badge").click()
            field = page.locator("#field-model")
            field.fill(model)
            with page.expect_response("**/config/setting") as saved:
                field.locator("xpath=ancestor::div[@class='setting-row']").get_by_role(
                    "button", name="Save for next launch", exact=True
                ).click()
            assert saved.value.ok
            page.locator("#start").click()
            wait_for(
                "Aider announces the selected local model",
                lambda: session.snapshot()["model"] == model,
                60,
            )
            page.locator("#mode-buttons").get_by_role(
                "button", name="Ask", exact=True
            ).click()
            wait_for("Ask mode", lambda: session.snapshot()["mode"] == "ask")
            cursor, _, _ = session.read_since(None, timeout=0)
            page.locator("#prompt").fill(
                "What is 379 plus 824? Reply with just the integer."
            )
            page.locator("#send").click()

            def answered():
                _, output, _ = session.read_since(cursor, timeout=0)
                plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
                return re.search(r"\b1,?203\b", plain) and session.snapshot()["usage"]

            wait_for("installed local model answered through Deck", answered, 150)
            browser.close()
    finally:
        deck.close()


if __name__ == "__main__":
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tag", required=True, help="Exact tag of an installed local model"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    address = urlsplit(base_url)
    if address.scheme != "http" or address.hostname not in (
        "localhost",
        "127.0.0.1",
        "::1",
    ):
        parser.error("Use a loopback Ollama HTTP endpoint.")
    with urlopen(base_url + "/api/tags", timeout=5) as response:
        installed = json.load(response)["models"]
    if not any(
        row["name"] == args.tag and not row.get("remote_model") for row in installed
    ):
        parser.error("The selected local model must already be installed.")
    with tempfile.TemporaryDirectory(
        prefix="deck-ollama-", ignore_cleanup_errors=True
    ) as name:
        root = Path(name)
        home = root / "home"
        home.mkdir()
        for key in list(os.environ):
            if (
                key.startswith(("AIDER_", "OPENAI_", "ANTHROPIC_", "OLLAMA_"))
                or "API_KEY" in key
            ):
                del os.environ[key]
        os.environ.update(
            HOME=str(home),
            USERPROFILE=str(home),
            NO_PROXY="127.0.0.1,localhost,::1",
            HTTP_PROXY="http://127.0.0.1:1",
            HTTPS_PROXY="http://127.0.0.1:1",
            LITELLM_LOCAL_MODEL_COST_MAP="True",
        )
        exercise(root, base_url, args.tag)
