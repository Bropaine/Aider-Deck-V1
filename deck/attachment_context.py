"""Observed model budget and local estimates for external text attachments."""

import string
from pathlib import Path

from .attachment_validation import IMAGE_FORMAT


def _count(value):
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def context_budget(snapshot, configured, runner):
    """Use the live model, the most recent valid usage, and a labeled window."""
    model = snapshot.get("model") if snapshot.get("status") == "live" else None
    runner = runner if model and runner and runner.get("model") == model else {}
    local = bool(runner or (model or "").lower().startswith(("ollama/", "ollama_chat/")))
    tokens = snapshot.get("tokens") or {}
    report = tokens.get("data") or {}
    matches = bool(model and report.get("model") == model)
    current = tokens.get("stale") is False and (matches or (model and not local and not report.get("model")))
    reported_limit = report.get("max") if matches or current else None
    source, window = None, None
    for label, candidate in (
        ("Ollama loaded window", runner.get("window")),
        ("Ollama model default", runner.get("model_default")),
        ("Configured input limit", configured if model else None),
        ("Ollama model capacity", runner.get("model_capacity")),
        ("Aider input limit", reported_limit),
    ):
        if _count(candidate):
            source, window = label, candidate
            break
    used = _count(report.get("used")) if current else None
    usage_source = "report" if used is not None else None
    reply = snapshot.get("reply_tokens") or {}
    if used is None and model and reply.get("model") == model:
        used = _count(reply.get("used"))
        if used is not None:
            usage_source = "reply"
    remaining = max(0, window - used) if window is not None and used is not None else None
    return dict(model=model, window=window, source=source, used=used,
                remaining=remaining, usage_source=usage_source)


# A local estimate avoids sending prospective attachment contents to a provider.
# Treat punctuation and non-ASCII UTF-8 more conservatively than prose.
_PROSE = (string.ascii_letters + string.digits + string.whitespace).encode("ascii")


def text_batch_estimate(uploads):
    total, has_text, has_images = 0, False, False
    for name, content in uploads:
        if Path(name).suffix.lower() in IMAGE_FORMAT:
            has_images = True
            continue
        has_text = True
        dense = len(content.translate(None, _PROSE))
        total += dense + (len(content) - dense + 2) // 3
        total += len(name.encode("utf-8")) + 16  # File label and Aider formatting.
    return (total if has_text else None), has_images


def attachment_warning(budget, estimate, has_images, changing):
    if estimate is None:
        return None
    remaining = None if changing else budget["remaining"]
    if remaining is None:
        reason = "unknown"
    elif estimate > remaining:
        reason = "over"
    elif estimate * 10 >= remaining * 9:
        reason = "near"
    elif budget["source"] == "Ollama model capacity":
        reason = "capacity"
    elif has_images:
        reason = "mixed"
    else:
        return None
    return dict(model=budget["model"], estimated_tokens=estimate, remaining_tokens=remaining,
                window_tokens=budget["window"], source=budget["source"], reason=reason,
                has_images=has_images)
