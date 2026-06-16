from __future__ import annotations

import json
import re
import shutil
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from src.debug.facebook_debug_bundle import (
    facebook_debug_bundle_dir,
    save_facebook_debug_bundle,
    should_save_facebook_debug_bundle,
    start_facebook_trace,
    stop_facebook_trace,
)
from src.extractors.facebook_post import (
    FAILURE_DIAGNOSTICS_PATH,
    FAILURE_HTML_PATH,
    FAILURE_SCREENSHOT_PATH,
    FacebookLoginRequiredError,
    FacebookPostExtractionError,
    FacebookPostNotFoundError,
    FacebookPostUnavailableError,
    extract_facebook_post_from_page,
)
from src.filters.post_url import classify_post_url

DEFAULT_BATCH_OUTPUT_PATH = Path("output") / "facebook_posts.jsonl"
DEFAULT_BATCH_SUMMARY_PATH = Path("output") / "facebook_batch_summary.json"
DEFAULT_BATCH_DIAGNOSTICS_DIR = Path("output") / "debug" / "facebook_batch"
LOGIN_WALL_STOP_THRESHOLD = 2
TRACKING_QUERY_PARAMS = {
    "__cft__",
    "__tn__",
    "comment_id",
    "eid",
    "fbclid",
    "mibextid",
    "rdid",
}


@dataclass(frozen=True)
class FacebookBatchItem:
    input_url: str
    original_url: str
    canonical_url: str | None
    post_id: str | None
    input_metadata: dict[str, Any]
    skip_reason: str | None = None


