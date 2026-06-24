# 社媒搜索内容采集系统

## 当前支持

- Google Programmable Search Engine 关键词搜索
- X/Facebook 帖子 URL 识别、标准化和过滤
- 单个 X 帖子采集的实验性实现与诊断
- 单个 Facebook 公开帖子基础采集
- Facebook 批量采集与 debug bundle
- 多关键词 Facebook 搜索到批量采集的 pipeline
- 配置驱动的 Facebook run 目录、内部结果/最终结果分离、CSV、报告和结构化日志

X 采集仍受登录限制影响。Facebook 采集只处理无需绕过访问控制即可查看的内容；项目不实现验证码绕过、代理池或平台访问控制绕过。

## 目录结构

- `src/`：正式代码
- `tests/`：离线单元测试
- `scripts/`：辅助脚本
- `examples/`：可复用的示例输入和 manifest 样例
- `configs/`：运行配置样例
- `docs/`：任务文档
- `legacy/`：早期实验脚本
- `output/`：运行产物目录，仅保留 `.gitkeep`

## 运行环境

本项目使用 Windows 本地虚拟环境。执行 Python 命令时请显式使用：

```powershell
.\.venv\Scripts\python.exe
```

安装依赖和浏览器：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

## Simplified Social Collection

For daily collection, prefer the unified config-driven mode:

```powershell
.\.venv\Scripts\python.exe main.py --collect "Flock camera" --limit 50
```

Multiple keywords are supported:

```powershell
.\.venv\Scripts\python.exe main.py `
  --collect "Flock camera" `
  --collect "license plate reader" `
  --collect-file "keywords.json" `
  --limit 50
```

`--collect-file` must point to a JSON string array.

Config priority is:

1. built-in defaults
2. `configs/social_collect.local.json`, when present
3. a file passed with `--config path\to\config.json`
4. explicit CLI arguments

Copy `configs/social_collect.example.json` to
`configs/social_collect.local.json` for local daily settings. Local config files
matching `configs/*.local.json` are ignored by Git, so storage-state paths and
machine-specific output choices stay local. The config can define platforms,
Facebook/X storage-state paths, search result count, collection limit, delay,
headless mode, normalized output, Google PSE timeout, platform order, and
timestamped run output. By default, unified collection tries X first and lets
Facebook fill the remaining global limit.

Useful overrides:

```powershell
.\.venv\Scripts\python.exe main.py `
  --collect "Flock camera" `
  --config "configs/social_collect.local.json" `
  --social-platforms "facebook,x" `
  --social-search-max-results 80 `
  --social-search-timeout-ms 30000 `
  --social-delay 2 `
  --social-output "output/runs/social-collection/manual/social_posts.jsonl" `
  --facebook-storage-state ".playwright/facebook_storage_state.json" `
  --x-storage-state ".playwright/x_storage_state.json" `
  --headless
```

If a configured storage-state file is missing, the command fails with the
platform name, missing path, and the CLI/config option to fix. Storage-state
contents, cookies, and tokens are never printed.

Unified collection always writes normalized `social_post_v1` records. Facebook
group source identity is stored separately under
`platform_context.facebook_group`; when no individual/page author is available,
that group identity can be used as a conservative author fallback. X records use
`author.type: "user"` and an empty `platform_context`.

## MySQL Storage

MySQL can persist the normalized JSONL produced by unified collection. Configure
the connection with `SOCIAL_DB_HOST`, `SOCIAL_DB_PORT` (default `3306`),
`SOCIAL_DB_NAME`, `SOCIAL_DB_USER`, and `SOCIAL_DB_PASSWORD`. Secret values are
not printed or stored in repository files.

Initialize the schema:

```powershell
.\.venv\Scripts\python.exe main.py --init-db --db mysql
```

Collect normally, keep the JSONL output, and import it after the run:

```powershell
.\.venv\Scripts\python.exe main.py `
  --collect "flock camera" `
  --collect "license plate reader" `
  --limit 30 `
  --db mysql `
  --save-db
