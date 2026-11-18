"""Verify prompt, composer, file and raw-editor controls in an isolated Chromium session."""

import json
import logging
import os
import re
import tempfile
from pathlib import Path

from browser_acceptance import FIRST, KEY, fixture_api, repo_setup, serve, wait_for
from playwright.sync_api import expect, sync_playwright

from app import create_app


def button(scope, name):
    return scope.get_by_role("button", name=name, exact=True)


def file_post(item):
    request = item.request if hasattr(item, "request") else item
    return request.method == "POST" and request.url.endswith("/files")


def library_controls(page, folder):
    draft = page.locator("#prompt")
    dialog = page.locator("#dialog")
    old = "First prompt\nKeep its line breaks."
    new = "Replacement prompt\nWith another line."
    name = "Example.md"
    item = folder / name
    button(page, "Prompts").click()
    button(page, "Change folder").click()
    dialog.get_by_label("Folder path").fill(str(folder))
    button(dialog, "Save folder").click()
    expect(page.locator("#inspector-content")).to_contain_text(str(folder))

    def save():
        button(page, "Save composer as prompt").click()
        dialog.get_by_label("Prompt filename").fill(name)
        button(dialog, "Save").click()

    def preview(text):
        button(page, name).click()
        expect(dialog.locator("pre")).to_have_text(text)

    draft.fill(old)
    save()
    expect(button(page, name)).to_be_visible()
    assert item.read_text() == old
    draft.fill(new)
    preview(old)
    button(dialog, "Load into composer").click()
    button(dialog, "Cancel").click()
    expect(draft).to_have_value(new)
    preview(old)
    button(dialog, "Load into composer").click()
    button(dialog, "Load").click()
    expect(draft).to_have_value(old)
    draft.fill(new)
    save()
    button(dialog, "Cancel").click()
    assert item.read_text() == old
    save()
    button(dialog, "Replace").click()
    expect(dialog).not_to_be_visible()
    assert item.read_text() == new
    preview(new)
    button(dialog, "Delete").click()
    button(dialog, "Cancel").click()
    assert item.is_file()
    preview(new)
    button(dialog, "Delete").click()
    button(dialog, "Delete").click()
    expect(button(page, name)).to_have_count(0)
    assert not item.exists()
    print(
        "PASS library folder, save, preview, protected load/overwrite/delete with both decisions",
        flush=True,
    )


def raw_editors(page, repo):
    button(page, "Settings").click()
    dialog = page.locator("#dialog")
    models = ".aider.model.settings.yml"
    metadata = ".aider.model.metadata.json"
    for name, invalid, addition in [
        (models, "- name: [\n", f"\n- name: {FIRST}\n  edit_format: diff\n"),
        (metadata, '{"model": }', None),
    ]:
        file = repo / name
        before = file.read_text()
        if addition:
            updated = before + addition
        else:
            values = json.loads(before)
            values[FIRST]["max_input_tokens"] = 9100
            updated = json.dumps(values, indent=2) + "\n"
        button(page, name).click()
        field = dialog.get_by_role("textbox", name=name, exact=True)
        expect(field).to_have_value(before)
        field.fill(invalid)
        button(dialog, "Save file").click()
        expect(dialog.locator(".bad-text")).not_to_be_empty()
        expect(field).to_have_value(invalid)
        assert file.read_text() == before
        field.fill(updated)
        button(dialog, "Save file").click()
        expect(dialog).not_to_be_visible()
        assert file.read_text() == updated
        button(page, name).click()
        expect(field).to_have_value(updated)
        button(dialog, "Cancel").click()
        # Open a file that was already malformed before Deck read it.
        file.write_text(invalid)
        button(page, name).click()
        expect(dialog.locator(".bad-text")).not_to_be_empty()
        field.fill(updated)
        page.once("dialog", lambda confirm: confirm.dismiss())
        button(dialog, "Save file").click()
        assert file.read_text() == invalid
        page.once("dialog", lambda confirm: confirm.accept())
        button(dialog, "Save file").click()
        expect(dialog).not_to_be_visible()
        assert file.read_text() == updated
    print(
        "PASS JSON metadata and YAML model settings reject malformed drafts and round-trip good edits",
        flush=True,
    )


