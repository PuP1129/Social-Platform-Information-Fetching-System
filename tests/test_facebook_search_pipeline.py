from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import main
from src.pipelines.facebook_search_pipeline import (
    load_keywords_file,
    run_facebook_multi_search_pipeline,
    run_facebook_search_manifest_pipeline,
    run_facebook_search_pipeline,
)
from src.search.google_pse import GooglePSEPageLog, GooglePSESearchResponse


def _search_response(results: list[dict[str, str | None]]) -> GooglePSESearchResponse:
    return GooglePSESearchResponse(
        results=results,
        requested_count=len(results),
        target_count=len(results),
        page_logs=[GooglePSEPageLog(page_number=1, start=1, requested=len(results), received=len(results))],
    )


def _batch_summary(output_path: Path, records: list[dict] | None = None, resume: bool = False) -> dict:
    records = records or []
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if records:
        with output_path.open("a" if resume else "w", encoding="utf-8") as file:
            for record in records:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
    success_count = sum(1 for record in records if record.get("collection_status") in {"success", "partial"})
    failed_count = sum(1 for record in records if record.get("collection_status") == "failed")
    return {
        "current_run": {
            "record_count": len(records),
            "success_count": success_count,
            "failed_count": failed_count,
            "skipped_count": 0,
        },
        "total": {
            "record_count": len(records),
            "success_count": success_count,
            "failed_count": failed_count,
            "skipped_count": 0,
        },
    }


