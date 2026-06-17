from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SearchConfig:
    max_results_per_keyword: int = 20
    page_size: int = 10


@dataclass(frozen=True)
class FacebookRuntimeConfig:
    storage_state: Path | None = None
    headless: bool = True
    delay_seconds: float = 2.0
    save_debug_bundle: bool = False


@dataclass(frozen=True)
class CommentsConfig:
    enabled: bool = True
    max_comments_per_post: int = 100
    expand_comments: bool = True
    max_expand_actions: int = 30
    no_progress_round_limit: int = 3


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 2
    retry_delay_seconds: float = 5.0


@dataclass(frozen=True)
class HistoryConfig:
    enabled: bool = True
    index_path: Path = Path("output/history/facebook_posts.jsonl")
    skip_statuses: tuple[str, ...] = ("success", "partial")
    retry_failed: bool = True


@dataclass(frozen=True)
class OutputConfig:
    root_dir: Path = Path("output/runs")
    export_csv: bool = True
    generate_report: bool = True


@dataclass(frozen=True)
class FacebookJobConfig:
    job_name: str
    platform: str
    keywords: tuple[str, ...]
    search: SearchConfig
    facebook: FacebookRuntimeConfig
    comments: CommentsConfig
    retry: RetryConfig
    history: HistoryConfig
    output: OutputConfig


def load_facebook_job_config(path: Path) -> FacebookJobConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ValueError(f"Could not read Facebook job config: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Facebook job config is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Facebook job config must be a JSON object.")
    return parse_facebook_job_config(payload)


def parse_facebook_job_config(payload: dict[str, Any]) -> FacebookJobConfig:
    job_name = _required_string(payload, "job_name")
    platform = _required_string(payload, "platform")
    if platform != "facebook":
        raise ValueError("facebook job config platform must be facebook.")
    keywords = _keywords(payload.get("keywords"))

    search_payload = _object(payload.get("search"), "search")
    facebook_payload = _object(payload.get("facebook"), "facebook")
    comments_payload = _object(payload.get("comments"), "comments")
    retry_payload = _object(payload.get("retry"), "retry")
    history_payload = _object(payload.get("history"), "history")
    output_payload = _object(payload.get("output"), "output")

    search = SearchConfig(
        max_results_per_keyword=_positive_int(search_payload.get("max_results_per_keyword", 20), "search.max_results_per_keyword"),
        page_size=_positive_int(search_payload.get("page_size", 10), "search.page_size"),
    )
    facebook = FacebookRuntimeConfig(
        storage_state=_optional_path(facebook_payload.get("storage_state"), "facebook.storage_state"),
        headless=_bool(facebook_payload.get("headless", True), "facebook.headless"),
        delay_seconds=_non_negative_float(facebook_payload.get("delay_seconds", 2.0), "facebook.delay_seconds"),
        save_debug_bundle=_bool(facebook_payload.get("save_debug_bundle", False), "facebook.save_debug_bundle"),
    )
    comments = CommentsConfig(
        enabled=_bool(comments_payload.get("enabled", True), "comments.enabled"),
        max_comments_per_post=_non_negative_int(
            comments_payload.get("max_comments_per_post", 100),
            "comments.max_comments_per_post",
        ),
        expand_comments=_bool(comments_payload.get("expand_comments", True), "comments.expand_comments"),
        max_expand_actions=_non_negative_int(comments_payload.get("max_expand_actions", 30), "comments.max_expand_actions"),
        no_progress_round_limit=_non_negative_int(
            comments_payload.get("no_progress_round_limit", 3),
            "comments.no_progress_round_limit",
        ),
    )
    retry = RetryConfig(
        max_attempts=_positive_int(retry_payload.get("max_attempts", 2), "retry.max_attempts"),
        retry_delay_seconds=_non_negative_float(retry_payload.get("retry_delay_seconds", 5.0), "retry.retry_delay_seconds"),
    )
    history = HistoryConfig(
        enabled=_bool(history_payload.get("enabled", True), "history.enabled"),
        index_path=_path(history_payload.get("index_path", "output/history/facebook_posts.jsonl"), "history.index_path"),
        skip_statuses=tuple(_string_list(history_payload.get("skip_statuses", ["success", "partial"]), "history.skip_statuses")),
        retry_failed=_bool(history_payload.get("retry_failed", True), "history.retry_failed"),
    )
    output = OutputConfig(
        root_dir=_path(output_payload.get("root_dir", "output/runs"), "output.root_dir"),
        export_csv=_bool(output_payload.get("export_csv", True), "output.export_csv"),
        generate_report=_bool(output_payload.get("generate_report", True), "output.generate_report"),
    )
    return FacebookJobConfig(job_name, platform, keywords, search, facebook, comments, retry, history, output)


