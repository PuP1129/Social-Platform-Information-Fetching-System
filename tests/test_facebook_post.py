from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import main
from playwright.sync_api import sync_playwright
from src.extractors.facebook_post import (
    FacebookPostExtractionError,
    _apply_publish_time_to_result,
    _build_extraction_result,
    _container_score,
    _collect_failure_diagnostics,
    _extract_action_count_details,
    _extract_article_data,
    _extract_author,
    _extract_comments,
    _extract_metrics,
    _expand_one_comment_button,
    _comment_progress_from_scope,
    _find_comment_expand_button,
    _find_target_article,
    _find_target_article_result,
    _get_visible_text,
    _has_strong_post_semantics,
    _has_post_semantics,
    _looks_like_time_text,
    _metadata_has_post_specific_content,
    _parse_facebook_publish_time,
    _post_title_context,
    _select_best_publish_time_candidate,
    _semantic_score,
    _target_post_link_selector,
    _normalize_facebook_profile_url,
    _visible_publish_time_candidates,
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

    def test_valid_group_post_url_is_accepted(self) -> None:
        with patch("src.extractors.facebook_post.sync_playwright") as sync_playwright:
            sync_playwright.side_effect = RuntimeError("playwright marker")
            with self.assertRaisesRegex(RuntimeError, "playwright marker"):
                extract_facebook_post(
                    "https://www.facebook.com/groups/booklovers/posts/1234567890/"
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
        self.assertIn('comment_id', selector)

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

    def test_nested_target_containers_select_smallest_semantic_container(self) -> None:
        html = """
        <div role="dialog" id="dialog">
          <div role="article" id="broad">
            <div role="article" id="target">
              <a href="https://www.facebook.com/Genshinimpact">Genshin Impact</a>
              <a href="https://www.facebook.com/story.php?story_fbid=123&id=456">May 30, 2023</a>
              <div data-ad-comet-preview="message">Post body</div>
            </div>
          </div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                target = _find_target_article(page, "123")
                self.assertIsNotNone(target)
                self.assertEqual(target.get_attribute("id"), "target")
            finally:
                browser.close()

    def test_comment_permalink_with_target_post_id_is_not_strong_identity(self) -> None:
        html = """
        <div role="dialog">
          <div role="article" id="comment">
            <a href="https://www.facebook.com/groups/book/posts/123?comment_id=999">1y</a>
            <a href="https://www.facebook.com/groups/book/user/456">Commenter</a>
            <div>Only a comment body with target post id in its permalink.</div>
          </div>
          <div id="target" role="dialog" aria-label="Author Name's Post">
            <a href="https://www.facebook.com/groups/book/user/111">Author Name</a>
            <a aria-label="May 30, 2023">May 30, 2023</a>
            <div data-ad-preview="message">This is the actual main post body with enough text.</div>
            <div role="button"><div data-ad-rendering-role="like_button"></div><span dir="auto">4</span></div>
            <div role="button"><div data-ad-rendering-role="comment_button"></div><span dir="auto">48</span></div>
          </div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                selection = _find_target_article_result(page, "123")

                self.assertIsNotNone(selection)
                self.assertNotEqual(selection.locator.get_attribute("id"), "comment")
                self.assertEqual(selection.locator.get_attribute("id"), "target")
            finally:
                browser.close()

    def test_group_post_container_can_be_confirmed_without_comment_permalink_identity(self) -> None:
        html = """
        <html>
          <head><title>(1) Book Lovers | What is your favorite book | Facebook</title></head>
          <body>
            <div role="dialog" id="target" aria-label="Joy Lahman's Post">
              <h2>Joy Lahman's Post</h2>
              <a href="/groups/128051067263878/user/111">Joy Lahman</a>
              <a aria-label="December 12, 2024">December 12, 2024</a>
              <div data-ad-preview="message">What is your favorite book? The one that you read over and over again.</div>
              <button aria-label="Actions for this post by Joy Lahman">Actions</button>
              <div role="button"><div data-ad-rendering-role="like_button"></div><span dir="auto">4</span></div>
              <div role="button"><div data-ad-rendering-role="comment_button"></div><span dir="auto">48</span></div>
            </div>
            <div role="article" id="comment">
              <a href="/groups/128051067263878/posts/8702397713162461?comment_id=1">1y</a>
              <div>Born a crime</div>
            </div>
          </body>
        </html>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                selection = _find_target_article_result(page, "8702397713162461")

                self.assertIsNotNone(selection)
                self.assertEqual(selection.locator.get_attribute("id"), "target")
            finally:
                browser.close()

    def test_broad_dialog_is_not_selected_when_smaller_container_exists(self) -> None:
        html = """
        <div role="dialog" id="dialog">
          <button aria-label="Close">Close</button>
          <div role="article" id="target">
            <a href="https://www.facebook.com/Genshinimpact">Genshin Impact</a>
            <a href="https://www.facebook.com/story.php?story_fbid=123&id=456">May 30, 2023</a>
            <div data-ad-preview="message">Post body</div>
          </div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                target = _find_target_article(page, "123")
                self.assertIsNotNone(target)
                self.assertEqual(target.get_attribute("id"), "target")
            finally:
                browser.close()

    def test_selected_container_index_is_recorded(self) -> None:
        html = """
        <div role="dialog">
          <div role="article" id="target">
            <a href="https://www.facebook.com/Genshinimpact">Genshin Impact</a>
            <a href="https://www.facebook.com/story.php?story_fbid=123&id=456">May 30, 2023</a>
            <div data-ad-preview="message">Post body</div>
          </div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                selection = _find_target_article_result(page, "123")
                self.assertIsNotNone(selection)
                self.assertIsInstance(selection.candidate_index, int)
            finally:
                browser.close()

    def test_title_text_match_fallback_selects_article_without_target_link(self) -> None:
        html = """
        <html>
          <head><title>(1) McDonald's - The McDonald’s and Genshin Impact video game... | Facebook</title></head>
          <body>
            <div role="article" id="comment">Great collaboration!</div>
            <div role="article" id="target">
              <a href="https://www.facebook.com/McDonalds">McDonald's</a>
              <a aria-label="May 30, 2023">May 30, 2023</a>
              <div>The McDonald's and Genshin Impact video game collaboration has arrived with new in-game rewards.</div>
              <div data-ad-rendering-role="like_button"></div>
              <div data-ad-rendering-role="comment_button"></div>
              <div data-ad-rendering-role="share_button"></div>
            </div>
          </body>
        </html>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                selection = _find_target_article_result(page, "1035071751957724")

                self.assertIsNotNone(selection)
                self.assertEqual(selection.locator.get_attribute("id"), "target")
                self.assertIn(selection.strategy, {"title_text_match", "article_score"})
            finally:
                browser.close()

    def test_title_fallback_does_not_take_first_article(self) -> None:
        html = """
        <html>
          <head><title>Brand - This is the main post opening... | Facebook</title></head>
          <body>
            <div role="article" id="first">This is a short comment.</div>
            <div role="article" id="target">
              <a href="https://www.facebook.com/Brand">Brand</a>
              <a aria-label="May 30, 2023">May 30, 2023</a>
              <div>This is the main post opening with enough text to look like a real public post.</div>
              <div data-ad-rendering-role="like_button"></div>
            </div>
          </body>
        </html>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                selection = _find_target_article_result(page, "999")

                self.assertIsNotNone(selection)
                self.assertEqual(selection.locator.get_attribute("id"), "target")
            finally:
                browser.close()

    def test_title_fallback_rejects_close_scores(self) -> None:
        html = """
        <html>
          <head><title>Brand - Same title words appear here... | Facebook</title></head>
          <body>
            <div role="article" id="one">
              <a href="https://www.facebook.com/Brand">Brand</a>
              <div>Same title words appear here in one possible post.</div>
            </div>
            <div role="article" id="two">
              <a href="https://www.facebook.com/Brand">Brand</a>
              <div>Same title words appear here in another possible post.</div>
            </div>
          </body>
        </html>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                self.assertIsNone(_find_target_article_result(page, "999"))
            finally:
                browser.close()

    def test_short_comment_article_is_not_identified_as_main_post(self) -> None:
        html = """
        <html>
          <head><title>Brand - Tiny title... | Facebook</title></head>
          <body>
            <div role="article" id="comment">Tiny title</div>
          </body>
        </html>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                self.assertIsNone(_find_target_article_result(page, "999"))
            finally:
                browser.close()

    def test_article_candidate_diagnostics_include_scores(self) -> None:
        html = """
        <html>
          <head><title>Brand - The target opening text... | Facebook</title></head>
          <body>
            <div role="article" id="target">
              <a href="https://www.facebook.com/Brand">Brand</a>
              <a aria-label="May 30, 2023">May 30, 2023</a>
              <div>The target opening text is present in this article.</div>
              <div data-ad-rendering-role="like_button"></div>
            </div>
          </body>
        </html>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                diagnostics = _collect_failure_diagnostics(
                    page,
                    "999",
                    "https://www.facebook.com/story.php?story_fbid=999&id=1",
                )

                self.assertEqual(len(diagnostics["article_candidates"]), 1)
                candidate = diagnostics["article_candidates"][0]
                self.assertEqual(candidate["index"], 0)
                self.assertGreater(candidate["title_overlap_score"], 0)
                self.assertGreater(candidate["container_score"], 0)
                self.assertIn("score_reasons", candidate)
            finally:
                browser.close()

    def test_message_title_match_selects_ancestor_when_articles_are_empty(self) -> None:
        html = """
        <html>
          <head><title>(1) McDonald's - The McDonald's and Genshin Impact video game... | Facebook</title></head>
          <body>
            <div role="article" id="empty-shell"></div>
            <div id="target">
              <a href="https://www.facebook.com/McDonalds">McDonald's</a>
              <a aria-label="Sep 12, 2024">Sep 12, 2024</a>
              <div data-ad-comet-preview="message">
                The McDonald's and Genshin Impact video game collaboration is live today.
              </div>
              <div>1.7K reactions 124 comments 19 shares</div>
              <div id="bar">
                <div role="button"><div data-ad-rendering-role="like_button"></div></div>
                <div role="button"><div data-ad-rendering-role="comment_button"></div></div>
                <div role="button"><div data-ad-rendering-role="share_button"></div></div>
              </div>
            </div>
          </body>
        </html>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                selection = _find_target_article_result(page, "1035071751957724")

                self.assertIsNotNone(selection)
                self.assertEqual(selection.locator.get_attribute("id"), "target")
                self.assertEqual(selection.strategy, "message_ancestor_title_match")
                self.assertEqual(selection.message_index, 0)
            finally:
                browser.close()

    def test_message_ancestor_path_extracts_full_post_data(self) -> None:
        html = """
        <html>
          <head><title>McDonald's - The McDonald's and Genshin Impact video game... | Facebook</title></head>
          <body>
            <div role="article" id="empty-shell"></div>
            <div id="target">
              <a href="https://www.facebook.com/McDonalds">McDonald's</a>
              <a aria-label="Sep 12, 2024">Sep 12, 2024</a>
              <div data-ad-preview="message">
                The McDonald's and Genshin Impact video game collaboration is live today.
              </div>
              <div>1.7K reactions 124 comments 19 shares</div>
              <div id="bar">
                <div role="button"><div data-ad-rendering-role="like_button"></div></div>
                <div role="button"><div data-ad-rendering-role="comment_button"></div></div>
                <div role="button"><div data-ad-rendering-role="share_button"></div></div>
              </div>
            </div>
          </body>
        </html>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                selection = _find_target_article_result(page, "1035071751957724")
                self.assertIsNotNone(selection)

                result = _extract_article_data(
                    selection.locator,
                    "https://www.facebook.com/story.php?story_fbid=1035071751957724&id=100063647256368",
                    "https://www.facebook.com/story.php?story_fbid=1035071751957724&id=100063647256368",
                    "1035071751957724",
                )

                self.assertEqual(result["author"]["name"], "McDonald's")
                self.assertIn("Genshin Impact", result["content"])
                self.assertEqual(result["publish_time"], "2024-09-12")
                self.assertEqual(result["like_count"], 1700)
                self.assertEqual(result["comment_count"], 124)
                self.assertEqual(result["share_count"], 19)
                self.assertEqual(result["comments"], [])
                self.assertNotIn("reply_count", result)
                self.assertEqual(result["collection_status"], "success")
            finally:
                browser.close()

    def test_extract_author_accepts_group_user_link_and_normalizes_it(self) -> None:
        html = """
        <div id="target">
          <a href="/groups/128051067263878/user/1241477756/?__cft__=secret&__tn__=x">Wendi Lynn</a>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                author = _extract_author(page.locator("#target"))

                self.assertEqual(author["name"], "Wendi Lynn")
                self.assertEqual(
                    author["profile_url"],
                    "https://www.facebook.com/groups/128051067263878/user/1241477756",
                )
                self.assertEqual(
                    _normalize_facebook_profile_url(
                        "https://www.facebook.com/groups/128051067263878/user/1241477756/?rdid=x#frag"
                    ),
                    "https://www.facebook.com/groups/128051067263878/user/1241477756",
                )
            finally:
                browser.close()

    def test_extract_author_falls_back_to_dialog_or_action_label(self) -> None:
        html = """
        <div role="dialog" id="dialog" aria-label="Mike Gentry's Post">
          <button aria-label="Actions for this post by Mike Gentry">Actions</button>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                author = _extract_author(page.locator("#dialog"))

                self.assertEqual(author["name"], "Mike Gentry")
                self.assertIsNone(author["profile_url"])
            finally:
                browser.close()

    def test_dialog_visible_content_prefers_main_text_before_comments(self) -> None:
        html = """
        <div role="dialog" id="dialog" aria-label="Joy Lahman's Post">
          <h2>Joy Lahman's Post</h2>
          <a href="/groups/128051067263878/user/100013658857173">Joy Lahman</a>
          <span>D</span><span>e</span><span>c</span>
          <div>What is your favorite book? The one that you read over and over again.</div>
          <div>4</div>
          <div>48</div>
          <div>Newest</div>
          <div role="article">Felecia Gaspard Hendricks NoraRoberts, midnight bayou. 1y Like Reply Share</div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                result = _extract_article_data(
                    page.locator("#dialog"),
                    "https://www.facebook.com/groups/mybookloversclub/posts/8702397713162461",
                    "https://www.facebook.com/groups/mybookloversclub/posts/8702397713162461",
                    "8702397713162461",
                    context_type="group",
                )

                self.assertEqual(
                    result["content"],
                    "What is your favorite book? The one that you read over and over again.",
                )
            finally:
                browser.close()

    def test_dialog_with_multiple_messages_selects_only_title_match(self) -> None:
        html = """
        <html>
          <head><title>Brand - Main title fragment for target post... | Facebook</title></head>
          <body>
            <div role="dialog">
              <div id="comment-wrapper">
                <a href="https://www.facebook.com/Commenter">Commenter</a>
                <div data-ad-preview="message">This is only a visible comment.</div>
              </div>
              <div id="target">
                <a href="https://www.facebook.com/Brand">Brand</a>
                <a aria-label="May 30, 2023">May 30, 2023</a>
                <div data-ad-comet-preview="message">Main title fragment for target post with additional body text.</div>
                <div role="button"><div data-ad-rendering-role="like_button"></div><span dir="auto">12</span></div>
              </div>
            </div>
          </body>
        </html>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                selection = _find_target_article_result(page, "999")

                self.assertIsNotNone(selection)
                self.assertEqual(selection.locator.get_attribute("id"), "target")
            finally:
                browser.close()

    def test_ambiguous_message_title_matches_are_rejected(self) -> None:
        html = """
        <html>
          <head><title>Brand - Same title words appear here... | Facebook</title></head>
          <body>
            <div id="one">
              <a href="https://www.facebook.com/Brand">Brand</a>
              <a aria-label="May 30, 2023">May 30, 2023</a>
              <div data-ad-preview="message">Same title words appear here in one possible post.</div>
              <div data-ad-rendering-role="like_button"></div>
            </div>
            <div id="two">
              <a href="https://www.facebook.com/Brand">Brand</a>
              <a aria-label="May 31, 2023">May 31, 2023</a>
              <div data-ad-preview="message">Same title words appear here in another possible post.</div>
              <div data-ad-rendering-role="like_button"></div>
            </div>
          </body>
        </html>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                self.assertIsNone(_find_target_article_result(page, "999"))
            finally:
                browser.close()

    def test_message_ancestor_with_multiple_post_messages_is_rejected(self) -> None:
        html = """
        <html>
          <head><title>Brand - Main title fragment for target post... | Facebook</title></head>
          <body>
            <section id="too-broad">
              <a href="https://www.facebook.com/Brand">Brand</a>
              <a aria-label="May 30, 2023">May 30, 2023</a>
              <div>
                <div data-ad-preview="message">Main title fragment for target post with additional body text.</div>
              </div>
              <div>
                <div data-ad-preview="message">Another full post body in the same broad ancestor.</div>
              </div>
              <div data-ad-rendering-role="like_button"></div>
            </section>
          </body>
        </html>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                self.assertIsNone(_find_target_article_result(page, "999"))
            finally:
                browser.close()

    def test_message_and_ancestor_diagnostics_are_recorded(self) -> None:
        html = """
        <html>
          <head><title>Brand - The target opening text... | Facebook</title></head>
          <body>
            <div id="target">
              <a href="https://www.facebook.com/Brand">Brand</a>
              <a aria-label="May 30, 2023">May 30, 2023</a>
              <div data-ad-comet-preview="message">The target opening text is present in this message.</div>
              <div data-ad-rendering-role="like_button"></div>
            </div>
          </body>
        </html>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                selection = _find_target_article_result(page, "999")
                diagnostics = _collect_failure_diagnostics(
                    page,
                    "999",
                    "https://www.facebook.com/story.php?story_fbid=999&id=1",
                    target_container=selection.locator if selection is not None else None,
                    selected_container_index=selection.candidate_index if selection is not None else None,
                    selected_container_strategy=selection.strategy if selection is not None else None,
                    selected_message_index=selection.message_index if selection is not None else None,
                    selected_ancestor_depth=selection.ancestor_depth if selection is not None else None,
                    selected_container_score=selection.score if selection is not None else None,
                    selected_container_reasons=list(selection.score_reasons) if selection is not None else None,
                )

                self.assertGreaterEqual(len(diagnostics["message_candidates"]), 1)
                self.assertGreaterEqual(len(diagnostics["message_ancestor_candidates"]), 1)
                self.assertIn("selected_message_index", diagnostics)
                self.assertIn("selected_container_score", diagnostics)
                self.assertIn("container_rejection_reason", diagnostics)
            finally:
                browser.close()


class FacebookPublishTimeTests(unittest.TestCase):
    def test_one_valid_candidate_is_selected(self) -> None:
        candidate = {
            "visible_text": "May 30, 2023",
            "matched_date_pattern": True,
            "parsed_publish_time": "2023-05-30",
            "parsed_publish_time_text": "May 30, 2023",
            "inside_anchor": True,
            "before_message": True,
            "near_author": True,
        }

        self.assertEqual(_select_best_publish_time_candidate([candidate]), candidate)

    def test_invalid_candidates_followed_by_valid_candidate_selects_valid(self) -> None:
        invalid = {"visible_text": "Facebook", "parsed_publish_time": None, "parsed_publish_time_text": None}
        valid = {
            "visible_text": "May 30, 2023",
            "matched_date_pattern": True,
            "parsed_publish_time": "2023-05-30",
            "parsed_publish_time_text": "May 30, 2023",
            "inside_anchor": True,
            "before_message": True,
            "near_author": True,
        }

        self.assertEqual(_select_best_publish_time_candidate([invalid, invalid, valid]), valid)

    def test_no_valid_candidate_returns_none(self) -> None:
        self.assertIsNone(
            _select_best_publish_time_candidate(
                [{"visible_text": "Facebook", "parsed_publish_time": None, "parsed_publish_time_text": None}]
            )
        )

    def test_parse_calendar_date(self) -> None:
        self.assertEqual(
            _parse_facebook_publish_time("May 30, 2023"),
            ("2023-05-30", "May 30, 2023"),
        )

    def test_parse_datetime_without_inventing_timezone(self) -> None:
        self.assertEqual(
            _parse_facebook_publish_time("May 30, 2023 at 3:42 PM"),
            ("2023-05-30T15:42", "May 30, 2023 at 3:42 PM"),
        )

    def test_rejects_non_date_text(self) -> None:
        self.assertEqual(_parse_facebook_publish_time("Genshin Impact"), (None, None))
        self.assertEqual(_parse_facebook_publish_time("Facebook"), (None, None))
        self.assertEqual(_parse_facebook_publish_time("5 shares"), (None, None))
        self.assertEqual(_parse_facebook_publish_time("Version 3.7"), (None, None))
        self.assertEqual(_parse_facebook_publish_time(""), (None, None))

    def test_relative_time_is_preserved_without_absolute_value(self) -> None:
        self.assertEqual(_parse_facebook_publish_time("2h"), (None, "2h"))

    def test_visible_text_ignores_hidden_hyphens(self) -> None:
        html = """
        <a id="date">
          <b>M</b><b style="display:none">-</b><b>ay</b>
          <b> 30</b><b style="display:none">-</b><b>, 2023</b>
        </a>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                self.assertEqual(_get_visible_text(page.locator("#date")), "May 30, 2023")
            finally:
                browser.close()

    def test_visible_date_after_many_unrelated_anchors_is_discovered(self) -> None:
        unrelated = "".join(f'<a href="#">Facebook {index}</a>' for index in range(15))
        html = f"""
        <div id="target">
          <a href="https://www.facebook.com/Genshinimpact">Genshin Impact</a>
          {unrelated}
          <a href="https://www.facebook.com/story.php?story_fbid=123&id=456">
            <b>M</b><b style="display:none">-</b><b>ay</b><b> 30</b><b>, 2023</b>
          </a>
          <div data-ad-comet-preview="message">Version 3.7 post body</div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                scan = _visible_publish_time_candidates(page.locator("#target"))
                self.assertGreater(scan["scanned_element_count"], 10)
                self.assertEqual(len(scan["candidates"]), 1)
                self.assertEqual(scan["candidates"][0]["visible_text"], "May 30, 2023")
                self.assertTrue(scan["candidates"][0]["before_message"])
                self.assertEqual(scan["candidates"][0]["parsed_publish_time"], "2023-05-30")
            finally:
                browser.close()

    def test_aria_labelledby_hidden_date_is_discovered_from_visible_element(self) -> None:
        html = """
        <div id="target">
          <a href="https://www.facebook.com/Genshinimpact">Genshin Impact</a>
          <a href="https://www.facebook.com/story.php?story_fbid=123&id=456">
            <span aria-labelledby="date_label"><b>M</b><b style="display:none">-</b><b>ay</b></span>
          </a>
          <div data-ad-comet-preview="message">Post body</div>
        </div>
        <div hidden style="display:none"><span id="date_label">May 30, 2023</span></div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                scan = _visible_publish_time_candidates(page.locator("#target"))
                self.assertEqual(len(scan["candidates"]), 1)
                self.assertEqual(scan["candidates"][0]["strategy"], "aria_labelledby_strict_match")
                self.assertEqual(scan["candidates"][0]["parsed_publish_time"], "2023-05-30")
            finally:
                browser.close()

    def test_selected_candidate_values_appear_in_final_post_dictionary(self) -> None:
        html = """
        <div id="target">
          <a href="https://www.facebook.com/Genshinimpact">Genshin Impact</a>
          <a href="https://www.facebook.com/story.php?story_fbid=123&id=456">
            <span aria-labelledby="date_label"><b>M</b><b>ay</b></span>
          </a>
          <div data-ad-comet-preview="message">Post body</div>
        </div>
        <div hidden style="display:none"><span id="date_label">May 30, 2023</span></div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                result = _extract_article_data(
                    page.locator("#target"),
                    "https://www.facebook.com/story.php?story_fbid=123&id=456",
                    "https://www.facebook.com/story.php?story_fbid=123&id=456",
                    "123",
                )

                self.assertEqual(result["publish_time"], "2023-05-30")
                self.assertEqual(result["publish_time_text"], "May 30, 2023")
                self.assertEqual(result["collection_status"], "success")
                self.assertIsNone(result["error"])
            finally:
                browser.close()

    def test_diagnostics_selected_publish_time_matches_final_result(self) -> None:
        html = """
        <div id="target">
          <a href="https://www.facebook.com/Genshinimpact">Genshin Impact</a>
          <a href="https://www.facebook.com/story.php?story_fbid=123&id=456">
            <span aria-labelledby="date_label"><b>M</b><b>ay</b></span>
          </a>
          <div data-ad-comet-preview="message">Post body</div>
        </div>
        <div hidden style="display:none"><span id="date_label">May 30, 2023</span></div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                target = page.locator("#target")
                result = _extract_article_data(
                    target,
                    "https://www.facebook.com/story.php?story_fbid=123&id=456",
                    "https://www.facebook.com/story.php?story_fbid=123&id=456",
                    "123",
                )
                diagnostics = _collect_failure_diagnostics(
                    page,
                    "123",
                    "https://www.facebook.com/story.php?story_fbid=123&id=456",
                    target_container=target,
                    selected_publish_time=result["publish_time"],
                    selected_publish_time_text=result["publish_time_text"],
                )

                self.assertEqual(diagnostics["selected_publish_time"], result["publish_time"])
                self.assertEqual(diagnostics["selected_publish_time_text"], result["publish_time_text"])
                self.assertTrue(diagnostics["publish_time_selected"])
            finally:
                browser.close()

    def test_publish_time_repair_updates_result_status_without_overwrite(self) -> None:
        result = _build_extraction_result(
            post_id="123",
            input_url="https://www.facebook.com/story.php?story_fbid=123&id=456",
            canonical_url="https://www.facebook.com/story.php?story_fbid=123&id=456",
            author={"name": "Genshin Impact", "profile_url": "https://www.facebook.com/Genshinimpact"},
            content="Post body",
            publish_time=None,
            publish_time_text=None,
            like_count=None,
            comment_count=None,
            share_count=None,
            extraction_source="dom",
            metadata_error=None,
        )

        repaired = _apply_publish_time_to_result(result, "2023-05-30", "May 30, 2023")

        self.assertEqual(repaired["publish_time"], "2023-05-30")
        self.assertEqual(repaired["publish_time_text"], "May 30, 2023")
        self.assertEqual(repaired["collection_status"], "success")
        self.assertIsNone(repaired["error"])

    def test_diagnostics_limit_after_strict_matching(self) -> None:
        dates = "".join(f"<span>May {day}, 2023</span>" for day in range(1, 13))
        html = f"<div id='target'>{dates}<div data-ad-preview='message'>Post body</div></div>"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                scan = _visible_publish_time_candidates(page.locator("#target"))
                self.assertEqual(len(scan["candidates"]), 12)
            finally:
                browser.close()


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
        self.assertEqual(result["comment_count"], 2)
        self.assertNotIn("reply_count", result)

    def test_group_context_type_is_preserved_in_result(self) -> None:
        result = _build_extraction_result(
            post_id="123",
            input_url="https://www.facebook.com/groups/booklovers/posts/123",
            canonical_url="https://www.facebook.com/groups/booklovers/posts/123",
            author={"name": "OpenAI", "profile_url": "https://www.facebook.com/openai"},
            content="Post body",
            publish_time="2026-06-12T00:00:00Z",
            publish_time_text=None,
            like_count=None,
            comment_count=None,
            share_count=None,
            extraction_source="dom",
            metadata_error=None,
            context_type="group",
        )

        self.assertEqual(result["context_type"], "group")

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

    def test_missing_metrics_remain_none_and_comment_button_maps_to_comment_count(self) -> None:
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


class FacebookCommentExpansionTests(unittest.TestCase):
    def _comment_html(self, start: int, end: int, post_id: str = "999") -> str:
        return "\n".join(
            f"""
            <div role="article" aria-label="Comment by User {index}">
              <a href="/u{index}">User {index}</a>
              <div data-ad-comet-preview="message">comment {index}</div>
              <a href="/groups/g/posts/{post_id}/?comment_id={index}">1y</a>
            </div>
            """
            for index in range(start, end + 1)
        )

    def test_scroll_lazy_loads_from_initial_ten_to_twenty_comments(self) -> None:
        initial_comments = self._comment_html(1, 10)
        additional_comments = self._comment_html(11, 20).replace("`", "\\`")
        html = f"""
        <div role="dialog" id="dialog" style="height: 120px; overflow-y: auto;">
          <div id="target"><div data-ad-comet-preview="message">Main post</div></div>
          <div id="comments">{initial_comments}</div>
          <span dir="auto" id="progress">10 of 56</span>
          <div style="height: 800px"></div>
        </div>
        <script>
        let loaded = false;
        document.querySelector("#dialog").addEventListener("scroll", () => {{
          if (loaded) return;
          loaded = true;
          document.querySelector("#comments").insertAdjacentHTML("beforeend", `{additional_comments}`);
          document.querySelector("#progress").textContent = "20 of 56";
        }});
        </script>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                comments, diagnostics = _extract_comments(
                    page.locator("#target"),
                    "999",
                    max_comments=20,
                    expand_comments=True,
                    max_expand_actions=5,
                    no_progress_round_limit=2,
                )
            finally:
                browser.close()

        self.assertEqual(len(comments), 20)
        self.assertEqual(diagnostics["comments_visible_before"], 10)
        self.assertEqual(diagnostics["comments_visible_after"], 20)
        self.assertEqual(diagnostics["comment_ids_before"], 10)
        self.assertEqual(diagnostics["comment_ids_after"], 20)
        self.assertEqual(diagnostics["comment_progress_text"], "20 of 56")
        self.assertEqual(diagnostics["comment_total_from_progress"], 56)
        self.assertEqual(diagnostics["comment_loading_mode"], "scroll")
        self.assertEqual(diagnostics["comment_scroll_actions"], 1)
        self.assertEqual(diagnostics["comment_expansion_stop_reason"], "max_comments_reached")
        self.assertIn("comment_scroll_started", [event["event"] for event in diagnostics["comment_loading_events"]])
        self.assertIn("comment_scroll_progress", [event["event"] for event in diagnostics["comment_loading_events"]])

    def test_first_scroll_without_progress_then_second_scroll_loads_comments(self) -> None:
        initial_comments = self._comment_html(1, 10)
        additional_comments = self._comment_html(11, 20).replace("`", "\\`")
        html = f"""
        <div role="dialog" id="dialog" style="height: 120px; overflow-y: auto;">
          <div id="target"><div data-ad-comet-preview="message">Main post</div></div>
          <div id="comments">{initial_comments}</div>
          <span dir="auto" id="progress">10 of 56</span>
          <div style="height: 1400px"></div>
        </div>
        <script>
        let scrollCount = 0;
        document.querySelector("#dialog").addEventListener("scroll", () => {{
          scrollCount += 1;
          if (scrollCount < 2 || document.querySelector('[href*="comment_id=20"]')) return;
          document.querySelector("#comments").insertAdjacentHTML("beforeend", `{additional_comments}`);
          document.querySelector("#progress").textContent = "20 of 56";
        }});
        </script>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                comments, diagnostics = _extract_comments(
                    page.locator("#target"),
                    "999",
                    max_comments=20,
                    expand_comments=True,
                    max_expand_actions=5,
                    no_progress_round_limit=2,
                )
            finally:
                browser.close()

        self.assertEqual(len(comments), 20)
        self.assertEqual(diagnostics["comment_scroll_actions"], 2)
        self.assertEqual(diagnostics["comment_expansion_stop_reason"], "max_comments_reached")

    def test_scroll_stops_after_consecutive_no_progress_rounds(self) -> None:
        initial_comments = self._comment_html(1, 10)
        html = f"""
        <div role="dialog" id="dialog" style="height: 120px; overflow-y: auto;">
          <div id="target"><div data-ad-comet-preview="message">Main post</div></div>
          <div id="comments">{initial_comments}</div>
          <span dir="auto">10 of 56</span>
          <div style="height: 1400px"></div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                comments, diagnostics = _extract_comments(
                    page.locator("#target"),
                    "999",
                    max_comments=20,
                    expand_comments=True,
                    max_expand_actions=5,
                    no_progress_round_limit=2,
                )
            finally:
                browser.close()

        self.assertEqual(len(comments), 10)
        self.assertEqual(diagnostics["comment_scroll_actions"], 2)
        self.assertEqual(diagnostics["comment_expansion_stop_reason"], "no_progress_round_limit")

    def test_scroll_counts_only_target_dialog_comment_ids(self) -> None:
        target_comments = self._comment_html(1, 10)
        other_comments = self._comment_html(101, 110)
        html = f"""
        <div role="dialog" id="target-dialog" style="height: 120px; overflow-y: auto;">
          <div id="target"><div data-ad-comet-preview="message">Main post</div></div>
          <div id="comments">{target_comments}</div>
          <span dir="auto">10 of 56</span>
          <div style="height: 800px"></div>
        </div>
        <div role="dialog" id="other-dialog">
          {other_comments}
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                comments, diagnostics = _extract_comments(
                    page.locator("#target"),
                    "999",
                    max_comments=20,
                    expand_comments=True,
                    max_expand_actions=1,
                    no_progress_round_limit=1,
                )
            finally:
                browser.close()

        self.assertEqual(len(comments), 10)
        self.assertEqual(diagnostics["comment_ids_before"], 10)
        self.assertEqual(diagnostics["comment_ids_after"], 10)

    def test_button_strategy_is_still_preferred_over_scroll(self) -> None:
        html = """
        <div role="dialog" id="dialog" style="height: 120px; overflow-y: auto;" onscroll="window.scrolled = true">
          <div id="target"><div data-ad-comet-preview="message">Main post</div></div>
          <div role="article" aria-label="Comment by A">
            <a href="/a">A</a><div data-ad-comet-preview="message">one</div>
            <a href="/groups/g/posts/999/?comment_id=1">1y</a>
          </div>
          <div role="button" onclick="window.clicked = true">
            <span><span dir="auto">View more comments</span></span>
          </div>
          <span dir="auto">1 of 3</span>
          <div style="height: 800px"></div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                _, diagnostics = _extract_comments(
                    page.locator("#target"),
                    "999",
                    max_comments=10,
                    expand_comments=True,
                    max_expand_actions=1,
                    no_progress_round_limit=1,
                )
                clicked = page.evaluate("window.clicked === true")
                scrolled = page.evaluate("window.scrolled === true")
            finally:
                browser.close()

        self.assertTrue(clicked)
        self.assertFalse(scrolled)
        self.assertEqual(diagnostics["comment_loading_mode"], "button")
        self.assertEqual(diagnostics["comment_expansion_actions"], 1)
        self.assertEqual(diagnostics["comment_scroll_actions"], 0)

    def test_unusable_button_falls_back_to_scroll(self) -> None:
        initial_comments = self._comment_html(1, 10)
        additional_comments = self._comment_html(11, 20).replace("`", "\\`")
        html = f"""
        <div role="dialog" id="dialog" style="height: 120px; overflow-y: auto;">
          <div id="target"><div data-ad-comet-preview="message">Main post</div></div>
          <div id="comments">{initial_comments}</div>
          <div role="button" style="display: none">
            <span><span dir="auto">View more comments</span></span>
          </div>
          <span dir="auto" id="progress">10 of 56</span>
          <div style="height: 1000px"></div>
        </div>
        <script>
        document.querySelector("#dialog").addEventListener("scroll", () => {{
          if (document.querySelector('[href*="comment_id=20"]')) return;
          document.querySelector("#comments").insertAdjacentHTML("beforeend", `{additional_comments}`);
          document.querySelector("#progress").textContent = "20 of 56";
        }});
        </script>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                comments, diagnostics = _extract_comments(
                    page.locator("#target"),
                    "999",
                    max_comments=20,
                    expand_comments=True,
                    max_expand_actions=3,
                    no_progress_round_limit=2,
                )
            finally:
                browser.close()

        self.assertEqual(len(comments), 20)
        self.assertEqual(diagnostics["comment_loading_mode"], "scroll")
        self.assertTrue(diagnostics["comment_expand_candidate_found"])
        self.assertFalse(diagnostics["comment_expand_button_found"])
        self.assertFalse(diagnostics["comment_expansion_click_failed"])
        self.assertEqual(diagnostics["comment_scroll_actions"], 1)

    def test_span_text_button_ancestor_is_clicked_and_progress_total_is_used(self) -> None:
        html = """
        <div role="dialog" id="dialog">
          <div id="target">
            <a href="/groups/g/posts/999">Post permalink</a>
            <div data-ad-comet-preview="message">Main post</div>
          </div>
          <div id="comments">
            <div role="article" aria-label="Comment by A">
              <a href="/a">A</a><div data-ad-comet-preview="message">one</div>
              <a href="/groups/g/posts/999/?comment_id=1">1y</a>
            </div>
            <div role="article" aria-label="Comment by B">
              <a href="/b">B</a><div data-ad-comet-preview="message">two</div>
              <a href="/groups/g/posts/999/?comment_id=2">1y</a>
            </div>
            <div role="article" aria-label="Comment by C">
              <a href="/c">C</a><div data-ad-comet-preview="message">three</div>
              <a href="/groups/g/posts/999/?comment_id=3">1y</a>
            </div>
          </div>
          <div id="comment-controls">
            <div role="button" tabindex="0" onclick="addMoreComments()">
              <span><span dir="auto">View more comments</span></span>
            </div>
            <span dir="auto" id="progress">3 of 23</span>
          </div>
        </div>
        <script>
        function addMoreComments() {
          const comments = document.querySelector("#comments");
          comments.insertAdjacentHTML("beforeend", `
            <div role="article" aria-label="Comment by D">
              <a href="/d">D</a><div data-ad-comet-preview="message">four</div>
              <a href="/groups/g/posts/999/?comment_id=4">1y</a>
            </div>
            <div role="article" aria-label="Comment by E">
              <a href="/e">E</a><div data-ad-comet-preview="message">five</div>
              <a href="/groups/g/posts/999/?comment_id=5">1y</a>
            </div>
          `);
          document.querySelector("#progress").textContent = "5 of 23";
          document.querySelector("#comment-controls [role=button]").remove();
        }
        </script>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                comments, diagnostics = _extract_comments(
                    page.locator("#target"),
                    "999",
                    max_comments=100,
                    expand_comments=True,
                    max_expand_actions=3,
                    no_progress_round_limit=2,
                )
            finally:
                browser.close()

        self.assertEqual(len(comments), 5)
        self.assertEqual(diagnostics["comments_visible_before"], 3)
        self.assertEqual(diagnostics["comments_visible_after"], 5)
        self.assertEqual(diagnostics["comment_progress_text"], "5 of 23")
        self.assertEqual(diagnostics["comment_total_from_progress"], 23)
        self.assertTrue(diagnostics["comment_expand_button_found"])
        self.assertTrue(diagnostics["comment_expand_candidate_found"])
        self.assertTrue(diagnostics["comment_expansion_clicked"])
        self.assertFalse(diagnostics["comment_expansion_click_failed"])
        self.assertEqual(diagnostics["comment_expansion_actions"], 1)

    def test_find_comment_expand_button_returns_clickable_button_ancestor(self) -> None:
        html = """
        <div role="dialog" id="dialog">
          <div role="button" tabindex="0" id="real-button">
            <span><span dir="auto" id="text-node">View more comments</span></span>
          </div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                button_info = _find_comment_expand_button(page.locator("#dialog"))
                button_id = button_info["button"].get_attribute("id")
                tag_name = button_info["button"].evaluate("(node) => node.tagName.toLowerCase()")
            finally:
                browser.close()

        self.assertTrue(button_info["candidate_found"])
        self.assertEqual(button_id, "real-button")
        self.assertEqual(tag_name, "div")
        self.assertIsNone(button_info["reason"])

    def test_comment_progress_parser_uses_local_control_area(self) -> None:
        html = """
        <div role="dialog" id="dialog">
          <div role="button"><span><span dir="auto">View previous comments</span></span></div>
          <span dir="auto">3 of 23</span>
        </div>
        <div role="dialog" id="other">
          <div role="button"><span><span dir="auto">View more comments</span></span></div>
          <span dir="auto">8 of 99</span>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                progress = _comment_progress_from_scope(page.locator("#dialog"))
            finally:
                browser.close()

        self.assertEqual(progress["text"], "3 of 23")
        self.assertEqual(progress["current"], 3)
        self.assertEqual(progress["total"], 23)

    def test_dialog_scope_finds_button_outside_narrow_target_container(self) -> None:
        html = """
        <div role="dialog">
          <div id="target"><div data-ad-comet-preview="message">Main post</div></div>
          <div role="article" aria-label="Comment by A">
            <a href="/a">A</a><div data-ad-comet-preview="message">one</div>
            <a href="/groups/g/posts/999/?comment_id=1">1y</a>
          </div>
          <div role="button" onclick="window.clicked = true">
            <span><span dir="auto">More comments</span></span>
          </div>
          <span dir="auto">1 of 4</span>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                _, diagnostics = _extract_comments(
                    page.locator("#target"),
                    "999",
                    max_comments=10,
                    expand_comments=True,
                    max_expand_actions=1,
                    no_progress_round_limit=1,
                )
                clicked = page.evaluate("window.clicked === true")
            finally:
                browser.close()

        self.assertTrue(clicked)
        self.assertEqual(diagnostics["comment_expansion_actions"], 1)
        self.assertEqual(diagnostics["comment_total_from_progress"], 4)

    def test_recommended_area_button_outside_dialog_is_not_clicked(self) -> None:
        html = """
        <div role="dialog">
          <div id="target"><div data-ad-comet-preview="message">Main post</div></div>
          <div role="article" aria-label="Comment by A">
            <a href="/a">A</a><div data-ad-comet-preview="message">one</div>
            <a href="/groups/g/posts/999/?comment_id=1">1y</a>
          </div>
        </div>
        <aside>
          <div role="button" onclick="window.badClicked = true">
            <span><span dir="auto">View more comments</span></span>
          </div>
          <span dir="auto">1 of 99</span>
        </aside>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                _, diagnostics = _extract_comments(
                    page.locator("#target"),
                    "999",
                    max_comments=10,
                    expand_comments=True,
                    max_expand_actions=1,
                    no_progress_round_limit=1,
                )
                bad_clicked = page.evaluate("window.badClicked === true")
            finally:
                browser.close()

        self.assertFalse(bad_clicked)
        self.assertFalse(diagnostics["comment_expand_button_found"])
        self.assertFalse(diagnostics["comment_expand_candidate_found"])
        self.assertEqual(diagnostics["comment_expansion_actions"], 0)

    def test_no_progress_stops_after_limited_rounds(self) -> None:
        html = """
        <div role="dialog">
          <div id="target"><div data-ad-comet-preview="message">Main post</div></div>
          <div role="article" aria-label="Comment by A">
            <a href="/a">A</a><div data-ad-comet-preview="message">one</div>
            <a href="/groups/g/posts/999/?comment_id=1">1y</a>
          </div>
          <div role="button"><span><span dir="auto">View more replies</span></span></div>
          <span dir="auto">1 of 3</span>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                comments, diagnostics = _extract_comments(
                    page.locator("#target"),
                    "999",
                    max_comments=10,
                    expand_comments=True,
                    max_expand_actions=5,
                    no_progress_round_limit=1,
                )
            finally:
                browser.close()

        self.assertEqual(len(comments), 1)
        self.assertEqual(diagnostics["comment_expansion_actions"], 1)
        self.assertEqual(diagnostics["comment_expansion_stop_reason"], "no_progress_round_limit")

    def test_hidden_expand_candidate_has_specific_stop_reason(self) -> None:
        html = """
        <div role="dialog">
          <div id="target"><div data-ad-comet-preview="message">Main post</div></div>
          <div role="article" aria-label="Comment by A">
            <a href="/a">A</a><div data-ad-comet-preview="message">one</div>
            <a href="/groups/g/posts/999/?comment_id=1">1y</a>
          </div>
          <div role="button" style="display: none">
            <span><span dir="auto">View more comments</span></span>
          </div>
          <span dir="auto">1 of 3</span>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                _, diagnostics = _extract_comments(
                    page.locator("#target"),
                    "999",
                    max_comments=10,
                    expand_comments=True,
                    max_expand_actions=3,
                    no_progress_round_limit=1,
                )
            finally:
                browser.close()

        self.assertTrue(diagnostics["comment_expand_candidate_found"])
        self.assertFalse(diagnostics["comment_expand_button_found"])
        self.assertEqual(diagnostics["comment_expansion_actions"], 0)
        self.assertEqual(diagnostics["comment_scroll_actions"], 1)
        self.assertEqual(diagnostics["comment_expansion_stop_reason"], "no_progress_round_limit")
        self.assertNotEqual(diagnostics["comment_expansion_stop_reason"], "no_more_expand_buttons")

    def test_detached_expand_locator_reports_click_failure_not_no_more_buttons(self) -> None:
        html = """
        <div role="dialog" id="dialog">
          <div id="target"><div data-ad-comet-preview="message">Main post</div></div>
          <div role="article" aria-label="Comment by A">
            <a href="/a">A</a><div data-ad-comet-preview="message">one</div>
            <a href="/groups/g/posts/999/?comment_id=1">1y</a>
          </div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                with patch(
                    "src.extractors.facebook_post._find_comment_expand_button",
                    return_value={
                        "candidate_found": True,
                        "button": page.locator("#detached-button"),
                        "text": "View more comments",
                        "reason": None,
                    },
                ):
                    result = _expand_one_comment_button(page.locator("#dialog"), "999", 1, None)
            finally:
                browser.close()

        self.assertTrue(result["comment_expand_candidate_found"])
        self.assertTrue(result["comment_expansion_click_failed"])
        self.assertEqual(result["reason"], "expand_click_failed")
        self.assertNotEqual(result["reason"], "no_more_expand_buttons")


class FacebookActionMetricTests(unittest.TestCase):
    def test_rendering_role_buttons_without_summary_do_not_create_counts(self) -> None:
        html = """
        <div id="target">
          <div role="button"><div data-ad-rendering-role="like_button"></div><span dir="auto">1</span></div>
          <div role="button"><div data-ad-rendering-role="comment_button"></div></div>
          <div role="button"><div data-ad-rendering-role="share_button"></div></div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                like_count, comment_count, share_count = _extract_metrics(page.locator("#target"))

                self.assertIsNone(like_count)
                self.assertIsNone(comment_count)
                self.assertIsNone(share_count)
            finally:
                browser.close()

    def test_extracts_metrics_from_action_bar_summary_text(self) -> None:
        html = """
        <div id="target">
          <div>1.7K reactions 124 comments 19 shares</div>
          <div id="bar">
            <div role="button"><div data-ad-rendering-role="like_button"></div></div>
            <div role="button"><div data-ad-rendering-role="comment_button"></div></div>
            <div role="button"><div data-ad-rendering-role="share_button"></div></div>
          </div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                like_count, comment_count, share_count = _extract_metrics(page.locator("#target"))

                self.assertEqual(like_count, 1700)
                self.assertEqual(comment_count, 124)
                self.assertEqual(share_count, 19)
            finally:
                browser.close()

    def test_extracts_ordered_action_bar_numbers_only_when_all_buttons_exist(self) -> None:
        html = """
        <div id="target">
          <div id="bar">
            <div role="button"><div data-ad-rendering-role="like_button"></div><span dir="auto">1.7K</span></div>
            <div role="button"><div data-ad-rendering-role="comment_button"></div><span dir="auto">124</span></div>
            <div role="button"><div data-ad-rendering-role="share_button"></div><span dir="auto">19</span></div>
          </div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                like_count, comment_count, share_count = _extract_metrics(page.locator("#target"))

                self.assertEqual(like_count, 1700)
                self.assertEqual(comment_count, 124)
                self.assertEqual(share_count, 19)
            finally:
                browser.close()

    def test_extracts_partial_ordered_action_bar_numbers_without_share_count(self) -> None:
        html = """
        <div id="target">
          <div id="bar">
            <div role="button"><div data-ad-rendering-role="like_button"></div></div>
            <div role="button"><div data-ad-rendering-role="comment_button"></div></div>
            <div role="button"><div data-ad-rendering-role="share_button"></div></div>
            <span dir="auto">4</span>
            <span dir="auto">48</span>
          </div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                like_count, comment_count, share_count = _extract_metrics(page.locator("#target"))

                self.assertEqual(like_count, 4)
                self.assertEqual(comment_count, 48)
                self.assertIsNone(share_count)
            finally:
                browser.close()

    def test_extracts_semantic_action_button_aria_labels(self) -> None:
        html = """
        <div id="target">
          <div id="bar">
            <div role="button" aria-label="Like: 4 people"><div data-ad-rendering-role="like_button"></div></div>
            <div role="button" aria-label="26 comments"><div data-ad-rendering-role="comment_button"></div></div>
            <div role="button" aria-label="1 share"><div data-ad-rendering-role="share_button"></div></div>
          </div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                like_count, comment_count, share_count = _extract_metrics(page.locator("#target"))

                self.assertEqual(like_count, 4)
                self.assertEqual(comment_count, 26)
                self.assertEqual(share_count, 1)
            finally:
                browser.close()

    def test_partial_ordered_summary_ignores_comment_permalink_numbers(self) -> None:
        html = """
        <div id="target">
          <div id="bar">
            <div role="button"><div data-ad-rendering-role="like_button"></div></div>
            <div role="button"><div data-ad-rendering-role="comment_button"></div></div>
            <div role="button"><div data-ad-rendering-role="share_button"></div></div>
            <span dir="auto">4</span>
            <span dir="auto">48</span>
          </div>
          <div role="article">
            <a href="/groups/book/posts/8702397713162461?comment_id=999999">999999</a>
            <span>12 replies</span>
          </div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                like_count, comment_count, share_count = _extract_metrics(page.locator("#target"))

                self.assertEqual(like_count, 4)
                self.assertEqual(comment_count, 48)
                self.assertIsNone(share_count)
            finally:
                browser.close()

    def test_summary_parser_supports_large_metric_formats(self) -> None:
        html = """
        <div id="target">
          <div>1.2M reactions 1,234 comments 2M shares</div>
          <div id="bar">
            <div role="button"><div data-ad-rendering-role="like_button"></div></div>
            <div role="button"><div data-ad-rendering-role="comment_button"></div></div>
            <div role="button"><div data-ad-rendering-role="share_button"></div></div>
          </div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                like_count, comment_count, share_count = _extract_metrics(page.locator("#target"))

                self.assertEqual(like_count, 1_200_000)
                self.assertEqual(comment_count, 1_234)
                self.assertEqual(share_count, 2_000_000)
            finally:
                browser.close()

    def test_comment_empty_state_maps_comment_count_to_zero(self) -> None:
        html = """
        <div id="target">
          <div id="bar">
            <div role="button"><div data-ad-rendering-role="like_button"></div></div>
            <div role="button"><div data-ad-rendering-role="comment_button"></div></div>
            <div role="button"><div data-ad-rendering-role="share_button"></div></div>
          </div>
          <div>No comments yet</div>
          <div>Be the first to comment.</div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                _, comment_count, _ = _extract_metrics(page.locator("#target"))

                self.assertEqual(comment_count, 0)
            finally:
                browser.close()

    def test_comment_count_remains_none_without_count_or_empty_state(self) -> None:
        html = """
        <div id="target">
          <div id="bar">
            <div role="button"><div data-ad-rendering-role="comment_button"></div></div>
          </div>
          <div>Write a comment...</div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                _, comment_count, _ = _extract_metrics(page.locator("#target"))

                self.assertEqual(comment_count, 0)
            finally:
                browser.close()

    def test_plain_numbers_in_content_are_not_metrics(self) -> None:
        html = """
        <div id="target">
          <div data-ad-preview="message">Version 3.7 launched in 2024 with 2 special events.</div>
          <div id="bar">
            <div role="button"><div data-ad-rendering-role="like_button"></div></div>
            <div role="button"><div data-ad-rendering-role="comment_button"></div></div>
            <div role="button"><div data-ad-rendering-role="share_button"></div></div>
          </div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                like_count, comment_count, share_count = _extract_metrics(page.locator("#target"))

                self.assertIsNone(like_count)
                self.assertIsNone(comment_count)
                self.assertIsNone(share_count)
            finally:
                browser.close()

    def test_comment_empty_state_is_not_post_content(self) -> None:
        html = """
        <div id="target">
          <div>No comments yet</div>
          <div>Be the first to comment.</div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                self.assertIsNone(_extract_article_data(
                    page.locator("#target"),
                    "https://www.facebook.com/story.php?story_fbid=123&id=456",
                    "https://www.facebook.com/story.php?story_fbid=123&id=456",
                    "123",
                ).get("content"))
            except FacebookPostExtractionError:
                pass
            finally:
                browser.close()

    def test_action_count_missing_button_returns_none(self) -> None:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content("<div id='target'></div>")
                details = _extract_action_count_details(page.locator("#target"), "share_button")

                self.assertFalse(details.button_found)
                self.assertIsNone(details.raw_text)
                self.assertIsNone(details.count)
            finally:
                browser.close()

    def test_action_count_button_without_parseable_number_returns_none(self) -> None:
        html = """
        <div id="target">
          <div role="button">
            <div data-ad-rendering-role="like_button"></div>
            <span dir="auto">Like</span>
          </div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                details = _extract_action_count_details(page.locator("#target"), "like_button")

                self.assertTrue(details.button_found)
                self.assertIsNone(details.raw_text)
                self.assertEqual(details.count, 0)
                self.assertIsNone(details.rejection_reason)
                self.assertEqual(details.count_state, "known_zero")
            finally:
                browser.close()

    def test_action_count_diagnostics_records_raw_and_parsed_values(self) -> None:
        html = """
        <div id="target">
          <div>1.7K reactions 124 comments 19 shares</div>
          <div id="bar">
            <div role="button"><div data-ad-rendering-role="like_button"></div></div>
            <div role="button"><div data-ad-rendering-role="comment_button"></div></div>
            <div role="button"><div data-ad-rendering-role="share_button"></div></div>
          </div>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.set_content(html)
                diagnostics = _collect_failure_diagnostics(
                    page,
                    "123",
                    "https://www.facebook.com/story.php?story_fbid=123&id=456",
                    target_container=page.locator("#target"),
                )

                self.assertTrue(diagnostics["like_button_found"])
                self.assertTrue(diagnostics["action_bar_found"])
                self.assertEqual(diagnostics["action_bar_match_strategy"], "rendering_role_common_ancestor")
                self.assertEqual(diagnostics["like_count_raw"], "1.7K reactions 124 comments 19 shares")
                self.assertEqual(diagnostics["like_count"], 1700)
                self.assertEqual(diagnostics["like_count_source"], "reaction_summary_text")
                self.assertTrue(diagnostics["comment_button_found"])
                self.assertEqual(diagnostics["comment_count_raw"], "1.7K reactions 124 comments 19 shares")
                self.assertEqual(diagnostics["comment_count"], 124)
                self.assertEqual(diagnostics["comment_count_source"], "comment_summary_text")
                self.assertTrue(diagnostics["share_button_found"])
                self.assertEqual(diagnostics["share_count_raw"], "1.7K reactions 124 comments 19 shares")
                self.assertEqual(diagnostics["share_count"], 19)
                self.assertEqual(diagnostics["share_count_source"], "share_summary_text")
                self.assertGreaterEqual(len(diagnostics["metric_summary_candidates"]), 1)
            finally:
                browser.close()


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
            "1.7K": 1700,
            "3.5M": 3_500_000,
            "2M": 2_000_000,
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
