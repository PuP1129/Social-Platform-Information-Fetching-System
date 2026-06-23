from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.extractors.x_post import extract_x_post
from src.filters.post_url import classify_post_url


DEFAULT_X_BATCH_OUTPUT_PATH = Path("output") / "x_posts.jsonl"


@dataclass(frozen=True)
class XBatchItem:
    input_url: str
    canonical_url: str
    post_id: str
    input_metadata: dict[str, Any]


def load_x_batch_input(input_path: Path) -> list[dict[str, Any]]:
    if not input_path.exists():
        raise ValueError(f"X batch input file does not exist: {input_path}")
    if not input_path.is_file():
        raise ValueError(f"X batch input path is not a file: {input_path}")
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"X batch input is not valid JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise ValueError("X batch input must be a JSON list of URLs or objects with a url field.")

    items: list[dict[str, Any]] = []
    for index, value in enumerate(payload):
        if isinstance(value, str):
            url = value.strip()
            metadata: dict[str, Any] = {}
        elif isinstance(value, dict) and isinstance(value.get("url"), str):
            url = value["url"].strip()
            metadata = {key: item for key, item in value.items() if key != "url"}
        else:
            raise ValueError(f"X batch item {index + 1} must be a URL string or an object with a url field.")
        if not url:
            raise ValueError(f"X batch item {index + 1} contains an empty URL.")
        items.append({"url": url, "metadata": metadata})
    return items


def prepare_x_batch_items(input_path: Path, limit: int | None = None) -> list[XBatchItem]:
    if limit is not None and limit < 1:
        raise ValueError("X batch limit must be greater than 0.")

    prepared: list[XBatchItem] = []
    seen: set[tuple[str, str]] = set()
    for item in load_x_batch_input(input_path):
        classified = classify_post_url(item["url"])
        if classified is None or classified.get("platform") != "x":
            continue
        identity = ("x", classified["post_id"])
        if identity in seen:
            continue
        seen.add(identity)
        prepared.append(
            XBatchItem(
                input_url=item["url"],
                canonical_url=classified["canonical_url"],
                post_id=classified["post_id"],
                input_metadata=item["metadata"],
            )
        )
        if limit is not None and len(prepared) >= limit:
            break
    return prepared


def run_x_batch(
    input_path: Path,
    storage_state_path: str | Path | None,
    output_path: Path = DEFAULT_X_BATCH_OUTPUT_PATH,
    limit: int | None = None,
    delay_seconds: float = 2.0,
    headless: bool = False,
    resume: bool = False,
    extractor: Callable[..., dict[str, Any]] = extract_x_post,
    sleep_fn: Callable[[float], None] = time.sleep,
    record_transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if delay_seconds < 0:
        raise ValueError("X batch delay must be 0 or greater.")
    items = prepare_x_batch_items(input_path, limit=limit)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    processed = _read_processed_identities(output_path) if resume else set()
    active_output_path = output_path if resume else _temporary_output_path(output_path)
    if not resume:
        active_output_path.write_text("", encoding="utf-8")

    success_count = 0
    failed_count = 0
    skipped_count = 0
    attempted_count = 0
    try:
        for item in items:
            if _is_processed(item, processed):
                skipped_count += 1
                continue

            attempted_count += 1
            print(f"[{attempted_count}/{len(items)}] extracting X post {item.post_id}")
            try:
                record = extractor(
                    item.input_url,
                    headless=headless,
                    storage_state_path=storage_state_path,
                )
                success_count += 1
            except Exception as exc:
                record = _failure_record(item, exc)
                failed_count += 1

            output_record = record_transform(record) if record_transform is not None else record
            _append_jsonl_record(active_output_path, output_record)
            processed.update(_record_identities(output_record))

            if delay_seconds > 0 and attempted_count < len(items):
                sleep_fn(delay_seconds)
    except BaseException:
        if not resume and active_output_path.exists():
            active_output_path.unlink()
        raise

    if not resume:
        active_output_path.replace(output_path)

    return {
        "input_count": len(items),
        "attempted_count": attempted_count,
        "success_count": success_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "output_file": str(output_path),
    }


def _failure_record(item: XBatchItem, exc: Exception) -> dict[str, Any]:
    return {
        "platform": "x",
        "post_id": item.post_id,
        "input_url": item.input_url,
        "canonical_url": item.canonical_url,
        "author": {"name": None, "handle": None},
        "content": None,
        "publish_time": None,
        "comment_count": None,
        "like_count": None,
        "share_count": None,
        "repost_count": None,
        "view_count": None,
        "comments": [],
        "collected_at": _utc_now(),
        "collection_status": "failed",
        "error": f"{type(exc).__name__}: {exc}",
    }


def _read_processed_identities(output_path: Path) -> set[tuple[str, str]]:
    identities: set[tuple[str, str]] = set()
    if not output_path.exists():
        return identities
    for line in output_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            identities.update(_record_identities(value))
    return identities


def _record_identities(record: dict[str, Any]) -> set[tuple[str, str]]:
    identities: set[tuple[str, str]] = set()
    if record.get("platform") not in {None, "x"}:
        return identities
    for field in ("input_url", "canonical_url", "post_id"):
        value = record.get(field)
        if isinstance(value, str) and value:
            identities.add((field, value))
    return identities


def _is_processed(item: XBatchItem, processed: set[tuple[str, str]]) -> bool:
    return bool(
        {
            ("input_url", item.input_url),
            ("canonical_url", item.canonical_url),
            ("post_id", item.post_id),
        }
        & processed
    )


def _append_jsonl_record(output_path: Path, record: dict[str, Any]) -> None:
    with output_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def _temporary_output_path(output_path: Path) -> Path:
    return output_path.with_name(f".{output_path.name}.tmp")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