def run_facebook_batch(
    input_path: Path,
    storage_state_path: str | Path | None,
    output_path: Path = DEFAULT_BATCH_OUTPUT_PATH,
    summary_path: Path = DEFAULT_BATCH_SUMMARY_PATH,
    diagnostics_dir: Path = DEFAULT_BATCH_DIAGNOSTICS_DIR,
    limit: int | None = None,
    delay_seconds: float = 2.0,
    headless: bool = False,
    resume: bool = False,
    timeout_ms: int = 30_000,
    extractor: Callable[[Any, str], dict[str, Any]] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    playwright_factory: Callable[[], Any] = sync_playwright,
    save_debug_bundle: bool = False,
) -> dict[str, Any]:
    """Collect Facebook posts sequentially into JSONL."""
    storage_state = _validate_storage_state_path(storage_state_path)
    items = prepare_facebook_batch_items(input_path, limit=limit)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostics_dir.mkdir(parents=True, exist_ok=True)

    active_output_path = output_path
    if resume:
        processed_urls = _read_processed_urls(output_path)
    else:
        processed_urls = set()
        active_output_path = _temporary_batch_output_path(output_path)
        active_output_path.write_text("", encoding="utf-8")

    started_at = _utc_now()
    current_records: list[dict[str, Any]] = []
    failure_types: Counter[str] = Counter()
    login_wall_streak = 0
    processed_this_run = 0
    skipped_this_run = 0

    playwright = None
    browser = None
    context = None
    try:
        if extractor is None:
            playwright = playwright_factory().start()
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=str(storage_state))

        for index, item in enumerate(items, start=1):
            if item.skip_reason is not None:
                record = _skipped_record(item, item.skip_reason)
                _append_jsonl_record(active_output_path, record)
                current_records.append(record)
                skipped_this_run += 1
                failure_types[item.skip_reason] += 1
                continue

            resume_key = item.canonical_url or item.input_url
            if resume_key in processed_urls:
                skipped_this_run += 1
                failure_types["resume_already_processed"] += 1
                continue

            if login_wall_streak >= LOGIN_WALL_STOP_THRESHOLD:
                record = _skipped_record(item, "batch_stopped_due_to_login_wall")
                _append_jsonl_record(active_output_path, record)
                current_records.append(record)
                skipped_this_run += 1
                failure_types["batch_stopped_due_to_login_wall"] += 1
                continue

            record, error_type = _process_supported_item(
                index=index,
                item=item,
                context=context,
                extractor=extractor,
                diagnostics_dir=diagnostics_dir,
                timeout_ms=timeout_ms,
                save_debug_bundle=save_debug_bundle,
            )
            _append_jsonl_record(active_output_path, record)
            current_records.append(record)
            processed_this_run += 1
            processed_urls.add(resume_key)

            if record.get("collection_status") == "failed" and error_type:
                failure_types[error_type] += 1
                if error_type == "FacebookLoginRequiredError":
                    login_wall_streak += 1
                else:
                    login_wall_streak = 0
            else:
                login_wall_streak = 0

            if delay_seconds > 0 and index < len(items):
                sleep_fn(delay_seconds)
    finally:
        _close_quietly(context)
        _close_quietly(browser)
        if playwright is not None:
            playwright.stop()

    if not resume:
        active_output_path.replace(output_path)

    total_records = _read_jsonl_records(output_path)
    summary = _build_summary(
        started_at=started_at,
        input_count=len(items),
        current_records=current_records,
        total_records=total_records,
        output_path=output_path,
        diagnostics_dir=diagnostics_dir,
        failure_types=failure_types,
        processed_this_run=processed_this_run,
        skipped_this_run=skipped_this_run,
        resume=resume,
    )
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def prepare_facebook_batch_items(input_path: Path, limit: int | None = None) -> list[FacebookBatchItem]:
    raw_items = load_facebook_batch_input(input_path)
    prepared: list[FacebookBatchItem] = []
    seen: set[tuple[str, str]] = set()
    supported_count = 0

    for raw in raw_items:
        cleaned_url = clean_facebook_batch_url(raw["url"])
        classified = classify_post_url(cleaned_url)
        metadata = dict(raw["metadata"])
        metadata.setdefault("original_url", raw["url"])

        if classified is None or classified["platform"] != "facebook":
            prepared.append(
                FacebookBatchItem(
                    input_url=cleaned_url,
                    original_url=raw["url"],
                    canonical_url=None,
                    post_id=None,
                    input_metadata=metadata,
                    skip_reason="unsupported_facebook_url",
                )
            )
            continue

        identity = (classified["platform"], classified["post_id"])
        if identity in seen:
            prepared.append(
                FacebookBatchItem(
                    input_url=classified["canonical_url"],
                    original_url=raw["url"],
                    canonical_url=classified["canonical_url"],
                    post_id=classified["post_id"],
                    input_metadata=metadata,
                    skip_reason="duplicate_facebook_post",
                )
            )
            continue

        if limit is not None and supported_count >= limit:
            break

        seen.add(identity)
        supported_count += 1
        prepared.append(
            FacebookBatchItem(
                input_url=classified["canonical_url"],
                original_url=raw["url"],
                canonical_url=classified["canonical_url"],
                post_id=classified["post_id"],
                input_metadata=metadata,
            )
        )

    return prepared


def load_facebook_batch_input(input_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        if isinstance(payload.get("posts"), list):
            payload_items = payload["posts"]
        elif isinstance(payload.get("urls"), list):
            payload_items = payload["urls"]
        else:
            raise ValueError("Facebook batch input object must contain a posts or urls list.")
    elif isinstance(payload, list):
        payload_items = payload
    else:
        raise ValueError("Facebook batch input must be a JSON list or an object with posts/urls.")

    items: list[dict[str, Any]] = []
    for index, item in enumerate(payload_items):
        if isinstance(item, str):
            url = item
            metadata: dict[str, Any] = {}
        elif isinstance(item, dict):
            url = _url_from_input_object(item)
            metadata = {key: value for key, value in item.items() if key not in {"url", "canonical_url", "original_url"}}
        else:
            raise ValueError(f"Facebook batch input item #{index + 1} must be a string or object.")

        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"Facebook batch input item #{index + 1} does not contain a usable URL.")
        items.append({"url": url.strip(), "metadata": metadata})
    return items