def composer_controls(page, calls):
    pref = page.get_by_label("Enter sends", exact=True)
    prompt = page.locator("#prompt")
    expect(pref).to_be_checked()
    pref.uncheck()
    prompt.fill("First panel message")
    prompt.press("End")
    prompt.press("Enter")
    expect(prompt).to_have_value("First panel message\n")
    prompt.press("End")
    prompt.press("x")
    first = "First panel message\nx"
    prompt.press("Shift+Enter")
    expect(prompt).to_have_value("")
    wait_for(
        "Shift+Enter sends the literal multiline prompt",
        lambda: any(first in q and k for q, k in calls),
    )
    page.reload()
    expect(pref).not_to_be_checked()
    pref.check()
    second = "Second panel message"
    prompt.fill(second)
    prompt.press("Shift+Enter")
    expect(prompt).to_have_value(second + "\n")
    prompt.fill(second)
    prompt.press("Enter")
    expect(prompt).to_have_value("")
    wait_for(
        "Enter sends when enabled", lambda: any(second in q and k for q, k in calls)
    )
    prompt.press("ArrowUp")
    expect(prompt).to_have_value(second)
    prompt.press("ArrowUp")
    expect(prompt).to_have_value(first)
    prompt.press("ArrowDown")
    expect(prompt).to_have_value(second)
    prompt.press("ArrowDown")
    expect(prompt).to_have_value("")
    prompt.fill("unsent draft")
    prompt.press("ArrowUp")
    expect(prompt).to_have_value("unsent draft")
    print(
        "PASS composer preferences persisted, both keyboard modes and two-entry history navigation",
        flush=True,
    )


def file_controls(page):
    tree = page.locator("#tree")
    chat = page.locator("#chat-files")
    tree.get_by_title("samples", exact=True).click()
    expect(tree.get_by_title("samples/one.py", exact=True)).to_be_visible()
    expect(tree.get_by_text("muted.py", exact=True)).to_have_count(0)
    page.get_by_label("Show ignored").check()
    for ignored in ("muted.py", "git-muted.py"):
        expect(
            tree.locator(".tree-row.ignored").get_by_text(ignored, exact=True)
        ).to_be_visible()
    page.get_by_label("Show ignored").uncheck()
    expect(tree.get_by_text("muted.py", exact=True)).to_have_count(0)
    folder = tree.locator(".tree-row").filter(
        has=page.get_by_title("samples", exact=True)
    )
    folder.get_by_title("Add all matching…").click()
    field = page.get_by_label("Glob pattern inside samples")
    field.fill("*.py")
    button(page, "Add matching").click()
    expect(chat).to_contain_text("one.py", timeout=16000)
    expect(chat).to_contain_text("two.py")
    expect(chat).not_to_contain_text("muted.py")
    path = page.get_by_label("File path or glob")
    path.fill("samples/*.txt")
    button(page, "Add files").click()
    expect(chat).to_contain_text("notes.txt", timeout=16000)
    expect(path).to_have_value("")
    path.fill("samples/*.missing")
    button(page, "Add files").click()
    expect(page.locator("#toast")).to_have_text("No matching files.")
    expect(path).to_have_value("samples/*.missing")
    button(page, ".aiderignore").click()
    dialog = page.locator("#dialog")
    dialog.get_by_role("textbox", name=".aiderignore", exact=True).fill(
        "muted.py\nnotes.txt\n"
    )
    button(dialog, "Save file").click()
    expect(dialog.get_by_text("Saved .aiderignore", exact=True)).to_be_visible()
    button(dialog, "Close").click()
    expect(tree.get_by_title("samples/notes.txt", exact=True)).to_have_count(0)
    page.get_by_label("Show ignored").check()
    expect(
        tree.locator(".tree-row.ignored").get_by_text("notes.txt", exact=True)
    ).to_be_visible()
    print(
        "PASS directory/path globs, unmatched pattern, immediate tree refresh after editing ignore rules",
        flush=True,
    )


