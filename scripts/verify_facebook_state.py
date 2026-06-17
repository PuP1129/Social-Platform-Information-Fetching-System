from playwright.sync_api import sync_playwright

POST_URL = "https://www.facebook.com/groups/454270309188726/posts/1375712860377795"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)

    context = browser.new_context(
        storage_state=".playwright/facebook_storage_state.json"
    )

    page = context.new_page()
    page.goto(
        POST_URL,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    print("Current URL:", page.url)
    print("Login input count:", page.locator('input[name="email"]').count())
    print("Browser stays open for 120 seconds.")

    page.wait_for_timeout(120000)
    browser.close()
