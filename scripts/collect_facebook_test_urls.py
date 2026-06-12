from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import Page, sync_playwright

from src.filters.post_url import filter_post_results
from src.search.google_pse import search_google_pse

OUTPUT_PATH = PROJECT_ROOT / "output" / "facebook_test_urls.json"
MAX_REJECTED = 20
ACCEPTED_DESTINATION_TYPES = {"standard_post", "story_or_permalink"}
DEFAULT_QUERIES = [
    "{keyword}",
    "{keyword} Facebook",
    "{keyword} official Facebook",
    "{keyword} update Facebook",
    "{keyword} event Facebook",
    "site:facebook.com {keyword}",
    'site:facebook.com "{keyword}"',
    'site:facebook.com "{keyword}" "story_fbid"',
    'site:facebook.com "{keyword}" "permalink.php"',
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect preflighted Facebook post URLs for extractor testing.")
    parser.add_argument("--keyword", default="Genshin Impact", help="Topic keyword to search.")
    parser.add_argument(
        "--target-count",
        type=int,
        default=5,
        help="Number of accepted Facebook post candidates to collect, from 1 to 5.",
    )
    parser.add_argument(
        "--facebook-storage-state",
        type=Path,
        required=True,
        help="Path to a local Playwright storage-state JSON file for authenticated Facebook preflight.",
    )
    parser.add_argument("--headless", action="store_true", help="Run Chromium in headless mode.")
    args = parser.parse_args()
    if not 1 <= args.target_count <= 5:
        parser.error("--target-count must be between 1 and 5.")
    if not args.facebook_storage_state.exists():
        parser.error(f"--facebook-storage-state does not exist: {args.facebook_storage_state}")
    if not args.facebook_storage_state.is_file():
        parser.error(f"--facebook-storage-state is not a file: {args.facebook_storage_state}")
    return args


def collect_facebook_test_urls(
    keyword: str,
    target_count: int,
    storage_state_path: Path,
    headless: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    posts: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_candidates: set[tuple[str, str]] = set()
    candidates: list[tuple[dict[str, Any], str]] = []
    query_stats: list[dict[str, Any]] = []
    successful_queries = 0

    for query in _build_queries(keyword):
        try:
            raw_results = search_google_pse(query, headless=headless, max_results=10)
        except Exception as exc:
            query_stats.append(
                {
                    "query": query,
                    "raw_result_count": 0,
                    "facebook_candidate_count": 0,
                    "status": "failed",
                    "error": str(exc),
                }
            )
            continue

        successful_queries += 1
        ranked_results = _add_ranks(raw_results)
        filtered_posts = filter_post_results(ranked_results)
        facebook_candidates = [post for post in filtered_posts if post.get("platform") == "facebook"]
        query_stats.append(
            {
                "query": query,
                "raw_result_count": len(raw_results),
                "facebook_candidate_count": len(facebook_candidates),
                "status": "success",
                "error": None,
            }
        )

        for candidate in facebook_candidates:
            identity = candidate_identity(candidate)
            if identity in seen_candidates:
                continue
            seen_candidates.add(identity)
            candidates.append((candidate, query))

    if successful_queries == 0:
        raise RuntimeError(f"All queries failed: {query_stats}")

    playwright = None
    browser = None
    context = None
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(storage_state_path))

        for candidate, query in candidates:
            if len(posts) >= target_count:
                break

            preflight = preflight_candidate(context, candidate)
            if is_accepted_preflight(preflight):
                posts.append(_accepted_post_payload(candidate, preflight, query))
            elif len(rejected) < MAX_REJECTED:
                rejected.append(_rejected_post_payload(candidate, preflight))
    finally:
        if context is not None:
            context.close()
        if browser is not None:
            browser.close()
        if playwright is not None:
            playwright.stop()

    payload = {
        "topic": keyword,
        "query_count": len(query_stats),
        "candidate_count": len(candidates),
        "post_count": len(posts),
        "posts": posts,
        "rejected": rejected,
    }
    return payload, query_stats


def preflight_candidate(context: Any, candidate: dict[str, Any]) -> dict[str, Any]:
    page = context.new_page()
    canonical_url = candidate["canonical_url"]
    try:
        page.goto(canonical_url, wait_until="domcontentloaded", timeout=30_000)
        _wait_for_redirects_to_settle(page)
        final_url = page.url
        destination_type = classify_final_destination(final_url)
        login_redirected = destination_type == "login"
        unavailable = unavailable_message_detected(page)
        semantic_counts = semantic_signal_counts(page)
        return {
            "initial_canonical_url": canonical_url,
            "final_url": final_url,
            "page_title": _safe_page_title(page),
            "login_redirected": login_redirected,
            "unavailable_message_detected": unavailable,
            "final_destination_type": destination_type,
            "semantic_signal_counts": semantic_counts,
            "has_semantic_signal": has_semantic_signal(semantic_counts),
            "preflight_error": None,
        }
    except Exception as exc:
        return {
            "initial_canonical_url": canonical_url,
            "final_url": _safe_page_url(page),
            "page_title": _safe_page_title(page),
            "login_redirected": False,
            "unavailable_message_detected": False,
            "final_destination_type": "unknown",
            "semantic_signal_counts": {},
            "has_semantic_signal": False,
            "preflight_error": str(exc),
        }
    finally:
        page.close()


def classify_final_destination(url: str | None) -> str:
    if not url:
        return "unknown"

    parsed = urlparse(url)
    path = parsed.path.lower()
    query = parse_qs(parsed.query)

    if path == "/login/" or path == "/login" or path.startswith("/login/"):
        return "login"
    if path.startswith("/watch/") or (path == "/watch" and "v" in query):
        return "watch"
    if "/videos/" in path:
        return "video"
    if path.startswith("/reel/") or path.startswith("/reels/"):
        return "reel"
    if path.startswith("/groups/") or "/groups/" in path:
        return "group"
    if "/posts/" in path:
        return "standard_post"
    if path.endswith("/story.php") or path.endswith("/permalink.php"):
        return "story_or_permalink"
    return "unknown"


def is_accepted_preflight(preflight: dict[str, Any]) -> bool:
    return (
        preflight["final_destination_type"] in ACCEPTED_DESTINATION_TYPES
        and not preflight["login_redirected"]
        and not preflight["unavailable_message_detected"]
        and preflight["has_semantic_signal"]
        and preflight["preflight_error"] is None
    )


def rejection_reason(preflight: dict[str, Any]) -> str:
    destination_type = preflight["final_destination_type"]
    if preflight["preflight_error"]:
        return f"Preflight failed: {preflight['preflight_error']}"
    if preflight["login_redirected"]:
        return "Redirected to Facebook login"
    if preflight["unavailable_message_detected"]:
        return "Facebook reported unavailable content"
    if destination_type in {"watch", "video"}:
        return "Unsupported Facebook Watch/video destination"
    if destination_type == "reel":
        return "Unsupported Facebook Reel destination"
    if destination_type == "group":
        return "Unsupported Facebook group destination"
    if destination_type == "unknown":
        return "Unknown Facebook final destination"
    if not preflight["has_semantic_signal"]:
        return "No plausible post semantic signal found"
    return "Rejected by conservative preflight rules"


def semantic_signal_counts(page: Page) -> dict[str, int]:
    return {
        "role_article": _safe_count(page, '[role="article"]'),
        "message_container": _safe_count(page, '[data-ad-preview="message"], [data-ad-comet-preview="message"]'),
        "time_or_date_link": _safe_count(page, 'time, a[aria-label]'),
        "engagement_controls": _safe_count(
            page,
            '[aria-label*="comment" i], [aria-label*="share" i], [aria-label*="reaction" i], [aria-label*="like" i]',
        ),
    }


def has_semantic_signal(counts: dict[str, int]) -> bool:
    return any(value > 0 for value in counts.values())


def unavailable_message_detected(page: Page) -> bool:
    try:
        body_text = page.locator("body").inner_text(timeout=1_000)
    except PlaywrightError:
        return False
    normalized = " ".join(body_text.split()).lower()
    markers = (
        "this content isn't available",
        "this page isn't available",
        "this content is no longer available",
        "此内容目前无法显示",
        "链接可能已失效",
    )
    return any(marker in normalized for marker in markers)


def write_output(payload: dict[str, Any]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _accepted_post_payload(candidate: dict[str, Any], preflight: dict[str, Any], query: str) -> dict[str, Any]:
    return {
        "post_id": candidate["post_id"],
        "original_url": candidate["original_url"],
        "canonical_url": candidate["canonical_url"],
        "final_url": preflight["final_url"],
        "final_destination_type": preflight["final_destination_type"],
        "title": candidate.get("title"),
        "snippet": candidate.get("snippet"),
        "source_query": query,
        "preflight_status": "accepted",
    }


def candidate_identity(candidate: dict[str, Any]) -> tuple[str, str]:
    return str(candidate["platform"]), str(candidate["post_id"])


def _rejected_post_payload(candidate: dict[str, Any], preflight: dict[str, Any]) -> dict[str, Any]:
    return {
        "post_id": candidate["post_id"],
        "canonical_url": candidate["canonical_url"],
        "final_url": preflight["final_url"],
        "final_destination_type": preflight["final_destination_type"],
        "reason": rejection_reason(preflight),
    }


def _build_queries(keyword: str) -> list[str]:
    queries = []
    for template in DEFAULT_QUERIES:
        query = template.format(keyword=keyword)
        if query not in queries:
            queries.append(query)
    return queries[:10]


def _add_ranks(results: list[dict[str, str | None]]) -> list[dict[str, str | None | int]]:
    ranked_results: list[dict[str, str | None | int]] = []
    for rank, result in enumerate(results, start=1):
        ranked = dict(result)
        ranked["rank"] = rank
        ranked_results.append(ranked)
    return ranked_results


def _wait_for_redirects_to_settle(page: Page) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10_000)
        page.wait_for_load_state("networkidle", timeout=5_000)
    except PlaywrightTimeoutError:
        pass


def _safe_count(page: Page, selector: str) -> int:
    try:
        return page.locator(selector).count()
    except PlaywrightError:
        return 0


def _safe_page_url(page: Page) -> str | None:
    try:
        return page.url
    except PlaywrightError:
        return None


def _safe_page_title(page: Page) -> str | None:
    try:
        return page.title()
    except PlaywrightError:
        return None


def rejected_type_breakdown(rejected: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(item["final_destination_type"] for item in rejected))


def main() -> None:
    args = parse_args()
    try:
        payload, query_stats = collect_facebook_test_urls(
            keyword=args.keyword.strip(),
            target_count=args.target_count,
            storage_state_path=args.facebook_storage_state,
            headless=args.headless,
        )
    except RuntimeError as exc:
        raise SystemExit(f"Failed to collect Facebook test URLs: {exc}") from exc

    write_output(payload)
    for stat in query_stats:
        print(
            f"{stat['query']}: {stat['raw_result_count']} raw result(s), "
            f"{stat.get('facebook_candidate_count', 0)} filtered Facebook candidate(s), {stat['status']}"
        )

    breakdown = rejected_type_breakdown(payload["rejected"])
    print(f"Queries run: {payload['query_count']}")
    print(f"Filtered Facebook candidates: {payload['candidate_count']}")
    print(f"Accepted candidates: {payload['post_count']}")
    print(f"Rejected destination breakdown: {breakdown}")
    print(f"Saved output to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
