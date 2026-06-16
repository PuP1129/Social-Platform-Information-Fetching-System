from __future__ import annotations

import copy
import unittest

from src.filters.post_url import classify_post_url, filter_post_results


class ClassifyXPostUrlTests(unittest.TestCase):
    def test_valid_x_urls(self) -> None:
        cases = [
            (
                "https://x.com/openai/status/1234567890",
                "https://x.com/openai/status/1234567890",
            ),
            (
                "https://twitter.com/OpenAI/status/1234567890?s=20",
                "https://x.com/OpenAI/status/1234567890",
            ),
            (
                "https://mobile.twitter.com/openai/status/1234567890",
                "https://x.com/openai/status/1234567890",
            ),
            (
                "https://x.com/openai/status/1234567890/photo/1",
                "https://x.com/openai/status/1234567890",
            ),
            (
                "https://x.com/openai/status/1234567890/video/1",
                "https://x.com/openai/status/1234567890",
            ),
            (
                "https://x.com/i/web/status/1234567890",
                "https://x.com/i/web/status/1234567890",
            ),
        ]

        for url, canonical_url in cases:
            with self.subTest(url=url):
                result = classify_post_url(url)
                self.assertIsNotNone(result)
                self.assertEqual(result["platform"], "x")
                self.assertEqual(result["post_id"], "1234567890")
                self.assertEqual(result["canonical_url"], canonical_url)

    def test_invalid_x_urls(self) -> None:
        urls = [
            "https://x.com/openai",
            "https://x.com/search?q=openai",
            "https://x.com/hashtag/openai",
            "https://x.com/home",
            "https://twitter.com/intent/tweet",
        ]

        for url in urls:
            with self.subTest(url=url):
                self.assertIsNone(classify_post_url(url))


class ClassifyFacebookPostUrlTests(unittest.TestCase):
    def test_valid_facebook_urls(self) -> None:
        cases = [
            (
                "https://www.facebook.com/openai/posts/1234567890",
                "1234567890",
                "https://www.facebook.com/openai/posts/1234567890",
            ),
            (
                "https://facebook.com/openai/posts/pfbidExample123",
                "pfbidExample123",
                "https://www.facebook.com/openai/posts/pfbidExample123",
            ),
            (
                "https://m.facebook.com/openai/posts/1234567890?ref=share",
                "1234567890",
                "https://www.facebook.com/openai/posts/1234567890",
            ),
            (
                "https://www.facebook.com/permalink.php?story_fbid=1234567890&id=987654321",
                "1234567890",
                "https://www.facebook.com/permalink.php?story_fbid=1234567890&id=987654321",
            ),
            (
                "https://m.facebook.com/story.php?story_fbid=1234567890&id=987654321&ref=share",
                "1234567890",
                "https://www.facebook.com/story.php?story_fbid=1234567890&id=987654321",
            ),
            (
                "https://www.facebook.com/groups/booklovers/posts/1234567890/",
                "1234567890",
                "https://www.facebook.com/groups/booklovers/posts/1234567890",
            ),
            (
                "https://www.facebook.com/groups/booklovers/posts/pfbidExample123?rdid=abc#comments",
                "pfbidExample123",
                "https://www.facebook.com/groups/booklovers/posts/pfbidExample123",
            ),
            (
                "https://www.facebook.com/groups/booklovers/permalink/1234567890/?comment_id=999#comments",
                "1234567890",
                "https://www.facebook.com/groups/booklovers/posts/1234567890",
            ),
        ]

        for url, post_id, canonical_url in cases:
            with self.subTest(url=url):
                result = classify_post_url(url)
                self.assertIsNotNone(result)
                self.assertEqual(result["platform"], "facebook")
                self.assertEqual(result["post_id"], post_id)
                self.assertEqual(result["canonical_url"], canonical_url)

    def test_facebook_group_post_has_group_context(self) -> None:
        result = classify_post_url("https://www.facebook.com/groups/booklovers/posts/1234567890/")

        self.assertIsNotNone(result)
        self.assertEqual(result["context_type"], "group")

    def test_invalid_facebook_urls(self) -> None:
        urls = [
            "https://www.facebook.com/openai",
            "https://www.facebook.com/search/top?q=openai",
            "https://www.facebook.com/groups/example/",
            "https://www.facebook.com/groups/example/media/",
            "https://www.facebook.com/groups/example/members/",
            "https://www.facebook.com/groups/example/search/",
            "https://www.facebook.com/reel/123",
            "https://www.facebook.com/openai/videos/123",
            "https://www.facebook.com/share/p/example",
        ]

        for url in urls:
            with self.subTest(url=url):
                self.assertIsNone(classify_post_url(url))


class FilterPostResultsTests(unittest.TestCase):
    def test_deduplicates_posts_and_preserves_first_result(self) -> None:
        results = [
            {
                "rank": 1,
                "title": "First X",
                "url": "https://twitter.com/OpenAI/status/123?s=20",
                "snippet": "first snippet",
            },
            {
                "rank": 2,
                "title": "Duplicate X",
                "url": "https://x.com/openai/status/123/photo/1",
                "snippet": "duplicate snippet",
            },
            {
                "rank": 3,
                "title": "Invalid",
                "url": "https://x.com/openai",
                "snippet": "invalid snippet",
            },
            {
                "rank": 4,
                "title": "Facebook same id",
                "url": "https://www.facebook.com/openai/posts/123?ref=share",
                "snippet": "facebook snippet",
            },
            {
                "rank": 5,
                "title": "Duplicate Facebook",
                "url": "https://m.facebook.com/openai/posts/123",
                "snippet": "duplicate facebook snippet",
            },
            {
                "rank": 6,
                "title": "Missing URL",
                "snippet": "missing url snippet",
            },
        ]
        original_results = copy.deepcopy(results)

        filtered = filter_post_results(results)

        self.assertEqual(results, original_results)
        self.assertEqual(len(filtered), 2)
        self.assertEqual(filtered[0]["rank"], 1)
        self.assertEqual(filtered[0]["title"], "First X")
        self.assertEqual(filtered[0]["snippet"], "first snippet")
        self.assertEqual(filtered[0]["platform"], "x")
        self.assertEqual(filtered[0]["post_id"], "123")
        self.assertEqual(filtered[0]["canonical_url"], "https://x.com/OpenAI/status/123")
        self.assertEqual(filtered[1]["rank"], 4)
        self.assertEqual(filtered[1]["platform"], "facebook")
        self.assertEqual(filtered[1]["post_id"], "123")

    def test_group_posts_and_permalink_urls_dedupe_to_same_post(self) -> None:
        filtered = filter_post_results(
            [
                {
                    "rank": 1,
                    "title": "Group posts path",
                    "url": "https://www.facebook.com/groups/booklovers/posts/1234567890",
                    "snippet": "first",
                },
                {
                    "rank": 2,
                    "title": "Group permalink path",
                    "url": "https://www.facebook.com/groups/booklovers/permalink/1234567890/?comment_id=999",
                    "snippet": "duplicate",
                },
            ]
        )

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["rank"], 1)
        self.assertEqual(filtered[0]["canonical_url"], "https://www.facebook.com/groups/booklovers/posts/1234567890")

    def test_skips_wrong_url_types_without_failing(self) -> None:
        filtered = filter_post_results(
            [
                {"rank": 1, "title": "Bad", "url": None, "snippet": None},
                {"rank": 2, "title": "Good", "url": "https://x.com/openai/status/456", "snippet": None},
            ]
        )

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["post_id"], "456")


if __name__ == "__main__":
    unittest.main()
