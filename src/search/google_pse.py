from __future__ import annotations

from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

GOOGLE_PSE_URL = "https://cse.google.com/cse?cx=e26d7e4e0bc774ea6"
DEFAULT_TIMEOUT_MS = 15_000


class GooglePSESearchError(RuntimeError):
    """Base exception for Google PSE search failures."""


class GooglePSEPageOpenError(GooglePSESearchError):
    """Raised when the Google PSE page cannot be opened."""


class GooglePSEInputNotFoundError(GooglePSESearchError):
    """Raised when the Google PSE search input cannot be found."""


class GooglePSEResultsNotLoadedError(GooglePSESearchError):
    """Raised when the first page of search results does not load."""


def search_google_pse(
    keyword: str,
    headless: bool = False,
    max_results: int = 10,
) -> list[dict[str, str | None]]:
    """Search Google Programmable Search Engine and return first-page results."""
    normalized_keyword = _normalize_keyword(keyword)
    if max_results < 1:
        raise ValueError("max_results must be greater than 0.")

    playwright = None
    browser = None
    context = None
    page = None

    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        _open_search_page(page)
        search_input = _find_search_input(page)
        _submit_search(search_input, normalized_keyword)
        _wait_for_first_page_results(page)

        return _extract_results(page, max_results)
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


def _wait_for_first_page_results(page: Page) -> None:
    try:
        page.wait_for_function(
            """
            () => {
                const isVisible = (element) => {
                    if (!element) return false;
                    const style = window.getComputedStyle(element);
                    return style && style.visibility !== 'hidden' && style.display !== 'none'
                        && element.getClientRects().length > 0;
                };

                const results = Array.from(document.querySelectorAll('.gsc-webResult.gsc-result'));
                const noResults = Array.from(document.querySelectorAll('.gs-no-results-result, .gsc-results .gs-snippet'))
                    .some((element) => isVisible(element) && /no results|没有找到|未找到/i.test(element.textContent || ''));

                return results.some(isVisible) || noResults;
            }
            """,
            timeout=DEFAULT_TIMEOUT_MS,
        )
    except (PlaywrightError, PlaywrightTimeoutError) as exc:
        raise GooglePSEResultsNotLoadedError(
            f"Google PSE first-page results did not load: {exc}"
        ) from exc


def _extract_results(page: Page, max_results: int) -> list[dict[str, str | None]]:
    result_items = page.locator(".gsc-webResult.gsc-result")
    seen_urls: set[str] = set()
    results: list[dict[str, str | None]] = []

    for index in range(result_items.count()):
        item = result_items.nth(index)
        result = _extract_result(item)
        if result is None:
            continue

        url = result["url"]
        if not isinstance(url, str) or url in seen_urls:
            continue

        seen_urls.add(url)
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

