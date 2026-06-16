from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import main
from src.extractors.x_post import (
    XPostExtractionError,
    _build_extraction_result,
    extract_x_post,
    parse_metric_count,
)
from src.filters.post_url import classify_post_url


class ParseMetricCountTests(unittest.TestCase):
    def test_parse_metric_count_supported_formats(self) -> None:
        cases = [
            (None, None),
            ("", None),
            ("0", 0),
            ("12", 12),
            ("1,234", 1234),
            ("1.2K", 1200),
            ("3.5M", 3_500_000),
            ("2B", 2_000_000_000),
            ("12 replies", 12),
            ("1.2K Likes", 1200),
            ("1.2\u4e07", 12_000),
            ("3\u4e07", 30_000),
            ("1.5\u4ebf", 150_000_000),
        ]

        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(parse_metric_count(value), expected)

    def test_parse_metric_count_returns_none_without_valid_number(self) -> None:
        self.assertIsNone(parse_metric_count("Likes"))


class ExtractXPostValidationTests(unittest.TestCase):
    def test_empty_url_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "supported X post URL"):
            extract_x_post("")

    def test_non_x_url_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "supported X post URL"):
            extract_x_post("https://example.com/post/123")

    def test_facebook_url_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not another platform"):
            extract_x_post("https://www.facebook.com/openai/posts/1234567890")

    def test_twitter_url_is_normalized_by_existing_classifier(self) -> None:
        result = classify_post_url("https://twitter.com/OpenAI/status/1234567890?s=20")

        self.assertEqual(
            result,
            {
                "platform": "x",
                "post_id": "1234567890",
                "canonical_url": "https://x.com/OpenAI/status/1234567890",
            },
        )

    def test_supported_x_url_reaches_playwright_after_validation(self) -> None:
        with patch("src.extractors.x_post.sync_playwright") as sync_playwright:
            sync_playwright.side_effect = RuntimeError("playwright start marker")
            with self.assertRaisesRegex(RuntimeError, "playwright start marker"):
                extract_x_post("https://twitter.com/OpenAI/status/1234567890?s=20")

    def test_missing_storage_state_file_is_rejected_before_playwright(self) -> None:
        with self.assertRaisesRegex(ValueError, "storage state file does not exist"):
            extract_x_post(
                "https://x.com/OpenAI/status/1234567890",
                storage_state_path=Path("missing_storage_state.json"),
            )

    def test_storage_state_file_is_passed_to_browser_context(self) -> None:
        with TemporaryDirectory() as temp_dir:
            storage_state_path = Path(temp_dir) / "x_state.json"
            storage_state_path.write_text("{}", encoding="utf-8")

            playwright_manager = MagicMock()
            playwright = MagicMock()
            browser = MagicMock()
            context = MagicMock()
            page = MagicMock()

            playwright_manager.start.return_value = playwright
            playwright.chromium.launch.return_value = browser
            browser.new_context.return_value = context
            context.new_page.return_value = page
            page.wait_for_function.side_effect = RuntimeError("stop after context creation")

            with patch("src.extractors.x_post.sync_playwright", return_value=playwright_manager):
                with self.assertRaisesRegex(RuntimeError, "stop after context creation"):
                    extract_x_post(
                        "https://x.com/OpenAI/status/1234567890",
                        storage_state_path=storage_state_path,
                    )

            browser.new_context.assert_called_once_with(storage_state=str(storage_state_path))


