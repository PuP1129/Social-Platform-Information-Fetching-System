# 社媒搜索内容采集系统

## 当前阶段目标

当前项目支持单关键词 Google Programmable Search Engine 搜索，并对第一页原始搜索结果中的 URL 做保守识别，只保留明确匹配的 X/Facebook 帖子 URL。

本阶段只识别和规范化帖子 URL，不打开 X 或 Facebook 页面，也不采集正文、作者、发布时间、互动数据或评论。

## 项目目录结构

```text
project/
├── src/
│   ├── filters/
│   │   ├── __init__.py
│   │   └── post_url.py
│   ├── search/
│   │   ├── __init__.py
│   │   └── google_pse.py
│   └── __init__.py
├── tests/
│   └── test_post_url.py
├── legacy/
├── docs/
├── output/
│   └── .gitkeep
├── main.py
├── requirements.txt
├── README.md
└── .gitignore
```

## 安装依赖

```bash
pip install -r requirements.txt
```

首次安装 Playwright 后，需要安装 Chromium：

```bash
playwright install chromium
```

## 运行方式

默认使用有界面的 Chromium：

```bash
python main.py --keyword "OpenAI"
```

使用 headless 模式：

```bash
python main.py --keyword "OpenAI" --headless
```

限制最多返回结果数量：

```bash
python main.py --keyword "OpenAI" --max-results 5
```

## JSON 输出

原始 Google PSE 搜索结果写入：

```text
output/search_results.json
```

过滤后的帖子 URL 写入：

```text
output/post_urls.json
```

如果没有识别到帖子 URL，`post_urls.json` 仍会生成合法 JSON，`post_count` 为 `0`，`posts` 为空数组。

## 支持的 X URL 格式

支持以下域名，并统一规范化为 `x.com`：

- `x.com`
- `www.x.com`
- `twitter.com`
- `www.twitter.com`
- `mobile.twitter.com`

当前支持的帖子格式：

- `https://x.com/openai/status/1234567890`
- `https://twitter.com/OpenAI/status/1234567890`
- `https://mobile.twitter.com/openai/status/1234567890`
- `https://x.com/openai/status/1234567890/photo/1`
- `https://x.com/openai/status/1234567890/video/1`
- `https://x.com/i/web/status/1234567890`

会删除 query、fragment、`/photo/1`、`/video/1` 以及 status ID 后面的展示后缀。去重优先使用 `(platform, post_id)`。

## 支持的 Facebook URL 格式

支持以下域名，并统一规范化为 `www.facebook.com`：

- `facebook.com`
- `www.facebook.com`
- `m.facebook.com`

当前支持的帖子格式：

- `https://www.facebook.com/openai/posts/1234567890`
- `https://facebook.com/openai/posts/pfbidExample123`
- `https://m.facebook.com/openai/posts/1234567890`
- `https://www.facebook.com/permalink.php?story_fbid=1234567890&id=987654321`
- `https://m.facebook.com/story.php?story_fbid=1234567890&id=987654321`

`/posts/` 后的 ID 支持数字或以 `pfbid` 开头的字母数字标识。`permalink.php` 和 `story.php` 至少需要有效的 `story_fbid`。

## 明确不支持的 URL

当前不会把以下页面识别为帖子：

- X 用户主页、搜索页、话题页、首页、intent 页面
- Facebook 用户主页、搜索页、登录页、marketplace、groups、reel、watch、videos、photos、share 页面
- Reddit 页面
- 无法确定为帖子详情页的其他 URL

## 运行测试

```bash
python -m unittest discover -s tests -v
```

也可以执行基础检查：

```bash
python -m compileall src tests main.py
python main.py --help
```

## 当前未实现

当前阶段未实现以下内容：

- 搜索结果分页
- X/Facebook 正文采集
- 评论采集
- Cookie、登录或账号认证
- Reddit
- MySQL
- 定时任务
- Google Custom Search JSON API
- API Key 相关代码
- 并发采集
