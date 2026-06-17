from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from src.validation.facebook_results import (
    FacebookResultValidationError,
    to_final_facebook_result,
    validate_internal_result,
)


def export_facebook_results(
    internal_results_path: Path,
    final_results_path: Path,
    csv_path: Path,
) -> dict[str, Any]:
    final_results: list[dict[str, Any]] = []
    validation_errors: list[dict[str, Any]] = []

    for line_number, record in _iter_jsonl(internal_results_path):
        try:
            validate_internal_result(record)
            if record.get("collection_status") not in {"success", "partial"}:
                continue
            final_results.append(to_final_facebook_result(record))
        except FacebookResultValidationError as exc:
            validation_errors.append({"line": line_number, "error": str(exc)})

    _write_jsonl(final_results_path, final_results)
    _write_csv(csv_path, final_results)
    return {
        "source_path": str(internal_results_path),
        "final_results_path": str(final_results_path),
        "csv_path": str(csv_path),
        "exported_count": len(final_results),
        "validation_error_count": len(validation_errors),
        "validation_errors": validation_errors,
    }


def _iter_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    if not path.exists():
        return []
    records: list[tuple[int, dict[str, Any]]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            records.append((line_number, value))
    return records


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
    temp_path.replace(path)


def _write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "author",
        "content",
        "publish_time",
        "like_count",
        "share_count",
        "comment_count",
        "comments",
    ]
    temp_path = path.with_name(f".{path.name}.tmp")
    with temp_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            row = dict(record)
            row["comments"] = json.dumps(row["comments"], ensure_ascii=False)
            writer.writerow(row)
    temp_path.replace(path)
