"""Read Aider's documented configuration chain without importing Aider."""
import difflib
import hashlib
import io
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from collections import Counter
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

from . import envfile
from .registry import FORMATS, MODEL_DEFAULT_FORMATS, REGISTRY

CONF = ".aider.conf.yml"
MODELS = ".aider.model.settings.yml"
METADATA = ".aider.model.metadata.json"
RAW_FILES = {CONF, MODELS, METADATA, ".aiderignore"}
PRESETS = Path(__file__).resolve().parents[1] / "presets.json"
DECK_DEFAULTS = {"edit-format": "diff", "editor-edit-format": "editor-diff",
                 "auto-commits": False, "analytics-disable": True}
INTEGER_KEYS = {"cache-keepalive-pings", "map-tokens", "max-chat-history-tokens"}
TOKEN_KEY = secrets.token_bytes(32)
EFFORT_LEVEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


def yaml():
    parser = YAML(typ="rt")
    parser.preserve_quotes = True
    parser.width = 4096
    return parser


def parse(text, name):
    try:
        if name == METADATA:
            data = json.loads(text) if text.strip() else {}
        elif name == ".aiderignore":
            return text
        else:
            data = yaml().load(text)
            if data is None:
                data = CommentedSeq() if name == MODELS else CommentedMap()
    except (YAMLError, json.JSONDecodeError) as exc:
        if isinstance(exc, json.JSONDecodeError):
            raise ValueError(f"{name}, line {exc.lineno}, column {exc.colno}: {exc.msg}") from None
        mark = getattr(exc, "problem_mark", None)
        location = f"line {mark.line + 1}, column {mark.column + 1}" if mark else "unknown location"
        # Parser messages may quote tokens or entire source lines (including credentials).
        problem = str(getattr(exc, "problem", "invalid YAML syntax"))
        # Keep structural messages; arbitrary quoted source values are private.
        if "duplicate key" in problem:
            problem = "duplicate mapping key"
        elif problem.startswith("expected ") and ", but found" in problem:
            problem = problem.split(", but found", 1)[0]
        elif problem not in ("mapping values are not allowed here", "could not find expected ':'",
                             "found unexpected end of stream", "found unexpected document separator"):
            problem = "invalid YAML syntax"
        raise ValueError(f"{name}, {location}: {problem}.") from None
    if name == MODELS:
        if not isinstance(data, list) or any(not isinstance(x, dict) or not isinstance(x.get("name"), str) for x in data):
            raise ValueError(f"{name} must contain a YAML list of model mappings, each with a name.")
    elif not isinstance(data, dict):
        raise ValueError(f"{name} must contain an object with named keys.")
    elif any(not isinstance(key, str) for key in data):
        raise ValueError(f"{name} requires text keys.")
    return data


def dump(data, name=CONF):
    if name == METADATA:
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    stream = io.StringIO()
    yaml().dump(data, stream)
    return stream.getvalue()


def normalize(key, value):
    if value is None or value == "":
        return None
    row = REGISTRY[key]
    kind = row["type"]
    if kind == "toggle":
        if isinstance(value, bool):
            return value
        values = {"true": True, "yes": True, "on": True, "1": True,
                  "false": False, "no": False, "off": False, "0": False}
        if str(value).lower() in values:
            return values[str(value).lower()]
        raise ValueError(f"{key} expects true or false.")
    if kind == "number":
        try:
            number = (int if key in INTEGER_KEYS else float)(str(value))
            if not math.isfinite(number):
                raise ValueError
            return number
        except ValueError:
            raise ValueError(f"{key} requires a number.") from None
    if kind == "list":
        if isinstance(value, str):
            value = value.splitlines()
        if not isinstance(value, list) or any(not isinstance(s, str) for s in value):
            raise ValueError(f"{key} expects a list of text values.")
        return value
    if not isinstance(value, (str, int, float)):
        raise ValueError(f"{key} expects text.")
    if key == "reasoning-effort" and isinstance(value, bool):
        raise ValueError("Enter a named or numeric effort level.")
    value = str(value)
    if key == "reasoning-effort" and not EFFORT_LEVEL.fullmatch(value):
        raise ValueError("Enter an effort level of up to 64 letters, digits, periods, underscores or hyphens.")
    if kind == "select" and value not in row["choices"]:
        raise ValueError(f"Unsupported selection for {key}.")
    return value