def selected_file_controls(page, repo, session):
    tree = page.locator("#tree")
    chat = page.locator("#chat-files")
    add = button(page, "Add selected")
    read_only = button(page, "Add selected read-only")
    clear = button(page, "Clear selection")

    def choose(path):
        return tree.get_by_role("checkbox", name=f"Select {path}", exact=True)

    def chat_role(path, role):
        row = chat.locator(".file-row").filter(has_text=Path(path).name)
        expect(row.locator(".pill")).to_have_text(role, timeout=16000)

    tree.get_by_title("docs", exact=True).click()
    tree.get_by_title("source", exact=True).click()
    tree.get_by_title("docs/direct.md", exact=True).click()
    chat_role("docs/direct.md", "Editable")
    tree.locator(".tree-row").filter(
        has=page.get_by_title("source/single.txt", exact=True)
    ).get_by_title("Add read-only").click()
    chat_role("source/single.txt", "Read-only")
    print("PASS direct filename click adds editable and R adds read-only", flush=True)

    attempts = []

    def record(request):
        if file_post(request):
            attempts.append(request.post_data_json)

    page.on("request", record)
    editable = ["source/left file.py", "docs/right.md"]
    references = ["docs/reference.txt", "source/other.ini"]

    def assert_batch(index, action, paths):
        assert attempts[index]["action"] == action
        assert sorted(attempts[index]["paths"]) == sorted(paths)

    for path in editable:
        choose(path).check()
    clear.click()
    for path in editable:
        expect(choose(path)).not_to_be_checked()
    assert not attempts

    # Hold the native Aider prompt on a harmless, unfinished slash command.
    # A successful server response can precede a change to Aider's /ls readout.
    page.locator("#terminal .xterm-screen").click()
    page.keyboard.type("/ls")
    wait_for(
        "raw terminal draft holds queued file commands",
        lambda: session.snapshot()["input_state"] == "typing",
    )
    choose(editable[0]).check()
    tree.get_by_title("source", exact=True).click()
    expect(choose(editable[0])).not_to_be_visible()
    choose(editable[1]).check()
    expect(page.get_by_text("2 selected", exact=True)).to_be_visible()
    tree.get_by_title("source", exact=True).click()
    expect(choose(editable[0])).to_be_checked()
    expect(choose(editable[1])).to_be_checked()

    # Pause one request en route to the real server, then select another file.
    # The original acknowledgement must leave the newer selection intact.
    held = []
    page.route("**/files", lambda route: held.append(route), times=1)
    add.click()
    expect(add).to_be_disabled()
    expect(read_only).to_be_disabled()
    choose(references[0]).check()
    with page.expect_response(file_post) as response:
        held.pop().continue_()
    assert response.value.ok
    expect(choose(editable[0])).not_to_be_checked()
    expect(choose(editable[1])).not_to_be_checked()
    expect(choose(references[0])).to_be_checked()
    expect(chat.locator(".file-row").filter(has_text="left file.py")).to_have_count(0)
    page.locator("#terminal .xterm-screen").click()
    page.keyboard.press("Enter")
    for path in editable:
        chat_role(path, "Editable")
    assert len(attempts) == 1
    assert_batch(0, "add", editable)
    print(
        "PASS cross-folder editable batch and in-flight selection preserved until native /ls",
        flush=True,
    )

    # Make one selected entry stale, as when an external editor moves a file.
    # A declined batch should leave both checkboxes available for retry.
    choose(references[1]).check()
    missing = repo / references[1]
    holding = repo.parent / "temporarily-moved.ini"
    missing.rename(holding)
    try:
        with page.expect_response(file_post) as response:
            read_only.click()
        assert 400 <= response.value.status < 500
        expect(read_only).to_be_enabled()
        expect(page.locator("#file-selection-message")).to_contain_text(
            "Accepted 0 of 2 files."
        )
        for path in references:
            expect(choose(path)).to_be_checked()
            expect(
                chat.locator(".file-row").filter(has_text=Path(path).name)
            ).to_have_count(0)
        assert len(attempts) == 2
        assert_batch(1, "read-only", references)
    finally:
        holding.rename(missing)
    read_only.click()
    for path in references:
        chat_role(path, "Read-only")
        expect(choose(path)).not_to_be_checked()
    assert len(attempts) == 3
    assert_batch(2, "read-only", references)
    print(
        "PASS failed read-only batch retained both checks; retry attached both in one request",
        flush=True,
    )


