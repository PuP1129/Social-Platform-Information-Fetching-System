from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from src.config.facebook_job import load_facebook_job_config
from src.extractors.facebook_post import _extract_comment_from_candidate
from src.export.facebook_export import export_facebook_results
from src.pipelines.facebook_job_pipeline import export_facebook_run, run_facebook_job_config
from src.runtime.facebook_run import FacebookRunContext, write_json_atomic
from src.validation.facebook_results import FINAL_RESULT_FIELDS, to_final_facebook_result


class FacebookFinalSchemaTests(unittest.TestCase):
    def test_final_schema_is_strict_and_hides_internal_fields(self) -> None:
        final = to_final_facebook_result(
            {
                "platform": "facebook",
                "post_id": "123",
                "input_url": "https://www.facebook.com/example/posts/123",
                "canonical_url": "https://www.facebook.com/example/posts/123",
                "collection_status": "success",
                "author": {"name": "Example", "profile_url": "https://www.facebook.com/example"},
                "content": "Hello",
                "publish_time": "2025-01-22",
                "reply_count": None,
                "like_count": 0,
                "share_count": 3,
                "comment_count": None,
                "comments": [
                    {
                        "comment_content": "Nice post",
                        "comment_time": "2h",
                        "comment_author": "Reader",
                        "internal_id": "hidden",
                    }
                ],
                "collected_at": "2026-06-16T08:00:00Z",
                "diagnostics": {"hidden": True},
            }
        )

        self.assertEqual(tuple(final.keys()), FINAL_RESULT_FIELDS)
        self.assertNotIn("reply_count", final)
        self.assertIsNone(final["comment_count"])
        self.assertEqual(final["like_count"], 0)
        self.assertEqual(
            final["comments"],
            [{"comment_content": "Nice post", "comment_time": "2026-06-16T06:00:00Z", "comment_author": "Reader"}],
        )
        self.assertNotIn("post_id", final)
        self.assertNotIn("diagnostics", final)

    def test_export_writes_jsonl_and_excel_friendly_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            internal = root / "internal_results.jsonl"
            internal.write_text(
                json.dumps(
                    {
                        "platform": "facebook",
                        "post_id": "1",
                        "input_url": "u",
                        "canonical_url": "u",
                        "collection_status": "success",
                        "author": {"name": "作者"},
                        "content": "含,逗号\n换行",
                        "publish_time": "2025-01-01",
                        "comments": [{"comment_content": "评论", "comment_time": "", "comment_author": ""}],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            summary = export_facebook_results(internal, root / "results.jsonl", root / "results.csv")

            self.assertEqual(summary["exported_count"], 1)
            csv_bytes = (root / "results.csv").read_bytes()
            self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))
            final = json.loads((root / "results.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(tuple(final.keys()), FINAL_RESULT_FIELDS)
            self.assertNotIn("reply_count", (root / "results.csv").read_text(encoding="utf-8-sig").splitlines()[0])

    def test_relative_times_are_normalized_in_final_schema(self) -> None:
        final = to_final_facebook_result(
            {
                "platform": "facebook",
                "post_id": "1",
                "input_url": "u",
                "canonical_url": "u",
                "collection_status": "success",
                "author": {"name": "A"},
                "content": "C",
                "publish_time_text": "4h",
                "collected_at": "2026-06-16T08:59:20Z",
                "comments": [{"comment_content": "Hi", "comment_time": "1y", "comment_author": "B"}],
            }
        )

        self.assertEqual(final["publish_time"], "2026-06-16T04:59:20Z")
        self.assertEqual(final["comments"][0]["comment_time"], "2025-06-16")


class FacebookJobConfigTests(unittest.TestCase):
    def test_valid_config_loads_and_invalid_values_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "job.json"
            path.write_text(
                json.dumps(
                    {
                        "job_name": "facebook-text-monitor",
                        "platform": "facebook",
                        "keywords": ["Genshin Impact"],
                        "facebook": {"delay_seconds": 0},
                        "retry": {"max_attempts": 2},
                    }
                ),
                encoding="utf-8",
            )
            config = load_facebook_job_config(path)
            self.assertEqual(config.keywords, ("Genshin Impact",))

            path.write_text(
                json.dumps(
                    {
                        "job_name": "bad",
                        "platform": "facebook",
                        "keywords": [""],
                        "facebook": {"delay_seconds": -1},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_facebook_job_config(path)


class FacebookCommentQualityTests(unittest.TestCase):
    def test_comment_id_time_is_bound_to_same_comment_and_author_is_not_time(self) -> None:
        html = """
        <div role="article" aria-label="Comment by Anonymous participant">
          <a href="/groups/example/user/123">Anonymous participant</a>
          <div data-ad-comet-preview="message">This is a real comment.</div>
          <a href="/groups/example/posts/999/?comment_id=555">1y</a>
          <span>Like</span><span>Reply</span>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(html)
            comment = _extract_comment_from_candidate(page.locator('[role="article"]').first, "999")
            browser.close()

        self.assertIsNotNone(comment)
        self.assertEqual(comment["comment_author"], "Anonymous participant")
        self.assertEqual(comment["comment_content"], "This is a real comment.")
        self.assertEqual(comment["comment_time"], "1y")
        self.assertEqual(comment["comment_id"], "555")

    def test_follow_and_author_only_comment_candidates_are_skipped(self) -> None:
        html = """
        <div>
          <div role="article" aria-label="Comment by Page">
            <a href="/page">Neuvillette's Court Room</a>
            <div dir="auto">Follow</div>
            <a href="/groups/example/posts/999/?comment_id=1">2y</a>
          </div>
          <div role="article" aria-label="Comment by Tim">
            <a href="/tim">Tim</a>
            <div dir="auto">Tim</div>
            <a href="/groups/example/posts/999/?comment_id=2">4h</a>
          </div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(html)
            comments = [
                _extract_comment_from_candidate(page.locator('[role="article"]').nth(0), "999"),
                _extract_comment_from_candidate(page.locator('[role="article"]').nth(1), "999"),
            ]
            browser.close()

        self.assertEqual(comments, [None, None])


class FacebookJobPipelineTests(unittest.TestCase):
    def test_job_config_pipeline_creates_standard_run_outputs_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "job.json"
            storage_state = root / "state.json"
            storage_state.write_text("{}", encoding="utf-8")
            config_path.write_text(
                json.dumps(
                    {
                        "job_name": "unit-job",
                        "platform": "facebook",
                        "keywords": ["Unit Keyword"],
                        "search": {"max_results_per_keyword": 5},
                        "facebook": {"storage_state": str(storage_state), "headless": True, "delay_seconds": 0},
                        "comments": {"enabled": False},
                        "history": {"enabled": False},
                        "output": {"root_dir": str(root / "runs")},
                    }
                ),
                encoding="utf-8",
            )

            class SearchResponse:
                results = [
                    {
                        "title": "Post",
                        "url": "https://www.facebook.com/example/posts/123",
                        "snippet": "Snippet",
                    }
                ]
                requested_count = 5
                partial_error = None
                page_logs = []

            def fake_batch(**kwargs):
                output_path = kwargs["output_path"]
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    json.dumps(
                        {
                            "platform": "facebook",
                            "post_id": "123",
                            "input_url": "https://www.facebook.com/example/posts/123",
                            "canonical_url": "https://www.facebook.com/example/posts/123",
                            "collection_status": "success",
                            "author": {"name": "Example"},
                            "content": "Content",
                            "publish_time": "2025-01-01",
                            "like_count": 1,
                            "share_count": None,
                            "comment_count": 2,
                            "comments": [],
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return {"current_run": {"success_count": 1, "failed_count": 0, "skipped_count": 0}}

            with patch("src.pipelines.facebook_job_pipeline.search_google_pse_with_metadata", return_value=SearchResponse()):
                with patch("src.pipelines.facebook_job_pipeline.run_facebook_batch", side_effect=fake_batch):
                    summary = run_facebook_job_config(config_path)

            run_dir = Path(summary["run_dir"])
            self.assertTrue((run_dir / "job_config.snapshot.json").exists())
            self.assertTrue((run_dir / "search_results.json").exists())
            self.assertTrue((run_dir / "manifest.json").exists())
            self.assertTrue((run_dir / "pipeline_state.json").exists())
            self.assertTrue((run_dir / "internal_results.jsonl").exists())
            self.assertTrue((run_dir / "results.jsonl").exists())
            self.assertTrue((run_dir / "results.csv").exists())
            self.assertTrue((run_dir / "report.md").exists())
            self.assertTrue((run_dir / "logs.jsonl").exists())
            final = json.loads((run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(tuple(final.keys()), FINAL_RESULT_FIELDS)

    def test_export_run_rebuilds_final_outputs_without_browser(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = FacebookRunContext.create(root, "unit-job", run_id="run1")
            write_json_atomic(
                context.paths.config_snapshot,
                {
                    "job_name": "unit-job",
                    "platform": "facebook",
                    "keywords": ["Unit"],
                    "history": {"enabled": False},
                },
            )
            write_json_atomic(context.paths.manifest, {"items": []})
            context.paths.internal_results.write_text(
                json.dumps(
                    {
                        "platform": "facebook",
                        "post_id": "1",
                        "input_url": "u",
                        "canonical_url": "u",
                        "collection_status": "success",
                        "author": {"name": "A"},
                        "content": "C",
                        "publish_time": "T",
                        "comments": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            summary = export_facebook_run(context.paths.run_dir)

            self.assertEqual(summary["success_count"], 1)
            self.assertTrue(context.paths.final_results.exists())
            self.assertTrue(context.paths.csv.exists())


if __name__ == "__main__":
    unittest.main()
