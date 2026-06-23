from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main
from src.pipelines.x_search_pipeline import (
    filter_x_search_results,
    load_x_search_keywords_file,
    merge_x_search_keywords,
    run_x_search_pipeline,
)


class XSearchPipelineTests(unittest.TestCase):
    def test_repeated_x_search_keyword_is_recognized(self) -> None:
        with patch.object(
            sys,
            "argv",
            ["main.py", "--x-search-keyword", "OpenAI", "--x-search-keyword", "Codex"],
        ):
            args = main.parse_args()
        self.assertEqual(args.x_search_keyword, ["OpenAI", "Codex"])

    def test_keyword_file_loading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keywords.json"
            path.write_text(json.dumps(["OpenAI", "Codex"], ensure_ascii=False), encoding="utf-8")
            self.assertEqual(load_x_search_keywords_file(path), ["OpenAI", "Codex"])

    def test_cli_and_file_keywords_are_merged_and_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keywords.json"
            path.write_text(json.dumps(["Codex", "GPT-5"]), encoding="utf-8")
            merged = merge_x_search_keywords([" OpenAI ", "Codex", "OpenAI"], path)
        self.assertEqual(merged, ["OpenAI", "Codex", "GPT-5"])

    def test_filter_mixed_results_and_normalize_twitter_url(self) -> None:
        posts, supported_count = filter_x_search_results(
            [
                {"url": "https://twitter.com/OpenAI/status/123?s=20", "title": "one"},
                {"url": "https://x.com/OpenAI", "title": "profile"},
                {"url": "https://x.com/search?q=OpenAI", "title": "search"},
                {"url": "https://www.facebook.com/example/posts/456", "title": "facebook"},
                {"url": "https://x.com/i/web/status/789", "title": "two"},
            ]
        )
        self.assertEqual(supported_count, 2)
        self.assertEqual(
            [post["canonical_url"] for post in posts],
            ["https://x.com/OpenAI/status/123", "https://x.com/i/web/status/789"],
        )

    def test_filter_deduplicates_by_post_id(self) -> None:
        posts, supported_count = filter_x_search_results(
            [
                {"url": "https://twitter.com/OpenAI/status/123"},
                {"url": "https://x.com/OpenAI/status/123?ref=duplicate"},
                {"url": "https://x.com/i/web/status/123"},
            ]
        )
        self.assertEqual(supported_count, 3)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["post_id"], "123")

    def test_pipeline_passes_transform_and_batch_options(self) -> None:
        search_fn = Mock(
            return_value=SimpleNamespace(
                results=[{"url": "https://x.com/OpenAI/status/123", "title": "post"}]
            )
        )
        batch_fn = Mock(
            return_value={"success_count": 1, "failed_count": 0, "skipped_count": 0}
        )
        transform = Mock(side_effect=lambda record: record)
        secret_path = Path(".playwright") / "secret-state.json"

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "x.jsonl"
            summary = run_x_search_pipeline(
                keywords=["OpenAI"],
                max_results=25,
                storage_state_path=secret_path,
                output_path=output_path,
                batch_limit=4,
                delay_seconds=0.5,
                headless=True,
                resume=True,
                timeout_ms=12_345,
                search_fn=search_fn,
                batch_fn=batch_fn,
                record_transform=transform,
            )

        search_fn.assert_called_once_with(keyword="OpenAI", headless=True, max_results=25, timeout_ms=12_345)
        kwargs = batch_fn.call_args.kwargs
        self.assertEqual(kwargs["limit"], 4)
        self.assertEqual(kwargs["delay_seconds"], 0.5)
        self.assertIs(kwargs["record_transform"], transform)
        self.assertEqual(kwargs["storage_state_path"], secret_path)
        self.assertNotIn("secret-state", json.dumps(summary))

    def test_pipeline_default_output_does_not_pass_transform(self) -> None:
        search_fn = Mock(
            return_value=SimpleNamespace(results=[{"url": "https://x.com/OpenAI/status/123"}])
        )
        batch_fn = Mock(
            return_value={"success_count": 1, "failed_count": 0, "skipped_count": 0}
        )
        with tempfile.TemporaryDirectory() as directory:
            run_x_search_pipeline(
                keywords=["OpenAI"],
                max_results=10,
                storage_state_path=None,
                output_path=Path(directory) / "x.jsonl",
                delay_seconds=0,
                search_fn=search_fn,
                batch_fn=batch_fn,
            )
        self.assertNotIn("record_transform", batch_fn.call_args.kwargs)

    def test_main_routes_normalize_output_to_x_search_collection(self) -> None:
        with (
            patch.object(
                sys,
                "argv",
                [
                    "main.py",
                    "--x-search-keyword",
                    "OpenAI",
                    "--x-search-keywords-file",
                    "keywords.json",
                    "--normalize-output",
                ],
            ),
            patch("main.run_x_search_collection") as runner,
        ):
            main.main()
        self.assertTrue(runner.call_args.kwargs["normalize_output"])
        self.assertEqual(runner.call_args.kwargs["keywords"], ["OpenAI"])
        self.assertEqual(runner.call_args.kwargs["keywords_file"], Path("keywords.json"))


if __name__ == "__main__":
    unittest.main()
