from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from src.batch.x_batch import run_x_batch
from src.filters.post_url import classify_post_url
from src.search.google_pse import GooglePSESearchResponse, search_google_pse_with_metadata


SearchFn = Callable[..., GooglePSESearchResponse]
BatchFn = Callable[..., dict[str, Any]]


def normalize_x_search_keywords(keywords: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        if not isinstance(keyword, str):
            continue
        value = keyword.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    if not normalized:
        raise ValueError("At least one non-empty X search keyword is required.")
    return normalized


def load_x_search_keywords_file(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Could not read X keywords file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"X keywords file is not valid JSON: {path}") from exc
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise ValueError("X keywords file must contain a JSON array of strings.")
    return normalize_x_search_keywords(payload)


def merge_x_search_keywords(
    cli_keywords: list[str] | None,
    keywords_file: Path | None,
) -> list[str]:
    keywords = list(cli_keywords or [])
    if keywords_file is not None:
        keywords.extend(load_x_search_keywords_file(keywords_file))
    return normalize_x_search_keywords(keywords)


def filter_x_search_results(
    results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    posts: list[dict[str, Any]] = []
    seen_post_ids: set[str] = set()
    supported_count = 0

    for result in results:
        url = result.get("url") if isinstance(result, dict) else None
        if not isinstance(url, str):
            continue
        classified = classify_post_url(url)
        if classified is None or classified.get("platform") != "x":
            continue
        supported_count += 1
        post_id = classified["post_id"]
        if post_id in seen_post_ids:
            continue
        seen_post_ids.add(post_id)
        posts.append(
            {
                "url": classified["canonical_url"],
                "post_id": post_id,
                "canonical_url": classified["canonical_url"],
                "original_url": url,
                "title": result.get("title"),
                "snippet": result.get("snippet"),
            }
        )
    return posts, supported_count


def run_x_search_pipeline(
    keywords: list[str],
    max_results: int,
    storage_state_path: str | Path | None,
    output_path: Path,
    batch_limit: int | None = None,
    delay_seconds: float = 2.0,
    headless: bool = False,
    resume: bool = False,
    timeout_ms: int = 30_000,
    search_fn: SearchFn = search_google_pse_with_metadata,
    batch_fn: BatchFn = run_x_batch,
    record_transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_keywords = normalize_x_search_keywords(keywords)
    if max_results < 1:
        raise ValueError("X search max results must be greater than 0.")
    if timeout_ms < 1:
        raise ValueError("X search timeout_ms must be greater than 0.")
    if batch_limit is not None and batch_limit < 1:
        raise ValueError("X batch limit must be greater than 0.")
    if delay_seconds < 0:
        raise ValueError("X batch delay must be 0 or greater.")

    all_results: list[dict[str, Any]] = []
    keyword_success_count = 0
    keyword_failed_count = 0
    search_errors: list[dict[str, str]] = []
    for keyword in normalized_keywords:
        try:
            response = search_fn(
                keyword=keyword,
                headless=headless,
                max_results=max_results,
                timeout_ms=timeout_ms,
            )
        except Exception as exc:
            keyword_failed_count += 1
            search_errors.append({"keyword": keyword, "error": f"{type(exc).__name__}: {exc}"})
            continue
        keyword_success_count += 1
        all_results.extend(response.results)

    if keyword_success_count == 0:
        raise RuntimeError("All X search keywords failed.")

    posts, supported_count = filter_x_search_results(all_results)
    batch_summary: dict[str, Any] | None = None
    if posts:
        batch_input_path = output_path.with_name(f".{output_path.name}.search-input.tmp.json")
        batch_input_path.parent.mkdir(parents=True, exist_ok=True)
        batch_input_path.write_text(json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            batch_kwargs: dict[str, Any] = {
                "input_path": batch_input_path,
                "storage_state_path": storage_state_path,
                "output_path": output_path,
                "limit": batch_limit,
                "delay_seconds": delay_seconds,
                "headless": headless,
                "resume": resume,
            }
            if record_transform is not None:
                batch_kwargs["record_transform"] = record_transform
            batch_summary = batch_fn(**batch_kwargs)
        finally:
            batch_input_path.unlink(missing_ok=True)

    return {
        "keyword_count": len(normalized_keywords),
        "keyword_success_count": keyword_success_count,
        "keyword_failed_count": keyword_failed_count,
        "search_result_count": len(all_results),
        "supported_x_post_url_count": supported_count,
        "unique_url_count": len(posts),
        "extracted_success_count": int((batch_summary or {}).get("success_count", 0)),
        "failed_count": int((batch_summary or {}).get("failed_count", 0)),
        "skipped_count": int((batch_summary or {}).get("skipped_count", 0)),
        "output_file": str(output_path),
        "search_errors": search_errors,
        "batch_started": bool(posts),
    }
