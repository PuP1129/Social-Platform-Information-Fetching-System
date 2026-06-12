from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a local Playwright storage-state file for X.")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path where the storage-state JSON file should be saved.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path: Path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://x.com/login", wait_until="domcontentloaded")

        print("A Chromium window has opened. Log in to X manually.")
        input("After login completes in the browser, press Enter here to save storage state...")

        context.storage_state(path=str(output_path))
        context.close()
        browser.close()

    print(f"Storage state saved to {output_path}")


if __name__ == "__main__":
    main()
