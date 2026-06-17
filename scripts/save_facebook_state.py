from pathlib import Path
from playwright.sync_api import sync_playwright

STATE_PATH = Path(".playwright/facebook_storage_state.json")
STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()

    page.goto(
        "https://www.facebook.com/",
        wait_until="domcontentloaded",
        timeout=60000,
    )

    print("Log in to Facebook in the opened browser.")
    input("After login is complete, return here and press Enter to save: ")

    context.storage_state(path=str(STATE_PATH))
    print(f"Saved storage state to: {STATE_PATH}")

    browser.close()