def effective(root):
    result = {k: (DECK_DEFAULTS.get(k, r["default"]), "Default", None)
              for k, r in REGISTRY.items()}
    for source, folder in envfile.layers(root):
        path = envfile.workspace_file(root, CONF) if folder == root else folder / CONF
        for key, value in parse(envfile.read_text(path), CONF).items():
            if key in REGISTRY:
                if value is not None and REGISTRY[key]["type"] in {"toggle", "number", "list"}:
                    value = normalize(key, value)
                elif value is not None and not isinstance(value, (str, bool, int, float)):
                    raise ValueError(f"{path.name}: {key} requires a scalar value.")
                result[key] = (value, source, str(path))
            elif key == "chat-mode":
                result["edit-format"] = (value, source, str(path))
    env = aider_environment(root)
    for key, row in REGISTRY.items():
        name = row["env"]
        if name in env.values:
            value = env.values[name]
            try:
                value = normalize(key, value)
            except ValueError:
                pass  # Surface the actual inherited value. Aider validates it at launch.
            result[key] = (value, "Env", env.sources[name])
    return result


def launch_values(root):
    return {k: v for k, (v, _, _) in effective(root).items()
            if v is not None and not envfile.is_sensitive(k)}


def aider_environment(root, tolerate_invalid=False):
    """Aider loads an explicitly selected env-file after home/repo/cwd dotenv."""
    base = envfile.environment(root, tolerate_invalid=tolerate_invalid)
    extra = base.values.get("AIDER_ENV_FILE")
    if not extra:
        for _, folder in envfile.layers(root):
            path = folder / CONF
            try:
                doc = parse(envfile.read_text(path), CONF)
            except ValueError:
                if tolerate_invalid:
                    continue
                raise
            if "env-file" in doc:
                extra = doc["env-file"]
    if extra and not isinstance(extra, str):
        raise ValueError("env-file requires a path.")
    if extra:
        return envfile.environment(root, tolerate_invalid=tolerate_invalid, extra=extra)
    return base


def launch_environment(root, architect):
    """Keep --architect authoritative over configargparse's appended file/env arguments."""
    if not architect:
        return envfile.child_environment(root), None
    loaded = aider_environment(root)
    values = loaded.values.copy()
    key = "AIDER_EDIT_FORMAT"
    values[key] = "architect"
    if loaded.sources.get(key) in (None, "Process environment") or loaded.values.get(key) == "architect":
        return values, None
    # Aider reloads dotenv with override=True after its first argument parse.
    # Select one last, nonsecret dotenv for this launch. Preload a custom
    # env-file's resolved values and restore any that a default .env shadows.
    defaults = envfile.environment(root)
    lines = [key + "=architect"]
    for name in sorted(defaults.file_names - {key}):
        if name in values and defaults.values.get(name) != values[name]:
            if not envfile.NAME.fullmatch(name):
                raise ValueError("A custom dotenv override requires a valid environment variable name.")
            reference = "DECK_LAUNCH_" + secrets.token_hex(16).upper()
            values[reference] = values[name]
            lines.append(name + "=${" + reference + "}")
    launch_files = tempfile.TemporaryDirectory(prefix="deck-architect-", ignore_cleanup_errors=True)
    try:
        overlay = Path(launch_files.name) / "architect.env"
        overlay.write_text("\n".join(lines) + "\n", encoding="utf-8")
        values["AIDER_ENV_FILE"] = str(overlay)
    except BaseException:
        launch_files.cleanup()
        raise
    return values, launch_files


def candidate_files(root, name, custom=None):
    paths = [folder / name for _, folder in envfile.layers(root)]
    if custom:
        paths.append(root / Path(custom).expanduser())
    # Aider de-duplicates from the end. An explicit file has final precedence.
    return list(reversed(list(dict.fromkeys(p.resolve() for p in reversed(paths)))))


