from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.runtime.facebook_run import utc_now


@dataclass(frozen=True)
class HistoryDecision:
    should_collect: bool
    reason: str | None = None


class FacebookHistoryIndex:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.records = _read_history(path)

    def decision(
        self,
        platform: str,
        post_id: str,
        skip_statuses: tuple[str, ...],
        retry_failed: bool,
    ) -> HistoryDecision:
        record = self.records.get((platform, post_id))
        if record is None:
            return HistoryDecision(True)
        status = str(record.get("last_collection_status") or "")
        if status in skip_statuses:
            return HistoryDecision(False, f"history_{status}")
        if status == "failed" and not retry_failed:
            return HistoryDecision(False, "history_failed_not_retryable")
        return HistoryDecision(True)

    def mark_seen(self, item: dict[str, Any], run_id: str) -> None:
        platform = str(item.get("platform") or "facebook")
        post_id = str(item.get("post_id") or "")
        if not post_id:
            return
        now = utc_now()
        key = (platform, post_id)
        record = self.records.get(key)
        if record is None:
            self.records[key] = {
                "platform": platform,
                "post_id": post_id,
                "canonical_url": item.get("canonical_url"),
                "first_seen_at": now,
                "last_seen_at": now,
                "first_run_id": run_id,
                "last_run_id": run_id,
                "last_collection_status": item.get("collection_status", "seen"),
            }
            return
        record["canonical_url"] = item.get("canonical_url") or record.get("canonical_url")
        record["last_seen_at"] = now
        record["last_run_id"] = run_id
        if item.get("collection_status"):
            record["last_collection_status"] = item["collection_status"]

    def write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f".{self.path.name}.tmp")
        with temp_path.open("w", encoding="utf-8") as file:
            for record in self.records.values():
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        temp_path.replace(self.path)


def _read_history(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    records: dict[tuple[str, str], dict[str, Any]] = {}
    if not path.exists():
        return records
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Corrupt Facebook history JSONL line {line_number}: {path}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Corrupt Facebook history JSONL line {line_number}: expected object.")
        platform = payload.get("platform")
        post_id = payload.get("post_id")
        if not isinstance(platform, str) or not isinstance(post_id, str):
            raise ValueError(f"Corrupt Facebook history JSONL line {line_number}: missing identity.")
        records[(platform, post_id)] = payload
    return records
