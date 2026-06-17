from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.batch.facebook_batch import run_facebook_batch
from src.config.facebook_job import (
    FacebookJobConfig,
    apply_cli_overrides,
    config_to_jsonable,
    load_facebook_job_config,
)
from src.export.facebook_export import export_facebook_results
from src.filters.post_url import classify_post_url, filter_post_results
from src.history.facebook_history import FacebookHistoryIndex
from src.report.facebook_report import SUPPORT_MATRIX, summarize_records, write_facebook_report, write_json_summary
from src.runtime.facebook_run import FacebookRunContext, read_json, read_jsonl, utc_now, write_json_atomic
from src.search.google_pse import GooglePSESearchResponse, search_google_pse_with_metadata


def run_facebook_job_config(
    config_path: Path,
    *,
    headless_override: bool | None = None,
    storage_state_override: Path | None = None,
    ignore_history: bool = False,
) -> dict[str, Any]:
    config = apply_cli_overrides(
        load_facebook_job_config(config_path),
        headless=headless_override,
        storage_state=storage_state_override,
        ignore_history=ignore_history,
    )
    context = FacebookRunContext.create(config.output.root_dir, config.job_name)
    write_json_atomic(context.paths.config_snapshot, config_to_jsonable(config))
    context.logger.log("info", "config_loaded", "initialized", "Facebook job config loaded.")
    try:
        return _run_job(context, config)
    except Exception as exc:
        context.write_state({"current_stage": "failed", "last_error": str(exc)})
        context.logger.log("error", "run_failed", "failed", str(exc))
        raise


def resume_facebook_run(run_dir: Path) -> dict[str, Any]:
    context = FacebookRunContext.load(run_dir)
    config = _load_snapshot_config(context)
    state = read_json(context.paths.state)
    if state.get("current_stage") == "completed":
        return export_facebook_run(run_dir)
    return _run_job(context, config, resume=True)


def retry_failed_facebook_run(run_dir: Path) -> dict[str, Any]:
    context = FacebookRunContext.load(run_dir)
    config = _load_snapshot_config(context)
    failed_records = [
        record for record in read_jsonl(context.paths.failed_items)
        if record.get("retryable") is True and record.get("canonical_url")
    ]
    retry_input = context.paths.run_dir / "retry_input.json"
    write_json_atomic(retry_input, {"posts": [{"canonical_url": record["canonical_url"]} for record in failed_records]})
    if not failed_records:
        return export_facebook_run(run_dir)
    context.logger.log("info", "batch_started", "collecting", "Retrying failed Facebook items.")
    run_facebook_batch(
        input_path=retry_input,
        storage_state_path=config.facebook.storage_state,
        output_path=context.paths.internal_results,
        summary_path=context.paths.run_dir / "retry_batch_summary.json",
        diagnostics_dir=context.paths.debug_dir,
        delay_seconds=config.facebook.delay_seconds,
        headless=config.facebook.headless,
        resume=True,
        save_debug_bundle=config.facebook.save_debug_bundle,
        extract_comments=config.comments.enabled,
        max_comments_per_post=config.comments.max_comments_per_post,
        expand_comments=config.comments.expand_comments,
        max_comment_expand_actions=config.comments.max_expand_actions,
        no_progress_round_limit=config.comments.no_progress_round_limit,
        max_attempts=config.retry.max_attempts,
        progress_callback=lambda event, data: context.logger.log(
            "info" if event != "item_failed" else "error",
            event,
            "collecting",
            event.replace("_", " "),
            **data,
        ),
    )
    return _export_and_finish(context, config)


def export_facebook_run(run_dir: Path) -> dict[str, Any]:
    context = FacebookRunContext.load(run_dir)
    config = _load_snapshot_config(context)
    return _export_and_finish(context, config)