def model_format_error(entry, path, previous=None):
    for field in ("edit_format", "editor_edit_format"):
        value = entry.get(field)
        if value is None or (previous is not None and previous.get(field) == value):
            continue
        if not isinstance(value, str) or value not in MODEL_DEFAULT_FORMATS:
            return (f"{path}: model {entry['name']} has an unsupported {field}. "
                    "Choose a supported Aider coder in the model settings file and restart the session.")
    return None


def registrations(root, configured=None):
    configured = configured if configured is not None else launch_values(root)
    found = {}
    for p in candidate_files(root, MODELS, configured.get("model-settings-file")):
        for entry in parse(envfile.read_text(p), MODELS):
            model = entry["name"]
            found[model] = dict(model=model, registered=True, edit_format=entry.get("edit_format"), path=str(p))
            issue = model_format_error(entry, p)
            if issue:
                found[model]["format_error"] = issue
    return found


def model_format_errors(registered, aliases):
    errors = {name: row["format_error"] for name, row in registered.items() if row.get("format_error")}
    # Aider resolves custom alias:model settings before looking up the model.
    for definition in aliases or ():
        alias, separator, target = definition.partition(":")
        if separator:
            issue = registered.get(target.strip(), {}).get("format_error")
            if issue:
                errors[alias.strip()] = issue
            else:
                errors.pop(alias.strip(), None)
    return errors


def registration(root, model, configured=None):
    return registrations(root, configured).get(model, dict(model=model, registered=False, edit_format=None, path=None))


def model_limit(root, model):
    if not model:
        return None
    limit = None
    for p in candidate_files(root, METADATA, launch_values(root).get("model-metadata-file")):
        entry = parse(envfile.read_text(p), METADATA).get(model)
        if isinstance(entry, dict):
            limit = entry.get("max_input_tokens")
    return limit if isinstance(limit, int) and limit > 0 else None


def config_fingerprint(root):
    """Compare launch inputs by value. Comments and equivalent rewrites are harmless."""
    values = launch_values(root)
    unlisted = {}
    for path in candidate_files(root, CONF):
        for key, value in parse(envfile.read_text(path), CONF).items():
            if key not in REGISTRY or envfile.is_sensitive(key):
                unlisted[key] = value
    models, metadata = {}, {}
    for path in candidate_files(root, MODELS, values.get("model-settings-file")):
        models.update({entry["name"]: entry for entry in parse(envfile.read_text(path), MODELS)})
    for path in candidate_files(root, METADATA, values.get("model-metadata-file")):
        metadata.update(parse(envfile.read_text(path), METADATA))
    inputs = [values, unlisted, aider_environment(root).values, models, metadata]
    raw = json.dumps(inputs, sort_keys=True, ensure_ascii=False, default=str)
    return digest(raw)


def protected_fields(data, parent=(), seen=None):
    if seen is None:
        seen = set()
    if not isinstance(data, (dict, list)) or id(data) in seen:
        return
    seen.add(id(data))
    for key, value in (data.items() if isinstance(data, dict) else enumerate(data)):
        location = parent + (key,)
        if envfile.is_sensitive(key) or key in ("api-key", "set-env", "extra_headers", "headers"):
            yield location, data, key, value
        else:
            yield from protected_fields(value, location, seen)


def strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from strings(item)
    elif isinstance(value, str) and value:
        yield value
        if "=" in value:
            yield value.split("=", 1)[1]
        if value.startswith("Bearer "):
            yield value[7:]


