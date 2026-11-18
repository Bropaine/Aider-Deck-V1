"""Discover model-specific effort choices without inference or hosted API credentials."""

from dataclasses import dataclass
import os
from urllib.parse import urlsplit

from . import aider_config as cfg
from . import envfile
from .ollama_service import OllamaError, OllamaService, local_endpoint


OLLAMA_DOC = "https://docs.ollama.com/capabilities/thinking"
OLLAMA_COMPAT_DOC = "https://docs.ollama.com/api/openai-compatibility"
OPENAI_DOC = "https://developers.openai.com/api/docs/models"
CLAUDE_DOC = "https://platform.claude.com/docs/en/build-with-claude/effort"
GEMINI_DOC = "https://ai.google.dev/gemini-api/docs/thinking"
METADATA_DOC = "https://platform.claude.com/docs/en/api/models/retrieve"


# Exact model IDs and explicitly published snapshots only. Each group is from
# the provider model pages linked with the result. Reviewed 2026-11-18.
def _catalog(groups):
    return {model: levels.split() for levels, models in groups for model in models.split()}


OPENAI = _catalog(
    (
        ("low medium high xhigh max", "gpt-6.2-astra gpt-6.2-sol gpt-6.1-sol gpt-6-astra"),
        (
            "none low medium high xhigh max",
            "gpt-6.1-terra gpt-6-sol gpt-6-luna gpt-5.6-sol gpt-5.6-terra gpt-5.6",
        ),
        (
            "none low medium high xhigh",
            "gpt-5.5 gpt-5.4 gpt-5.4-2026-03-05 gpt-5.4-mini gpt-5.4-nano gpt-5.2 gpt-5.2-2025-12-11",
        ),
        ("medium high xhigh", "gpt-5.5-pro gpt-5.4-pro gpt-5.2-pro"),
        ("low medium high xhigh", "gpt-5.3-codex"),
        ("none low medium high", "gpt-5.1"),
        ("minimal low medium high", "gpt-5 gpt-5-mini gpt-5-nano"),
        ("low medium high", "o3 o3-mini o4-mini"),
        ("", "gpt-4.1 gpt-4.1-mini gpt-4.1-nano"),
    )
)
CLAUDE = _catalog(
    (
        (
            "low medium high xhigh max",
            "claude-fable-5-2 claude-mythos-5-2 claude-fable-5-1 claude-mythos-5-1 "
            "claude-fable-5 claude-mythos-5 claude-opus-5-5 claude-opus-5 "
            "claude-opus-4-8 claude-opus-4-7 claude-sonnet-5-5 claude-sonnet-5 "
            "claude-haiku-5-5",
        ),
        ("low medium high max", "claude-opus-4-6 claude-sonnet-4-6"),
        ("low medium high", "claude-opus-4-5 claude-opus-4-5-20251101"),
    )
)
GEMINI = _catalog(
    (
        (
            "low medium high",
            "gemini-4-carbon gemini-4-argon gemini-3.8-flash gemini-3.1-pro-preview "
            "gemini-2.5-pro gemini-2.5-flash gemini-2.5-flash-lite",
        ),
        ("low medium high xhigh", "gemini-4-hydrogen"),
        (
            "minimal low medium high",
            "gemini-3.6-flash gemini-3.5-flash-lite gemini-3.1-flash-lite "
            "gemini-3-flash-preview gemini-robotics-er-2-preview",
        ),
        ("minimal high", "gemini-3.1-flash-lite-image"),
        ("low high", "gemini-3-pro-preview"),
    )
)
CLOUD = {
    "openai": (OPENAI, "OpenAI model catalog", OPENAI_DOC),
    "anthropic": (CLAUDE, "Anthropic effort guide", CLAUDE_DOC),
    "gemini": (GEMINI, "Google Gemini thinking guide", GEMINI_DOC),
}
OPENAI_PAGES = {
    "gpt-6.2-astra": OPENAI_DOC,
    "gpt-6.2-sol": OPENAI_DOC,
    "gpt-6-luna": OPENAI_DOC,
    "gpt-5.6": OPENAI_DOC + "/gpt-5.6-sol",
    "gpt-5.4-2026-03-05": OPENAI_DOC + "/gpt-5.4",
    "gpt-5.2-2025-12-11": OPENAI_DOC + "/gpt-5.2",
    "o3-mini": "https://openai.com/index/openai-o3-mini/",
    **dict.fromkeys(
        ("o3", "o4-mini"),
        "https://developers.openai.com/cookbook/examples/partners/model_selection_guide/model_selection_guide",
    ),
    **dict.fromkeys(
        ("gpt-5-mini", "gpt-5-nano"),
        "https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5",
    ),
    **dict.fromkeys(
        ("gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano"),
        "https://developers.openai.com/api/docs/guides/latest-model?model=gpt-4.1",
    ),
}
PROVIDER_BASES = {
    "https://api.openai.com/v1": "openai",
    "https://api.anthropic.com": "anthropic",
    "https://api.anthropic.com/v1": "anthropic",
    "https://generativelanguage.googleapis.com": "gemini",
    "https://generativelanguage.googleapis.com/v1beta": "gemini",
}
BASE_VARS = {
    "openai": ("OPENAI_BASE_URL", "OPENAI_API_BASE"),
    "anthropic": ("ANTHROPIC_API_BASE", "ANTHROPIC_BASE_URL"),
    "gemini": ("GEMINI_API_BASE", "GOOGLE_GEMINI_BASE_URL"),
    "ollama": ("OLLAMA_API_BASE",),
}