DROP_FILES = {
    "drop one.py": False,
    "drop one.py.bak": False,
    "ref [1].md": True,
    "ref 1.md": False,
    "ref [1].md.notes": True,
    "single.md": False,
    "old left.c": False,
    "old right.h": True,
}


def drop_controls(page, repo, session, *, legacy=False):
    tree = page.locator("#tree")
    chat = page.locator("#chat-files")
    group = button(page, "Drop selected")
    files = list(DROP_FILES)
    contents = {name: (repo / name).read_bytes() for name in files}

    def label(name):
        return chat.locator(".file-name").filter(
            has_text=re.compile(r"(?:^|[\\/])" + re.escape(name) + "$")
        )

    def attached(name):
        tree_name = tree.get_by_title(name, exact=True)
        if DROP_FILES[name]:
            tree_name.locator("..").get_by_title("Add read-only").click()
        else:
            tree_name.click()
        expect(label(name)).to_have_count(1, timeout=15000)
        reported = label(name).inner_text()
        expect(label(name).locator("..").locator(".pill")).to_have_text(
            "Read-only" if DROP_FILES[name] else "Editable"
        )
        return reported

    def pick(reported):
        return chat.get_by_role(
            "checkbox", name=f"Select {reported} to drop", exact=True
        )

    def active():
        snapshot = session.snapshot()["files"]
        return (
            None
            if snapshot["stale"]
            else {(item["path"], item["read_only"]) for item in snapshot["data"]}
        )

    def without(start, *removed):
        wait_for(
            "Aider /ls retains every unselected attachment and its original role",
            lambda: active() == {item for item in start if item[0] not in removed},
        )

    posts = []

    def record(request):
        if file_post(request) and request.post_data_json["action"] == "drop":
            posts.append(request.post_data_json)

    page.on("request", record)
    if not legacy:
        reported = {name: attached(name) for name in files}
        baseline = active()
        # Individual row action must continue working alongside checkboxes.
        index = (
            chat.locator(".file-name").all_inner_texts().index(reported["single.md"])
        )
        button(chat, "Drop").nth(index).click()
        without(baseline, reported["single.md"])
        expect(label("single.md")).to_have_count(0)
        assert posts == [
            {"action": "drop", "path": reported["single.md"], "from_chat": True}
        ]
        targets = [reported["drop one.py"], reported["ref [1].md"]]
        for path in targets:
            pick(path).check()
        button(page, "Clear drop selection").click()
        expect(page.locator("#chat-selection-count")).to_have_text("0 selected")
        assert len(posts) == 1
        for path in targets:
            pick(path).check()
        expect(page.locator("#chat-selection-count")).to_have_text("2 selected")
        # A missing read-only file would require Aider's native substring
        # fallback and could drop its .notes sibling. Reject the entire batch.
        missing = repo / "ref [1].md"
        holding = repo.parent / "temporarily-moved-ref.md"
        missing.rename(holding)
        try:
            with page.expect_response(file_post) as rejected:
                group.click()
            assert rejected.value.status == 400
            without(baseline, reported["single.md"])
            for path in targets:
                expect(pick(path)).to_be_checked()
        finally:
            holding.rename(missing)
        with page.expect_response(file_post) as response:
            group.click()
        assert response.value.ok, response.value.text()
        without(baseline, reported["single.md"], *targets)
        for name in ("drop one.py", "ref [1].md"):
            expect(label(name)).to_have_count(0)
        assert len(posts) == 3 and all(
            sorted(body["paths"]) == sorted(targets) for body in posts[1:]
        )
        print(
            "PASS row Drop and one grouped removal preserved editable and read-only prefix/bracket siblings",
            flush=True,
        )
    else:
        # Older /drop implementations can substring-match a similar attached file.
        collision = attached("drop one.py")
        baseline = active()
        pick(collision).check()
        group.click()
        expect(page.locator("#chat-selection-message")).to_contain_text(
            "updated Deck server"
        )
        assert not posts and active() == baseline
        button(page, "Clear drop selection").click()
        index = chat.locator(".file-name").all_inner_texts().index(collision)
        button(chat, "Drop").nth(index).click()
        expect(page.locator("#chat-selection-message")).to_contain_text(
            "updated Deck server"
        )
        assert not posts and active() == baseline

        other = repo.parent / "other-workspace"
        page.locator("#workspace-picker").click()
        button(page.locator("#dialog"), str(other)).click()
        expect(page.locator("#workspace-path")).to_have_text(str(other))
        page.locator("#start").click()
        expect(page.locator("#status")).to_have_text("Live", timeout=15000)
        attached("old right.h")
        old_pid = session.proc.pid
        page.locator("#workspace-picker").click()
        button(page.locator("#dialog"), str(repo)).click()
        expect(page.locator("#workspace-path")).to_have_text(str(repo))
        expect(label("old left.c")).to_be_visible()
        targets = [label(name).inner_text() for name in ("old left.c", "old right.h")]
        for path in targets:
            pick(path).check()
        held = []
        page.route(
            "**/files", lambda route: held.append((route, route.fetch())), times=1
        )
        group.click()
        expect(group).to_be_disabled()
        page.locator("#workspace-picker").click()
        button(page.locator("#dialog"), str(other)).click()
        expect(page.locator("#workspace-path")).to_have_text(str(other))
        route, response = held.pop()
        assert response.ok
        with page.expect_response(file_post):
            route.fulfill(response=response)
        expect(label("old right.h")).to_be_visible()
        expect(page.locator("#chat-count")).to_have_text("1")
        page.locator("#workspace-picker").click()
        button(page.locator("#dialog"), str(repo)).click()
        expect(page.locator("#workspace-path")).to_have_text(str(repo))
        without(baseline, targets[0])
        expect(pick(targets[1])).to_be_checked()
        assert posts == [{"action": "drop", "path": targets[0], "from_chat": True}]
        with page.expect_response(file_post) as retried:
            group.click()
        assert retried.value.ok, retried.value.text()
        without(baseline, *targets)
        assert posts == [
            {"action": "drop", "path": path, "from_chat": True} for path in targets
        ]
        targets = [attached(name) for name in ("single.md", "old right.h")]
        baseline = active()
        for path in targets:
            pick(path).check()
        group.click()
        expect(label("old right.h")).to_have_count(0, timeout=15000)
        without(baseline, *targets)
        assert posts[2:] == [
            {"action": "drop", "path": path, "from_chat": True} for path in targets
        ]
        assert session.proc.pid == old_pid
        assert (other / "old right.h").read_text() == "Other workspace attachment\n"
        print(
            "PASS legacy drop refused collisions, paused on workspace switch, retried outstanding checks, and removed an uninterrupted mixed pair",
            flush=True,
        )
    assert {name: (repo / name).read_bytes() for name in files} == contents
    page.remove_listener("request", record)


