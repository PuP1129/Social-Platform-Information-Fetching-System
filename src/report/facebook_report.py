from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


SUPPORT_MATRIX = [
    ("story.php", True, True, True, True, "verified"),
    ("permalink.php", True, True, True, True, "implemented_unverified"),
    ("numeric posts", True, True, True, False, "implemented_unverified"),
    ("pfbid posts", True, True, True, True, "verified"),
    ("group posts", True, True, True, True, "verified"),
    ("group permalink", True, True, True, True, "verified"),
    ("photo", False, False, False, False, "unsupported"),
    ("watch", False, False, True, False, "unsupported"),
    ("reel", False, False, True, False, "unsupported"),
    ("video", False, False, True, False, "unsupported"),
    ("share redirect", False, False, False, False, "unsupported"),
]


def write_facebook_report(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_facebook_report(summary), encoding="utf-8")


def build_facebook_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Facebook Collection Report",
        "",
        f"- Job name: {summary.get('job_name', '')}",
        f"- Run id: {summary.get('run_id', '')}",
        f"- Started at: {summary.get('started_at', '')}",
        f"- Finished at: {summary.get('finished_at', '')}",
        f"- Keywords: {', '.join(summary.get('keywords', []))}",
        f"- Search result count: {summary.get('search_result_count', 0)}",
        f"- Manifest item count: {summary.get('manifest_item_count', 0)}",
        f"- Duplicate count: {summary.get('duplicate_count', 0)}",
        f"- History skipped: {summary.get('history_skipped_count', 0)}",
        f"- Success: {summary.get('success_count', 0)}",
        f"- Partial: {summary.get('partial_count', 0)}",
        f"- Failed: {summary.get('failed_count', 0)}",
        f"- Skipped: {summary.get('skipped_count', 0)}",
        f"- Comment records extracted: {summary.get('comment_record_count', 0)}",
        "",
        "## Failure Categories",
        "",
    ]
    failure_categories = summary.get("failure_categories") or {}
    if failure_categories:
        for category, count in sorted(failure_categories.items()):
            lines.append(f"- {category}: {count}")
    else:
        lines.append("- none: 0")
    lines.extend(["", "## Files", ""])
    for key in ("run_dir", "manifest_path", "internal_results_path", "final_results_path", "csv_path", "logs_path"):
        lines.append(f"- {key}: {summary.get(key, '')}")
    lines.extend(["", "## URL Support Matrix", "", "| URL type | filter accepted | identity parsed | unit test | real success sample | status |", "| --- | --- | --- | --- | --- | --- |"])
    for row in SUPPORT_MATRIX:
        lines.append("| " + " | ".join(_format_cell(value) for value in row) + " |")
    return "\n".join(lines) + "\n"


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(record.get("collection_status")) for record in records)
    failure_categories = Counter(
        str(record.get("failure_category"))
        for record in records
        if record.get("failure_category")
    )
    comments = 0
    for record in records:
        value = record.get("comments")
        if isinstance(value, list):
            comments += len(value)
    return {
        "success_count": statuses.get("success", 0),
        "partial_count": statuses.get("partial", 0),
        "failed_count": statuses.get("failed", 0),
        "skipped_count": statuses.get("skipped", 0),
        "failure_categories": dict(failure_categories),
        "comment_record_count": comments,
    }


def write_json_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _format_cell(value: object) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)
