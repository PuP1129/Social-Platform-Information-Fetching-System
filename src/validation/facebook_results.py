from __future__ import annotations

from typing import Any

from src.utils.facebook_time import normalize_facebook_time


FINAL_RESULT_FIELDS = (
    "author",
    "content",
    "publish_time",
    "like_count",
    "share_count",
    "comment_count",
    "comments",
)

FINAL_COMMENT_FIELDS = (
    "comment_content",
    "comment_time",
    "comment_author",
)

VALID_COLLECTION_STATUSES = {"success", "partial", "failed", "skipped"}


class FacebookResultValidationError(ValueError):
    """Raised when a Facebook result does not match the expected schema."""


def validate_internal_result(record: dict[str, Any]) -> None:
    required = ("platform", "input_url", "canonical_url", "post_id", "collection_status")
    missing = [field for field in required if field not in record]
    if missing:
        raise FacebookResultValidationError(f"Internal result missing field(s): {', '.join(missing)}")
    if record.get("platform") != "facebook":
        raise FacebookResultValidationError("Internal result platform must be facebook.")
    if record.get("collection_status") not in VALID_COLLECTION_STATUSES:
        raise FacebookResultValidationError("Internal result collection_status is invalid.")
    for field in ("like_count", "share_count", "comment_count", "reply_count"):
        value = record.get(field)
        if value is not None and (not isinstance(value, int) or value < 0):
            raise FacebookResultValidationError(f"Internal result {field} must be a non-negative integer or null.")
    if "input_metadata" in record and not isinstance(record["input_metadata"], dict):
        raise FacebookResultValidationError("Internal result input_metadata must be an object.")
    if "search_contexts" in record and not isinstance(record["search_contexts"], list):
        raise FacebookResultValidationError("Internal result search_contexts must be a list.")
    if record.get("collection_status") == "failed" and not (record.get("error") or record.get("failure_category")):
        raise FacebookResultValidationError("Failed internal result must include error or failure_category.")


def to_final_facebook_result(record: dict[str, Any]) -> dict[str, Any]:
    author = record.get("author")
    if isinstance(author, dict):
        author_value = _string_or_empty(author.get("name"))
    else:
        author_value = _string_or_empty(author)

    publish_time = record.get("publish_time") or record.get("publish_time_text")
    normalized_publish_time = normalize_facebook_time(
        publish_time if isinstance(publish_time, str) else None,
        record.get("collected_at") if isinstance(record.get("collected_at"), str) else None,
    )
    final = {
        "author": author_value,
        "content": _string_or_empty(record.get("content")),
        "publish_time": normalized_publish_time,
        "like_count": _count_or_none(record.get("like_count")),
        "share_count": _count_or_none(record.get("share_count")),
        "comment_count": _count_or_none(record.get("comment_count")),
        "comments": _final_comments(
            record.get("comments"),
            record.get("collected_at") if isinstance(record.get("collected_at"), str) else None,
        ),
    }
    validate_final_result(final)
    return final


def validate_final_result(record: dict[str, Any]) -> None:
    if tuple(record.keys()) != FINAL_RESULT_FIELDS:
        raise FacebookResultValidationError("Final result fields do not match the fixed Facebook schema.")
    if not isinstance(record["comments"], list):
        raise FacebookResultValidationError("Final result comments must be a list.")
    for field in ("author", "content", "publish_time"):
        if not isinstance(record[field], str):
            raise FacebookResultValidationError(f"Final result {field} must be a string.")
    for field in ("like_count", "share_count", "comment_count"):
        value = record[field]
        if value is not None and (not isinstance(value, int) or value < 0):
            raise FacebookResultValidationError(f"Final result {field} must be a non-negative integer or null.")
    for comment in record["comments"]:
        validate_final_comment(comment)


def validate_final_comment(comment: dict[str, Any]) -> None:
    if tuple(comment.keys()) != FINAL_COMMENT_FIELDS:
        raise FacebookResultValidationError("Final comment fields do not match the fixed Facebook comment schema.")
    for field in FINAL_COMMENT_FIELDS:
        if not isinstance(comment[field], str):
            raise FacebookResultValidationError(f"Final comment {field} must be a string.")


def _final_comments(value: Any, collected_at: str | None = None) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    comments: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        content = _string_or_empty(item.get("comment_content"))
        if not content:
            continue
        comment = {
            "comment_content": content,
            "comment_time": normalize_facebook_time(
                item.get("comment_time") if isinstance(item.get("comment_time"), str) else None,
                item.get("collected_at") if isinstance(item.get("collected_at"), str) else collected_at,
            ),
            "comment_author": _string_or_empty(item.get("comment_author")),
        }
        key = (comment["comment_content"], comment["comment_time"], comment["comment_author"])
        if key in seen:
            continue
        seen.add(key)
        comments.append(comment)
    return comments


def _string_or_empty(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _count_or_none(value: Any) -> int | None:
    if isinstance(value, int) and value >= 0:
        return value
    return None
