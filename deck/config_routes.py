"""Workspace-scoped configuration API. Responses never include stored credentials."""
import hmac
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, request

from . import aider_config as cfg
from . import envfile as env
from . import reasoning_capabilities as effort
from .model_install import require_installed
from .registry import FORMATS, REGISTRY
from .model_catalog import catalog_link, model_access, model_candidates, safe_model_id

config_bp = Blueprint("configuration", __name__, url_prefix="/api/<wid>/config")


def picker_models(wid, current, protected, access, action=None, model=None, enabled=None):
    seed = {current: not access.key_warning(current)} if safe_model_id(current, protected) else {}
    if action == "save":
        enabled = not access.key_warning(model)
    if action == "visibility" and enabled and (warning := access.key_warning(model)):
        raise ValueError(warning)
    models = deck().state.saved_models(wid, seed, action, model, enabled)
    rows = []
    for name, shown in models.items():
        if safe_model_id(name, protected):
            warning = access.key_warning(name)
            rows.append(dict(name=name, enabled=shown and not warning, key_warning=warning))
    return rows


def deck():
    return current_app.extensions["deck"]


def body():
    result = request.get_json(silent=True)
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object.")
    return result


def protect(wid, root):
    session = deck().session(wid)
    if session and hasattr(session, "protect"):
        session.protect(cfg.secret_values(root, strict=False))


@config_bp.errorhandler(ValueError)
def invalid(exc):
    message = str(exc)
    try:
        root = deck().workspace(request.view_args["wid"])
        message = cfg.replace_secrets(message, cfg.secret_values(root, strict=False))
    except (KeyError, ValueError, OSError):
        message = "Unable to read the workspace configuration."
    return jsonify(error=message), 400


@config_bp.errorhandler(OSError)
def inaccessible(exc):
    return jsonify(error="Unable to read or write the workspace configuration."), 400


@config_bp.get("")
def configuration(wid):
    root = deck().workspace(wid)
    issues = []
    try:
        values = cfg.effective(root)
    except ValueError as exc:
        issues.append(str(exc))
        values = {key: (None, "Unknown", None) for key in REGISTRY}
    secrets_list = cfg.secret_values(root, strict=False)
    session = deck().session(wid)
    snapshot = session.snapshot() if session else {}
    flags = snapshot.get("launch") or {}
    rows = []
    for key, definition in REGISTRY.items():
        configured, source, source_path = values[key]
        actual = flags.get(key, configured)
        if env.is_sensitive(key):
            configured, actual = env.mask(configured), env.mask(actual)
        elif isinstance(configured, str):
            configured = cfg.replace_secrets(configured, secrets_list)
            if isinstance(actual, str):
                actual = cfg.replace_secrets(actual, secrets_list)
        elif isinstance(configured, list):
            configured = [cfg.replace_secrets(str(v), secrets_list) for v in configured]
            if key not in flags:
                actual = configured
        row = {**definition, "default": cfg.DECK_DEFAULTS.get(key, definition["default"]),
               "aider_default": definition["default"]}
        rows.append(dict(**row, value=actual, source="Launch flag" if key in flags else source,
                         source_path=None if key in flags else source_path, configured=configured,
                         configured_source=source, configured_path=source_path))
    model = snapshot.get("model") or values["model"][0]
    try:
        registered = cfg.registration(root, model, {k: v[0] for k, v in values.items()})
        if registered.get("format_error"):
            issues.append(registered["format_error"])
    except ValueError as exc:
        issues.append(str(exc))
        registered = dict(model=model, registered=None, edit_format=None, path=None)
    try:
        saved = picker_models(wid, values["model"][0], secrets_list, model_access(root))
    except (OSError, ValueError):
        saved = []
        issues.append("Repair the workspace configuration to load saved models.")
    return jsonify(rows=rows, launch=flags, presets=list(cfg.presets()), formats=FORMATS,
                   saved_models=saved,
                   issues=[cfg.replace_secrets(issue, secrets_list) for issue in issues], registration=registered,
                   first_run=not env.workspace_file(root, cfg.CONF).exists())


