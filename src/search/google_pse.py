from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

GOOGLE_PSE_URL = "https://cse.google.com/cse?cx=e26d7e4e0bc774ea6"
DEFAULT_TIMEOUT_MS = 15_000
GOOGLE_PSE_PAGE_SIZE = 10
GOOGLE_PSE_MAX_RESULTS = 100


class GooglePSESearchError(RuntimeError):
    """Base exception for Google PSE search failures."""


class GooglePSEPageOpenError(GooglePSESearchError):
    """Raised when the Google PSE page cannot be opened."""


class GooglePSEInputNotFoundError(GooglePSESearchError):
    """Raised when the Google PSE search input cannot be found."""


class GooglePSEResultsNotLoadedError(GooglePSESearchError):
    """Raised when a Google PSE results page does not load."""


@dataclass(frozen=True)
class GooglePSEPageFetch:
    results: list[dict[str, str | None]]
    has_next_page: bool | None = None


@dataclass(frozen=True)
class GooglePSEPageLog:
    page_number: int
    start: int
    requested: int
    received: int


@dataclass(frozen=True)
class GooglePSESearchResponse:
    results: list[dict[str, str | None]]
    requested_count: int
    target_count: int
    page_logs: list[GooglePSEPageLog]
    partial_error: str | None = None


PageFetcher = Callable[[Page, str, int, int, int], GooglePSEPageFetch]


def search_google_pse(
    keyword: str,
    headless: bool = False,
    max_results: int = 10,
) -> list[dict[str, str | None]]:
    """Search Google Programmable Search Engine and return merged results."""
    return search_google_pse_with_metadata(
        keyword=keyword,
        headless=headless,
        max_results=max_results,
    ).results


def search_google_pse_with_metadata(
    keyword: str,
    headless: bool = False,
    max_results: int = 10,
) -> GooglePSESearchResponse:
    """Search Google PSE across pages and include pagination metadata."""
    normalized_keyword = _normalize_keyword(keyword)
    requested_count, target_count = _validate_max_results(max_results)

    playwright = None
    browser = None
    context = None
    page = None

    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        return _collect_paginated_results(
            page=page,
            keyword=normalized_keyword,
            requested_count=requested_count,
            target_count=target_count,
        )
    finally:
        _close_quietly(page)
        _close_quietly(context)
        _close_quietly(browser)
        if playwright is not None:
            playwright.stop()


def _normalize_keyword(keyword: str) -> str:
    if not isinstance(keyword, str):
        raise TypeError("keyword must be a string.")

    normalized_keyword = keyword.strip()
    if not normalized_keyword:
        raise ValueError("keyword cannot be empty.")
    return normalized_keyword


def _validate_max_results(max_results: int) -> tuple[int, int]:
    if not isinstance(max_results, int):
        raise TypeError("max_results must be an integer.")
    if max_results < 1:
        raise ValueError("max_results must be greater than 0.")
    if max_results > GOOGLE_PSE_MAX_RESULTS:
        raise ValueError(f"max_results cannot exceed {GOOGLE_PSE_MAX_RESULTS}.")
    return max_results, min(max_results, GOOGLE_PSE_MAX_RESULTS)


def _collect_paginated_results(
    page: Page,
    keyword: str,
    requested_count: int,
    target_count: int,
    fetch_page: PageFetcher | None = None,
) -> GooglePSESearchResponse:
    fetch = fetch_page or _fetch_google_pse_page
    results: list[dict[str, str | None]] = []
    seen_urls: set[str] = set()
    page_logs: list[GooglePSEPageLog] = []
    partial_error = None

    for page_number, start, num in _planned_page_requests(target_count):
        try:
            page_fetch = fetch(page, keyword, start, num, page_number)
        except GooglePSESearchError as exc:
            if page_number == 1:
                raise
            partial_error = f"Google PSE page {page_number} request failed: {type(exc).__name__}: {exc}"
            break
        except Exception as exc:
            if page_number == 1:
                raise GooglePSESearchError(f"Google PSE page 1 request failed: {exc}") from exc
            partial_error = f"Google PSE page {page_number} request failed: {type(exc).__name__}: {exc}"
            break

        page_results = page_fetch.results
        page_logs.append(
            GooglePSEPageLog(
                page_number=page_number,
                start=start,
                requested=num,
                received=len(page_results),
            )
        )

        for result in page_results:
            url = result.get("url")
            if not isinstance(url, str):
                continue
            normalized_url = url.strip()
            if not normalized_url or normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)
            results.append(result)
            if len(results) >= target_count:
                break

        if len(results) >= target_count:
            break
        if not page_results:
            break
        if len(page_results) < num:
            break
        if page_fetch.has_next_page is False:
            break

    return GooglePSESearchResponse(
        results=results,
        requested_count=requested_count,
        target_count=target_count,
        page_logs=page_logs,
        partial_error=partial_error,
    )


def _planned_page_requests(target_count: int) -> list[tuple[int, int, int]]:
    requests: list[tuple[int, int, int]] = []
    remaining = target_count
    page_number = 1
    start = 1
    while remaining > 0 and start <= GOOGLE_PSE_MAX_RESULTS:
        num = min(GOOGLE_PSE_PAGE_SIZE, remaining, GOOGLE_PSE_MAX_RESULTS - start + 1)
        requests.append((page_number, start, num))
        remaining -= num
        page_number += 1
        start += GOOGLE_PSE_PAGE_SIZE
    return requests