def secret_values(root, strict=True):
    try:
        env = aider_environment(root)
    except ValueError:
        if strict:
            raise
        env = aider_environment(root, tolerate_invalid=True)
    # Include arbitrary dotenv entries so a provider with a non-standard name is protected.
    values = {v for k, v in env.values.items() if v and (envfile.is_sensitive(k) or
              (k in env.file_names and not k.startswith("AIDER_") and k != "OPENAI_API_BASE" and len(v) > 4))}
    try:
        providers, overrides = provider_environment(root)
        for name in overrides:
            if providers.values[name] and (envfile.is_sensitive(name) or
                    (name != "OPENAI_API_BASE" and len(providers.values[name]) > 4)):
                values.add(providers.values[name])
    except ValueError:
        if strict:
            raise
    custom_files = {}
    try:
        options = effective(root)
        custom_files = {MODELS: options["model-settings-file"][0], METADATA: options["model-metadata-file"][0]}
    except ValueError:
        if strict:
            raise
    for name in (CONF, MODELS, METADATA):
        for p in candidate_files(root, name, custom_files.get(name)):
            try:
                data = parse(envfile.read_text(p), name)
            except ValueError:
                if strict:
                    raise
                continue
            for _, _, _, v in protected_fields(data):
                values.update(strings(v))
    return sorted(values - {""}, key=len, reverse=True)


def digest(*parts):
    h = hashlib.blake2b(key=TOKEN_KEY, digest_size=24)
    for part in parts:
        raw = str(part).encode()
        h.update(len(raw).to_bytes(8, "big"))
        h.update(raw)
    return h.hexdigest()


def replace_secrets(text, secrets_list):
    for secret in secrets_list:
        if secret in text:
            text = text.replace(secret, "••••••••")
    return text


def option_assignments(name, raw):
    if isinstance(raw, str) and raw.startswith("["):
        try:
            raw = yaml().load(raw)
        except YAMLError:
            raise ValueError(f"Invalid list in the {name} option.") from None
    for item in raw if isinstance(raw, list) else [raw]:
        key, separator, value = str(item).partition("=")
        key = key.strip()
        if not separator or not envfile.NAME.fullmatch(key):
            raise ValueError(f"Invalid {name} assignment. Expected a name followed by = and a value.")
        yield key.upper() + "_API_KEY" if name == "api-key" else key, value.strip()


def provider_environment(root):
    """Include provider variables Aider sets after reading dotenv and parsing options."""
    env = aider_environment(root, tolerate_invalid=True)
    option_names = ("set-env", "api-key", "openai-api-key", "anthropic-api-key", "openai-api-base")
    configured = {}
    for path in candidate_files(root, CONF):
        data = parse(envfile.read_text(path), CONF)
        for name in option_names:
            if name in data:
                configured[name] = (data[name], str(path))
    for name in option_names:
        flag_env = "AIDER_" + name.upper().replace("-", "_")
        if env.values.get(flag_env):
            configured[name] = (env.values[flag_env], env.sources[flag_env])

    overrides = set()
    for name in option_names:
        if name not in configured:
            continue
        raw, source = configured[name]
        if not raw:
            continue
        if name in ("api-key", "set-env"):
            assignments = option_assignments(name, raw)
        else:
            assignments = [(name.upper().replace("-", "_"), str(raw))]
        for key, value in assignments:
            overrides.add(key)
            env.file_names.add(key)
            env.values[key] = value
            env.sources[key] = f"{source} ({name})"
    return env, overrides


# These Aider options perform work or exit during startup, before the owner
# can interact with the terminal. String-valued options are active even when
# their literal path or message is the word "false".
STARTUP_ACTIONS = {
    "commit", "test", "lint", "apply-clipboard-edits", "exit", "just-check-update",
    "upgrade", "install-main-branch", "show-repo-map", "show-prompts", "version", "help",
}
STARTUP_ARGUMENTS = {"load", "message", "message-file", "apply", "list-models", "shell-completions"}
ALIASES = {"msg": "message", "models": "list-models", "update": "upgrade"}
CREDENTIAL_OPTIONS = {
    "api-key", "set-env", "analytics-posthog-project-api-key", "openai-api-key", "anthropic-api-key",
}


