# 社媒搜索内容采集系统

## 当前支持

- Google Programmable Search Engine 关键词搜索
- X/Facebook 帖子 URL 识别、标准化和过滤
- 单个 X 帖子采集的实验性实现与诊断
- 单个 Facebook 公开帖子基础采集
- Facebook 批量采集与 debug bundle
- 多关键词 Facebook 搜索到批量采集的 pipeline

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

- Facebook 评论详情采集
- Facebook 自动登录或账号密码登录
- Reel、Watch、视频页、照片页专项采集
- X 评论采集
- X 批量采集
- Reddit
- MySQL
- Scheduler
- 并发采集
- 验证码绕过、代理池或反检测功能