```

Existing normalized or raw platform JSONL can also be imported explicitly. Raw
records are normalized at the storage boundary:

```powershell
.\.venv\Scripts\python.exe main.py `
  --import-jsonl "output/runs/social-collection/example/social_posts.jsonl" `
  --db mysql
```

Re-importing a `(platform, post_id)` updates the stored post rather than creating
a duplicate. Keyword relationships are also deduplicated.

## Google PSE 搜索

```powershell
.\.venv\Scripts\python.exe main.py --keyword "OpenAI" --headless --max-results 10
```

输出：

- `output/search_results.json`
- `output/post_urls.json`

## X 单帖采集

```powershell
.\.venv\Scripts\python.exe scripts/create_x_storage_state.py --output ".playwright/x_test_account.json"
.\.venv\Scripts\python.exe main.py --x-url "https://x.com/OpenAI/status/1234567890" --x-storage-state ".playwright/x_test_account.json"
```

输出：

- `output/x_post.json`
- `output/debug/x_post_failure.*`

## X 批量采集

输入 JSON 可以是 URL 字符串数组，或包含 `url` 字段的对象数组。运行：

```powershell
.\.venv\Scripts\python.exe main.py `
  --x-batch-file "x_urls.json" `
  --x-storage-state ".playwright/x_storage_state.json" `
  --x-batch-output "output/x_posts.jsonl" `
  --x-batch-delay 2
```

常用选项：

- `--x-batch-limit 10`
- `--x-batch-resume`
- `--normalize-output`
- `--headless`

默认每行保存现有 X extractor 原始结果；使用 `--normalize-output` 时，每行保存统一的 `social_post_v1` 结果。单条失败会写入 `collection_status: "failed"` 的记录，并继续处理后续 URL。当前不采集评论、线程、引用帖详情或媒体文件。

## X keyword search collection

Search one or more keywords with Google PSE, retain supported X post URLs, and pass
the unique posts to the existing X batch collector:

```powershell
.\.venv\Scripts\python.exe main.py `
  --x-search-keyword "OpenAI" `
  --x-search-keyword "Codex" `
  --x-search-keywords-file "x_keywords.json" `
  --x-search-max-results 20 `
  --x-storage-state ".playwright/x_storage_state.json" `
  --x-batch-output "output/x_posts.jsonl" `
  --x-batch-limit 10 `
  --headless
```

The keyword file is a JSON string array. CLI and file keywords are merged and
deduplicated. `--x-search-max-results` applies per keyword; `--x-batch-limit`
controls the number of unique posts actually extracted. Existing
`--x-batch-delay`, `--x-batch-resume`, and `--normalize-output` behavior is
preserved. This mode does not collect comments, threads, quote-post details, or
media files.

## Facebook 单帖采集

```powershell
.\.venv\Scripts\python.exe scripts/create_facebook_storage_state.py --output ".playwright/facebook_storage_state.json"
.\.venv\Scripts\python.exe main.py --facebook-url "https://www.facebook.com/example/posts/123" --facebook-storage-state ".playwright/facebook_storage_state.json"
```

输出：

- `output/facebook_post.json`
- `output/debug/facebook_post_failure.*`

## Facebook 批量采集

```powershell
.\.venv\Scripts\python.exe main.py `
  --facebook-batch-file "examples/facebook_batch_urls.example.json" `
  --facebook-storage-state ".playwright/facebook_storage_state.json" `
  --facebook-batch-output "output/facebook_posts.jsonl" `
  --facebook-batch-delay 2
```

常用选项：

- `--facebook-batch-limit 3`
- `--facebook-batch-resume`
- `--facebook-save-debug-bundle`
- `--headless`

批量输出：

- `output/facebook_posts.jsonl`
- `output/facebook_batch_summary.json`
- `output/debug/facebook_batch/`

## Facebook 搜索 Pipeline

```powershell
.\.venv\Scripts\python.exe main.py `
  --facebook-search-keywords-file "examples/facebook_keywords.example.json" `
  --facebook-storage-state ".playwright/facebook_storage_state.json" `
  --facebook-search-output "output/facebook_search_results.jsonl" `
  --facebook-search-manifest "output/facebook_search_manifest.json"
