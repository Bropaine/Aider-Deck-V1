"""Exercise desktop file attachments with Chromium, real Aider and a loopback model."""

import base64
import json
import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from browser_acceptance import FIRST, SECOND, KEY, create_app, fixture_api, repo_setup, serve, wait_for
from flask import request
from playwright.sync_api import expect, sync_playwright


def model_metadata(repo, *, vision):
    path = repo / ".aider.model.metadata.json"
    models = json.loads(path.read_text())
    for entry in models.values():
        # Both tags allow a large window, but their Ollama allocations differ.
        entry.update(supports_vision=vision, max_input_tokens=524288, max_tokens=524288)
    path.write_text(json.dumps(models), encoding="utf-8")


def choose_files(page, paths):
    with page.expect_file_chooser() as chooser:
        page.locator("#attach-files").click()
    chooser.value.set_files(paths)


def drop_files(page, paths):
    # Actual browser Files and DataTransfer, dispatched to the visible composer.
    page.locator("#prompt").evaluate(
        """(prompt, files) => {
            const transfer = new DataTransfer();
            for (const {name, data} of files) {
                const bytes = Uint8Array.from(atob(data), c => c.charCodeAt(0));
                transfer.items.add(new File([bytes], name));
            }
            for (const type of ['dragenter', 'dragover', 'drop']) {
                const event = new DragEvent(type, {
                    dataTransfer: transfer, bubbles: true, cancelable: true
                });
                prompt.dispatchEvent(event);
            }
        }""",
        [
            {
                "name": path.name,
                "data": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
            for path in paths
        ],
    )


def token_report(page, state):
    page.locator("#refresh-tokens").click()
    wait_for(
        "Aider supplied a current token report for the active model",
        lambda: (s := state())["input_state"] == "ready" and not s["tokens"]["stale"]
        and s["tokens"]["data"].get("model") == s["model"],
    )
    return state()["tokens"]["data"]["used"]


def context_batches(page, state, source, copies, requests):
    warning = page.get_by_role("region", name="Attachment context warning")
    cancel = warning.get_by_role("button", name="Cancel", exact=True)
    confirm = warning.get_by_role("button", name="Attach anyway", exact=True)
    prompt = page.locator("#prompt")
    draft = prompt.input_value()

    def attached():
        return {row["name"] for row in state()["attachments"] if row["status"] == "attached"}

    def remove(path):
        page.get_by_role("button", name="Remove " + path.name, exact=True).click()
        wait_for(
            "removed " + path.name,
            lambda: path.name not in attached() and not list(copies.glob("deck-*/" + path.name)),
        )

    def switch_model(name):
        page.locator("#terminal .xterm-screen").click()
        page.keyboard.type("/model " + name)
        page.keyboard.press("Enter")
        wait_for(
            "native Aider switched to " + name,
            lambda: (s := state())["model"] == name and s["input_state"] == "ready",
        )
        expect(page.locator("#model")).to_have_text(name)

    def upload_with_review(select, paths):
        with page.expect_response("**/attachments") as response:
            select(page, paths)
        assert response.value.ok, response.value.text()
        review = response.value.json()["context_review"]
        expect(warning).to_be_visible()
        expect(warning).to_contain_text(review["model"])
        expect(prompt).to_have_value(draft)
        for path in paths:
            assert not list(copies.glob("deck-*/" + path.name))
        assert not attached() and not requests
        return review

    big = source / "large reference.txt"
    big.write_text("Sample source line.\n" * 15000, encoding="utf-8")
    assert big.stat().st_size > 256 * 1024
    review = upload_with_review(choose_files, [big])
    assert review["reason"] == "unknown" and review["remaining_tokens"] is None
    cancel.click()
    expect(warning).to_be_hidden()
    expect(prompt).to_have_value(draft)
    expect(page.locator("#send")).to_be_enabled()
    assert not attached() and not list(copies.glob("deck-*/" + big.name)) and not requests

    token_report(page, state)
    assert state()["local_context"]["window"] == 524288
    choose_files(page, [big])
    wait_for(
        "text over 256 KiB attached directly within the larger local window",
        lambda: attached() == {big.name},
    )
    expect(warning).to_be_hidden()
    assert next(copies.glob("deck-*/" + big.name)).read_bytes() == big.read_bytes()
    remove(big)

    switch_model(SECOND)
    used = token_report(page, state)
    window = state()["local_context"]["window"]
    assert window == 16384 and 0 < used < window
    remaining = window - used
    words = b"Read this example "
    near = source / "nearly full.txt"
    near_bytes = int(remaining * 0.95) * 3
    near.write_bytes((words * (near_bytes // len(words) + 1))[:near_bytes])
    review = upload_with_review(choose_files, [near])
    assert review["reason"] == "near" and review["estimated_tokens"] < remaining
    cancel.click()
    expect(warning).to_be_hidden()
    # Combined plain ASCII estimate falls between the remaining and full window.
    # Either half fits alone. Count bytes on disk independently below.
    part_bytes = 3 * (remaining + min(used // 2, 600)) // 2
    parts = [source / "chapter one.txt", source / "chapter two.txt"]
    for path in parts:
        path.write_bytes((words * (part_bytes // len(words) + 1))[:part_bytes])
    choose_files(page, [parts[0]])
    wait_for("one chapter fits the smaller local model", lambda: attached() == {parts[0].name})
    expect(warning).to_be_hidden()
    remove(parts[0])
    used = token_report(page, state)
    review = upload_with_review(drop_files, parts)
    assert review["model"] == SECOND and review["reason"] == "over", review
    assert review["remaining_tokens"] == window - used
    assert review["remaining_tokens"] < review["estimated_tokens"] < window
    expect(warning).to_contain_text(f"{review['estimated_tokens']:,}")
    expect(warning).to_contain_text(f"{review['remaining_tokens']:,}")
    cancel.click()
    expect(warning).to_be_hidden()
    assert not list(copies.glob("deck-*")) and not attached()
    assert not requests
    upload_with_review(choose_files, parts)
    confirm.click()
    wait_for("explicit override attached the reviewed group", lambda: attached() == {p.name for p in parts})
    expect(warning).to_be_hidden()
    expect(prompt).to_have_value(draft)
    for path in parts:
        assert next(copies.glob("deck-*/" + path.name)).read_bytes() == path.read_bytes()
        remove(path)
    assert not requests
    switch_model(FIRST)
    token_report(page, state)
    print(
        "PASS large text, live model switch, unknown/near/over budget, "
        "combined warning, Cancel and Attach anyway",
        flush=True,
    )


def exercise(page, state, repo, source, copies, requests, git):
    picker_file = (
        source / "target.txt"
    )  # A project file with the same basename already exists.
    removed_file = source / "selected for removal.md"
    dragged_file = source / "notes with spaces.md"
    picker_file.write_text("DESKTOP_PICKER_MARKER", encoding="utf-8")
    removed_file.write_text("REMOVED_BEFORE_SEND_MARKER", encoding="utf-8")
    dragged_file.write_text("DRAGGED_UTF8_\u0394_MARKER", encoding="utf-8")
    png, jpg = source / "diagram.png", source / "photo.jpg"
    for path, mime in [(png, "image/png"), (jpg, "image/jpeg")]:
        data_url = page.evaluate(
            """mime => {
                const canvas = document.createElement('canvas');
                canvas.width = canvas.height = 5;
                canvas.getContext('2d').fillRect(0, 0, 4, 4);
                return canvas.toDataURL(mime);
            }""",
            mime,
        )
        path.write_bytes(base64.b64decode(data_url.split(",", 1)[1]))

    def entries():
        return {row["name"]: row["status"] for row in state()["attachments"]}

    def expected_status(paths):
        return {path.name: "attached" for path in paths}

    def remove(path):
        page.get_by_role("button", name="Remove " + path.name, exact=True).click()
        wait_for("removed " + path.name, lambda: path.name not in entries())

    prompt = page.locator("#prompt")
    chips = page.locator("#prompt-attachments")
    prompt.fill("An unfinished attachment prompt")
    page.locator("#start").click()
    wait_for("real Aider terminal ready", lambda: state()["input_state"] == "ready")
    expect(page.locator("#model")).to_have_text(FIRST)
    context_batches(page, state, source, copies, requests)
    choose_files(page, [picker_file, removed_file])
    wait_for(
        "both chosen files attached read-only",
        lambda: entries() == expected_status([picker_file, removed_file]),
    )
    expect(chips).to_contain_text(picker_file.name)
    token_report(page, state)
    drop_files(page, [dragged_file, png, jpg])
    page.get_by_role("region", name="Attachment context warning").get_by_role("button", name="Attach anyway").click()
    active = [picker_file, removed_file, dragged_file, png, jpg]
    wait_for(
        "dragged text and both image formats attached",
        lambda: entries() == expected_status(active),
    )
    expect(prompt).to_have_value("An unfinished attachment prompt")
    remove(removed_file)
    active.remove(removed_file)
    page.reload()
    for path in active:
        expect(chips).to_contain_text(path.name)
    expect(chips).not_to_contain_text(removed_file.name)

    ignored = source / "private-note.txt"
    innocent = source / "rejected batch sibling.txt"
    ignored.write_text("IGNORED_FILE_MARKER", encoding="utf-8")
    innocent.write_text("ATOMIC_BATCH_MARKER", encoding="utf-8")
    with page.expect_response("**/attachments") as response:
        drop_files(page, [innocent, ignored])
    assert 400 <= response.value.status < 500, response.value.text()
    assert entries() == expected_status(active)
    expect(page.locator("#attachment-message")).to_be_visible()
    expect(page.locator("#send")).to_be_disabled()
    page.locator("#attachment-dismiss").click()
    binary = source / "not-text.txt"
    binary.write_bytes(b"BINARY\x00MARKER")
    with page.expect_response("**/attachments") as response:
        choose_files(page, [binary])
    assert 400 <= response.value.status < 500, response.value.text()
    expect(page.locator("#attachment-message")).to_be_visible()
    page.locator("#attachment-dismiss").click()
    assert entries() == expected_status(active)
    assert requests == [], "Managing attachments unexpectedly sent a model request"

    prompt.fill("DECK_ATTACHMENT_SMOKE: describe the attached references.")
    page.locator("#send").click()
    wait_for("one explicit prompt reached the local model", lambda: len(requests) == 1)
    messages = requests[0]
    content = json.dumps(messages, ensure_ascii=False)
    for path in [picker_file, dragged_file]:
        assert path.read_text(encoding="utf-8") in content, path.name
    for path in [removed_file, ignored, innocent]:
        assert path.read_text(encoding="utf-8") not in content, path.name
    image_parts = [
        part
        for msg in messages
        if isinstance(msg["content"], list)
        for part in msg["content"]
    ]
    images = {
        part["image_url"]["url"] for part in image_parts if part["type"] == "image_url"
    }
    for path, mime in [(png, "image/png"), (jpg, "image/jpeg")]:
        assert (
            "data:"
            + mime
            + ";base64,"
            + base64.b64encode(path.read_bytes()).decode("ascii")
            in images
        )
    native_images = [
        (repo / part["text"].removeprefix("Image file: ")).resolve()
        for part in image_parts
        if part["type"] == "text" and part["text"].startswith("Image file: ")
    ]
    assert len(native_images) == 2
    assert all(
        path.is_file() and not path.is_relative_to(repo) for path in native_images
    )
    assert (repo / "target.txt").read_text() == "before\n"
    assert git("status", "--porcelain") == ""
    print(
        "PASS fixture received exact text and image bytes only after Send; Git stayed clean",
        flush=True,
    )

    wait_for("Aider ready for restart", lambda: state()["input_state"] == "ready")
    model_metadata(repo, vision=False)
    page.locator("#start").click()
    page.get_by_role("dialog").get_by_role(
        "button", name="Restart session", exact=True
    ).click()
    wait_for(
        "restart removed temporary image copies",
        lambda: all(not path.exists() for path in native_images),
    )
    wait_for(
        "clean session with text-only model",
        lambda: (s := state())["input_state"] == "ready" and s["model"] == FIRST and not entries(),
    )
    expect(page.locator("#model")).to_have_text(FIRST)
    with page.expect_response("**/attachments") as response:
        choose_files(page, [png])
    assert response.value.ok, response.value.text()
    assert "attachments" in response.value.json()
    wait_for(
        "Aider declined image for text-only model",
        lambda: entries().get(png.name) == "failed",
    )
    expect(page.locator("#send")).to_be_disabled()
    remove(png)
    assert len(requests) == 1
    assert all(path.is_file() for path in active)
    print(
        "PASS restart cleanup, unsupported vision model and removal preserve original files",
        flush=True,
    )


def main():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    requests = []
    fixture = fixture_api([])

    @fixture.get("/api/tags")
    @fixture.get("/api/ps")
    def fixture_models():
        return {"models": [
            {"name": FIRST.split("/", 1)[1], "context_length": 524288},
            {"name": SECOND.split("/", 1)[1], "context_length": 16384},
        ]}

    @fixture.post("/api/show")
    def model_details():
        return {"capabilities": ["completion", "vision"]}

    @fixture.before_request
    def capture_messages():
        if request.path == "/v1/chat/completions":
            requests.append(request.get_json()["messages"])

    with (
        tempfile.TemporaryDirectory(
            prefix="deck-attachments-", ignore_cleanup_errors=True
        ) as tmp,
        serve(fixture) as endpoint,
    ):
        base = Path(tmp)
        repo, source, home, library = [
            base / name for name in ("repo", "Desktop", "home", "prompts")
        ]
        copies = base / "session-copies"
        for folder in (source, home, library, copies):
            folder.mkdir()
        for key in list(os.environ):
            if key.startswith(("AIDER_", "OPENAI_", "ANTHROPIC_", "OLLAMA_")) or "API_KEY" in key:
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
        git = repo_setup(repo, endpoint)
        (repo / ".env").write_text(
            f"OPENAI_API_BASE={endpoint}/v1\nOPENAI_API_KEY={KEY}\nOLLAMA_API_BASE={endpoint}\n"
        )
        (repo / ".aiderignore").write_text("private-note.txt\n")
        with (repo / ".aider.conf.yml").open("a") as config:
            config.write("show-release-notes: false\n")
        model_metadata(repo, vision=True)
        app = create_app(base / "state.json", library)
        client = app.test_client()
        wid = client.post("/api/workspaces", json={"path": str(repo)}).json["id"]
        try:
            with patch.object(tempfile, "tempdir", str(copies)), serve(app) as url, sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=os.getenv("DECK_CHROME"))
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.on(
                    "pageerror",
                    lambda error: print("Browser error:", error, flush=True),
                )
                page.goto(url)
                exercise(
                    page,
                    lambda: client.get(f"/api/{wid}/state").json,
                    repo,
                    source,
                    copies,
                    requests,
                    git,
                )
                browser.close()
        finally:
            app.extensions["deck"].close()


if __name__ == "__main__":
    main()
