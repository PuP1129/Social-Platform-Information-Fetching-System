from __future__ import annotations

from html import unescape
from urllib.parse import ParseResult, parse_qs, quote, unquote, urlparse

X_HOSTS = {
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
}
FACEBOOK_HOSTS = {
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "web.facebook.com",
}


def classify_post_url(url: str) -> dict[str, str] | None:
    """Classify and normalize a supported X or Facebook post URL."""
    parsed_url = _parse_supported_url(url)
    if parsed_url is None:
        return None

    hostname = parsed_url.hostname.lower() if parsed_url.hostname else ""
    if hostname in X_HOSTS:
        return _classify_x_url(parsed_url.path)
    if hostname in FACEBOOK_HOSTS:
        return _classify_facebook_url(parsed_url.path, parsed_url.query)
    return None


def filter_post_results(results: list[dict]) -> list[dict]:
    """Keep supported post URLs from search results and deduplicate by platform/post_id."""
    filtered_results: list[dict] = []
    seen_posts: set[tuple[str, str]] = set()

    for result in results:
        if not isinstance(result, dict):
            continue

        url = result.get("url")
        if not isinstance(url, str):
            continue

        classified = classify_post_url(url)
        if classified is None:
            continue

        dedupe_key = (classified["platform"], classified["post_id"])
        if dedupe_key in seen_posts:
            continue

        seen_posts.add(dedupe_key)
        filtered_result = {
            "rank": result.get("rank"),
            "platform": classified["platform"],
            "post_id": classified["post_id"],
            "original_url": url,
            "canonical_url": classified["canonical_url"],
            "title": result.get("title"),
            "snippet": result.get("snippet"),
        }
        if "context_type" in classified:
            filtered_result["context_type"] = classified["context_type"]
        filtered_results.append(filtered_result)

    return filtered_results


def _parse_supported_url(url: str) -> ParseResult | None:
    if not isinstance(url, str):
        return None

    cleaned_url = unescape(url).strip()
    if not cleaned_url:
        return None

    try:
        parsed_url = urlparse(cleaned_url)
    except ValueError:
        return None

    if parsed_url.scheme.lower() not in {"http", "https"}:
        return None
    if not parsed_url.hostname:
        return None

    return parsed_url


def _classify_x_url(path: str) -> dict[str, str] | None:
    segments = _path_segments(path)

    if len(segments) >= 4 and segments[:3] == ["i", "web", "status"]:
        post_id = segments[3]
        if not post_id.isdigit():
            return None
        return {
            "platform": "x",
            "post_id": post_id,
            "canonical_url": f"https://x.com/i/web/status/{post_id}",
        }

    if len(segments) >= 3 and segments[1] == "status":
        handle = segments[0]
        post_id = segments[2]
        if not handle or not post_id.isdigit():
            return None
        return {
            "platform": "x",
            "post_id": post_id,
            "canonical_url": f"https://x.com/{_quote_path_part(handle)}/status/{post_id}",
        }

    return None


def _classify_facebook_url(path: str, query: str) -> dict[str, str] | None:
    segments = _path_segments(path)

    if len(segments) >= 4 and segments[0] == "groups" and segments[2] in {"posts", "permalink"}:
        group = segments[1]
        post_id = segments[3]
        if not group or not _is_valid_facebook_path_post_id(post_id):
            return None
        return {
            "platform": "facebook",
            "post_id": post_id,
            "canonical_url": (
                "https://www.facebook.com/groups/"
                f"{_quote_path_part(group)}/posts/{_quote_path_part(post_id)}"
            ),
            "context_type": "group",
        }

    if len(segments) >= 3 and segments[1] == "posts":
        account = segments[0]
        post_id = segments[2]
        if not account or not _is_valid_facebook_path_post_id(post_id):
            return None
        return {
            "platform": "facebook",
            "post_id": post_id,
            "canonical_url": (
                "https://www.facebook.com/"
                f"{_quote_path_part(account)}/posts/{_quote_path_part(post_id)}"
            ),
            "context_type": "profile",
        }

    page_name = segments[0] if len(segments) == 1 else ""
    if page_name not in {"permalink.php", "story.php"}:
        return None

    params = parse_qs(query, keep_blank_values=False)
    story_fbid = _first_query_value(params, "story_fbid")
    account_id = _first_query_value(params, "id")
    if not story_fbid or not _is_valid_facebook_query_post_id(story_fbid):
        return None

    canonical_query = f"story_fbid={quote(story_fbid, safe='')}"
    if account_id:
        canonical_query = f"{canonical_query}&id={quote(account_id, safe='')}"

    return {
        "platform": "facebook",
        "post_id": story_fbid,
        "canonical_url": f"https://www.facebook.com/{page_name}?{canonical_query}",
    }


def _path_segments(path: str) -> list[str]:
    return [unquote(segment) for segment in path.split("/") if segment]


def _quote_path_part(value: str) -> str:
    return quote(value, safe="")


def _first_query_value(params: dict[str, list[str]], name: str) -> str | None:
    values = params.get(name)
    if not values:
        return None
    value = values[0].strip()
    return value or None


def _is_valid_facebook_path_post_id(post_id: str) -> bool:
    if post_id.isdigit():
        return True
    if post_id.startswith("pfbid") and post_id.isalnum():
        return True
    return False


def _is_valid_facebook_query_post_id(post_id: str) -> bool:
    return post_id.isalnum()
