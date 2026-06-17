from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from src.debug.facebook_debug_bundle import (
    facebook_debug_bundle_dir,
    save_facebook_debug_bundle,
    should_save_facebook_debug_bundle,
    start_facebook_trace,
    stop_facebook_trace,
)
from src.filters.post_url import classify_post_url
from src.utils.facebook_time import is_relative_time_text
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


@dataclass(frozen=True)
class TargetContainerSelection:
    locator: Locator
    candidate_index: int
    metrics: dict[str, int | bool]
    strategy: str
    message_index: int | None = None
    ancestor_depth: int | None = None
    score: int | None = None
    score_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActionCountExtraction:
    button_found: bool
    raw_text: str | None
    count: int | None
    source: str | None = None
    rejection_reason: str | None = None
    count_state: str = "unknown"


def extract_facebook_post(
    url: str,
    headless: bool = False,
    timeout_ms: int = 30_000,
    storage_state_path: str | Path | None = None,
    save_debug_bundle: bool = False,
    extract_comments: bool = False,
    max_comments: int = 20,
    expand_comments: bool = True,
    max_comment_expand_actions: int = 3,
    no_progress_round_limit: int = 3,
) -> dict[str, Any]:
    """Open a public Facebook post page and extract the target main post."""
    classified_url = classify_post_url(url)
    if classified_url is None:
        raise ValueError("url must be a supported Facebook post URL.")
    if classified_url["platform"] != "facebook":
        raise ValueError("url must be a Facebook post URL, not another platform.")

    normalized_storage_state_path = _validate_storage_state_path(storage_state_path)
    post_id = classified_url["post_id"]
    playwright = None
    browser = None
    context = None
    page = None
    trace_started = False

    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=headless)
        if normalized_storage_state_path is None:
            context = browser.new_context()
        else:
            context = browser.new_context(storage_state=str(normalized_storage_state_path))
        trace_error = start_facebook_trace(context)
        trace_started = trace_error is None
        page = context.new_page()

        payload = extract_facebook_post_from_page(
            page=page,
            url=url,
            timeout_ms=timeout_ms,
            authenticated=normalized_storage_state_path is not None,
            extract_comments=extract_comments,
            max_comments=max_comments,
            expand_comments=expand_comments,
            max_comment_expand_actions=max_comment_expand_actions,
            no_progress_round_limit=no_progress_round_limit,
        )
        should_save = should_save_facebook_debug_bundle(
            str(payload.get("collection_status")),
            force=save_debug_bundle,
        )
        bundle_dir = facebook_debug_bundle_dir(index=1, post_id=post_id)
        if trace_started:
            trace_error = stop_facebook_trace(context, bundle_dir / "trace.zip" if should_save else None)
            trace_started = False
        if should_save:
            payload.update(
                save_facebook_debug_bundle(
                    page,
                    bundle_dir,
                    diagnostics=_read_json_file(FAILURE_DIAGNOSTICS_PATH),
                    trace_error=trace_error,
                )
            )
        return payload
    except Exception as exc:
        if page is not None and context is not None:
            bundle_dir = facebook_debug_bundle_dir(index=1, post_id=post_id)
            if trace_started:
                trace_error = stop_facebook_trace(context, bundle_dir / "trace.zip")
                trace_started = False
            else:
                trace_error = None
            diagnostics = exc.diagnostics if isinstance(exc, FacebookPostExtractionError) else None
            if diagnostics is None:
                diagnostics = _read_json_file(FAILURE_DIAGNOSTICS_PATH)
            save_facebook_debug_bundle(page, bundle_dir, diagnostics=diagnostics, trace_error=trace_error)
        raise
    finally:
        if trace_started and context is not None:
            stop_facebook_trace(context)
        _close_quietly(page)
        _close_quietly(context)
        _close_quietly(browser)
        if playwright is not None:
            playwright.stop()


def extract_facebook_post_from_page(
    page: Page,
    url: str,
    timeout_ms: int = 30_000,
    authenticated: bool = False,
    extract_comments: bool = False,
    max_comments: int = 20,
    expand_comments: bool = True,
    max_comment_expand_actions: int = 3,
    no_progress_round_limit: int = 3,
) -> dict[str, Any]:
    """Extract one Facebook post using an existing Playwright page."""
    classified_url = classify_post_url(url)
    if classified_url is None:
        raise ValueError("url must be a supported Facebook post URL.")
    if classified_url["platform"] != "facebook":
        raise ValueError("url must be a Facebook post URL, not another platform.")

    post_id = classified_url["post_id"]
    canonical_url = classified_url["canonical_url"]
    context_type = classified_url.get("context_type")

    _open_post_page(page, canonical_url, timeout_ms)
    _wait_for_redirects_to_settle(page, timeout_ms)
    if authenticated and _is_login_url(page.url):
        raise FacebookLoginRequiredError(
            "The supplied Facebook authentication state is missing or expired. "
            "Re-run scripts/create_facebook_storage_state.py."
        )
    _wait_for_page_body(page, timeout_ms)
    _raise_if_unavailable(page)

    target_selection = _find_target_article_result(page, post_id)
    if target_selection is None:
        metadata_result = _build_metadata_result(page, url, canonical_url, post_id, context_type=context_type)
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

    target_article = target_selection.locator
    result = _extract_article_data(
        target_article,
        url,
        canonical_url,
        post_id,
        context_type=context_type,
        extract_comments=extract_comments,
        max_comments=max_comments,
        expand_comments=expand_comments,
        max_comment_expand_actions=max_comment_expand_actions,
        no_progress_round_limit=no_progress_round_limit,
    )
    if result["publish_time"] is None and result.get("publish_time_text") is None:
        publish_time, publish_time_text = _extract_publish_time(target_article, post_id)
        result = _apply_publish_time_to_result(result, publish_time, publish_time_text)
    diagnostics = _collect_failure_diagnostics(
        page,
        post_id,
        url,
        field_locator_presence=_field_locator_presence(target_article),
        target_container=target_article,
        selected_container_index=target_selection.candidate_index,
        selected_container_metrics=target_selection.metrics,
        selected_container_strategy=target_selection.strategy,
        selected_message_index=target_selection.message_index,
        selected_ancestor_depth=target_selection.ancestor_depth,
        selected_container_score=target_selection.score,
        selected_container_reasons=list(target_selection.score_reasons),
        selected_publish_time=result.get("publish_time"),
        selected_publish_time_text=result.get("publish_time_text"),
    )
    if result["publish_time"] is None and result.get("publish_time_text") is None:
        result = _apply_publish_time_to_result(
            result,
            _optional_string(diagnostics.get("selected_publish_time")),
            _optional_string(diagnostics.get("selected_publish_time_text")),
        )
    if _has_any_core_field(result):
        return result

    metadata_result = _build_metadata_result(page, url, canonical_url, post_id, context_type=context_type)
    if metadata_result is not None:
        return metadata_result

    diagnostics = _collect_failure_diagnostics(
        page,
        post_id,
        url,
        field_locator_presence=_field_locator_presence(target_article),
        target_container=target_article,
        selected_container_index=target_selection.candidate_index,
        selected_container_metrics=target_selection.metrics,
        selected_container_strategy=target_selection.strategy,
        selected_message_index=target_selection.message_index,
        selected_ancestor_depth=target_selection.ancestor_depth,
        selected_container_score=target_selection.score,
        selected_container_reasons=list(target_selection.score_reasons),
    )
    raise FacebookPostExtractionError(
        f"Target Facebook post matched, but no core post fields could be extracted. "
        f"Diagnostics: {_diagnostics_summary(diagnostics)}",
        diagnostics=diagnostics,
    )


def _validate_storage_state_path(storage_state_path: str | Path | None) -> Path | None:
    if storage_state_path is None:
        return None

    path = Path(storage_state_path)
    if not path.exists():
        raise ValueError(f"Facebook storage state file does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"Facebook storage state path is not a file: {path}")
    return path


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


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
    selection = _find_target_article_result(page, post_id)
    return selection.locator if selection is not None else None


def _find_target_article_result(page: Page, post_id: str) -> TargetContainerSelection | None:
    title_context = _post_title_context(_safe_page_title(page))
    target_links = _target_post_link_locator(page, post_id)
    best_selection = None
    best_score: tuple[int, int, int, int, int, int] | None = None
    candidate_index = 0
    for selector in TARGET_CONTAINER_SELECTORS:
        containers = page.locator(selector).filter(has=target_links)
        for index in range(_safe_count(containers)):
            container = containers.nth(index)
            metrics = _container_metrics(container, post_id)
            if not _is_valid_target_container(metrics):
                candidate_index += 1
                continue
            score = _container_selection_score(metrics)
            if best_score is None or score > best_score:
                best_selection = TargetContainerSelection(
                    locator=container,
                    candidate_index=candidate_index,
                    metrics=metrics,
                    strategy="smallest_semantic_target_container",
                )
                best_score = score
            candidate_index += 1

    if best_selection is not None:
        return best_selection

    dialog_selection = _find_dialog_by_author_action_context(page, post_id, title_context)
    if dialog_selection is not None:
        return dialog_selection

    if _is_single_post_permalink(page, post_id):
        fallback = _single_semantic_post_container(page)
        if fallback is not None:
            return TargetContainerSelection(
                locator=fallback,
                candidate_index=0,
                metrics=_container_metrics(fallback, post_id),
                strategy="single_semantic_post_container",
            )

    title_selection = _find_article_by_title_context(page, post_id, title_context)
    if title_selection is not None:
        return title_selection

    message_selection = _find_message_ancestor_by_title_context(page, post_id, title_context)
    if message_selection is not None:
        return message_selection

    return None


def _target_post_link_locator(page: Page, post_id: str) -> Locator:
    return page.locator(_target_post_link_selector(post_id))


def _target_post_link_selector(post_id: str) -> str:
    not_comment = ':not([href*="comment_id="]):not([href*="comment_id%3D"]):not([href*="reply_comment_id="])'
    selectors = (
        f'a[href*="story_fbid={post_id}"]{not_comment}',
        f'a[href*="/posts/{post_id}"]{not_comment}',
        f'a[href*="permalink.php"][href*="{post_id}"]{not_comment}',
        f'a[href*="{post_id}"]{not_comment}',
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


def _find_dialog_by_author_action_context(
    page: Page,
    post_id: str,
    title_context: dict[str, str | None],
) -> TargetContainerSelection | None:
    dialogs = page.locator('[role="dialog"]')
    candidates: list[dict[str, Any]] = []
    for index in range(_safe_count(dialogs)):
        dialog = dialogs.nth(index)
        summary = _dialog_author_action_summary(dialog, index, title_context)
        if bool(summary["is_valid_candidate"]):
            candidates.append(summary)

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            int(item["container_score"]),
            float(item["title_overlap_score"]),
            -int(item["descendant_count"]),
        ),
        reverse=True,
    )
    best = candidates[0]

    selected_index = int(best["index"])
    selected = dialogs.nth(selected_index)
    return TargetContainerSelection(
        locator=selected,
        candidate_index=selected_index,
        metrics=_container_metrics(selected, post_id),
        strategy="dialog_author_action_context",
        score=int(best["container_score"]),
        score_reasons=tuple(str(reason) for reason in best.get("score_reasons", [])),
    )


