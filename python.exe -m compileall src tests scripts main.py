warning: in the working copy of '.gitignore', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'README.md', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'main.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'src/extractors/x_post.py', LF will be replaced by CRLF the next time Git touches it
[1mdiff --git a/.gitignore b/.gitignore[m
[1mindex 9842242..46984c9 100644[m
[1m--- a/.gitignore[m
[1m+++ b/.gitignore[m
[36m@@ -35,6 +35,7 @@[m [mtest-results/[m
 .playwright/[m
 playwright/.auth/[m
 *_storage_state.json[m
[32m+[m[32mfacebook_storage_state.json[m
 storage_state.json[m
 cookies.json[m
 [m
[1mdiff --git a/README.md b/README.md[m
[1mindex 1043ccb..e0100b0 100644[m
[1m--- a/README.md[m
[1m+++ b/README.md[m
[36m@@ -2,13 +2,14 @@[m
 [m
 ## 当前支持[m
 [m
[31m-当前项目支持三个最小能力：[m
[32m+[m[32m当前项目支持：[m
 [m
[31m-- 使用 Google Programmable Search Engine 搜索单个关键词[m
[31m-- 识别、标准化并过滤 Google 结果中的 X/Facebook 帖子 URL[m
[31m-- 输入单个公开 X 帖子 URL，采集目标主帖的基础信息[m
[32m+[m[32m- Google Programmable Search Engine 单关键词搜索[m
[32m+[m[32m- X/Facebook 帖子 URL 识别、标准化和过滤[m
[32m+[m[32m- 单个 X 帖子采集的实验性实现与诊断[m
[32m+[m[32m- 单个 Facebook 公开帖子的基础采集[m
 [m
[31m-X 单帖采集不会采集评论内容，不使用登录状态、Cookie、账号认证或 X API。若 X 页面要求登录、帖子被删除、账号被冻结、内容不可用，或当前网络环境限制访问，采集可能失败。[m
[32m+[m[32mX 采集实验目前因登录限制暂停。Facebook 采集只处理无需绕过访问控制即可查看的公开帖子，不使用账号、密码、Cookie 或 storage state。[m
 [m
 ## 运行环境[m
 [m
[36m@@ -29,40 +30,49 @@[m [mX 单帖采集不会采集评论内容，不使用登录状态、Cookie、账号[m
 [m
 ## Google PSE 搜索[m
 [m
[31m-默认使用有界面的 Chromium：[m
[31m-[m
 ```powershell[m
[31m-.\.venv\Scripts\python.exe main.py --keyword "OpenAI"[m
[32m+[m[32m.\.venv\Scripts\python.exe main.py --keyword "OpenAI" --headless --max-results 10[m
 ```[m
 [m
[31m-headless 模式：[m
[32m+[m[32m输出：[m
[32m+[m
[32m+[m[32m- `output/search_results.json`：Google 原始搜索结果[m
[32m+[m[32m- `output/post_urls.json`：过滤后的 X/Facebook 帖子 URL[m
[32m+[m
[32m+[m[32m## X 单帖采集[m
 [m
 ```powershell[m
[31m-.\.venv\Scripts\python.exe main.py --keyword "OpenAI" --headless --max-results 10[m
[32m+[m[32m.\.venv\Scripts\python.exe main.py --x-url "https://x.com/OpenAI/status/1234567890"[m
 ```[m
 [m
[31m-输出文件：[m
[32m+[m[32m如需使用本地 Playwright storage state：[m
 [m
[31m-- `output/search_results.json`：未经过帖子过滤的 Google 原始搜索结果[m
[31m-- `output/post_urls.json`：过滤后的 X/Facebook 帖子 URL[m
[32m+[m[32m```powershell[m
[32m+[m[32m.\.venv\Scripts\python.exe scripts/create_x_storage_state.py --output ".playwright/x_test_account.json"[m
[32m+[m[32m.\.venv\Scripts\python.exe main.py --x-url "https://x.com/OpenAI/status/1234567890" --x-storage-state ".playwright/x_test_account.json"[m
[32m+[m[32m```[m
 [m
[31m-## X 单帖采集[m
[32m+[m[32m输出：[m
 [m
[31m-运行命令：[m
[32m+[m[32m- `output/x_post.json`[m
[32m+[m[32m- 失败诊断：`output/debug/x_post_failure.png`、`output/debug/x_post_failure.html`、`output/debug/x_post_diagnostics.json`[m
[32m+[m
[32m+[m[32m## Facebook 单帖采集[m
 [m
 ```powershell[m
[31m-.\.venv\Scripts\python.exe main.py --x-url "https://x.com/OpenAI/status/1234567890"[m
[32m+[m[32m.\.venv\Scripts\python.exe main.py --facebook-url "https://www.facebook.com/example/posts/123"[m
 ```[m
 [m
 headless 模式：[m
 [m
 ```powershell[m
[31m-.\.venv\Scripts\python.exe main.py --x-url "https://x.com/OpenAI/status/1234567890" --headless[m
[32m+[m[32m.\.venv\Scripts\python.exe main.py --facebook-url "https://www.facebook.com/example/posts/123" --headless[m
 ```[m
 [m
[31m-输出文件：[m
[32m+[m[32m输出：[m
 [m
[31m-- `output/x_post.json`[m
[32m+[m[32m- `output/facebook_post.json`[m
[32m+[m[32m- 失败诊断：`output/debug/facebook_post_failure.png`、`output/debug/facebook_post_failure.html`、`output/debug/facebook_post_diagnostics.json`[m
 [m
 当前提取字段：[m
 [m
[36m@@ -71,25 +81,29 @@[m [mheadless 模式：[m
 - `input_url`[m
 - `canonical_url`[m
 - `author.name`[m
[31m-- `author.handle`[m
[32m+[m[32m- `author.profile_url`[m
 - `content`[m
 - `publish_time`[m
[32m+[m[32m- `publish_time_text`[m
 - `reply_count`[m
[31m-- `comment_count`[m
[31m-- `repost_count`[m
 - `like_count`[m
[31m-- `view_count`[m
 - `share_count`[m
[32m+[m[32m- `comment_count`[m
 - `comments`[m
 - `collected_at`[m
 - `collection_status`[m
[32m+[m[32m- `extraction_source`[m
 - `error`[m
 [m
[31m-其中 `share_count` 当前固定为 `null`，`comments` 当前固定为空数组。作者、正文、发布时间或互动数据无法从页面公开内容中稳定获取时，允许为 `null`；缺失互动数据不会被默认写成 `0`。[m
[32m+[m[32m当前不采集评论内容，`comments` 固定为空数组。Facebook 帖子中的 `reply_count` 通常属于评论内部，本阶段不展开评论，因此一般为 `null`。缺失的互动数量不会默认写成 `0`。[m
[32m+[m
[32m+[m[32m如果目标帖子 DOM 不可用，但页面存在帖子特定的标准 metadata，采集器可以返回 `page_metadata` 来源的 partial 结果。metadata fallback 不会伪造发布时间、互动数量或评论。[m
[32m+[m
[32m+[m[32m登录墙、删除内容、隐私设置、群组限制或网络环境都可能导致采集失败。项目不会绕过验证码、登录限制或平台访问控制。[m
 [m
 ## 支持的帖子 URL 识别[m
 [m
[31m-X 支持：[m
[32m+[m[32mX：[m
 [m
 - `https://x.com/{handle}/status/{post_id}`[m
 - `https://twitter.com/{handle}/status/{post_id}`[m
[36m@@ -98,7 +112,7 @@[m [mX 支持：[m
 - `https://x.com/{handle}/status/{post_id}/video/1`[m
 - `https://x.com/i/web/status/{post_id}`[m
 [m
[31m-Facebook 支持：[m
[32m+[m[32mFacebook：[m
 [m
 - `https://www.facebook.com/{account}/posts/{post_id}`[m
 - `https://facebook.com/{account}/posts/pfbid...`[m
[36m@@ -116,14 +130,15 @@[m [mFacebook 支持：[m
 [m
 ## 当前未实现[m
 [m
[32m+[m[32m- Facebook 评论详情采集[m
[32m+[m[32m- Facebook 批量帖子采集[m
[32m+[m[32m- Facebook 自动登录[m
[32m+[m[32m- 私密帖子、好友可见帖子、群组非公开帖子采集[m
[32m+[m[32m- Reel、视频页、照片页采集[m
 - X 评论采集[m
 - X 批量采集[m
[31m-- Facebook 正文采集[m
 - Reddit[m
 - MySQL[m
 - Scheduler[m
[31m-- Cookie、登录或账号认证[m
[31m-- X API[m
[31m-- Google API[m
[31m-- 代理、验证码绕过或反爬绕过[m
[31m-- 并发采集[m
[32m+[m[32m- 并发[m
[32m+[m[32m- 验证码绕过、代理池或反检测功能[m
[1mdiff --git a/main.py b/main.py[m
[1mindex 0890e60..66ce2bc 100644[m
[1m--- a/main.py[m
[1m+++ b/main.py[m
[36m@@ -9,6 +9,7 @@[m [mfrom src.filters.post_url import filter_post_results[m
 RAW_OUTPUT_PATH = Path("output") / "search_results.json"[m
 POST_OUTPUT_PATH = Path("output") / "post_urls.json"[m
 X_POST_OUTPUT_PATH = Path("output") / "x_post.json"[m
[32m+[m[32mFACEBOOK_POST_OUTPUT_PATH = Path("output") / "facebook_post.json"[m
 [m
 [m
 def parse_args() -> argparse.Namespace:[m
[36m@@ -16,6 +17,7 @@[m [mdef parse_args() -> argparse.Namespace:[m
     input_group = parser.add_mutually_exclusive_group(required=True)[m
     input_group.add_argument("--keyword", help="Single keyword to search.")[m
     input_group.add_argument("--x-url", help="Single X post URL to extract.")[m
[32m+[m[32m    input_group.add_argument("--facebook-url", help="Single public Facebook post URL to extract.")[m
     parser.add_argument("--headless", action="store_true", help="Run Chromium in headless mode.")[m
     parser.add_argument("--max-results", type=int, default=10, help="Maximum number of first-page results to save.")[m
     parser.add_argument([m
[36m@@ -24,6 +26,12 @@[m [mdef parse_args() -> argparse.Namespace:[m
         default=None,[m
         help="Path to a local Playwright storage-state JSON file for authenticated X access.",[m
     )[m
[32m+[m[32m    parser.add_argument([m
[32m+[m[32m        "--facebook-storage-state",[m
[32m+[m[32m        type=Path,[m
[32m+[m[32m        default=None,[m
[32m+[m[32m        help="Path to a local Playwright storage-state JSON file for authenticated Facebook access.",[m
[32m+[m[32m    )[m
     return parser.parse_args()[m
 [m
 [m
[36m@@ -67,8 +75,14 @@[m [mdef main() -> None:[m
     args = parse_args()[m
     if args.keyword:[m
         run_search(args.keyword.strip(), args.headless, args.max_results)[m
[31m-    else:[m
[32m+[m[32m    elif args.x_url:[m
         run_x_post_extraction(args.x_url.strip(), args.headless, args.x_storage_state)[m
[32m+[m[32m    else:[m
[32m+[m[32m        run_facebook_post_extraction([m
[32m+[m[32m            args.facebook_url.strip(),[m
[32m+[m[32m            args.headless,[m
[32m+[m[32m            args.facebook_storage_state,[m
[32m+[m[32m        )[m
 [m
 [m
 def run_search(keyword: str, headless: bool, max_results: int) -> None:[m
[36m@@ -120,6 +134,34 @@[m [mdef run_x_post_extraction(url: str, headless: bool, storage_state_path: Path | N[m
     print(f"JSON written to {X_POST_OUTPUT_PATH}")[m
 [m
 [m
[32m+[m[32mdef run_facebook_post_extraction([m
[32m+[m[32m    url: str,[m
[32m+[m[32m    headless: bool,[m
[32m+[m[32m    storage_state_path: Path | None = None,[m
[32m+[m[32m) -> None:[m
[32m+[m[32m    try:[m
[32m+[m[32m        from src.extractors.facebook_post import FacebookPostExtractionError, extract_facebook_post[m
[32m+[m[32m    except ModuleNotFoundError as exc:[m
[32m+[m[32m        _raise_playwright_install_error(exc)[m
[32m+[m
[32m+[m[32m    print(f"Extracting Facebook post: {url}")[m
[32m+[m[32m    try:[m
[32m+[m[32m        payload = extract_facebook_post([m
[32m+[m[32m            url=url,[m
[32m+[m[32m            headless=headless,[m
[32m+[m[32m            storage_state_path=storage_state_path,[m
[32m+[m[32m        )[m
[32m+[m[32m    except (ValueError, FacebookPostExtractionError) as exc:[m
[32m+[m[32m        raise SystemExit(f"Facebook post extraction failed: {exc}") from exc[m
[32m+[m
[32m+[m[32m    write_output(payload, FACEBOOK_POST_OUTPUT_PATH)[m
[32m+[m[32m    if payload["collection_status"] == "success":[m
[32m+[m[32m        print("Facebook post extracted successfully.")[m
[32m+[m[32m    else:[m
[32m+[m[32m        print("Facebook post extracted with partial data.")[m
[32m+[m[32m    print(f"JSON written to {FACEBOOK_POST_OUTPUT_PATH}")[m
[32m+[m
[32m+[m
 def _raise_playwright_install_error(exc: ModuleNotFoundError) -> None:[m
     if exc.name == "playwright":[m
         raise SystemExit([m
[1mdiff --git a/src/extractors/x_post.py b/src/extractors/x_post.py[m
[1mindex 4765d98..09276ec 100644[m
[1m--- a/src/extractors/x_post.py[m
[1m+++ b/src/extractors/x_post.py[m
[36m@@ -1,6 +1,5 @@[m
 from __future__ import annotations[m
 [m
[31m-import re[m
 import json[m
 from datetime import datetime, timezone[m
 from pathlib import Path[m
[36m@@ -11,6 +10,7 @@[m [mfrom playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeout[m
 from playwright.sync_api import sync_playwright[m
 [m
 from src.filters.post_url import classify_post_url[m
[32m+[m[32mfrom src.utils.metrics import parse_metric_count[m
 [m
 TWEET_ARTICLE_SELECTOR = 'article[data-testid="tweet"]'[m
 DEBUG_OUTPUT_DIR = Path("output") / "debug"[m
[36m@@ -111,45 +111,6 @@[m [mdef _validate_storage_state_path(storage_state_path: str | Path | None) -> Path[m
     return path[m
 [m
 [m
[31m-def parse_metric_count(value: str | None) -> int | None:[m
[31m-    """Parse X-style metric counts such as 1.2K, 3.5M, or 12 replies."""[m
[31m-    if value is None:[m
[31m-        return None[m
[31m-[m
[31m-    text = value.strip()[m
[31m-    if not text:[m
[31m-        return None[m
[31m-[m
[31m-    match = re.search([m
[31m-        r"(\d+(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)(\s*[KkMmBb\u4e07\u4ebf\u5104])?",[m
[31m-        text,[m
[31m-    )[m
[31m-    if match is None:[m
[31m-        return None[m
[31m-[m
[31m-    number_text = match.group(1).replace(",", "")[m
[31m-    suffix = (match.group(2) or "").strip().lower()[m
[31m-[m
[31m-    try:[m
[31m-        number = float(number_text)[m
[31m-    except ValueError:[m
[31m-        return None[m
[31m-[m
[31m-    multiplier = {[m
[31m-        "": 1,[m
[31m-        "k": 1_000,[m
[31m-        "m": 1_000_000,[m
[31m-        "b": 1_000_000_000,[m
[31m-        "\u4e07": 10_000,[m
[31m-        "\u4ebf": 100_000_000,[m
[31m-        "\u5104": 100_000_000,[m
[31m-    }.get(suffix)[m
[31m-    if multiplier is None:[m
[31m-        return None[m
[31m-[m
[31m-    return int(number * multiplier)[m
[31m-[m
[31m-[m
 def _open_post_page(page: Page, canonical_url: str, timeout_ms: int) -> None:[m
     try:[m
         page.goto(canonical_url, wait_until="domcontentloaded", timeout=timeout_ms)[m
[36m@@ -580,6 +541,8 @@[m [mdef _safe_inner_text(locator: Locator, preserve_lines: bool = False) -> str | No[m
 [m
 [m
 def _first_text_matching(locator: Locator, pattern: str) -> str | None:[m
[32m+[m[32m    import re[m
[32m+[m
     compiled = re.compile(pattern)[m
     for index in range(locator.count()):[m
         text = _safe_inner_text(locator.nth(index))[m
