from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from src.filters.post_url import classify_post_url
from src.utils.metrics import parse_metric_count

TWEET_ARTICLE_SELECTOR = 'article[data-testid="tweet"]'
DEBUG_OUTPUT_DIR = Path("output") / "debug"
FAILURE_SCREENSHOT_PATH = DEBUG_OUTPUT_DIR / "x_post_failure.png"
FAILURE_HTML_PATH = DEBUG_OUTPUT_DIR / "x_post_failure.html"
FAILURE_DIAGNOSTICS_PATH = DEBUG_OUTPUT_DIR / "x_post_diagnostics.json"
METRIC_TEST_IDS = {
    "reply_count": "reply",
    "repost_count": "retweet",
    "like_count": "like",
}


class XPostExtractionError(RuntimeError):
    """Base exception for X post extraction failures."""

    def __init__(self, message: str, diagnostics: dict[str, Any] | None = None) -> None:
        self.diagnostics = diagnostics
        super().__init__(message)


class XPostPageOpenError(XPostExtractionError):
    """Raised when the X post page cannot be opened."""


class XPostLoadTimeoutError(XPostExtractionError):
    """Raised when the X post page does not load in time."""


class XPostNotFoundError(XPostExtractionError):
    """Raised when no tweet article is found."""


class XPostArticleMismatchError(XPostExtractionError):
    """Raised when tweet articles exist but none match the target post ID."""


class XPostUnavailableError(XPostExtractionError):
    """Raised when X reports the post or content is unavailable."""


class XPostLoginRequiredError(XPostExtractionError):
    """Raised when X requires login before the public post can be read."""


