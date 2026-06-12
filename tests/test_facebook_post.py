from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import main
from src.extractors.facebook_post import (
    FacebookPostExtractionError,
    _build_extraction_result,
    _container_score,
    _has_strong_post_semantics,
    _has_post_semantics,
    _looks_like_time_text,
    _metadata_has_post_specific_content,
    _semantic_score,
    _target_post_link_selector,
    extract_facebook_post,
)
from src.utils.metrics import parse_metric_count


class FacebookPostValidationTests(unittest.TestCase):
    def test_valid_posts_url_is_accepted(self) -> None:
        with patch("src.extractors.facebook_post.sync_playwright") as sync_playwright:
            sync_playwright.side_effect = RuntimeError("playwright marker")
            with self.assertRaisesRegex(RuntimeError, "playwright marker"):
                extract_facebook_post("https://www.facebook.com/openai/posts/1234567890")

    def test_valid_permalink_url_is_accepted(self) -> None:
        with patch("src.extractors.facebook_post.sync_playwright") as sync_playwright:
            sync_playwright.side_effect = RuntimeError("playwright marker")
            with self.assertRaisesRegex(RuntimeError, "playwright marker"):
                extract_facebook_post(
                    "https://www.facebook.com/permalink.php?story_fbid=1234567890&id=987654321"
                )

    def test_valid_story_url_is_accepted(self) -> None:
        with patch("src.extractors.facebook_post.sync_playwright") as sync_playwright:
            sync_playwright.side_effect = RuntimeError("playwright marker")
            with self.assertRaisesRegex(RuntimeError, "playwright marker"):
                extract_facebook_post(
                    "https://www.facebook.com/story.php?story_fbid=1234567890&id=987654321"
                )

    def test_x_url_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not another platform"):
            extract_facebook_post("https://x.com/OpenAI/status/1234567890")

    def test_facebook_homepage_url_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "supported Facebook post URL"):
            extract_facebook_post("https://www.facebook.com/openai")

    def test_empty_url_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "supported Facebook post URL"):
            extract_facebook_post("")


class FacebookTargetSemanticsTests(unittest.TestCase):
    def test_target_link_without_semantics_cannot_confirm_container(self) -> None:
        self.assertFalse(
            _has_post_semantics(
                {
                    "author": False,
                    "content": False,
                    "publish_time": False,
                    "like_metric": False,
                    "comment_metric": False,
                    "share_metric": False,
                }
            )
        )

    def test_single_post_fallback_requires_multiple_semantic_signals(self) -> None:
        weak_presence = {
            "author": False,
            "content": True,
            "publish_time": False,
            "like_metric": False,
            "comment_metric": False,
            "share_metric": False,
        }
        strong_presence = {
            "author": True,
            "content": True,
            "publish_time": False,
            "like_metric": False,
            "comment_metric": False,
            "share_metric": False,
        }

        self.assertEqual(_semantic_score(weak_presence), 1)
        self.assertFalse(_has_strong_post_semantics(weak_presence))
        self.assertTrue(_has_strong_post_semantics(strong_presence))

    def test_target_post_link_selector_prefers_clear_identity_patterns(self) -> None:
        selector = _target_post_link_selector("1449348982581954")

        self.assertIn('story_fbid=1449348982581954', selector)
        self.assertIn('/posts/1449348982581954', selector)
        self.assertIn('permalink.php', selector)

    def test_container_scoring_prefers_content_and_time_over_weak_metrics(self) -> None:
        weak_presence = {
            "author": True,
            "content": False,
            "publish_time": False,
            "like_metric": True,
            "comment_metric": False,
            "share_metric": False,
        }
        strong_presence = {
            "author": True,
            "content": True,
            "publish_time": True,
            "like_metric": False,
            "comment_metric": False,
            "share_metric": False,
        }

        self.assertGreater(_container_score(strong_presence), _container_score(weak_presence))

    def test_author_like_label_is_not_publish_time_text(self) -> None:
        self.assertFalse(_looks_like_time_text("Genshin Impact"))
        self.assertTrue(_looks_like_time_text("May 22"))


