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
)

_X_HANDLE_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,15}$")
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

    return {
        "schema_version": SCHEMA_VERSION,
        "platform": platform,
        "post_id": _optional_string(record.get("post_id")),
        "input_url": _optional_string(record.get("input_url")),
        "canonical_url": _optional_string(record.get("canonical_url")),
        "author": _normalize_author(record.get("author"), platform),
        "content": _optional_string(record.get("content")),
        "publish_time": _optional_string(record.get("publish_time")),
        "comment_count": comment_count,
        "like_count": _count_or_none(record.get("like_count")),
        "share_count": _count_or_none(record.get("share_count")),
        "repost_count": _count_or_none(record.get("repost_count")) if platform == "x" else None,
        "view_count": _count_or_none(record.get("view_count")) if platform == "x" else None,
        "comments": _normalize_comments(record.get("comments")),
        "collected_at": _optional_string(record.get("collected_at")),
        "collection_status": _optional_string(record.get("collection_status")),
        "error": _optional_string(record.get("error")),
        "platform_metrics": _platform_metrics(record, platform, comment_count),
    }


def _normalize_author(value: Any, platform: str) -> dict[str, str | None]:
    if isinstance(value, Mapping):
        name = _optional_string(value.get("name"))
        handle = _optional_string(value.get("handle"))
        profile_url = _optional_string(value.get("profile_url"))
    else:
        name = _optional_string(value)
        handle = None
        profile_url = None

    if platform == "x" and handle:
        normalized_handle = handle if handle.startswith("@") else f"@{handle}"
        username = normalized_handle[1:]
        handle = normalized_handle
        if profile_url is None and _X_HANDLE_PATTERN.fullmatch(username):
            profile_url = f"https://x.com/{username}"

    return {"name": name, "handle": handle, "profile_url": profile_url}


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


def _count_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) and value >= 0 else None