def extract_x_post(
    url: str,
    headless: bool = False,
    timeout_ms: int = 30_000,
    storage_state_path: str | Path | None = None,
) -> dict[str, Any]:
    """Open a single X post page and extract the target main post."""
    classified_url = classify_post_url(url)
    if classified_url is None:
        raise ValueError("url must be a supported X post URL.")
    if classified_url["platform"] != "x":
        raise ValueError("url must be an X post URL, not another platform.")

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
        article = _find_target_article(page, post_id, timeout_ms)
        # X often renders visible metric text shortly after the article appears.
        page.wait_for_timeout(300)
        return _extract_article_data(page, article, url, canonical_url, post_id)
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
        raise ValueError(f"X storage state file does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"X storage state path is not a file: {path}")
    return path


def _open_post_page(page: Page, canonical_url: str, timeout_ms: int) -> None:
    try:
        page.goto(canonical_url, wait_until="domcontentloaded", timeout=timeout_ms)
    except PlaywrightTimeoutError as exc:
        raise XPostLoadTimeoutError(f"X post page load timed out: {exc}") from exc
    except PlaywrightError as exc:
        raise XPostPageOpenError(f"X post page open failed: {exc}") from exc


def _find_target_article(page: Page, post_id: str, timeout_ms: int) -> Locator:
    target_link = page.locator(f'a[href*="/status/{post_id}"]')

    try:
        page.wait_for_function(
            """
            (postId) => {
                return Boolean(document.querySelector('article'));
            }
            """,
            arg=post_id,
            timeout=timeout_ms,
        )
    except PlaywrightTimeoutError as exc:
        diagnostics = _collect_failure_diagnostics(page, post_id)
        _raise_if_diagnostics_show_blocked(diagnostics)
        if diagnostics["target_status_link_count"] > 0 and diagnostics["article_count"] == 0:
            message = (
                f"Found link(s) containing /status/{post_id}, but the page has no article "
                "element to use as the target post container."
            )
        else:
            message = "No article element was found on the X post page."
        raise XPostNotFoundError(
            f"{message} Diagnostics: {_diagnostics_summary(diagnostics)}",
            diagnostics=diagnostics,
        ) from exc
    except PlaywrightError as exc:
        diagnostics = _collect_failure_diagnostics(page, post_id)
        _raise_if_diagnostics_show_blocked(diagnostics)
        raise XPostNotFoundError(
            f"Unable to locate article elements: {exc}. "
            f"Diagnostics: {_diagnostics_summary(diagnostics)}",
            diagnostics=diagnostics,
        ) from exc

    tweet_article_matches = page.locator(TWEET_ARTICLE_SELECTOR).filter(has=target_link)
    article_matches = page.locator("article").filter(has=target_link)
    target_article = _select_matched_article(tweet_article_matches, post_id)
    if target_article is not None:
        return target_article

    target_article = _select_matched_article(article_matches, post_id)
    if target_article is not None:
        return target_article

    diagnostics = _collect_failure_diagnostics(page, post_id)
    _raise_if_diagnostics_show_blocked(diagnostics)
    if diagnostics["target_status_link_count"] > 0:
        message = (
            f"Found link(s) containing /status/{post_id}, but no containing article matched "
            "the target post ID."
        )
    else:
        message = f"No link containing /status/{post_id} was found in any article."
    raise XPostArticleMismatchError(
        f"{message} Diagnostics: {_diagnostics_summary(diagnostics)}",
        diagnostics=diagnostics,
    )


def _raise_if_diagnostics_show_blocked(diagnostics: dict[str, Any]) -> None:
    if diagnostics["login_wall_detected"]:
        raise XPostLoginRequiredError(
            f"X requires login before this post can be read. "
            f"Diagnostics: {_diagnostics_summary(diagnostics)}",
            diagnostics=diagnostics,
        )
    if diagnostics["unavailable_message_detected"]:
        raise XPostUnavailableError(
            f"X reports that the post or account is unavailable. "
            f"Diagnostics: {_diagnostics_summary(diagnostics)}",
            diagnostics=diagnostics,
        )


def _select_matched_article(matches: Locator, post_id: str) -> Locator | None:
    count = matches.count()
    if count == 0:
        return None
    if count == 1:
        return matches.first

    for index in range(count):
        candidate = matches.nth(index)
        time_links = candidate.locator(f'a[href*="/status/{post_id}"]:has(time)')
        if time_links.count() > 0:
            return candidate
    return None


def _extract_article_data(
    page: Page,
    article: Locator,
    input_url: str,
    canonical_url: str,
    post_id: str,
) -> dict[str, Any]:
    author = _extract_author(article)
    content = _extract_content(article)
    publish_time = _extract_publish_time(article)
    reply_count = _extract_metric(article, "reply_count")
    repost_count = _extract_metric(article, "repost_count")
    like_count = _extract_metric(article, "like_count")
    view_count = _extract_view_count(article)
    field_locator_presence = _field_locator_presence(article)

    try:
        return _build_extraction_result(
            post_id=post_id,
            input_url=input_url,
            canonical_url=canonical_url,
            author=author,
            content=content,
            publish_time=publish_time,
            reply_count=reply_count,
            repost_count=repost_count,
            like_count=like_count,
            view_count=view_count,
        )
    except XPostExtractionError as exc:
        diagnostics = _collect_failure_diagnostics(
            page,
            post_id,
            input_url=input_url,
            field_locator_presence=field_locator_presence,
        )
        raise XPostExtractionError(
            f"Target post matched, but no core post fields could be extracted. "
            f"Diagnostics: {_diagnostics_summary(diagnostics)}",
            diagnostics=diagnostics,
        ) from exc


def _build_extraction_result(
    post_id: str,
    input_url: str,
    canonical_url: str,
    author: dict[str, str | None],
    content: str | None,
    publish_time: str | None,
    reply_count: int | None,
    repost_count: int | None,
    like_count: int | None,
    view_count: int | None,
) -> dict[str, Any]:
    has_author = bool(author["name"] or author["handle"])
    has_content = bool(content)
    has_publish_time = bool(publish_time)
    if not has_author and not has_content and not has_publish_time:
        raise XPostExtractionError("Target post matched, but no core post fields could be extracted.")

    collection_status = "success" if has_author and has_content and has_publish_time else "partial"

    return {
        "platform": "x",
        "post_id": post_id,
        "input_url": input_url,
        "canonical_url": canonical_url,
        "author": author,
        "content": content,
        "publish_time": publish_time,
        "reply_count": reply_count,
        "comment_count": reply_count,
        "repost_count": repost_count,
        "like_count": like_count,
        "view_count": view_count,
        "share_count": None,
        "comments": [],
        "collected_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "collection_status": collection_status,
        "error": None,
    }


def _extract_author(article: Locator) -> dict[str, str | None]:
    user_name = article.locator('[data-testid="User-Name"]').first
    handle = _first_text_matching(user_name.locator("span"), r"^@[\w_]+$") if user_name.count() else None
    name = None

    if user_name.count():
        spans = user_name.locator("span")
        for index in range(spans.count()):
            text = _safe_inner_text(spans.nth(index))
            if not text or text.startswith("@") or text == "\u00b7":
                continue
            name = text
            break

    return {
        "name": name,
        "handle": handle,
    }


def _extract_content(article: Locator) -> str | None:
    tweet_text = article.locator('[data-testid="tweetText"]').first
    if tweet_text.count() == 0:
        return None
    text = _safe_inner_text(tweet_text, preserve_lines=True)
    return text if text else None


def _extract_publish_time(article: Locator) -> str | None:
    time_element = article.locator("time").first
    if time_element.count() == 0:
        return None
    value = time_element.get_attribute("datetime")
    return value.strip() if value else None


def _extract_metric(article: Locator, field_name: str) -> int | None:
    test_id = METRIC_TEST_IDS[field_name]
    metric = article.locator(f'[data-testid="{test_id}"]').first
    if metric.count() == 0:
        return None

    for attr_name in ("aria-label", "title"):
        parsed = parse_metric_count(metric.get_attribute(attr_name))
        if parsed is not None:
            return parsed

    return parse_metric_count(_safe_inner_text(metric))


def _extract_view_count(article: Locator) -> int | None:
    candidates = article.locator('a[href$="/analytics"], a[aria-label*="View"], a[aria-label*="view"]')
    for index in range(candidates.count()):
        candidate = candidates.nth(index)
        for attr_name in ("aria-label", "title"):
            parsed = parse_metric_count(candidate.get_attribute(attr_name))
            if parsed is not None:
                return parsed
        parsed = parse_metric_count(_safe_inner_text(candidate))
        if parsed is not None:
            return parsed
    return None


def _raise_if_blocked_or_unavailable(page: Page) -> None:
    try:
        body_text = page.locator("body").inner_text(timeout=1_000)
    except PlaywrightError:
        return

    normalized = " ".join(body_text.split()).lower()
    if not normalized:
        return

    login_markers = (
        "log in to see this post",
        "sign in to view this post",
        "sign in to see this post",
        "log in to see more",
        "\u767b\u5f55 x",
        "\u767b\u5165 x",
    )
    unavailable_markers = (
        "this post is unavailable",
        "this post was deleted",
        "this account has been suspended",
        "account suspended",
        "something went wrong",
        "\u6b64\u5e16\u5b50\u4e0d\u53ef\u7528",
        "\u8d26\u53f7\u5df2\u88ab\u51bb\u7ed3",
        "\u5167\u5bb9\u7121\u6cd5\u53d6\u5f97",
    )

    if any(marker in normalized for marker in login_markers):
        raise XPostLoginRequiredError("X requires login before this post can be read.")
    if any(marker in normalized for marker in unavailable_markers):
        raise XPostUnavailableError("X reports that the post or account is unavailable.")


def _collect_failure_diagnostics(
    page: Page,
    post_id: str,
    input_url: str | None = None,
    field_locator_presence: dict[str, bool] | None = None,
) -> dict[str, Any]:
    DEBUG_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    diagnostics: dict[str, Any] = {
        "input_url": input_url,
        "final_url": _safe_page_url(page),
        "page_title": _safe_page_title(page),
        "target_post_id": post_id,
        "article_count": _safe_count(page.locator("article")),
        "tweet_article_count": _safe_count(page.locator(TWEET_ARTICLE_SELECTOR)),
        "tweet_text_count": _safe_count(page.locator('[data-testid="tweetText"]')),
        "time_count": _safe_count(page.locator("time[datetime]")),
        "target_status_link_count": _safe_count(page.locator(f'a[href*="/status/{post_id}"]')),
        "matched_tweet_article_count": _safe_count(
            page.locator(TWEET_ARTICLE_SELECTOR).filter(
                has=page.locator(f'a[href*="/status/{post_id}"]')
            )
        ),
        "matched_article_count": _safe_count(
            page.locator("article").filter(has=page.locator(f'a[href*="/status/{post_id}"]'))
        ),
        "login_wall_detected": _login_wall_detected(page),
        "unavailable_message_detected": _unavailable_message_detected(page),
        "field_locator_presence": field_locator_presence or {},
        "screenshot_path": str(FAILURE_SCREENSHOT_PATH),
        "html_path": str(FAILURE_HTML_PATH),
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
        f"article_count={diagnostics['article_count']}; "
        f"tweet_article_count={diagnostics['tweet_article_count']}; "
        f"tweet_text_count={diagnostics['tweet_text_count']}; "
        f"time_count={diagnostics['time_count']}; "
        f"target_status_link_count={diagnostics['target_status_link_count']}; "
        f"matched_article_count={diagnostics['matched_article_count']}; "
        f"login_wall_detected={diagnostics['login_wall_detected']}; "
        f"unavailable_message_detected={diagnostics['unavailable_message_detected']}; "
        f"screenshot={diagnostics['screenshot_path']}; "
        f"html={diagnostics['html_path']}; "
        f"diagnostics={FAILURE_DIAGNOSTICS_PATH}"
    )


def _login_wall_detected(page: Page) -> bool:
    normalized = _normalized_body_text(page)
    return any(marker in normalized for marker in ("log in", "sign up", "sign in"))


def _unavailable_message_detected(page: Page) -> bool:
    normalized = _normalized_body_text(page)
    return any(
        marker in normalized
        for marker in (
            "this post is unavailable",
            "something went wrong",
            "account suspended",
            "this account has been suspended",
        )
    )


def _normalized_body_text(page: Page) -> str:
    try:
        body_text = page.locator("body").inner_text(timeout=1_000)
    except PlaywrightError:
        return ""
    return " ".join(body_text.split()).lower()


def _field_locator_presence(article: Locator) -> dict[str, bool]:
    return {
        "author_block": _safe_count(article.locator('[data-testid="User-Name"]')) > 0,
        "author_handle": _safe_count(article.locator('[data-testid="User-Name"] span')) > 0,
        "tweet_text": _safe_count(article.locator('[data-testid="tweetText"]')) > 0,
        "time_datetime": _safe_count(article.locator("time[datetime]")) > 0,
        "reply_metric": _safe_count(article.locator('[data-testid="reply"]')) > 0,
        "repost_metric": _safe_count(article.locator('[data-testid="retweet"]')) > 0,
        "like_metric": _safe_count(article.locator('[data-testid="like"]')) > 0,
        "view_metric": _safe_count(article.locator('a[href$="/analytics"], a[aria-label*="View"], a[aria-label*="view"]')) > 0,
    }


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


def _first_text_matching(locator: Locator, pattern: str) -> str | None:
    import re

    compiled = re.compile(pattern)
    for index in range(locator.count()):
        text = _safe_inner_text(locator.nth(index))
        if text and compiled.match(text):
            return text
    return None


def _close_quietly(resource: Any) -> None:
    if resource is None:
        return
    try:
        resource.close()
    except PlaywrightError:
        pass
