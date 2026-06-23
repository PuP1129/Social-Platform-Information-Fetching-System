from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.filters.post_url import filter_post_results
from src.utils.social_post_normalizer import normalize_social_post

RAW_OUTPUT_PATH = Path("output") / "search_results.json"
POST_OUTPUT_PATH = Path("output") / "post_urls.json"
X_POST_OUTPUT_PATH = Path("output") / "x_post.json"
X_BATCH_OUTPUT_PATH = Path("output") / "x_posts.jsonl"
FACEBOOK_POST_OUTPUT_PATH = Path("output") / "facebook_post.json"
FACEBOOK_BATCH_OUTPUT_PATH = Path("output") / "facebook_posts.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search Google PSE or extract social posts.")
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--collect",
        action="append",
        help="Simplified unified social collection keyword. May be repeated.",
    )
    input_group.add_argument("--keyword", help="Single keyword to search.")
    input_group.add_argument("--x-url", help="Single X post URL to extract.")
    input_group.add_argument("--x-batch-file", type=Path, help="JSON file containing X post URLs to extract.")
    input_group.add_argument("--facebook-url", help="Single public Facebook post URL to extract.")
    input_group.add_argument("--facebook-batch-file", type=Path, help="JSON file containing Facebook post URLs to extract.")
    input_group.add_argument(
        "--facebook-search-keyword",
        action="append",
        help="Search Google PSE and batch-extract Facebook posts. May be repeated.",
    )
    input_group.add_argument(
        "--facebook-search-keywords-file",
        type=Path,
        help="JSON file containing Facebook search keywords.",
    )
    input_group.add_argument(
        "--facebook-search-manifest",
        type=Path,
        help="Run Facebook batch from a saved Facebook search manifest without Google PSE.",
    )
    input_group.add_argument("--facebook-job-config", type=Path, help="Run a Facebook collection job from JSON config.")
    input_group.add_argument("--facebook-resume-run", type=Path, help="Resume a Facebook run directory.")
    input_group.add_argument("--facebook-retry-failed-run", type=Path, help="Retry retryable failed items in a Facebook run.")
    input_group.add_argument("--facebook-export-run", type=Path, help="Rebuild final JSONL, CSV, and report from a Facebook run.")
    parser.add_argument(
        "--x-search-keyword",
        action="append",
        help="Search Google PSE and batch-extract X posts. May be repeated.",
    )
    parser.add_argument(
        "--x-search-keywords-file",
        type=Path,
        help="JSON file containing X search keywords; may be combined with --x-search-keyword.",
    )
    parser.add_argument(
        "--x-search-max-results",
        type=int,
        default=10,
        help="Maximum number of Google PSE results to inspect per X search keyword.",
    )
    parser.add_argument("--headless", action="store_true", help="Run Chromium in headless mode.")
    parser.add_argument("--max-results", type=int, default=10, help="Maximum number of merged Google PSE results to save.")
    parser.add_argument("--config", type=Path, default=None, help="JSON config file for --collect mode.")
    parser.add_argument("--collect-file", type=Path, default=None, help="JSON string-array keyword file for --collect mode.")
    parser.add_argument("--limit", type=int, default=None, help="Override collection.limit for --collect mode.")
    parser.add_argument(
        "--social-platforms",
        help="Comma-separated platform list for --collect mode, for example: facebook,x",
    )
    parser.add_argument(
        "--social-search-max-results",
        type=int,
        default=None,
        help="Override search.max_results for --collect mode.",
    )
    parser.add_argument(
        "--social-delay",
        type=float,
        default=None,
        help="Override collection.delay for --collect mode.",
    )
    parser.add_argument(
        "--social-output",
        type=Path,
        default=None,
        help="Explicit JSONL output path for --collect mode.",
    )
    parser.add_argument(
        "--facebook-search-max-results",
        type=int,
        default=10,
        help="Maximum number of Google PSE results to inspect for Facebook search collection.",
    )
    parser.add_argument(
        "--facebook-batch-output",
        type=Path,
        default=FACEBOOK_BATCH_OUTPUT_PATH,
        help="JSONL output path for Facebook batch extraction.",
    )
    parser.add_argument(
        "--facebook-batch-limit",
        type=int,
        default=None,
        help="Optional maximum number of unique supported Facebook posts to process.",
    )
    parser.add_argument(
        "--facebook-batch-delay",
        type=float,
        default=2.0,
        help="Delay in seconds between Facebook batch items.",
    )
    parser.add_argument(
        "--facebook-batch-resume",
        action="store_true",
        help="Append to an existing Facebook batch JSONL and skip already processed URLs.",
    )
    parser.add_argument(
        "--x-storage-state",
        type=Path,
        default=None,
        help="Path to a local Playwright storage-state JSON file for authenticated X access.",
    )
    parser.add_argument(
        "--x-batch-output",
        type=Path,
        default=X_BATCH_OUTPUT_PATH,
        help="JSONL output path for X batch extraction.",
    )
    parser.add_argument(
        "--x-batch-limit",
        type=int,
        default=None,
        help="Optional maximum number of unique supported X posts to process.",
    )
    parser.add_argument(
        "--x-batch-delay",
        type=float,
        default=2.0,
        help="Delay in seconds between X batch items.",
    )
    parser.add_argument(
        "--x-batch-resume",
        action="store_true",
        help="Append to an existing X batch JSONL and skip already processed posts.",
    )
    parser.add_argument(
        "--facebook-storage-state",
        type=Path,
        default=None,
        help="Path to a local Playwright storage-state JSON file for authenticated Facebook access.",
    )
    parser.add_argument(
        "--facebook-save-debug-bundle",
        action="store_true",
        help="Save a full Facebook debug bundle even for successful extractions.",
    )
    parser.add_argument(
        "--facebook-ignore-history",
        action="store_true",
        help="Ignore cross-run Facebook history when using --facebook-job-config.",
    )
    parser.add_argument(
        "--normalize-output",
        action="store_true",
        help="Write the shared social_post_v1 schema for supported single-post and batch outputs.",
    )
    args = parser.parse_args()
    selected_modes = sum(
        bool(value)
        for value in (
            args.keyword,
            args.x_url,
            args.x_batch_file,
            args.facebook_url,
            args.facebook_batch_file,
            args.facebook_search_keyword,
            args.facebook_search_keywords_file,
            args.facebook_search_manifest,
            args.facebook_job_config,
            args.facebook_resume_run,
            args.facebook_retry_failed_run,
            args.facebook_export_run,
            args.x_search_keyword or args.x_search_keywords_file,
            args.collect or args.collect_file,
        )
    )
    if selected_modes != 1:
        parser.error("exactly one input mode is required")
    return args


