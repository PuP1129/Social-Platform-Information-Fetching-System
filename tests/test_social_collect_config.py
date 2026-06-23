from __future__ import annotations

import json
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import main
from src.config.social_collect import (
    build_social_collect_settings,
    load_collect_keywords_file,
    load_social_collect_config,
    merge_collect_keywords,
    parse_social_platforms,
)


class SocialCollectConfigTests(unittest.TestCase):
    def test_falls_back_when_local_config_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing_local = Path(directory) / "missing.local.json"
            config = load_social_collect_config(local_config_path=missing_local)
        self.assertEqual(config["platforms"], ["facebook", "x"])
        self.assertEqual(config["collection"]["limit"], 50)

    def test_loads_default_local_config_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            local = base / "social_collect.local.json"
            local.write_text(
                json.dumps({"platforms": ["x"], "collection": {"limit": 3}}),
                encoding="utf-8",
            )
            config = load_social_collect_config(local_config_path=local)
        self.assertEqual(config["platforms"], ["x"])
        self.assertEqual(config["collection"]["limit"], 3)

    def test_config_override_wins_over_local_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            local = base / "social_collect.local.json"
            explicit = base / "override.json"
            local.write_text(json.dumps({"platforms": ["facebook"]}), encoding="utf-8")
            explicit.write_text(json.dumps({"platforms": ["x"]}), encoding="utf-8")
            config = load_social_collect_config(explicit, local_config_path=local)
        self.assertEqual(config["platforms"], ["x"])

    def test_cli_arguments_override_config_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            state = base / "x_state.json"
            state.write_text("{}", encoding="utf-8")
            config = base / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "platforms": ["facebook"],
                        "storage_state": {"x": str(state)},
                        "search": {"max_results": 10},
                        "collection": {"limit": 5, "delay": 2, "headless": False, "normalize_output": False},
                        "output": {"run_dir": str(base / "runs"), "filename": "old.jsonl", "timestamped": False},
                    }
                ),
                encoding="utf-8",
            )
            settings = build_social_collect_settings(
                keyword=" Flock camera ",
                config_path=config,
                local_config_path=base / "missing.local.json",
                platforms_override="x",
                limit_override=7,
                search_max_results_override=12,
                delay_override=0.25,
                output_override=base / "out.jsonl",
                x_storage_state_override=state,
                headless_override=True,
                normalize_output_override=True,
            )
        self.assertEqual(settings.keywords, ["Flock camera"])
        self.assertEqual(settings.platforms, ["x"])
        self.assertEqual(settings.limit, 7)
        self.assertEqual(settings.search_max_results, 12)
        self.assertEqual(settings.delay, 0.25)
        self.assertTrue(settings.headless)
        self.assertTrue(settings.normalize_output)
        self.assertEqual(settings.output_path.name, "out.jsonl")

    def test_missing_storage_state_error_message_names_platform_and_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            missing = base / "missing-facebook.json"
            config = base / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "platforms": ["facebook"],
                        "storage_state": {"facebook": str(missing)},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "facebook.*missing-facebook.json.*--facebook-storage-state"):
                build_social_collect_settings(
                    keyword="test",
                    config_path=config,
                    local_config_path=base / "missing.local.json",
                )

    def test_parse_social_platforms_deduplicates_comma_list(self) -> None:
        self.assertEqual(parse_social_platforms("facebook,x,facebook"), ["facebook", "x"])

    def test_simplified_collect_uses_config_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            state = base / "x_state.json"
            state.write_text("{}", encoding="utf-8")
            output = base / "social.jsonl"
            config = base / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "platforms": ["x"],
                        "storage_state": {"x": str(state)},
                        "search": {"max_results": 33},
                        "collection": {"limit": 4, "delay": 0.75, "headless": True, "normalize_output": True},
                        "output": {"run_dir": str(base), "filename": "ignored.jsonl", "timestamped": False},
                    }
                ),
                encoding="utf-8",
            )
            runner = Mock(
                return_value={
                    "search_result_count": 9,
                    "unique_url_count": 4,
                    "extracted_success_count": 3,
                    "failed_count": 1,
                    "skipped_count": 0,
                    "batch_started": True,
                }
            )

            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "main.py",
                        "--collect",
                        "Flock camera",
                        "--config",
                        str(config),
                        "--social-output",
                        str(output),
                        "--limit",
                        "2",
                    ],
                ),
                patch("src.pipelines.x_search_pipeline.run_x_search_pipeline", runner),
            ):
                main.main()

        kwargs = runner.call_args.kwargs
        self.assertEqual(kwargs["keywords"], ["Flock camera"])
        self.assertEqual(kwargs["max_results"], 33)
        self.assertEqual(kwargs["batch_limit"], 2)
        self.assertEqual(kwargs["delay_seconds"], 0.75)
        self.assertTrue(kwargs["headless"])
        self.assertEqual(kwargs["storage_state_path"], state)
        self.assertEqual(kwargs["output_path"], output)
        self.assertIn("record_transform", kwargs)

    def test_unified_collect_passes_repeated_keywords_to_x_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            state = base / "x_state.json"
            state.write_text("{}", encoding="utf-8")
            config = base / "config.json"
            config.write_text(
                json.dumps({"platforms": ["x"], "storage_state": {"x": str(state)}}),
                encoding="utf-8",
            )
            runner = Mock(
                return_value={
                    "search_result_count": 2,
                    "unique_url_count": 1,
                    "extracted_success_count": 1,
                    "failed_count": 0,
                    "skipped_count": 0,
                    "batch_started": True,
                }
            )
            with (
                patch.object(
                    sys,
                    "argv",
                    ["main.py", "--collect", "flock camera", "--collect", "license plate reader", "--config", str(config)],
                ),
                patch("src.pipelines.x_search_pipeline.run_x_search_pipeline", runner),
            ):
                main.main()

        self.assertEqual(runner.call_args.kwargs["keywords"], ["flock camera", "license plate reader"])

    def test_unified_collect_appends_facebook_and_x_to_same_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fb_state = base / "fb_state.json"
            x_state = base / "x_state.json"
            fb_state.write_text("{}", encoding="utf-8")
            x_state.write_text("{}", encoding="utf-8")
            output = base / "social.jsonl"
            config = base / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "platforms": ["facebook", "x"],
                        "storage_state": {"facebook": str(fb_state), "x": str(x_state)},
                    }
                ),
                encoding="utf-8",
            )
            facebook_runner = Mock(
                return_value={
                    "search_result_count": 5,
                    "batch_input_count": 1,
                    "batch_started": True,
                    "batch_current_run": {
                        "record_count": 1,
                        "success_count": 1,
                        "failed_count": 0,
                        "skipped_count": 0,
                    },
                }
            )
            x_runner = Mock(
                return_value={
                    "search_result_count": 4,
                    "unique_url_count": 1,
                    "extracted_success_count": 1,
                    "failed_count": 0,
                    "skipped_count": 0,
                    "batch_started": True,
                }
            )
            with (
                patch.object(
                    sys,
                    "argv",
                    ["main.py", "--collect", "flock camera", "--config", str(config), "--social-output", str(output)],
                ),
                patch("src.pipelines.facebook_search_pipeline.run_facebook_multi_search_pipeline", facebook_runner),
                patch("src.pipelines.x_search_pipeline.run_x_search_pipeline", x_runner),
            ):
                main.main()

        self.assertEqual(facebook_runner.call_args.kwargs["output_path"], output)
        self.assertEqual(x_runner.call_args.kwargs["output_path"], output)
        self.assertFalse(facebook_runner.call_args.kwargs["resume"])
        self.assertTrue(x_runner.call_args.kwargs["resume"])

    def test_x_no_candidate_is_warning_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            state = base / "x_state.json"
            state.write_text("{}", encoding="utf-8")
            config = base / "config.json"
            config.write_text(json.dumps({"platforms": ["x"], "storage_state": {"x": str(state)}}), encoding="utf-8")
            runner = Mock(
                return_value={
                    "search_result_count": 7,
                    "unique_url_count": 0,
                    "extracted_success_count": 0,
                    "failed_count": 0,
                    "skipped_count": 0,
                    "batch_started": False,
                }
            )
            output = io.StringIO()
            with (
                patch.object(sys, "argv", ["main.py", "--collect", "flock camera", "--config", str(config)]),
                patch("src.pipelines.x_search_pipeline.run_x_search_pipeline", runner),
                patch("sys.stdout", output),
            ):
                main.main()

        self.assertIn("warning=x_search_results_without_supported_post_urls", output.getvalue())

    def test_facebook_success_and_x_search_failure_exits_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fb_state = base / "fb_state.json"
            x_state = base / "x_state.json"
            fb_state.write_text("{}", encoding="utf-8")
            x_state.write_text("{}", encoding="utf-8")
            config = base / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "platforms": ["facebook", "x"],
                        "storage_state": {"facebook": str(fb_state), "x": str(x_state)},
                    }
                ),
                encoding="utf-8",
            )
            facebook_runner = Mock(
                return_value={
                    "search_result_count": 5,
                    "batch_input_count": 1,
                    "batch_started": True,
                    "batch_current_run": {
                        "record_count": 1,
                        "success_count": 1,
                        "failed_count": 0,
                        "skipped_count": 0,
                    },
                }
            )
            secret_state_contents = "secret-cookie-value"
            x_runner = Mock(side_effect=RuntimeError("All X search keywords failed."))
            output = io.StringIO()
            with (
                patch.object(sys, "argv", ["main.py", "--collect", "flock camera", "--config", str(config)]),
                patch("src.pipelines.facebook_search_pipeline.run_facebook_multi_search_pipeline", facebook_runner),
                patch("src.pipelines.x_search_pipeline.run_x_search_pipeline", x_runner),
                patch("sys.stdout", output),
            ):
                main.main()

        text = output.getvalue()
        self.assertIn("x:", text)
        self.assertIn("reason=google_pse_request_failed", text)
        self.assertNotIn(secret_state_contents, text)

    def test_collect_cli_accepts_repeated_keywords_and_file(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "main.py",
                "--collect",
                "flock camera",
                "--collect",
                "privacy",
                "--collect-file",
                "keywords.json",
            ],
        ):
            args = main.parse_args()

        self.assertEqual(args.collect, ["flock camera", "privacy"])
        self.assertEqual(args.collect_file, Path("keywords.json"))

    def test_local_config_is_ignored_by_git_rule(self) -> None:
        ignore_text = Path(".gitignore").read_text(encoding="utf-8")
        self.assertIn("configs/*.local.json", ignore_text)

    def test_repeated_collect_keywords_are_merged(self) -> None:
        self.assertEqual(
            merge_collect_keywords([" flock camera ", "privacy", "flock camera"], None),
            ["flock camera", "privacy"],
        )

    def test_collect_file_loads_keyword_list(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keywords.json"
            path.write_text(json.dumps(["flock camera", "privacy"]), encoding="utf-8")
            self.assertEqual(load_collect_keywords_file(path), ["flock camera", "privacy"])

    def test_collect_file_and_cli_keywords_merge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keywords.json"
            path.write_text(json.dumps(["privacy", "surveillance"]), encoding="utf-8")
            self.assertEqual(
                merge_collect_keywords(["flock camera", "privacy"], path),
                ["flock camera", "privacy", "surveillance"],
            )


if __name__ == "__main__":
    unittest.main()
