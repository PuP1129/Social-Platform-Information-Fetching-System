from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.batch.facebook_batch import DEFAULT_BATCH_DIAGNOSTICS_DIR, run_facebook_batch
from src.filters.post_url import classify_post_url, filter_post_results
from src.search.google_pse import GooglePSESearchResponse, search_google_pse_with_metadata

DEFAULT_FACEBOOK_SEARCH_DIR = Path("output") / "facebook_search"
MANIFEST_SCHEMA_VERSION = 1

SearchFn = Callable[..., GooglePSESearchResponse]
BatchFn = Callable[..., dict[str, Any]]


def run_facebook_search_pipeline(
    keyword: str,
    max_results: int,
    storage_state_path: str | Path | None,
    output_path: Path,
    delay_seconds: float = 2.0,
    headless: bool = False,
    resume: bool = False,
    save_debug_bundle: bool = False,
    artifact_dir: Path = DEFAULT_FACEBOOK_SEARCH_DIR,
    search_fn: SearchFn = search_google_pse_with_metadata,
    batch_fn: BatchFn = run_facebook_batch,
) -> dict[str, Any]:
    """Backward-compatible single-keyword wrapper."""
    return run_facebook_multi_search_pipeline(
        keywords=[keyword],
        max_results=max_results,
        storage_state_path=storage_state_path,
        output_path=output_path,
        delay_seconds=delay_seconds,
        headless=headless,
        resume=resume,
        save_debug_bundle=save_debug_bundle,
        artifact_dir=artifact_dir,
        search_fn=search_fn,
        batch_fn=batch_fn,
    )