@config_bp.route("/saved-models", methods=["GET", "POST"])
def saved_models(wid):
    root = deck().workspace(wid)
    if request.method == "GET":
        with deck().lock(wid):
            current = cfg.effective(root)["model"][0]
            protected = cfg.secret_values(root, strict=False)
            return jsonify(saved_models=picker_models(wid, current, protected, model_access(root)))
    args = body()
    action, model, enabled = args.get("action"), args.get("model"), args.get("enabled")
    if action not in ("save", "visibility", "remove"):
        raise ValueError("Choose a saved model action.")
    if action == "visibility" and not isinstance(enabled, bool):
        raise ValueError("Choose whether to show this model in the picker.")
    with deck().lock(wid):
        protected = cfg.secret_values(root, strict=False)
        if not safe_model_id(model, protected):
            raise ValueError("Enter a model ID without credentials, spaces, or a URL.")
        current = cfg.effective(root)["model"][0]
        models = picker_models(wid, current, protected, model_access(root), action, model, enabled)
    return jsonify(saved_models=models)


def effort_target(wid, root, target, expected_model):
    options = None
    if target == "launch":
        options = cfg.launch_values(root)
        model = str(options.get("model") or "")
        routes = effort.routing(root, options)
    elif target == "live":
        session = deck().session(wid)
        snapshot = session.snapshot() if session else {}
        if snapshot.get("status") != "live" or not snapshot.get("model"):
            abort(409, "Wait for Aider to announce its main model.")
        model = snapshot["model"]
        routes = deck().reasoning_routes.get(wid)
        if routes is None:
            abort(409, "The running session has no recorded provider settings.")
    else:
        raise ValueError("Choose the live session or the next launch.")
    if expected_model is not None and expected_model != model:
        abort(409, "The selected main model changed. Review its reasoning effort again.")
    return model, routes, options


@config_bp.get("/reasoning-effort")
def reasoning_effort(wid):
    root = deck().workspace(wid)
    target = request.args.get("target", "launch")
    with deck().lock(wid):
        model, routes, options = effort_target(wid, root, target, request.args.get("model"))
        guard = cfg.reasoning_guard(root, model, options) if options is not None else None
    result = effort.discover(model, routes, resolve_alias=target == "launch")
    if result["source"].startswith("Ollama"):
        result["catalog"] = catalog_link(result["resolved_model"], routes)
    secrets = cfg.secret_values(root, strict=False)
    if guard is not None:
        guard["message"] = cfg.replace_secrets(guard.get("message") or "", secrets)
        result["launch_guard"] = guard
    for key in ("model", "resolved_model"):
        result[key] = cfg.replace_secrets(result[key], secrets)
    if any(cfg.replace_secrets(level, secrets) != level for level in result["levels"]):
        result.update(status="unknown", levels=[], message="A protected value appears in this model’s metadata.")
    return jsonify(result)