class BuildExtractionResultTests(unittest.TestCase):
    def test_all_core_fields_absent_causes_failure(self) -> None:
        with self.assertRaisesRegex(XPostExtractionError, "no core post fields"):
            _build_extraction_result(
                post_id="123",
                input_url="https://x.com/OpenAI/status/123",
                canonical_url="https://x.com/OpenAI/status/123",
                author={"name": None, "handle": None},
                content=None,
                publish_time=None,
                reply_count=None,
                repost_count=None,
                like_count=None,
                view_count=None,
            )

    def test_one_core_field_present_is_partial(self) -> None:
        result = _build_extraction_result(
            post_id="123",
            input_url="https://x.com/OpenAI/status/123",
            canonical_url="https://x.com/OpenAI/status/123",
            author={"name": None, "handle": "@OpenAI"},
            content=None,
            publish_time=None,
            reply_count=None,
            repost_count=None,
            like_count=None,
            view_count=None,
        )

        self.assertEqual(result["collection_status"], "partial")
        self.assertEqual(result["author"]["handle"], "@OpenAI")
        self.assertIsNone(result["content"])
        self.assertIsNone(result["publish_time"])

    def test_missing_metrics_remain_none(self) -> None:
        result = _build_extraction_result(
            post_id="123",
            input_url="https://x.com/OpenAI/status/123",
            canonical_url="https://x.com/OpenAI/status/123",
            author={"name": "OpenAI", "handle": "@OpenAI"},
            content="Hello",
            publish_time="2026-06-10T12:30:00.000Z",
            reply_count=None,
            repost_count=None,
            like_count=None,
            view_count=None,
        )

        self.assertIsNone(result["reply_count"])
        self.assertIsNone(result["comment_count"])
        self.assertIsNone(result["repost_count"])
        self.assertIsNone(result["like_count"])
        self.assertIsNone(result["view_count"])
        self.assertIsNone(result["share_count"])
        self.assertEqual(result["comments"], [])
        self.assertEqual(result["collection_status"], "success")

    def test_all_core_fields_present_is_success(self) -> None:
        result = _build_extraction_result(
            post_id="123",
            input_url="https://x.com/OpenAI/status/123",
            canonical_url="https://x.com/OpenAI/status/123",
            author={"name": "OpenAI", "handle": None},
            content="Hello",
            publish_time="2026-06-10T12:30:00.000Z",
            reply_count=1,
            repost_count=2,
            like_count=3,
            view_count=4,
        )

        self.assertEqual(result["collection_status"], "success")


class MainXPostOutputTests(unittest.TestCase):
    def test_missing_storage_state_file_causes_clear_x_mode_error(self) -> None:
        with self.assertRaisesRegex(SystemExit, "storage state file does not exist"):
            main.run_x_post_extraction(
                "https://x.com/OpenAI/status/123",
                headless=True,
                storage_state_path=Path("missing_storage_state.json"),
            )

    def test_failed_extraction_does_not_overwrite_existing_success_output(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "x_post.json"
            original_content = '{"collection_status": "success"}'
            output_path.write_text(original_content, encoding="utf-8")

            with patch.object(main, "X_POST_OUTPUT_PATH", output_path):
                with patch(
                    "src.extractors.x_post.extract_x_post",
                    side_effect=XPostExtractionError("boom"),
                ):
                    with self.assertRaises(SystemExit):
                        main.run_x_post_extraction(
                            "https://x.com/OpenAI/status/123",
                            headless=True,
                        )

            self.assertEqual(output_path.read_text(encoding="utf-8"), original_content)


class MainArgumentParsingTests(unittest.TestCase):
    def test_x_storage_state_argument_is_recognized(self) -> None:
        with patch(
            "sys.argv",
            [
                "main.py",
                "--x-url",
                "https://x.com/OpenAI/status/123",
                "--x-storage-state",
                ".playwright/x_test_account.json",
            ],
        ):
            args = main.parse_args()

        self.assertEqual(args.x_storage_state, Path(".playwright/x_test_account.json"))

    def test_x_storage_state_argument_is_optional(self) -> None:
        with patch("sys.argv", ["main.py", "--x-url", "https://x.com/OpenAI/status/123"]):
            args = main.parse_args()

        self.assertIsNone(args.x_storage_state)

    def test_keyword_search_mode_ignores_storage_state_argument(self) -> None:
        from src.search.google_pse import GooglePSESearchResponse

        response = GooglePSESearchResponse(
            results=[],
            requested_count=5,
            target_count=5,
            page_logs=[],
        )
        with patch("src.search.google_pse.search_google_pse_with_metadata", return_value=response):
            with patch("main.write_output") as write_output:
                main.run_search("OpenAI", headless=True, max_results=5)

        self.assertEqual(write_output.call_count, 2)


if __name__ == "__main__":
    unittest.main()
