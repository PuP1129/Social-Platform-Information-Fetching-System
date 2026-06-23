from __future__ import annotations

import json
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main
from src.config.social_collect import (
    build_social_collect_settings,
    load_collect_keywords_file,
    load_social_collect_config,
    merge_collect_keywords,
    order_social_platforms,
    parse_social_platforms,
)


def _search_response(urls: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        results=[
            {"title": f"Result {index}", "url": url, "snippet": None}
            for index, url in enumerate(urls, start=1)
        ]
    )


def _x_batch_summary(success: int = 0, failed: int = 0, skipped: int = 0) -> dict[str, int | str]:
    return {
        "input_count": success + failed + skipped,
        "attempted_count": success + failed,
        "success_count": success,
        "failed_count": failed,
        "skipped_count": skipped,
        "output_file": "out.jsonl",
    }


def _facebook_batch_summary(success: int = 0, failed: int = 0, skipped: int = 0) -> dict[str, object]:
    return {
        "current_run": {
            "record_count": success + failed + skipped,
            "success_count": success,
            "failed_count": failed,
            "skipped_count": skipped,
        },
        "total": {
            "record_count": success + failed + skipped,
            "success_count": success,
            "failed_count": failed,
            "skipped_count": skipped,
        },
    }


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
                        "search": {"max_results": 10, "timeout_ms": 45_000},
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
                search_timeout_ms_override=22_000,
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
        self.assertEqual(settings.search_timeout_ms, 22_000)
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

    def test_default_platform_order_prioritizes_x(self) -> None:
        self.assertEqual(order_social_platforms(["facebook", "x"]), ["x", "facebook"])

    def test_config_platform_order_is_respected(self) -> None:
        self.assertEqual(
            order_social_platforms(["facebook", "x"], ["facebook", "x"]),
            ["facebook", "x"],
        )

    def test_social_platforms_cli_order_is_respected_without_config_order(self) -> None:
        self.assertEqual(
            order_social_platforms(["facebook", "x"], preserve_enabled_order=True),
            ["facebook", "x"],
        )

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
                        "search": {"max_results": 33, "timeout_ms": 12_345},
                        "collection": {"limit": 4, "delay": 0.75, "headless": True, "normalize_output": True},
                        "output": {"run_dir": str(base), "filename": "ignored.jsonl", "timestamped": False},
                    }
                ),
                encoding="utf-8",
            )
            search = Mock(return_value=_search_response(["https://x.com/OpenAI/status/123"]))
            runner = Mock(return_value=_x_batch_summary(success=1))

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
                patch("src.search.google_pse.search_google_pse_with_metadata", search),
                patch("src.batch.x_batch.run_x_batch", runner),
            ):
                main.main()

        search.assert_called_once_with(keyword="Flock camera", headless=True, max_results=33, timeout_ms=12_345)
        kwargs = runner.call_args.kwargs
        self.assertEqual(kwargs["limit"], 2)
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
            search = Mock(return_value=_search_response(["https://x.com/OpenAI/status/123"]))
            runner = Mock(return_value=_x_batch_summary(success=1))
            with (
                patch.object(
                    sys,
                    "argv",
                    ["main.py", "--collect", "flock camera", "--collect", "license plate reader", "--config", str(config)],
                ),
                patch("src.search.google_pse.search_google_pse_with_metadata", search),
                patch("src.batch.x_batch.run_x_batch", runner),
            ):
                main.main()

        self.assertEqual(
            [call.kwargs["keyword"] for call in search.call_args_list],
            ["flock camera", "license plate reader"],
        )

    def test_social_search_timeout_cli_override_wins_over_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            state = base / "x_state.json"
            state.write_text("{}", encoding="utf-8")
            config = base / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "platforms": ["x"],
                        "storage_state": {"x": str(state)},
                        "search": {"max_results": 10, "timeout_ms": 11_111},
                    }
                ),
                encoding="utf-8",
            )
            search = Mock(return_value=_search_response([]))
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "main.py",
                        "--collect",
                        "flock camera",
                        "--config",
                        str(config),
                        "--social-search-timeout-ms",
                        "22222",
                    ],
                ),
                patch("src.search.google_pse.search_google_pse_with_metadata", search),
            ):
                main.main()

        self.assertEqual(search.call_args.kwargs["timeout_ms"], 22_222)

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
            search = Mock(
                return_value=_search_response(
                    [
                        "https://x.com/OpenAI/status/123",
                        "https://www.facebook.com/example/posts/456",
                    ]
                )
            )
            facebook_runner = Mock(return_value=_facebook_batch_summary(success=1))
            x_runner = Mock(return_value=_x_batch_summary(success=1))
            with (
                patch.object(
                    sys,
                    "argv",
                    ["main.py", "--collect", "flock camera", "--config", str(config), "--social-output", str(output)],
                ),
                patch("src.search.google_pse.search_google_pse_with_metadata", search),
                patch("src.batch.facebook_batch.run_facebook_batch", facebook_runner),
                patch("src.batch.x_batch.run_x_batch", x_runner),
            ):
                main.main()

        search.assert_called_once()
        self.assertEqual(x_runner.call_args.kwargs["output_path"], output)
        self.assertEqual(facebook_runner.call_args.kwargs["output_path"], output)
        self.assertFalse(x_runner.call_args.kwargs["resume"])
        self.assertTrue(facebook_runner.call_args.kwargs["resume"])

    def test_limit_thirty_runs_x_first_and_facebook_gets_remaining(self) -> None:
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
            call_order: list[str] = []

            def x_runner(**_kwargs: object) -> dict[str, object]:
                call_order.append("x")
                return _x_batch_summary(success=5)

            def facebook_runner(**_kwargs: object) -> dict[str, object]:
                call_order.append("facebook")
                return _facebook_batch_summary(success=25)

            x_mock = Mock(side_effect=x_runner)
            facebook_mock = Mock(side_effect=facebook_runner)
            search = Mock(
                return_value=_search_response(
                    [f"https://x.com/OpenAI/status/{100 + index}" for index in range(5)]
                    + [f"https://www.facebook.com/example/posts/{200 + index}" for index in range(25)]
                )
            )
            output = io.StringIO()
            with (
                patch.object(sys, "argv", ["main.py", "--collect", "topic", "--config", str(config), "--limit", "30"]),
                patch("src.search.google_pse.search_google_pse_with_metadata", search),
                patch("src.batch.x_batch.run_x_batch", x_mock),
                patch("src.batch.facebook_batch.run_facebook_batch", facebook_mock),
                patch("sys.stdout", output),
            ):
                main.main()

        self.assertEqual(call_order, ["x", "facebook"])
        self.assertEqual(x_mock.call_args.kwargs["limit"], 30)
        self.assertEqual(facebook_mock.call_args.kwargs["limit"], 25)
        text = output.getvalue()
        self.assertIn("Platform order: x, facebook", text)
        self.assertIn("Global limit: 30", text)
        self.assertIn("- x: limit_in=30", text)
        self.assertIn("remaining=25", text)
        self.assertIn("- facebook: limit_in=25", text)
        self.assertIn("remaining=0", text)

    def test_x_fewer_candidates_lets_facebook_fill_unused_capacity(self) -> None:
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
                        "platforms": ["x", "facebook"],
                        "storage_state": {"facebook": str(fb_state), "x": str(x_state)},
                    }
                ),
                encoding="utf-8",
            )
            search = Mock(
                return_value=_search_response(
                    [f"https://x.com/OpenAI/status/{100 + index}" for index in range(3)]
                    + [f"https://www.facebook.com/example/posts/{200 + index}" for index in range(27)]
                )
            )
            x_runner = Mock(return_value=_x_batch_summary(success=3))
            facebook_runner = Mock(return_value=_facebook_batch_summary(success=27))
            with (
                patch.object(sys, "argv", ["main.py", "--collect", "topic", "--config", str(config), "--limit", "30"]),
                patch("src.search.google_pse.search_google_pse_with_metadata", search),
                patch("src.batch.x_batch.run_x_batch", x_runner),
                patch("src.batch.facebook_batch.run_facebook_batch", facebook_runner),
            ):
                main.main()

        self.assertEqual(x_runner.call_args.kwargs["limit"], 30)
        self.assertEqual(facebook_runner.call_args.kwargs["limit"], 27)

    def test_x_zero_candidates_still_runs_facebook_with_full_remaining_limit(self) -> None:
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
                        "platforms": ["x", "facebook"],
                        "storage_state": {"facebook": str(fb_state), "x": str(x_state)},
                    }
                ),
                encoding="utf-8",
            )
            search = Mock(
                return_value=_search_response(
                    [f"https://www.facebook.com/example/posts/{200 + index}" for index in range(30)]
                )
            )
            x_runner = Mock(return_value=_x_batch_summary(success=0))
            facebook_runner = Mock(return_value=_facebook_batch_summary(success=30))
            with (
                patch.object(sys, "argv", ["main.py", "--collect", "topic", "--config", str(config), "--limit", "30"]),
                patch("src.search.google_pse.search_google_pse_with_metadata", search),
                patch("src.batch.x_batch.run_x_batch", x_runner),
                patch("src.batch.facebook_batch.run_facebook_batch", facebook_runner),
            ):
                main.main()

        x_runner.assert_not_called()
        self.assertEqual(facebook_runner.call_args.kwargs["limit"], 30)

    def test_single_platform_collection_uses_full_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fb_state = base / "fb_state.json"
            fb_state.write_text("{}", encoding="utf-8")
            config = base / "config.json"
            config.write_text(
                json.dumps({"platforms": ["facebook"], "storage_state": {"facebook": str(fb_state)}}),
                encoding="utf-8",
            )
            search = Mock(
                return_value=_search_response(
                    [f"https://www.facebook.com/example/posts/{200 + index}" for index in range(30)]
                )
            )
            facebook_runner = Mock(return_value=_facebook_batch_summary(success=30))
            with (
                patch.object(sys, "argv", ["main.py", "--collect", "topic", "--config", str(config), "--limit", "30"]),
                patch("src.search.google_pse.search_google_pse_with_metadata", search),
                patch("src.batch.facebook_batch.run_facebook_batch", facebook_runner),
            ):
                main.main()

        self.assertEqual(facebook_runner.call_args.kwargs["limit"], 30)

    def test_config_platform_order_controls_collection_order(self) -> None:
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
                        "platforms": ["x", "facebook"],
                        "storage_state": {"facebook": str(fb_state), "x": str(x_state)},
                        "collection": {"platform_order": ["facebook", "x"]},
                    }
                ),
                encoding="utf-8",
            )
            call_order: list[str] = []
            search = Mock(
                return_value=_search_response(
                    [
                        "https://x.com/OpenAI/status/123",
                        "https://www.facebook.com/example/posts/456",
                        "https://www.facebook.com/example/posts/457",
                    ]
                )
            )
            facebook_runner = Mock(side_effect=lambda **_kwargs: call_order.append("facebook") or _facebook_batch_summary(success=2))
            x_runner = Mock(side_effect=lambda **_kwargs: call_order.append("x") or _x_batch_summary(success=1))
            with (
                patch.object(sys, "argv", ["main.py", "--collect", "topic", "--config", str(config), "--limit", "5"]),
                patch("src.search.google_pse.search_google_pse_with_metadata", search),
                patch("src.batch.facebook_batch.run_facebook_batch", facebook_runner),
                patch("src.batch.x_batch.run_x_batch", x_runner),
            ):
                main.main()

        self.assertEqual(call_order, ["facebook", "x"])
        self.assertEqual(facebook_runner.call_args.kwargs["limit"], 5)
        self.assertEqual(x_runner.call_args.kwargs["limit"], 3)

    def test_x_no_candidate_is_warning_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            state = base / "x_state.json"
            state.write_text("{}", encoding="utf-8")
            config = base / "config.json"
            config.write_text(json.dumps({"platforms": ["x"], "storage_state": {"x": str(state)}}), encoding="utf-8")
            search = Mock(return_value=_search_response(["https://example.com/not-a-post"]))
            output = io.StringIO()
            with (
                patch.object(sys, "argv", ["main.py", "--collect", "flock camera", "--config", str(config)]),
                patch("src.search.google_pse.search_google_pse_with_metadata", search),
                patch("sys.stdout", output),
            ):
                main.main()

        text = output.getvalue()
        self.assertIn("status=no_candidates", text)
        self.assertIn("warning=x_search_results_without_supported_post_urls", text)

    def test_shared_google_search_failure_is_reported_without_platform_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            state = base / "x_state.json"
            state.write_text("{}", encoding="utf-8")
            config = base / "config.json"
            config.write_text(json.dumps({"platforms": ["x"], "storage_state": {"x": str(state)}}), encoding="utf-8")
            search = Mock(side_effect=RuntimeError("pse timeout"))
            output = io.StringIO()
            with (
                patch.object(sys, "argv", ["main.py", "--collect", "flock camera", "--config", str(config)]),
                patch("src.search.google_pse.search_google_pse_with_metadata", search),
                patch("sys.stdout", output),
            ):
                main.main()

        text = output.getvalue()
        self.assertIn("status=no_candidates", text)
        self.assertIn("warning=google_pse_request_failed", text)
        self.assertNotIn("platform_failed", text)

    def test_facebook_success_and_x_no_candidates_exits_safely(self) -> None:
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
            search = Mock(return_value=_search_response(["https://www.facebook.com/example/posts/456"]))
            facebook_runner = Mock(return_value=_facebook_batch_summary(success=1))
            secret_state_contents = "secret-cookie-value"
            output = io.StringIO()
            with (
                patch.object(sys, "argv", ["main.py", "--collect", "flock camera", "--config", str(config)]),
                patch("src.search.google_pse.search_google_pse_with_metadata", search),
                patch("src.batch.facebook_batch.run_facebook_batch", facebook_runner),
                patch("sys.stdout", output),
            ):
                main.main()

        text = output.getvalue()
        self.assertIn("x:", text)
        self.assertIn("status=no_candidates", text)
        self.assertIn("facebook:", text)
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
