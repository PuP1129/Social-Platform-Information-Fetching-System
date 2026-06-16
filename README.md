# 社媒搜索内容采集系统

## 当前支持

当前项目支持：

- Google Programmable Search Engine 单关键词搜索
- X/Facebook 帖子 URL 识别、标准化和过滤
- 单个 X 帖子采集的实验性实现与诊断
- 单个 Facebook 公开帖子的基础采集

X 采集实验目前因登录限制暂停。Facebook 采集只处理无需绕过访问控制即可查看的公开帖子，不使用账号、密码、Cookie 或 storage state。

## 运行环境

本项目在 Windows 本地虚拟环境中运行。执行 Python 命令时请显式使用：

```powershell
.\.venv\Scripts\python.exe
```

不要假设 shell 已经激活虚拟环境。

## 安装依赖

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

## Google PSE 搜索

```powershell
.\.venv\Scripts\python.exe main.py --keyword "OpenAI" --headless --max-results 10
```

输出：

- `output/search_results.json`：Google 原始搜索结果
- `output/post_urls.json`：过滤后的 X/Facebook 帖子 URL

## X 单帖采集

```powershell
.\.venv\Scripts\python.exe main.py --x-url "https://x.com/OpenAI/status/1234567890"
```

如需使用本地 Playwright storage state：

```powershell
.\.venv\Scripts\python.exe scripts/create_x_storage_state.py --output ".playwright/x_test_account.json"
.\.venv\Scripts\python.exe main.py --x-url "https://x.com/OpenAI/status/1234567890" --x-storage-state ".playwright/x_test_account.json"
```

输出：

- `output/x_post.json`
- 失败诊断：`output/debug/x_post_failure.png`、`output/debug/x_post_failure.html`、`output/debug/x_post_diagnostics.json`

## Facebook 单帖采集

```powershell
.\.venv\Scripts\python.exe main.py --facebook-url "https://www.facebook.com/example/posts/123"
```

headless 模式：

```powershell
.\.venv\Scripts\python.exe main.py --facebook-url "https://www.facebook.com/example/posts/123" --headless
```

输出：

- `output/facebook_post.json`
- 失败诊断：`output/debug/facebook_post_failure.png`、`output/debug/facebook_post_failure.html`、`output/debug/facebook_post_diagnostics.json`

当前提取字段：

- `platform`
- `post_id`
- `input_url`
- `canonical_url`
- `author.name`
- `author.profile_url`
- `content`
- `publish_time`
- `publish_time_text`
- `reply_count`
- `like_count`
- `share_count`
- `comment_count`
- `comments`
- `collected_at`
- `collection_status`
- `extraction_source`
- `error`

当前不采集评论内容，`comments` 固定为空数组。Facebook 帖子中的 `reply_count` 通常属于评论内部，本阶段不展开评论，因此一般为 `null`。缺失的互动数量不会默认写成 `0`。

如果目标帖子 DOM 不可用，但页面存在帖子特定的标准 metadata，采集器可以返回 `page_metadata` 来源的 partial 结果。metadata fallback 不会伪造发布时间、互动数量或评论。

登录墙、删除内容、隐私设置、群组限制或网络环境都可能导致采集失败。项目不会绕过验证码、登录限制或平台访问控制。

## 支持的帖子 URL 识别

X：

- `https://x.com/{handle}/status/{post_id}`
- `https://twitter.com/{handle}/status/{post_id}`
- `https://mobile.twitter.com/{handle}/status/{post_id}`
- `https://x.com/{handle}/status/{post_id}/photo/1`
- `https://x.com/{handle}/status/{post_id}/video/1`
- `https://x.com/i/web/status/{post_id}`

Facebook：

- `https://www.facebook.com/{account}/posts/{post_id}`
- `https://facebook.com/{account}/posts/pfbid...`
- `https://m.facebook.com/{account}/posts/{post_id}`
- `https://www.facebook.com/permalink.php?story_fbid=...&id=...`
- `https://m.facebook.com/story.php?story_fbid=...&id=...`

## 测试

```powershell
.\.venv\Scripts\python.exe -m compileall src tests main.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe main.py --help
```

## 当前未实现

- Facebook 评论详情采集
- Facebook 批量帖子采集
- Facebook 自动登录
- 私密帖子、好友可见帖子、群组非公开帖子采集
- Reel、视频页、照片页采集
- X 评论采集
- X 批量采集
- Reddit
- MySQL
- Scheduler
- 并发
- 验证码绕过、代理池或反检测功能
## Facebook batch extraction

Batch mode reuses one authenticated Playwright browser context and processes Facebook post URLs sequentially.

```powershell
.\.venv\Scripts\python.exe main.py `
  --facebook-batch-file "output/facebook_batch_sample_urls.json" `
  --facebook-storage-state ".playwright/facebook_storage_state.json" `
  --facebook-batch-output "output/facebook_posts.jsonl" `
  --facebook-batch-delay 2
```

Optional flags:
- `--facebook-batch-limit 3`
- `--facebook-batch-resume`
- `--headless`

Supported input JSON formats:
- A list of URL strings.
- A list of objects with `url`, `canonical_url`, or `original_url`.
- An object containing a `posts` or `urls` list.

Outputs:
- `output/facebook_posts.jsonl`: one result per processed input item.
- `output/facebook_batch_summary.json`: batch summary and counts.
- `output/debug/facebook_batch/`: per-item diagnostics, HTML, and screenshots.

Batch mode does not collect comments, does not expand Facebook URL support, does not perform automatic login, and does not overwrite `output/facebook_post.json`.
