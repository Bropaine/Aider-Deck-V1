"""Local previews and per-repository setup for the native Git pre-push check."""

import hashlib

from flask import Blueprint, abort, current_app, request

from . import git_service as git
from . import push_check, staged_scan
from .scan_values import workspace_values


safety_bp = Blueprint("git_safety", __name__, url_prefix="/api/<wid>/git/safety")


def _revision(root):
    # The index can change even if the list of staged filenames stays identical.
    index = git.run(root, "ls-files", "--stage", "-z")
    head = git.run(root, "rev-parse", "--verify", "--quiet", "HEAD", ok=(0, 1))
    upstream = git.run(root, "rev-parse", "--verify", "--quiet", "@{upstream}", ok=(0, 1, 128))
    return hashlib.sha256("\0".join((index, head, upstream)).encode()).hexdigest()


def _hook(root):
    status = push_check.hook_status(root)
    return {**status, "can_enable": status["available"] and not status["enabled"]}


@safety_bp.get("")
def status(wid):
    root = git.repository(current_app.extensions["deck"].workspace(wid))
    return {"hook": _hook(root), "revision": _revision(root)}


@safety_bp.post("/check")
def check(wid):
    workspace = current_app.extensions["deck"].workspace(wid)
    root = git.repository(workspace)
    body = request.get_json()
    if not isinstance(body, dict) or body.get("scope") not in ("staged", "outgoing"):
        abort(400, "Choose staged files or outgoing commits.")
    scope = body["scope"]
    revision = _revision(root)
    values = workspace_values(workspace)
    if scope == "staged":
        result = staged_scan.scan_staged(root, values)
        result["coverage"] = "Staged file contents compared with the current commit."
    else:
        result = push_check.preview(root, known_values=values)
    if _revision(root) != revision:
        abort(409, "The staged files or branch changed during the check. Run it again.")
    return {**result, "scope": scope, "revision": revision}


@safety_bp.post("/enable")
def enable(wid):
    deck = current_app.extensions["deck"]
    root = git.repository(deck.workspace(wid))
    with deck.lock(wid):
        push_check.install_hook(root)
    return {"hook": _hook(root), "revision": _revision(root)}