def validate_interactive_launch(root):
    """Reject effective startup actions and options Aider would log as plaintext."""
    env = aider_environment(root)
    watched = STARTUP_ACTIONS | STARTUP_ARGUMENTS | CREDENTIAL_OPTIONS | {"verbose"}
    selected = {}
    def observe(key, value):
        if value is None or value == []:
            return
        if (key in STARTUP_ACTIONS or key == "verbose") and str(value).lower() in ("0", "false", "off", "no"):
            # For a store_true action configargparse converts False to *no*
            # argument. Thus lower-precedence True still takes effect.
            return
        if key in ("api-key", "set-env") and value == "[]":
            return
        selected[key] = value

    for path in candidate_files(root, CONF):
        for key, value in parse(envfile.read_text(path), CONF).items():
            if not isinstance(key, str):
                raise ValueError("Configuration option names must be strings.")
            key = key.removeprefix("--")
            canonical = ALIASES.get(key, key)
            if canonical in watched:
                observe(canonical, value)
    for key in watched - {"version", "help"}:
        variable = "AIDER_" + key.upper().replace("-", "_")
        if variable in env.values:
            observe(key, env.values[variable])

    for key in sorted(STARTUP_ARGUMENTS | STARTUP_ACTIONS):
        value = selected.get(key)
        if value is None or value == "" or value == []:
            continue
        if key in STARTUP_ACTIONS and str(value).strip().lower() in ("0", "false", "off", "no"):
            continue
        raise ValueError(f"Disable the {key} startup option before starting an interactive Aider session.")

    for key in ("api-key", "analytics-posthog-project-api-key"):
        value = selected.get(key)
        if value and value != "[]":
            raise ValueError(f"Remove {key} from Aider options before starting: Aider prints this credential "
                             "in its native settings transcript. Store provider credentials directly in .env.")
    for key in ("openai-api-key", "anthropic-api-key"):
        value = selected.get(key)
        if value and len(str(value)) <= 4:
            raise ValueError(f"Aider's four-character mask reveals this entire {key} value. "
                             "Store the provider key directly in .env.")

    # Ordinary set-env entries such as LANG or HTTPS_PROXY are valid. Aider's
    # formatter copies their raw assignment to its own history when /settings
    # runs, before Deck's terminal redactor can see the output.
    if selected.get("set-env"):
        known = {v for k, v in env.values.items() if v and envfile.is_sensitive(k)}
        for key, value in option_assignments("set-env", selected["set-env"]):
            if value and (envfile.is_sensitive(key) or value in known or
                          re.search(r"://[^/\s]+:[^/@\s]+@|[?&](?:api[_-]?key|access_token)=", value, re.I)):
                raise ValueError("A set-env option contains a credential Aider would record in history. "
                                 "Move the assignment into the workspace .env and remove it from set-env.")

    # /settings prints model metadata. Verbose launch also prints the selected
    # model's entire settings object. Do not let a nested Authorization header
    # or literal provider key reach Aider's on-disk transcript through these.
    files = {METADATA: "model-metadata-file"}
    if selected.get("verbose"):
        files[MODELS] = "model-settings-file"
    options = launch_values(root)
    for filename, option in files.items():
        for path in candidate_files(root, filename, options.get(option)):
            model_data = parse(envfile.read_text(path), filename)
            if model_credentials(model_data):
                raise ValueError(f"{filename} contains credential fields that Aider prints in its native transcript. "
                                 "Move literal credentials into provider environment variables before starting.")


def model_credentials(data, seen=None):
    if seen is None:
        seen = set()
    if not isinstance(data, (dict, list)) or id(data) in seen:
        return False
    seen.add(id(data))
    for key, value in (data.items() if isinstance(data, dict) else enumerate(data)):
        if (envfile.is_sensitive(key) and isinstance(value, str) and value and
                not value.startswith(("os.environ/", "${"))):
            return True
        if model_credentials(value, seen):
            return True
    return False


def raw_view(root, name, text):
    # Remove entire sensitive YAML/JSON subtrees before serializing, including
    # multiline strings and provider credentials encoded with escape sequences.
    safe_text, restore = text, {}
    if name != ".aiderignore":
        data = parse(text, name)
        for location, container, key, value in list(protected_fields(data)):
            marker = "DECK_MASKED_" + digest(location, name, text)
            restore[location] = (marker, value)
            container[key] = marker
        if restore:
            safe_text = dump(data, name)
    # Preserve matching secrets in comments and noncredential fields through a raw save.
    literal_markers = {}
    for value in secret_values(root, strict=False):
        if value in safe_text:
            marker = "DECK_REDACTED_" + digest(name, value, text)
            safe_text = safe_text.replace(value, marker)
            literal_markers[marker] = value
    return safe_text, restore, literal_markers


