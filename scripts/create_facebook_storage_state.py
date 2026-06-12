from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

DEFAULT_OUTPUT_PATH = Path(".playwright") / "facebook_storage_state.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a local Playwright storage-state file for Facebook.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Path where the Facebook storage-state JSON file should be saved.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path: Path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    playwright = None
    browser = None
    context = None
    page = None
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.facebook.com/", wait_until="domcontentloaded")

        print("A Chromium window has opened at facebook.com.")
        print("Log in manually in the browser. Do not enter credentials in this terminal.")
        input("After login completes and the browser is no longer on a login page, press Enter...")

        if _is_login_url(page.url):
            raise SystemExit("Still on a Facebook login page. Storage state was not saved.")

        context.storage_state(path=str(output_path))
        print(f"Facebook storage state saved to {output_path}")
    finally:
        if page is not None:
            page.close()
        if context is not None:
            context.close()
        if browser is not None:
            browser.close()
        if playwright is not None:
            playwright.stop()


def _is_login_url(url: str) -> bool:
    parsed_url = urlparse(url)
    path = parsed_url.path.rstrip("/")
    return path == "/login" or path.startswith("/login/")


if __name__ == "__main__":
    main()
