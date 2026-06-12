# 社媒搜索内容采集系统

## 当前支持

当前项目支持三个最小能力：

- 使用 Google Programmable Search Engine 搜索单个关键词
- 识别、标准化并过滤 Google 结果中的 X/Facebook 帖子 URL
- 输入单个公开 X 帖子 URL，采集目标主帖的基础信息

X 单帖采集不会采集评论内容，不使用登录状态、Cookie、账号认证或 X API。若 X 页面要求登录、帖子被删除、账号被冻结、内容不可用，或当前网络环境限制访问，采集可能失败。

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

默认使用有界面的 Chromium：

```powershell
.\.venv\Scripts\python.exe main.py --keyword "OpenAI"
```

headless 模式：

```powershell
.\.venv\Scripts\python.exe main.py --keyword "OpenAI" --headless --max-results 10
```

输出文件：

- `output/search_results.json`：未经过帖子过滤的 Google 原始搜索结果
- `output/post_urls.json`：过滤后的 X/Facebook 帖子 URL

## X 单帖采集

运行命令：

```powershell
.\.venv\Scripts\python.exe main.py --x-url "https://x.com/OpenAI/status/1234567890"
```

headless 模式：

```powershell
.\.venv\Scripts\python.exe main.py --x-url "https://x.com/OpenAI/status/1234567890" --headless
```

输出文件：

- `output/x_post.json`

当前提取字段：

- `platform`
- `post_id`
- `input_url`
- `canonical_url`
- `author.name`
- `author.handle`
- `content`
- `publish_time`
- `reply_count`
- `comment_count`
- `repost_count`
- `like_count`
- `view_count`
- `share_count`
- `comments`
- `collected_at`
- `collection_status`
- `error`

其中 `share_count` 当前固定为 `null`，`comments` 当前固定为空数组。作者、正文、发布时间或互动数据无法从页面公开内容中稳定获取时，允许为 `null`；缺失互动数据不会被默认写成 `0`。

## 支持的帖子 URL 识别

X 支持：

- `https://x.com/{handle}/status/{post_id}`
- `https://twitter.com/{handle}/status/{post_id}`
- `https://mobile.twitter.com/{handle}/status/{post_id}`
- `https://x.com/{handle}/status/{post_id}/photo/1`
- `https://x.com/{handle}/status/{post_id}/video/1`
- `https://x.com/i/web/status/{post_id}`

Facebook 支持：

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

- X 评论采集
- X 批量采集
- Facebook 正文采集
- Reddit
- MySQL
- Scheduler
- Cookie、登录或账号认证
- X API
- Google API
- 代理、验证码绕过或反爬绕过
- 并发采集