def _fetch_google_pse_page(
    page: Page,
    keyword: str,
    start: int,
    num: int,
    page_number: int,
) -> GooglePSEPageFetch:
    # Google PSE exposes pages in the hosted UI. We still log equivalent start/num
    # values because callers reason about the 1-based PSE result window.
    if page_number == 1:
        _open_search_page(page)
        search_input = _find_search_input(page)
        _submit_search(search_input, keyword)
    else:
        if not _go_to_results_page(page, page_number):
            return GooglePSEPageFetch(results=[], has_next_page=False)

    _wait_for_results(page, page_number)
    return GooglePSEPageFetch(
        results=_extract_results(page, num),
        has_next_page=_has_next_page(page, page_number),
    )


def _open_search_page(page: Page) -> None:
    try:
        page.goto(GOOGLE_PSE_URL, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
    except (PlaywrightError, PlaywrightTimeoutError) as exc:
        raise GooglePSEPageOpenError(f"Google PSE page open failed: {exc}") from exc


def _find_search_input(page: Page) -> Locator:
    search_input = page.locator("input.gsc-input, #gsc-i-id1").first
    try:
        search_input.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
    except (PlaywrightError, PlaywrightTimeoutError) as exc:
        raise GooglePSEInputNotFoundError(f"Google PSE search input was not found: {exc}") from exc
    return search_input


def _submit_search(search_input: Locator, keyword: str) -> None:
    try:
        search_input.fill(keyword)
        search_input.press("Enter")
    except PlaywrightError as exc:
        raise GooglePSEInputNotFoundError(f"Google PSE search input could not be used: {exc}") from exc


def _wait_for_results(page: Page, page_number: int) -> None:
    try:
        page.wait_for_function(
            """
            (pageNumber) => {
                const isVisible = (element) => {
                    if (!element) return false;
                    const style = window.getComputedStyle(element);
                    return style && style.visibility !== 'hidden' && style.display !== 'none'
                        && element.getClientRects().length > 0;
                };

                const results = Array.from(document.querySelectorAll('.gsc-webResult.gsc-result'));
                const noResults = Array.from(document.querySelectorAll('.gs-no-results-result, .gsc-results .gs-snippet'))
                    .some((element) => isVisible(element) && /no results|没有找到|未找到/i.test(element.textContent || ''));
                const currentPage = Array.from(document.querySelectorAll('.gsc-cursor-current-page'))
                    .some((element) => isVisible(element) && (element.textContent || '').trim() === String(pageNumber));

                return (results.some(isVisible) && (pageNumber === 1 || currentPage)) || noResults;
            }
            """,
            arg=page_number,
            timeout=DEFAULT_TIMEOUT_MS,
        )
    except (PlaywrightError, PlaywrightTimeoutError) as exc:
        raise GooglePSEResultsNotLoadedError(
            f"Google PSE page {page_number} results did not load: {exc}"
        ) from exc


def _go_to_results_page(page: Page, page_number: int) -> bool:
    cursor_page = page.locator(".gsc-cursor-page", has_text=str(page_number)).first
    try:
        if cursor_page.count() == 0 or not cursor_page.is_visible():
            return False
        cursor_page.click()
    except PlaywrightError as exc:
        raise GooglePSEResultsNotLoadedError(
            f"Google PSE page {page_number} pagination control could not be used: {exc}"
        ) from exc
    return True


def _has_next_page(page: Page, page_number: int) -> bool | None:
    next_page = page.locator(".gsc-cursor-page", has_text=str(page_number + 1)).first
    try:
        if next_page.count() == 0:
            return False
        return next_page.is_visible()
    except PlaywrightError:
        return None


def _extract_results(page: Page, max_results: int) -> list[dict[str, str | None]]:
    result_items = page.locator(".gsc-webResult.gsc-result")
    results: list[dict[str, str | None]] = []

    for index in range(result_items.count()):
        item = result_items.nth(index)
        result = _extract_result(item)
        if result is None:
            continue

        results.append(result)
        if len(results) >= max_results:
            break

    return results


def _extract_result(item: Locator) -> dict[str, str | None] | None:
    title_links = item.locator("a.gs-title")
    for index in range(title_links.count()):
        title_link = title_links.nth(index)
        url = (title_link.get_attribute("href") or "").strip()
        if not url:
            continue

        title = _clean_text(title_link.inner_text())
        snippet = _first_visible_text(item.locator(".gs-snippet"))
        return {
            "title": title,
            "url": url,
            "snippet": snippet,
        }

    return None


def _first_visible_text(locator: Locator) -> str | None:
    for index in range(locator.count()):
        candidate = locator.nth(index)
        try:
            if not candidate.is_visible():
                continue
            text = _clean_text(candidate.inner_text())
        except PlaywrightError:
            continue
        if text:
            return text
    return None


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _close_quietly(resource: Any) -> None:
    if resource is None:
        return
    try:
        resource.close()
    except PlaywrightError:
        pass
    except Exception:
        pass