def run_facebook_multi_search_pipeline(
    keywords: list[str],
    max_results: int,
    storage_state_path: str | Path | None,
    output_path: Path,
    delay_seconds: float = 2.0,
    headless: bool = False,
    resume: bool = False,
    save_debug_bundle: bool = False,
    artifact_dir: Path = DEFAULT_FACEBOOK_SEARCH_DIR,
    manifest_path: Path | None = None,
    search_fn: SearchFn = search_google_pse_with_metadata,
    batch_fn: BatchFn = run_facebook_batch,
) -> dict[str, Any]:
    """Search multiple keywords, globally deduplicate Facebook posts, then run batch."""
    normalized_keywords = normalize_keywords(keywords)
    _validate_pipeline_args(max_results, delay_seconds)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    per_keyword: list[dict[str, Any]] = []
    search_payloads: list[dict[str, Any]] = []
    merged_posts: dict[tuple[str, str], dict[str, Any]] = {}
    total_before_global_dedup = 0
    keyword_success_count = 0

    for keyword in normalized_keywords:
        try:
            search_response = search_fn(keyword=keyword, headless=headless, max_results=max_results)
        except Exception as exc:
            per_keyword.append(
                {
                    "keyword": keyword,
                    "search_status": "failed",
                    "search_result_count": 0,
                    "accepted_before_global_dedup": 0,
                    "rejected_count": 0,
                    "rejected_by_reason": {},
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        keyword_success_count += 1
        ranked_results = _rank_search_results(search_response.results)
        search_payload = _search_payload(keyword, search_response, ranked_results)
        search_payloads.append(search_payload)
        filtered_posts = [post for post in filter_post_results(ranked_results) if post.get("platform") == "facebook"]
        filter_summary = _build_filter_summary(ranked_results, filtered_posts)
        total_before_global_dedup += len(filtered_posts)

        per_keyword.append(
            {
                "keyword": keyword,
                "search_status": "success",
                "search_result_count": len(ranked_results),
                "accepted_before_global_dedup": len(filtered_posts),
                "rejected_count": filter_summary["rejected_count"],
                "rejected_by_reason": filter_summary["rejected_by_reason"],
                "partial_error": search_response.partial_error,
            }
        )
        _merge_filtered_posts(merged_posts, keyword, filtered_posts)

    search_results_path = artifact_dir / "search_results.json"
    _write_json_atomic(
        search_results_path,
        {
            "keywords": normalized_keywords,
            "keyword_count": len(normalized_keywords),
            "search_max_results_per_keyword": max_results,
            "per_keyword": search_payloads,
        },
    )

    if keyword_success_count == 0:
        summary = _summary_without_batch(
            keywords=normalized_keywords,
            per_keyword=per_keyword,
            output_path=output_path,
            artifact_dir=artifact_dir,
            search_results_path=search_results_path,
            manifest_path=manifest_path or _default_manifest_path(output_path),
            status="search_failed",
            resume=resume,
            total_before_global_dedup=0,
        )
        summary_path = artifact_dir / "summary.json"
        _write_json_atomic(summary_path, summary)
        summary["summary_path"] = str(summary_path)
        raise RuntimeError(f"All Facebook search keywords failed. Summary written to {summary_path}")

    items = list(merged_posts.values())
    manifest = _build_manifest(normalized_keywords, max_results, items)
    final_manifest_path = manifest_path or _default_manifest_path(output_path)
    _write_json_atomic(final_manifest_path, manifest)

    accepted_urls_path = artifact_dir / "accepted_urls.json"
    batch_input_path = artifact_dir / "batch_input.json"
    _write_json_atomic(
        accepted_urls_path,
        {
            "keywords": normalized_keywords,
            "accepted_count": len(items),
            "posts": items,
        },
    )
    _write_json_atomic(batch_input_path, {"posts": items})

    batch_summary = _run_batch_if_needed(
        items=items,
        batch_input_path=batch_input_path,
        storage_state_path=storage_state_path,
        output_path=output_path,
        artifact_dir=artifact_dir,
        delay_seconds=delay_seconds,
        headless=headless,
        resume=resume,
        save_debug_bundle=save_debug_bundle,
        batch_fn=batch_fn,
    )

    summary = _build_pipeline_summary(
        keywords=normalized_keywords,
        per_keyword=per_keyword,
        batch_summary=batch_summary,
        output_path=output_path,
        artifact_dir=artifact_dir,
        search_results_path=search_results_path,
        accepted_urls_path=accepted_urls_path,
        batch_input_path=batch_input_path,
        manifest_path=final_manifest_path,
        batch_started=bool(items),
        resume=resume,
        total_before_global_dedup=total_before_global_dedup,
    )
    summary_path = artifact_dir / "summary.json"
    _write_json_atomic(summary_path, summary)
    summary["summary_path"] = str(summary_path)
    return summary


def run_facebook_search_manifest_pipeline(
    manifest_path: Path,
    storage_state_path: str | Path | None,
    output_path: Path,
    delay_seconds: float = 2.0,
    headless: bool = False,
    resume: bool = False,
    save_debug_bundle: bool = False,
    artifact_dir: Path = DEFAULT_FACEBOOK_SEARCH_DIR,
    batch_fn: BatchFn = run_facebook_batch,
) -> dict[str, Any]:
    """Run Facebook batch directly from a saved search manifest without Google PSE."""
    _validate_pipeline_args(1, delay_seconds)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_facebook_search_manifest(manifest_path)
    items = list(manifest["items"])
    batch_input_path = artifact_dir / "batch_input.json"
    _write_json_atomic(batch_input_path, {"posts": items})

    batch_summary = _run_batch_if_needed(
        items=items,
        batch_input_path=batch_input_path,
        storage_state_path=storage_state_path,
        output_path=output_path,
        artifact_dir=artifact_dir,
        delay_seconds=delay_seconds,
        headless=headless,
        resume=resume,
        save_debug_bundle=save_debug_bundle,
        batch_fn=batch_fn,
    )
    per_keyword = [
        {
            "keyword": keyword,
            "search_status": "manifest",
            "search_result_count": None,
            "accepted_before_global_dedup": None,
            "rejected_count": None,
            "rejected_by_reason": {},
        }
        for keyword in manifest["keywords"]
    ]
    summary = _build_pipeline_summary(
        keywords=manifest["keywords"],
        per_keyword=per_keyword,
        batch_summary=batch_summary,
        output_path=output_path,
        artifact_dir=artifact_dir,
        search_results_path=None,
        accepted_urls_path=None,
        batch_input_path=batch_input_path,
        manifest_path=manifest_path,
        batch_started=bool(items),
        resume=resume,
        total_before_global_dedup=len(items),
        pipeline_status_override="success" if items else "no_accepted_urls",
    )
    summary["manifest_mode"] = True
    summary_path = artifact_dir / "summary.json"
    _write_json_atomic(summary_path, summary)
    summary["summary_path"] = str(summary_path)
    return summary


def normalize_keywords(keywords: list[str]) -> list[str]:
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
        raise ValueError("At least one non-empty Facebook search keyword is required.")
    return normalized


def load_keywords_file(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Could not read Facebook keywords file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Facebook keywords file is not valid JSON: {path}") from exc
    if not isinstance(payload, list):
        raise ValueError("Facebook keywords file must contain a JSON array of strings.")
    if not all(isinstance(item, str) for item in payload):
        raise ValueError("Facebook keywords file must contain only strings.")
    return normalize_keywords(payload)


def load_facebook_search_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Could not read Facebook search manifest: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Facebook search manifest is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Facebook search manifest must be a JSON object.")
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("Unsupported Facebook search manifest schema_version.")
    keywords = payload.get("keywords")
    items = payload.get("items")
    if not isinstance(keywords, list) or not all(isinstance(item, str) for item in keywords):
        raise ValueError("Facebook search manifest keywords must be a string array.")
    if not isinstance(items, list):
        raise ValueError("Facebook search manifest items must be an array.")
    for index, item in enumerate(items, start=1):
        _validate_manifest_item(item, index)
    return {"keywords": keywords, "items": items, "search_max_results_per_keyword": payload.get("search_max_results_per_keyword")}


def _validate_manifest_item(item: Any, index: int) -> None:
    if not isinstance(item, dict):
        raise ValueError(f"Facebook search manifest item #{index} must be an object.")
    required = ("url", "platform", "post_id", "canonical_url", "search_contexts")
    missing = [field for field in required if field not in item]
    if missing:
        raise ValueError(f"Facebook search manifest item #{index} missing field(s): {', '.join(missing)}.")
    if item["platform"] != "facebook":
        raise ValueError(f"Facebook search manifest item #{index} is not a Facebook item.")
    if not isinstance(item["search_contexts"], list):
        raise ValueError(f"Facebook search manifest item #{index} search_contexts must be an array.")
    classified = classify_post_url(str(item["canonical_url"]))
    if classified is None or classified["platform"] != "facebook":
        raise ValueError(f"Facebook search manifest item #{index} canonical_url is unsupported.")


def _validate_pipeline_args(max_results: int, delay_seconds: float) -> None:
    if max_results < 1:
        raise ValueError("facebook search max results must be greater than 0.")
    if delay_seconds < 0:
        raise ValueError("facebook batch delay must be 0 or greater.")


def _rank_search_results(results: list[dict[str, str | None]]) -> list[dict[str, Any]]:
    return [
        {
            "rank": rank,
            "title": result.get("title"),
            "url": result.get("url"),
            "snippet": result.get("snippet"),
        }
        for rank, result in enumerate(results, start=1)
    ]


def _search_payload(
    keyword: str,
    search_response: GooglePSESearchResponse,
    ranked_results: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "keyword": keyword,
        "requested_count": search_response.requested_count,
        "result_count": len(ranked_results),
        "page_logs": [asdict(page_log) for page_log in search_response.page_logs],
        "partial_error": search_response.partial_error,
        "results": ranked_results,
    }


def _build_filter_summary(
    ranked_results: list[dict[str, Any]],
    filtered_posts: list[dict[str, Any]],
) -> dict[str, Any]:
    accepted_identities = {
        (str(post.get("platform")), str(post.get("post_id")))
        for post in filtered_posts
        if post.get("platform") and post.get("post_id")
    }
    seen_supported: set[tuple[str, str]] = set()
    rejected_by_reason: Counter[str] = Counter()
    supported_facebook_count = 0

    for result in ranked_results:
        url = result.get("url")
        if not isinstance(url, str) or not url.strip():
            rejected_by_reason["missing_url"] += 1
            continue
        classified = classify_post_url(url)
        if classified is None:
            rejected_by_reason["unsupported_url"] += 1
            continue
        if classified["platform"] != "facebook":
            rejected_by_reason["non_facebook_post"] += 1
            continue
        supported_facebook_count += 1
        identity = (classified["platform"], classified["post_id"])
        if identity in seen_supported:
            rejected_by_reason["duplicate_facebook_post"] += 1
        seen_supported.add(identity)

    accepted_count = len(accepted_identities)
    return {
        "search_result_count": len(ranked_results),
        "supported_facebook_count": supported_facebook_count,
        "accepted_count": accepted_count,
        "rejected_count": max(0, len(ranked_results) - accepted_count),
        "rejected_by_reason": dict(rejected_by_reason),
        "deduplicated_count": rejected_by_reason.get("duplicate_facebook_post", 0),
        "batch_input_count": accepted_count,
    }


def _merge_filtered_posts(
    merged_posts: dict[tuple[str, str], dict[str, Any]],
    keyword: str,
    filtered_posts: list[dict[str, Any]],
) -> None:
    for post in filtered_posts:
        platform = post.get("platform")
        post_id = post.get("post_id")
        canonical_url = post.get("canonical_url")
        if not isinstance(platform, str) or not isinstance(post_id, str) or not isinstance(canonical_url, str):
            continue
        identity = (platform, post_id)
        context = {
            "keyword": keyword,
            "rank": post.get("rank"),
            "title": post.get("title"),
            "snippet": post.get("snippet"),
            "result_url": post.get("original_url"),
        }
        if identity not in merged_posts:
            merged_posts[identity] = {
                "url": canonical_url,
                "platform": platform,
                "post_id": post_id,
                "canonical_url": canonical_url,
                "context_type": post.get("context_type"),
                "search_keyword": keyword,
                "search_rank": post.get("rank"),
                "search_title": post.get("title"),
                "search_snippet": post.get("snippet"),
                "search_result_url": post.get("original_url"),
                "search_contexts": [context],
            }
            continue
        contexts = merged_posts[identity]["search_contexts"]
        context_key = (context["keyword"], context["rank"], context["result_url"])
        if all((existing.get("keyword"), existing.get("rank"), existing.get("result_url")) != context_key for existing in contexts):
            contexts.append(context)


def _build_manifest(keywords: list[str], max_results: int, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "created_at": _utc_now(),
        "keywords": keywords,
        "search_max_results_per_keyword": max_results,
        "items": items,
    }


def _run_batch_if_needed(
    items: list[dict[str, Any]],
    batch_input_path: Path,
    storage_state_path: str | Path | None,
    output_path: Path,
    artifact_dir: Path,
    delay_seconds: float,
    headless: bool,
    resume: bool,
    save_debug_bundle: bool,
    batch_fn: BatchFn,
) -> dict[str, Any] | None:
    if not items:
        return None
    return batch_fn(
        input_path=batch_input_path,
        storage_state_path=storage_state_path,
        output_path=output_path,
        summary_path=artifact_dir / "batch_summary.json",
        diagnostics_dir=DEFAULT_BATCH_DIAGNOSTICS_DIR,
        delay_seconds=delay_seconds,
        headless=headless,
        resume=resume,
        save_debug_bundle=save_debug_bundle,
    )


def _build_pipeline_summary(
    keywords: list[str],
    per_keyword: list[dict[str, Any]],
    batch_summary: dict[str, Any] | None,
    output_path: Path,
    artifact_dir: Path,
    search_results_path: Path | None,
    accepted_urls_path: Path | None,
    batch_input_path: Path,
    manifest_path: Path,
    batch_started: bool,
    resume: bool,
    total_before_global_dedup: int,
    pipeline_status_override: str | None = None,
) -> dict[str, Any]:
    current_run = batch_summary.get("current_run", {}) if batch_summary else {}
    total = batch_summary.get("total", {}) if batch_summary else {}
    keyword_success_count = sum(1 for item in per_keyword if item.get("search_status") in {"success", "manifest"})
    keyword_failed_count = sum(1 for item in per_keyword if item.get("search_status") == "failed")
    batch_failed = int(current_run.get("failed_count", 0) or 0)
    batch_input_count = max(0, total_before_global_dedup - (total_before_global_dedup - len(load_facebook_search_manifest(manifest_path)["items"]) if manifest_path.exists() else 0))
    duplicate_count = _duplicate_count(per_keyword, total_before_global_dedup, batch_input_count)
    summary = {
        "keywords": keywords,
        "keyword_count": len(keywords),
        "keyword_success_count": keyword_success_count,
        "keyword_failed_count": keyword_failed_count,
        "resume": resume,
        "batch_started": batch_started,
        "search_result_count": sum(int(item.get("search_result_count") or 0) for item in per_keyword),
        "accepted_before_global_dedup": total_before_global_dedup,
        "rejected_count": sum(int(item.get("rejected_count") or 0) for item in per_keyword),
        "rejected_by_reason": _sum_rejected_reasons(per_keyword),
        "duplicate_count": duplicate_count,
        "deduplicated_count": duplicate_count,
        "batch_input_count": batch_input_count,
        "accepted_count": batch_input_count,
        "batch": {
            "success": current_run.get("success_count", 0),
            "partial": 0,
            "failed": current_run.get("failed_count", 0),
            "skipped": current_run.get("skipped_count", 0),
            "record_count": current_run.get("record_count", 0),
        },
        "batch_current_run": {
            "success_count": current_run.get("success_count", 0),
            "failed_count": current_run.get("failed_count", 0),
            "skipped_count": current_run.get("skipped_count", 0),
            "record_count": current_run.get("record_count", 0),
        },
        "batch_total": {
            "success": total.get("success_count", 0),
            "failed": total.get("failed_count", 0),
            "skipped": total.get("skipped_count", 0),
            "record_count": total.get("record_count", 0),
        },
        "per_keyword": per_keyword,
        "manifest_path": str(manifest_path),
        "batch_output_path": str(output_path),
        "output_file": str(output_path),
        "artifact_dir": str(artifact_dir),
        "search_results_path": str(search_results_path) if search_results_path else None,
        "accepted_urls_path": str(accepted_urls_path) if accepted_urls_path else None,
        "batch_input_path": str(batch_input_path),
        "batch_summary_path": str(artifact_dir / "batch_summary.json") if batch_summary else None,
    }
    if pipeline_status_override:
        summary["pipeline_status"] = pipeline_status_override
    elif keyword_failed_count and keyword_success_count:
        summary["pipeline_status"] = "partial_search_failure"
    elif batch_input_count == 0:
        summary["pipeline_status"] = "no_accepted_urls"
    elif batch_failed:
        summary["pipeline_status"] = "batch_completed_with_failures"
    else:
        summary["pipeline_status"] = "success"
    return summary


def _summary_without_batch(
    keywords: list[str],
    per_keyword: list[dict[str, Any]],
    output_path: Path,
    artifact_dir: Path,
    search_results_path: Path | None,
    manifest_path: Path,
    status: str,
    resume: bool,
    total_before_global_dedup: int,
) -> dict[str, Any]:
    empty_manifest = _build_manifest(keywords, 0, [])
    _write_json_atomic(manifest_path, empty_manifest)
    batch_input_path = artifact_dir / "batch_input.json"
    _write_json_atomic(batch_input_path, {"posts": []})
    return _build_pipeline_summary(
        keywords=keywords,
        per_keyword=per_keyword,
        batch_summary=None,
        output_path=output_path,
        artifact_dir=artifact_dir,
        search_results_path=search_results_path,
        accepted_urls_path=None,
        batch_input_path=batch_input_path,
        manifest_path=manifest_path,
        batch_started=False,
        resume=resume,
        total_before_global_dedup=total_before_global_dedup,
        pipeline_status_override=status,
    )


def _sum_rejected_reasons(per_keyword: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for item in per_keyword:
        for reason, count in (item.get("rejected_by_reason") or {}).items():
            counter[str(reason)] += int(count)
    return dict(counter)


def _duplicate_count(per_keyword: list[dict[str, Any]], accepted_before_global_dedup: int, batch_input_count: int) -> int:
    rejected_duplicates = _sum_rejected_reasons(per_keyword).get("duplicate_facebook_post", 0)
    global_duplicates = max(0, accepted_before_global_dedup - batch_input_count)
    return rejected_duplicates + global_duplicates


def _default_manifest_path(output_path: Path) -> Path:
    return output_path.with_suffix(".manifest.json")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
