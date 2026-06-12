from __future__ import annotations

import unittest

from scripts.collect_facebook_test_urls import (
    _accepted_post_payload,
    candidate_identity,
    classify_final_destination,
    has_semantic_signal,
    is_accepted_preflight,
    rejection_reason,
)


class FacebookPreflightClassificationTests(unittest.TestCase):
    def test_final_destination_classification(self) -> None:
        cases = {
            "https://www.facebook.com/example/posts/123": "standard_post",
            "https://www.facebook.com/story.php?story_fbid=123&id=456": "story_or_permalink",
            "https://www.facebook.com/permalink.php?story_fbid=123&id=456": "story_or_permalink",
            "https://www.facebook.com/watch/?v=123": "watch",
            "https://www.facebook.com/example/videos/123": "video",
            "https://www.facebook.com/reel/123": "reel",
            "https://www.facebook.com/groups/example/posts/123": "group",
            "https://www.facebook.com/login/?next=https%3A%2F%2Fexample.com": "login",
        }

        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(classify_final_destination(url), expected)

    def test_unsupported_final_destinations_are_rejected(self) -> None:
        preflight = {
            "final_destination_type": "watch",
            "login_redirected": False,
            "unavailable_message_detected": False,
            "has_semantic_signal": True,
            "preflight_error": None,
        }

        self.assertFalse(is_accepted_preflight(preflight))
        self.assertEqual(rejection_reason(preflight), "Unsupported Facebook Watch/video destination")

    def test_story_redirected_to_watch_is_rejected_as_watch(self) -> None:
        self.assertEqual(classify_final_destination("https://www.facebook.com/watch/?v=123"), "watch")

    def test_semantic_signal_detection(self) -> None:
        self.assertFalse(
            has_semantic_signal(
                {
                    "role_article": 0,
                    "message_container": 0,
                    "time_or_date_link": 0,
                    "engagement_controls": 0,
                }
            )
        )
        self.assertTrue(
            has_semantic_signal(
                {
                    "role_article": 1,
                    "message_container": 0,
                    "time_or_date_link": 0,
                    "engagement_controls": 0,
                }
            )
        )

    def test_accepts_supported_destination_with_semantic_signal(self) -> None:
        preflight = {
            "final_destination_type": "story_or_permalink",
            "login_redirected": False,
            "unavailable_message_detected": False,
            "has_semantic_signal": True,
            "preflight_error": None,
        }

        self.assertTrue(is_accepted_preflight(preflight))


class FacebookPreflightPayloadTests(unittest.TestCase):
    def test_accepted_payload_dedup_identity_is_preserved(self) -> None:
        candidate = {
            "post_id": "123",
            "original_url": "https://facebook.com/story.php?story_fbid=123&id=456",
            "canonical_url": "https://www.facebook.com/story.php?story_fbid=123&id=456",
            "title": "Title",
            "snippet": "Snippet",
        }
        preflight = {
            "final_url": "https://www.facebook.com/story.php?story_fbid=123&id=456",
            "final_destination_type": "story_or_permalink",
        }

        payload = _accepted_post_payload(candidate, preflight, "world cup")

        self.assertEqual(payload["post_id"], "123")
        self.assertEqual(payload["preflight_status"], "accepted")
        self.assertEqual(payload["source_query"], "world cup")

    def test_candidates_are_deduplicated_by_platform_and_post_id(self) -> None:
        first = {
            "platform": "facebook",
            "post_id": "123",
            "canonical_url": "https://www.facebook.com/story.php?story_fbid=123&id=1",
        }
        second = {
            "platform": "facebook",
            "post_id": "123",
            "canonical_url": "https://www.facebook.com/permalink.php?story_fbid=123&id=1",
        }

        self.assertEqual(candidate_identity(first), candidate_identity(second))

    def test_no_accepted_results_can_be_valid_json_payload(self) -> None:
        payload = {
            "topic": "Genshin Impact",
            "query_count": 1,
            "candidate_count": 0,
            "post_count": 0,
            "posts": [],
            "rejected": [],
        }

        self.assertEqual(payload["post_count"], len(payload["posts"]))

    def test_storage_state_contents_are_not_in_payload(self) -> None:
        secret = "super-secret-cookie-value"
        payload = {
            "topic": "Genshin Impact",
            "query_count": 1,
            "candidate_count": 0,
            "post_count": 0,
            "posts": [],
            "rejected": [{"post_id": "1", "reason": "login", "final_destination_type": "login"}],
        }

        self.assertNotIn(secret, str(payload))


if __name__ == "__main__":
    unittest.main()
