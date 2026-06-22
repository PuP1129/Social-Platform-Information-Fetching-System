from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import main
from src.batch.x_batch import load_x_batch_input, prepare_x_batch_items, run_x_batch
from src.utils.social_post_normalizer import normalize_social_post


class XBatchInputTests(unittest.TestCase):
    def test_loads_url_strings_and_url_objects(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "input.json"
            path.write_text(
                json.dumps(
                    [
                        "https://x.com/OpenAI/status/1",
                        {"url": "https://x.com/OpenAI/status/2", "label": "second"},
                    ]
                ),
                encoding="utf-8",
            )

            items = load_x_batch_input(path)

        self.assertEqual(items[0], {"url": "https://x.com/OpenAI/status/1", "metadata": {}})
        self.assertEqual(items[1]["metadata"], {"label": "second"})

    def test_normalizes_deduplicates_and_applies_limit(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "input.json"
            path.write_text(
                json.dumps(
                    [
                        "https://twitter.com/OpenAI/status/1?s=20",
                        "https://x.com/OpenAI/status/1",
                        "https://example.com/not-x",
                        "https://x.com/OpenAI/status/2",
                        "https://x.com/OpenAI/status/3",
                    ]
                ),
                encoding="utf-8",
            )

            items = prepare_x_batch_items(path, limit=2)

        self.assertEqual([item.post_id for item in items], ["1", "2"])
        self.assertEqual(items[0].canonical_url, "https://x.com/OpenAI/status/1")


class XBatchRunTests(unittest.TestCase):
    def test_delay_is_called_between_items(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = _batch_paths(Path(temp_dir), ["1", "2"])
            delays: list[float] = []

            summary = run_x_batch(
                input_path=paths["input"],
                storage_state_path=None,
                output_path=paths["output"],
                delay_seconds=1.25,
                extractor=_successful_extractor,
                sleep_fn=delays.append,
            )

        self.assertEqual(delays, [1.25])
        self.assertEqual(summary["success_count"], 2)

    def test_failure_record_is_written_and_batch_continues(self) -> None:
        def extractor(url: str, **_kwargs: object) -> dict[str, object]:
            if url.endswith("/1"):
                raise RuntimeError("test failure")
            return _raw_x_record(url)

        with TemporaryDirectory() as temp_dir:
            paths = _batch_paths(Path(temp_dir), ["1", "2"])
            summary = run_x_batch(
                input_path=paths["input"],
                storage_state_path=None,
                output_path=paths["output"],
                delay_seconds=0,
                extractor=extractor,
            )
            records = _read_jsonl(paths["output"])

        self.assertEqual([record["collection_status"] for record in records], ["failed", "success"])
        self.assertIn("RuntimeError: test failure", records[0]["error"])
        self.assertEqual(summary["failed_count"], 1)
        self.assertEqual(summary["success_count"], 1)

    def test_resume_skips_processed_post_id(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = _batch_paths(Path(temp_dir), ["1", "2"])
            paths["output"].write_text(
                json.dumps(_raw_x_record("https://x.com/OpenAI/status/1")) + "\n",
                encoding="utf-8",
            )
            seen: list[str] = []

            def extractor(url: str, **_kwargs: object) -> dict[str, object]:
                seen.append(url)
                return _raw_x_record(url)

            summary = run_x_batch(
                input_path=paths["input"],
                storage_state_path=None,
                output_path=paths["output"],
                delay_seconds=0,
                resume=True,
                extractor=extractor,
            )
            records = _read_jsonl(paths["output"])

        self.assertEqual(seen, ["https://x.com/OpenAI/status/2"])
        self.assertEqual(len(records), 2)
        self.assertEqual(summary["skipped_count"], 1)

    def test_default_output_preserves_raw_fields(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = _batch_paths(Path(temp_dir), ["1"])
            run_x_batch(
                input_path=paths["input"],
                storage_state_path=None,
                output_path=paths["output"],
                delay_seconds=0,
                extractor=_successful_extractor,
            )
            record = _read_jsonl(paths["output"])[0]

        self.assertEqual(record["reply_count"], 4)
        self.assertEqual(record["raw_only"], "preserved")
        self.assertNotIn("schema_version", record)

    def test_normalized_output_uses_social_post_schema(self) -> None:
        with TemporaryDirectory() as temp_dir:
            paths = _batch_paths(Path(temp_dir), ["1"])
            run_x_batch(
                input_path=paths["input"],
                storage_state_path=None,
                output_path=paths["output"],
                delay_seconds=0,
                extractor=_successful_extractor,
                record_transform=normalize_social_post,
            )
            record = _read_jsonl(paths["output"])[0]

        self.assertEqual(record["schema_version"], "1.0")
        self.assertEqual(record["comment_count"], 4)
        self.assertNotIn("reply_count", record)
        self.assertEqual(record["platform_metrics"], {"reply_count": 4})
        self.assertNotIn("raw_only", record)


class XBatchCliTests(unittest.TestCase):
    def test_x_batch_arguments_are_recognized(self) -> None:
        with patch(
            "sys.argv",
            [
                "main.py",
                "--x-batch-file",
                "input.json",
                "--x-batch-output",
                "output.jsonl",
                "--x-batch-limit",
                "3",
                "--x-batch-delay",
                "0.5",
                "--x-batch-resume",
                "--normalize-output",
            ],
        ):
            args = main.parse_args()

        self.assertEqual(args.x_batch_file, Path("input.json"))
        self.assertEqual(args.x_batch_output, Path("output.jsonl"))
        self.assertEqual(args.x_batch_limit, 3)
        self.assertEqual(args.x_batch_delay, 0.5)
        self.assertTrue(args.x_batch_resume)
        self.assertTrue(args.normalize_output)


def _batch_paths(root: Path, post_ids: list[str]) -> dict[str, Path]:
    input_path = root / "input.json"
    input_path.write_text(
        json.dumps([f"https://x.com/OpenAI/status/{post_id}" for post_id in post_ids]),
        encoding="utf-8",
    )
    return {"input": input_path, "output": root / "output.jsonl"}


def _successful_extractor(url: str, **_kwargs: object) -> dict[str, object]:
    return _raw_x_record(url)


def _raw_x_record(url: str) -> dict[str, object]:
    post_id = url.rstrip("/").rsplit("/", 1)[-1]
    return {
        "platform": "x",
        "post_id": post_id,
        "input_url": url,
        "canonical_url": f"https://x.com/OpenAI/status/{post_id}",
        "author": {"name": "OpenAI", "handle": "@OpenAI"},
        "content": "Example post",
        "publish_time": "2026-06-22T00:00:00Z",
        "reply_count": 4,
        "comment_count": 4,
        "repost_count": 3,
        "like_count": 2,
        "view_count": 10,
        "share_count": None,
        "comments": [],
        "collected_at": "2026-06-22T01:00:00Z",
        "collection_status": "success",
        "error": None,
        "raw_only": "preserved",
    }


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
