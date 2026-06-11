from playwright.sync_api import sync_playwright

p = sync_playwright().start()
browser = p.chromium.launch(headless=False)

# 创建 BrowserContext对象
context = browser.new_context()
# 启动跟踪功能
context.tracing.start(snapshots=True, sources=True, screenshots=True)

page = context.new_page()
page.goto("https://www.byhy.net/cdn2/files/selenium/stock1.html")

# 搜索名称中包含 通讯 的股票
page.locator('#kw').fill('通讯')
page.locator('#go').click()

page.wait_for_timeout(1000) # 等待1秒

lcs = page.locator(".result-item").all()
for lc in lcs:
    print(lc.inner_text())

# 搜索名称中包含 软件 的股票
page.locator('#kw').fill('软件')
page.locator('#go').click()

page.wait_for_timeout(1000) # 等待1秒

lcs = page.locator(".result-item").all()
for lc in lcs:
    print(lc.inner_text())

# 结束跟踪
context.tracing.stop(path="trace.zip")

browser.close()
p.stop()