"""Workspace bookmarks and explicit use of a locally managed Ollama model."""

import os
from contextlib import contextmanager

from flask import Blueprint, abort, current_app, request

from . import aider_config as cfg
from . import envfile
from .reasoning_capabilities import model_route
from .ollama_service import OllamaService, local_endpoint, validate_tag


ollama_bp = Blueprint("ollama", __name__, url_prefix="/api/<wid>/ollama")
ROLES = ("model", "weak-model", "editor-model")


def _deck():
    return current_app.extensions["deck"]


def _body():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        abort(400, "Expected a JSON object.")
    return value


def _service(root):
    env, _ = cfg.provider_environment(root)
    endpoint = local_endpoint(
        env.values.get("OLLAMA_API_BASE") or os.getenv("OLLAMA_HOST")
    )
    return service_at(endpoint)


def service_at(endpoint):
    with _deck().guard:
        services = current_app.extensions["ollama_services"]
        if endpoint not in services:
            services[endpoint] = OllamaService(endpoint)
        return services[endpoint]


def live_context(snapshot, routes):
    model = snapshot.get("model") or ""
    if snapshot.get("status") != "live" or not model or routes is None:
        return None
    provider, tag, endpoint = model_route(routes.aliases.get(model, model), routes)
    if provider not in ("ollama", "ollama-openai"):
        return None
    result = {"model": model, "window": None}
    try:
        if endpoint and endpoint.startswith("http://"):
            service = service_at(local_endpoint(endpoint))
            result["window"] = service.context_window(tag)
            if result["window"] is None:
                result.update(service.model_context_limit(tag))
    except ValueError:
        # Unrecognized or unavailable routes cannot supply local runner details.
        pass
    return result


@ollama_bp.get("")
def status(wid):
    deck = _deck()
    root = deck.workspace(wid)
    return {**_service(root).status(), "saved": deck.state.ollama_tags(wid)}


@ollama_bp.route("/tags", methods=["POST", "DELETE"])
def tags(wid):
    deck = _deck()
    deck.workspace(wid)
    tag = validate_tag(_body().get("tag"))
    return {
        "saved": deck.state.ollama_tags(wid, tag, remove=request.method == "DELETE")
    }


@ollama_bp.post("/start")
def start(wid):
    deck = _deck()
    return {
        **_service(deck.workspace(wid)).start(),
        "saved": deck.state.ollama_tags(wid),
    }


@ollama_bp.post("/pull")
def pull(wid):
    tag = validate_tag(_body().get("tag"))
    return _service(_deck().workspace(wid)).pull(tag), 202


@contextmanager
def _local_configuration(root, model, endpoint):
    """Stage workspace settings and restore the original bytes if launch fails."""
    paths = [envfile.workspace_file(root, name) for name in (cfg.CONF, ".env")]
    originals = [path.read_bytes() if path.exists() else None for path in paths]
    conf, dotenv = [envfile.read_text(path) for path in paths]
    roles = dict.fromkeys(ROLES, model)
    environment = cfg.aider_environment(root)
    overrides = {"OLLAMA_API_BASE": endpoint}
    for role in roles:
        key = "AIDER_" + role.upper().replace("-", "_")
        if environment.values.get(key) and environment.values[key] != model:
            overrides[key] = model
    contents = [cfg.update_yaml(conf, roles), envfile.updated_values(dotenv, overrides)]
    written = []
    try:
        for path, original, new in zip(paths, originals, contents):
            encoded = new.encode("utf-8")
            if encoded == original:
                continue
            if (path.read_bytes() if path.exists() else None) != original:
                abort(409, "Workspace settings changed. Review them and try again.")
            envfile.atomic_write(path, new)
            written.append((path, original, encoded))
        effective = cfg.launch_values(root)
        provider, _ = cfg.provider_environment(root)
        if any(effective.get(role) != model for role in ROLES):
            raise ValueError(
                "A higher-priority Aider model setting overrides this selection. Check additional dotenv files."
            )
        if local_endpoint(provider.values.get("OLLAMA_API_BASE")) != endpoint:
            raise ValueError(
                "Another Aider setting overrides OLLAMA_API_BASE. Check additional dotenv files and set-env."
            )
        yield
    except BaseException:
        for path, original, written_bytes in reversed(written):
            # Another Deck instance or the owner's editor may have changed the file.
            if path.exists() and path.read_bytes() == written_bytes:
                if original is None:
                    path.unlink()
                else:
                    envfile.atomic_write(path, original.decode("utf-8"))
        raise


@ollama_bp.post("/use")
def use(wid):
    body = _body()
    tag = validate_tag(body.get("tag"))
    deck = _deck()
    root = deck.workspace(wid)
    service = _service(root)
    active = current_app.extensions["ollama_activations"]
    with deck.lock(wid):
        original = deck.session(wid)
        proc = original.proc
        live = original.snapshot()["status"] == "live"
        if live and body.get("restart_confirmed") is not True:
            abort(
                409,
                "Confirm restarting the running Aider session before changing models.",
            )
        fingerprint = cfg.config_fingerprint(root)
        with deck.guard:
            if wid in active:
                abort(409, "A local model is already being started for this workspace.")
            active.add(wid)
    try:
        job = service.load(tag)
        # Allow the daemon's cold-load timeout plus its startup and status checks.
        result = service.wait(job["id"], timeout=660)
        if result["state"] != "succeeded":
            raise ValueError(result.get("error") or result["message"])
        model = "ollama_chat/" + tag
        with deck.lock(wid):
            if (
                deck.session(wid) is not original
                or original.proc is not proc
                or cfg.config_fingerprint(root) != fingerprint
            ):
                abort(
                    409,
                    "The workspace or session changed while loading. Review it and try again.",
                )
            with _local_configuration(root, model, service.endpoint):
                deck.start(wid, restart=live, expected_model=model)
        return {"model": model, "status": "live"}
    finally:
        with deck.guard:
            active.discard(wid)
