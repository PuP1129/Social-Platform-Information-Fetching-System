from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.clean_generated import clean_generated, iter_generated_paths


class CleanGeneratedTests(unittest.TestCase):
    def test_dry_run_finds_only_generated_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pycache = root / "src" / "__pycache__"
            pycache.mkdir(parents=True)
            pyc = pycache / "module.cpython-312.pyc"
            pyc.write_bytes(b"cache")

            temp_file = root / "scratch.tmp"
            temp_file.write_text("temporary", encoding="utf-8")

            output_file = root / "output" / "search_results.json"
            output_file.parent.mkdir()
            output_file.write_text("{}", encoding="utf-8")

            example_file = root / "examples" / "sample.tmp"
            example_file.parent.mkdir()
            example_file.write_text("keep", encoding="utf-8")

            state_file = root / ".playwright" / "facebook_storage_state.json"
            state_file.parent.mkdir()
            state_file.write_text("{}", encoding="utf-8")

            found = {path.relative_to(root).as_posix() for path in iter_generated_paths(root)}

        self.assertEqual(found, {"scratch.tmp", "src/__pycache__"})

    def test_clean_generated_does_not_remove_protected_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pycache = root / "tests" / "__pycache__"
            pycache.mkdir(parents=True)
            (pycache / "test.cpython-312.pyc").write_bytes(b"cache")

            output_file = root / "output" / "facebook_post.json"
            output_file.parent.mkdir()
            output_file.write_text("{}", encoding="utf-8")

            removed = clean_generated(root, dry_run=False)

            self.assertEqual([path.relative_to(root).as_posix() for path in removed], ["tests/__pycache__"])
            self.assertFalse(pycache.exists())
            self.assertTrue(output_file.exists())


if __name__ == "__main__":
    unittest.main()
