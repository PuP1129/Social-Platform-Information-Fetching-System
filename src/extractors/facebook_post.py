from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from src.filters.post_url import classify_post_url
from src.utils.metrics import parse_metric_count

DEBUG_OUTPUT_DIR = Path("output") / "debug"
FAILURE_SCREENSHOT_PATH = DEBUG_OUTPUT_DIR / "facebook_post_failure.png"
FAILURE_HTML_PATH = DEBUG_OUTPUT_DIR / "facebook_post_failure.html"
FAILURE_DIAGNOSTICS_PATH = DEBUG_OUTPUT_DIR / "facebook_post_diagnostics.json"

ROLE_ARTICLE_SELECTOR = '[role="article"]'
TARGET_CONTAINER_SELECTORS = (
    ROLE_ARTICLE_SELECTOR,
    "article",
    '[role="dialog"]',
)
MESSAGE_SELECTORS = (
    '[data-ad-preview="message"]',
    '[data-ad-comet-preview="message"]',
)


class FacebookPostExtractionError(RuntimeError):
    """Base exception for Facebook post extraction failures."""

    def __init__(self, message: str, diagnostics: dict[str, Any] | None = None) -> None:
        self.diagnostics = diagnostics
        super().__init__(message)


class FacebookLoginRequiredError(FacebookPostExtractionError):
    """Raised when Facebook requires login before the post can be read."""


class FacebookPostUnavailableError(FacebookPostExtractionError):
    """Raised when Facebook reports the post or page is unavailable."""


class FacebookPostNotFoundError(FacebookPostExtractionError):
    """Raised when the target Facebook post cannot be confirmed."""


def extract_facebook_post(
    url: str,
    headless: bool = False,
    timeout_ms: int = 30_000,
    storage_state_path: str | Path | None = None,
) -> dict[str, Any]:
    """Open a public Facebook post page and extract the target main post."""
    classified_url = classify_post_url(url)
    if classified_url is None:
        raise ValueError("url must be a supported Facebook post URL.")
    if classified_url["platform"] != "facebook":
        raise ValueError("url must be a Facebook post URL, not another platform.")

    normalized_storage_state_path = _validate_storage_state_path(storage_state_path)
    post_id = classified_url["post_id"]
    canonical_url = classified_url["canonical_url"]
    playwright = None
    browser = None
    context = None
    page = None

    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=headless)
        if normalized_storage_state_path is None:
            context = browser.new_context()
        else:
            context = browser.new_context(storage_state=str(normalized_storage_state_path))
        page = context.new_page()

        _open_post_page(page, canonical_url, timeout_ms)
        _wait_for_redirects_to_settle(page, timeout_ms)
        if normalized_storage_state_path is not None and _is_login_url(page.url):
            raise FacebookLoginRequiredError(
                "The supplied Facebook authentication state is missing or expired. "
                "Re-run scripts/create_facebook_storage_state.py."
            )
        _wait_for_page_body(page, timeout_ms)
        _raise_if_unavailable(page)

        target_article = _find_target_article(page, post_id)
        if target_article is None:
            metadata_result = _build_metadata_result(page, url, canonical_url, post_id)
            if metadata_result is not None:
                return metadata_result

            diagnostics = _collect_failure_diagnostics(page, post_id, url)
            if diagnostics["login_wall_detected"]:
                raise FacebookLoginRequiredError(
                    f"Facebook login wall detected and no target post DOM or useful metadata was available. "
                    f"Diagnostics: {_diagnostics_summary(diagnostics)}",
                    diagnostics=diagnostics,
                )
            raise FacebookPostNotFoundError(
                f"Could not confirm the target Facebook post container. "
                f"Diagnostics: {_diagnostics_summary(diagnostics)}",
                diagnostics=diagnostics,
            )

        result = _extract_article_data(target_article, url, canonical_url, post_id)
        if _has_any_core_field(result):
            return result

        metadata_result = _build_metadata_result(page, url, canonical_url, post_id)
        if metadata_result is not None:
            return metadata_result

        diagnostics = _collect_failure_diagnostics(
            page,
            post_id,
            url,
            field_locator_presence=_field_locator_presence(target_article),
        )
        raise FacebookPostExtractionError(
            f"Target Facebook post matched, but no core post fields could be extracted. "
            f"Diagnostics: {_diagnostics_summary(diagnostics)}",
            diagnostics=diagnostics,
        )
    finally:
        _close_quietly(page)
        _close_quietly(context)
        _close_quietly(browser)
        if playwright is not None:
            playwright.stop()


