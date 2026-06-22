from __future__ import annotations

import unittest

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
        self.assertIsNone(normalized["share_count"])
        self.assertNotIn("reply_count", normalized)
        self.assertEqual(normalized["platform_metrics"], {"reply_count": 12})

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
        self.assertEqual(normalized["platform_metrics"], {})

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


if __name__ == "__main__":
    unittest.main()