@config_bp.post("/setting")
def setting(wid):
    args = body()
    key = args.get("key")
    if not isinstance(key, str) or key not in REGISTRY or key == "check-update":
        raise ValueError("Choose an editable setting.")
    value = cfg.normalize(key, args.get("value"))
    root = deck().workspace(wid)
    with deck().lock(wid):
        live = args.get("live") is True
        if key == "model":
            protected = cfg.secret_values(root, strict=False)
            if value is not None and not safe_model_id(args.get("value"), protected):
                raise ValueError("Enter a model ID without credentials, spaces, or a URL.")
        if key == "reasoning-effort":
            model, routes, _ = effort_target(wid, root, "live" if live else "launch", args.get("model"))
            if value is not None:
                capabilities = effort.discover(model, routes, resolve_alias=not live)
                if capabilities["status"] == "unsupported":
                    raise ValueError("This model has no selectable reasoning effort levels.")
                if capabilities["status"] == "known" and value not in capabilities["levels"]:
                    raise ValueError("Choose one of this model's listed reasoning effort levels.")
        if live:
            command = REGISTRY[key]["live"]
            if not command or value is None:
                raise ValueError("This change requires a session restart.")
            if re.search(r"[\x00-\x1f\x7f]", str(value)):
                raise ValueError("Live commands require a single line of text.")
            session = deck().session(wid)
            if not session:
                raise ValueError("Start a session before applying a live setting.")
            line = command + " " + str(value)
            if key == "reasoning-effort":
                session.command(line, expected_model=model)
            else:
                if key == "model":
                    require_installed(deck(), wid, "live", value, expected=args.get("install_route"))
                session.command(line)
            return jsonify(sent=True)
        path = env.workspace_file(root, cfg.CONF)
        if env.is_sensitive(key) and value is None:
            return jsonify(saved=False)
        changes = {key: value}
        if key in ("dark-mode", "light-mode") and (value or value is None):
            opposite = "light-mode" if key == "dark-mode" else "dark-mode"
            changes[opposite] = None
        old = env.read_text(path)
        new = cfg.update_yaml(old, changes, remove_defaults=True)
        if key == "model":
            previous = cfg.effective(root)["model"][0]
            original = path.read_bytes() if path.exists() else None
        if old != new:
            env.atomic_write(path, new)
        if key == "model":
            try:
                picker_models(wid, previous, protected, model_access(root), "save" if value else "initialize", value)
            except (OSError, ValueError):
                if old != new and path.exists() and env.read_text(path) == new:
                    if original is not None:
                        env.atomic_write(path, original.decode("utf-8"))
                    else:
                        path.unlink()
                raise
        protect(wid, root)
    return jsonify(saved=True, removed=key not in cfg.parse(new, cfg.CONF))


@config_bp.post("/preview")
def preview(wid):
    with deck().lock(wid):
        _, _, data = cfg.preview(deck().workspace(wid), body())
    return jsonify(data)


@config_bp.post("/apply")
def apply_preview(wid):
    root = deck().workspace(wid)
    args = body()
    with deck().lock(wid):
        path, new, data = cfg.preview(root, args)
        if not isinstance(args.get("token"), str) or not hmac.compare_digest(args["token"], data["token"]):
            return jsonify(error="Preview changed or expired. Review the new diff before applying it."), 409
        env.atomic_write(path, new)
        protect(wid, root)
    return jsonify(saved=True, file=path.name)


@config_bp.route("/raw/<name>", methods=["GET", "PUT"])
def raw(wid, name):
    if name not in cfg.RAW_FILES:
        raise ValueError("Unsupported raw configuration file.")
    root = deck().workspace(wid)
    with deck().lock(wid):
        path = env.workspace_file(root, name)
        if request.method == "PUT":
            args = body()
            cfg.save_raw(root, name, args.get("text"), args.get("revision"), args.get("replace_invalid") is True)
            protect(wid, root)
        original = env.read_text(path)
        revision = cfg.digest(path, original)
        try:
            safe = cfg.raw_view(root, name, original)[0]
        except ValueError as exc:
            # Parsing is a prerequisite for masking arbitrary nested secrets.
            # Never stream unparsed configuration. Offer a revision-bound replacement.
            return jsonify(text="", revision=revision, exists=path.exists(), invalid=True,
                           error=f"{exc} Review the original locally, then enter the full replacement.")
        return jsonify(text=safe, revision=revision, exists=path.exists(), invalid=False)