def clean_facebook_batch_url(url: str) -> str:
    parsed = urlparse(url.strip())
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if key not in TRACKING_QUERY_PARAMS
    ]
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path.rstrip("/"),
            parsed.params,
            urlencode(query_pairs, doseq=True),
            "",
        )
    )


def _process_supported_item(
    index: int,
    item: FacebookBatchItem,
    context: Any,
    extractor: Callable[[Any, str], dict[str, Any]] | None,
    diagnostics_dir: Path,
    timeout_ms: int,
    save_debug_bundle: bool = False,
) -> tuple[dict[str, Any], str | None]:
    attempts = 0
    while attempts < 2:
        attempts += 1
        page = None
        trace_started = False
        trace_error = None
        bundle_dir = facebook_debug_bundle_dir(diagnostics_dir, index=index, post_id=item.post_id)
        try:
            if extractor is None:
                trace_error = start_facebook_trace(context)
                trace_started = trace_error is None
                page = context.new_page()
                payload = extract_facebook_post_from_page(
                    page=page,
                    url=item.input_url,
                    timeout_ms=timeout_ms,
                    authenticated=True,
                )
            else:
                payload = extractor(context, item.input_url)

            payload = _enrich_success_record(payload, item)
            should_save_bundle = (
                extractor is None
                and should_save_facebook_debug_bundle(
                    str(payload.get("collection_status")),
                    force=save_debug_bundle,
                )
            )
            if trace_started:
                trace_error = stop_facebook_trace(context, bundle_dir / "trace.zip" if should_save_bundle else None)
                trace_started = False
            if should_save_bundle:
                payload.update(
                    save_facebook_debug_bundle(
                        page,
                        bundle_dir,
                        diagnostics=_read_json_file(FAILURE_DIAGNOSTICS_PATH),
                        trace_error=trace_error,
                    )
                )
            diagnostics_paths = copy_latest_facebook_diagnostics(index, payload.get("post_id") or item.post_id, diagnostics_dir)
            payload.update(diagnostics_paths)
            return payload, None
        except Exception as exc:
            if attempts == 1 and _is_retryable_exception(exc):
                if trace_started:
                    stop_facebook_trace(context)
                    trace_started = False
                continue
            record = _failed_record(item, exc)
            if extractor is None and page is not None:
                if trace_started:
                    trace_error = stop_facebook_trace(context, bundle_dir / "trace.zip")
                    trace_started = False
                diagnostics = exc.diagnostics if isinstance(exc, FacebookPostExtractionError) else None
                if diagnostics is None:
                    diagnostics = _read_json_file(FAILURE_DIAGNOSTICS_PATH)
                record.update(save_facebook_debug_bundle(page, bundle_dir, diagnostics=diagnostics, trace_error=trace_error))
            diagnostics_paths = copy_latest_facebook_diagnostics(index, item.post_id, diagnostics_dir)
            record.update(diagnostics_paths)
            return record, type(exc).__name__
        finally:
            if trace_started:
                stop_facebook_trace(context)
            _close_quietly(page)

    return _failed_record(item, FacebookPostExtractionError("Facebook batch item failed unexpectedly.")), "FacebookPostExtractionError"


def copy_latest_facebook_diagnostics(index: int, post_id: str | None, diagnostics_dir: Path) -> dict[str, str | None]:
    safe_id = _safe_filename_part(post_id or "unknown")
    prefix = f"{index:04d}_{safe_id}"
    mapping = {
        "diagnostics_path": (FAILURE_DIAGNOSTICS_PATH, diagnostics_dir / f"{prefix}_diagnostics.json"),
        "html_path": (FAILURE_HTML_PATH, diagnostics_dir / f"{prefix}_page.html"),
        "screenshot_path": (FAILURE_SCREENSHOT_PATH, diagnostics_dir / f"{prefix}_page.png"),
    }
    copied: dict[str, str | None] = {}
    for key, (source, target) in mapping.items():
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied[key] = str(target)
        else:
            copied[key] = None
    return copied


def _enrich_success_record(payload: dict[str, Any], item: FacebookBatchItem) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["input_metadata"] = item.input_metadata
    enriched["batch_input_url"] = item.original_url
    return enriched