def _validate_storage_state_path(storage_state_path: str | Path | None) -> Path | None:
    if storage_state_path is None:
        return None

    path = Path(storage_state_path)
    if not path.exists():
        raise ValueError(f"Facebook storage state file does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"Facebook storage state path is not a file: {path}")
    return path


def _open_post_page(page: Page, canonical_url: str, timeout_ms: int) -> None:
    try:
        page.goto(canonical_url, wait_until="domcontentloaded", timeout=timeout_ms)
    except PlaywrightTimeoutError as exc:
        raise FacebookPostExtractionError(f"Facebook post page load timed out: {exc}") from exc
    except PlaywrightError as exc:
        raise FacebookPostExtractionError(f"Facebook post page open failed: {exc}") from exc


def _wait_for_page_body(page: Page, timeout_ms: int) -> None:
    try:
        page.locator("body").wait_for(state="visible", timeout=timeout_ms)
    except PlaywrightError as exc:
        raise FacebookPostExtractionError(f"Facebook page body did not become visible: {exc}") from exc


def _wait_for_redirects_to_settle(page: Page, timeout_ms: int) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5_000))
    except PlaywrightTimeoutError:
        pass


def _is_login_url(url: str) -> bool:
    parsed_url = urlparse(url)
    path = parsed_url.path.rstrip("/")
    return path == "/login" or path.startswith("/login/")


def _find_target_article(page: Page, post_id: str) -> Locator | None:
    target_links = _target_post_link_locator(page, post_id)
    best_container = None
    best_score = -1
    for selector in TARGET_CONTAINER_SELECTORS:
        containers = page.locator(selector).filter(has=target_links)
        for index in range(_safe_count(containers)):
            container = containers.nth(index)
            field_presence = _field_locator_presence(container)
            if not _has_post_semantics(field_presence):
                continue
            score = _container_score(field_presence)
            if score > best_score:
                best_container = container
                best_score = score

    if best_container is not None:
        return best_container

    if _is_single_post_permalink(page, post_id):
        fallback = _single_semantic_post_container(page)
        if fallback is not None:
            return fallback

    return None


def _target_post_link_locator(page: Page, post_id: str) -> Locator:
    return page.locator(_target_post_link_selector(post_id))


def _target_post_link_selector(post_id: str) -> str:
    selectors = (
        f'a[href*="story_fbid={post_id}"]',
        f'a[href*="/posts/{post_id}"]',
        f'a[href*="permalink.php"][href*="{post_id}"]',
        f'a[href*="{post_id}"]',
    )
    return ", ".join(selectors)


def _single_semantic_post_container(page: Page) -> Locator | None:
    candidates: list[Locator] = []
    for selector in TARGET_CONTAINER_SELECTORS:
        containers = page.locator(selector)
        for index in range(_safe_count(containers)):
            container = containers.nth(index)
            if _has_strong_post_semantics(_field_locator_presence(container)):
                candidates.append(container)
    if len(candidates) == 1:
        return candidates[0]
    return None


def _container_score(field_presence: dict[str, bool]) -> int:
    core_score = 0
    if field_presence["author"]:
        core_score += 1
    if field_presence["content"]:
        core_score += 3
    if field_presence["publish_time"]:
        core_score += 3
    metric_score = sum(
        1
        for key in ("like_metric", "comment_metric", "share_metric")
        if field_presence[key]
    )
    return core_score * 10 + metric_score


def _extract_article_data(
    article: Locator,
    input_url: str,
    canonical_url: str,
    post_id: str,
) -> dict[str, Any]:
    author = _extract_author(article)
    content = _extract_content(article)
    publish_time, publish_time_text = _extract_publish_time(article)
    like_count, comment_count, share_count = _extract_metrics(article)

    return _build_extraction_result(
        post_id=post_id,
        input_url=input_url,
        canonical_url=canonical_url,
        author=author,
        content=content,
        publish_time=publish_time,
        publish_time_text=publish_time_text,
        like_count=like_count,
        comment_count=comment_count,
        share_count=share_count,
        extraction_source="dom",
        metadata_error=None,
    )


