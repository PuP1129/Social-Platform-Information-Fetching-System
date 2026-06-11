import json
from playwright.sync_api import sync_playwright

SEARCH_ENGINE_URL = "https://cse.google.com/cse?cx=e26d7e4e0bc774ea6"
TIMEOUT_TIME = 2000 #2秒
SEARCH_KEYS = [
    "twitter animal",
    "math"
]
POSTS_URL = []
DATA_DISC = []


def searchKey(locator_input, search_key):
    search_input = page.locator(locator_input)
    search_input.fill(search_key)
    search_input.press("Enter")
    page.wait_for_timeout(TIMEOUT_TIME)
    print(page.title)

def getPostsUrl(locator_input):
    posts_url = []
    results = page.locator(locator_input).all()
    for result in results:
        if result is not None:
            posts_url.append(result.get_attribute("href"))
    unique_urls = list(dict.fromkeys(posts_url))
    for url in unique_urls:
        print(url)
    return unique_urls

def clickNextPage(page, current_index):
    next_page = page.locator(".gsc-cursor-page").nth(current_index)
    if page.locator(".gsc-cursor-page").count() <= current_index:
        print("no more page")
        return False

    next_page.click()
    print(f"currently on page {current_index + 1}")
    page.wait_for_timeout(TIMEOUT_TIME)
    return True

def normalize_url(url):
    if url is None:
        return None
    url = url.strip()
    url = url.split("?")[0]
    url = url.rstrip("/")
    # 统一 twitter.com 为 x.com
    url = url.replace("https://twitter.com/", "https://x.com/")
    url = url.replace("https://www.twitter.com/", "https://x.com/")
    url = url.replace("https://www.x.com/", "https://x.com/")

    return url

def is_x_post_url(url):
    url = normalize_url(url)

    if url is None:
        return False

    if not url.startswith("https://x.com/"):
        return False

    # 去掉前缀，只保留后面的路径
    # 例如 Reuters/status/2056619622816751725
    path = url.replace("https://x.com/", "", 1)

    parts = path.split("/")

    # 普通 post:
    # Reuters/status/2056619622816751725
    if len(parts) == 3:
        username = parts[0]
        middle = parts[1]
        post_id = parts[2]

        return username != "" and middle == "status" and post_id.isdigit()

    # 兼容这种形式:
    # x.com/i/web/status/2056619622816751725
    if len(parts) == 4:
        return parts[0] == "i" and parts[1] == "web" and parts[2] == "status" and parts[3].isdigit()

    return False



# 创建playwright实例
# p = sync_playwright().start()
with sync_playwright() as p:
    # 启动chromium浏览器
    browser = p.chromium.launch(headless=False)
    # 新建一页
    page = browser.new_page()
    page.goto(SEARCH_ENGINE_URL)
    print(page.title)

    # TODO 依次输入搜索关键词
    search_input = page.locator("#gsc-i-id1")
    search_input.fill(SEARCH_KEYS[0])
    search_input.press("Enter")
    page.wait_for_timeout(TIMEOUT_TIME)
    print(page.title)
    # 查询所有页面,共10页
    unique_urls = []
    for i in range(10):
        success = clickNextPage(page, i)
        if not success:
            break
        unique_urls.extend(getPostsUrl("a.gs-title"))
        if i+2 <= 10:
            print(f"going to page {i + 2}")
        page.wait_for_timeout(TIMEOUT_TIME)

    # 对unique_urls进行筛选，去除除帖子外的所有url
    print("\n筛选后的帖子url：")
    for url in unique_urls:
        if is_x_post_url(url):
            POSTS_URL.append(url)
            print(url)


    input("Enter to close...")