def legacy_file_controls(page, repo, session):
    # Emulate the state response of an already-running Deck server while keeping
    # its real Aider process and scalar API. All file operations reach Flask.
    def legacy_state(route):
        response = route.fetch()
        state = response.json()
        state.pop("supports_file_groups", None)
        state.pop("supports_group_drop", None)
        route.fulfill(response=response, json=state)

    def cached_template(route):
        response = route.fetch()
        # The previous template predates the terminal question and mode status
        # rows. Bulk controls must also mount when these optional rows are absent.
        html = re.sub(
            r'<div class="terminal-question".*?</div>',
            "",
            response.text(),
            flags=re.DOTALL,
        )
        html = re.sub(r'<p id="mode-request".*?</p>', "", html, flags=re.DOTALL)
        assert 'id="terminal-question"' not in html and 'id="mode-request"' not in html
        route.fulfill(response=response, body=html)

    pid = session.proc.pid
    page.route(page.url, cached_template, times=1)
    page.route("**/state", legacy_state)
    page.reload()
    tree = page.locator("#tree")
    chat = page.locator("#chat-files")
    for folder in ("docs", "source"):
        tree.get_by_title(folder, exact=True).click()
    paths = ["docs/01.md", "docs/02.md", "source/03.ini"]

    def checkbox(path):
        return tree.get_by_role("checkbox", name="Select " + path, exact=True)

    attempts = []
    page.on(
        "request",
        lambda req: attempts.append(req.post_data_json) if file_post(req) else None,
    )
    for path in paths:
        checkbox(path).check()
    read_only = button(page, "Add selected read-only")
    missing = repo / paths[1]
    holding = repo.parent / "temporarily-moved.md"
    missing.rename(holding)
    try:
        read_only.click()
        expect(checkbox(paths[0])).not_to_be_checked()
        expect(read_only).to_be_enabled()
        expect(page.locator("#file-selection-message")).to_contain_text(
            "Accepted 1 of 3 files."
        )
        for path in paths[1:]:
            expect(checkbox(path)).to_be_checked()
        expect(
            chat.locator(".file-row").filter(has_text="01.md").locator(".pill")
        ).to_have_text("Read-only", timeout=16000)
        assert [a["path"] for a in attempts] == paths[:2]
    finally:
        holding.rename(missing)
    read_only.click()
    for path in paths[1:]:
        expect(
            chat.locator(".file-row").filter(has_text=Path(path).name).locator(".pill")
        ).to_have_text("Read-only", timeout=16000)
        expect(checkbox(path)).not_to_be_checked()
    assert [a["path"] for a in attempts] == paths[:2] + paths[1:]
    assert all(a["action"] == "read-only" and "paths" not in a for a in attempts)
    print(
        "PASS old-server scalar read-only selection stops at failure and retries only remaining files",
        flush=True,
    )

    # Accept the first file on the old workspace, then switch workspaces while
    # the browser is awaiting that HTTP acknowledgement. No subsequent scalar
    # request should reach either workspace.
    for path in paths[:2]:
        checkbox(path).check()
    held = []

    def hold_ack(route):
        held.append((route, route.fetch()))

    page.route("**/files", hold_ack, times=1)
    button(page, "Add selected").click()
    expect(button(page, "Add selected")).to_be_disabled()
    alternate = repo.parent / "other-workspace"
    page.locator("#workspace-picker").click()
    dialog = page.locator("#dialog")
    expect(dialog.get_by_text("Folders", exact=True)).to_be_visible()
    dialog.get_by_label("Workspace folder").fill(str(alternate))
    button(dialog, "Open workspace").click()
    expect(page.locator("#workspace-path")).to_have_text(str(alternate))
    route, response = held.pop()
    assert response.ok
    with page.expect_response(file_post):
        route.fulfill(response=response)
    assert [a["path"] for a in attempts[4:]] == paths[:1]
    assert attempts[4]["action"] == "add" and "paths" not in attempts[4]
    expect(page.locator("#file-selection-count")).to_have_text("0 selected")
    page.locator("#workspace-picker").click()
    button(dialog, str(repo)).click()
    expect(page.locator("#workspace-path")).to_have_text(str(repo))
    tree.get_by_title("docs", exact=True).click()
    expect(checkbox(paths[0])).not_to_be_checked()
    expect(checkbox(paths[1])).to_be_checked()
    expect(page.locator("#file-selection-message")).to_contain_text("Accepted 1 of 2")
    for path, role in zip(paths[:2], ["Editable", "Read-only"], strict=True):
        expect(
            chat.locator(".file-row").filter(has_text=Path(path).name).locator(".pill")
        ).to_have_text(role, timeout=16000)
    assert session.proc.pid == pid
    print(
        "PASS changing workspaces prevents further scalar submissions and preserves the Aider PID",
        flush=True,
    )


