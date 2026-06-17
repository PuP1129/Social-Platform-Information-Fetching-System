from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUN_STAGES = {
    "initialized",
    "searching",
    "search_completed",
    "filtered",
    "manifest_created",
    "collecting",
    "collection_completed",
    "export_completed",
    "completed",
    "failed",
}


@dataclass(frozen=True)
class FacebookRunPaths:
    run_dir: Path
    config_snapshot: Path
    search_results: Path
    filter_results: Path
    manifest: Path
    state: Path
    internal_results: Path
    final_results: Path
    failed_items: Path
    summary: Path
    csv: Path
    report: Path
    logs: Path
    debug_dir: Path


class StructuredLogger:
    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, level: str, event: str, stage: str, message: str, **extra: Any) -> None:
        payload = {
            "timestamp": utc_now(),
            "level": level,
            "event": event,
            "run_id": self.run_id,
            "stage": stage,
            "message": message,
        }
        payload.update(_safe_log_extra(extra))
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")


class FacebookRunContext:
    def __init__(self, paths: FacebookRunPaths, job_name: str, run_id: str) -> None:
        self.paths = paths
        self.job_name = job_name
        self.run_id = run_id
        self.logger = StructuredLogger(paths.logs, run_id)

    @classmethod
    def create(cls, root_dir: Path, job_name: str, run_id: str | None = None) -> "FacebookRunContext":
        normalized_job = _safe_path_part(job_name)
        selected_run_id = run_id or utc_now().replace(":", "").replace("-", "")
        run_dir = root_dir / normalized_job / selected_run_id
        paths = _build_paths(run_dir)
        run_dir.mkdir(parents=True, exist_ok=False)
        paths.debug_dir.mkdir(parents=True, exist_ok=True)
        context = cls(paths, normalized_job, selected_run_id)
        context.write_state({"current_stage": "initialized"})
        context.logger.log("info", "run_started", "initialized", "Facebook run initialized.")
        return context

    @classmethod
    def load(cls, run_dir: Path) -> "FacebookRunContext":
        paths = _build_paths(run_dir)
        state = read_json(paths.state)
        job_name = str(state.get("job_name") or run_dir.parent.name)
        run_id = str(state.get("run_id") or run_dir.name)
        return cls(paths, job_name, run_id)

    def write_state(self, updates: dict[str, Any]) -> dict[str, Any]:
        previous = read_json(self.paths.state) if self.paths.state.exists() else {}
        now = utc_now()
        state = {
            "job_name": self.job_name,
            "run_id": self.run_id,
            "created_at": previous.get("created_at") or now,
            "updated_at": now,
            "current_stage": previous.get("current_stage") or "initialized",
            "keywords_completed": previous.get("keywords_completed") or [],
            "keywords_failed": previous.get("keywords_failed") or [],
            "manifest_path": str(self.paths.manifest),
            "internal_result_path": str(self.paths.internal_results),
            "final_result_path": str(self.paths.final_results),
            "processed_count": previous.get("processed_count") or 0,
            "success_count": previous.get("success_count") or 0,
            "partial_count": previous.get("partial_count") or 0,
            "failed_count": previous.get("failed_count") or 0,
            "skipped_count": previous.get("skipped_count") or 0,
            "last_error": previous.get("last_error"),
        }
        state.update(updates)
        if state["current_stage"] not in RUN_STAGES:
            raise ValueError(f"Invalid run stage: {state['current_stage']}")
        write_json_atomic(self.paths.state, state)
        return state


def _build_paths(run_dir: Path) -> FacebookRunPaths:
    return FacebookRunPaths(
        run_dir=run_dir,
        config_snapshot=run_dir / "job_config.snapshot.json",
        search_results=run_dir / "search_results.json",
        filter_results=run_dir / "filter_results.json",
        manifest=run_dir / "manifest.json",
        state=run_dir / "pipeline_state.json",
        internal_results=run_dir / "internal_results.jsonl",
        final_results=run_dir / "results.jsonl",
        failed_items=run_dir / "failed_items.jsonl",
        summary=run_dir / "summary.json",
        csv=run_dir / "results.csv",
        report=run_dir / "report.md",
        logs=run_dir / "logs.jsonl",
        debug_dir=run_dir / "debug",
    )


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Could not read JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON file is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return payload


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL line {line_number} in {path}") from exc
        if isinstance(payload, dict):
            records.append(payload)
    return records


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_path_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return cleaned.strip("-") or "facebook-job"


def _safe_log_extra(extra: dict[str, Any]) -> dict[str, Any]:
    blocked = {"cookie", "cookies", "authorization", "storage_state", "html"}
    return {key: value for key, value in extra.items() if key.lower() not in blocked}


def paths_as_dict(paths: FacebookRunPaths) -> dict[str, str]:
    return {key: str(value) for key, value in asdict(paths).items()}
