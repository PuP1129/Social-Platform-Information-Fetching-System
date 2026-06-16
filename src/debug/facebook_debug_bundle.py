from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

DEFAULT_FACEBOOK_DEBUG_BUNDLE_ROOT = Path("output") / "debug" / "facebook_batch"
MAX_OUTER_HTML_LENGTH = 8_000
MAX_TEXT_PREVIEW_LENGTH = 240
MAX_CANDIDATES_PER_KIND = 30


def facebook_debug_bundle_dir(
    root: Path = DEFAULT_FACEBOOK_DEBUG_BUNDLE_ROOT,
    index: int | None = None,
    post_id: str | None = None,
) -> Path:
    prefix = f"{index:04d}" if index is not None else "single"
    return root / f"{prefix}_{_safe_filename_part(post_id or 'unknown')}"


def should_save_facebook_debug_bundle(collection_status: str | None, force: bool = False) -> bool:
    return force or collection_status in {"failed", "partial"}


def start_facebook_trace(context: Any) -> str | None:
    try:
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
    except Exception as exc:
        return str(exc)
    return None


def stop_facebook_trace(context: Any, trace_path: Path | None = None) -> str | None:
    try:
        if trace_path is None:
            context.tracing.stop()
        else:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            context.tracing.stop(path=str(trace_path))
    except Exception as exc:
        return str(exc)
    return None


def save_facebook_debug_bundle(
    page: Page | None,
    bundle_dir: Path,
    diagnostics: dict[str, Any] | None = None,
    trace_error: str | None = None,
) -> dict[str, str | None]:
    """Save non-secret debug artifacts without raising over extraction errors."""
    bundle_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str | None] = {
        "debug_bundle_path": str(bundle_dir),
        "debug_page_html_path": str(bundle_dir / "page.html"),
        "debug_screenshot_path": str(bundle_dir / "page.png"),
        "debug_aria_snapshot_path": str(bundle_dir / "aria_snapshot.yaml"),
        "debug_diagnostics_path": str(bundle_dir / "diagnostics.json"),
        "debug_candidate_nodes_path": str(bundle_dir / "candidate_nodes.json"),
        "debug_trace_path": str(bundle_dir / "trace.zip") if (bundle_dir / "trace.zip").exists() else None,
    }
    errors: dict[str, str] = {}

    if page is None:
        errors["page"] = "page was not available"
    else:
        _write_page_html(page, bundle_dir / "page.html", errors)
        _write_screenshot(page, bundle_dir / "page.png", errors)
        _write_aria_snapshot(page, bundle_dir / "aria_snapshot.yaml", errors)
        _write_candidate_nodes(page, bundle_dir / "candidate_nodes.json", errors)

    diagnostics_payload = dict(diagnostics or {})
    diagnostics_payload["debug_bundle_path"] = str(bundle_dir)
    if trace_error:
        diagnostics_payload["trace_error"] = trace_error
    if errors:
        diagnostics_payload["debug_bundle_errors"] = errors
    _write_json(bundle_dir / "diagnostics.json", diagnostics_payload, errors, "diagnostics")

    if (bundle_dir / "trace.zip").exists():
        paths["debug_trace_path"] = str(bundle_dir / "trace.zip")
    return paths