def _dialog_author_action_summary(
    dialog: Locator,
    index: int,
    title_context: dict[str, str | None],
) -> dict[str, Any]:
    script = r"""
    (element) => {
      const text = (element.innerText || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
      const actionLabels = Array.from(element.querySelectorAll('[aria-label]'))
        .map((node) => node.getAttribute('aria-label') || '')
        .filter((label) => label.toLowerCase().includes('actions for this post by'));
      const headingTexts = Array.from(element.querySelectorAll('h1, h2, h3, [role="heading"]'))
        .map((node) => (node.innerText || '').replace(/\\s+/g, ' ').trim())
        .filter(Boolean);
      const messageSelector = '[data-ad-preview="message"], [data-ad-comet-preview="message"]';
      return {
        aria_label: element.getAttribute('aria-label') || '',
        text,
        action_labels: actionLabels,
        heading_texts: headingTexts,
        descendant_count: element.querySelectorAll('*').length,
        message_count: element.querySelectorAll(messageSelector).length,
        timestamp_candidate_count: element.querySelectorAll('time, a[aria-label], [aria-labelledby], [title]').length,
        reaction_marker_count: element.querySelectorAll('[data-ad-rendering-role="like_button"]').length,
        comment_marker_count: element.querySelectorAll('[data-ad-rendering-role="comment_button"]').length,
        share_marker_count: element.querySelectorAll('[data-ad-rendering-role="share_button"]').length,
      };
    }
    """
    defaults = {
        "aria_label": "",
        "text": "",
        "action_labels": [],
        "heading_texts": [],
        "descendant_count": 1_000_000,
        "message_count": 0,
        "timestamp_candidate_count": 0,
        "reaction_marker_count": 0,
        "comment_marker_count": 0,
        "share_marker_count": 0,
    }
    try:
        raw = dialog.evaluate(script)
    except PlaywrightError:
        raw = defaults
    if not isinstance(raw, dict):
        raw = defaults

    text = str(raw.get("text") or "")
    action_labels = [str(label) for label in raw.get("action_labels") or []]
    heading_texts = [str(label) for label in raw.get("heading_texts") or []]
    author_names = [
        name
        for name in (
            _author_name_from_post_label(str(raw.get("aria_label") or "")),
            *(_author_name_from_post_label(text) for text in heading_texts),
            *(_author_name_from_action_label(label) for label in action_labels),
        )
        if name
    ]
    title_overlap_score = _title_overlap_score(text, title_context.get("content_fragment"))
    marker_count = int(raw.get("reaction_marker_count") or 0) + int(raw.get("comment_marker_count") or 0) + int(raw.get("share_marker_count") or 0)
    timestamp_count = int(raw.get("timestamp_candidate_count") or 0)
    message_count = int(raw.get("message_count") or 0)
    descendant_count = int(raw.get("descendant_count") or 0)

    score = 0
    reasons: list[str] = []
    if author_names:
        score += 5
        reasons.append("dialog_or_action_author")
    if title_overlap_score >= 0.65:
        score += 5
        reasons.append("dialog_title_text_match")
    if message_count >= 1:
        score += 3
        reasons.append("message_present")
    if timestamp_count > 0:
        score += 2
        reasons.append("timestamp_present")
    if marker_count > 0:
        score += min(marker_count, 3)
        reasons.append("interaction_markers_present")
    if len(text) < 40:
        score -= 4
        reasons.append("too_short_for_main_post")
    if descendant_count > 3_000 or len(text) > 8_000:
        score -= 3
        reasons.append("too_broad_for_single_post")

    has_title_or_single_dialog_signal = title_overlap_score >= 0.65 or _safe_count(dialog.locator('[role="dialog"]')) <= 1
    is_valid = (
        bool(author_names)
        and has_title_or_single_dialog_signal
        and (message_count >= 1 or marker_count > 0)
        and timestamp_count > 0
        and score >= 9
        and descendant_count <= 3_000
    )
    return {
        "index": index,
        "text_preview": text[:240],
        "text_length": len(text),
        "author_candidates": author_names[:3],
        "title_overlap_score": title_overlap_score,
        "descendant_count": descendant_count,
        "container_score": score,
        "score_reasons": reasons,
        "is_valid_candidate": is_valid,
    }


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


def _container_metrics(container: Locator, post_id: str) -> dict[str, int | bool]:
    script = r"""
    (element, postId) => {
      const messageSelector = '[data-ad-preview="message"], [data-ad-comet-preview="message"]';
      const targetLinks = Array.from(element.querySelectorAll('a[href]')).filter((link) => {
        const href = link.getAttribute('href') || '';
        const isCommentPermalink = href.includes('comment_id=') ||
          href.includes('comment_id%3D') ||
          href.includes('reply_comment_id=');
        return !isCommentPermalink && (href.includes(postId) ||
          href.includes(`story_fbid=${postId}`) ||
          href.includes(`/posts/${postId}`));
      });
      const profileLinks = Array.from(element.querySelectorAll('a[href]')).filter((link) => {
        const href = link.getAttribute('href') || '';
        const text = (link.innerText || '').trim();
        const path = (() => {
          try { return new URL(href, 'https://www.facebook.com').pathname; }
          catch (_) { return href; }
        })();
        const isGroupUser = /\/groups\/[^/]+\/user\/[^/]+/.test(path);
        return text && (href.includes('facebook.com') || href.startsWith('/') || isGroupUser) &&
          !href.includes('story.php') &&
          !href.includes('permalink.php') &&
          !href.includes('/posts/') &&
          !href.includes('/watch/') &&
          !href.includes('/reel/');
      });
      let depth = 0;
      let current = element;
      while (current && current.parentElement) {
        depth += 1;
        current = current.parentElement;
      }
      return {
        targetLinkCount: targetLinks.length,
        messageCount: element.querySelectorAll(messageSelector).length,
        authorCount: profileLinks.length,
        roleArticleCount: element.querySelectorAll('[role="article"]').length,
        descendantCount: element.querySelectorAll('*').length,
        textLength: (element.innerText || '').length,
        depth,
        isDialog: element.getAttribute('role') === 'dialog',
      };
    }
    """
    defaults: dict[str, int | bool] = {
        "targetLinkCount": 0,
        "messageCount": 0,
        "authorCount": 0,
        "roleArticleCount": 0,
        "descendantCount": 1_000_000,
        "textLength": 1_000_000,
        "depth": 0,
        "isDialog": False,
    }
    try:
        value = container.evaluate(script, post_id)
    except PlaywrightError:
        return defaults
    if not isinstance(value, dict):
        return defaults
    return {**defaults, **value}


def _is_valid_target_container(metrics: dict[str, int | bool]) -> bool:
    if int(metrics["targetLinkCount"]) <= 0:
        return False
    if int(metrics["messageCount"]) > 1:
        return False
    if int(metrics["messageCount"]) == 1 and int(metrics["authorCount"]) >= 1:
        return True
    return int(metrics["messageCount"]) == 1


def _container_selection_score(metrics: dict[str, int | bool]) -> tuple[int, int, int, int, int, int]:
    return (
        1 if int(metrics["messageCount"]) == 1 else 0,
        1 if int(metrics["authorCount"]) >= 1 and int(metrics["messageCount"]) == 1 else 0,
        int(metrics["depth"]),
        0 if bool(metrics["isDialog"]) else 1,
        -int(metrics["descendantCount"]),
        -int(metrics["textLength"]),
    )


def _find_article_by_title_context(
    page: Page,
    post_id: str,
    title_context: dict[str, str | None],
) -> TargetContainerSelection | None:
    title_fragment = title_context.get("content_fragment")
    if not title_fragment:
        return None

    articles = page.locator(ROLE_ARTICLE_SELECTOR)
    summaries: list[dict[str, Any]] = []
    for index in range(_safe_count(articles)):
        article = articles.nth(index)
        summary = _article_diagnostic_summary(article, index, post_id, title_context)
        summaries.append(summary)

    if not summaries:
        return None

    sorted_summaries = sorted(summaries, key=lambda item: item["container_score"], reverse=True)
    best = sorted_summaries[0]
    second_score = sorted_summaries[1]["container_score"] if len(sorted_summaries) > 1 else 0
    best_score = int(best["container_score"])
    has_clear_title_match = float(best["title_overlap_score"]) >= 0.65
    has_clear_margin = best_score - int(second_score) >= 3

    if best_score < 8 or not has_clear_title_match or not has_clear_margin:
        return None

    selected_index = int(best["index"])
    selected = articles.nth(selected_index)
    return TargetContainerSelection(
        locator=selected,
        candidate_index=selected_index,
        metrics=_container_metrics(selected, post_id),
        strategy="title_text_match" if float(best["title_overlap_score"]) >= 0.9 else "article_score",
    )


def _find_message_ancestor_by_title_context(
    page: Page,
    post_id: str,
    title_context: dict[str, str | None],
) -> TargetContainerSelection | None:
    title_fragment = title_context.get("content_fragment")
    if not title_fragment:
        return None

    messages = _message_locator(page)
    message_summaries = _message_candidate_diagnostics(page, title_context)
    matching_messages = [
        summary
        for summary in message_summaries
        if float(summary["title_overlap_score"]) >= 0.65
        and bool(summary["contains_title_fragment"])
        and int(summary["text_length"]) >= 20
    ]
    if not matching_messages:
        return None

    matching_messages.sort(key=lambda item: (float(item["title_overlap_score"]), int(item["text_length"])), reverse=True)
    best_message = matching_messages[0]
    if len(matching_messages) > 1:
        second_message = matching_messages[1]
        if float(best_message["title_overlap_score"]) - float(second_message["title_overlap_score"]) < 0.2:
            return None

    message_index = int(best_message["index"])
    message = messages.nth(message_index)
    ancestor_summaries = _message_ancestor_diagnostics_for_message(message, message_index, title_context)
    valid_ancestors = [
        summary
        for summary in ancestor_summaries
        if bool(summary.get("is_valid_candidate"))
    ]
    if not valid_ancestors:
        return None

    valid_ancestors.sort(
        key=lambda item: (
            int(item["container_score"]),
            int(item["depth"]),
            -int(item["descendant_count"]),
            -int(item["text_length"]),
        ),
        reverse=True,
    )
    best_ancestor = valid_ancestors[0]

    selected_depth = int(best_ancestor["depth"])
    selected = message.locator("xpath=ancestor::*").nth(selected_depth - 1)
    metrics = _container_metrics(selected, post_id)
    return TargetContainerSelection(
        locator=selected,
        candidate_index=message_index,
        metrics=metrics,
        strategy="message_ancestor_title_match",
        message_index=message_index,
        ancestor_depth=selected_depth,
        score=int(best_ancestor["container_score"]),
        score_reasons=tuple(str(reason) for reason in best_ancestor.get("score_reasons", [])),
    )


def _post_title_context(page_title: str | None) -> dict[str, str | None]:
    if not page_title:
        return {"author": None, "content_fragment": None}

    title = re.sub(r"^\(\d+\)\s*", "", page_title).strip()
    title = re.sub(r"\s*\|\s*Facebook\s*$", "", title, flags=re.IGNORECASE).strip()
    author = None
    content_fragment = title
    if " - " in title:
        author, content_fragment = title.split(" - ", 1)
    content_fragment = re.sub(r"\.\.\.$", "", content_fragment).strip()
    content_fragment = re.sub(r"…$", "", content_fragment).strip()
    return {
        "author": author.strip() if author else None,
        "content_fragment": content_fragment or None,
    }


def _article_diagnostic_summary(
    article: Locator,
    index: int,
    post_id: str,
    title_context: dict[str, str | None],
) -> dict[str, Any]:
    script = """
    (element, postId) => {
      const text = (element.innerText || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
      const links = Array.from(element.querySelectorAll('a[href]'));
      const authorCandidates = links
        .map((link) => (link.innerText || '').replace(/\\s+/g, ' ').trim())
        .filter(Boolean)
        .slice(0, 5);
      const timestampCandidates = Array.from(element.querySelectorAll('time, a[aria-label], [aria-labelledby], [title]'))
        .map((node) => (
          node.getAttribute('aria-label') ||
          node.getAttribute('title') ||
          node.innerText ||
          ''
        ).replace(/\\s+/g, ' ').trim())
        .filter(Boolean)
        .slice(0, 5);
      const interactionMarkers = Array.from(element.querySelectorAll('[data-ad-rendering-role]'))
        .map((node) => node.getAttribute('data-ad-rendering-role'))
        .filter(Boolean);
      const matchingLinks = links.filter((link) => {
        const href = link.getAttribute('href') || '';
        const isCommentPermalink = href.includes('comment_id=') ||
          href.includes('comment_id%3D') ||
          href.includes('reply_comment_id=');
        return href.includes(postId) && !isCommentPermalink;
      });
      const commentPermalinkLinks = links.filter((link) => {
        const href = link.getAttribute('href') || '';
        return href.includes(postId) && (
          href.includes('comment_id=') ||
          href.includes('comment_id%3D') ||
          href.includes('reply_comment_id=')
        );
      });
      return {
        text,
        link_count: links.length,
        contains_target_post_id: matchingLinks.length > 0,
        target_comment_link_count: commentPermalinkLinks.length,
        author_candidates: authorCandidates,
        timestamp_candidates: timestampCandidates,
        interaction_markers: interactionMarkers,
      };
    }
    """
    defaults = {
        "text": "",
        "link_count": 0,
        "contains_target_post_id": False,
        "author_candidates": [],
        "timestamp_candidates": [],
        "interaction_markers": [],
    }
    try:
        raw = article.evaluate(script, post_id)
    except PlaywrightError:
        raw = defaults
    if not isinstance(raw, dict):
        raw = defaults

    text = str(raw.get("text") or "")
    title_overlap_score = _title_overlap_score(text, title_context.get("content_fragment"))
    score, reasons = _score_article_candidate(raw, title_context, title_overlap_score)
    return {
        "index": index,
        "text_preview": text[:240],
        "text_length": len(text),
        "link_count": int(raw.get("link_count") or 0),
        "contains_target_post_id": bool(raw.get("contains_target_post_id")),
        "target_comment_link_count": int(raw.get("target_comment_link_count") or 0),
        "author_candidates": list(raw.get("author_candidates") or []),
        "timestamp_candidates": list(raw.get("timestamp_candidates") or []),
        "title_overlap_score": title_overlap_score,
        "interaction_markers": list(raw.get("interaction_markers") or []),
        "container_score": score,
        "score_reasons": reasons,
    }


def _message_locator(page: Page) -> Locator:
    return page.locator(", ".join(MESSAGE_SELECTORS))


def _message_candidate_diagnostics(
    page: Page,
    title_context: dict[str, str | None],
    limit: int = 20,
) -> list[dict[str, Any]]:
    messages = _message_locator(page)
    summaries: list[dict[str, Any]] = []
    for index in range(min(max(_safe_count(messages), 0), limit)):
        summaries.append(_message_diagnostic_summary(messages.nth(index), index, title_context))
    return summaries