def _run_job(context: FacebookRunContext, config: FacebookJobConfig, resume: bool = False) -> dict[str, Any]:
    if not resume or not context.paths.manifest.exists():
        search_payload = _run_search(context, config)
        manifest_items, filter_payload = _filter_search_results(search_payload)
        write_json_atomic(context.paths.filter_results, filter_payload)
        write_json_atomic(
            context.paths.manifest,
            {
                "schema_version": 1,
                "created_at": utc_now(),
                "keywords": list(config.keywords),
                "search_max_results_per_keyword": config.search.max_results_per_keyword,
                "items": manifest_items,
            },
        )
        context.write_state(
            {
                "current_stage": "manifest_created",
                "manifest_path": str(context.paths.manifest),
                "keywords_completed": filter_payload["keywords_completed"],
                "keywords_failed": filter_payload["keywords_failed"],
            }
        )
        context.logger.log("info", "manifest_written", "manifest_created", "Facebook search manifest written.")
    else:
        context.logger.log("info", "manifest_written", "manifest_created", "Reusing existing manifest for resume.")

    manifest = read_json(context.paths.manifest)
    items = list(manifest.get("items") or [])
    collection_input, history_skipped = _apply_history(context, config, items)
    batch_input_path = context.paths.run_dir / "batch_input.json"
    write_json_atomic(batch_input_path, {"posts": collection_input})

    context.write_state({"current_stage": "collecting"})
    context.logger.log("info", "batch_started", "collecting", "Facebook batch collection started.")
    if collection_input:
        run_facebook_batch(
            input_path=batch_input_path,
            storage_state_path=config.facebook.storage_state,
            output_path=context.paths.internal_results,
            summary_path=context.paths.run_dir / "batch_summary.json",
            diagnostics_dir=context.paths.debug_dir,
            delay_seconds=config.facebook.delay_seconds,
            headless=config.facebook.headless,
            resume=True,
            save_debug_bundle=config.facebook.save_debug_bundle,
            extract_comments=config.comments.enabled,
            max_comments_per_post=config.comments.max_comments_per_post,
            expand_comments=config.comments.expand_comments,
            max_comment_expand_actions=config.comments.max_expand_actions,
            no_progress_round_limit=config.comments.no_progress_round_limit,
            max_attempts=config.retry.max_attempts,
            progress_callback=lambda event, data: context.logger.log(
                "info" if event != "item_failed" else "error",
                event,
                "collecting",
                event.replace("_", " "),
                **data,
            ),
        )
    context.write_state({"current_stage": "collection_completed"})
    context.logger.log("info", "batch_completed", "collection_completed", "Facebook batch collection completed.")
    return _export_and_finish(context, config, history_skipped_count=history_skipped)


def _run_search(context: FacebookRunContext, config: FacebookJobConfig) -> dict[str, Any]:
    context.write_state({"current_stage": "searching"})
    context.logger.log("info", "search_page_started", "searching", "Google PSE search started.")
    per_keyword: list[dict[str, Any]] = []
    keywords_completed: list[str] = []
    keywords_failed: list[str] = []
    for keyword in config.keywords:
        try:
            response = search_google_pse_with_metadata(
                keyword=keyword,
                headless=config.facebook.headless,
                max_results=config.search.max_results_per_keyword,
            )
            keywords_completed.append(keyword)
            per_keyword.append(_search_payload(keyword, response))
            context.logger.log("info", "search_page_completed", "searching", f"Search completed for {keyword}.")
        except Exception as exc:
            keywords_failed.append(keyword)
            per_keyword.append({"keyword": keyword, "status": "failed", "error": f"{type(exc).__name__}: {exc}", "results": []})
            context.logger.log("error", "search_page_failed", "searching", f"Search failed for {keyword}: {exc}")
    payload = {
        "keywords": list(config.keywords),
        "keywords_completed": keywords_completed,
        "keywords_failed": keywords_failed,
        "per_keyword": per_keyword,
    }
    write_json_atomic(context.paths.search_results, payload)
    context.write_state(
        {
            "current_stage": "search_completed",
            "keywords_completed": keywords_completed,
            "keywords_failed": keywords_failed,
        }
    )
    if not keywords_completed:
        raise RuntimeError("All Facebook job search keywords failed.")
    return payload


