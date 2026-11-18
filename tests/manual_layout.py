"""Resize and restore the workspace in Chromium while inspecting a real Windows ConPTY."""

import json
import logging
import os
import tempfile
from pathlib import Path

from browser_acceptance import FIRST, fixture_api, repo_setup, serve, wait_for
from manual_saved_models import deck_browser
from playwright.sync_api import expect, sync_playwright


def geometry(element):
    bounds = element.bounding_box()
    assert bounds, f"Missing element geometry: {element}"
    return bounds


def check_pty(page, session, repo):
    # Running under Aider lets the probe open the live console even though Aider captures stdout.
    result = repo / "terminal-size.jsonl"
    before = len(result.read_text().splitlines()) if result.exists() else 0
    page.locator("#terminal .xterm-screen").click()
    page.keyboard.type("/test python read_terminal_size.py")
    page.keyboard.press("Enter")
    wait_for(
        "Aider probed the live console",
        lambda: result.exists() and len(result.read_text().splitlines()) > before,
    )
    width, height = json.loads(result.read_text().splitlines()[-1])
    assert (width, height) == (session.cols, session.rows), (
        (width, height),
        (session.cols, session.rows),
    )
    return width, height


def drag_edge(page, edge, dx):
    box = geometry(edge)
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y, steps=10)
    page.mouse.up()


def exercise_side_widths(page, session, repo, draft):
    explorer, inspector = page.locator("#explorer-pane"), page.locator("#inspector-pane")
    left, right = page.locator("#explorer-divider"), page.locator("#inspector-divider")
    prompt = page.locator("#prompt")
    terminal = page.locator("#terminal-shell")

    def width(element):
        return geometry(element)["width"]

    for edge in (left, right):
        expect(edge).to_have_attribute("role", "separator")
        expect(edge).to_have_attribute("aria-orientation", "vertical")
        expect(edge).to_be_visible()
    pid = session.proc.pid
    initial_left, initial_right = width(explorer), width(inspector)
    initial_center = width(terminal)
    initial_cols, _ = check_pty(page, session, repo)

    drag_edge(page, left, -60)
    narrow_left = width(explorer)
    assert narrow_left < initial_left - 30 and abs(width(inspector) - initial_right) < 2
    drag_edge(page, right, 80)
    narrow_right = width(inspector)
    assert narrow_right < initial_right - 40 and abs(width(explorer) - narrow_left) < 2
    expect(explorer).to_be_visible()
    expect(inspector).to_be_visible()
    assert width(terminal) > initial_center + 70
    assert abs(width(page.locator(".composer")) - width(terminal)) < 2
    wait_for("both open panels yielded terminal columns", lambda: session.cols > initial_cols)
    expanded_cols, _ = check_pty(page, session, repo)
    assert expanded_cols > initial_cols

    left.press("ArrowRight")
    assert width(explorer) > narrow_left and abs(width(inspector) - narrow_right) < 2
    left.press("ArrowLeft")
    right.press("ArrowLeft")
    assert width(inspector) > narrow_right and abs(width(explorer) - narrow_left) < 2
    right.press("ArrowRight")
    assert abs(width(inspector) - narrow_right) < 2
    for control, edge, pane, previous in (
        ("#toggle-explorer", left, explorer, narrow_left),
        ("#toggle-inspector", right, inspector, narrow_right),
    ):
        page.locator(control).click()
        expect(edge).to_be_hidden()
        page.locator(control).click()
        expect(edge).to_be_visible()
        assert abs(width(pane) - previous) < 2
    expect(prompt).to_have_value(draft)
    page.reload()
    expect(page.locator("#model")).to_have_text(FIRST)
    expect(page.locator("#chat-files")).to_contain_text("target.txt")
    assert abs(width(explorer) - narrow_left) < 2 and abs(width(inspector) - narrow_right) < 2
    assert session.proc.pid == pid
    prompt.fill(draft)

    page.set_viewport_size({"width": 900, "height": 900})
    drag_edge(page, left, 470)
    drag_edge(page, right, -530)
    assert width(terminal) >= 298 and width(explorer) > 100 and width(inspector) > 100
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    # Keyboard users can reach the same bounds the separators announce.
    for edge, pane in ((left, explorer), (right, inspector)):
        for key, limit in (("Home", "aria-valuemin"), ("End", "aria-valuemax")):
            edge.press(key)
            expect(edge).to_have_attribute("aria-valuenow", edge.get_attribute(limit))
            assert abs(width(pane) - int(edge.get_attribute("aria-valuenow"))) <= 1
    page.set_viewport_size({"width": 1440, "height": 900})
    left.dblclick()
    right.focus()
    right.press("Enter")
    assert abs(width(explorer) - initial_left) < 2 and abs(width(inspector) - initial_right) < 2
    expect(prompt).to_have_value(draft)
    print(
        f"PASS independent side-panel drags increased native columns {initial_cols} to {expanded_cols}"
        " with both open; keyboard, toggle, reload, small desktop bounds and reset passed",
        flush=True,
    )