def _build_extraction_result(
    post_id: str,
    input_url: str,
    canonical_url: str,
    author: dict[str, str | None],
    content: str | None,
    publish_time: str | None,
    publish_time_text: str | None,
    like_count: int | None,
    comment_count: int | None,
    share_count: int | None,
    extraction_source: str,
    metadata_error: str | None,
) -> dict[str, Any]:
    has_author = bool(author["name"] or author["profile_url"])
    has_content = bool(content)
    has_publish_time = bool(publish_time or publish_time_text)

    if not has_author and not has_content and not has_publish_time:
        raise FacebookPostExtractionError("No core Facebook post fields could be extracted.")

    status = "success" if extraction_source == "dom" and has_author and has_content and has_publish_time else "partial"
    missing = []
    if not has_author:
        missing.append("author")
    if not has_content:
        missing.append("content")
    if not has_publish_time:
        missing.append("publish_time")

    error = metadata_error
    if error is None and status == "partial":
        error = f"Missing core field(s): {', '.join(missing)}."

    result: dict[str, Any] = {
        "platform": "facebook",
        "post_id": post_id,
        "input_url": input_url,
        "canonical_url": canonical_url,
        "author": author,
        "content": content,
        "publish_time": publish_time,
        "reply_count": None,
        "like_count": like_count,
        "share_count": share_count,
        "comment_count": comment_count,
        "comments": [],
        "collected_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "collection_status": status,
        "extraction_source": extraction_source,
        "error": error,
    }
    if publish_time_text:
        result["publish_time_text"] = publish_time_text
    return result


def _build_metadata_result(
    page: Page,
    input_url: str,
    canonical_url: str,
    post_id: str,
) -> dict[str, Any] | None:
    metadata = _page_metadata(page)
    if not _metadata_has_post_specific_content(metadata):
        return None

    author_name = _metadata_author_name(metadata)
    metadata_url = metadata.get("og:url") or metadata.get("canonical") or canonical_url
    result_canonical_url = _strip_url_query_and_fragment(metadata_url) or canonical_url
    content = metadata.get("og:description") or metadata.get("description")

    return _build_extraction_result(
        post_id=post_id,
        input_url=input_url,
        canonical_url=result_canonical_url,
        author={
            "name": author_name,
            "profile_url": None,
        },
        content=content,
        publish_time=None,
        publish_time_text=None,
        like_count=None,
        comment_count=None,
        share_count=None,
        extraction_source="page_metadata",
        metadata_error="Full Facebook post DOM was unavailable.",
    )


def _extract_author(article: Locator) -> dict[str, str | None]:
    links = article.locator('a[role="link"], a[href]')
    for index in range(links.count()):
        link = links.nth(index)
        text = _safe_inner_text(link)
        href = link.get_attribute("href")
        if not text or not href:
            continue
        if _is_facebook_profile_url(href):
            return {
                "name": text,
                "profile_url": _normalize_facebook_profile_url(href),
            }

    return {
        "name": None,
        "profile_url": None,
    }


def _extract_content(article: Locator) -> str | None:
    _click_see_more_in_message(article)
    for selector in MESSAGE_SELECTORS:
        message = article.locator(selector).first
        if message.count() == 0:
            continue
        text = _safe_inner_text(message, preserve_lines=True)
        if text:
            return text
    return _extract_fallback_content(article)


def _extract_fallback_content(article: Locator) -> str | None:
    blocks = article.locator('div[dir="auto"], span[dir="auto"]')
    best_text = None
    for index in range(_safe_count(blocks)):
        text = _safe_inner_text(blocks.nth(index), preserve_lines=True)
        if not text or not _looks_like_post_content(text):
            continue
        if best_text is None or len(text) > len(best_text):
            best_text = text
    return best_text


def _looks_like_post_content(text: str) -> bool:
    normalized = " ".join(text.split()).lower()
    if len(normalized) < 20:
        return False
    blocked_exact = {
        "like",
        "comment",
        "share",
        "follow",
        "send",
        "see more",
        "log in",
        "sign up",
        "点赞",
        "评论",
        "分享",
    }
    if normalized in blocked_exact:
        return False
    blocked_phrases = (
        "all reactions:",
        "active",
        "view more comments",
        "most relevant",
        "write a comment",
        "see more on facebook",
        "log in to comment",
    )
    return not any(phrase in normalized for phrase in blocked_phrases)


