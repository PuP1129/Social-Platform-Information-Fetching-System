# 社交媒体帖子采集系统

[English](README.md) | [简体中文](README.zh-CN.md)

本项目用于根据关键词采集公开社交媒体帖子，目前支持 X 和 Facebook。

项目使用 Google Programmable Search Engine（PSE）搜索候选帖子 URL，通过 Playwright 抓取支持的平台帖子内容，将 X 和 Facebook 的数据统一归一化为同一种 `social_post_v1` 结构，并最终写入 MySQL 数据库。

JSONL 文件会始终保留，作为可复现的运行记录和调试输出。

## 主流程

```text
关键词
-> Google PSE 搜索
-> X/Facebook URL 过滤与去重
-> 平台帖子抓取
-> social_post_v1 归一化
-> 按时间戳保存 JSONL 输出
-> MySQL 作者/帖子/关键词 upsert
```

默认的平台采集优先级是：先采集 X，再采集 Facebook。Facebook 会使用剩余的全局采集数量。

## 环境要求

* Windows
* Python，并使用项目本地 `.venv`
* Playwright Chromium
* MySQL Server 8.x 或兼容的 MySQL 服务
* 一个当前项目支持的 Google Programmable Search Engine 页面

建议始终使用项目虚拟环境中的 Python：

```powershell
.\.venv\Scripts\python.exe
```

安装 Python 依赖和 Chromium：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

## MySQL 配置

先创建数据库和专用用户。可以根据自己的环境修改主机名和密码：

```sql
CREATE DATABASE social_collect
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER 'social_user'@'localhost' IDENTIFIED BY 'replace-with-a-strong-password';
GRANT ALL PRIVILEGES ON social_collect.* TO 'social_user'@'localhost';
FLUSH PRIVILEGES;
```

在终端中设置数据库连接环境变量：

```powershell
$env:SOCIAL_DB_HOST = "127.0.0.1"
$env:SOCIAL_DB_PORT = "3306"
$env:SOCIAL_DB_NAME = "social_collect"
$env:SOCIAL_DB_USER = "social_user"
$env:SOCIAL_DB_PASSWORD = "your-password"
```

不要把数据库密码提交到 Git。`.env`、本地配置文件、浏览器登录状态和生成的输出文件都应该被 Git 忽略。

初始化数据库表：

```powershell
.\.venv\Scripts\python.exe main.py --init-db
```

`--db mysql` 仍然可以使用，用于兼容旧命令。但现在 MySQL 是默认且唯一的数据库后端。

## 登录状态配置

部分公开页面仍然需要已登录的浏览器状态。登录过程通过有界面的浏览器手动完成，账号密码不会通过命令行传入。

创建 Facebook 登录状态：

```powershell
.\.venv\Scripts\python.exe scripts\create_facebook_storage_state.py `
  --output ".playwright/facebook_storage_state.json"
```

交互式创建 X 登录状态：

```powershell
.\.venv\Scripts\python.exe scripts\create_x_storage_state.py `
  --output ".playwright/x_storage_state.json"
```

如果你已经通过环境变量保存了 X 的 cookie，也可以使用 cookie 转换脚本：

```powershell
.\.venv\Scripts\python.exe scripts\create_x_storage_state_from_cookies.py
```

登录状态路径可以写在 `configs/social_collect.local.json` 中。建议先复制一份：

```text
configs/social_collect.example.json
```

然后改成本地配置。匹配 `configs/*.local.json` 的本地配置文件会被 Git 忽略。

## 日常使用

使用两个关键词采集帖子，全局采集上限为 30：

```powershell
.\.venv\Scripts\python.exe main.py `
  --collect "flock camera" `
  --collect "license plate reader" `
  --limit 30
```

这个命令会执行：

1. 对每个关键词进行一次搜索；
2. 将候选 URL 分成 X 和 Facebook 两类；
3. 按配置的平台顺序抓取帖子；
4. 输出归一化 JSONL；
5. 如果 `SOCIAL_DB_*` 环境变量完整，则自动保存到 MySQL。

如果某次运行只想保留 JSONL，不写入数据库：

```powershell
.\.venv\Scripts\python.exe main.py --collect "keyword" --limit 10 --no-db
```

旧版显式数据库参数仍然可用：

```powershell
.\.venv\Scripts\python.exe main.py `
  --collect "keyword" `
  --db mysql `
  --save-db
```

## 导入已有 JSONL

导入之前某次运行生成的归一化 JSONL：