def save_raw(root, name, text, revision, replace_invalid=False):
    if name not in RAW_FILES or not isinstance(text, str):
        raise ValueError("Unsupported raw file or body.")
    path = envfile.workspace_file(root, name)
    old = envfile.read_text(path)
    if revision != digest(path, old):
        raise ValueError("The file changed. Reopen it before saving.")
    try:
        _, protected, literals = raw_view(root, name, old)
    except ValueError:
        if not replace_invalid:
            raise ValueError("Confirm a full replacement of the unreadable file first.") from None
        protected, literals = {}, {}
    for marker, value in literals.items():
        text = text.replace(marker, value)
    if name == METADATA and not text.strip():
        raise ValueError("Metadata requires a JSON object. Use {} for an empty object.")
    data = parse(text, name)
    if name == MODELS:
        try:
            prior = {row["name"]: row for row in parse(old, name)}
        except ValueError:
            prior = {}
        for row in data:
            issue = model_format_error(row, path, prior.get(row["name"]))
            if issue:
                raise ValueError(issue)
    if name in (CONF, MODELS) and not any(line.split("#", 1)[0].strip() for line in text.splitlines()):
        # An existing empty/comment-only config produces None in Aider's loader.
        text = text.rstrip() + "\n" + ("{}\n" if name == CONF else "[]\n")
    restored = False
    if protected:
        for location, container, key, value in list(protected_fields(data)):
            if location in protected and value == protected[location][0]:
                container[key] = protected[location][1]
                restored = True
    if restored:
        text = dump(data, name)
    if "DECK_MASKED_" in text or "DECK_REDACTED_" in text:
        raise ValueError("A masked value was moved or changed. Reopen the editor.")
    envfile.atomic_write(path, text)


def _comments(text):
    comments = []
    def collect(token):
        if isinstance(token, list):
            for t in token:
                collect(t)
        elif hasattr(token, "value"):
            comments.extend(line.strip() for line in token.value.splitlines() if line.strip().startswith("#"))
    for token in yaml().scan(text):
        collect(token.comment)
    return comments


def update_yaml(old, changes, remove_defaults=False):
    doc = parse(old, CONF)
    for key, value in changes.items():
        if value is None or (remove_defaults and value == DECK_DEFAULTS.get(key, REGISTRY[key]["default"])):
            doc.pop(key, None)
        else:
            doc[key] = value
    new = dump(doc)
    # ruamel attaches some standalone comments to the preceding key. Retain any
    # comments orphaned when a user explicitly removes that key.
    remaining = Counter(_comments(new))
    orphaned = []
    for comment in _comments(old):
        if remaining[comment]:
            remaining[comment] -= 1
        else:
            orphaned.append(comment)
    if orphaned:
        new += "\n".join(orphaned) + "\n"
    return new