```

示例文件：

- `examples/facebook_keywords.example.json`
- `examples/facebook_batch_urls.example.json`
- `examples/facebook_group_urls.example.json`
- `examples/facebook_search_manifest.example.json`
- `configs/facebook_job.example.json`

## Facebook Job Config

配置驱动模式会为每次运行创建标准目录：

```text
output/runs/<job_name>/<run_id>/
├── job_config.snapshot.json
├── search_results.json
├── filter_results.json
├── manifest.json
├── pipeline_state.json
├── internal_results.jsonl
├── results.jsonl
├── failed_items.jsonl
├── summary.json
├── results.csv
├── report.md
├── logs.jsonl
└── debug/
```

运行：

```powershell
.\.venv\Scripts\python.exe main.py `
  --facebook-job-config "configs/facebook_job.example.json"
```

常用控制：

```powershell
.\.venv\Scripts\python.exe main.py --facebook-resume-run "output/runs/facebook-text-monitor/<run_id>"
.\.venv\Scripts\python.exe main.py --facebook-retry-failed-run "output/runs/facebook-text-monitor/<run_id>"
.\.venv\Scripts\python.exe main.py --facebook-export-run "output/runs/facebook-text-monitor/<run_id>"
```

`--facebook-ignore-history` 可在配置模式下临时关闭跨运行历史去重。配置中只保存 storage-state 路径，不保存 Cookie、账号密码或 storage-state 内容。

## Raw 与标准化输出

默认情况下，单帖和批量命令继续写入各平台原始结果字段，以保持向后兼容。

在单个 X 帖子、单个 Facebook 帖子、Facebook 直接批量或 Facebook 搜索批量命令中增加：

```text
--normalize-output
```

即可改为写入统一的 `social_post_v1`（`schema_version: "1.0"`）结构。该结构统一作者、正文、发布时间和互动量字段；X 的 `reply_count` 映射为顶层 `comment_count`，并仅在 `platform_metrics.reply_count` 中保留旧字段语义。

当下游需要同时消费 X 与 Facebook 数据时使用标准化输出；依赖现有平台字段或诊断字段时继续使用默认 raw 输出。配置驱动的 Facebook job/export 仍使用其现有固定导出 schema，不受该选项影响。

### Internal Result 与 Final Result

`internal_results.jsonl` 保留工程字段，例如 URL、post_id、状态、错误、debug 路径和输入 metadata。

`results.jsonl` 面向用户，每行严格只有 8 个字段：

```json
{
  "author": "",
  "content": "",
  "publish_time": "",
  "like_count": null,
  "share_count": null,
  "comment_count": null,
  "comments": []
}
```

每条评论只包含：

```json
{
  "comment_content": "",
  "comment_time": "",
  "comment_author": ""
}
```

未知数量使用空字符串；页面明确显示 0 时保留整数 `0`。`results.csv` 使用 `utf-8-sig`，方便 Windows Excel 打开中文内容。

## 清理生成物

查看将被清理的 Python 缓存：

```powershell
.\.venv\Scripts\python.exe scripts/clean_generated.py --dry-run
```

实际清理：

```powershell
.\.venv\Scripts\python.exe scripts/clean_generated.py
```

该脚本不会删除 `output/`、`examples/`、`configs/`、`.playwright/` 或登录态文件。

## 测试

```powershell
.\.venv\Scripts\python.exe -m compileall src tests main.py scripts
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe main.py --help
```

## 当前未实现

- 完整 Facebook 评论/楼中楼深度采集
- Facebook 自动登录或账号密码登录
- Reel、Watch、视频页、照片页专项采集
- X 评论采集
- X 批量采集
- Reddit
- MySQL
- Scheduler
- 并发采集
- 验证码绕过、代理池或反检测功能