def _failed_record(item: FacebookBatchItem, exc: Exception) -> dict[str, Any]:
    return {
        "platform": "facebook",
        "post_id": item.post_id,
        "input_url": item.input_url,
        "canonical_url": item.canonical_url,
        "collection_status": "failed",
        "error": {
            "type": type(exc).__name__,
            "message": str(exc),
        },
        "collected_at": _utc_now(),
        "input_metadata": item.input_metadata,
    }


def _skipped_record(item: FacebookBatchItem, reason: str) -> dict[str, Any]:
    return {
        "platform": "facebook",
        "post_id": item.post_id,
        "input_url": item.input_url,
        "canonical_url": item.canonical_url,
        "collection_status": "skipped",
        "skip_reason": reason,
        "collected_at": _utc_now(),
        "input_metadata": item.input_metadata,
    }


def _build_summary(
    started_at: str,
    input_count: int,
    current_records: list[dict[str, Any]],
    total_records: list[dict[str, Any]],
    output_path: Path,
    diagnostics_dir: Path,
    failure_types: Counter[str],
    processed_this_run: int,
    skipped_this_run: int,
    resume: bool,
) -> dict[str, Any]:
    current_counts = Counter(str(record.get("collection_status")) for record in current_records)
    total_counts = Counter(str(record.get("collection_status")) for record in total_records)
    return {
        "started_at": started_at,
        "finished_at": _utc_now(),
        "resume": resume,
        "input_count": input_count,
        "processed_count": processed_this_run,
        "success_count": current_counts.get("success", 0) + current_counts.get("partial", 0),
        "failed_count": current_counts.get("failed", 0),
        "skipped_count": skipped_this_run,
        "output_file": str(output_path),
        "diagnostics_directory": str(diagnostics_dir),
        "failure_types": dict(failure_types),
        "current_run": {
            "record_count": len(current_records),
            "success_count": current_counts.get("success", 0) + current_counts.get("partial", 0),
            "failed_count": current_counts.get("failed", 0),
            "skipped_count": skipped_this_run,
        },
        "total": {
            "record_count": len(total_records),
            "success_count": total_counts.get("success", 0) + total_counts.get("partial", 0),
            "failed_count": total_counts.get("failed", 0),
            "skipped_count": total_counts.get("skipped", 0),
        },
    }


def _read_processed_urls(output_path: Path) -> set[str]:
    urls: set[str] = set()
    for record in _read_jsonl_records(output_path):
        input_url = record.get("input_url")
        canonical_url = record.get("canonical_url")
        if isinstance(input_url, str):
            urls.add(input_url)
        if isinstance(canonical_url, str):
            urls.add(canonical_url)
    return urls


def _read_jsonl_records(output_path: Path) -> list[dict[str, Any]]:
    if not output_path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in output_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _append_jsonl_record(output_path: Path, record: dict[str, Any]) -> None:
    with output_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def _temporary_batch_output_path(output_path: Path) -> Path:
    return output_path.with_name(f".{output_path.name}.tmp")


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _validate_storage_state_path(storage_state_path: str | Path | None) -> Path:
    if storage_state_path is None:
        raise ValueError("Facebook batch extraction requires --facebook-storage-state.")
    path = Path(storage_state_path)
    if not path.exists():
        raise ValueError(f"Facebook storage state file does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"Facebook storage state path is not a file: {path}")
    return path


def _url_from_input_object(item: dict[str, Any]) -> str | None:
    for key in ("url", "canonical_url", "original_url"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, (FacebookLoginRequiredError, FacebookPostUnavailableError, FacebookPostNotFoundError, ValueError)):
        return False
    if isinstance(exc, (PlaywrightTimeoutError, PlaywrightError)):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in ("timeout", "timed out", "page crash", "navigation"))


def _safe_filename_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    return cleaned[:80] or "unknown"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _close_quietly(resource: Any) -> None:
    if resource is None:
        return
    try:
        resource.close()
    except PlaywrightError:
        pass