class FacebookSearchPipelineTests(unittest.TestCase):
    def test_multi_keyword_dedupes_keywords_and_merges_search_contexts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            calls: list[str] = []
            captured: dict = {}

            def fake_search(**kwargs):
                keyword = kwargs["keyword"]
                calls.append(keyword)
                if keyword == "Genshin Impact":
                    return _search_response(
                        [
                            {
                                "title": "First hit",
                                "url": "https://www.facebook.com/story.php?story_fbid=123&id=456",
                                "snippet": "first",
                            }
                        ]
                    )
                return _search_response(
                    [
                        {
                            "title": "Second hit",
                            "url": "https://www.facebook.com/story.php?story_fbid=123&id=456&comment_id=999",
                            "snippet": "second",
                        }
                    ]
                )

            def fake_batch(**kwargs):
                captured["batch_input"] = json.loads(Path(kwargs["input_path"]).read_text(encoding="utf-8"))
                return _batch_summary(kwargs["output_path"], [])

            summary = run_facebook_multi_search_pipeline(
                keywords=["Genshin Impact", "HoYoverse", "Genshin Impact", " "],
                max_results=1,
                storage_state_path=base / "state.json",
                output_path=base / "results.jsonl",
                artifact_dir=base / "artifacts",
                search_fn=fake_search,
                batch_fn=fake_batch,
            )

            self.assertEqual(calls, ["Genshin Impact", "HoYoverse"])
            self.assertEqual(summary["accepted_before_global_dedup"], 2)
            self.assertEqual(summary["batch_input_count"], 1)
            self.assertEqual(summary["duplicate_count"], 1)
            post = captured["batch_input"]["posts"][0]
            self.assertEqual(post["search_keyword"], "Genshin Impact")
            self.assertEqual(len(post["search_contexts"]), 2)
            self.assertEqual([context["keyword"] for context in post["search_contexts"]], ["Genshin Impact", "HoYoverse"])

    def test_one_keyword_search_failure_does_not_stop_other_keywords(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)

            def fake_search(**kwargs):
                if kwargs["keyword"] == "bad":
                    raise RuntimeError("boom")
                return _search_response(
                    [
                        {
                            "title": "Story",
                            "url": "https://www.facebook.com/story.php?story_fbid=123&id=456",
                            "snippet": None,
                        }
                    ]
                )

            summary = run_facebook_multi_search_pipeline(
                keywords=["bad", "good"],
                max_results=1,
                storage_state_path=base / "state.json",
                output_path=base / "results.jsonl",
                artifact_dir=base / "artifacts",
                search_fn=fake_search,
                batch_fn=lambda **kwargs: _batch_summary(kwargs["output_path"], []),
            )

            self.assertEqual(summary["keyword_success_count"], 1)
            self.assertEqual(summary["keyword_failed_count"], 1)
            self.assertEqual(summary["pipeline_status"], "partial_search_failure")
            self.assertEqual(summary["batch_input_count"], 1)

    def test_keywords_file_parses_and_rejects_invalid_payloads(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            keywords_path = base / "keywords.json"
            keywords_path.write_text(json.dumps(["A", "A", " ", "B"]), encoding="utf-8")

            self.assertEqual(load_keywords_file(keywords_path), ["A", "B"])

            bad_path = base / "bad.json"
            bad_path.write_text(json.dumps({"keyword": "A"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "array"):
                load_keywords_file(bad_path)

    def test_manifest_mode_reuses_manifest_without_searching(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            manifest_path = base / "manifest.json"
            manifest = {
                "schema_version": 1,
                "created_at": "2026-06-16T00:00:00Z",
                "keywords": ["A"],
                "search_max_results_per_keyword": 5,
                "items": [
                    {
                        "url": "https://www.facebook.com/story.php?story_fbid=123&id=456",
                        "platform": "facebook",
                        "post_id": "123",
                        "canonical_url": "https://www.facebook.com/story.php?story_fbid=123&id=456",
                        "search_contexts": [{"keyword": "A", "rank": 1, "title": "T", "snippet": None, "result_url": "u"}],
                    }
                ],
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            captured: dict = {}

            def fake_batch(**kwargs):
                captured["input"] = json.loads(Path(kwargs["input_path"]).read_text(encoding="utf-8"))
                self.assertTrue(kwargs["resume"])
                return _batch_summary(kwargs["output_path"], [], resume=True)

            summary = run_facebook_search_manifest_pipeline(
                manifest_path=manifest_path,
                storage_state_path=base / "state.json",
                output_path=base / "results.jsonl",
                artifact_dir=base / "artifacts",
                resume=True,
                batch_fn=fake_batch,
            )

            self.assertTrue(summary["manifest_mode"])
            self.assertEqual(summary["batch_input_count"], 1)
            self.assertEqual(captured["input"]["posts"][0]["post_id"], "123")

    def test_empty_manifest_does_not_start_batch_or_overwrite_output(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_path = base / "results.jsonl"
            output_path.write_text('{"post_id":"old"}\n', encoding="utf-8")
            manifest_path = base / "manifest.json"
            manifest_path.write_text(
                json.dumps({"schema_version": 1, "created_at": "x", "keywords": [], "search_max_results_per_keyword": 5, "items": []}),
                encoding="utf-8",
            )

            summary = run_facebook_search_manifest_pipeline(
                manifest_path=manifest_path,
                storage_state_path=base / "state.json",
                output_path=output_path,
                artifact_dir=base / "artifacts",
                batch_fn=lambda **_: (_ for _ in ()).throw(AssertionError("batch should not start")),
            )

            self.assertFalse(summary["batch_started"])
            self.assertEqual(summary["pipeline_status"], "no_accepted_urls")
            self.assertEqual(output_path.read_text(encoding="utf-8"), '{"post_id":"old"}\n')

    def test_invalid_manifest_reports_clear_error(self) -> None:
        with TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "manifest.json"
            manifest_path.write_text(json.dumps({"schema_version": 999, "items": []}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "schema_version"):
                run_facebook_search_manifest_pipeline(
                    manifest_path=manifest_path,
                    storage_state_path=Path(temp_dir) / "state.json",
                    output_path=Path(temp_dir) / "results.jsonl",
                    artifact_dir=Path(temp_dir) / "artifacts",
                    batch_fn=lambda **_: {},
                )

    def test_search_results_flow_through_filter_and_batch_with_source_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_path = base / "results.jsonl"
            captured: dict = {}

            def fake_batch(**kwargs):
                batch_input = json.loads(Path(kwargs["input_path"]).read_text(encoding="utf-8"))
                captured["kwargs"] = kwargs
                captured["batch_input"] = batch_input
                records = [
                    {
                        "platform": "facebook",
                        "post_id": item["post_id"],
                        "input_url": item["canonical_url"],
                        "canonical_url": item["canonical_url"],
                        "collection_status": "success",
                        "input_metadata": {
                            key: value
                            for key, value in item.items()
                            if key not in {"url", "canonical_url", "original_url"}
                        },
                    }
                    for item in batch_input["posts"]
                ]
                return _batch_summary(kwargs["output_path"], records)

            response = _search_response(
                [
                    {
                        "title": "Story",
                        "url": "https://www.facebook.com/story.php?story_fbid=123&id=456&comment_id=999",
                        "snippet": "story snippet",
                    },
                    {
                        "title": "Watch",
                        "url": "https://www.facebook.com/watch/?v=123",
                        "snippet": "watch snippet",
                    },
                    {
                        "title": "Pfbid",
                        "url": "https://www.facebook.com/example/posts/pfbidExample123?fbclid=abc",
                        "snippet": "pfbid snippet",
                    },
                    {
                        "title": "Group permalink",
                        "url": "https://www.facebook.com/groups/booklovers/permalink/789/?comment_id=1",
                        "snippet": "group snippet",
                    },
                ]
            )

            summary = run_facebook_search_pipeline(
                keyword="Genshin Impact",
                max_results=4,
                storage_state_path=base / "state.json",
                output_path=output_path,
                artifact_dir=base / "artifacts",
                headless=True,
                search_fn=lambda **_: response,
                batch_fn=fake_batch,
            )

            self.assertEqual(summary["search_result_count"], 4)
            self.assertEqual(summary["accepted_count"], 3)
            self.assertEqual(summary["rejected_by_reason"], {"unsupported_url": 1})
            self.assertEqual(summary["batch_current_run"]["success_count"], 3)
            self.assertEqual(captured["kwargs"]["storage_state_path"], base / "state.json")
            self.assertTrue(captured["kwargs"]["headless"])
            posts = captured["batch_input"]["posts"]
            self.assertEqual(len(posts), 3)
            self.assertEqual(posts[0]["search_keyword"], "Genshin Impact")
            self.assertEqual(posts[0]["search_rank"], 1)
            self.assertEqual(posts[0]["search_result_url"], response.results[0]["url"])
            self.assertEqual(posts[0]["canonical_url"], "https://www.facebook.com/story.php?story_fbid=123&id=456")
            self.assertEqual(posts[2]["canonical_url"], "https://www.facebook.com/groups/booklovers/posts/789")
            lines = output_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 3)
            self.assertEqual(json.loads(lines[0])["input_metadata"]["search_title"], "Story")

    def test_duplicate_tracking_and_comment_urls_are_deduplicated_before_batch(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            called = {"count": 0}

            def fake_batch(**kwargs):
                called["count"] += 1
                batch_input = json.loads(Path(kwargs["input_path"]).read_text(encoding="utf-8"))
                self.assertEqual(len(batch_input["posts"]), 1)
                self.assertEqual(batch_input["posts"][0]["search_rank"], 1)
                return _batch_summary(kwargs["output_path"], [])

            response = _search_response(
                [
                    {
                        "title": "First",
                        "url": "https://www.facebook.com/story.php?story_fbid=123&id=456&comment_id=1",
                        "snippet": None,
                    },
                    {
                        "title": "Duplicate",
                        "url": "https://m.facebook.com/story.php?story_fbid=123&id=456&fbclid=abc",
                        "snippet": None,
                    },
                ]
            )

            summary = run_facebook_search_pipeline(
                keyword="book",
                max_results=2,
                storage_state_path=base / "state.json",
                output_path=base / "results.jsonl",
                artifact_dir=base / "artifacts",
                search_fn=lambda **_: response,
                batch_fn=fake_batch,
            )

            self.assertEqual(called["count"], 1)
            self.assertEqual(summary["accepted_count"], 1)
            self.assertEqual(summary["deduplicated_count"], 1)

    def test_zero_accepted_urls_writes_summary_without_starting_batch_or_overwriting_jsonl(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_path = base / "existing.jsonl"
            output_path.write_text('{"collection_status":"success"}\n', encoding="utf-8")

            def fake_batch(**kwargs):
                raise AssertionError("batch should not start when no Facebook post URLs are accepted")

            summary = run_facebook_search_pipeline(
                keyword="no facebook",
                max_results=2,
                storage_state_path=base / "state.json",
                output_path=output_path,
                artifact_dir=base / "artifacts",
                search_fn=lambda **_: _search_response(
                    [
                        {"title": "Watch", "url": "https://www.facebook.com/watch/?v=123", "snippet": None},
                        {"title": "X", "url": "https://x.com/openai/status/123", "snippet": None},
                    ]
                ),
                batch_fn=fake_batch,
            )

            self.assertFalse(summary["batch_started"])
            self.assertEqual(summary["accepted_count"], 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), '{"collection_status":"success"}\n')

    def test_google_pse_error_is_distinct_from_zero_accepted_urls(self) -> None:
        def failing_search(**kwargs):
            raise GooglePSESearchError("pse unavailable")

        with TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(RuntimeError, "All Facebook search keywords failed"):
                run_facebook_search_pipeline(
                    keyword="book",
                    max_results=1,
                    storage_state_path=Path(temp_dir) / "state.json",
                    output_path=Path(temp_dir) / "results.jsonl",
                    artifact_dir=Path(temp_dir) / "artifacts",
                    search_fn=failing_search,
                    batch_fn=lambda **_: {},
                )

    def test_resume_is_passed_to_batch_and_does_not_duplicate_existing_jsonl(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_path = base / "results.jsonl"
            output_path.write_text('{"post_id":"123","collection_status":"success"}\n', encoding="utf-8")

            def fake_batch(**kwargs):
                self.assertTrue(kwargs["resume"])
                return _batch_summary(kwargs["output_path"], [], resume=True)

            summary = run_facebook_search_pipeline(
                keyword="book",
                max_results=1,
                storage_state_path=base / "state.json",
                output_path=output_path,
                artifact_dir=base / "artifacts",
                resume=True,
                search_fn=lambda **_: _search_response(
                    [
                        {
                            "title": "Story",
                            "url": "https://www.facebook.com/story.php?story_fbid=123&id=456",
                            "snippet": None,
                        }
                    ]
                ),
                batch_fn=fake_batch,
            )

            self.assertEqual(len(output_path.read_text(encoding="utf-8").splitlines()), 1)
            self.assertEqual(summary["batch_current_run"]["record_count"], 0)


class FacebookSearchCliTests(unittest.TestCase):
    def test_facebook_search_cli_argument_is_recognized(self) -> None:
        with patch(
            "sys.argv",
            [
                "main.py",
                "--facebook-search-keyword",
                "Genshin Impact",
                "--facebook-search-keyword",
                "HoYoverse",
                "--facebook-search-max-results",
                "5",
            ],
        ):
            args = main.parse_args()

        self.assertEqual(args.facebook_search_keyword, ["Genshin Impact", "HoYoverse"])
        self.assertEqual(args.facebook_search_max_results, 5)

    def test_keywords_file_and_manifest_cli_arguments_are_recognized(self) -> None:
        with patch("sys.argv", ["main.py", "--facebook-search-keywords-file", "keywords.json"]):
            file_args = main.parse_args()
        with patch("sys.argv", ["main.py", "--facebook-search-manifest", "manifest.json"]):
            manifest_args = main.parse_args()

        self.assertEqual(str(file_args.facebook_search_keywords_file), "keywords.json")
        self.assertEqual(str(manifest_args.facebook_search_manifest), "manifest.json")

    def test_keyword_and_manifest_modes_conflict(self) -> None:
        with patch(
            "sys.argv",
            ["main.py", "--facebook-search-keyword", "A", "--facebook-search-manifest", "manifest.json"],
        ):
            with self.assertRaises(SystemExit):
                main.parse_args()

    def test_existing_cli_modes_still_parse(self) -> None:
        with patch("sys.argv", ["main.py", "--facebook-url", "https://www.facebook.com/openai/posts/123"]):
            facebook_args = main.parse_args()
        with patch("sys.argv", ["main.py", "--facebook-batch-file", "input.json"]):
            batch_args = main.parse_args()
        with patch("sys.argv", ["main.py", "--x-url", "https://x.com/OpenAI/status/123"]):
            x_args = main.parse_args()

        self.assertEqual(facebook_args.facebook_url, "https://www.facebook.com/openai/posts/123")
        self.assertEqual(str(batch_args.facebook_batch_file), "input.json")
        self.assertEqual(x_args.x_url, "https://x.com/OpenAI/status/123")


if __name__ == "__main__":
    unittest.main()