```powershell
.\.venv\Scripts\python.exe main.py `
  --import-jsonl "output/runs/social-collection/example/social_posts.jsonl"
```

在写入数据库前，原始 X 或 Facebook JSONL 记录会在可能的情况下被归一化。无效记录会被计入失败数量，但不会影响其他有效记录继续导入。

重复导入不会产生重复帖子：

* `posts` 表按 `(platform, post_id)` upsert；
* `authors` 表会尽可能根据平台和 profile 身份复用作者；
* `post_keywords` 表会对帖子和关键词关系去重。

## 输出结果

统一采集通常会生成：

```text
output/runs/social-collection/<UTC timestamp>/
├── social_posts.jsonl
└── social_collect_artifacts/
    ├── search_results.json
    ├── accepted_urls.json
    └── 平台批处理输入与 summary
```

归一化 JSONL 包含统一的 `social_post_v1` 字段，包括平台信息、作者信息、正文、发布时间、互动指标、评论、采集状态、平台原生指标和平台上下文信息。

MySQL 中包含以下表：

* `collection_runs`：采集任务元数据、关键词、平台顺序、采集上限、输出路径和 summary
* `authors`：X/Facebook 的统一作者身份
* `posts`：归一化后的帖子内容、互动数据、状态和原始 JSON
* `post_keywords`：去重后的帖子与关键词关系

## 常用 MySQL 查询

查看帖子总数：

```sql
SELECT COUNT(*) FROM posts;
```

按平台统计帖子数：

```sql
SELECT platform, COUNT(*)
FROM posts
GROUP BY platform;
```

按平台和采集状态统计帖子数：

```sql
SELECT platform, collection_status, COUNT(*)
FROM posts
GROUP BY platform, collection_status;
```

查看最近导入的帖子：

```sql
SELECT id, platform, post_id, collection_status, LEFT(content, 80)
FROM posts
ORDER BY id DESC
LIMIT 10;
```

## 配置与高级命令

统一采集的配置优先级如下：

1. 程序内置默认配置；
2. `configs/social_collect.local.json`；
3. 通过 `--config` 指定的配置文件；
4. 显式命令行参数。

常用高级选项包括：

* `--social-platforms "x,facebook"`
* `--social-search-max-results 80`
* `--social-search-timeout-ms 30000`
* `--social-delay 2`
* `--social-output <path>`
* `--facebook-storage-state <path>`
* `--x-storage-state <path>`
* `--headless`

旧版单帖采集、批量采集、独立 X 搜索、独立 Facebook 搜索、Facebook job、resume、retry、export 和 debug bundle 模式仍然保留。可以运行以下命令查看完整参数列表：

```powershell
.\.venv\Scripts\python.exe main.py --help
```

## 常见问题

### Google PSE 出现真人验证或超时

Google 有时会显示真人验证页面，或者返回较少的搜索结果。可以尝试关闭 headless、减少搜索结果数量，或者稍后再试。本项目不会绕过 CAPTCHA 或访问控制。

### 缺少数据库环境变量

`--init-db` 和 `--import-jsonl` 会提示缺失的环境变量名称。如果普通采集时没有完整的 MySQL 配置，项目仍会保留 JSONL 输出。

### MySQL 连接失败

请检查 MySQL 服务是否正在运行，数据库是否已创建，host 和 port 是否可访问，以及配置的用户是否拥有目标数据库权限。错误信息和 summary 中不会打印数据库密码。

### Facebook 或 X 登录状态失效

如果登录状态过期，请重新生成对应平台的 storage state。采集器不会在运行中暂停等待交互式登录，也不会尝试绕过验证页面。

### partial 记录是什么意思

`partial` 表示页面中有一个或多个核心字段无法被可靠确认。可以查看 JSONL 输出和平台诊断信息。缺失的可选指标会保持为 `null`，不会被自动改成 0，除非页面中明确观察到该值为 0。

### 重复导入

重复的 `(platform, post_id)` 记录会更新已有数据库行，而不是插入重复数据。互动指标、正文、采集状态、采集时间、平台上下文和原始 JSON 都会刷新。

## 开发说明

不要提交以下内容：

* `.playwright/`
* `.env`
* `configs/*.local.json`
* `output/`
* cookies、密码、token 或 storage-state 文件

运行离线验证：

```powershell
.\.venv\Scripts\python.exe -m compileall src tests main.py scripts
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe main.py --help
```

目前项目支持 X 和 Facebook。项目不实现 CAPTCHA 绕过、代理池轮换、无限制私有内容访问，也不保证覆盖所有平台 DOM 变体。
