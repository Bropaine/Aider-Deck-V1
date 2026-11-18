"""Offline provider references, picker candidates and effective credential presence."""

from dataclasses import dataclass
import re

from . import aider_config as cfg
from . import envfile
from . import reasoning_capabilities as effort
from .ollama_routes import service_at
from .staged_scan import scan_text


MODEL_ID = re.compile(r"[\w][\w.:/+@-]{0,511}\Z")


def safe_model_id(name, protected):
    return (isinstance(name, str) and MODEL_ID.fullmatch(name) and "//" not in name
            and not any(secret in name for secret in protected)
            and not scan_text(name.encode(), "model ID"))


PROVIDERS = {"openai": "OpenAI", "anthropic": "Anthropic", "gemini": "Google Gemini"}
# Additional published chat IDs, with the provider's public catalog for review.
OTHER_CATALOGS = {
    "cohere_chat": ("Cohere", ("command-a-03-2025",), "https://docs.cohere.com/docs/models"),
    "deepseek": ("DeepSeek", ("deepseek-flash", "deepseek-v4-pro"), "https://api-docs.deepseek.com/guides/codex"),
    "groq": ("Groq", ("openai/gpt-oss-120b", "openai/gpt-oss-20b"), "https://console.groq.com/docs/models"),
    "mistral": ("Mistral", ("mistral-medium-3-5", "devstral-2512"), "https://docs.mistral.ai/models"),
    "openrouter": ("OpenRouter", ("openai/gpt-oss-120b",), "https://openrouter.ai/openai/gpt-oss-120b"),
}
KEY_NAMES = {
    "cohere_chat": ("COHERE_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "azure": ("AZURE_API_KEY", "AZURE_AD_TOKEN"),
    "huggingface": ("HUGGINGFACE_API_KEY", "HF_TOKEN"),
}
# Link to published family pages. A locally chosen quantization or private
# namespace may have no public tag page. Never send it in a search query.
OLLAMA_FAMILIES = {"deepseek-r1", "gemma3", "gpt-oss", "llama3.2", "llama3.3",
                   "nemotron-3.5-bolt", "qwen2.5-coder", "qwen3", "qwen3-coder", "smollm2"}
OLLAMA_SEARCH = "https://ollama.com/search"


def catalog_link(model, routes):
    provider, name, _ = effort.model_route(model, routes)
    if provider in ("ollama", "ollama-openai"):
        family = name.removeprefix("registry.ollama.ai/").removeprefix("library/").split(":", 1)[0]
        if family in OLLAMA_FAMILIES:
            return {"label": "Ollama model page · " + family, "url": "https://ollama.com/library/" + family}
        return {"label": "Browse Ollama models", "url": OLLAMA_SEARCH}
    if provider == "openai" and name in effort.OPENAI:
        return {"label": "OpenAI model catalog",
                "url": effort.OPENAI_PAGES.get(name, effort.OPENAI_DOC + "/" + name)}
    if provider == "anthropic" and name in effort.CLAUDE:
        return {"label": "Anthropic model catalog", "url": "https://platform.claude.com/docs/en/models/overview"}
    if provider == "gemini" and name in effort.GEMINI:
        return {"label": "Gemini model catalog", "url": "https://ai.google.dev/gemini-api/docs/models"}
    if provider in OTHER_CATALOGS:
        label, names, url = OTHER_CATALOGS[provider]
        if name in names:
            return {"label": label + " model catalog", "url": url}
    return None


@dataclass
class ModelAccess:
    routes: effort.Routes
    configured_keys: set
    overrides: dict

    def key_warning(self, model):
        resolved = self.routes.aliases.get(model, model)
        provider, name, _ = effort.model_route(resolved, self.routes)
        if provider in ("ollama", "ollama-openai"):
            if name.casefold().endswith((":cloud", "-cloud")) and "OLLAMA_API_KEY" not in self.configured_keys:
                return "Configure OLLAMA_API_KEY in Keys to enable this Ollama cloud tag."
            return ""
        if not provider:
            if name.startswith(("gpt-", "o1-", "o3-", "o4-")):
                provider = "openai"
            elif name.startswith("claude-"):
                provider = "anthropic"
            elif name.startswith("gemini-"):
                provider = "gemini"
        override = self.overrides.get("aider/extra_params", self.overrides.get(resolved))
        if override is True:
            return ""
        if not provider or not re.fullmatch(r"[a-zA-Z0-9_-]+", provider):
            return "Use a provider-qualified model ID so Deck can check its API key in Keys."
        names = KEY_NAMES.get(provider, (provider.upper().replace("-", "_") + "_API_KEY",))
        if override is None and self.configured_keys.intersection(names):
            return ""
        if override is False:
            return "The API key override in this model’s Aider settings is empty or unresolved."
        return "Configure " + " or ".join(names) + " in Keys to enable this model."


def model_access(root):
    options = cfg.launch_values(root)
    routes = effort.routing(root, options)
    environment, _ = cfg.provider_environment(root)
    present = {key for key, value in environment.values.items() if isinstance(value, str) and value.strip()}
    overrides = {}
    for path in cfg.candidate_files(root, cfg.MODELS, options.get("model-settings-file")):
        for entry in cfg.parse(envfile.read_text(path), cfg.MODELS):
            name, params = entry["name"], entry.get("extra_params")
            overrides.pop(name, None)
            if isinstance(params, dict) and "api_key" in params:
                value = params["api_key"]
                if isinstance(value, str) and value.startswith("os.environ/"):
                    overrides[name] = value.removeprefix("os.environ/") in present
                else:
                    overrides[name] = bool(isinstance(value, str) and value.strip())
    return ModelAccess(routes, present, overrides)


def model_candidates(root, saved_tags, safe_name):
    access = model_access(root)
    groups = []
    warnings = {}

    def group(label, names):
        models = sorted({name for name in names if safe_name(name)}, key=str.casefold)
        groups.append({"label": label, "models": models})
        for model in models:
            warning = access.key_warning(model)
            if warning:
                warnings[model] = warning

    for provider, label in PROVIDERS.items():
        group(label, (provider + "/" + model for model in effort.CLOUD[provider][0]))
    for provider, (label, names, _) in OTHER_CATALOGS.items():
        group(label, (provider + "/" + model for model in names))
    endpoint = access.routes.ollama_library
    status = service_at(endpoint).status() if endpoint else {}
    group("Installed Ollama", ("ollama_chat/" + m["name"] for m in status.get("installed", [])
                                if not m.get("remote")))
    group("Saved Ollama tags", ("ollama_chat/" + tag for tag in saved_tags))
    group("Ollama library examples", ("ollama_chat/" + tag for tag in (
        "qwen2.5-coder:1.5b", "gpt-oss:20b", "nemotron-3.5-bolt:e4b", "smollm2:135m")))
    group("Workspace models", (name for name in cfg.registrations(root) if name != "aider/extra_params"))
    message = "" if status.get("running") else "Start the workspace’s local Ollama service to list installed models."
    return {"groups": groups, "local_message": message, "warnings": warnings}