def collect_candidate_nodes(page: Page) -> dict[str, list[dict[str, Any]]]:
    script = """
    ({ maxOuterHtmlLength, maxTextPreviewLength, maxCandidatesPerKind }) => {
      const selectors = {
        message_candidates: '[data-ad-preview="message"], [data-ad-comet-preview="message"]',
        article_candidates: '[role="article"], article',
        author_candidates: 'a[role="link"], a[href]',
        time_candidates: 'time, a[aria-label], [aria-labelledby], [title]',
        action_bar_candidates: '[data-ad-rendering-role="like_button"], [data-ad-rendering-role="comment_button"], [data-ad-rendering-role="share_button"], [role="button"]',
        target_post_links: 'a[href*="/posts/"], a[href*="story_fbid="], a[href*="permalink.php"]'
      };
      const visible = (element) => {
        const style = window.getComputedStyle(element);
        return style && style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
      };
      const clean = (text) => (text || '').replace(/\\s+/g, ' ').trim();
      const preview = (text, limit) => clean(text).slice(0, limit);
      const attrs = (element) => ({
        role: element.getAttribute('role'),
        'aria-label': element.getAttribute('aria-label'),
        'data-ad-preview': element.getAttribute('data-ad-preview'),
        'data-ad-comet-preview': element.getAttribute('data-ad-comet-preview'),
        'data-ad-rendering-role': element.getAttribute('data-ad-rendering-role'),
        href: element.getAttribute('href')
      });
      const count = (element, selector) => element.querySelectorAll(selector).length;
      const chain = (element) => {
        const result = [];
        let current = element.parentElement;
        let depth = 1;
        while (current && depth <= 8) {
          result.push({
            depth,
            tag: current.tagName ? current.tagName.toLowerCase() : null,
            role: current.getAttribute ? current.getAttribute('role') : null,
            text_preview: preview(current.innerText, maxTextPreviewLength),
            message_count: count(current, selectors.message_candidates),
            link_count: count(current, 'a[href]'),
            time_candidate_count: count(current, 'time, a[aria-label], [aria-labelledby], [title]'),
            action_button_count: count(current, '[data-ad-rendering-role], [role="button"]')
          });
          current = current.parentElement;
          depth += 1;
        }
        return result;
      };
      const box = (element) => {
        const rect = element.getBoundingClientRect();
        return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
      };
      const nodePayload = (element, index) => ({
        index,
        text_preview: preview(element.innerText || element.textContent, maxTextPreviewLength),
        visible: visible(element),
        outer_html: (element.outerHTML || '').slice(0, maxOuterHtmlLength),
        bounding_box: box(element),
        attributes: attrs(element),
        ancestor_chain: chain(element)
      });
      const payload = {};
      for (const [name, selector] of Object.entries(selectors)) {
        payload[name] = Array.from(document.querySelectorAll(selector))
          .slice(0, maxCandidatesPerKind)
          .map((element, index) => nodePayload(element, index));
      }
      return payload;
    }
    """
    try:
        value = page.evaluate(
            script,
            {
                "maxOuterHtmlLength": MAX_OUTER_HTML_LENGTH,
                "maxTextPreviewLength": MAX_TEXT_PREVIEW_LENGTH,
                "maxCandidatesPerKind": MAX_CANDIDATES_PER_KIND,
            },
        )
    except Exception as exc:
        return {"collection_error": [{"message": str(exc)}]}
    if not isinstance(value, dict):
        return {"collection_error": [{"message": "candidate node script did not return an object"}]}
    return _sanitize_json_value(value)


def _write_page_html(page: Page, path: Path, errors: dict[str, str]) -> None:
    try:
        path.write_text(_sanitize_html(page.content()), encoding="utf-8")
    except Exception as exc:
        errors["page.html"] = str(exc)


def _write_screenshot(page: Page, path: Path, errors: dict[str, str]) -> None:
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception as exc:
        errors["page.png"] = str(exc)


def _write_aria_snapshot(page: Page, path: Path, errors: dict[str, str]) -> None:
    try:
        snapshot = page.locator("body").aria_snapshot()
        path.write_text(str(snapshot), encoding="utf-8")
    except Exception as exc:
        errors["aria_snapshot.yaml"] = str(exc)


def _write_candidate_nodes(page: Page, path: Path, errors: dict[str, str]) -> None:
    try:
        _write_json(path, collect_candidate_nodes(page), errors, "candidate_nodes")
    except Exception as exc:
        errors["candidate_nodes.json"] = str(exc)


def _write_json(path: Path, payload: Any, errors: dict[str, str], key: str) -> None:
    try:
        path.write_text(json.dumps(_sanitize_json_value(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        errors[f"{key}.json"] = str(exc)


def _sanitize_html(value: str) -> str:
    sanitized = re.sub(
        r'(<input\b[^>]*type=["\']?password["\']?[^>]*\bvalue=)(["\']).*?\2',
        r"\1\2[REDACTED]\2",
        value,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r'(<input\b[^>]*(?:name|autocomplete)=["\']?(?:email|username|current-password|password)["\']?[^>]*\bvalue=)(["\']).*?\2',
        r"\1\2[REDACTED]\2",
        sanitized,
        flags=re.IGNORECASE,
    )
    return sanitized


def _sanitize_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_html(value)
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_json_value(item) for key, item in value.items()}
    return value


def _safe_filename_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    return cleaned[:80] or "unknown"
