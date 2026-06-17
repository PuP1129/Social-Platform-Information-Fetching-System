from __future__ import annotations

from typing import Any


FAILURE_CATEGORIES = {
    "unavailable",
    "login_required",
    "rate_limited",
    "navigation_timeout",
    "network_error",
    "extraction_error",
    "invalid_url",
    "validation_error",
    "unknown_error",
}

NON_RETRYABLE = {"unavailable", "invalid_url"}
RETRYABLE = {"navigation_timeout", "network_error", "rate_limited", "extraction_error"}
MANUAL = {"login_required"}


def categorize_failure(record_or_error: dict[str, Any] | Exception | str) -> tuple[str, bool]:
    text = _failure_text(record_or_error)
    lowered = text.lower()
    if "unavailable" in lowered or "not available" in lowered or "does not exist" in lowered:
        return "unavailable", False
    if "login" in lowered or "authentication state" in lowered or "storage state" in lowered:
        return "login_required", False
    if "rate" in lowered or "too many" in lowered:
        return "rate_limited", True
    if "timeout" in lowered or "timed out" in lowered:
        return "navigation_timeout", True
    if "network" in lowered or "net::" in lowered or "connection" in lowered:
        return "network_error", True
    if "unsupported" in lowered or "invalid" in lowered or "url" in lowered and "supported" in lowered:
        return "invalid_url", False
    if "validation" in lowered:
        return "validation_error", False
    if "extract" in lowered or "container" in lowered or "field" in lowered:
        return "extraction_error", True
    return "unknown_error", False


def enrich_failure_record(record: dict[str, Any], attempt_count: int) -> dict[str, Any]:
    enriched = dict(record)
    category, retryable = categorize_failure(record)
    enriched["attempt_count"] = attempt_count
    enriched["failure_category"] = category
    enriched["retryable"] = retryable
    if isinstance(enriched.get("error"), dict):
        enriched["last_error"] = enriched["error"].get("message")
    else:
        enriched["last_error"] = enriched.get("error")
    return enriched


def _failure_text(value: dict[str, Any] | Exception | str) -> str:
    if isinstance(value, Exception):
        return f"{type(value).__name__}: {value}"
    if isinstance(value, str):
        return value
    error = value.get("error")
    if isinstance(error, dict):
        return f"{error.get('type', '')}: {error.get('message', '')}"
    return str(error or value.get("last_error") or value.get("skip_reason") or "")
