from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import main
from src.batch.facebook_batch import (
    DEFAULT_BATCH_DIAGNOSTICS_DIR,
    FacebookBatchItem,
    clean_facebook_batch_url,
    load_facebook_batch_input,
    prepare_facebook_batch_items,
    run_facebook_batch,
)
from src.extractors.facebook_post import FacebookLoginRequiredError, FacebookPostNotFoundError


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _touch_storage_state(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"cookies":[],"origins":[]}', encoding="utf-8")


def _success_payload(url: str, post_id: str = "123") -> dict[str, object]:
    return {
        "platform": "facebook",
        "post_id": post_id,
        "input_url": url,
        "canonical_url": url,
        "author": {"name": "Example", "profile_url": "https://www.facebook.com/example"},
        "content": "Post body",
        "publish_time": "2026-06-15",
        "like_count": 1,
        "share_count": 2,
        "comment_count": 3,
        "comments": [],
        "collected_at": "2026-06-15T00:00:00Z",
        "collection_status": "success",
        "extraction_source": "dom",
        "error": None,
    }


class FacebookBatchInputTests(unittest.TestCase):
    def test_loads_string_and_object_inputs_with_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "input.json"
            _write_json(
                path,
                [
                    "https://www.facebook.com/example/posts/123",
                    {
                        "url": "https://www.facebook.com/story.php?story_fbid=456&id=1",
                        "title": "Story",
                        "rank": 2,
                    },
                ],
            )

            items = load_facebook_batch_input(path)

        self.assertEqual(items[0]["url"], "https://www.facebook.com/example/posts/123")
        self.assertEqual(items[0]["metadata"], {})
        self.assertEqual(items[1]["metadata"], {"title": "Story", "rank": 2})

    def test_loads_posts_object_and_prefers_canonical_url(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "post_urls.json"
            _write_json(
                path,
                {
                    "posts": [
                        {
                            "original_url": "https://bad.example",
                            "canonical_url": "https://www.facebook.com/example/posts/123",
                            "title": "Title",
                        }
                    ]
                },
            )

            items = load_facebook_batch_input(path)

        self.assertEqual(items[0]["url"], "https://www.facebook.com/example/posts/123")
        self.assertEqual(items[0]["metadata"], {"title": "Title"})

    def test_clean_url_removes_fragment_and_tracking_but_keeps_story_identity(self) -> None:
        cleaned = clean_facebook_batch_url(
            "https://www.facebook.com/story.php?story_fbid=123&id=456&rdid=secret&fbclid=x#frag"
        )

        self.assertEqual(cleaned, "https://www.facebook.com/story.php?story_fbid=123&id=456")

    def test_prepare_dedupes_supported_urls_and_marks_unsupported(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "input.json"
            _write_json(
                path,
                [
                    "https://www.facebook.com/example/posts/123",
                    "https://www.facebook.com/example/posts/123?rdid=secret",
                    "https://www.facebook.com/share/p/abc",
                ],
            )

            items = prepare_facebook_batch_items(path)

        self.assertIsNone(items[0].skip_reason)
        self.assertEqual(items[1].skip_reason, "duplicate_facebook_post")
        self.assertEqual(items[2].skip_reason, "unsupported_facebook_url")

    def test_prepare_accepts_group_post_but_skips_group_homepage(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "input.json"
            _write_json(
                path,
                [
                    "https://www.facebook.com/groups/booklovers/posts/1234567890/?rdid=abc#comments",
                    "https://www.facebook.com/groups/booklovers/",
                ],
            )

            items = prepare_facebook_batch_items(path)

        self.assertIsNone(items[0].skip_reason)
        self.assertEqual(items[0].post_id, "1234567890")
        self.assertEqual(items[0].canonical_url, "https://www.facebook.com/groups/booklovers/posts/1234567890")
        self.assertEqual(items[1].skip_reason, "unsupported_facebook_url")


class FacebookBatchRunTests(unittest.TestCase):
    def test_success_failure_and_skip_are_written_as_jsonl(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            input_path = temp / "input.json"
            output_path = temp / "out.jsonl"
            summary_path = temp / "summary.json"
            storage = temp / "state.json"
            _touch_storage_state(storage)
            _write_json(
                input_path,
                [
                    "https://www.facebook.com/example/posts/123",
                    "https://www.facebook.com/example/posts/456",
                    "https://www.facebook.com/share/p/unsupported",
                ],
            )

            def extractor(_context: object, url: str) -> dict[str, object]:
                if url.endswith("/456"):
                    raise FacebookPostNotFoundError("not found")
                return _success_payload(url)

            summary = run_facebook_batch(
                input_path=input_path,
                storage_state_path=storage,
                output_path=output_path,
                summary_path=summary_path,
                diagnostics_dir=temp / "debug",
                extractor=extractor,
                sleep_fn=lambda _seconds: None,
            )
            records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]["collection_status"], "success")
        self.assertEqual(records[1]["collection_status"], "failed")
        self.assertEqual(records[2]["collection_status"], "skipped")
        self.assertEqual(summary["current_run"]["failed_count"], 1)

    def test_resume_skips_existing_processed_url(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            input_path = temp / "input.json"
            output_path = temp / "out.jsonl"
            summary_path = temp / "summary.json"
            storage = temp / "state.json"
            _touch_storage_state(storage)
            url = "https://www.facebook.com/example/posts/123"
            _write_json(input_path, [url])
            output_path.write_text(json.dumps({"input_url": url, "collection_status": "success"}) + "\n", encoding="utf-8")
            calls = 0

            def extractor(_context: object, _url: str) -> dict[str, object]:
                nonlocal calls
                calls += 1
                return _success_payload(_url)

            summary = run_facebook_batch(
                input_path=input_path,
                storage_state_path=storage,
                output_path=output_path,
                summary_path=summary_path,
                diagnostics_dir=temp / "debug",
                extractor=extractor,
                resume=True,
                sleep_fn=lambda _seconds: None,
            )

        self.assertEqual(calls, 0)
        self.assertEqual(summary["current_run"]["skipped_count"], 1)

    def test_non_resume_replaces_existing_output_after_success(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            input_path = temp / "input.json"
            output_path = temp / "out.jsonl"
            summary_path = temp / "summary.json"
            storage = temp / "state.json"
            _touch_storage_state(storage)
            _write_json(input_path, ["https://www.facebook.com/example/posts/123"])
            output_path.write_text("old\n", encoding="utf-8")

            run_facebook_batch(
                input_path=input_path,
                storage_state_path=storage,
                output_path=output_path,
                summary_path=summary_path,
                diagnostics_dir=temp / "debug",
                extractor=lambda _context, url: _success_payload(url),
                sleep_fn=lambda _seconds: None,
            )

            self.assertNotIn("old", output_path.read_text(encoding="utf-8"))

    def test_non_resume_interruption_does_not_truncate_existing_output(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            input_path = temp / "input.json"
            output_path = temp / "out.jsonl"
            summary_path = temp / "summary.json"
            storage = temp / "state.json"
            _touch_storage_state(storage)
            _write_json(input_path, ["https://www.facebook.com/example/posts/123"])
            original = json.dumps({"input_url": "old", "collection_status": "success"}) + "\n"
            output_path.write_text(original, encoding="utf-8")

            def interrupted(_context: object, _url: str) -> dict[str, object]:
                raise KeyboardInterrupt()

            with self.assertRaises(KeyboardInterrupt):
                run_facebook_batch(
                    input_path=input_path,
                    storage_state_path=storage,
                    output_path=output_path,
                    summary_path=summary_path,
                    diagnostics_dir=temp / "debug",
                    extractor=interrupted,
                    sleep_fn=lambda _seconds: None,
                )

            self.assertEqual(output_path.read_text(encoding="utf-8"), original)

    def test_login_wall_threshold_marks_remaining_items_skipped(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            input_path = temp / "input.json"
            output_path = temp / "out.jsonl"
            summary_path = temp / "summary.json"
            storage = temp / "state.json"
            _touch_storage_state(storage)
            _write_json(
                input_path,
                [
                    "https://www.facebook.com/example/posts/1",
                    "https://www.facebook.com/example/posts/2",
                    "https://www.facebook.com/example/posts/3",
                ],
            )

            run_facebook_batch(
                input_path=input_path,
                storage_state_path=storage,
                output_path=output_path,
                summary_path=summary_path,
                diagnostics_dir=temp / "debug",
                extractor=lambda _context, _url: (_ for _ in ()).throw(FacebookLoginRequiredError("login")),
                sleep_fn=lambda _seconds: None,
            )
            records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(records[0]["collection_status"], "failed")
        self.assertEqual(records[1]["collection_status"], "failed")
        self.assertEqual(records[2]["collection_status"], "skipped")
        self.assertEqual(records[2]["skip_reason"], "batch_stopped_due_to_login_wall")

    def test_retryable_error_is_retried_once(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            input_path = temp / "input.json"
            output_path = temp / "out.jsonl"
            summary_path = temp / "summary.json"
            storage = temp / "state.json"
            _touch_storage_state(storage)
            _write_json(input_path, ["https://www.facebook.com/example/posts/123"])
            calls = 0

            def extractor(_context: object, url: str) -> dict[str, object]:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RuntimeError("navigation timeout")
                return _success_payload(url)

            run_facebook_batch(
                input_path=input_path,
                storage_state_path=storage,
                output_path=output_path,
                summary_path=summary_path,
                diagnostics_dir=temp / "debug",
                extractor=extractor,
                sleep_fn=lambda _seconds: None,
            )

        self.assertEqual(calls, 2)

    def test_non_retryable_error_is_not_retried(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            input_path = temp / "input.json"
            output_path = temp / "out.jsonl"
            summary_path = temp / "summary.json"
            storage = temp / "state.json"
            _touch_storage_state(storage)
            _write_json(input_path, ["https://www.facebook.com/example/posts/123"])
            calls = 0

            def extractor(_context: object, _url: str) -> dict[str, object]:
                nonlocal calls
                calls += 1
                raise FacebookPostNotFoundError("not found")

            run_facebook_batch(
                input_path=input_path,
                storage_state_path=storage,
                output_path=output_path,
                summary_path=summary_path,
                diagnostics_dir=temp / "debug",
                extractor=extractor,
                sleep_fn=lambda _seconds: None,
            )

        self.assertEqual(calls, 1)

    def test_diagnostics_files_are_copied_with_unique_names(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            input_path = temp / "input.json"
            output_path = temp / "out.jsonl"
            summary_path = temp / "summary.json"
            diagnostics_dir = temp / "debug"
            storage = temp / "state.json"
            _touch_storage_state(storage)
            _write_json(input_path, ["https://www.facebook.com/example/posts/123"])
            source = temp / "facebook_post_diagnostics.json"
            source.write_text("{}", encoding="utf-8")

            with patch("src.batch.facebook_batch.FAILURE_DIAGNOSTICS_PATH", source):
                run_facebook_batch(
                    input_path=input_path,
                    storage_state_path=storage,
                    output_path=output_path,
                    summary_path=summary_path,
                    diagnostics_dir=diagnostics_dir,
                    extractor=lambda _context, url: _success_payload(url),
                    sleep_fn=lambda _seconds: None,
                )

            records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]

            self.assertTrue(records[0]["diagnostics_path"].endswith("0001_123_diagnostics.json"))
            self.assertTrue(Path(records[0]["diagnostics_path"]).exists())

    def test_missing_storage_state_fails_clearly(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            input_path = temp / "input.json"
            _write_json(input_path, ["https://www.facebook.com/example/posts/123"])

            with self.assertRaisesRegex(ValueError, "storage state file does not exist"):
                run_facebook_batch(
                    input_path=input_path,
                    storage_state_path=temp / "missing.json",
                    extractor=lambda _context, url: _success_payload(url),
                    sleep_fn=lambda _seconds: None,
                )

    def test_storage_state_contents_are_not_written_to_output(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            input_path = temp / "input.json"
            output_path = temp / "out.jsonl"
            summary_path = temp / "summary.json"
            storage = temp / "state.json"
            secret = "super-secret-cookie"
            storage.write_text(secret, encoding="utf-8")
            _write_json(input_path, ["https://www.facebook.com/example/posts/123"])

            run_facebook_batch(
                input_path=input_path,
                storage_state_path=storage,
                output_path=output_path,
                summary_path=summary_path,
                diagnostics_dir=temp / "debug",
                extractor=lambda _context, url: _success_payload(url),
                sleep_fn=lambda _seconds: None,
            )

            self.assertNotIn(secret, output_path.read_text(encoding="utf-8"))
            self.assertNotIn(secret, summary_path.read_text(encoding="utf-8"))

    def test_real_batch_path_reuses_one_browser_context(self) -> None:
        class FakePage:
            def close(self) -> None:
                pass

        class FakeContext:
            def __init__(self) -> None:
                self.page_count = 0

            def new_page(self) -> FakePage:
                self.page_count += 1
                return FakePage()

            def close(self) -> None:
                pass

        class FakeBrowser:
            def __init__(self) -> None:
                self.context_count = 0
                self.context = FakeContext()

            def new_context(self, **_kwargs: object) -> FakeContext:
                self.context_count += 1
                return self.context

            def close(self) -> None:
                pass

        class FakeChromium:
            def __init__(self, browser: FakeBrowser) -> None:
                self.browser = browser
                self.launch_count = 0

            def launch(self, **_kwargs: object) -> FakeBrowser:
                self.launch_count += 1
                return self.browser

        class FakePlaywright:
            def __init__(self, browser: FakeBrowser) -> None:
                self.chromium = FakeChromium(browser)

            def start(self) -> "FakePlaywright":
                return self

            def stop(self) -> None:
                pass

        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            input_path = temp / "input.json"
            output_path = temp / "out.jsonl"
            summary_path = temp / "summary.json"
            storage = temp / "state.json"
            _touch_storage_state(storage)
            _write_json(
                input_path,
                [
                    "https://www.facebook.com/example/posts/1",
                    "https://www.facebook.com/example/posts/2",
                ],
            )
            fake_browser = FakeBrowser()

            with patch(
                "src.batch.facebook_batch.extract_facebook_post_from_page",
                side_effect=lambda _page, url, **_kwargs: _success_payload(url, post_id=url.rsplit("/", 1)[-1]),
            ):
                run_facebook_batch(
                    input_path=input_path,
                    storage_state_path=storage,
                    output_path=output_path,
                    summary_path=summary_path,
                    diagnostics_dir=temp / "debug",
                    playwright_factory=lambda: FakePlaywright(fake_browser),
                    sleep_fn=lambda _seconds: None,
                )

        self.assertEqual(fake_browser.context_count, 1)
        self.assertEqual(fake_browser.context.page_count, 2)


class FacebookBatchCliTests(unittest.TestCase):
    def test_facebook_batch_cli_argument_is_recognized(self) -> None:
        with patch("sys.argv", ["main.py", "--facebook-batch-file", "input.json"]):
            args = main.parse_args()

        self.assertEqual(args.facebook_batch_file, Path("input.json"))

    def test_facebook_debug_bundle_cli_argument_is_recognized(self) -> None:
        with patch(
            "sys.argv",
            [
                "main.py",
                "--facebook-url",
                "https://www.facebook.com/example/posts/123",
                "--facebook-save-debug-bundle",
            ],
        ):
            args = main.parse_args()

        self.assertTrue(args.facebook_save_debug_bundle)

    def test_facebook_batch_passes_debug_bundle_flag(self) -> None:
        summary = {
            "current_run": {"success_count": 0, "failed_count": 0, "skipped_count": 0},
        }
        with patch("src.batch.facebook_batch.run_facebook_batch", return_value=summary) as run_batch:
            main.run_facebook_batch_extraction(
                input_path=Path("input.json"),
                output_path=Path("out.jsonl"),
                limit=None,
                delay_seconds=0,
                resume=False,
                headless=True,
                storage_state_path=Path("state.json"),
                save_debug_bundle=True,
            )

        self.assertTrue(run_batch.call_args.kwargs["save_debug_bundle"])

    def test_keyword_and_x_modes_remain_unchanged(self) -> None:
        with patch("sys.argv", ["main.py", "--keyword", "OpenAI"]):
            keyword_args = main.parse_args()
        with patch("sys.argv", ["main.py", "--x-url", "https://x.com/OpenAI/status/123"]):
            x_args = main.parse_args()

        self.assertEqual(keyword_args.keyword, "OpenAI")
        self.assertEqual(x_args.x_url, "https://x.com/OpenAI/status/123")

    def test_batch_mode_is_mutually_exclusive_with_single_facebook_url(self) -> None:
        with patch(
            "sys.argv",
            [
                "main.py",
                "--facebook-url",
                "https://www.facebook.com/example/posts/123",
                "--facebook-batch-file",
                "input.json",
            ],
        ):
            with self.assertRaises(SystemExit):
                main.parse_args()


if __name__ == "__main__":
    unittest.main()