def _click_see_more_in_message(article: Locator) -> None:
    for selector in MESSAGE_SELECTORS:
        container = article.locator(selector).first
        if container.count() == 0:
            continue
        buttons = container.locator('div[role="button"], span[role="button"], button')
        for index in range(buttons.count()):
            button = buttons.nth(index)
            text = _safe_inner_text(button)
            if text and text.lower() in {"see more", "查看更多"}:
                try:
                    button.click(timeout=1_000)
                except PlaywrightError:
                    return
                return


def _extract_publish_time(article: Locator) -> tuple[str | None, str | None]:
    time_element = article.locator("time").first
    if time_element.count() > 0:
        for attr_name in ("datetime", "title", "aria-label"):
            value = time_element.get_attribute(attr_name)
            if value:
                return value.strip(), None
        text = _safe_inner_text(time_element)
        if text:
            return None, text

    time_links = article.locator('a[href*="/posts/"], a[href*="story_fbid"], a[href*="permalink.php"]')
    for index in range(time_links.count()):
        link = time_links.nth(index)
        label = link.get_attribute("aria-label")
        if label and _looks_like_time_text(label):
            return None, label.strip()
        text = _safe_inner_text(link)
        if text and _looks_like_time_text(text):
            return None, text

    return None, None


def _extract_metrics(article: Locator) -> tuple[int | None, int | None, int | None]:
    text = _safe_inner_text(article) or ""
    like_count = _parse_metric_near_words(text, ("reaction", "reactions", "like", "likes"))
    comment_count = _parse_metric_near_words(text, ("comment", "comments"))
    share_count = _parse_metric_near_words(text, ("share", "shares"))
    return like_count, comment_count, share_count


def _parse_metric_near_words(text: str, words: tuple[str, ...]) -> int | None:
    for line in text.splitlines():
        normalized = line.lower()
        if any(word in normalized for word in words):
            parsed = parse_metric_count(line)
            if parsed is not None:
                return parsed
    return None


def _page_metadata(page: Page) -> dict[str, str]:
    metadata: dict[str, str] = {}
    selectors = {
        "og:title": 'meta[property="og:title"]',
        "og:description": 'meta[property="og:description"]',
        "og:url": 'meta[property="og:url"]',
        "description": 'meta[name="description"]',
        "canonical": 'link[rel="canonical"]',
    }
    for name, selector in selectors.items():
        locator = page.locator(selector).first
        if locator.count() == 0:
            continue
        attr_name = "href" if name == "canonical" else "content"
        value = locator.get_attribute(attr_name)
        if value:
            metadata[name] = value.strip()
    return metadata


def _metadata_has_post_specific_content(metadata: dict[str, str]) -> bool:
    content = metadata.get("og:description") or metadata.get("description") or ""
    title = metadata.get("og:title") or ""
    combined = f"{title} {content}".strip().lower()
    if not combined:
        return False
    generic_markers = (
        "facebook helps you connect",
        "log in or sign up",
        "create an account or log into facebook",
        "see posts, photos and more on facebook",
    )
    return not any(marker in combined for marker in generic_markers)


def _metadata_author_name(metadata: dict[str, str]) -> str | None:
    title = metadata.get("og:title") or ""
    if " on facebook" in title.lower():
        return title.split(" on Facebook", 1)[0].strip() or None
    if title and "facebook" not in title.lower():
        return title.strip()
    return None


def _raise_if_unavailable(page: Page) -> None:
    if _unavailable_message_detected(page):
        diagnostics = _collect_failure_diagnostics(page, "unknown", None)
        raise FacebookPostUnavailableError(
            f"Facebook reports that the post or page is unavailable. "
            f"Diagnostics: {_diagnostics_summary(diagnostics)}",
            diagnostics=diagnostics,
        )


