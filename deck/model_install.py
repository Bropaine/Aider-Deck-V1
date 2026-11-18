"""Check the selected main model against its actual local Ollama library."""

import hashlib
import json
from flask import Blueprint, abort, current_app, request

from . import aider_config as cfg
from . import reasoning_capabilities as capabilities
from .model_catalog import safe_model_id
from .ollama_routes import service_at
from .ollama_service import Offline, OllamaError, local_endpoint, validate_tag


install_bp = Blueprint("model_install", __name__, url_prefix="/api/<wid>/ollama")


def _selection(deck, wid, model, target):
    root = deck.workspace(wid)
    session = deck.session(wid)
    if target == "launch":
        options = cfg.launch_values(root)
        if model is None:
            model = options.get("model") or ""
        routes = capabilities.routing(root, options)
    elif target == "live":
        if session.snapshot()["status"] != "live":
            abort(409, "Start a live Aider session first.")
        routes = deck.reasoning_routes.get(wid)
        if routes is None:
            abort(409, "The live session has no recorded provider settings.")
    else:
        raise ValueError("Choose the live session or the next launch.")
    if not isinstance(model, str):
        raise ValueError("Enter a model ID.")
    resolved = routes.aliases.get(model, model)
    provider, tag, destination = capabilities.model_route(resolved, routes)
    key = [wid, target, model, resolved, provider, destination, session.attachments.generation]
    route = hashlib.sha256(json.dumps(key).encode()).hexdigest()
    return model, provider, tag, destination, route


def inspect_install(deck, wid, target, model=None, *, expected=None, action="check"):
    model, provider, tag, destination, route = _selection(deck, wid, model, target)
    if expected is not None and expected != route:
        abort(409, "The session, selected model or its endpoint changed. Check model availability again.")
    if action != "check" and expected is None:
        abort(409, "Check the selected model before managing this download.")
    result = dict(status="other", message="", model=model, route=route, blocked=False)
    if provider not in {"ollama", "ollama-openai"}:
        if action != "check":
            raise ValueError("Downloads require a local Ollama route.")
        return result
    try:
        if not destination or not destination.startswith("http://"):
            raise ValueError
        endpoint = local_endpoint(destination)
    except ValueError:
        if action != "check":
            raise ValueError("Deck can manage models only on a loopback Ollama endpoint.") from None
        result.update(status="unmanaged", message="Deck cannot check this Ollama endpoint's installed models.")
        return result
    protected = cfg.secret_values(deck.workspace(wid), strict=False)
    if not safe_model_id(tag, protected):
        raise ValueError("Enter an Ollama model tag without credentials or a URL.")
    tag = validate_tag(tag, strip_provider=False)
    result.update(tag=tag, endpoint=endpoint)
    service = service_at(endpoint)
    if action == "start":
        service.start()
    target_label = "Live session" if target == "live" else "Next launch"
    try:
        installed = service.installed_model(tag)
    except Offline:
        result.update(status="offline", blocked=True, message=f"{target_label} · Ollama is offline at {endpoint}.")
    except OllamaError:
        result.update(status="unavailable", blocked=True, message=f"{target_label} · Could not read installed models at {endpoint}.")
    else:
        if tag.lower().endswith((":cloud", "-cloud")) or (installed and installed["remote"]):
            result.update(status="cloud", message=f"{target_label} · {tag} uses Ollama's cloud runner.")
        elif installed:
            result.update(status="installed", message=f"{target_label} · {tag} is installed at {endpoint}.")
        else:
            result.update(status="missing", blocked=True,
                          message=f"{target_label} · {tag} is not installed at {endpoint}.")
    if action == "pull":
        if result["status"] == "missing":
            result["job"] = service.pull(tag, strip_provider=False)
        elif result["status"] != "installed":
            raise ValueError("Check this local Ollama library before downloading a model.")
    job = service.job()
    if job and job["model"] == tag and job["action"] == "pull":
        result["job"] = job
    return result


def require_installed(deck, wid, target, model=None, expected=None):
    result = inspect_install(deck, wid, target, model, expected=expected)
    if result["blocked"]:
        abort(409, result["message"] + " Open the main model setting to resolve this.")


@install_bp.post("/model-install")
def model_install(wid):
    deck = current_app.extensions["deck"]
    body = request.get_json()
    if not isinstance(body, dict) or body.get("action", "check") not in ("check", "start", "pull"):
        raise ValueError("Choose a model installation action.")
    model = body.get("model")
    root = deck.workspace(wid)
    protected = cfg.secret_values(root, strict=False)
    if model is not None and not safe_model_id(model, protected):
        raise ValueError("Enter a model ID without credentials, spaces, or a URL.")
    with deck.lock(wid):
        if "session" in body and body["session"] != deck.session(wid).attachments.generation:
            abort(409, "The Aider session changed. Check model availability again.")
        result = inspect_install(deck, wid, body.get("target", "launch"), model,
                                 expected=body.get("route"), action=body.get("action", "check"))
    # The endpoint comes from local_endpoint(), which admits only a literal
    # loopback origin. The general dotenv redactor can otherwise mask the
    # entire public address simply because it appeared in OLLAMA_API_BASE.
    protected = [value for value in protected if value != result.get("endpoint")]
    for key in ("message", "model", "tag"):
        if key in result:
            result[key] = cfg.replace_secrets(result[key], protected)
    if job := result.get("job"):
        for key in ("model", "message", "error"):
            if job.get(key):
                job[key] = cfg.replace_secrets(job[key], protected)
    return result