def _filter_search_results(search_payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    rejected_by_reason: Counter[str] = Counter()
    duplicate_count = 0
    search_result_count = 0
    for keyword_payload in search_payload["per_keyword"]:
        keyword = keyword_payload["keyword"]
        ranked_results = keyword_payload.get("results") or []
        search_result_count += len(ranked_results)
        posts = [post for post in filter_post_results(ranked_results) if post.get("platform") == "facebook"]
        for result in ranked_results:
            url = result.get("url")
            classified = classify_post_url(url) if isinstance(url, str) else None
            if classified is None:
                rejected_by_reason["unsupported_url"] += 1
            elif classified.get("platform") != "facebook":
                rejected_by_reason["non_facebook_post"] += 1
        for post in posts:
            identity = (post["platform"], post["post_id"])
            context = {
                "keyword": keyword,
                "rank": post.get("rank"),
                "title": post.get("title"),
                "snippet": post.get("snippet"),
                "result_url": post.get("original_url"),
            }
            if identity in merged:
                duplicate_count += 1
                merged[identity]["search_contexts"].append(context)
                continue
            item = {
                "url": post["canonical_url"],
                "platform": "facebook",
                "post_id": post["post_id"],
                "canonical_url": post["canonical_url"],
                "context_type": post.get("context_type"),
                "search_keyword": keyword,
                "search_contexts": [context],
            }
            merged[identity] = item
    return list(merged.values()), {
        "search_result_count": search_result_count,
        "accepted_count": len(merged),
        "duplicate_count": duplicate_count,
        "rejected_by_reason": dict(rejected_by_reason),
        "keywords_completed": search_payload.get("keywords_completed", []),
        "keywords_failed": search_payload.get("keywords_failed", []),
    }


def _apply_history(
    context: FacebookRunContext,
    config: FacebookJobConfig,
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    if not config.history.enabled:
        return items, 0
    history = FacebookHistoryIndex(config.history.index_path)
    kept: list[dict[str, Any]] = []
    skipped = 0
    for item in items:
        decision = history.decision(
            "facebook",
            str(item.get("post_id") or ""),
            config.history.skip_statuses,
            config.history.retry_failed,
        )
        history.mark_seen(item, context.run_id)
        if decision.should_collect:
            kept.append(item)
        else:
            skipped += 1
    history.write()
    context.logger.log("info", "history_checked", "filtered", f"History skipped {skipped} item(s).")
    return kept, skipped


def _export_and_finish(
    context: FacebookRunContext,
    config: FacebookJobConfig,
    history_skipped_count: int = 0,
) -> dict[str, Any]:
    export_summary = export_facebook_results(context.paths.internal_results, context.paths.final_results, context.paths.csv)
    records = read_jsonl(context.paths.internal_results)
    failed_records = [record for record in records if record.get("collection_status") == "failed"]
    _write_jsonl(context.paths.failed_items, failed_records)
    record_summary = summarize_records(records)
    if config.history.enabled:
        history = FacebookHistoryIndex(config.history.index_path)
        for record in records:
            history.mark_seen(record, context.run_id)
        history.write()
        context.logger.log("info", "history_updated", "export_completed", "Facebook history updated.")
    manifest_items = read_json(context.paths.manifest).get("items", []) if context.paths.manifest.exists() else []
    filter_payload = read_json(context.paths.filter_results) if context.paths.filter_results.exists() else {}
    summary = {
        "job_name": context.job_name,
        "run_id": context.run_id,
        "started_at": read_json(context.paths.state).get("created_at"),
        "finished_at": utc_now(),
        "keywords": list(config.keywords),
        "run_dir": str(context.paths.run_dir),
        "manifest_path": str(context.paths.manifest),
        "internal_results_path": str(context.paths.internal_results),
        "final_results_path": str(context.paths.final_results),
        "csv_path": str(context.paths.csv),
        "logs_path": str(context.paths.logs),
        "search_result_count": filter_payload.get("search_result_count", 0),
        "manifest_item_count": len(manifest_items),
        "duplicate_count": filter_payload.get("duplicate_count", 0),
        "history_skipped_count": history_skipped_count,
        "validation_error_count": export_summary["validation_error_count"],
        "support_matrix": [list(row) for row in SUPPORT_MATRIX],
    }
    summary.update(record_summary)
    write_json_summary(context.paths.summary, summary)
    if config.output.generate_report:
        write_facebook_report(context.paths.report, summary)
        context.logger.log("info", "report_written", "export_completed", "Facebook report written.")
    context.write_state(
        {
            "current_stage": "completed",
            "processed_count": len(records),
            "success_count": summary["success_count"],
            "partial_count": summary["partial_count"],
            "failed_count": summary["failed_count"],
            "skipped_count": summary["skipped_count"],
            "last_error": None,
        }
    )
    context.logger.log("info", "export_completed", "export_completed", "Facebook export completed.")
    context.logger.log("info", "run_completed", "completed", "Facebook run completed.")
    return summary


def _search_payload(keyword: str, response: GooglePSESearchResponse) -> dict[str, Any]:
    return {
        "keyword": keyword,
        "status": "success",
        "requested_count": response.requested_count,
        "partial_error": response.partial_error,
        "page_logs": [asdict(log) for log in response.page_logs],
        "results": [
            {
                "keyword": keyword,
                "rank": rank,
                "page": _page_for_rank(rank),
                "title": result.get("title"),
                "snippet": result.get("snippet"),
                "url": result.get("url"),
            }
            for rank, result in enumerate(response.results, start=1)
        ],
    }


def _page_for_rank(rank: int) -> int:
    return ((rank - 1) // 10) + 1


def _load_snapshot_config(context: FacebookRunContext) -> FacebookJobConfig:
    from src.config.facebook_job import parse_facebook_job_config

    return parse_facebook_job_config(read_json(context.paths.config_snapshot))


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    temp_path = path.with_name(f".{path.name}.tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
    temp_path.replace(path)