@config_bp.route("/keys", methods=["GET", "POST"])
def keys(wid):
    root = deck().workspace(wid)
    with deck().lock(wid):
        if request.method == "POST":
            env.write_values(root, body().get("values"))
            protect(wid, root)
        issues = []
        try:
            effective_env, overrides = cfg.provider_environment(root)
        except ValueError:
            issues.append("Aider's YAML provider settings could not be read. Showing dotenv and process values.")
            effective_env, overrides = None, set()
        rows = env.key_rows(root, effective_env)
        for row in rows:
            row["aider_override"] = row["name"] in overrides
        return jsonify(keys=rows, issues=issues)


@config_bp.post("/keys/verify")
def verify(wid):
    name = body().get("name")
    if not isinstance(name, str) or not env.NAME.fullmatch(name):
        raise ValueError("Invalid environment name.")
    values, _ = cfg.provider_environment(deck().workspace(wid))
    return jsonify(check="presence + non-empty check only", name=name,
                   present=bool(values.values.get(name)))


def aider_info(root, option):
    binary = shutil.which("aider")
    if not binary:
        raise ValueError("Aider is not on the server PATH.")
    # Informational commands must not inherit interactive launch actions or log
    # credentials. Supply only sanitized configuration and an empty dotenv.
    with tempfile.TemporaryDirectory(prefix="deck-info-") as temp:
        empty = Path(temp) / "empty.yml"
        empty.write_text("{}\n", encoding="utf-8")
        dotenv = Path(temp) / ".env"
        dotenv.touch()
        process_env = {k: v for k, v in os.environ.items()
                       if not k.startswith("AIDER_") and not env.is_sensitive(k)}
        # --config and --env-file add a file to Aider's normal search chain.
        # Isolate the home directory as well, so passive lookups cannot execute
        # an owner's unrelated home launch options or load their credentials.
        process_env.update(USERPROFILE=temp, HOME=temp, LITELLM_LOCAL_MODEL_COST_MAP="True")
        argv = [binary, "--config", str(empty), "--env-file", str(dotenv), "--no-git", "--no-analytics",
                "--no-check-update", "--no-pretty", "--chat-history-file", str(Path(temp) / "info.md"), *option]
        try:
            result = subprocess.run(argv, cwd=temp, env=process_env, stdin=subprocess.DEVNULL, capture_output=True,
                                    encoding="utf-8", errors="replace", timeout=45)
        except subprocess.TimeoutExpired:
            raise ValueError("Aider did not respond within 45 seconds.") from None
    output = cfg.replace_secrets(result.stdout + result.stderr, cfg.secret_values(root))
    return result.returncode, output.strip()


@config_bp.route("/models", methods=["GET", "POST"])
def models(wid):
    query = (body() if request.method == "POST" else request.args).get("q", "")
    if not isinstance(query, str):
        raise ValueError("Enter a model ID or a partial model name.")
    query = query.strip()
    if not query:
        return jsonify(models=[])
    root = deck().workspace(wid)
    if len(query) > 160 or not safe_model_id(query, cfg.secret_values(root, strict=False)):
        raise ValueError("Enter a model ID or a partial model name.")
    status, output = aider_info(root, ["--list-models", query])
    if status:
        raise ValueError("Aider could not list models.")
    return jsonify(models=[s[2:].strip() for s in output.splitlines() if s.startswith("- ")])


@config_bp.post("/update")
def update(wid):
    status, output = aider_info(deck().workspace(wid), ["--just-check-update"])
    return jsonify(exit_code=status, output=output)


@config_bp.get("/model-candidates")
def candidates(wid):
    root = deck().workspace(wid)
    protected = cfg.secret_values(root)
    return jsonify(model_candidates(root, deck().state.ollama_tags(wid),
                                    lambda name: safe_model_id(name, protected)))


@config_bp.post("/model-catalog")
def model_catalog(wid):
    model = body().get("model")
    root = deck().workspace(wid)
    if not safe_model_id(model, cfg.secret_values(root)):
        return jsonify(link=None)
    routes = effort.routing(root, cfg.launch_values(root))
    return jsonify(link=catalog_link(routes.aliases.get(model, model), routes))