def apply_cli_overrides(
    config: FacebookJobConfig,
    *,
    headless: bool | None = None,
    storage_state: Path | None = None,
    ignore_history: bool = False,
) -> FacebookJobConfig:
    facebook = config.facebook
    if headless is not None:
        facebook = FacebookRuntimeConfig(
            storage_state=facebook.storage_state,
            headless=headless,
            delay_seconds=facebook.delay_seconds,
            save_debug_bundle=facebook.save_debug_bundle,
        )
    if storage_state is not None:
        facebook = FacebookRuntimeConfig(
            storage_state=storage_state,
            headless=facebook.headless,
            delay_seconds=facebook.delay_seconds,
            save_debug_bundle=facebook.save_debug_bundle,
        )
    history = config.history
    if ignore_history:
        history = HistoryConfig(
            enabled=False,
            index_path=history.index_path,
            skip_statuses=history.skip_statuses,
            retry_failed=history.retry_failed,
        )
    return FacebookJobConfig(
        config.job_name,
        config.platform,
        config.keywords,
        config.search,
        facebook,
        config.comments,
        config.retry,
        history,
        config.output,
    )


def config_to_jsonable(config: FacebookJobConfig) -> dict[str, Any]:
    return {
        "job_name": config.job_name,
        "platform": config.platform,
        "keywords": list(config.keywords),
        "search": {
            "max_results_per_keyword": config.search.max_results_per_keyword,
            "page_size": config.search.page_size,
        },
        "facebook": {
            "storage_state": str(config.facebook.storage_state) if config.facebook.storage_state else None,
            "headless": config.facebook.headless,
            "delay_seconds": config.facebook.delay_seconds,
            "save_debug_bundle": config.facebook.save_debug_bundle,
        },
        "comments": {
            "enabled": config.comments.enabled,
            "max_comments_per_post": config.comments.max_comments_per_post,
            "expand_comments": config.comments.expand_comments,
            "max_expand_actions": config.comments.max_expand_actions,
            "no_progress_round_limit": config.comments.no_progress_round_limit,
        },
        "retry": {
            "max_attempts": config.retry.max_attempts,
            "retry_delay_seconds": config.retry.retry_delay_seconds,
        },
        "history": {
            "enabled": config.history.enabled,
            "index_path": str(config.history.index_path),
            "skip_statuses": list(config.history.skip_statuses),
            "retry_failed": config.history.retry_failed,
        },
        "output": {
            "root_dir": str(config.output.root_dir),
            "export_csv": config.output.export_csv,
            "generate_report": config.output.generate_report,
        },
    }


def _required_string(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"facebook job config {name} must be a non-empty string.")
    return value.strip()


def _keywords(value: Any) -> tuple[str, ...]:
    keywords = _string_list(value, "keywords")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in keywords:
        stripped = item.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        normalized.append(stripped)
    if not normalized:
        raise ValueError("facebook job config keywords must contain at least one non-empty string.")
    return tuple(normalized)


def _object(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"facebook job config {name} must be an object.")
    return value


def _string_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"facebook job config {name} must be a string array.")
    return value


def _positive_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or value < 1:
        raise ValueError(f"facebook job config {name} must be a positive integer.")
    return value


def _non_negative_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"facebook job config {name} must be a non-negative integer.")
    return value


def _non_negative_float(value: Any, name: str) -> float:
    if not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"facebook job config {name} must be 0 or greater.")
    return float(value)


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"facebook job config {name} must be a boolean.")
    return value


def _path(value: Any, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"facebook job config {name} must be a path string.")
    return Path(value.strip())


def _optional_path(value: Any, name: str) -> Path | None:
    if value is None:
        return None
    return _path(value, name)