def build_output(keyword: str, results: list[dict[str, str | None]]) -> dict[str, object]:
    ranked_results = []
    for rank, result in enumerate(results, start=1):
        ranked_results.append(
            {
                "rank": rank,
                "title": result["title"],
                "url": result["url"],
                "snippet": result["snippet"],
            }
        )

    return {
        "keyword": keyword.strip(),
        "result_count": len(ranked_results),
        "results": ranked_results,
    }


def build_post_output(keyword: str, raw_result_count: int, posts: list[dict]) -> dict[str, object]:
    return {
        "keyword": keyword.strip(),
        "raw_result_count": raw_result_count,
        "post_count": len(posts),
        "posts": posts,
    }


def write_output(payload: dict[str, object], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_social_collect(args: argparse.Namespace) -> None:
    try:
        from src.config.social_collect import build_social_collect_settings, merge_collect_keywords
        from src.pipelines.facebook_search_pipeline import run_facebook_multi_search_pipeline
        from src.pipelines.x_search_pipeline import run_x_search_pipeline
        from src.search.google_pse import GooglePSESearchError
    except ModuleNotFoundError as exc:
        _raise_playwright_install_error(exc)

    try:
        selected_keywords = merge_collect_keywords(args.collect, args.collect_file)
        settings = build_social_collect_settings(
            keyword=selected_keywords,
            config_path=args.config,
            platforms_override=args.social_platforms,
            limit_override=args.limit,
            search_max_results_override=args.social_search_max_results,
            delay_override=args.social_delay,
            output_override=args.social_output,
            facebook_storage_state_override=args.facebook_storage_state,
            x_storage_state_override=args.x_storage_state,
            headless_override=True if args.headless else None,
            normalize_output_override=True,
        )
    except ValueError as exc:
        raise SystemExit(f"Social collect config error: {exc}") from exc

    print(f"Collecting social posts for {len(settings.keywords)} keyword(s).")
    print(f"Platforms: {', '.join(settings.platforms)}")
    print(f"Output: {settings.output_path}")

    remaining = settings.limit
    output_started = False
    total_processed = 0
    platform_summaries: list[dict[str, object]] = []

    for platform in settings.platforms:
        if remaining <= 0:
            platform_summaries.append({"platform": platform, "status": "skipped_limit_reached"})
            continue

        processed = 0
        if platform == "facebook":
            try:
                artifact_dir = settings.output_path.parent / "facebook_artifacts"
                summary = run_facebook_multi_search_pipeline(
                    keywords=settings.keywords,
                    max_results=settings.search_max_results,
                    storage_state_path=settings.facebook_storage_state,
                    output_path=settings.output_path,
                    delay_seconds=settings.delay,
                    batch_limit=remaining,
                    headless=settings.headless,
                    resume=output_started,
                    artifact_dir=artifact_dir,
                    record_transform=normalize_social_post,
                )
                current = summary.get("batch_current_run", {})
                processed = int(current.get("record_count", 0) or 0)
                platform_summaries.append(
                    _social_platform_summary(
                        platform="facebook",
                        search_result_count=summary.get("search_result_count", 0),
                        accepted_count=summary.get("batch_input_count", 0),
                        success_count=current.get("success_count", 0),
                        failed_count=current.get("failed_count", 0),
                        skipped_count=current.get("skipped_count", 0),
                    )
                )
                if processed > 0:
                    output_started = True
            except (ValueError, OSError, RuntimeError, GooglePSESearchError) as exc:
                platform_summaries.append(_social_platform_failure_summary("facebook", exc))
        else:
            try:
                summary = run_x_search_pipeline(
                    keywords=settings.keywords,
                    max_results=settings.search_max_results,
                    storage_state_path=settings.x_storage_state,
                    output_path=settings.output_path,
                    batch_limit=remaining,
                    delay_seconds=settings.delay,
                    headless=settings.headless,
                    resume=output_started,
                    record_transform=normalize_social_post,
                )
                processed = (
                    int(summary.get("extracted_success_count", 0) or 0)
                    + int(summary.get("failed_count", 0) or 0)
                    + int(summary.get("skipped_count", 0) or 0)
                )
                platform_summaries.append(
                    _social_platform_summary(
                        platform="x",
                        search_result_count=summary.get("search_result_count", 0),
                        accepted_count=summary.get("unique_url_count", 0),
                        success_count=summary.get("extracted_success_count", 0),
                        failed_count=summary.get("failed_count", 0),
                        skipped_count=summary.get("skipped_count", 0),
                        warning=_x_social_warning(summary),
                    )
                )
                if processed > 0:
                    output_started = True
            except (ValueError, OSError, RuntimeError, GooglePSESearchError) as exc:
                platform_summaries.append(_social_platform_failure_summary("x", exc))

        total_processed += processed
        remaining = max(0, remaining - processed)

    print("Social collect finished.")
    for item in platform_summaries:
        if item.get("status") == "skipped_limit_reached":
            print(f"- {item['platform']}: skipped because collection limit was reached.")
            continue
        line = (
            f"- {item['platform']}: "
            f"{item.get('search_result_count', 0)} search result(s), "
            f"{item.get('accepted_count', 0)} accepted URL(s), "
            f"{item.get('success_count', 0)} success/partial, "
            f"{item.get('failed_count', 0)} failed, "
            f"{item.get('skipped_count', 0)} skipped"
        )
        if item.get("status") == "failed":
            line += f", status=failed, reason={item.get('reason')}"
        elif item.get("warning"):
            line += f", warning={item.get('warning')}"
        print(line + ".")
    print(f"JSONL written to {settings.output_path}")
    if total_processed == 0 and any(item.get("status") == "failed" for item in platform_summaries):
        raise SystemExit("Social collect failed before any records were written.")


def _social_platform_summary(
    *,
    platform: str,
    search_result_count: object,
    accepted_count: object,
    success_count: object,
    failed_count: object,
    skipped_count: object,
    warning: str | None = None,
) -> dict[str, object]:
    return {
        "platform": platform,
        "status": "completed",
        "search_result_count": search_result_count,
        "accepted_count": accepted_count,
        "success_count": success_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "warning": warning,
    }


def _social_platform_failure_summary(platform: str, exc: Exception) -> dict[str, object]:
    message = str(exc)
    reason = "platform_failed"
    if platform == "x" and "All X search keywords failed" in message:
        reason = "google_pse_request_failed"
    elif "No supported" in message or "no supported" in message:
        reason = "no_supported_post_urls"
    return {
        "platform": platform,
        "status": "failed",
        "search_result_count": 0,
        "accepted_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "reason": reason,
        "error": f"{type(exc).__name__}: {message}",
    }


def _x_social_warning(summary: dict[str, object]) -> str | None:
    if int(summary.get("search_result_count", 0) or 0) == 0:
        return "zero_x_search_results"
    if int(summary.get("unique_url_count", 0) or 0) == 0:
        return "x_search_results_without_supported_post_urls"
    if int(summary.get("failed_count", 0) or 0) > 0:
        return "x_extraction_failed_for_some_posts"
    return None


def main() -> None:
    args = parse_args()
    if args.collect:
        run_social_collect(args)
    elif args.keyword:
        run_search(args.keyword.strip(), args.headless, args.max_results)
    elif args.x_url:
        run_x_post_extraction(
            args.x_url.strip(),
            args.headless,
            args.x_storage_state,
            normalize_output=args.normalize_output,
        )
    elif args.x_batch_file:
        run_x_batch_extraction(
            input_path=args.x_batch_file,
            output_path=args.x_batch_output,
            limit=args.x_batch_limit,
            delay_seconds=args.x_batch_delay,
            resume=args.x_batch_resume,
            headless=args.headless,
            storage_state_path=args.x_storage_state,
            normalize_output=args.normalize_output,
        )
    elif args.x_search_keyword or args.x_search_keywords_file:
        run_x_search_collection(
            keywords=args.x_search_keyword,
            keywords_file=args.x_search_keywords_file,
            max_results=args.x_search_max_results,
            output_path=args.x_batch_output,
            batch_limit=args.x_batch_limit,
            delay_seconds=args.x_batch_delay,
            resume=args.x_batch_resume,
            headless=args.headless,
            storage_state_path=args.x_storage_state,
            normalize_output=args.normalize_output,
        )
    elif args.facebook_url:
        run_facebook_post_extraction(
            args.facebook_url.strip(),
            args.headless,
            args.facebook_storage_state,
            args.facebook_save_debug_bundle,
            normalize_output=args.normalize_output,
        )
    elif args.facebook_batch_file:
        run_facebook_batch_extraction(
            input_path=args.facebook_batch_file,
            output_path=args.facebook_batch_output,
            limit=args.facebook_batch_limit,
            delay_seconds=args.facebook_batch_delay,
            resume=args.facebook_batch_resume,
            headless=args.headless,
            storage_state_path=args.facebook_storage_state,
            save_debug_bundle=args.facebook_save_debug_bundle,
            normalize_output=args.normalize_output,
        )
    elif args.facebook_job_config:
        run_facebook_job_config_mode(
            args.facebook_job_config,
            headless=args.headless,
            storage_state_path=args.facebook_storage_state,
            ignore_history=args.facebook_ignore_history,
        )
    elif args.facebook_resume_run:
        run_facebook_resume_run(args.facebook_resume_run)
    elif args.facebook_retry_failed_run:
        run_facebook_retry_failed_run(args.facebook_retry_failed_run)
    elif args.facebook_export_run:
        run_facebook_export_run(args.facebook_export_run)
    else:
        run_facebook_search_collection(
            keywords=args.facebook_search_keyword,
            keywords_file=args.facebook_search_keywords_file,
            manifest_path=args.facebook_search_manifest,
            max_results=args.facebook_search_max_results,
            output_path=args.facebook_batch_output,
            delay_seconds=args.facebook_batch_delay,
            resume=args.facebook_batch_resume,
            headless=args.headless,
            storage_state_path=args.facebook_storage_state,
            save_debug_bundle=args.facebook_save_debug_bundle,
            normalize_output=args.normalize_output,
        )


def run_search(keyword: str, headless: bool, max_results: int) -> None:
    try:
        from src.search.google_pse import GooglePSESearchError, search_google_pse_with_metadata
    except ModuleNotFoundError as exc:
        _raise_playwright_install_error(exc)

    print(f"Searching Google PSE for: {keyword}")
    try:
        search_response = search_google_pse_with_metadata(
            keyword=keyword,
            headless=headless,
            max_results=max_results,
        )
    except (ValueError, GooglePSESearchError) as exc:
        raise SystemExit(f"Google PSE search failed: {exc}") from exc

    for page_log in search_response.page_logs:
        print(
            f"Google PSE page {page_log.page_number}: "
            f"start={page_log.start}, requested={page_log.requested}, received={page_log.received}"
        )
    if search_response.partial_error:
        print(f"Google PSE returned partial results: {search_response.partial_error}")

    raw_payload = build_output(keyword, search_response.results)
    write_output(raw_payload, RAW_OUTPUT_PATH)

    posts = filter_post_results(raw_payload["results"])
    post_payload = build_post_output(keyword, raw_payload["result_count"], posts)
    write_output(post_payload, POST_OUTPUT_PATH)

    print(
        f"Found {raw_payload['result_count']} raw result(s) "
        f"across {len(search_response.page_logs)} page(s)."
    )
    print(
        f"Requested up to {search_response.requested_count} result(s), "
        f"received {raw_payload['result_count']}."
    )
    print(f"Identified {post_payload['post_count']} post URL(s).")
    print(f"Raw JSON written to {RAW_OUTPUT_PATH}")
    print(f"Post URL JSON written to {POST_OUTPUT_PATH}")


def run_x_post_extraction(
    url: str,
    headless: bool,
    storage_state_path: Path | None = None,
    normalize_output: bool = False,
) -> None:
    try:
        from src.extractors.x_post import XPostExtractionError, extract_x_post
    except ModuleNotFoundError as exc:
        _raise_playwright_install_error(exc)

    print(f"Extracting X post: {url}")
    try:
        payload = extract_x_post(
            url=url,
            headless=headless,
            storage_state_path=storage_state_path,
        )
    except (ValueError, XPostExtractionError) as exc:
        raise SystemExit(f"X post extraction failed: {exc}") from exc

    output_payload = normalize_social_post(payload) if normalize_output else payload
    write_output(output_payload, X_POST_OUTPUT_PATH)
    if payload["collection_status"] == "success":
        print("X post extracted successfully.")
    else:
        print("X post extracted with partial data.")
    print(f"JSON written to {X_POST_OUTPUT_PATH}")


def run_x_batch_extraction(
    input_path: Path,
    output_path: Path,
    limit: int | None,
    delay_seconds: float,
    resume: bool,
    headless: bool,
    storage_state_path: Path | None,
    normalize_output: bool = False,
) -> None:
    from src.batch.x_batch import run_x_batch

    if limit is not None and limit < 1:
        raise SystemExit("--x-batch-limit must be greater than 0.")
    if delay_seconds < 0:
        raise SystemExit("--x-batch-delay must be 0 or greater.")

    print(f"Extracting X posts from: {input_path}")
    try:
        summary = run_x_batch(
            input_path=input_path,
            storage_state_path=storage_state_path,
            output_path=output_path,
            limit=limit,
            delay_seconds=delay_seconds,
            headless=headless,
            resume=resume,
            record_transform=normalize_social_post if normalize_output else None,
        )
    except (ValueError, OSError, RuntimeError) as exc:
        raise SystemExit(f"X batch extraction failed: {exc}") from exc

    print(
        "X batch finished: "
        f"{summary['success_count']} success, "
        f"{summary['failed_count']} failed, "
        f"{summary['skipped_count']} skipped."
    )
    print(f"JSONL written to {output_path}")


def run_x_search_collection(
    keywords: list[str] | None,
    keywords_file: Path | None,
    max_results: int,
    output_path: Path,
    batch_limit: int | None,
    delay_seconds: float,
    resume: bool,
    headless: bool,
    storage_state_path: Path | None,
    normalize_output: bool = False,
) -> None:
    try:
        from src.pipelines.x_search_pipeline import merge_x_search_keywords, run_x_search_pipeline
        from src.search.google_pse import GooglePSESearchError
    except ModuleNotFoundError as exc:
        _raise_playwright_install_error(exc)

    try:
        selected_keywords = merge_x_search_keywords(keywords, keywords_file)
        summary = run_x_search_pipeline(
            keywords=selected_keywords,
            max_results=max_results,
            storage_state_path=storage_state_path,
            output_path=output_path,
            batch_limit=batch_limit,
            delay_seconds=delay_seconds,
            headless=headless,
            resume=resume,
            record_transform=normalize_social_post if normalize_output else None,
        )
    except (ValueError, OSError, RuntimeError, GooglePSESearchError) as exc:
        raise SystemExit(f"X search collection failed: {exc}") from exc

    print(
        "X search collection finished: "
        f"{summary['keyword_count']} keyword(s), "
        f"{summary['search_result_count']} search result(s), "
        f"{summary['supported_x_post_url_count']} supported X post URL(s), "
        f"{summary['unique_url_count']} unique URL(s), "
        f"{summary['extracted_success_count']} extracted, "
        f"{summary['failed_count']} failed."
    )
    if not summary["batch_started"]:
        print("No supported X post URLs found; X batch was not started.")
    print(f"JSONL output: {output_path}")


def run_facebook_post_extraction(
    url: str,
    headless: bool,
    storage_state_path: Path | None = None,
    save_debug_bundle: bool = False,
    normalize_output: bool = False,
) -> None:
    try:
        from src.extractors.facebook_post import FacebookPostExtractionError, extract_facebook_post
    except ModuleNotFoundError as exc:
        _raise_playwright_install_error(exc)

    print(f"Extracting Facebook post: {url}")
    try:
        payload = extract_facebook_post(
            url=url,
            headless=headless,
            storage_state_path=storage_state_path,
            save_debug_bundle=save_debug_bundle,
        )
    except (ValueError, FacebookPostExtractionError) as exc:
        raise SystemExit(f"Facebook post extraction failed: {exc}") from exc

    output_payload = normalize_social_post(payload) if normalize_output else payload
    write_output(output_payload, FACEBOOK_POST_OUTPUT_PATH)
    if payload["collection_status"] == "success":
        print("Facebook post extracted successfully.")
    else:
        print("Facebook post extracted with partial data.")
    print(f"JSON written to {FACEBOOK_POST_OUTPUT_PATH}")


def run_facebook_batch_extraction(
    input_path: Path,
    output_path: Path,
    limit: int | None,
    delay_seconds: float,
    resume: bool,
    headless: bool,
    storage_state_path: Path | None,
    save_debug_bundle: bool = False,
    normalize_output: bool = False,
) -> None:
    try:
        from src.batch.facebook_batch import DEFAULT_BATCH_SUMMARY_PATH, run_facebook_batch
    except ModuleNotFoundError as exc:
        _raise_playwright_install_error(exc)

    if limit is not None and limit < 1:
        raise SystemExit("--facebook-batch-limit must be greater than 0.")
    if delay_seconds < 0:
        raise SystemExit("--facebook-batch-delay must be 0 or greater.")

    print(f"Extracting Facebook posts from: {input_path}")
    try:
        summary = run_facebook_batch(
            input_path=input_path,
            storage_state_path=storage_state_path,
            output_path=output_path,
            summary_path=DEFAULT_BATCH_SUMMARY_PATH,
            limit=limit,
            delay_seconds=delay_seconds,
            headless=headless,
            resume=resume,
            save_debug_bundle=save_debug_bundle,
            record_transform=normalize_social_post if normalize_output else None,
        )
    except (ValueError, OSError, RuntimeError) as exc:
        raise SystemExit(f"Facebook batch extraction failed: {exc}") from exc

    print(
        "Facebook batch finished: "
        f"{summary['current_run']['success_count']} success/partial, "
        f"{summary['current_run']['failed_count']} failed, "
        f"{summary['current_run']['skipped_count']} skipped."
    )
    print(f"JSONL written to {output_path}")
    print(f"Summary written to {DEFAULT_BATCH_SUMMARY_PATH}")


def run_facebook_search_collection(
    keywords: list[str] | None,
    keywords_file: Path | None,
    manifest_path: Path | None,
    max_results: int,
    output_path: Path,
    delay_seconds: float,
    resume: bool,
    headless: bool,
    storage_state_path: Path | None,
    save_debug_bundle: bool = False,
    normalize_output: bool = False,
) -> None:
    try:
        from src.pipelines.facebook_search_pipeline import (
            load_keywords_file,
            run_facebook_multi_search_pipeline,
            run_facebook_search_manifest_pipeline,
        )
        from src.search.google_pse import GooglePSESearchError
    except ModuleNotFoundError as exc:
        _raise_playwright_install_error(exc)

    if manifest_path is not None:
        print(f"Collecting Facebook posts from search manifest: {manifest_path}")
        try:
            summary = run_facebook_search_manifest_pipeline(
                manifest_path=manifest_path,
                storage_state_path=storage_state_path,
                output_path=output_path,
                delay_seconds=delay_seconds,
                headless=headless,
                resume=resume,
                save_debug_bundle=save_debug_bundle,
                record_transform=normalize_social_post if normalize_output else None,
            )
        except (ValueError, OSError, RuntimeError) as exc:
            raise SystemExit(f"Facebook search manifest collection failed: {exc}") from exc
        _print_facebook_search_summary(summary, output_path)
        return

    try:
        selected_keywords = load_keywords_file(keywords_file) if keywords_file is not None else (keywords or [])
    except ValueError as exc:
        raise SystemExit(f"Facebook search collection failed: {exc}") from exc

    print(f"Searching Google PSE and collecting Facebook posts for {len(selected_keywords)} keyword(s).")
    try:
        summary = run_facebook_multi_search_pipeline(
            keywords=selected_keywords,
            max_results=max_results,
            storage_state_path=storage_state_path,
            output_path=output_path,
            delay_seconds=delay_seconds,
            headless=headless,
            resume=resume,
            save_debug_bundle=save_debug_bundle,
            record_transform=normalize_social_post if normalize_output else None,
        )
    except (ValueError, OSError, RuntimeError, GooglePSESearchError) as exc:
        raise SystemExit(f"Facebook search collection failed: {exc}") from exc

    _print_facebook_search_summary(summary, output_path)


def _print_facebook_search_summary(summary: dict[str, object], output_path: Path) -> None:
    print(
        "Facebook search collection finished: "
        f"{summary['search_result_count']} search result(s), "
        f"{summary['batch_input_count']} accepted, "
        f"{summary['rejected_count']} rejected, "
        f"{summary['duplicate_count']} duplicate(s)."
    )
    if not summary["batch_started"]:
        print("No supported Facebook post URLs found; Facebook batch was not started.")
        print(f"JSONL output was not written or modified: {output_path}")
    else:
        current = summary["batch_current_run"]
        print(
            "Facebook batch finished: "
            f"{current['success_count']} success/partial, "
            f"{current['failed_count']} failed, "
            f"{current['skipped_count']} skipped."
        )
        print(f"JSONL written to {output_path}")
    print(f"Pipeline summary written to {summary['summary_path']}")


def run_facebook_job_config_mode(
    config_path: Path,
    headless: bool,
    storage_state_path: Path | None,
    ignore_history: bool,
) -> None:
    try:
        from src.pipelines.facebook_job_pipeline import run_facebook_job_config
    except ModuleNotFoundError as exc:
        _raise_playwright_install_error(exc)
    print(f"Running Facebook job config: {config_path}")
    try:
        summary = run_facebook_job_config(
            config_path,
            headless_override=headless if headless else None,
            storage_state_override=storage_state_path,
            ignore_history=ignore_history,
        )
    except (ValueError, OSError, RuntimeError) as exc:
        raise SystemExit(f"Facebook job failed: {exc}") from exc
    _print_facebook_job_summary(summary)


def run_facebook_resume_run(run_dir: Path) -> None:
    from src.pipelines.facebook_job_pipeline import resume_facebook_run

    print(f"Resuming Facebook run: {run_dir}")
    try:
        summary = resume_facebook_run(run_dir)
    except (ValueError, OSError, RuntimeError) as exc:
        raise SystemExit(f"Facebook run resume failed: {exc}") from exc
    _print_facebook_job_summary(summary)


def run_facebook_retry_failed_run(run_dir: Path) -> None:
    from src.pipelines.facebook_job_pipeline import retry_failed_facebook_run

    print(f"Retrying failed Facebook items from run: {run_dir}")
    try:
        summary = retry_failed_facebook_run(run_dir)
    except (ValueError, OSError, RuntimeError) as exc:
        raise SystemExit(f"Facebook failed-item retry failed: {exc}") from exc
    _print_facebook_job_summary(summary)


def run_facebook_export_run(run_dir: Path) -> None:
    from src.pipelines.facebook_job_pipeline import export_facebook_run

    print(f"Exporting Facebook run: {run_dir}")
    try:
        summary = export_facebook_run(run_dir)
    except (ValueError, OSError, RuntimeError) as exc:
        raise SystemExit(f"Facebook run export failed: {exc}") from exc
    _print_facebook_job_summary(summary)


def _print_facebook_job_summary(summary: dict[str, object]) -> None:
    print(
        "Facebook job finished: "
        f"{summary.get('success_count', 0)} success, "
        f"{summary.get('partial_count', 0)} partial, "
        f"{summary.get('failed_count', 0)} failed, "
        f"{summary.get('skipped_count', 0)} skipped."
    )
    print(f"Run directory: {summary.get('run_dir')}")
    print(f"Final JSONL: {summary.get('final_results_path')}")
    print(f"CSV: {summary.get('csv_path')}")


def _raise_playwright_install_error(exc: ModuleNotFoundError) -> None:
    if exc.name == "playwright":
        raise SystemExit(
            "Playwright is not installed. Run `pip install -r requirements.txt` "
            "and then `playwright install chromium`."
        ) from exc
    raise exc


if __name__ == "__main__":
    main()
