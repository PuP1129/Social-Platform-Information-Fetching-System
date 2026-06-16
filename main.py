from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.filters.post_url import filter_post_results

RAW_OUTPUT_PATH = Path("output") / "search_results.json"
POST_OUTPUT_PATH = Path("output") / "post_urls.json"
X_POST_OUTPUT_PATH = Path("output") / "x_post.json"
FACEBOOK_POST_OUTPUT_PATH = Path("output") / "facebook_post.json"
FACEBOOK_BATCH_OUTPUT_PATH = Path("output") / "facebook_posts.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search Google PSE or extract social posts.")
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--keyword", help="Single keyword to search.")
    input_group.add_argument("--x-url", help="Single X post URL to extract.")
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
    parser.add_argument("--headless", action="store_true", help="Run Chromium in headless mode.")
    parser.add_argument("--max-results", type=int, default=10, help="Maximum number of merged Google PSE results to save.")
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
    return parser.parse_args()


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


def main() -> None:
    args = parse_args()
    if args.keyword:
        run_search(args.keyword.strip(), args.headless, args.max_results)
    elif args.x_url:
        run_x_post_extraction(args.x_url.strip(), args.headless, args.x_storage_state)
    elif args.facebook_url:
        run_facebook_post_extraction(
            args.facebook_url.strip(),
            args.headless,
            args.facebook_storage_state,
            args.facebook_save_debug_bundle,
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
        )
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


def run_x_post_extraction(url: str, headless: bool, storage_state_path: Path | None = None) -> None:
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

    write_output(payload, X_POST_OUTPUT_PATH)
    if payload["collection_status"] == "success":
        print("X post extracted successfully.")
    else:
        print("X post extracted with partial data.")
    print(f"JSON written to {X_POST_OUTPUT_PATH}")


def run_facebook_post_extraction(
    url: str,
    headless: bool,
    storage_state_path: Path | None = None,
    save_debug_bundle: bool = False,
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

    write_output(payload, FACEBOOK_POST_OUTPUT_PATH)
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


def _raise_playwright_install_error(exc: ModuleNotFoundError) -> None:
    if exc.name == "playwright":
        raise SystemExit(
            "Playwright is not installed. Run `pip install -r requirements.txt` "
            "and then `playwright install chromium`."
        ) from exc
    raise exc


if __name__ == "__main__":
    main()
