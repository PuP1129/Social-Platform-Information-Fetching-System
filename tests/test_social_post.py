from __future__ import annotations

import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import main
from src.batch.facebook_batch import run_facebook_batch
from src.utils.social_post_normalizer import (
    NORMALIZED_SOCIAL_POST_FIELDS,
    SocialPostNormalizationError,
    normalize_social_post,
)


class SocialPostNormalizationTests(unittest.TestCase):
    def test_normalizes_x_post_and_keeps_reply_count_only_as_platform_metric(self) -> None:
        normalized = normalize_social_post(
            {
                "platform": "x",
                "post_id": "123",
                "input_url": "https://twitter.com/OpenAI/status/123",
                "canonical_url": "https://x.com/OpenAI/status/123",
                "author": {"name": "OpenAI", "handle": "@OpenAI"},
                "content": "Example X post",
                "publish_time": "2026-06-22T00:00:00Z",
                "reply_count": 12,
                "repost_count": 34,
                "like_count": 56,
                "view_count": 789,
                "share_count": None,
                "comments": [],
                "collected_at": "2026-06-22T01:00:00Z",
                "collection_status": "success",
                "error": None,
            }
        )

        self.assertEqual(tuple(normalized), NORMALIZED_SOCIAL_POST_FIELDS)
        self.assertEqual(normalized["schema_version"], "1.0")
        self.assertEqual(normalized["comment_count"], 12)
        self.assertEqual(normalized["repost_count"], 34)
        self.assertEqual(normalized["view_count"], 789)
        self.assertEqual(normalized["author"]["type"], "user")
        self.assertIsNone(normalized["share_count"])
        self.assertNotIn("reply_count", normalized)
        self.assertEqual(normalized["platform_metrics"], {"reply_count": 12})
        self.assertEqual(normalized["platform_context"], {})

    def test_normalizes_facebook_post_without_x_metrics(self) -> None:
        normalized = normalize_social_post(
            {
                "platform": "facebook",
                "post_id": "456",
                "input_url": "https://www.facebook.com/example/posts/456",
                "canonical_url": "https://www.facebook.com/example/posts/456",
                "author": {
                    "name": "Example Page",
                    "profile_url": "https://www.facebook.com/example",
                },
                "content": "Example Facebook post",
                "publish_time": "2026-06-22T00:00:00Z",
                "comment_count": 3,
                "like_count": 10,
                "share_count": 2,
                "comments": [],
                "collected_at": "2026-06-22T01:00:00Z",
                "collection_status": "success",
                "error": None,
            }
        )

        self.assertEqual(normalized["comment_count"], 3)
        self.assertEqual(normalized["like_count"], 10)
        self.assertEqual(normalized["share_count"], 2)
        self.assertIsNone(normalized["repost_count"])
        self.assertIsNone(normalized["view_count"])
        self.assertIsNone(normalized["author"]["handle"])
        self.assertEqual(normalized["author"]["type"], "unknown")
        self.assertEqual(normalized["platform_metrics"], {})
        self.assertEqual(normalized["platform_context"], {})

    def test_normalizes_facebook_page_author_type_when_known(self) -> None:
        record = _facebook_record()
        record["author"] = {
            "name": "Example Page",
            "profile_url": "https://www.facebook.com/example",
            "type": "page",
        }
        normalized = normalize_social_post(record)

        self.assertEqual(normalized["author"]["type"], "page")
        self.assertEqual(normalized["collection_status"], "success")

    def test_group_source_author_can_repair_partial_facebook_group_post(self) -> None:
        normalized = normalize_social_post(
            {
                "platform": "facebook",
                "post_id": "123",
                "input_url": "https://www.facebook.com/groups/booklovers/posts/123",
                "canonical_url": "https://www.facebook.com/groups/booklovers/posts/123",
                "context_type": "group",
                "author": {"name": None, "profile_url": None},
                "source": {
                    "type": "group",
                    "name": "Book Lovers",
                    "profile_url": "https://www.facebook.com/groups/booklovers",
                },
                "content": "A group post",
                "publish_time": None,
                "comment_count": None,
                "comments": [],
                "collection_status": "partial",
                "error": "Missing core field(s): author.",
            }
        )

        self.assertEqual(
            normalized["author"],
            {
                "name": "Book Lovers",
                "handle": None,
                "profile_url": "https://www.facebook.com/groups/booklovers",
                "type": "group",
            },
        )
        self.assertEqual(normalized["collection_status"], "success")
        self.assertIsNone(normalized["error"])
        self.assertEqual(
            normalized["platform_context"],
            {
                "facebook_group": {
                    "name": "Book Lovers",
                    "url": "https://www.facebook.com/groups/booklovers",
                }
            },
        )

    def test_missing_facebook_author_and_group_source_remains_partial(self) -> None:
        normalized = normalize_social_post(
            {
                "platform": "facebook",
                "post_id": "123",
                "canonical_url": "https://www.facebook.com/groups/booklovers/posts/123",
                "context_type": "group",
                "author": {"name": None, "profile_url": None},
                "content": "A group post",
                "collection_status": "partial",
                "error": "Missing core field(s): author.",
            }
        )

        self.assertIsNone(normalized["author"]["name"])
        self.assertEqual(normalized["author"]["type"], "unknown")
        self.assertEqual(normalized["collection_status"], "partial")

    def test_group_post_with_user_author_preserves_user_and_context(self) -> None:
        normalized = normalize_social_post(
            {
                "platform": "facebook",
                "post_id": "123",
                "canonical_url": "https://www.facebook.com/groups/booklovers/posts/123",
                "author": {
                    "name": "Raymond Baumgart",
                    "profile_url": "https://www.facebook.com/groups/booklovers/user/456",
                    "type": "user",
                },
                "source": {
                    "type": "group",
                    "name": "Kansas City Secrets",
                    "profile_url": "https://www.facebook.com/groups/kansascitysecrets",
                },
                "content": "Post body",
                "collection_status": "success",
            }
        )

        self.assertEqual(normalized["author"]["name"], "Raymond Baumgart")
        self.assertEqual(normalized["author"]["type"], "user")
        self.assertEqual(normalized["platform_context"]["facebook_group"]["name"], "Kansas City Secrets")

    def test_invalid_facebook_author_candidate_is_rejected(self) -> None:
        for invalid_name in ("#eattherich", "+3", "Join", "June 16 at 3:59 AM", "6w", "12 comments"):
            with self.subTest(invalid_name=invalid_name):
                normalized = normalize_social_post(
                    {
                        "platform": "facebook",
                        "post_id": "123",
                        "canonical_url": "https://www.facebook.com/example/posts/123",
                        "author": {"name": invalid_name, "profile_url": "https://www.facebook.com/photo"},
                        "content": "Post body",
                        "collection_status": "success",
                    }
                )
                self.assertIsNone(normalized["author"]["name"])
                self.assertEqual(normalized["collection_status"], "partial")
                self.assertEqual(normalized["error"], "Missing core field(s): author.")

    def test_missing_metrics_remain_none(self) -> None:
        normalized = normalize_social_post({"platform": "facebook"})
        for field in ("comment_count", "like_count", "share_count", "repost_count", "view_count"):
            self.assertIsNone(normalized[field])

    def test_generates_x_profile_url_from_handle(self) -> None:
        normalized = normalize_social_post(
            {"platform": "x", "author": {"name": "OpenAI", "handle": "OpenAI"}}
        )

        self.assertEqual(normalized["author"]["handle"], "@OpenAI")
        self.assertEqual(normalized["author"]["profile_url"], "https://x.com/OpenAI")

    def test_unknown_fields_and_secrets_are_not_copied(self) -> None:
        normalized = normalize_social_post(
            {
                "platform": "x",
                "author": {"handle": "@OpenAI", "auth_token": "secret-author-token"},
                "comments": [
                    {
                        "comment_content": "Safe comment",
                        "comment_author": "Reader",
                        "cookie": "secret-comment-cookie",
                    }
                ],
                "auth_token": "secret-token",
                "cookies": [{"value": "secret-cookie"}],
                "storage_state": {"origins": ["secret-origin"]},
            }
        )

        serialized = repr(normalized)
        self.assertNotIn("secret-token", serialized)
        self.assertNotIn("secret-cookie", serialized)
        self.assertNotIn("secret-author-token", serialized)
        self.assertNotIn("secret-comment-cookie", serialized)

    def test_rejects_unsupported_platform(self) -> None:
        with self.assertRaisesRegex(SocialPostNormalizationError, "x.*facebook"):
            normalize_social_post({"platform": "reddit"})


