from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


SCHEMA_VERSION = "1.0"
SUPPORTED_PLATFORMS = {"x", "facebook"}
NORMALIZED_SOCIAL_POST_FIELDS = (
    "schema_version",
    "platform",
    "post_id",
    "input_url",
    "canonical_url",
    "author",
    "content",
    "publish_time",
    "comment_count",
    "like_count",
    "share_count",
    "repost_count",
    "view_count",
    "comments",
    "collected_at",
    "collection_status",
    "error",
    "platform_metrics",
    "platform_context",
)

_X_HANDLE_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_COUNTER_PATTERN = re.compile(r"^\+\d+$")
_RELATIVE_TIME_PATTERN = re.compile(
    r"^\d+\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks|mo|month|months|y|yr|yrs|year|years)$",
    re.IGNORECASE,
)
_METRIC_AUTHOR_PATTERN = re.compile(
    r"^\d[\d,]*(?:\.\d+)?\s*(k|m|b|万|亿)?\s*(comments?|shares?|reactions?|likes?)$",
    re.IGNORECASE,
)
_TIMESTAMP_AUTHOR_PATTERN = re.compile(
    r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)\b.*\b(am|pm|\d{4})?$",
    re.IGNORECASE,
)
_INVALID_AUTHOR_NAMES = {"join", "like", "comment", "share", "most relevant"}
_SAFE_COMMENT_FIELDS = ("comment_content", "comment_time", "comment_author")


class SocialPostNormalizationError(ValueError):
    """Raised when a record cannot be mapped to the shared social-post schema."""


def normalize_social_post(record: Mapping[str, Any]) -> dict[str, Any]:
    platform = _optional_string(record.get("platform"))
    if platform not in SUPPORTED_PLATFORMS:
        raise SocialPostNormalizationError("platform must be 'x' or 'facebook'.")

    comment_count = _count_or_none(
        record.get("reply_count") if platform == "x" else record.get("comment_count")
    )
    if platform == "x" and comment_count is None:
        comment_count = _count_or_none(record.get("comment_count"))

    author = _normalize_author(record, platform)
    collection_status = _normalized_collection_status(record, platform, author)
    error = _normalize_error(record.get("error"))
    if collection_status == "success" and error == "Missing core field(s): author.":
        error = None
    elif collection_status == "partial" and not (author.get("name") or author.get("profile_url")) and error is None:
        error = "Missing core field(s): author."

    return {
        "schema_version": SCHEMA_VERSION,
        "platform": platform,
        "post_id": _optional_string(record.get("post_id")),
        "input_url": _optional_string(record.get("input_url")),
        "canonical_url": _optional_string(record.get("canonical_url")),
        "author": author,
        "content": _optional_string(record.get("content")),
        "publish_time": _optional_string(record.get("publish_time")),
        "comment_count": comment_count,
        "like_count": _count_or_none(record.get("like_count")),
        "share_count": _count_or_none(record.get("share_count")),
        "repost_count": _count_or_none(record.get("repost_count")) if platform == "x" else None,
        "view_count": _count_or_none(record.get("view_count")) if platform == "x" else None,
        "comments": _normalize_comments(record.get("comments")),
        "collected_at": _optional_string(record.get("collected_at")),
        "collection_status": collection_status,
        "error": error,
        "platform_metrics": _platform_metrics(record, platform, comment_count),
        "platform_context": _platform_context(record, platform),
    }


def _normalize_author(record: Mapping[str, Any], platform: str) -> dict[str, str | None]:
    value = record.get("author")
    if isinstance(value, Mapping):
        name = _optional_string(value.get("name"))
        handle = _optional_string(value.get("handle"))
        profile_url = _optional_string(value.get("profile_url"))
        author_type = _author_type_or_default(value.get("type"), platform)
    else:
        name = _optional_string(value)
        handle = None
        profile_url = None
        author_type = "user" if platform == "x" else "unknown"

    if platform == "x" and handle:
        normalized_handle = handle if handle.startswith("@") else f"@{handle}"
        username = normalized_handle[1:]
        handle = normalized_handle
        if profile_url is None and _X_HANDLE_PATTERN.fullmatch(username):
            profile_url = f"https://x.com/{username}"
        author_type = "user"

    if name and not _is_valid_author_name(name):
        name = None
        if platform == "facebook":
            profile_url = None
            author_type = "unknown"

    if platform == "facebook" and not (name or profile_url):
        source = record.get("source")
        if (
            isinstance(source, Mapping)
            and source.get("type") == "group"
            and _optional_string(source.get("name"))
            and _is_valid_author_name(str(source.get("name")))
        ):
            name = _optional_string(source.get("name"))
            handle = None
            profile_url = _optional_string(source.get("profile_url"))
            author_type = "group"

    return {"name": name, "handle": handle, "profile_url": profile_url, "type": author_type}


def _is_valid_author_name(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", value).strip()
    lowered = normalized.lower()
    if not normalized or lowered in _INVALID_AUTHOR_NAMES:
        return False
    if _COUNTER_PATTERN.fullmatch(normalized):
        return False
    if _RELATIVE_TIME_PATTERN.fullmatch(normalized):
        return False
    if _METRIC_AUTHOR_PATTERN.fullmatch(normalized):
        return False
    if _TIMESTAMP_AUTHOR_PATTERN.match(normalized):
        return False
    if not re.search(r"[\w\u4e00-\u9fff]", normalized, flags=re.UNICODE):
        return False
    return True


def _author_type_or_default(value: Any, platform: str) -> str:
    if isinstance(value, str) and value in {"user", "page", "group", "unknown"}:
        return value
    return "user" if platform == "x" else "unknown"


def _normalized_collection_status(
    record: Mapping[str, Any],
    platform: str,
    author: Mapping[str, Any],
) -> str | None:
    status = _optional_string(record.get("collection_status"))
    has_author = bool(author.get("name") or author.get("profile_url"))
    has_content = bool(_optional_string(record.get("content")))
    has_identity = bool(_optional_string(record.get("post_id")) or _optional_string(record.get("canonical_url")))
    if (
        platform == "facebook"
        and status == "partial"
        and author.get("type") == "group"
        and _optional_string(author.get("name"))
        and has_identity
        and has_content
    ):
        return "success"
    if status == "success" and not (has_author and has_content and has_identity):
        return "partial"
    return status


def _platform_metrics(
    record: Mapping[str, Any],
    platform: str,
    comment_count: int | None,
) -> dict[str, Any]:
    if platform == "x":
        return {
            "reply_count": _count_or_none(record.get("reply_count"))
            if "reply_count" in record
            else comment_count,
        }
    return {}


def _platform_context(record: Mapping[str, Any], platform: str) -> dict[str, Any]:
    if platform == "x":
        return {}
    context: dict[str, Any] = {}
    source = record.get("source")
    if isinstance(source, Mapping) and source.get("type") == "group":
        name = _optional_string(source.get("name"))
        profile_url = _optional_string(source.get("profile_url"))
        if name or profile_url:
            context["facebook_group"] = {
                "name": name,
                "url": profile_url,
            }
    return context


def _normalize_comments(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    comments: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        comment = {field: _optional_string(item.get(field)) for field in _SAFE_COMMENT_FIELDS}
        if any(comment.values()):
            comments.append(comment)
    return comments


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_error(value: Any) -> str | None:
    if isinstance(value, Mapping):
        error_type = _optional_string(value.get("type"))
        message = _optional_string(value.get("message"))
        if error_type and message:
            return f"{error_type}: {message}"
        return message or error_type
    return _optional_string(value)


def _count_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) and value >= 0 else None