def _collect_failure_diagnostics(
    page: Page,
    post_id: str,
    input_url: str | None,
    field_locator_presence: dict[str, bool] | None = None,
) -> dict[str, Any]:
    DEBUG_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    matched_container_count = _matched_semantic_container_count(page, post_id)
    diagnostics: dict[str, Any] = {
        "input_url": input_url,
        "final_url": _safe_page_url(page),
        "page_title": _safe_page_title(page),
        "target_post_id": post_id,
        "article_count": _safe_count(page.locator("article")),
        "role_article_count": _safe_count(page.locator(ROLE_ARTICLE_SELECTOR)),
        "dialog_count": _safe_count(page.locator('[role="dialog"]')),
        "target_link_count": _safe_count(page.locator(f'a[href*="{post_id}"]')),
        "target_id_link_count": _safe_count(page.locator(f'a[href*="{post_id}"]')),
        "story_fbid_link_count": _safe_count(page.locator(f'a[href*="story_fbid={post_id}"]')),
        "posts_path_link_count": _safe_count(page.locator(f'a[href*="/posts/{post_id}"]')),
        "matched_article_count": _safe_count(page.locator(ROLE_ARTICLE_SELECTOR).filter(has=_target_post_link_locator(page, post_id))),
        "matched_container_count": matched_container_count,
        "message_locator_count": _safe_count(page.locator(", ".join(MESSAGE_SELECTORS))),
        "time_candidate_count": _safe_count(page.locator("time, a[aria-label]")),
        "time_locator_count": _safe_count(page.locator("time, a[aria-label]")),
        "author_candidate_count": _safe_count(page.locator('a[role="link"], a[href]')),
        "reaction_candidate_count": _safe_count(page.locator(_metric_candidate_selector(("reaction", "like")))),
        "comment_candidate_count": _safe_count(page.locator(_metric_candidate_selector(("comment",)))),
        "share_candidate_count": _safe_count(page.locator(_metric_candidate_selector(("share",)))),
        "container_match_strategy": _container_match_strategy(page, post_id, matched_container_count),
        "login_wall_detected": _login_wall_detected(page),
        "unavailable_message_detected": _unavailable_message_detected(page),
        "metadata_available": _metadata_has_post_specific_content(_page_metadata(page)),
        "field_locator_presence": field_locator_presence
        or {
            "author": False,
            "content": False,
            "publish_time": False,
            "like_metric": False,
            "comment_metric": False,
            "share_metric": False,
        },
    }

    try:
        page.screenshot(path=str(FAILURE_SCREENSHOT_PATH), full_page=True)
    except PlaywrightError as exc:
        diagnostics["screenshot_error"] = str(exc)

    try:
        FAILURE_HTML_PATH.write_text(page.content(), encoding="utf-8")
    except (OSError, PlaywrightError) as exc:
        diagnostics["html_error"] = str(exc)

    try:
        FAILURE_DIAGNOSTICS_PATH.write_text(
            json.dumps(diagnostics, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        diagnostics["diagnostics_json_error"] = str(exc)

    return diagnostics


def _diagnostics_summary(diagnostics: dict[str, Any]) -> str:
    return (
        f"final_url={diagnostics['final_url']!r}; "
        f"title={diagnostics['page_title']!r}; "
        f"role_article_count={diagnostics['role_article_count']}; "
        f"target_link_count={diagnostics['target_link_count']}; "
        f"matched_container_count={diagnostics['matched_container_count']}; "
        f"container_match_strategy={diagnostics['container_match_strategy']}; "
        f"metadata_available={diagnostics['metadata_available']}; "
        f"login_wall_detected={diagnostics['login_wall_detected']}; "
        f"unavailable_message_detected={diagnostics['unavailable_message_detected']}"
    )


def _field_locator_presence(article: Locator) -> dict[str, bool]:
    return {
        "author": _safe_count(article.locator('a[role="link"], a[href]')) > 0,
        "content": any(_safe_count(article.locator(selector)) > 0 for selector in MESSAGE_SELECTORS),
        "publish_time": _safe_count(article.locator("time, a[aria-label]")) > 0,
        "like_metric": _safe_count(article.locator(_metric_candidate_selector(("reaction", "like")))) > 0,
        "comment_metric": _safe_count(article.locator(_metric_candidate_selector(("comment",)))) > 0,
        "share_metric": _safe_count(article.locator(_metric_candidate_selector(("share",)))) > 0,
    }


def _has_post_semantics(field_presence: dict[str, bool]) -> bool:
    return any(field_presence.values())


def _has_strong_post_semantics(field_presence: dict[str, bool]) -> bool:
    return _semantic_score(field_presence) >= 2


def _semantic_score(field_presence: dict[str, bool]) -> int:
    return sum(1 for present in field_presence.values() if present)


def _metric_candidate_selector(words: tuple[str, ...]) -> str:
    selectors: list[str] = []
    for word in words:
        selectors.extend(
            (
                f'[aria-label*="{word}" i]',
                f'[aria-label*="{word.title()}" i]',
            )
        )
    return ", ".join(selectors)


def _matched_semantic_container_count(page: Page, post_id: str) -> int:
    total = 0
    target_links = _target_post_link_locator(page, post_id)
    for selector in TARGET_CONTAINER_SELECTORS:
        containers = page.locator(selector).filter(has=target_links)
        for index in range(_safe_count(containers)):
            if _has_post_semantics(_field_locator_presence(containers.nth(index))):
                total += 1
    return total


def _container_match_strategy(page: Page, post_id: str, matched_container_count: int) -> str | None:
    if matched_container_count > 0:
        return "target_id_semantic_container"
    if _is_single_post_permalink(page, post_id) and _single_semantic_post_container(page) is not None:
        return "single_semantic_post_container"
    if _safe_count(_target_post_link_locator(page, post_id)) > 0:
        return "target_link_without_semantic_container"
    return None


def _has_any_core_field(result: dict[str, Any]) -> bool:
    return bool(
        result["author"]["name"]
        or result["author"]["profile_url"]
        or result["content"]
        or result["publish_time"]
        or result.get("publish_time_text")
    )


def _is_single_post_permalink(page: Page, post_id: str) -> bool:
    current_url = _safe_page_url(page) or ""
    return post_id in current_url and any(marker in current_url for marker in ("/posts/", "story_fbid=", "permalink.php"))


def _is_facebook_profile_url(href: str) -> bool:
    parsed = urlparse(href)
    host = (parsed.hostname or "").lower()
    if host not in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        return False
    first_segment = parsed.path.strip("/").split("/", 1)[0]
    if not first_segment or first_segment in {"permalink.php", "story.php", "posts", "groups", "reel", "watch"}:
        return False
    return True


def _normalize_facebook_profile_url(href: str) -> str:
    parsed = urlparse(href)
    return urlunparse(("https", "www.facebook.com", parsed.path.rstrip("/"), "", "", ""))


def _strip_url_query_and_fragment(href: str) -> str | None:
    parsed = urlparse(href)
    if not parsed.scheme or not parsed.netloc:
        return None
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def _looks_like_time_text(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "min",
        "hr",
        "hour",
        "yesterday",
        "202",
        "am",
        "pm",
        "jan",
        "feb",
        "mar",
        "apr",
        "may",
        "jun",
        "jul",
        "aug",
        "sep",
        "oct",
        "nov",
        "dec",
        "昨天",
    )
    return any(marker in lowered for marker in markers)


def _login_wall_detected(page: Page) -> bool:
    normalized = _normalized_body_text(page)
    markers = (
        "see more on facebook",
        "you must log in",
        "log in to continue",
        "登录",
        "创建新账户",
    )
    return any(marker in normalized for marker in markers)


def _unavailable_message_detected(page: Page) -> bool:
    normalized = _normalized_body_text(page)
    markers = (
        "this content isn't available",
        "this page isn't available",
        "this content is no longer available",
        "此内容目前无法显示",
        "链接可能已失效",
    )
    return any(marker in normalized for marker in markers)


def _normalized_body_text(page: Page) -> str:
    try:
        body_text = page.locator("body").inner_text(timeout=1_000)
    except PlaywrightError:
        return ""
    return " ".join(body_text.split()).lower()


def _safe_count(locator: Locator) -> int:
    try:
        return locator.count()
    except PlaywrightError:
        return -1


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


def _safe_inner_text(locator: Locator, preserve_lines: bool = False) -> str | None:
    try:
        text = locator.inner_text()
    except PlaywrightError:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    if preserve_lines:
        return "\n".join(line.rstrip() for line in stripped.splitlines()).strip()
    return " ".join(stripped.split())


def _close_quietly(resource: Any) -> None:
    if resource is None:
        return
    try:
        resource.close()
    except PlaywrightError:
        pass
