from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_SOCIAL_COLLECT_CONFIG_PATH = Path("configs") / "social_collect.local.json"
DEFAULT_SOCIAL_COLLECT_CONFIG: dict[str, Any] = {
    "platforms": ["facebook", "x"],
    "storage_state": {
        "facebook": ".playwright/facebook_storage_state.json",
        "x": ".playwright/x_storage_state.json",
    },
    "search": {
        "max_results": 80,
    },
    "collection": {
        "limit": 50,
        "delay": 2.0,
        "headless": True,
        "normalize_output": True,
    },
    "output": {
        "run_dir": "output/runs/social-collection",
        "filename": "social_posts.jsonl",
        "timestamped": True,
    },
}


@dataclass(frozen=True)
class SocialCollectSettings:
    keywords: list[str]
    platforms: list[str]
    facebook_storage_state: Path | None
    x_storage_state: Path | None
    search_max_results: int
    limit: int
    delay: float
    headless: bool
    normalize_output: bool
    output_path: Path


def load_social_collect_config(
    config_path: Path | None = None,
    *,
    local_config_path: Path = DEFAULT_SOCIAL_COLLECT_CONFIG_PATH,
) -> dict[str, Any]:
    """Load built-in defaults, optional local config, and optional explicit config."""
    config = deepcopy(DEFAULT_SOCIAL_COLLECT_CONFIG)
    if local_config_path.exists():
        config = _deep_merge(config, _read_config_file(local_config_path))
    if config_path is not None:
        if not config_path.exists():
            raise ValueError(f"Social collect config file does not exist: {config_path}")
        config = _deep_merge(config, _read_config_file(config_path))
    return config


def build_social_collect_settings(
    *,
    keyword: str | list[str],
    config_path: Path | None = None,
    local_config_path: Path = DEFAULT_SOCIAL_COLLECT_CONFIG_PATH,
    platforms_override: str | list[str] | None = None,
    limit_override: int | None = None,
    search_max_results_override: int | None = None,
    delay_override: float | None = None,
    output_override: Path | None = None,
    facebook_storage_state_override: Path | None = None,
    x_storage_state_override: Path | None = None,
    headless_override: bool | None = None,
    normalize_output_override: bool | None = None,
) -> SocialCollectSettings:
    config = load_social_collect_config(config_path, local_config_path=local_config_path)
    keywords = normalize_collect_keywords(keyword)

    platforms = parse_social_platforms(
        platforms_override if platforms_override is not None else config.get("platforms")
    )
    collection = _object_at(config, "collection")
    search = _object_at(config, "search")
    storage_state = _object_at(config, "storage_state")

    limit = _positive_int(limit_override if limit_override is not None else collection.get("limit"), "collection.limit")
    search_max_results = _positive_int(
        search_max_results_override if search_max_results_override is not None else search.get("max_results"),
        "search.max_results",
    )
    delay = _non_negative_float(delay_override if delay_override is not None else collection.get("delay"), "collection.delay")
    headless = bool(collection.get("headless", True)) if headless_override is None else headless_override
    normalize_output = (
        bool(collection.get("normalize_output", True))
        if normalize_output_override is None
        else normalize_output_override
    )

    facebook_state = _path_or_none(
        facebook_storage_state_override
        if facebook_storage_state_override is not None
        else storage_state.get("facebook")
    )
    x_state = _path_or_none(
        x_storage_state_override if x_storage_state_override is not None else storage_state.get("x")
    )
    output_path = output_override if output_override is not None else _configured_output_path(config)

    settings = SocialCollectSettings(
        keywords=keywords,
        platforms=platforms,
        facebook_storage_state=facebook_state,
        x_storage_state=x_state,
        search_max_results=search_max_results,
        limit=limit,
        delay=delay,
        headless=headless,
        normalize_output=normalize_output,
        output_path=output_path,
    )
    validate_social_collect_storage(settings)
    return settings


def load_collect_keywords_file(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Collect keywords file is not valid JSON: {path}") from exc
    except OSError as exc:
        raise ValueError(f"Could not read collect keywords file: {path}") from exc
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise ValueError("Collect keywords file must contain a JSON array of strings.")
    return normalize_collect_keywords(payload)


def merge_collect_keywords(cli_keywords: list[str] | None, keywords_file: Path | None) -> list[str]:
    keywords = list(cli_keywords or [])
    if keywords_file is not None:
        keywords.extend(load_collect_keywords_file(keywords_file))
    return normalize_collect_keywords(keywords)


def normalize_collect_keywords(value: str | list[str]) -> list[str]:
    raw_keywords = [value] if isinstance(value, str) else value
    normalized: list[str] = []
    seen: set[str] = set()
    for keyword in raw_keywords:
        if not isinstance(keyword, str):
            continue
        text = keyword.strip()
        if not text or text in seen:
            continue
        normalized.append(text)
        seen.add(text)
    if not normalized:
        raise ValueError("--collect keyword cannot be empty.")
    return normalized


def parse_social_platforms(value: str | list[str] | Any) -> list[str]:
    if isinstance(value, str):
        raw_platforms = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        raw_platforms = [str(part).strip() for part in value]
    else:
        raise ValueError("platforms must be a list or comma-separated string.")

    platforms: list[str] = []
    seen: set[str] = set()
    for platform in raw_platforms:
        normalized = platform.lower()
        if not normalized:
            continue
        if normalized not in {"facebook", "x"}:
            raise ValueError(f"Unsupported social platform: {platform}")
        if normalized not in seen:
            platforms.append(normalized)
            seen.add(normalized)
    if not platforms:
        raise ValueError("At least one social platform is required.")
    return platforms


def validate_social_collect_storage(settings: SocialCollectSettings) -> None:
    for platform, path in (
        ("facebook", settings.facebook_storage_state),
        ("x", settings.x_storage_state),
    ):
        if platform not in settings.platforms:
            continue
        if path is None:
            if platform == "facebook":
                raise ValueError(
                    "Missing storage state path for facebook. Set storage_state.facebook in "
                    "configs/social_collect.local.json or pass --facebook-storage-state."
                )
            continue
        if not path.exists() or not path.is_file():
            override = "--facebook-storage-state" if platform == "facebook" else "--x-storage-state"
            raise ValueError(
                f"Configured storage state for {platform} is missing or is not a file: {path}. "
                f"Override it with {override} or edit configs/social_collect.local.json."
            )


def _read_config_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Social collect config is not valid JSON: {path}") from exc
    except OSError as exc:
        raise ValueError(f"Could not read social collect config: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Social collect config must be a JSON object: {path}")
    return payload


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _object_at(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a JSON object.")
    return value


def _positive_int(value: Any, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer.") from exc
    if number < 1:
        raise ValueError(f"{name} must be a positive integer.")
    return number


def _non_negative_float(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be 0 or greater.") from exc
    if number < 0:
        raise ValueError(f"{name} must be 0 or greater.")
    return number


def _path_or_none(value: Any) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    return Path(text) if text else None


def _configured_output_path(config: dict[str, Any]) -> Path:
    output = _object_at(config, "output")
    run_dir = Path(str(output.get("run_dir") or DEFAULT_SOCIAL_COLLECT_CONFIG["output"]["run_dir"]))
    filename = str(output.get("filename") or DEFAULT_SOCIAL_COLLECT_CONFIG["output"]["filename"])
    if bool(output.get("timestamped", True)):
        stamp = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y%m%dT%H%M%SZ")
        return run_dir / stamp / filename
    return run_dir / filename
