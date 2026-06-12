from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.filters.post_url import filter_post_results

RAW_OUTPUT_PATH = Path("output") / "search_results.json"
POST_OUTPUT_PATH = Path("output") / "post_urls.json"
X_POST_OUTPUT_PATH = Path("output") / "x_post.json"
FACEBOOK_POST_OUTPUT_PATH = Path("output") / "facebook_post.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search Google PSE or extract one X post.")
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--keyword", help="Single keyword to search.")
    input_group.add_argument("--x-url", help="Single X post URL to extract.")
    input_group.add_argument("--facebook-url", help="Single public Facebook post URL to extract.")
    parser.add_argument("--headless", action="store_true", help="Run Chromium in headless mode.")
    parser.add_argument("--max-results", type=int, default=10, help="Maximum number of first-page results to save.")
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
    else:
        run_facebook_post_extraction(
            args.facebook_url.strip(),
            args.headless,
            args.facebook_storage_state,
        )


def run_search(keyword: str, headless: bool, max_results: int) -> None:
    try:
        from src.search.google_pse import search_google_pse
    except ModuleNotFoundError as exc:
        _raise_playwright_install_error(exc)

    print(f"Searching Google PSE for: {keyword}")
    results = search_google_pse(
        keyword=keyword,
        headless=headless,
        max_results=max_results,
    )
    raw_payload = build_output(keyword, results)
    write_output(raw_payload, RAW_OUTPUT_PATH)

    posts = filter_post_results(raw_payload["results"])
    post_payload = build_post_output(keyword, raw_payload["result_count"], posts)
    write_output(post_payload, POST_OUTPUT_PATH)

    print(f"Found {raw_payload['result_count']} raw result(s).")
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
        )
    except (ValueError, FacebookPostExtractionError) as exc:
        raise SystemExit(f"Facebook post extraction failed: {exc}") from exc

    write_output(payload, FACEBOOK_POST_OUTPUT_PATH)
    if payload["collection_status"] == "success":
        print("Facebook post extracted successfully.")
    else:
        print("Facebook post extracted with partial data.")
    print(f"JSON written to {FACEBOOK_POST_OUTPUT_PATH}")


def _raise_playwright_install_error(exc: ModuleNotFoundError) -> None:
    if exc.name == "playwright":
        raise SystemExit(
            "Playwright is not installed. Run `pip install -r requirements.txt` "
            "and then `playwright install chromium`."
        ) from exc
    raise exc


if __name__ == "__main__":
    main()
