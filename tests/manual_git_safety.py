"""Exercise Deck's Git safety controls and an actual push to a temporary local remote."""

import logging
import os
from pathlib import Path
import secrets
import subprocess
import tempfile

from browser_acceptance import serve
from playwright.sync_api import expect, sync_playwright

from app import create_app


def git(root, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=check
    )


def open_git(page, url):
    page.goto(url, wait_until="domcontentloaded")
    page.get_by_role("button", name="Git", exact=True).click()
    return page.locator("#git-safety")


def main():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    with tempfile.TemporaryDirectory(prefix="deck-browser-git-") as temp:
        base = Path(temp)
        home, repo, remote = (base / name for name in ("home", "workspace", "remote.git"))
        for folder in (home, repo, remote):
            folder.mkdir()
        for key in list(os.environ):
            if key.startswith(("AIDER_", "GIT_", "OPENAI_")):
                os.environ.pop(key)
        os.environ.update(
            HOME=str(home), USERPROFILE=str(home), GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", NO_PROXY="127.0.0.1,localhost",
        )
        git(remote, "init", "--bare", "-q")
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.name", "Deck Test")
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "remote", "add", "origin", str(remote))
        (repo / ".gitignore").write_text(".env\n")
        git(repo, "add", ".gitignore")
        git(repo, "commit", "-qm", "Baseline")
        git(repo, "push", "-qu", "origin", "main")
        baseline = git(remote, "rev-parse", "refs/heads/main").stdout.strip()
        sample = "gh" + "p_" + secrets.token_hex(18)
        (repo / ".env").write_text("OPENAI_API_KEY=" + sample + "\n")
        public = repo / "guide.txt"
        public.write_text("Example\n" + sample + "\n")
        git(repo, "add", "guide.txt")
        public.write_text("Sanitized example\n")
        app = create_app(base / "state.json", base / "prompts")
        client = app.test_client()
        client.post("/api/workspaces", json={"path": str(repo)})
        try:
            with serve(app) as url, sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=os.getenv("DECK_CHROME"))
                page = browser.new_page(viewport={"width": 1300, "height": 900})
                page.set_default_timeout(20000)
                section = open_git(page, url)
                result = section.locator("#git-safety-result")
                expect(section).to_contain_text("pre-push check is off")
                section.get_by_role("button", name="Check before push").click()
                expect(result).to_contain_text("No outgoing commits")
                section.get_by_role("button", name="Check staged files").click()
                expect(result).to_contain_text("guide.txt:2")
                assert sample not in page.locator("body").inner_text()

                section.get_by_role("button", name="Enable pre-push check").click()
                expect(section).to_contain_text("Automatic pre-push check enabled")
                page.get_by_label("Commit message", exact=True).fill("Introduce scratch example")
                page.get_by_role("button", name="Commit staged", exact=True).click()
                expect(page.get_by_role("button", name="Commit staged", exact=True)).to_be_disabled()
                introduced = git(repo, "rev-parse", "HEAD").stdout.strip()
                git(repo, "add", "guide.txt")
                git(repo, "commit", "-qm", "Clean up scratch example")
                section.get_by_role("button", name="Check before push").click()
                expect(result).to_contain_text("guide.txt:2", timeout=20000)
                expect(result).to_contain_text(introduced[:7])
                assert sample not in page.locator("body").inner_text()
                rejected = git(repo, "push", "origin", "main", check=False)
                assert rejected.returncode and sample not in (rejected.stdout + rejected.stderr)
                assert git(remote, "rev-parse", "refs/heads/main").stdout.strip() == baseline

                # Restaging a changed file with the same name changes its index blob.
                public.write_text("Third snapshot\n")
                git(repo, "add", "guide.txt")
                section.get_by_role("button", name="Check staged files").click()
                expect(result).to_contain_text("No potential secrets found", timeout=20000)
                public.write_text("Fourth snapshot\n")
                git(repo, "add", "guide.txt")
                expect(result).to_contain_text("Git changed since", timeout=20000)
                section.get_by_role("button", name="Check before push").click()
                expect(result).to_contain_text("guide.txt:2")

                other = base / "second"
                other.mkdir()
                git(other, "init", "-q")
                git(other, "config", "core.hooksPath", ".project-hooks")
                page.get_by_label("Commit message", exact=True).fill("Only for the first repository")
                page.locator("#workspace-picker").click()
                dialog = page.get_by_role("dialog")
                dialog.get_by_label("Workspace folder").fill(str(other))
                dialog.get_by_role("button", name="Open workspace", exact=True).click()
                expect(section).to_contain_text("custom Git hooks path")
                expect(section.get_by_role("button", name="Enable pre-push check")).to_be_disabled()
                expect(result).to_be_empty()
                expect(page.locator("#workspace-path")).to_have_text(str(other))
                expect(page.get_by_label("Commit message", exact=True)).to_have_value("")

                # When an older Deck server cannot serve the safety endpoints,
                # its normal Git controls remain accessible.
                page.route("**/git/safety", lambda route: route.fulfill(status=404, json={}))
                section = open_git(page, url)
                expect(section).to_contain_text("Open an updated Deck server")
                expect(section.get_by_role("button", name="Check before push")).to_be_disabled()
                expect(page.get_by_role("button", name="Working diff", exact=True)).to_be_visible()
                browser.close()
        finally:
            app.extensions["deck"].close()
    print("PASS staged and historical findings, clean scan, enabled hook, blocked local push, stale results, workspace switch, custom hooks and older server")


if __name__ == "__main__":
    main()
