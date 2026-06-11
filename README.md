# 社媒搜索内容采集系统

## 当前阶段目标

当前阶段只实现一个最小可运行版本：使用 Playwright 打开 Google Programmable Search Engine 页面，输入单个关键词，获取第一页原始搜索结果，并输出结构化 JSON。

## 项目目录结构

```text
project/
├── src/
│   ├── __init__.py
│   └── search/
│       ├── __init__.py
│       └── google_pse.py
├── legacy/
│   ├── playwright_search_prototype.py
│   ├── playwright_tracing_demo.py
│   └── google_api_prototype.py
├── docs/
│   └── 原任务安排文档.docx
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

默认使用有界面的 Chromium，便于观察页面行为：

```bash
python main.py --keyword "OpenAI"
```

也可以使用 headless 模式：

```bash
python main.py --keyword "OpenAI" --headless
```

限制最多返回结果数量：

```bash
python main.py --keyword "OpenAI" --max-results 5
```

## JSON 输出

搜索结果会写入：

```text
output/search_results.json
```

输出包含关键词、结果数量，以及带有 `rank`、`title`、`url`、`snippet` 的结果列表。没有结果时也会生成合法 JSON。

## 当前未实现

当前阶段未实现以下内容：

- 搜索结果分页
- 帖子 URL 过滤
- X 帖子正文采集
- Facebook 采集
- Reddit
- MySQL
- 定时任务
- 评论采集
- 登录、Cookie 或账号认证
- Google Custom Search JSON API
- API Key 相关代码