def _reasoning_definition(root, model, configured):
    aliases = {}
    for pair in configured.get("alias") or ():
        alias, sep, target = pair.partition(":")
        if sep:
            aliases[alias.strip()] = target.strip()
    model = aliases.get(model, model)
    local = envfile.workspace_file(root, MODELS)
    custom = configured.get("model-settings-file")
    paths = candidate_files(root, MODELS, custom)
    source, entry = None, None
    local_path = local.resolve()
    originals = {}
    for path in paths:
        originals[path] = envfile.read_text(path)
        for item in parse(originals[path], MODELS):
            if item["name"] == model:
                source, entry = path, item
    dest = local
    checked = configured.get("check-model-accepts-settings", True) is not False
    allowed = entry.get("accepts_settings") if entry is not None else None
    declared = None if entry is None else isinstance(allowed, list) and "reasoning_effort" in allowed
    can_declare = False
    if not model:
        message = "Choose a main model first."
    elif not checked:
        message = "Aider’s launch check for model settings is disabled."
    elif model.startswith("openrouter/"):
        declared = True
        message = "Aider includes reasoning_effort for OpenRouter models."
    elif declared:
        message = "The model definition accepts reasoning_effort at launch."
    elif allowed is not None and (not isinstance(allowed, list) or any(not isinstance(v, str) for v in allowed)):
        message = "The model’s accepts_settings must be a YAML list of names. Correct its model settings file."
    elif source and paths.index(source) > paths.index(local_path) and not source.is_relative_to(root):
        message = "An external model-settings-file overrides this workspace. Add reasoning_effort to that model’s accepts_settings in the configured file."
    else:
        if source and paths.index(source) > paths.index(local_path):
            # Validate the original custom path before writing. candidate_files resolves symlinks.
            dest = envfile.workspace_file(root, Path(custom).expanduser())
        can_declare = True
        message = ("The model definition omits reasoning_effort from Aider’s launch settings."
                   if entry is not None else
                   "Aider has no user-file declaration for this model. Its built-in settings may already accept reasoning_effort.")
        if source and source != dest.resolve():
            # Carry forward the whole effective entry because Aider replaces, rather than merges, model entries.
            # A workspace copy of a parent definition must not bring credentials into the project.
            serialized = dump(entry)
            if model_credentials(entry) or any(s in serialized for s in secret_values(root)
                                               if not s.startswith(("os.environ/", "${"))):
                can_declare = False
                message = "The inherited model entry contains protected values. Add reasoning_effort to accepts_settings in its original model settings file."
    guard = dict(checked=checked, declared=declared, can_declare=can_declare, needs_format=entry is None, message=message)
    return model, dest, entry, guard, originals[dest.resolve()]


def reasoning_guard(root, model, configured=None):
    configured = launch_values(root) if configured is None else configured
    return _reasoning_definition(root, model, configured)[3]


@lru_cache(maxsize=16)
def _stock_model_settings(executable, binary_revision, model):
    """Ask Aider for its built-in/family defaults before creating an exact-match override."""
    with tempfile.TemporaryDirectory(prefix="deck-model-defaults-") as temp:
        # Only OS launch variables are inherited. Both provider traffic and Aider's optional
        # metadata refresh go through a closed loopback proxy, using a disposable home.
        system = {"PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP"}
        environment = {key: value for key, value in os.environ.items() if key.upper() in system}
        blocked = "http://127.0.0.1:1"
        environment.update(HOME=temp, USERPROFILE=temp, APPDATA=temp, LOCALAPPDATA=temp,
                           HTTP_PROXY=blocked, HTTPS_PROXY=blocked, ALL_PROXY=blocked,
                           OLLAMA_API_BASE=blocked, AWS_EC2_METADATA_DISABLED="true",
                           LITELLM_LOCAL_MODEL_COST_MAP="True", PYTHONIOENCODING="utf-8", COLUMNS="240")
        argv = [executable, "--model=" + model, "--verbose", "--exit", "--no-git", "--no-analytics",
                "--no-check-update", "--no-show-model-warnings", "--no-pretty", "--no-suggest-shell-commands"]
        try:
            result = subprocess.run(argv, env=environment, cwd=temp, stdin=subprocess.DEVNULL, capture_output=True,
                                    encoding="utf-8", errors="replace", timeout=35)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("Aider could not report this model’s defaults. Check the installed Aider CLI and try again.") from None
    # Aider prints one field name followed by its JSON value, which can span lines.
    section = re.search(r"^Model settings:\s*$", result.stdout, re.M)
    settings = {}
    if result.returncode == 0 and section:
        rest = result.stdout[section.end():].lstrip()
        while match := re.match(r"([a-z_]+):\s*", rest):
            try:
                value, end = json.JSONDecoder().raw_decode(rest[match.end():])
            except ValueError:
                raise ValueError("Aider returned unreadable model defaults. Review the model settings editor.") from None
            settings[match[1]] = value
            rest = rest[match.end() + end:].lstrip()
    if not {"name", "edit_format", "accepts_settings", "extra_params", "weak_model_name"} <= settings.keys():
        raise ValueError("Aider did not return its full model settings. Review the model settings editor.")
    return {key: value for key, value in settings.items() if value is not None}