def _message_diagnostic_summary(
    message: Locator,
    index: int,
    title_context: dict[str, str | None],
) -> dict[str, Any]:
    text = _safe_inner_text(message, preserve_lines=True) or ""
    normalized_text = _normalize_match_text(text)
    title_fragment = title_context.get("content_fragment")
    normalized_title = _normalize_match_text(title_fragment)
    author = title_context.get("author")
    normalized_author = _normalize_match_text(author)
    title_overlap_score = _title_overlap_score(text, title_fragment)
    try:
        visible = message.is_visible(timeout=500)
    except PlaywrightError:
        visible = False
    return {
        "index": index,
        "text_preview": " ".join(text.split())[:240],
        "normalized_text_preview": normalized_text[:240],
        "text_length": len(text),
        "visible": visible,
        "title_overlap_score": title_overlap_score,
        "contains_title_fragment": bool(normalized_title and normalized_title in normalized_text),
        "contains_author_name": bool(normalized_author and normalized_author in normalized_text),
    }


def _message_ancestor_candidate_diagnostics(
    page: Page,
    title_context: dict[str, str | None],
    limit_messages: int = 8,
    limit_depth: int = 12,
) -> list[dict[str, Any]]:
    messages = _message_locator(page)
    all_summaries: list[dict[str, Any]] = []
    max_messages = min(max(_safe_count(messages), 0), limit_messages)
    for message_index in range(max_messages):
        message_summary = _message_diagnostic_summary(messages.nth(message_index), message_index, title_context)
        if (
            float(message_summary["title_overlap_score"]) < 0.35
            and not bool(message_summary["contains_title_fragment"])
        ):
            continue
        all_summaries.extend(
            _message_ancestor_diagnostics_for_message(
                messages.nth(message_index),
                message_index,
                title_context,
                limit_depth=limit_depth,
            )
        )
    return all_summaries


def _message_ancestor_diagnostics_for_message(
    message: Locator,
    message_index: int,
    title_context: dict[str, str | None],
    limit_depth: int = 12,
) -> list[dict[str, Any]]:
    ancestors = message.locator("xpath=ancestor::*")
    summaries: list[dict[str, Any]] = []
    for ancestor_index in range(min(max(_safe_count(ancestors), 0), limit_depth)):
        depth = ancestor_index + 1
        ancestor = ancestors.nth(ancestor_index)
        summary = _message_ancestor_diagnostic_summary(ancestor, message_index, depth, title_context)
        summaries.append(summary)
    return summaries


def _message_ancestor_diagnostic_summary(
    ancestor: Locator,
    message_index: int,
    depth: int,
    title_context: dict[str, str | None],
) -> dict[str, Any]:
    script = """
    (element) => {
      const messageSelector = '[data-ad-preview="message"], [data-ad-comet-preview="message"]';
      const normalizedText = (element.innerText || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
      const timestampCandidates = Array.from(element.querySelectorAll('time, a[aria-label], [aria-labelledby], [title]'))
        .map((node) => (
          node.getAttribute('aria-label') ||
          node.getAttribute('title') ||
          node.innerText ||
          ''
        ).replace(/\\s+/g, ' ').trim())
        .filter(Boolean)
        .slice(0, 5);
      return {
        tag_name: element.tagName.toLowerCase(),
        role: element.getAttribute('role'),
        text: normalizedText,
        descendant_count: element.querySelectorAll('*').length,
        message_count: element.querySelectorAll(messageSelector).length,
        timestamp_candidate_count: timestampCandidates.length,
        timestamp_candidates: timestampCandidates,
        reaction_marker_count: element.querySelectorAll('[data-ad-rendering-role="like_button"]').length,
        comment_marker_count: element.querySelectorAll('[data-ad-rendering-role="comment_button"]').length,
        share_marker_count: element.querySelectorAll('[data-ad-rendering-role="share_button"]').length,
      };
    }
    """
    defaults = {
        "tag_name": "",
        "role": None,
        "text": "",
        "descendant_count": 1_000_000,
        "message_count": 0,
        "timestamp_candidate_count": 0,
        "timestamp_candidates": [],
        "reaction_marker_count": 0,
        "comment_marker_count": 0,
        "share_marker_count": 0,
    }
    try:
        raw = ancestor.evaluate(script)
    except PlaywrightError:
        raw = defaults
    if not isinstance(raw, dict):
        raw = defaults

    text = str(raw.get("text") or "")
    title_overlap_score = _title_overlap_score(text, title_context.get("content_fragment"))
    normalized_text = _normalize_match_text(text)
    normalized_author = _normalize_match_text(title_context.get("author"))
    author_match = bool(normalized_author and normalized_author in normalized_text)
    score, reasons, is_valid = _score_message_ancestor(raw, author_match, title_overlap_score)
    return {
        "message_index": message_index,
        "depth": depth,
        "tag_name": str(raw.get("tag_name") or ""),
        "role": raw.get("role"),
        "text_preview": text[:240],
        "text_length": len(text),
        "descendant_count": int(raw.get("descendant_count") or 0),
        "message_count": int(raw.get("message_count") or 0),
        "author_match": author_match,
        "timestamp_candidate_count": int(raw.get("timestamp_candidate_count") or 0),
        "timestamp_candidates": list(raw.get("timestamp_candidates") or []),
        "reaction_marker_count": int(raw.get("reaction_marker_count") or 0),
        "comment_marker_count": int(raw.get("comment_marker_count") or 0),
        "share_marker_count": int(raw.get("share_marker_count") or 0),
        "title_overlap_score": title_overlap_score,
        "container_score": score,
        "score_reasons": reasons,
        "is_valid_candidate": is_valid,
    }


def _score_message_ancestor(
    raw: dict[str, Any],
    author_match: bool,
    title_overlap_score: float,
) -> tuple[int, list[str], bool]:
    score = 0
    reasons: list[str] = []
    text = str(raw.get("text") or "")
    message_count = int(raw.get("message_count") or 0)
    descendant_count = int(raw.get("descendant_count") or 0)
    timestamp_count = int(raw.get("timestamp_candidate_count") or 0)
    reaction_count = int(raw.get("reaction_marker_count") or 0)
    comment_count = int(raw.get("comment_marker_count") or 0)
    share_count = int(raw.get("share_marker_count") or 0)
    tag_name = str(raw.get("tag_name") or "").lower()

    if title_overlap_score >= 0.9:
        score += 8
        reasons.append("strong_message_title_match")
    elif title_overlap_score >= 0.65:
        score += 5
        reasons.append("partial_message_title_match")
    if message_count == 1:
        score += 3
        reasons.append("single_message_container")
    else:
        score -= 8
        reasons.append("multiple_or_missing_messages")
    if author_match:
        score += 3
        reasons.append("title_author_match")
    if timestamp_count > 0:
        score += 3
        reasons.append("timestamp_candidate_present")
    marker_score = 0
    if reaction_count > 0:
        marker_score += 1
    if comment_count > 0:
        marker_score += 1
    if share_count > 0:
        marker_score += 1
    if marker_score:
        score += marker_score
        reasons.append("interaction_markers_present")
    if 80 <= len(text) <= 6_000:
        score += 1
        reasons.append("reasonable_text_length")
    if len(text) < 40:
        score -= 4
        reasons.append("too_short_for_main_post")
    if descendant_count > 2_500 or len(text) > 8_000 or tag_name in {"html", "body"}:
        score -= 4
        reasons.append("too_broad_for_single_post")

    has_semantic_signal = author_match or timestamp_count > 0 or marker_score > 0
    is_valid = (
        score >= 12
        and message_count == 1
        and has_semantic_signal
        and descendant_count <= 2_500
        and len(text) <= 8_000
        and tag_name not in {"html", "body"}
    )
    return score, reasons, is_valid


def _message_ancestor_rejection_reason(
    message_candidates: list[dict[str, Any]],
    ancestor_candidates: list[dict[str, Any]],
) -> str | None:
    if not message_candidates:
        return "no_message_candidates"
    matching_messages = [
        candidate
        for candidate in message_candidates
        if float(candidate["title_overlap_score"]) >= 0.65 and bool(candidate["contains_title_fragment"])
    ]
    if not matching_messages:
        return "no_message_matches_title"
    if len(matching_messages) > 1:
        sorted_messages = sorted(
            matching_messages,
            key=lambda item: float(item["title_overlap_score"]),
            reverse=True,
        )
        if float(sorted_messages[0]["title_overlap_score"]) - float(sorted_messages[1]["title_overlap_score"]) < 0.2:
            return "ambiguous_message_matches"
    if ancestor_candidates and not any(bool(candidate.get("is_valid_candidate")) for candidate in ancestor_candidates):
        return "no_valid_message_ancestor"
    return None