def exercise(page, session, repo):
    terminal = page.locator("#terminal-shell")
    composer = page.locator(".composer")
    explorer = page.locator("#explorer-pane")
    inspector = page.locator("#inspector-pane")
    divider = page.get_by_role(
        "separator", name="Resize Terminal and Composer", exact=True
    )
    left = page.locator("#toggle-explorer")
    right = page.locator("#toggle-inspector")
    prompt = page.locator("#prompt")

    def toggle(control, panel, showing):
        control.click()
        expect(control).to_have_attribute("aria-expanded", str(showing).lower())
        if showing:
            expect(panel).to_be_visible()
        else:
            expect(panel).to_be_hidden()

    page.locator("#start").click()
    wait_for(
        "initial Aider ready",
        lambda: (
            session.snapshot()["model"] == FIRST
            and session.snapshot()["input_state"] == "ready"
        ),
    )
    pid = session.proc.pid
    row = (
        page.locator("#tree").get_by_text("target.txt", exact=True).first.locator("..")
    )
    row.get_by_title("Add read-only").click()
    wait_for(
        "target file attached",
        lambda: any(
            r["path"] == "target.txt" and r["read_only"]
            for r in session.snapshot()["files"]["data"]
        ),
    )
    draft = "Keep this draft as I make room for the terminal."
    prompt.fill(draft)
    initial_width = geometry(terminal)["width"]
    initial_composer_width = geometry(composer)["width"]
    initial_cols, _ = check_pty(page, session, repo)
    toggle(left, explorer, False)
    expect(inspector).to_be_visible()
    left_width = geometry(terminal)["width"]
    assert (
        left_width > initial_width + 100
        and geometry(composer)["width"] > initial_composer_width + 100
    )
    wait_for("Explorer reclaimed terminal columns", lambda: session.cols > initial_cols)
    left_cols = session.cols
    toggle(right, inspector, False)
    full_width = geometry(terminal)["width"]
    assert full_width > left_width + 100
    wait_for(
        "Inspector reclaimed more terminal columns", lambda: session.cols > left_cols
    )
    wide_cols, _ = check_pty(page, session, repo)
    toggle(left, explorer, True)
    expect(inspector).to_be_hidden()
    assert initial_width < geometry(terminal)["width"] < full_width
    toggle(right, inspector, True)
    assert abs(geometry(terminal)["width"] - initial_width) < 2
    wait_for(
        "restoring both panels restores original column count",
        lambda: session.cols == initial_cols,
    )
    assert wide_cols > initial_cols and session.proc.pid == pid
    expect(prompt).to_have_value(draft)
    exercise_side_widths(page, session, repo, draft)

    # Keep one panel closed while changing and restoring the vertical divider.
    toggle(left, explorer, False)
    initial_height = geometry(terminal)["height"]
    composer_height = geometry(composer)["height"]
    _, initial_rows = check_pty(page, session, repo)
    bar = geometry(divider)
    x, y = bar["x"] + bar["width"] / 2, bar["y"] + bar["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y - 80, steps=10)
    page.mouse.up()
    dragged = geometry(terminal)["height"]
    assert dragged < initial_height - 45
    assert geometry(composer)["height"] > composer_height + 45
    wait_for(
        "dragging the divider resized ConPTY rows", lambda: session.rows < initial_rows
    )
    divider.focus()
    divider.press("ArrowDown")
    after_key = geometry(terminal)["height"]
    assert after_key > dragged
    divider.press("ArrowUp")
    assert abs(geometry(terminal)["height"] - dragged) < 2
    _, dragged_rows = check_pty(page, session, repo)
    assert dragged_rows < initial_rows
    expect(prompt).to_have_value(draft)

    page.reload()
    expect(left).to_have_attribute("aria-expanded", "false")
    expect(right).to_have_attribute("aria-expanded", "true")
    expect(explorer).to_be_hidden()
    expect(inspector).to_be_visible()
    expect(page.locator("#model")).to_have_text(FIRST)
    # Restoring the saved ratio follows the workspace notices and terminal fit.
    page.wait_for_function(
        "height => Math.abs(document.querySelector('#terminal-shell').getBoundingClientRect().height - height) < 3",
        arg=dragged,
    )
    assert session.proc.pid == pid
    expect(page.locator("#chat-files")).to_contain_text("target.txt")
    prompt.fill(draft)
    divider.press("Home")
    minimum = geometry(terminal)["height"]
    divider.press("End")
    assert minimum < dragged < geometry(terminal)["height"]
    divider.dblclick()
    assert abs(geometry(terminal)["height"] - initial_height) < 3
    toggle(right, inspector, False)
    page.locator("#model-badge").click()
    expect(inspector).to_be_visible()
    expect(right).to_have_attribute("aria-expanded", "true")
    expect(explorer).to_be_hidden()
    toggle(left, explorer, True)

    page.set_viewport_size({"width": 790, "height": 900})
    for edge in ("#explorer-divider", "#inspector-divider"):
        expect(page.locator(edge)).to_be_hidden()
    half_width = geometry(explorer)["width"]
    toggle(right, inspector, False)
    assert geometry(explorer)["width"] > half_width * 1.8
    toggle(right, inspector, True)
    page.set_viewport_size({"width": 390, "height": 850})
    for edge in ("#explorer-divider", "#inspector-divider"):
        expect(page.locator(edge)).to_be_hidden()
    for panel in (explorer, inspector):
        assert (
            geometry(panel)["y"]
            >= geometry(terminal)["y"] + geometry(terminal)["height"]
        )
    toggle(left, explorer, False)
    toggle(right, inspector, False)
    expect(left).to_be_visible()
    expect(right).to_be_visible()
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    toggle(right, inspector, True)
    toggle(left, explorer, True)
    expect(prompt).to_have_value(draft)
    assert session.proc.pid == pid
    print(
        "PASS independently hidden panes, real ConPTY size, draggable and keyboard divider, reload, tablet and phone",
        flush=True,
    )


def main():
    calls = []
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    with (
        tempfile.TemporaryDirectory(
            prefix="deck-layout-", ignore_cleanup_errors=True
        ) as tmp,
        serve(fixture_api(calls)) as endpoint,
    ):
        base = Path(tmp)
        home, repo = base / "home", base / "repo"
        for folder in (home, base / "prompts"):
            folder.mkdir()
        for key in list(os.environ):
            if key.startswith(("AIDER_", "OPENAI_", "ANTHROPIC_")) or "API_KEY" in key:
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
        repo_setup(repo, endpoint)
        with (repo / ".aider.conf.yml").open("a") as config:
            config.write("show-release-notes: false\n")
        (repo / "read_terminal_size.py").write_text(
            "import json, os\nfrom pathlib import Path\n"
            'with open("CONOUT$", "w") as console, Path("terminal-size.jsonl").open("a") as record:\n'
            '    record.write(json.dumps(os.get_terminal_size(console.fileno())) + "\\n")\n'
        )
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=os.getenv("DECK_CHROME"))
            with deck_browser(browser, base, repo) as (page, session):
                exercise(page, session, repo)
            browser.close()
        assert not calls, "Layout controls unexpectedly contacted a model"


if __name__ == "__main__":
    main()