class NormalizedOutputBoundaryTests(unittest.TestCase):
    def test_normalize_output_cli_flag_is_recognized(self) -> None:
        with patch(
            "sys.argv",
            ["main.py", "--x-url", "https://x.com/OpenAI/status/123", "--normalize-output"],
        ):
            args = main.parse_args()

        self.assertTrue(args.normalize_output)

    def test_x_single_output_is_normalized_when_requested(self) -> None:
        raw = _x_record()
        raw["unknown_field"] = "not-normalized"
        with patch("src.extractors.x_post.extract_x_post", return_value=raw):
            with patch("main.write_output") as write_output:
                main.run_x_post_extraction(
                    raw["canonical_url"],
                    headless=True,
                    normalize_output=True,
                )

        written = write_output.call_args.args[0]
        self.assertEqual(written["schema_version"], "1.0")
        self.assertEqual(written["comment_count"], 12)
        self.assertNotIn("reply_count", written)
        self.assertEqual(written["platform_metrics"], {"reply_count": 12})
        self.assertNotIn("unknown_field", written)

    def test_facebook_single_output_is_normalized_when_requested(self) -> None:
        raw = _facebook_record()
        with patch("src.extractors.facebook_post.extract_facebook_post", return_value=raw):
            with patch("main.write_output") as write_output:
                main.run_facebook_post_extraction(
                    raw["canonical_url"],
                    headless=True,
                    normalize_output=True,
                )

        written = write_output.call_args.args[0]
        self.assertEqual(written["schema_version"], "1.0")
        self.assertEqual(written["comment_count"], 3)
        self.assertIsNone(written["repost_count"])
        self.assertIsNone(written["author"]["handle"])

    def test_default_single_output_remains_raw(self) -> None:
        raw = _x_record()
        raw["unknown_field"] = "preserved"
        with patch("src.extractors.x_post.extract_x_post", return_value=raw):
            with patch("main.write_output") as write_output:
                main.run_x_post_extraction(raw["canonical_url"], headless=True)

        self.assertIs(write_output.call_args.args[0], raw)
        self.assertEqual(write_output.call_args.args[0]["unknown_field"], "preserved")

    def test_normalized_facebook_batch_writes_only_normalized_records(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.json"
            output_path = root / "results.jsonl"
            summary_path = root / "summary.json"
            state_path = root / "state.json"
            url = "https://www.facebook.com/example/posts/456"
            input_path.write_text(json.dumps([url]), encoding="utf-8")
            state_path.write_text('{"cookies": [], "origins": []}', encoding="utf-8")

            raw = _facebook_record()
            raw.update(
                {
                    "cookies": [{"value": "secret-cookie"}],
                    "storage_state": {"token": "secret-state"},
                    "unknown_field": "not-normalized",
                }
            )
            run_facebook_batch(
                input_path=input_path,
                storage_state_path=state_path,
                output_path=output_path,
                summary_path=summary_path,
                diagnostics_dir=root / "debug",
                extractor=lambda _context, _url: raw,
                sleep_fn=lambda _seconds: None,
                record_transform=normalize_social_post,
            )

            written = json.loads(output_path.read_text(encoding="utf-8").strip())

        self.assertEqual(written["schema_version"], "1.0")
        self.assertNotIn("cookies", written)
        self.assertNotIn("storage_state", written)
        self.assertNotIn("unknown_field", written)


def _x_record() -> dict[str, object]:
    return {
        "platform": "x",
        "post_id": "123",
        "input_url": "https://x.com/OpenAI/status/123",
        "canonical_url": "https://x.com/OpenAI/status/123",
        "author": {"name": "OpenAI", "handle": "@OpenAI"},
        "content": "Example X post",
        "publish_time": "2026-06-22T00:00:00Z",
        "reply_count": 12,
        "comment_count": 12,
        "repost_count": 34,
        "like_count": 56,
        "view_count": 789,
        "share_count": None,
        "comments": [],
        "collected_at": "2026-06-22T01:00:00Z",
        "collection_status": "success",
        "error": None,
    }


def _facebook_record() -> dict[str, object]:
    return {
        "platform": "facebook",
        "post_id": "456",
        "input_url": "https://www.facebook.com/example/posts/456",
        "canonical_url": "https://www.facebook.com/example/posts/456",
        "author": {"name": "Example Page", "profile_url": "https://www.facebook.com/example"},
        "content": "Example Facebook post",
        "publish_time": "2026-06-22T00:00:00Z",
        "comment_count": 3,
        "like_count": 10,
        "share_count": 2,
        "comments": [],
        "collected_at": "2026-06-22T01:00:00Z",
        "collection_status": "success",
        "error": None,
    }


if __name__ == "__main__":
    unittest.main()
