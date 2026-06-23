from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import main
from src.search.google_pse import (
    GOOGLE_PSE_MAX_RESULTS,
    GooglePSEPageFetch,
    GooglePSEPageOpenError,
    GooglePSESearchError,
    GooglePSESearchResponse,
    _collect_paginated_results,
    _planned_page_requests,
    _validate_max_results,
)


def _result(url: str) -> dict[str, str | None]:
    return {"title": f"Title {url}", "url": url, "snippet": f"Snippet {url}"}


class GooglePSEPaginationPlanTests(unittest.TestCase):
    def test_max_results_5_requests_one_page(self) -> None:
        self.assertEqual(_planned_page_requests(5), [(1, 1, 5)])

    def test_max_results_10_requests_one_full_page(self) -> None:
        self.assertEqual(_planned_page_requests(10), [(1, 1, 10)])

    def test_max_results_11_requests_two_pages(self) -> None:
        self.assertEqual(_planned_page_requests(11), [(1, 1, 10), (2, 11, 1)])

    def test_max_results_25_requests_three_pages_with_final_5(self) -> None:
        self.assertEqual(_planned_page_requests(25), [(1, 1, 10), (2, 11, 10), (3, 21, 5)])

    def test_max_results_30_uses_one_based_starts(self) -> None:
        self.assertEqual(_planned_page_requests(30), [(1, 1, 10), (2, 11, 10), (3, 21, 10)])

    def test_max_results_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "greater than 0"):
            _validate_max_results(0)
        with self.assertRaisesRegex(ValueError, "greater than 0"):
            _validate_max_results(-1)

    def test_max_results_over_100_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, str(GOOGLE_PSE_MAX_RESULTS)):
            _validate_max_results(101)


class GooglePSEPaginationCollectionTests(unittest.TestCase):
    def test_second_page_empty_stops_and_returns_first_page(self) -> None:
        calls: list[tuple[int, int]] = []

        def fetch(_page: object, _keyword: str, start: int, num: int, _page_number: int, _timeout_ms: int) -> GooglePSEPageFetch:
            calls.append((start, num))
            if start == 1:
                return GooglePSEPageFetch([_result(f"https://example.com/{index}") for index in range(10)], True)
            return GooglePSEPageFetch([], False)

        response = _collect_paginated_results(object(), "book", 30, 30, fetch)

        self.assertEqual(len(response.results), 10)
        self.assertEqual(calls, [(1, 10), (11, 10)])

    def test_short_second_page_stops_before_third_page(self) -> None:
        calls: list[tuple[int, int]] = []

        def fetch(_page: object, _keyword: str, start: int, num: int, _page_number: int, _timeout_ms: int) -> GooglePSEPageFetch:
            calls.append((start, num))
            if start == 1:
                return GooglePSEPageFetch([_result(f"https://example.com/a{index}") for index in range(10)], True)
            return GooglePSEPageFetch([_result("https://example.com/b1"), _result("https://example.com/b2")], True)

        response = _collect_paginated_results(object(), "book", 30, 30, fetch)

        self.assertEqual(len(response.results), 12)
        self.assertEqual(calls, [(1, 10), (11, 10)])

    def test_duplicate_urls_are_removed_preserving_first_order(self) -> None:
        def fetch(_page: object, _keyword: str, start: int, _num: int, _page_number: int, _timeout_ms: int) -> GooglePSEPageFetch:
            if start == 1:
                return GooglePSEPageFetch(
                    [_result("A"), _result("B"), _result("C")]
                    + [_result(f"X{index}") for index in range(7)],
                    True,
                )
            return GooglePSEPageFetch([_result("C"), _result("D"), _result("E")], False)

        response = _collect_paginated_results(object(), "book", 13, 13, fetch)

        self.assertEqual(
            [item["url"] for item in response.results],
            ["A", "B", "C"] + [f"X{index}" for index in range(7)] + ["D", "E"],
        )

    def test_missing_items_is_treated_as_empty_results(self) -> None:
        response = _collect_paginated_results(
            object(),
            "book",
            10,
            10,
            lambda *_args: GooglePSEPageFetch([], False),
        )

        self.assertEqual(response.results, [])
        self.assertEqual(response.page_logs[0].received, 0)

    def test_first_page_failure_raises_search_error(self) -> None:
        def fetch(*_args: object) -> GooglePSEPageFetch:
            raise GooglePSEPageOpenError("open failed")

        with self.assertRaisesRegex(GooglePSEPageOpenError, "open failed"):
            _collect_paginated_results(object(), "book", 10, 10, fetch)

    def test_later_page_failure_returns_partial_results(self) -> None:
        def fetch(_page: object, _keyword: str, start: int, _num: int, _page_number: int, _timeout_ms: int) -> GooglePSEPageFetch:
            if start == 1:
                return GooglePSEPageFetch([_result(f"https://example.com/{index}") for index in range(10)], True)
            raise GooglePSESearchError("page failed")

        response = _collect_paginated_results(object(), "book", 30, 30, fetch)

        self.assertEqual(len(response.results), 10)
        self.assertIn("page 2", response.partial_error or "")

    def test_timeout_is_passed_to_page_fetcher(self) -> None:
        seen_timeouts: list[int] = []

        def fetch(
            _page: object,
            _keyword: str,
            _start: int,
            _num: int,
            _page_number: int,
            timeout_ms: int,
        ) -> GooglePSEPageFetch:
            seen_timeouts.append(timeout_ms)
            return GooglePSEPageFetch([], False)

        _collect_paginated_results(object(), "book", 10, 10, timeout_ms=12_345, fetch_page=fetch)

        self.assertEqual(seen_timeouts, [12_345])


class GooglePSEMainFlowTests(unittest.TestCase):
    def test_run_search_saves_merged_results_and_filters_once(self) -> None:
        response = GooglePSESearchResponse(
            results=[_result("https://example.com/a"), _result("https://example.com/b")],
            requested_count=30,
            target_count=30,
            page_logs=[],
        )

        with TemporaryDirectory() as temp_dir:
            raw_path = Path(temp_dir) / "search_results.json"
            post_path = Path(temp_dir) / "post_urls.json"
            with patch.object(main, "RAW_OUTPUT_PATH", raw_path):
                with patch.object(main, "POST_OUTPUT_PATH", post_path):
                    with patch("src.search.google_pse.search_google_pse_with_metadata", return_value=response):
                        with patch.object(main, "filter_post_results", return_value=[]) as filter_mock:
                            main.run_search("book", headless=True, max_results=30)

            raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
            post_payload = json.loads(post_path.read_text(encoding="utf-8"))

        self.assertEqual(raw_payload["result_count"], 2)
        self.assertEqual(len(raw_payload["results"]), 2)
        self.assertEqual(post_payload["raw_result_count"], 2)
        filter_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