def _score_article_candidate(
    raw: dict[str, Any],
    title_context: dict[str, str | None],
    title_overlap_score: float,
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    text = str(raw.get("text") or "")
    normalized_text = _normalize_match_text(text)
    author = title_context.get("author")

    if title_overlap_score >= 0.9:
        score += 8
        reasons.append("strong_title_text_match")
    elif title_overlap_score >= 0.65:
        score += 5
        reasons.append("partial_title_text_match")
    if author and _normalize_match_text(author) in normalized_text:
        score += 2
        reasons.append("title_author_match")
    if raw.get("timestamp_candidates"):
        score += 2
        reasons.append("timestamp_candidate_present")
    interaction_markers = set(raw.get("interaction_markers") or [])
    if interaction_markers.intersection({"like_button", "comment_button", "share_button"}):
        score += 2
        reasons.append("interaction_markers_present")
    if 80 <= len(text) <= 5_000:
        score += 1
        reasons.append("reasonable_text_length")
    if len(text) < 40:
        score -= 4
        reasons.append("too_short_for_main_post")
    if any(marker in normalized_text for marker in ("sponsored", "suggested for you", "people you may know")):
        score -= 4
        reasons.append("recommendation_or_navigation_text")

    return score, reasons


def _title_overlap_score(article_text: str, title_fragment: str | None) -> float:
    if not title_fragment:
        return 0.0
    normalized_article = _normalize_match_text(article_text)
    normalized_title = _normalize_match_text(title_fragment)
    if not normalized_title:
        return 0.0
    if normalized_title in normalized_article:
        return 1.0
    title_words = [word for word in normalized_title.split() if len(word) > 2]
    if not title_words:
        return 0.0
    article_words = set(normalized_article.split())
    matched = sum(1 for word in title_words if word in article_words)
    return matched / len(title_words)


def _normalize_match_text(value: str | None) -> str:
    if value is None:
        return ""
    normalized = value.lower()
    normalized = normalized.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    normalized = normalized.replace("…", " ")
    normalized = re.sub(r"\.\.\.", " ", normalized)
    normalized = re.sub(r"[^\w\s']", " ", normalized, flags=re.UNICODE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _extract_article_data(
    article: Locator,
    input_url: str,
    canonical_url: str,
    post_id: str,
    context_type: str | None = None,
    extract_comments: bool = False,
    max_comments: int = 20,
    expand_comments: bool = True,
    max_comment_expand_actions: int = 3,
    no_progress_round_limit: int = 3,
) -> dict[str, Any]:
    author = _extract_author(article)
    content = _extract_content(article)
    publish_time, publish_time_text = _extract_publish_time(article, post_id)
    like_count, comment_count, share_count = _extract_metrics(article)
    comment_scope = _comment_scope(article)
    comment_state = _comment_state(comment_scope)
    comment_progress = _comment_progress_from_scope(comment_scope) if not comment_state["found"] else {}
    if comment_state["found"]:
        comment_count = 0
    elif comment_count is None and isinstance(comment_progress.get("total"), int):
        comment_count = int(comment_progress["total"])
    comments, comments_diagnostics = (
        _extract_comments(
            article,
            post_id,
            max_comments,
            expand_comments,
            max_comment_expand_actions,
            no_progress_round_limit,
            comment_scope=comment_scope,
            initial_progress=comment_progress,
            initial_comment_state=comment_state,
        )
        if extract_comments and max_comments > 0
        else ([], {"enabled": False})
    )

    result = _build_extraction_result(
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
        comments=comments,
        extraction_source="dom",
        metadata_error=None,
        context_type=context_type,
    )
    if extract_comments:
        result["comments_diagnostics"] = comments_diagnostics
    result["comments_disabled"] = bool(comment_state["comments_disabled"])
    result["comment_empty_state_found"] = bool(comment_state["found"])
    result["comment_empty_state_source"] = comment_state["source"]
    result["comment_count_state"] = "known_zero" if comment_state["found"] else ("known_value" if isinstance(comment_count, int) else "unknown")
    return result


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
    context_type: str | None = None,
    comments: list[dict[str, str]] | None = None,
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
        "like_count": like_count,
        "share_count": share_count,
        "comment_count": comment_count,
        "comments": comments or [],
        "collected_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "collection_status": status,
        "extraction_source": extraction_source,
        "error": error,
    }
    if context_type:
        result["context_type"] = context_type
    if publish_time_text:
        result["publish_time_text"] = publish_time_text
    return result


def _apply_publish_time_to_result(
    result: dict[str, Any],
    publish_time: str | None,
    publish_time_text: str | None,
) -> dict[str, Any]:
    if publish_time is None and publish_time_text is None:
        return result

    updated = dict(result)
    updated["publish_time"] = publish_time
    if publish_time_text:
        updated["publish_time_text"] = publish_time_text
    else:
        updated.pop("publish_time_text", None)

    has_author = bool(updated["author"]["name"] or updated["author"]["profile_url"])
    has_content = bool(updated["content"])
    has_publish_time = bool(updated["publish_time"] or updated.get("publish_time_text"))

    if updated["extraction_source"] == "dom" and has_author and has_content and has_publish_time:
        updated["collection_status"] = "success"
        updated["error"] = None
        return updated

    missing = []
    if not has_author:
        missing.append("author")
    if not has_content:
        missing.append("content")
    if not has_publish_time:
        missing.append("publish_time")
    updated["collection_status"] = "partial"
    updated["error"] = f"Missing core field(s): {', '.join(missing)}." if missing else updated.get("error")
    return updated


def _build_metadata_result(
    page: Page,
    input_url: str,
    canonical_url: str,
    post_id: str,
    context_type: str | None = None,
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
        comments=[],
        extraction_source="page_metadata",
        metadata_error="Full Facebook post DOM was unavailable.",
        context_type=context_type,
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

    fallback_name = _extract_author_name_from_action_label(article) or _extract_author_name_from_dialog_label(article)
    if fallback_name:
        return {
            "name": fallback_name,
            "profile_url": None,
        }

    return {
        "name": None,
        "profile_url": None,
    }


def _extract_author_name_from_action_label(article: Locator) -> str | None:
    nodes = article.locator('[aria-label*="Actions for this post by" i]')
    for index in range(_safe_count(nodes)):
        label = nodes.nth(index).get_attribute("aria-label") or ""
        author = _author_name_from_action_label(label)
        if author:
            return author
    return None


def _extract_author_name_from_dialog_label(article: Locator) -> str | None:
    labels: list[str] = []
    role = article.get_attribute("role")
    if role == "dialog":
        labels.append(article.get_attribute("aria-label") or "")
    headings = article.locator('h1, h2, h3, [role="heading"]')
    for index in range(min(max(_safe_count(headings), 0), 5)):
        text = _safe_inner_text(headings.nth(index))
        if text:
            labels.append(text)
    for label in labels:
        author = _author_name_from_post_label(label)
        if author:
            return author
    return None


def _author_name_from_action_label(label: str) -> str | None:
    match = re.search(r"Actions for this post by\s+(.+)$", label, flags=re.IGNORECASE)
    return match.group(1).strip() if match and match.group(1).strip() else None


def _author_name_from_post_label(label: str) -> str | None:
    match = re.match(r"(.+?)[’']s Post$", label.strip())
    return match.group(1).strip() if match and match.group(1).strip() else None


def _extract_content(article: Locator) -> str | None:
    _click_see_more_in_message(article)
    for selector in MESSAGE_SELECTORS:
        message = article.locator(selector).first
        if message.count() == 0:
            continue
        text = _safe_inner_text(message, preserve_lines=True)
        if text:
            return text
    dialog_text = _extract_dialog_visible_post_content(article)
    if dialog_text:
        return dialog_text
    return _extract_fallback_content(article)


def _extract_comments(
    article: Locator,
    post_id: str,
    max_comments: int,
    expand_comments: bool,
    max_expand_actions: int,
    no_progress_round_limit: int,
    comment_scope: Locator | None = None,
    initial_progress: dict[str, Any] | None = None,
    initial_comment_state: dict[str, Any] | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if max_comments <= 0:
        return [], {"enabled": True, "comments_collected_count": 0, "comment_expansion_stop_reason": "max_comments_zero"}
    scope = comment_scope or _comment_scope(article)
    comment_state = initial_comment_state or _comment_state(scope)
    if comment_state["found"]:
        stop_reason = "comments_disabled" if comment_state["comments_disabled"] else "no_comments"
        return [], _comment_diagnostics(
            [],
            0,
            stop_reason,
            progress={},
            comments_visible_before=0,
            comments_visible_after=0,
            comment_ids_before=0,
            comment_ids_after=0,
            expand_button_found=False,
            expand_candidate_found=False,
            expansion_clicked=False,
            expansion_click_failed=False,
            loading_mode=None,
            scroll_actions=0,
            loading_events=[],
            comment_state=comment_state,
        )
    comments: list[dict[str, str]] = []
    seen: set[str] = set()
    expansion_actions = 0
    no_progress_rounds = 0
    stop_reason = "no_expand_requested" if not expand_comments else "no_more_expand_buttons"
    progress = initial_progress or _comment_progress_from_scope(scope, allow_without_button=True)
    initial_current_comments = _extract_current_comments(scope, post_id)
    comments_visible_before = len(initial_current_comments)
    comments_visible_after = comments_visible_before
    comment_ids_before = _comment_id_set(initial_current_comments)
    comment_ids_after = set(comment_ids_before)
    expand_button_found = bool(progress.get("button_found"))
    expand_candidate_found = bool(progress.get("comment_expand_candidate_found") or progress.get("button_found"))
    expansion_clicked = False
    expansion_click_failed = False
    scroll_actions = 0
    loading_mode: str | None = None
    loading_events: list[dict[str, Any]] = []

    def collect_current_comments() -> int:
        nonlocal comments_visible_after, comment_ids_after
        current_comments = _extract_current_comments(scope, post_id)
        comments_visible_after = len(current_comments)
        comment_ids_after = _comment_id_set(current_comments)
        for current_comment in current_comments:
            key = _comment_dedupe_key(current_comment)
            if key in seen:
                continue
            seen.add(key)
            comments.append(current_comment)
            if len(comments) >= max_comments:
                break
        return len(current_comments)

    collect_current_comments()
    while True:
        if len(comments) >= max_comments:
            stop_reason = "max_comments_reached"
            break
        if not expand_comments:
            break
        if expansion_actions + scroll_actions >= max_expand_actions:
            stop_reason = "max_expand_actions_reached"
            break
        if no_progress_rounds >= no_progress_round_limit:
            stop_reason = "no_progress_round_limit"
            break
        progress_total = progress.get("total")
        if isinstance(progress_total, int) and len(comments) >= progress_total:
            stop_reason = "displayed_comment_total_reached"
            break

        before_unique_count = len(comments)
        before_visible_count = comments_visible_after
        before_progress_current = progress.get("current")
        before_comment_ids = set(comment_ids_after)
        expand_result = _expand_one_comment_button(scope, post_id, before_visible_count, before_progress_current)
        expand_button_found = expand_button_found or bool(expand_result.get("button_found"))
        expand_candidate_found = expand_candidate_found or bool(expand_result.get("comment_expand_candidate_found"))
        expansion_clicked = expansion_clicked or bool(expand_result.get("comment_expansion_clicked"))
        expansion_click_failed = expansion_click_failed or bool(expand_result.get("comment_expansion_click_failed"))
        next_progress = expand_result.get("progress") or _comment_progress_from_scope(scope)
        if next_progress.get("text") or not progress.get("text"):
            progress = next_progress
        button_clicked = bool(expand_result.get("clicked"))
        scroll_result: dict[str, Any] | None = None
        if button_clicked:
            expansion_actions += 1
            loading_mode = "button" if loading_mode is None else "mixed"
        else:
            if expansion_actions + scroll_actions >= max_expand_actions:
                stop_reason = "max_expand_actions_reached"
                break
            loading_events.append(
                {
                    "event": "comment_scroll_started",
                    "comments_visible": comments_visible_after,
                    "comment_id_count": len(comment_ids_after),
                }
            )
            scroll_result = _scroll_comment_scope(scope, post_id)
            if not scroll_result.get("scrolled"):
                stop_reason = str(scroll_result.get("reason") or expand_result.get("reason") or "comment_scroll_unavailable")
                loading_events.append(
                    {
                        "event": "comment_scroll_stopped",
                        "reason": stop_reason,
                        "comments_visible": comments_visible_after,
                        "comment_id_count": len(comment_ids_after),
                    }
                )
                break
            scroll_actions += 1
            loading_mode = "scroll" if loading_mode is None else "mixed"
            next_progress = _comment_progress_from_scope(scope, allow_without_button=True)
            if next_progress.get("text") or not progress.get("text"):
                progress = next_progress
        after_visible_count = collect_current_comments()
        after_progress_current = progress.get("current")
        progressed = (
            len(comments) > before_unique_count
            or after_visible_count > before_visible_count
            or len(comment_ids_after) > len(before_comment_ids)
            or (
                isinstance(before_progress_current, int)
                and isinstance(after_progress_current, int)
                and after_progress_current > before_progress_current
            )
            or bool(expand_result.get("button_changed"))
            or bool(scroll_result and scroll_result.get("progressed"))
        )
        if progressed:
            no_progress_rounds = 0
            if scroll_result is not None:
                loading_events.append(
                    {
                        "event": "comment_scroll_progress",
                        "comments_visible": comments_visible_after,
                        "comment_id_count": len(comment_ids_after),
                    }
                )
        else:
            no_progress_rounds += 1
            if scroll_result is not None:
                loading_events.append(
                    {
                        "event": "comment_scroll_stopped",
                        "reason": "no_progress",
                        "comments_visible": comments_visible_after,
                        "comment_id_count": len(comment_ids_after),
                    }
                )
    return comments, _comment_diagnostics(
        comments,
        expansion_actions,
        stop_reason,
        progress=progress,
        comments_visible_before=comments_visible_before,
        comments_visible_after=comments_visible_after,
        comment_ids_before=len(comment_ids_before),
        comment_ids_after=len(comment_ids_after),
        expand_button_found=expand_button_found,
        expand_candidate_found=expand_candidate_found,
        expansion_clicked=expansion_clicked,
        expansion_click_failed=expansion_click_failed,
        loading_mode=loading_mode,
        scroll_actions=scroll_actions,
        loading_events=loading_events,
        comment_state=comment_state,
    )


def _extract_current_comments(article: Locator, post_id: str) -> list[dict[str, str]]:
    candidates = article.locator(
        '[aria-label*="Comment by" i], [aria-label*="Reply by" i], '
        '[role="article"][aria-label*="comment" i], [role="article"][aria-label*="reply" i]'
    )
    comments: list[dict[str, str]] = []
    for index in range(_safe_count(candidates)):
        candidate = candidates.nth(index)
        comment = _extract_comment_from_candidate(candidate, post_id)
        if comment is None:
            continue
        comments.append(comment)
    return comments


def _comment_id_set(comments: list[dict[str, str]]) -> set[str]:
    return {comment["comment_id"] for comment in comments if comment.get("comment_id")}


def _comment_scope(article: Locator) -> Locator:
    try:
        if article.evaluate("(node) => node.getAttribute('role') === 'dialog'", timeout=500):
            return article
    except PlaywrightError:
        pass
    dialog = article.locator("xpath=ancestor::*[@role='dialog'][1]")
    if _safe_count(dialog) > 0:
        return dialog.first
    return article


def _comment_state(scope: Locator | None) -> dict[str, Any]:
    empty = _comment_empty_state(scope)
    if empty["found"]:
        return {
            "found": True,
            "comments_disabled": bool(empty["comments_disabled"]),
            "source": empty["source"],
            "markers": list(empty["markers"]),
            "comment_count_state": "known_zero",
        }
    return {
        "found": False,
        "comments_disabled": False,
        "source": None,
        "markers": [],
        "comment_count_state": "unknown",
    }


def _scroll_comment_scope(scope: Locator, post_id: str) -> dict[str, Any]:
    before_ids = _comment_id_set(_extract_current_comments(scope, post_id))
    current_comments = _extract_current_comments(scope, post_id)
    if current_comments:
        last_id = current_comments[-1].get("comment_id")
        if last_id:
            last_links = scope.locator(f'a[href*="comment_id={last_id}"]')
            if _safe_count(last_links) > 0:
                try:
                    last_links.last.scroll_into_view_if_needed(timeout=1_000)
                except PlaywrightError:
                    pass
    try:
        scroll_payload = scope.evaluate(
            """
            (node) => {
              const canScroll = (element) => {
                if (!element) {
                  return false;
                }
                const style = window.getComputedStyle(element);
                return /(auto|scroll)/.test(style.overflowY || '') && element.scrollHeight > element.clientHeight + 8;
              };
              let current = node;
              while (current && !canScroll(current)) {
                current = current.parentElement;
              }
              const target = current || node;
              const before = target.scrollTop || 0;
              const distance = Math.max(480, Math.floor((target.clientHeight || window.innerHeight || 600) * 0.85));
              target.scrollTop = before + distance;
              if (target === document.body || target === document.documentElement) {
                window.scrollBy(0, distance);
              }
              return {
                before,
                after: target.scrollTop || before,
                distance,
                usedScrollableAncestor: Boolean(current),
              };
            }
            """,
            timeout=1_000,
        )
    except PlaywrightError:
        return {"scrolled": False, "progressed": False, "reason": "comment_scroll_failed"}

    progressed = False
    for _ in range(10):
        _short_wait(scope, 50)
        after_ids = _comment_id_set(_extract_current_comments(scope, post_id))
        if len(after_ids) > len(before_ids):
            progressed = True
            break
    return {
        "scrolled": True,
        "progressed": progressed,
        "reason": None,
        "scroll_payload": scroll_payload if isinstance(scroll_payload, dict) else {},
    }


def _expand_one_comment_button(
    scope: Locator,
    post_id: str,
    previous_visible_count: int,
    previous_progress_current: Any,
) -> dict[str, Any]:
    button_info = _find_comment_expand_button(scope)
    if button_info.get("button") is None:
        return {
            "button_found": False,
            "comment_expand_candidate_found": bool(button_info.get("candidate_found")),
            "comment_expansion_click_failed": False,
            "clicked": False,
            "button_changed": False,
            "reason": button_info.get("reason") or "no_more_expand_buttons",
            "progress": _comment_progress_from_scope(scope),
        }
    button = button_info["button"]
    before_text = str(button_info.get("text") or "")
    before_progress = _comment_progress_near_button(button)
    try:
        button.click(timeout=500)
    except PlaywrightError:
        return {
            "button_found": True,
            "comment_expand_candidate_found": True,
            "comment_expansion_click_failed": True,
            "clicked": False,
            "button_changed": False,
            "reason": "expand_click_failed",
            "progress": before_progress or _comment_progress_from_scope(scope),
        }

    last_progress = before_progress or _comment_progress_from_scope(scope)
    button_changed = False
    for _ in range(10):
        _short_wait(scope, 50)
        current_comments = _extract_current_comments(scope, post_id)
        current_progress = _comment_progress_from_scope(scope, allow_without_button=True)
        next_button = _find_comment_expand_button(scope)
        next_text = str(next_button.get("text") or "") if next_button else ""
        button_changed = next_button is None or (before_text and next_text and next_text != before_text)
        last_progress = current_progress
        progress_current = current_progress.get("current")
        if (
            len(current_comments) > previous_visible_count
            or (
                isinstance(previous_progress_current, int)
                and isinstance(progress_current, int)
                and progress_current > previous_progress_current
            )
            or button_changed
        ):
            break
    return {
        "button_found": True,
        "comment_expand_candidate_found": True,
        "comment_expansion_clicked": True,
        "comment_expansion_click_failed": False,
        "clicked": True,
        "button_changed": button_changed,
        "reason": None,
        "progress": last_progress,
    }


def _find_comment_expand_button(scope: Locator) -> dict[str, Any]:
    text_nodes = scope.locator('span[dir="auto"], div[dir="auto"]')
    for index in range(min(_safe_count(text_nodes), 120)):
        node = text_nodes.nth(index)
        text = _safe_inner_text(node)
        if not _is_comment_expand_text(text):
            continue
        button = node.locator('xpath=ancestor::div[@role="button"][1]')
        if _safe_count(button) == 0:
            return {"candidate_found": True, "button": None, "text": text, "reason": "expand_button_not_clickable"}
        candidate = button.first
        status = _comment_expand_button_status(candidate)
        if status is None:
            return {"candidate_found": True, "button": candidate, "text": text, "reason": None}
        return {"candidate_found": True, "button": None, "text": text, "reason": status}
    return {"candidate_found": False, "button": None, "text": None, "reason": "no_more_expand_buttons"}


def _comment_expand_button_status(button: Locator) -> str | None:
    try:
        if _safe_count(button) == 0:
            return "expand_button_detached"
        if not button.is_visible(timeout=200):
            return "expand_button_not_clickable"
        if not button.is_enabled(timeout=200):
            return "expand_button_not_clickable"
        return None
    except PlaywrightError:
        return "expand_button_detached"


def _is_comment_expand_text(value: str | None) -> bool:
    if not value:
        return False
    normalized = " ".join(value.split()).lower()
    return any(
        phrase in normalized
        for phrase in (
            "view more comments",
            "see more comments",
            "view previous comments",
            "more comments",
            "view more replies",
        )
    )


def _comment_progress_from_scope(scope: Locator, allow_without_button: bool = False) -> dict[str, Any]:
    button_info = _find_comment_expand_button(scope)
    if button_info.get("button") is None:
        if allow_without_button:
            progress = _comment_progress_without_button(scope)
            if progress.get("text"):
                return progress
        return {
            "button_found": False,
            "comment_expand_candidate_found": bool(button_info.get("candidate_found")),
            "text": None,
            "current": None,
            "total": None,
            "reason": button_info.get("reason"),
        }
    progress = _comment_progress_near_button(button_info["button"])
    progress["button_found"] = True
    progress["comment_expand_candidate_found"] = True
    return progress


def _comment_progress_without_button(scope: Locator) -> dict[str, Any]:
    texts = scope.locator('span[dir="auto"], div[dir="auto"]')
    for index in range(min(_safe_count(texts), 80)):
        text = _safe_inner_text(texts.nth(index))
        parsed = _parse_comment_progress_text(text)
        if parsed is not None:
            return {
                "button_found": False,
                "text": parsed["text"],
                "current": parsed["current"],
                "total": parsed["total"],
            }
    return {
        "button_found": False,
        "text": None,
        "current": None,
        "total": None,
    }


def _parse_comment_progress_text(value: str | None) -> dict[str, int | str] | None:
    if not value:
        return None
    normalized = " ".join(value.replace("\u00a0", " ").split())
    match = re.search(r"\b(\d[\d,]*)\s+of\s+(\d[\d,]*)\b", normalized, flags=re.IGNORECASE)
    if not match:
        return None
    current = int(match.group(1).replace(",", ""))
    total = int(match.group(2).replace(",", ""))
    return {"text": match.group(0), "current": current, "total": total}


def _comment_progress_near_button(button: Locator) -> dict[str, Any]:
    script = r"""
    (button) => {
      const normalize = (value) => (value || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
      const parseProgress = (text) => {
        const match = normalize(text).match(/\b(\d[\d,]*)\s+of\s+(\d[\d,]*)\b/i);
        if (!match) {
          return null;
        }
        const current = Number.parseInt(match[1].replace(/,/g, ''), 10);
        const total = Number.parseInt(match[2].replace(/,/g, ''), 10);
        if (!Number.isFinite(current) || !Number.isFinite(total)) {
          return null;
        }
        return { text: match[0], current, total };
      };
      let current = button;
      for (let depth = 0; current && depth <= 5; depth += 1) {
        const own = parseProgress(current.innerText || '');
        if (own) {
          return own;
        }
        const nodes = Array.from(current.querySelectorAll('span[dir="auto"], div[dir="auto"]'));
        for (const node of nodes) {
          const parsed = parseProgress(node.innerText || node.textContent || '');
          if (parsed) {
            return parsed;
          }
        }
        current = current.parentElement;
      }
      return null;
    }
    """
    try:
        result = button.evaluate(script, timeout=1_000)
    except PlaywrightError:
        result = None
    if isinstance(result, dict):
        return {
            "button_found": True,
            "text": result.get("text") if isinstance(result.get("text"), str) else None,
            "current": result.get("current") if isinstance(result.get("current"), int) else None,
            "total": result.get("total") if isinstance(result.get("total"), int) else None,
        }
    return {
        "button_found": True,
        "text": None,
        "current": None,
        "total": None,
    }


def _short_wait(locator: Locator, timeout_ms: int) -> None:
    try:
        locator.evaluate("(node, timeout) => new Promise((resolve) => setTimeout(resolve, timeout))", timeout_ms)
    except PlaywrightError:
        pass


def _comment_dedupe_key(comment: dict[str, str]) -> str:
    comment_id = comment.get("comment_id")
    if comment_id:
        return f"comment_id:{comment_id}"
    return "fallback:" + "|".join(
        " ".join(str(comment.get(field, "")).lower().split())
        for field in ("comment_author", "comment_content", "comment_time")
    )


def _comment_diagnostics(
    comments: list[dict[str, str]],
    expansion_actions: int,
    stop_reason: str,
    progress: dict[str, Any] | None = None,
    comments_visible_before: int | None = None,
    comments_visible_after: int | None = None,
    comment_ids_before: int | None = None,
    comment_ids_after: int | None = None,
    expand_button_found: bool = False,
    expand_candidate_found: bool = False,
    expansion_clicked: bool = False,
    expansion_click_failed: bool = False,
    loading_mode: str | None = None,
    scroll_actions: int = 0,
    loading_events: list[dict[str, Any]] | None = None,
    comment_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    progress = progress or {}
    comment_state = comment_state or {
        "found": False,
        "comments_disabled": False,
        "source": None,
        "comment_count_state": "unknown",
    }
    resolved_loading_mode = loading_mode
    if resolved_loading_mode is None:
        resolved_loading_mode = "button" if expansion_actions > 0 else ("scroll" if scroll_actions > 0 else None)
    return {
        "comments_collected_count": len(comments),
        "comments_complete": False,
        "comment_loading_mode": resolved_loading_mode,
        "comment_progress_text": progress.get("text"),
        "comments_visible_before": comments_visible_before,
        "comments_visible_after": comments_visible_after,
        "comment_ids_before": comment_ids_before,
        "comment_ids_after": comment_ids_after,
        "comment_total_from_progress": progress.get("total"),
        "comment_expand_button_found": expand_button_found,
        "comment_expand_candidate_found": expand_candidate_found,
        "comment_expansion_clicked": expansion_clicked,
        "comment_expansion_click_failed": expansion_click_failed,
        "comment_expansion_actions": expansion_actions,
        "comment_scroll_actions": scroll_actions,
        "comment_loading_events": loading_events or [],
        "comment_expansion_stop_reason": stop_reason,
        "comments_disabled": bool(comment_state.get("comments_disabled")),
        "comment_empty_state_found": bool(comment_state.get("found")),
        "comment_empty_state_source": comment_state.get("source"),
        "comment_count_state": comment_state.get("comment_count_state") or (
            "known_zero" if comment_state.get("found") else "unknown"
        ),
    }


def _extract_comment_from_candidate(candidate: Locator, post_id: str) -> dict[str, str] | None:
    if _candidate_is_target_post(candidate, post_id):
        return None
    text = _safe_inner_text(candidate, preserve_lines=True)
    if not text:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    author = _extract_comment_author(candidate) or ""
    content = _extract_comment_message(candidate, author) or _comment_content_from_lines(lines, author)
    if not content:
        return None
    if author and _normalize_match_text(content) == _normalize_match_text(author):
        return None
    comment_id = _extract_comment_id(candidate, post_id)
    return {
        "comment_content": content,
        "comment_time": _extract_comment_time(candidate) or "",
        "comment_author": author,
        "comment_id": comment_id or "",
    }


def _candidate_is_target_post(candidate: Locator, post_id: str) -> bool:
    target_links = candidate.locator(f'a[href*="{post_id}"]')
    for index in range(min(_safe_count(target_links), 5)):
        href = target_links.nth(index).get_attribute("href") or ""
        if "comment_id=" not in href and post_id in href:
            return True
    return False


def _comment_content_from_lines(lines: list[str], author: str = "") -> str | None:
    ignored = {
        "like",
        "reply",
        "share",
        "comment",
        "edited",
        "top fan",
        "follow",
        "see more",
        "view more replies",
        "write a reply",
        "anonymous participant",
    }
    normalized_author = " ".join(author.split()).lower()
    for line in lines:
        normalized = " ".join(line.split()).lower()
        if normalized_author and normalized == normalized_author:
            continue
        if is_relative_time_text(normalized):
            continue
        if normalized in ignored:
            continue
        if parse_metric_count(normalized) is not None and len(normalized) <= 8:
            continue
        if not _looks_like_comment_content(line):
            continue
        return line
    return None


def _extract_comment_message(candidate: Locator, author: str = "") -> str | None:
    selectors = (
        '[data-ad-preview="message"]',
        '[data-ad-comet-preview="message"]',
        'div[dir="auto"]',
        'span[dir="auto"]',
    )
    normalized_author = _normalize_match_text(author)
    for selector in selectors:
        nodes = candidate.locator(selector)
        for index in range(min(_safe_count(nodes), 20)):
            node = nodes.nth(index)
            text = _safe_inner_text(node, preserve_lines=True)
            if not text:
                continue
            normalized = _normalize_match_text(text)
            if normalized_author and normalized == normalized_author:
                continue
            if is_relative_time_text(text):
                continue
            if _looks_like_comment_content(text):
                return text.strip()
    return None


def _looks_like_comment_content(text: str) -> bool:
    normalized = " ".join(text.split()).lower()
    if len(normalized) < 2:
        return False
    blocked = (
        "view more comments",
        "see more comments",
        "view more replies",
        "most relevant",
        "write a comment",
        "write a reply",
        "anonymous participant",
        "follow",
        "log in",
        "sign up",
    )
    if is_relative_time_text(normalized):
        return False
    return not any(phrase in normalized for phrase in blocked)


def _extract_comment_time(candidate: Locator) -> str | None:
    comment_links = candidate.locator('a[href*="comment_id="]')
    for index in range(min(_safe_count(comment_links), 8)):
        link = comment_links.nth(index)
        text = _safe_inner_text(link)
        if text and is_relative_time_text(text):
            return text.strip()
    for selector in ("a[aria-label]", "abbr[title]", "span[aria-label]"):
        nodes = candidate.locator(selector)
        for index in range(min(_safe_count(nodes), 8)):
            node = nodes.nth(index)
            value = node.get_attribute("aria-label") or node.get_attribute("title") or _safe_inner_text(node)
            if not value:
                continue
            publish_time, publish_time_text = _parse_facebook_publish_time(value)
            if publish_time or publish_time_text:
                return publish_time or publish_time_text
    return None


def _extract_comment_author(candidate: Locator) -> str | None:
    links = candidate.locator('a[role="link"], a[href]')
    for index in range(min(_safe_count(links), 8)):
        link = links.nth(index)
        text = _safe_inner_text(link)
        href = link.get_attribute("href") or ""
        if not text or not href:
            continue
        normalized = " ".join(text.split())
        if _invalid_comment_author_text(normalized):
            continue
        if "comment_id=" in href or is_relative_time_text(normalized):
            continue
        if "facebook.com" in href or href.startswith("/"):
            return normalized
    return None


def _extract_comment_id(candidate: Locator, post_id: str) -> str | None:
    links = candidate.locator('a[href*="comment_id="]')
    for index in range(min(_safe_count(links), 8)):
        href = links.nth(index).get_attribute("href") or ""
        if post_id not in href:
            continue
        match = re.search(r"[?&]comment_id=([^&#]+)", href)
        if match:
            return match.group(1)
    return None


def _invalid_comment_author_text(value: str) -> bool:
    normalized = " ".join(value.split()).lower()
    if not normalized:
        return True
    if is_relative_time_text(normalized):
        return True
    return normalized in {
        "like",
        "reply",
        "share",
        "comment",
        "follow",
        "see more",
        "edited",
        "view more replies",
        "write a reply",
    }


def _extract_dialog_visible_post_content(article: Locator) -> str | None:
    if article.get_attribute("role") != "dialog" and _safe_count(article.locator('[aria-label*="Actions for this post by" i]')) == 0:
        return None
    text = _safe_inner_text(article, preserve_lines=True)
    if not text:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    author_name = (_extract_author(article).get("name") or "").strip()
    start_index = 0
    if author_name:
        for index, line in enumerate(lines):
            if line == author_name or author_name in line:
                start_index = index + 1
                break

    for line in lines[start_index:]:
        normalized = " ".join(line.split()).lower()
        if normalized in {"newest", "all comments", "most relevant", "write an answer…", "write an answer...", "write a comment"}:
            break
        if _is_dialog_content_noise(line):
            continue
        if _looks_like_post_content(line):
            return line
    return None


def _is_dialog_content_noise(line: str) -> bool:
    normalized = " ".join(line.split()).lower()
    if not normalized:
        return True
    if len(normalized) == 1:
        return True
    if normalized in {"facebook", "join", "like", "react", "reply", "share", "send", "comment"}:
        return True
    if normalized in {"shared with public group"}:
        return True
    if parse_metric_count(normalized) is not None and len(normalized) <= 8:
        return True
    return False


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
        "no comments yet",
        "be the first to comment",
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


def _extract_publish_time(article: Locator, post_id: str) -> tuple[str | None, str | None]:
    strict_candidates = _visible_publish_time_candidates(article)
    best_candidate = _select_best_publish_time_candidate(strict_candidates["candidates"])
    if best_candidate is not None:
        return (
            _optional_string(best_candidate.get("parsed_publish_time")),
            _optional_string(best_candidate.get("parsed_publish_time_text")),
        )

    time_element = article.locator("time").first
    if time_element.count() > 0:
        parsed = _parse_candidate_publish_time(time_element)
        if parsed != (None, None):
            return parsed

    candidates = _publish_time_candidate_locators(article, post_id)
    best_relative: tuple[str | None, str | None] = (None, None)
    for index in range(_safe_count(candidates)):
        parsed = _parse_candidate_publish_time(candidates.nth(index))
        if parsed[0]:
            return parsed
        if parsed[1] and best_relative == (None, None):
            best_relative = parsed

    return best_relative


def _select_best_publish_time_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("parsed_publish_time") is not None
        or candidate.get("parsed_publish_time_text") is not None
    ]
    if not valid_candidates:
        return None
    return sorted(valid_candidates, key=_date_candidate_sort_key, reverse=True)[0]


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _visible_publish_time_candidates(article: Locator) -> dict[str, Any]:
    script = """
    (container) => {
      const messageSelector = '[data-ad-preview="message"], [data-ad-comet-preview="message"]';
      const blockedSelector = [
        messageSelector,
        '[aria-label*="Like" i]',
        '[aria-label*="Comment" i]',
        '[aria-label*="Share" i]',
        '[role="navigation"]',
        '[aria-label="Close"]'
      ].join(',');
      const datePatterns = [
        /^[A-Z][a-z]+ \\d{1,2}, \\d{4}$/,
        /^[A-Z][a-z]{2} \\d{1,2}, \\d{4}$/,
        /^[A-Z][a-z]+ \\d{1,2}, \\d{4} at \\d{1,2}:\\d{2} [AP]M$/,
        /^[A-Z][a-z]{2} \\d{1,2}, \\d{4} at \\d{1,2}:\\d{2} [AP]M$/,
        /^\\d{4}-\\d{2}-\\d{2}$/,
        /^\\d+\\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks)$/i,
        /^Yesterday$/i
      ];
      const isHidden = (element) => {
        let current = element;
        while (current && current.nodeType === Node.ELEMENT_NODE) {
          const style = window.getComputedStyle(current);
          if (
            current.getAttribute('aria-hidden') === 'true' ||
            style.display === 'none' ||
            style.visibility === 'hidden' ||
            style.opacity === '0'
          ) {
            return true;
          }
          current = current.parentElement;
        }
        return false;
      };
      const normalizedText = (element) =>
        (element.innerText || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
      const labelledText = (element) => {
        const ids = (element.getAttribute('aria-labelledby') || '')
          .split(/\\s+/)
          .map((id) => id.trim())
          .filter(Boolean);
        const parts = [];
        for (const id of ids) {
          const label = document.getElementById(id);
          if (label) {
            parts.push((label.textContent || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim());
          }
        }
        return parts.join(' ').replace(/\\s+/g, ' ').trim();
      };
      const message = container.querySelector(messageSelector);
      const elements = Array.from(container.querySelectorAll('*'));
      const candidates = [];
      let scanned = 0;
      for (const element of elements) {
        if (isHidden(element) || element.closest(blockedSelector)) {
          continue;
        }
        const textOptions = [
          { text: normalizedText(element), strategy: 'visible_descendant_strict_match' },
          { text: (element.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim(), strategy: 'aria_label_strict_match' },
          { text: (element.getAttribute('title') || '').replace(/\\s+/g, ' ').trim(), strategy: 'title_strict_match' },
          { text: labelledText(element), strategy: 'aria_labelledby_strict_match' },
        ].filter((option) => option.text && option.text.length <= 80);
        if (textOptions.length === 0) {
          continue;
        }
        scanned += 1;
        for (const option of textOptions) {
          if (!datePatterns.some((pattern) => pattern.test(option.text))) {
            continue;
          }
          const relation = message ? (element.compareDocumentPosition(message) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0 : false;
          candidates.push({
            strategy: option.strategy,
            visible_text: option.text,
            tag_name: element.tagName,
            inside_anchor: Boolean(element.closest('a')),
            before_message: relation,
            near_author: Boolean(element.closest('[role="article"], article, [role="dialog"]')),
            descendant_count: element.querySelectorAll('*').length,
            text_length: option.text.length,
          });
        }
      }
      return { scanned_element_count: scanned, candidates };
    }
    """
    try:
        scan = article.evaluate(script)
    except PlaywrightError:
        return {"scanned_element_count": 0, "candidates": []}
    if not isinstance(scan, dict):
        return {"scanned_element_count": 0, "candidates": []}

    deduped: dict[str, dict[str, Any]] = {}
    for raw_candidate in scan.get("candidates", []):
        if not isinstance(raw_candidate, dict):
            continue
        visible_text = _normalize_visible_text(str(raw_candidate.get("visible_text") or ""))
        publish_time, publish_time_text = _parse_facebook_publish_time(visible_text)
        if not publish_time and not publish_time_text:
            continue
        candidate = {
            **raw_candidate,
            "visible_text": visible_text,
            "matched_date_pattern": bool(publish_time),
            "parsed_publish_time": publish_time,
            "parsed_publish_time_text": publish_time_text,
        }
        existing = deduped.get(visible_text or "")
        if existing is None or _date_candidate_sort_key(candidate) > _date_candidate_sort_key(existing):
            deduped[visible_text or ""] = candidate

    candidates = sorted(deduped.values(), key=_date_candidate_sort_key, reverse=True)
    return {
        "scanned_element_count": int(scan.get("scanned_element_count") or 0),
        "candidates": candidates,
    }


def _date_candidate_sort_key(candidate: dict[str, Any]) -> tuple[int, int, int, int, int]:
    return (
        1 if candidate.get("parsed_publish_time") else 0,
        1 if candidate.get("inside_anchor") else 0,
        1 if candidate.get("before_message") else 0,
        -int(candidate.get("descendant_count") or 0),
        -int(candidate.get("text_length") or 0),
    )


def _publish_time_candidate_locators(article: Locator, post_id: str) -> Locator:
    selector = ", ".join(
        (
            f'a[href*="story_fbid={post_id}"]',
            f'a[href*="/posts/{post_id}"]',
            f'a[href*="permalink.php"][href*="{post_id}"]',
            'a[href*="story_fbid"]',
            'a[href*="/posts/"]',
            'a[href*="permalink.php"]',
            'a[role="link"]',
            '[role="link"]',
            "[aria-label]",
            "[title]",
            "span",
            "b",
            "strong",
            "time",
        )
    )
    return article.locator(selector)


def _parse_candidate_publish_time(candidate: Locator) -> tuple[str | None, str | None]:
    aria_label = candidate.get_attribute("aria-label")
    title = candidate.get_attribute("title")
    visible_text = _get_visible_text(candidate)
    return _parse_facebook_publish_time(visible_text, aria_label=aria_label, title=title)


def _get_visible_text(locator: Locator) -> str | None:
    try:
        visible_text = locator.inner_text().strip()
    except PlaywrightError:
        visible_text = ""
    visible_text = _normalize_visible_text(visible_text)
    if visible_text and not _has_hidden_node_noise(visible_text):
        return visible_text

    script = """
    (element) => {
      const hiddenByStyle = (node) => {
        let current = node.parentElement;
        while (current) {
          const style = window.getComputedStyle(current);
          if (
            current.getAttribute("aria-hidden") === "true" ||
            style.display === "none" ||
            style.visibility === "hidden" ||
            style.opacity === "0"
          ) {
            return true;
          }
          current = current.parentElement;
        }
        return false;
      };
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      const parts = [];
      let node = walker.nextNode();
      while (node) {
        if (!hiddenByStyle(node)) {
          parts.push(node.nodeValue || "");
        }
        node = walker.nextNode();
      }
      return parts.join("");
    }
    """
    try:
        fallback_text = locator.evaluate(script)
    except PlaywrightError:
        return visible_text or None
    return _normalize_visible_text(str(fallback_text or ""))


def _normalize_visible_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()
    return normalized or None


def _has_hidden_node_noise(value: str) -> bool:
    return bool(re.search(r"[A-Za-z]-[A-Za-z]|[A-Za-z]-\d|\d-\d", value))


def _parse_facebook_publish_time(
    visible_text: str | None,
    aria_label: str | None = None,
    title: str | None = None,
) -> tuple[str | None, str | None]:
    for raw_value in (aria_label, title, visible_text):
        candidate = _normalize_visible_text(raw_value)
        if not candidate:
            continue

        parsed_datetime = _parse_absolute_facebook_date(candidate)
        if parsed_datetime is not None:
            return parsed_datetime, _extract_publish_time_text(candidate)

        if _is_relative_publish_time_text(candidate):
            return None, candidate

    return None, None


def _parse_absolute_facebook_date(value: str) -> str | None:
    text = _extract_publish_time_text(value)
    formats = (
        ("%B %d, %Y at %I:%M %p", "datetime"),
        ("%b %d, %Y at %I:%M %p", "datetime"),
        ("%B %d, %Y", "date"),
        ("%b %d, %Y", "date"),
        ("%Y-%m-%d", "date"),
    )
    for date_format, output_kind in formats:
        try:
            parsed = datetime.strptime(text, date_format)
        except ValueError:
            continue
        if output_kind == "datetime":
            return parsed.isoformat(timespec="minutes")
        return parsed.date().isoformat()
    return None


def _extract_publish_time_text(value: str) -> str:
    normalized = _normalize_visible_text(value) or ""
    patterns = (
        r"\b[A-Z][a-z]+ \d{1,2}, \d{4} at \d{1,2}:\d{2} [AP]M\b",
        r"\b[A-Z][a-z]{2} \d{1,2}, \d{4} at \d{1,2}:\d{2} [AP]M\b",
        r"\b[A-Z][a-z]+ \d{1,2}, \d{4}\b",
        r"\b[A-Z][a-z]{2} \d{1,2}, \d{4}\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return match.group(0)
    return normalized


def _is_relative_publish_time_text(value: str) -> bool:
    normalized = value.strip().lower()
    return bool(
        re.fullmatch(
            f"\d+\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks)",
            normalized,
        )
        or normalized in {"yesterday", "昨天"}
    )


def _extract_metrics(article: Locator) -> tuple[int | None, int | None, int | None]:
    metrics = _extract_metric_details(article)
    return metrics["like"].count, metrics["comment"].count, metrics["share"].count


def _extract_action_count(container: Locator, rendering_role: str) -> int | None:
    return _extract_action_count_details(container, rendering_role).count


def _extract_action_count_details(container: Locator, rendering_role: str) -> ActionCountExtraction:
    role_map = {
        "like_button": "like",
        "comment_button": "comment",
        "share_button": "share",
    }
    metric_name = role_map.get(rendering_role)
    if metric_name is None:
        return ActionCountExtraction(
            button_found=False,
            raw_text=None,
            count=None,
            rejection_reason="unsupported_rendering_role",
        )
    return _extract_metric_details(container)[metric_name]


def _extract_metric_details(container: Locator) -> dict[str, ActionCountExtraction]:
    context = _action_bar_context(container)
    candidates = _metric_summary_candidates(context)
    metrics = {
        "like": _metric_from_candidates(
            "reaction",
            action_bar_found=bool(context["action_bar_found"]),
            button_found=bool(context["like_button_found"]),
            candidates=candidates,
        ),
        "comment": _metric_from_candidates(
            "comment",
            action_bar_found=bool(context["action_bar_found"]),
            button_found=bool(context["comment_button_found"]),
            candidates=candidates,
        ),
        "share": _metric_from_candidates(
            "share",
            action_bar_found=bool(context["action_bar_found"]),
            button_found=bool(context["share_button_found"]),
            candidates=candidates,
        ),
    }
    empty_state = _comment_empty_state(container)
    if metrics["comment"].count is None and empty_state["found"]:
        metrics["comment"] = ActionCountExtraction(
            button_found=metrics["comment"].button_found,
            raw_text=" | ".join(empty_state["markers"]),
            count=0,
            source="comment_empty_state",
            rejection_reason=None,
            count_state="known_zero",
        )
    return metrics


def _action_bar_context(container: Locator) -> dict[str, Any]:
    script = """
    (container) => {
      const roleNames = ['like_button', 'comment_button', 'share_button'];
      const markers = Object.fromEntries(
        roleNames.map((role) => [role, container.querySelector(`[data-ad-rendering-role="${role}"]`)])
      );
      const buttons = Object.fromEntries(
        Object.entries(markers).map(([role, marker]) => [role, marker ? marker.closest('[role="button"]') : null])
      );
      const foundButtons = Object.values(buttons).filter(Boolean);
      const commonAncestor = (nodes) => {
        if (nodes.length === 0) {
          return null;
        }
        let current = nodes[0];
        while (current && !nodes.every((node) => current.contains(node))) {
          current = current.parentElement;
        }
        return current;
      };
      const actionBar = commonAncestor(foundButtons);
      const preview = (element) => element
        ? (element.innerText || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim().slice(0, 240)
        : '';
      const depthFromBar = (node) => {
        if (!actionBar || !node) {
          return null;
        }
        let depth = 0;
        let current = node;
        while (current && current !== actionBar) {
          depth += 1;
          current = current.parentElement;
        }
        return current === actionBar ? depth : null;
      };
      const candidateElements = [];
      const addCandidate = (element, relativePosition) => {
        if (!element || !container.contains(element)) {
          return;
        }
        const text = preview(element);
        if (!text) {
          return;
        }
        candidateElements.push({ element, text, relative_position: relativePosition });
      };
      const addTextCandidate = (text, relativePosition) => {
        const normalized = (text || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
        if (!normalized) {
          return;
        }
        candidateElements.push({ element: null, text: normalized.slice(0, 240), relative_position: relativePosition });
      };
      const ariaLabel = (element) => element ? (element.getAttribute('aria-label') || '').trim() : '';
      const allRoleButtons = Array.from(container.querySelectorAll('[role="button"]'));
      const actionButtonAriaLabels = allRoleButtons
        .map(ariaLabel)
        .filter(Boolean)
        .slice(0, 20);
      const collectNearbyNumericTexts = (root) => {
        if (!root) {
          return [];
        }
        return Array.from(root.querySelectorAll('span[dir="auto"], div[dir="auto"], [aria-label]'))
          .map((node) => {
            const text = preview(node);
            const label = ariaLabel(node);
            return text || label;
          })
          .filter((text) => /\d/.test(text))
          .slice(0, 20);
      };
      if (actionBar) {
        addCandidate(actionBar, 'action_bar');
        Object.entries(buttons).forEach(([role, button]) => {
          addTextCandidate(ariaLabel(button), `${role}_button_aria_label`);
        });
        let previous = actionBar.previousElementSibling;
        for (let index = 0; previous && index < 3; index += 1) {
          addCandidate(previous, `previous_sibling_${index + 1}`);
          previous = previous.previousElementSibling;
        }
        let next = actionBar.nextElementSibling;
        for (let index = 0; next && index < 3; index += 1) {
          addCandidate(next, `next_sibling_${index + 1}`);
          next = next.nextElementSibling;
        }
        let ancestor = actionBar.parentElement;
        for (let depth = 1; ancestor && container.contains(ancestor) && depth <= 3; depth += 1) {
          Array.from(ancestor.children).forEach((child, index) => {
            if (child !== actionBar && !child.contains(actionBar)) {
              addCandidate(child, `ancestor_${depth}_child_${index}`);
            }
          });
          addCandidate(ancestor, `ancestor_${depth}`);
          ancestor = ancestor.parentElement;
        }
      } else {
        addCandidate(container, 'target_container_without_action_bar');
      }
      const uniqueCandidates = [];
      const seen = new Set();
      for (const candidate of candidateElements) {
        const key = `${candidate.relative_position}:${candidate.text}`;
        if (!seen.has(key)) {
          seen.add(key);
          uniqueCandidates.push({
            text: candidate.text,
            relative_position: candidate.relative_position,
          });
        }
      }
      return {
        action_bar_found: Boolean(actionBar),
        like_button_found: Boolean(buttons.like_button),
        comment_button_found: Boolean(buttons.comment_button),
        share_button_found: Boolean(buttons.share_button),
        action_role_count: allRoleButtons.length,
        like_button_count: container.querySelectorAll('[data-ad-rendering-role="like_button"]').length,
        comment_button_count: container.querySelectorAll('[data-ad-rendering-role="comment_button"]').length,
        share_button_count: container.querySelectorAll('[data-ad-rendering-role="share_button"]').length,
        action_bar_ancestor_depth: Math.max(
          ...Object.values(buttons).filter(Boolean).map(depthFromBar),
          0
        ),
        action_bar_text_preview: preview(actionBar),
        action_bar_match_strategy: actionBar ? 'rendering_role_common_ancestor' : null,
        action_button_aria_labels: actionButtonAriaLabels,
        candidate_action_bar_count: uniqueCandidates.length,
        candidate_action_bar_texts: uniqueCandidates.map((candidate) => candidate.text).slice(0, 10),
        nearby_numeric_texts: collectNearbyNumericTexts(actionBar),
        reaction_summary_texts: Array.from(container.querySelectorAll('[aria-label*="reaction" i], [aria-label*="reacted" i], [aria-label*="Like:" i]'))
          .map((node) => ariaLabel(node) || preview(node))
          .filter(Boolean)
          .slice(0, 10),
        summary_candidates: uniqueCandidates.slice(0, 20),
      };
    }
    """
    defaults: dict[str, Any] = {
        "action_bar_found": False,
        "like_button_found": False,
        "comment_button_found": False,
        "share_button_found": False,
        "action_role_count": 0,
        "like_button_count": 0,
        "comment_button_count": 0,
        "share_button_count": 0,
        "action_bar_ancestor_depth": None,
        "action_bar_text_preview": "",
        "action_bar_match_strategy": None,
        "action_button_aria_labels": [],
        "candidate_action_bar_count": 0,
        "candidate_action_bar_texts": [],
        "nearby_numeric_texts": [],
        "reaction_summary_texts": [],
        "summary_candidates": [],
    }
    try:
        value = container.evaluate(script)
    except PlaywrightError:
        return defaults
    if not isinstance(value, dict):
        return defaults
    return {**defaults, **value}


def _metric_summary_candidates(context: dict[str, Any]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for index, candidate in enumerate(context.get("summary_candidates") or []):
        text = _normalize_visible_text(str(candidate.get("text") or ""))
        if text is None:
            continue
        parsed = {
            "index": index,
            "text_preview": text[:240],
            "relative_position": candidate.get("relative_position"),
            "reaction_candidate": _parse_semantic_metric_text(text, "reaction"),
            "comment_candidate": _parse_semantic_metric_text(text, "comment"),
            "share_candidate": _parse_semantic_metric_text(text, "share"),
            "reaction_candidate_source": None,
            "comment_candidate_source": None,
            "share_candidate_source": None,
            "rejection_reason": None,
        }
        if parsed["reaction_candidate"] is not None:
            parsed["reaction_candidate_source"] = "reaction_summary_text"
        if parsed["comment_candidate"] is not None:
            parsed["comment_candidate_source"] = "comment_summary_text"
        if parsed["share_candidate"] is not None:
            parsed["share_candidate_source"] = "share_summary_text"
        ordered_counts = _parse_ordered_action_bar_counts(text, context, parsed["relative_position"])
        if ordered_counts is not None:
            parsed["reaction_candidate"] = ordered_counts[0]
            parsed["comment_candidate"] = ordered_counts[1]
            parsed["share_candidate"] = ordered_counts[2]
            source = "action_bar_ordered_summary" if ordered_counts[2] is not None else "action_bar_ordered_partial_summary"
            parsed["reaction_candidate_source"] = source
            parsed["comment_candidate_source"] = source
            parsed["share_candidate_source"] = source if ordered_counts[2] is not None else None
        if (
            parsed["reaction_candidate"] is None
            and parsed["comment_candidate"] is None
            and parsed["share_candidate"] is None
        ):
            parsed["rejection_reason"] = "no_semantic_metric_text"
        diagnostics.append(parsed)
    return diagnostics


def _metric_from_candidates(
    metric_name: str,
    action_bar_found: bool,
    button_found: bool,
    candidates: list[dict[str, Any]],
) -> ActionCountExtraction:
    candidate_key = f"{metric_name}_candidate"
    source_map = {
        "reaction": "reaction_summary_text",
        "comment": "comment_summary_text",
        "share": "share_summary_text",
    }
    for candidate in candidates:
        count = candidate.get(candidate_key)
        if isinstance(count, int):
            source = candidate.get(f"{metric_name}_candidate_source") or source_map[metric_name]
            return ActionCountExtraction(
                button_found=button_found,
                raw_text=str(candidate.get("text_preview") or ""),
                count=count,
                source=str(source),
                rejection_reason=None,
                count_state="known_zero" if count == 0 else "known_value",
            )
    ambiguous_numeric = any(
        re.search(r"\d", str(candidate.get("text_preview") or ""))
        and candidate.get("rejection_reason") == "no_semantic_metric_text"
        for candidate in candidates
    )
    if ambiguous_numeric:
        return ActionCountExtraction(
            button_found=button_found,
            raw_text=None,
            count=None,
            source=None,
            rejection_reason="ambiguous_unassigned_numeric_text",
            count_state="unknown",
        )
    if action_bar_found and button_found:
        return ActionCountExtraction(
            button_found=button_found,
            raw_text=None,
            count=None,
            source=None,
            rejection_reason="button_found_but_no_numeric_summary",
            count_state="unknown",
        )
    return ActionCountExtraction(
        button_found=button_found,
        raw_text=None,
        count=None,
        source=None,
        rejection_reason="button_found_but_no_numeric_summary" if button_found else "button_not_found",
        count_state="unknown",
    )


def _parse_semantic_metric_text(value: str | None, metric_name: str) -> int | None:
    text = _normalize_visible_text(value)
    if text is None:
        return None
    word_groups = {
        "reaction": ("reactions?", "likes?"),
        "comment": ("comments?",),
        "share": ("shares?",),
    }
    words = word_groups[metric_name]
    number_pattern = r"(\d[\d,\s]*(?:\.\d+)?\s*(?:[KkMmBb万亿億])?)"
    word_pattern = "|".join(words)
    patterns = (
        rf"{number_pattern}\s*(?:{word_pattern})\b",
        rf"\b(?:{word_pattern})\s*[:：]?\s*{number_pattern}",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        raw_number = match.group(1)
        parsed = parse_metric_count(raw_number)
        if parsed is not None:
            return parsed
    return None


def _parse_ordered_action_bar_counts(
    value: str | None,
    context: dict[str, Any],
    relative_position: Any = None,
) -> tuple[int | None, int | None, int | None] | None:
    if not (
        context.get("action_bar_found")
        and context.get("like_button_found")
        and context.get("comment_button_found")
        and context.get("share_button_found")
    ):
        return None
    if relative_position != "action_bar":
        return None
    text = _normalize_visible_text(value)
    if text is None:
        return None
    if re.search(r"\b(reactions?|likes?|comments?|shares?)\b", text, flags=re.IGNORECASE):
        return None
    number_matches = re.findall(r"\d[\d,]*(?:\.\d+)?\s*(?:[KkMmBb万亿億])?", text)
    numbers = [parse_metric_count(match) for match in number_matches]
    parsed_numbers = [number for number in numbers if number is not None]
    if len(" ".join(text.split())) > 40:
        return None
    if len(parsed_numbers) == 3:
        return parsed_numbers[0], parsed_numbers[1], parsed_numbers[2]
    if len(parsed_numbers) == 2:
        return parsed_numbers[0], parsed_numbers[1], None
    return None


def _parse_action_count_text(value: str | None) -> int | None:
    text = _normalize_visible_text(value)
    if text is None:
        return None
    if not re.fullmatch(r"\d[\d,\s]*(?:\.\d+)?\s*(?:[KkMmBb万亿億])?", text):
        return None
    return parse_metric_count(text)


def _comment_empty_state(container: Locator | None) -> dict[str, Any]:
    empty = {
        "found": False,
        "comments_disabled": False,
        "source": None,
        "markers": [],
    }
    if container is None:
        return empty
    try:
        text = container.inner_text(timeout=1_000)
    except PlaywrightError:
        return empty
    normalized = " ".join(text.split()).lower()
    disabled_markers = (
        "turned off commenting for this post",
        "comments are turned off",
        "commenting has been turned off",
    )
    for marker in disabled_markers:
        if marker in normalized:
            return {
                "found": True,
                "comments_disabled": True,
                "source": "turned_off_commenting_text",
                "markers": [marker],
            }

    no_comment_markers = (
        "no comments yet",
        "be the first to comment",
        "no comments",
    )
    matched_markers = [marker for marker in no_comment_markers if marker in normalized]
    if matched_markers:
        return {
            "found": True,
            "comments_disabled": False,
            "source": "no_comments_text",
            "markers": matched_markers,
        }
    return empty


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
    target_container: Locator | None = None,
    selected_container_index: int | None = None,
    selected_container_metrics: dict[str, int | bool] | None = None,
    selected_container_strategy: str | None = None,
    selected_message_index: int | None = None,
    selected_ancestor_depth: int | None = None,
    selected_container_score: int | None = None,
    selected_container_reasons: list[str] | None = None,
    selected_publish_time: str | None = None,
    selected_publish_time_text: str | None = None,
) -> dict[str, Any]:
    DEBUG_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    matched_container_count = _matched_semantic_container_count(page, post_id)
    publish_time_scan = _publish_time_candidate_diagnostics(target_container)
    action_count_diagnostics = _action_count_diagnostics(target_container)
    if selected_publish_time is None and selected_publish_time_text is None:
        selected_candidate = _select_best_publish_time_candidate(publish_time_scan["candidates"])
        if selected_candidate is not None:
            selected_publish_time = _optional_string(selected_candidate.get("parsed_publish_time"))
            selected_publish_time_text = _optional_string(selected_candidate.get("parsed_publish_time_text"))
    selected_container_diagnostics = _selected_container_diagnostics(
        target_container,
        post_id,
        selected_container_index=selected_container_index,
        selected_container_metrics=selected_container_metrics,
    )
    if selected_ancestor_depth is not None:
        selected_container_diagnostics["selected_container_depth"] = selected_ancestor_depth
    publish_time_strict_match_found = len(publish_time_scan["candidates"]) > 0
    publish_time_selected = selected_publish_time is not None or selected_publish_time_text is not None
    title_context = _post_title_context(_safe_page_title(page))
    article_candidates = _article_candidate_diagnostics(page, post_id, title_context)
    message_candidates = _message_candidate_diagnostics(page, title_context)
    ancestor_candidates = _message_ancestor_candidate_diagnostics(page, title_context)
    diagnostics: dict[str, Any] = {
        "input_url": input_url,
        "final_url": _safe_page_url(page),
        "page_title": _safe_page_title(page),
        "target_post_id": post_id,
        "title_match_context": title_context,
        "article_count": _safe_count(page.locator("article")),
        "role_article_count": _safe_count(page.locator(ROLE_ARTICLE_SELECTOR)),
        "dialog_count": _safe_count(page.locator('[role="dialog"]')),
        "target_link_count": _safe_count(page.locator(f'a[href*="{post_id}"]')),
        "strong_target_link_count": _safe_count(_target_post_link_locator(page, post_id)),
        "target_comment_link_count": _safe_count(
            page.locator(
                f'a[href*="{post_id}"][href*="comment_id="], '
                f'a[href*="{post_id}"][href*="comment_id%3D"], '
                f'a[href*="{post_id}"][href*="reply_comment_id="]'
            )
        ),
        "target_id_link_count": _safe_count(page.locator(f'a[href*="{post_id}"]')),
        "story_fbid_link_count": _safe_count(page.locator(f'a[href*="story_fbid={post_id}"]')),
        "posts_path_link_count": _safe_count(page.locator(f'a[href*="/posts/{post_id}"]')),
        "matched_article_count": _safe_count(page.locator(ROLE_ARTICLE_SELECTOR).filter(has=_target_post_link_locator(page, post_id))),
        "matched_container_count": matched_container_count,
        "container_candidate_count": _container_candidate_count(page, post_id),
        "article_candidates": article_candidates,
        "message_candidates": message_candidates,
        "message_ancestor_candidates": ancestor_candidates,
        **selected_container_diagnostics,
        "selected_message_index": selected_message_index,
        "selected_ancestor_depth": selected_ancestor_depth,
        "selected_container_score": selected_container_score,
        "selected_container_reasons": selected_container_reasons or [],
        "container_rejection_reason": None
        if target_container is not None
        else _message_ancestor_rejection_reason(message_candidates, ancestor_candidates),
        "message_locator_count": _safe_count(page.locator(", ".join(MESSAGE_SELECTORS))),
        "time_candidate_count": _safe_count(page.locator("time, a[aria-label]")),
        "time_locator_count": _safe_count(page.locator("time, a[aria-label]")),
        "publish_time_locator_present": _safe_count(page.locator("time, a[aria-label], [aria-labelledby], [title]")) > 0,
        "publish_time_strict_match_found": publish_time_strict_match_found,
        "publish_time_selected": publish_time_selected,
        "publish_time_scanned_element_count": publish_time_scan["scanned_element_count"],
        "publish_time_strict_match_count": len(publish_time_scan["candidates"]),
        "publish_time_candidate_count": len(publish_time_scan["candidates"]),
        "publish_time_candidates": publish_time_scan["candidates"][:10],
        "selected_publish_time_text": selected_publish_time_text,
        "selected_publish_time": selected_publish_time,
        "author_candidate_count": _safe_count(page.locator('a[role="link"], a[href]')),
        "reaction_candidate_count": _safe_count(page.locator(_metric_candidate_selector(("reaction", "like")))),
        "comment_candidate_count": _safe_count(page.locator(_metric_candidate_selector(("comment",)))),
        "share_candidate_count": _safe_count(page.locator(_metric_candidate_selector(("share",)))),
        **action_count_diagnostics,
        "container_match_strategy": selected_container_strategy
        or _container_match_strategy(page, post_id, matched_container_count),
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


def _action_count_diagnostics(target_container: Locator | None) -> dict[str, Any]:
    empty = {
        "action_bar_found": False,
        "action_role_count": 0,
        "like_button_count": 0,
        "comment_button_count": 0,
        "share_button_count": 0,
        "action_bar_ancestor_depth": None,
        "action_bar_text_preview": "",
        "action_bar_match_strategy": None,
        "action_button_aria_labels": [],
        "candidate_action_bar_count": 0,
        "candidate_action_bar_texts": [],
        "nearby_numeric_texts": [],
        "reaction_summary_count": 0,
        "reaction_summary_texts": [],
        "like_button_found": False,
        "like_count_raw": None,
        "like_count": None,
        "like_count_state": "unknown",
        "like_count_source": None,
        "like_count_rejection_reason": "button_not_found",
        "comment_button_found": False,
        "comment_count_raw": None,
        "comment_count": None,
        "comment_count_state": "unknown",
        "comment_count_source": None,
        "comment_count_rejection_reason": "button_not_found",
        "share_button_found": False,
        "share_count_raw": None,
        "share_count": None,
        "share_count_state": "unknown",
        "share_count_source": None,
        "share_count_rejection_reason": "button_not_found",
        "metric_summary_candidates": [],
        "comments_disabled": False,
        "comment_empty_state_found": False,
        "comment_empty_state_source": None,
        "comment_empty_state_markers": [],
    }
    if target_container is None:
        return empty

    context = _action_bar_context(target_container)
    summary_candidates = _metric_summary_candidates(context)
    metrics = _extract_metric_details(target_container)
    like = metrics["like"]
    comment = metrics["comment"]
    share = metrics["share"]
    empty_state = _comment_empty_state(target_container)
    return {
        "action_bar_found": bool(context["action_bar_found"]),
        "action_role_count": context["action_role_count"],
        "like_button_count": context["like_button_count"],
        "comment_button_count": context["comment_button_count"],
        "share_button_count": context["share_button_count"],
        "action_bar_ancestor_depth": context["action_bar_ancestor_depth"],
        "action_bar_text_preview": context["action_bar_text_preview"],
        "action_bar_match_strategy": context["action_bar_match_strategy"],
        "action_button_aria_labels": context["action_button_aria_labels"],
        "candidate_action_bar_count": context["candidate_action_bar_count"],
        "candidate_action_bar_texts": context["candidate_action_bar_texts"],
        "nearby_numeric_texts": context["nearby_numeric_texts"],
        "reaction_summary_count": len(context["reaction_summary_texts"]),
        "reaction_summary_texts": context["reaction_summary_texts"],
        "like_button_found": like.button_found,
        "like_count_raw": like.raw_text,
        "like_count": like.count,
        "like_count_state": like.count_state,
        "like_count_source": like.source,
        "like_count_rejection_reason": like.rejection_reason,
        "comment_button_found": comment.button_found,
        "comment_count_raw": comment.raw_text,
        "comment_count": comment.count,
        "comment_count_state": comment.count_state,
        "comment_count_source": comment.source,
        "comment_count_rejection_reason": comment.rejection_reason,
        "share_button_found": share.button_found,
        "share_count_raw": share.raw_text,
        "share_count": share.count,
        "share_count_state": share.count_state,
        "share_count_source": share.source,
        "share_count_rejection_reason": share.rejection_reason,
        "metric_summary_candidates": summary_candidates,
        "comment_empty_state_found": bool(empty_state["found"]),
        "comments_disabled": bool(empty_state["comments_disabled"]),
        "comment_empty_state_source": empty_state["source"],
        "comment_empty_state_markers": list(empty_state["markers"]),
    }


def _article_candidate_diagnostics(
    page: Page,
    post_id: str,
    title_context: dict[str, str | None],
    limit: int = 10,
) -> list[dict[str, Any]]:
    articles = page.locator(ROLE_ARTICLE_SELECTOR)
    summaries: list[dict[str, Any]] = []
    for index in range(min(max(_safe_count(articles), 0), limit)):
        summaries.append(_article_diagnostic_summary(articles.nth(index), index, post_id, title_context))
    return summaries


def _publish_time_candidate_diagnostics(
    target_container: Locator | None,
    limit: int = 10,
) -> dict[str, Any]:
    if target_container is None:
        return {"scanned_element_count": 0, "candidates": []}
    scan = _visible_publish_time_candidates(target_container)
    return {
        "scanned_element_count": scan["scanned_element_count"],
        "candidates": scan["candidates"][:limit],
    }


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


def _container_candidate_count(page: Page, post_id: str) -> int:
    total = 0
    target_links = _target_post_link_locator(page, post_id)
    for selector in TARGET_CONTAINER_SELECTORS:
        total += max(_safe_count(page.locator(selector).filter(has=target_links)), 0)
    return total


def _selected_container_diagnostics(
    target_container: Locator | None,
    post_id: str,
    selected_container_index: int | None = None,
    selected_container_metrics: dict[str, int | bool] | None = None,
) -> dict[str, int | None]:
    if target_container is None:
        return {
            "selected_container_index": None,
            "selected_container_depth": None,
            "selected_container_message_count": None,
            "selected_container_author_count": None,
            "selected_container_text_length": None,
            "selected_container_descendant_count": None,
        }
    metrics = selected_container_metrics or _container_metrics(target_container, post_id)
    return {
        "selected_container_index": selected_container_index,
        "selected_container_depth": int(metrics["depth"]),
        "selected_container_message_count": int(metrics["messageCount"]),
        "selected_container_author_count": int(metrics["authorCount"]),
        "selected_container_text_length": int(metrics["textLength"]),
        "selected_container_descendant_count": int(metrics["descendantCount"]),
    }


def _container_match_strategy(page: Page, post_id: str, matched_container_count: int) -> str | None:
    if matched_container_count > 0:
        return "smallest_semantic_target_container"
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
    if host and host not in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        return False
    path_segments = [segment for segment in parsed.path.strip("/").split("/") if segment]
    if _is_facebook_group_user_path(path_segments):
        return True
    first_segment = path_segments[0] if path_segments else ""
    if not first_segment or first_segment in {"permalink.php", "story.php", "posts", "groups", "reel", "watch"}:
        return False
    return True


def _normalize_facebook_profile_url(href: str) -> str:
    parsed = urlparse(href)
    path_segments = [segment for segment in parsed.path.strip("/").split("/") if segment]
    if _is_facebook_group_user_path(path_segments):
        path = "/".join(path_segments[:4])
    else:
        path = parsed.path.strip("/")
    return urlunparse(("https", "www.facebook.com", f"/{path}".rstrip("/"), "", "", ""))


def _is_facebook_group_user_path(path_segments: list[str]) -> bool:
    return (
        len(path_segments) >= 4
        and path_segments[0] == "groups"
        and bool(path_segments[1])
        and path_segments[2] == "user"
        and bool(path_segments[3])
    )


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