def _destination(value):
    """Store only a recognized endpoint's identity; discard arbitrary URLs and credentials."""
    if isinstance(value, str):
        try:
            url = urlsplit(value)
            if url.scheme == "http" and url.path in ("/v1", "/v1/"):
                if (
                    url.hostname in ("0.0.0.0", "::")
                    or url.username is not None
                    or url.password is not None
                    or url.query
                    or url.fragment
                ):
                    return None
                # An OpenAI client connects to port 80 for HTTP URLs without a port.
                # Keep /v1 distinct from the native API until both endpoints match.
                netloc = url.netloc if url.port is not None else url.netloc + ":80"
                origin = local_endpoint(url._replace(netloc=netloc, path="").geturl())
                return origin + "/v1"
            if (
                url.scheme == "https"
                and url.hostname
                and url.port in (None, 443)
                and url.username is None
                and url.password is None
                and not url.query
                and not url.fragment
            ):
                return PROVIDER_BASES.get("https://" + url.hostname + url.path.rstrip("/"))
            return local_endpoint(value)
        except ValueError:
            pass
    return None


@dataclass(frozen=True)
class Routes:
    defaults: dict
    overrides: dict
    aliases: dict
    metadata: dict
    ollama_library: str | None = None


def _workspace_effort(value):
    if not isinstance(value, dict) or type(value.get("supported")) is not bool:
        return None
    levels = []
    for name, descriptor in value.items():
        if name == "supported":
            continue
        if (
            not cfg.EFFORT_LEVEL.fullmatch(name)
            or not isinstance(descriptor, dict)
            or type(descriptor.get("supported")) is not bool
        ):
            return None
        if descriptor["supported"]:
            levels.append(name)
    if not value["supported"]:
        return None if levels else []
    preferred = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
    return (
        sorted(levels, key=lambda name: preferred.index(name) if name in preferred else len(preferred))
        or None
    )


def routing(root, options):
    """Freeze Aider routing at launch without retaining the provider environment."""
    env, _ = cfg.provider_environment(root)
    defaults = {}
    for provider, variables in BASE_VARS.items():
        destinations = {_destination(env.values[key]) for key in variables if env.values.get(key)}
        if destinations:
            defaults[provider] = destinations.pop() if len(destinations) == 1 else None
        else:
            defaults[provider] = local_endpoint(None) if provider == "ollama" else provider

    overrides = {}
    for path in cfg.candidate_files(root, cfg.MODELS, options.get("model-settings-file")):
        for entry in cfg.parse(envfile.read_text(path), cfg.MODELS):
            name = entry["name"]
            params = entry.get("extra_params") or {}
            overrides.pop(name, None)
            if not isinstance(params, dict) or any(
                key in params for key in ("custom_llm_provider", "deployment_id", "model")
            ):
                overrides[name] = None
            elif params.get("api_base") or params.get("base_url"):
                overrides[name] = _destination(params.get("api_base") or params["base_url"])

    metadata = {}
    for path in cfg.candidate_files(root, cfg.METADATA, options.get("model-metadata-file")):
        for name, entry in cfg.parse(envfile.read_text(path), cfg.METADATA).items():
            metadata.pop(name, None)
            caps = entry.get("capabilities") if isinstance(entry, dict) else None
            if isinstance(caps, dict) and "effort" in caps:
                metadata[name] = _workspace_effort(caps["effort"])

    aliases = {}
    for definition in options.get("alias") or []:
        alias, colon, target = definition.partition(":")
        if colon:
            aliases[alias.strip()] = target.strip()
    try:
        # The Ollama panel uses OLLAMA_HOST when no workspace API base is set.
        library = local_endpoint(env.values.get("OLLAMA_API_BASE") or os.getenv("OLLAMA_HOST"))
    except ValueError:
        library = None
    return Routes(defaults, overrides, aliases, metadata, library)


