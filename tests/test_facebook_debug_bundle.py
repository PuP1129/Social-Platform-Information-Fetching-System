from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.debug.facebook_debug_bundle import (
    facebook_debug_bundle_dir,
    save_facebook_debug_bundle,
    should_save_facebook_debug_bundle,
)


class FakeLocator:
    def aria_snapshot(self) -> str:
        return "- document:\n  - text: Example post"


class FakePage:
    def __init__(self, fail_content: bool = False) -> None:
        self.fail_content = fail_content

    def content(self) -> str:
        if self.fail_content:
            raise RuntimeError("content unavailable")
        return '<html><body><input type="password" value="secret-password"><article>Post</article></body></html>'

    def screenshot(self, path: str, full_page: bool) -> None:
        self.last_full_page = full_page
        Path(path).write_bytes(b"fake image")

    def locator(self, selector: str) -> FakeLocator:
        self.last_locator = selector
        return FakeLocator()

    def evaluate(self, _script: str, _args: dict[str, object]) -> dict[str, object]:
        return {
            "message_candidates": [
                {
                    "index": 0,
                    "text_preview": "Example post",
                    "visible": True,
                    "outer_html": '<div><input autocomplete="current-password" value="secret-password"></div>',
                    "bounding_box": {"x": 0, "y": 0, "width": 10, "height": 10},
                    "attributes": {"role": None},
                    "ancestor_chain": [],
                }
            ],
            "article_candidates": [],
            "author_candidates": [],
            "time_candidates": [],
            "action_bar_candidates": [],
            "target_post_links": [],
        }


class FacebookDebugBundleTests(unittest.TestCase):
    def test_failed_and_partial_should_save_automatically(self) -> None:
        self.assertTrue(should_save_facebook_debug_bundle("failed"))
        self.assertTrue(should_save_facebook_debug_bundle("partial"))

    def test_success_saves_only_when_forced(self) -> None:
        self.assertFalse(should_save_facebook_debug_bundle("success"))
        self.assertTrue(should_save_facebook_debug_bundle("success", force=True))

    def test_save_bundle_writes_expected_files_and_redacts_password_values(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bundle_dir = Path(temp_dir) / "0001_123"
            paths = save_facebook_debug_bundle(FakePage(), bundle_dir, diagnostics={"collection_status": "partial"})

            self.assertTrue((bundle_dir / "page.html").exists())
            self.assertTrue((bundle_dir / "page.png").exists())
            self.assertTrue((bundle_dir / "aria_snapshot.yaml").exists())
            self.assertTrue((bundle_dir / "diagnostics.json").exists())
            self.assertTrue((bundle_dir / "candidate_nodes.json").exists())
            self.assertEqual(paths["debug_bundle_path"], str(bundle_dir))

            combined_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (bundle_dir / "page.html", bundle_dir / "candidate_nodes.json")
            )
            self.assertNotIn("secret-password", combined_text)
            self.assertIn("[REDACTED]", combined_text)

    def test_multiple_batch_directories_do_not_conflict(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            first = facebook_debug_bundle_dir(root, index=1, post_id="123")
            second = facebook_debug_bundle_dir(root, index=2, post_id="123")

        self.assertNotEqual(first, second)
        self.assertEqual(first.name, "0001_123")
        self.assertEqual(second.name, "0002_123")

    def test_debug_save_failure_is_recorded_without_raising(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bundle_dir = Path(temp_dir) / "0001_123"

            paths = save_facebook_debug_bundle(FakePage(fail_content=True), bundle_dir)

            self.assertEqual(paths["debug_bundle_path"], str(bundle_dir))
            diagnostics = json.loads((bundle_dir / "diagnostics.json").read_text(encoding="utf-8"))
            self.assertIn("page.html", diagnostics["debug_bundle_errors"])
            self.assertTrue((bundle_dir / "page.png").exists())

    def test_storage_state_contents_are_not_written_to_bundle(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            secret = "storage-state-secret"
            (temp / "facebook_storage_state.json").write_text(secret, encoding="utf-8")
            bundle_dir = temp / "0001_123"

            save_facebook_debug_bundle(FakePage(), bundle_dir)
            bundle_text = "\n".join(
                path.read_text(encoding="utf-8", errors="ignore")
                for path in bundle_dir.iterdir()
                if path.is_file() and path.suffix != ".png"
            )

            self.assertNotIn(secret, bundle_text)


if __name__ == "__main__":
    unittest.main()