class FacebookExtractionResultTests(unittest.TestCase):
    def test_all_core_fields_empty_fails(self) -> None:
        with self.assertRaisesRegex(FacebookPostExtractionError, "No core Facebook post fields"):
            _build_extraction_result(
                post_id="123",
                input_url="https://www.facebook.com/openai/posts/123",
                canonical_url="https://www.facebook.com/openai/posts/123",
                author={"name": None, "profile_url": None},
                content=None,
                publish_time=None,
                publish_time_text=None,
                like_count=None,
                comment_count=None,
                share_count=None,
                extraction_source="dom",
                metadata_error=None,
            )

    def test_content_only_is_partial(self) -> None:
        result = _build_extraction_result(
            post_id="123",
            input_url="https://www.facebook.com/openai/posts/123",
            canonical_url="https://www.facebook.com/openai/posts/123",
            author={"name": None, "profile_url": None},
            content="Post body",
            publish_time=None,
            publish_time_text=None,
            like_count=None,
            comment_count=None,
            share_count=None,
            extraction_source="dom",
            metadata_error=None,
        )

        self.assertEqual(result["collection_status"], "partial")
        self.assertEqual(result["content"], "Post body")
        self.assertIsNotNone(result["error"])

    def test_full_dom_core_fields_are_success(self) -> None:
        result = _build_extraction_result(
            post_id="123",
            input_url="https://www.facebook.com/openai/posts/123",
            canonical_url="https://www.facebook.com/openai/posts/123",
            author={"name": "OpenAI", "profile_url": "https://www.facebook.com/openai"},
            content="Post body",
            publish_time="2026-06-12T00:00:00Z",
            publish_time_text=None,
            like_count=10,
            comment_count=2,
            share_count=1,
            extraction_source="dom",
            metadata_error=None,
        )

        self.assertEqual(result["collection_status"], "success")
        self.assertEqual(result["reply_count"], None)

    def test_metadata_specific_content_is_partial(self) -> None:
        result = _build_extraction_result(
            post_id="123",
            input_url="https://www.facebook.com/openai/posts/123",
            canonical_url="https://www.facebook.com/openai/posts/123",
            author={"name": "OpenAI", "profile_url": None},
            content="A real post summary",
            publish_time=None,
            publish_time_text=None,
            like_count=None,
            comment_count=None,
            share_count=None,
            extraction_source="page_metadata",
            metadata_error="Full Facebook post DOM was unavailable.",
        )

        self.assertEqual(result["collection_status"], "partial")
        self.assertEqual(result["extraction_source"], "page_metadata")
        self.assertEqual(result["error"], "Full Facebook post DOM was unavailable.")

    def test_generic_facebook_metadata_is_not_post_content(self) -> None:
        self.assertFalse(
            _metadata_has_post_specific_content(
                {
                    "og:title": "Facebook",
                    "og:description": "Facebook helps you connect and share with the people in your life.",
                }
            )
        )

    def test_missing_metrics_remain_none_and_reply_is_not_comment_count(self) -> None:
        result = _build_extraction_result(
            post_id="123",
            input_url="https://www.facebook.com/openai/posts/123",
            canonical_url="https://www.facebook.com/openai/posts/123",
            author={"name": "OpenAI", "profile_url": None},
            content="Post body",
            publish_time="2026-06-12T00:00:00Z",
            publish_time_text=None,
            like_count=None,
            comment_count=5,
            share_count=None,
            extraction_source="dom",
            metadata_error=None,
        )

        self.assertIsNone(result["like_count"])
        self.assertEqual(result["comment_count"], 5)
        self.assertIsNone(result["share_count"])
        self.assertIsNone(result["reply_count"])


class FacebookMainTests(unittest.TestCase):
    def test_failed_extraction_does_not_overwrite_existing_success_output(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "facebook_post.json"
            original_content = '{"collection_status": "success"}'
            output_path.write_text(original_content, encoding="utf-8")

            with patch.object(main, "FACEBOOK_POST_OUTPUT_PATH", output_path):
                with patch(
                    "src.extractors.facebook_post.extract_facebook_post",
                    side_effect=FacebookPostExtractionError("boom"),
                ):
                    with self.assertRaises(SystemExit):
                        main.run_facebook_post_extraction(
                            "https://www.facebook.com/openai/posts/123",
                            headless=True,
                        )

            self.assertEqual(output_path.read_text(encoding="utf-8"), original_content)

    def test_facebook_url_cli_argument_is_recognized(self) -> None:
        with patch(
            "sys.argv",
            ["main.py", "--facebook-url", "https://www.facebook.com/openai/posts/123"],
        ):
            args = main.parse_args()

        self.assertEqual(args.facebook_url, "https://www.facebook.com/openai/posts/123")

    def test_keyword_and_x_modes_still_parse(self) -> None:
        with patch("sys.argv", ["main.py", "--keyword", "OpenAI"]):
            keyword_args = main.parse_args()
        with patch("sys.argv", ["main.py", "--x-url", "https://x.com/OpenAI/status/123"]):
            x_args = main.parse_args()

        self.assertEqual(keyword_args.keyword, "OpenAI")
        self.assertEqual(x_args.x_url, "https://x.com/OpenAI/status/123")


class SharedMetricParserTests(unittest.TestCase):
    def test_shared_metric_parser_supports_facebook_and_x_formats(self) -> None:
        cases = {
            "0": 0,
            "12": 12,
            "1,234": 1234,
            "1.2K": 1200,
            "3.5M": 3_500_000,
            "2B": 2_000_000_000,
            "12 comments": 12,
            "1.2K reactions": 1200,
            "1.2\u4e07": 12_000,
            "3\u4e07": 30_000,
            "1.5\u4ebf": 150_000_000,
        }

        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(parse_metric_count(value), expected)


if __name__ == "__main__":
    unittest.main()