def main():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    calls = []
    with (
        tempfile.TemporaryDirectory(
            prefix="deck-panels-", ignore_cleanup_errors=True
        ) as tmp,
        serve(fixture_api(calls)) as endpoint,
    ):
        base = Path(tmp)
        repo, home, original, folder = [
            base / part for part in ("repo", "home", "prompts", "new library")
        ]
        for directory in (home, original, folder):
            directory.mkdir()
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
        git = repo_setup(repo, endpoint)
        other = base / "other-workspace"
        other_git = repo_setup(other, endpoint)
        (other / "old right.h").write_text("Other workspace attachment\n")
        other_git("add", "old right.h")
        other_git("commit", "-qm", "Other workspace sentinel")
        for root in (repo, other):
            with (root / ".aider.conf.yml").open("a") as config:
                config.write("show-release-notes: false\n")
        (repo / ".env").write_text(
            f"OPENAI_API_BASE={endpoint}/v1\nOPENAI_API_KEY={KEY}\n"
        )
        samples = repo / "samples"
        samples.mkdir()
        for name in ["one.py", "two.py", "notes.txt", "muted.py", "git-muted.py"]:
            (samples / name).write_text("# Example\n")
        with (repo / ".gitignore").open("a") as f:
            f.write("git-muted.py\n")
        (repo / ".aiderignore").write_text("muted.py\n")
        for path in (
            "docs/direct.md",
            "source/single.txt",
            "source/left file.py",
            "docs/right.md",
            "docs/reference.txt",
            "source/other.ini",
            "docs/01.md",
            "docs/02.md",
            "source/03.ini",
        ):
            file = repo / path
            file.parent.mkdir(exist_ok=True)
            file.write_text("# Batch attachment fixture\n")
        git("add", ".gitignore", "samples", "docs", "source")
        for name in DROP_FILES:
            (repo / name).write_text(f"Attachment fixture: {name}\n")
        git("--literal-pathspecs", "add", "--", *DROP_FILES)
        git("commit", "-qm", "Scratch file tree")
        app = create_app(base / "state.json", original)
        client = app.test_client()
        wid = client.post("/api/workspaces", json={"path": str(repo)}).json["id"]
        session = app.extensions["deck"].session(wid)
        try:
            with serve(app) as url, sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=os.getenv("DECK_CHROME"))
                page = browser.new_page(viewport={"width": 1500, "height": 960})
                page.set_default_timeout(10000)
                page.route("https://cdn.jsdelivr.net/**", lambda route: route.abort())
                page.goto(url)
                library_controls(page, folder)
                raw_editors(page, repo)
                page.locator("#start").click()
                wait_for(
                    "real Aider ready",
                    lambda: session.snapshot()["input_state"] == "ready",
                )
                composer_controls(page, calls)
                file_controls(page)
                selected_file_controls(page, repo, session)
                drop_controls(page, repo, session)
                legacy_file_controls(page, repo, session)
                drop_controls(page, repo, session, legacy=True)
                page.unroute_all(behavior="wait")
                page.close()
                browser.close()
        finally:
            app.extensions["deck"].close()


if __name__ == "__main__":
    main()