def _reasoning_preview(root, body):
    configured = launch_values(root)
    selection = body.get("model")
    if not isinstance(selection, str) or not selection or selection != configured.get("model"):
        raise ValueError("The selected launch model changed. Review the model before previewing.")
    model, path, entry, guard, old = _reasoning_definition(root, selection, configured)
    if not guard["can_declare"]:
        raise ValueError(guard["message"])
    doc = parse(old, MODELS)
    if entry is None:
        fmt = body.get("edit_format")
        if not isinstance(fmt, str) or fmt not in FORMATS:
            raise ValueError("Choose a model default edit format for this registration.")
        if len(model) > 200 or re.search(r"[\x00-\x1f\x7f]", model):
            raise ValueError("Choose a single model ID.")
        executable = shutil.which("aider")
        if not executable:
            raise ValueError("Aider was not found on the server PATH.")
        entry = deepcopy(_stock_model_settings(executable, Path(executable).stat().st_mtime_ns, model))
        if entry["name"] != model:
            raise ValueError("Select the full model ID Aider announces before registering this model.")
        entry["edit_format"] = fmt
    else:
        entry = deepcopy(entry)
    # An anchored accepts_settings list can also belong to other models.
    allowed = deepcopy(entry.get("accepts_settings") or CommentedSeq())
    if "reasoning_effort" not in allowed:
        allowed.append("reasoning_effort")
    entry["accepts_settings"] = allowed
    index = next((i for i in reversed(range(len(doc))) if doc[i]["name"] == model), None)
    if index is None:
        doc.append(entry)
    else:
        doc[index] = entry
    if envfile.read_text(path) != old:
        raise ValueError("The model settings file changed. Review a fresh preview.")
    return path, old, dump(doc, MODELS)


def preview(root, body):
    if body.get("kind") == "register":
        name = MODELS
        path = envfile.workspace_file(root, name)
        old = envfile.read_text(path)
        model, fmt = body.get("model"), body.get("edit_format")
        if not isinstance(model, str) or not model.strip() or re.search(r"[\x00-\x1f\x7f]", model) or fmt not in FORMATS:
            raise ValueError("Choose a model ID and an edit format.")
        doc = parse(old, name)
        if any(item["name"] == model for item in doc):
            raise ValueError("The model already has a workspace entry. Use the model settings editor to change it.")
        doc.append(CommentedMap(name=model, edit_format=fmt))
        new = dump(doc, name)
    elif body.get("kind") == "reasoning-support":
        name = MODELS
        path, old, new = _reasoning_preview(root, body)
    elif body.get("kind") == "preset":
        name = CONF
        path = envfile.workspace_file(root, name)
        old = envfile.read_text(path)
        profile = presets().get(body.get("name"))
        if profile is None:
            raise ValueError("Unknown preset.")
        new = update_yaml(old, {k: normalize(k, v) for k, v in profile.items()})
    else:
        raise ValueError("Choose a preset or model registration.")
    if "\r\n" in old and "\n" not in old.replace("\r\n", ""):
        new = new.replace("\r\n", "\n").replace("\n", "\r\n")
    # Masked editor handles are revision-specific. Present them identically in both
    # sides of a preview so an unchanged credential does not appear to have changed.
    def preview_lines(content):
        masked = raw_view(root, name, content)[0].replace("\r\n", "\n")
        return re.sub(r"DECK_(?:MASKED|REDACTED)_[0-9a-f]{48}", "[redacted]", masked).splitlines(True)
    before, after = preview_lines(old), preview_lines(new)
    filename = str(path.relative_to(root))
    diff = "".join(difflib.unified_diff(before, after, fromfile=filename, tofile=filename, n=0))
    return path, new, dict(diff=diff, file=filename, token=digest(path, old, new))


def presets():
    result = json.loads(PRESETS.read_text(encoding="utf8"))
    if not isinstance(result, dict) or any(not isinstance(v, dict) or v.keys() - REGISTRY.keys() for v in result.values()):
        raise ValueError("presets.json must map names to registered configuration keys.")
    return result