def _result(status, levels, source, message, source_url=None):
    return dict(status=status, levels=levels, source=source, source_url=source_url, message=message)


def _ollama(model, destination, *, compatible=False):
    source = "Ollama /api/show for its OpenAI-compatible route" if compatible else "Ollama /api/show"
    url = OLLAMA_COMPAT_DOC if compatible else OLLAMA_DOC
    if not destination or not destination.startswith("http://"):
        return _result(
            "unknown", [], source, "Deck can read model details from a loopback Ollama server.", url
        )
    try:
        details = OllamaService(destination).model_details(model, timeout=2)
    except (OllamaError, ValueError):
        return _result(
            "unknown",
            [],
            source,
            "Ollama model details are unavailable. Check that the server is running and the tag is installed.",
            url,
        )
    thinking = details.get("thinking")
    values = thinking.get("values") if isinstance(thinking, dict) else None
    if (
        not isinstance(values, list)
        or not values
        or any(
            type(value) is not bool
            and (not isinstance(value, str) or not cfg.EFFORT_LEVEL.fullmatch(value))
            for value in values
        )
    ):
        return _result(
            "unknown", [], source, "Ollama did not list usable thinking levels for this tag.", url
        )
    levels = list(dict.fromkeys(value for value in values if isinstance(value, str)))
    if levels:
        message = "Ollama reports these named thinking levels."
        if any(isinstance(value, bool) for value in values):
            message += " Ollama also has native boolean thinking controls."
        if compatible:
            message += " Ollama accepts these values in reasoning_effort on its OpenAI-compatible route."
        else:
            message += (" Aider 0.86.2 sends reasoning_effort on its native chat route. "
                        "To apply a named level, use openai/<tag> with Ollama’s local /v1 endpoint.")
        return _result("known", levels, source, message, url)
    if compatible and any(values):
        return _result(
            "unknown",
            [],
            source,
            "This model lists boolean thinking controls only. On Ollama’s OpenAI-compatible route, recognized effort aliases enable thinking and none requests it off.",
            url,
        )
    message = (
        "Ollama lists a native boolean thinking control only. This model has no named effort levels."
        if any(values)
        else "Ollama reports that this model does not support thinking."
    )
    return _result("unsupported", [], source, message, url)


def model_route(model, routes):
    """Classify a resolved Aider model without contacting its provider."""
    provider, slash, name = model.partition("/")
    if not slash:
        # Recognize only documented, unambiguous bare IDs. Gemini users can
        # select a Vertex deployment or a Gemini endpoint under different IDs.
        name = model
        provider = next((key for key in ("openai", "anthropic") if name in CLOUD[key][0]), "")
    if provider == "ollama_chat":
        provider = "ollama"
    destination = routes.overrides.get(
        "aider/extra_params", routes.overrides.get(model, routes.defaults.get(provider))
    )
    if provider == "openai" and routes.ollama_library and destination == routes.ollama_library + "/v1":
        return "ollama-openai", name, routes.ollama_library
    return provider, name, destination


def _discover(model, routes):
    provider, name, destination = model_route(model, routes)
    if provider in ("ollama", "ollama-openai"):
        return _ollama(name, destination, compatible=provider == "ollama-openai")

    if model in routes.metadata:
        levels = routes.metadata[model]
        if levels is None:
            return _result(
                "unknown",
                [],
                "Workspace model metadata",
                "The metadata has no valid list of effort levels.",
                METADATA_DOC,
            )
        status = "known" if levels else "unsupported"
        message = (
            "The model metadata lists these effort levels."
            if levels
            else "The model metadata reports that effort is unsupported."
        )
        return _result(status, levels, "Workspace model metadata", message, METADATA_DOC)
    if provider in CLOUD and destination == provider:
        catalog, source, url = CLOUD[provider]
        if name in catalog:
            if provider == "openai":
                url = OPENAI_PAGES.get(name, OPENAI_DOC + "/" + name)
            levels = catalog[name]
            status = "known" if levels else "unsupported"
            message = (
                "These levels come from the published model documentation."
                if levels
                else "The provider documents this as a non-reasoning model."
            )
            return _result(status, list(levels), source, message, url)
    return _result(
        "unknown", [], "Manual entry", "Deck has no verified effort list for this model and endpoint."
    )


def discover(model, routes, *, resolve_alias=True):
    """Return the provider evidence for an exact model, including a documented manual fallback."""
    if not isinstance(model, str):
        model = "" if model is None else str(model)
    resolved = routes.aliases.get(model, model) if resolve_alias else model
    result = _discover(resolved, routes)
    return {"model": model, "resolved_model": resolved, **result}
