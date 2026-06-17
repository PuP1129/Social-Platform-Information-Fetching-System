from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone


RELATIVE_TIME_RE = re.compile(
    r"^\s*(?:(?P<count>\d+)\s*)?(?P<unit>s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks|y|yr|yrs|year|years)\s*$",
    re.IGNORECASE,
)


def normalize_facebook_time(value: str | None, collected_at: str | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    absolute = _parse_iso_datetime(text)
    if absolute is not None:
        return _format_datetime(absolute)
    base = _parse_iso_datetime(collected_at) if collected_at else None
    relative = relative_time_to_datetime(text, base)
    if relative is None:
        return ""
    if _is_day_precision(text):
        return relative.date().isoformat()
    return _format_datetime(relative)


def relative_time_to_datetime(value: str | None, base: datetime | None = None) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    reference = base or datetime.now(timezone.utc).replace(microsecond=0)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    if text in {"just now", "now"}:
        return reference
    match = RELATIVE_TIME_RE.fullmatch(text)
    if not match:
        return None
    count = int(match.group("count") or "1")
    unit = match.group("unit").lower()
    if unit.startswith("s"):
        delta = timedelta(seconds=count)
    elif unit.startswith("m"):
        delta = timedelta(minutes=count)
    elif unit.startswith("h"):
        delta = timedelta(hours=count)
    elif unit.startswith("d"):
        delta = timedelta(days=count)
    elif unit.startswith("w"):
        delta = timedelta(weeks=count)
    else:
        delta = timedelta(days=365 * count)
    return (reference - delta).replace(microsecond=0)


def is_relative_time_text(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip().lower()
    return text in {"just now", "now"} or RELATIVE_TIME_RE.fullmatch(text) is not None


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        if text.endswith("Z"):
            parsed = datetime.fromisoformat(text[:-1] + "+00:00")
        else:
            parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_day_precision(value: str) -> bool:
    match = RELATIVE_TIME_RE.fullmatch(value.strip())
    if not match:
        return False
    unit = match.group("unit").lower()
    return unit.startswith(("d", "w", "y"))
