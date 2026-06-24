# Social Media Post Collector

[English](README.md) | [简体中文](README.zh-CN.md)

This project collects public social-media posts by keyword from X and Facebook.
It uses Google Programmable Search Engine (PSE) to discover candidate post URLs,
extracts supported posts with Playwright, normalizes both platforms into one
shared schema, and upserts the final records into MySQL.

JSONL is always retained as a reproducible run artifact and debugging format.

## Main Workflow

```text
keywords
-> Google PSE search
-> X/Facebook URL filtering and deduplication
-> platform post extraction
-> social_post_v1 normalization
-> timestamped JSONL output
-> MySQL author/post/keyword upsert
```

The default platform priority is X first, followed by Facebook. Facebook uses
the remaining global collection limit.

## Requirements

- Windows
- Python with the repository-local `.venv`
- Chromium for Playwright
- MySQL Server 8.x or a compatible MySQL service
- A Google Programmable Search Engine page supported by the existing project

Always use the project virtual environment:

```powershell
.\.venv\Scripts\python.exe
```

Install Python dependencies and Chromium:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

## MySQL Setup

Create a database and a dedicated user. Adjust the host and password for your
environment:

```sql
CREATE DATABASE social_collect
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER 'social_user'@'localhost' IDENTIFIED BY 'replace-with-a-strong-password';
GRANT ALL PRIVILEGES ON social_collect.* TO 'social_user'@'localhost';
FLUSH PRIVILEGES;
```

Set connection variables in the terminal:

```powershell
$env:SOCIAL_DB_HOST = "127.0.0.1"
$env:SOCIAL_DB_PORT = "3306"
$env:SOCIAL_DB_NAME = "social_collect"
$env:SOCIAL_DB_USER = "social_user"
$env:SOCIAL_DB_PASSWORD = "your-password"
```

Do not commit passwords. `.env`, local configuration, browser storage state,
and generated output are ignored by Git.

Initialize the tables:

```powershell
.\.venv\Scripts\python.exe main.py --init-db
```

`--db mysql` is still accepted for backward compatibility, but MySQL is now the
default and only database backend.

## Authentication State

Some public pages still require an authenticated browser session. Login is
performed manually in a headed browser; credentials are never passed through
CLI arguments.

Create Facebook storage state:

```powershell
.\.venv\Scripts\python.exe scripts\create_facebook_storage_state.py `
  --output ".playwright/facebook_storage_state.json"
```

Create X storage state interactively:

```powershell
.\.venv\Scripts\python.exe scripts\create_x_storage_state.py `
  --output ".playwright/x_storage_state.json"
```

The X cookie conversion helper is also available for cookies already present in
environment variables:

```powershell
.\.venv\Scripts\python.exe scripts\create_x_storage_state_from_cookies.py
```

Configure storage-state paths in `configs/social_collect.local.json`. Start by
copying `configs/social_collect.example.json`. Local config files matching
`configs/*.local.json` are ignored by Git.

## Daily Usage

Collect two keywords with a final global limit of 30:

```powershell
.\.venv\Scripts\python.exe main.py `
  --collect "flock camera" `
  --collect "license plate reader" `
  --limit 30
```

The command:

1. searches each keyword once;
2. splits candidates into X and Facebook buckets;
3. extracts posts in configured platform order;
4. writes normalized JSONL;
5. automatically saves to MySQL when all required `SOCIAL_DB_*` variables are
   configured.

To keep JSONL only for a run:

```powershell
.\.venv\Scripts\python.exe main.py --collect "keyword" --limit 10 --no-db
```

Legacy explicit database flags remain valid:

```powershell
.\.venv\Scripts\python.exe main.py `
  --collect "keyword" `
  --db mysql `
  --save-db
```

## Import Existing JSONL

Import a previous normalized JSONL run:

```powershell
.\.venv\Scripts\python.exe main.py `
  --import-jsonl "output/runs/social-collection/example/social_posts.jsonl"
```

Raw X or Facebook JSONL records are normalized at the storage boundary when
possible. Invalid records are counted as failed, while valid records continue
to import.

Repeated imports do not duplicate posts:

- posts are upserted by `(platform, post_id)`;
- authors are reused by platform and profile identity when available;
- post-keyword relationships are deduplicated.

## Output

Unified collection normally creates:

```text
output/runs/social-collection/<UTC timestamp>/
├── social_posts.jsonl
└── social_collect_artifacts/
    ├── search_results.json
    ├── accepted_urls.json
    └── platform batch inputs/summaries
```

The normalized JSONL contains the shared `social_post_v1` fields, including
platform identity, author, content, publication time, metrics, comments,
collection status, platform metrics, and platform context.

MySQL contains:

- `collection_runs`: run metadata, keywords, order, limit, output path, summary
- `authors`: shared X/Facebook author identities
- `posts`: normalized post content, metrics, status, and raw JSON
- `post_keywords`: deduplicated post-to-keyword relationships

## Useful MySQL Queries

Total posts:

```sql
SELECT COUNT(*) FROM posts;
```

Posts by platform:

```sql
SELECT platform, COUNT(*)
FROM posts
GROUP BY platform;
```

Posts by platform and collection status:

```sql
SELECT platform, collection_status, COUNT(*)
FROM posts
GROUP BY platform, collection_status;
```

Recent posts:

```sql
SELECT id, platform, post_id, collection_status, LEFT(content, 80)
FROM posts
ORDER BY id DESC
LIMIT 10;
```

## Configuration and Advanced CLI

Configuration priority for unified collection is:

1. built-in defaults
2. `configs/social_collect.local.json`
3. a file passed with `--config`
4. explicit CLI arguments

Useful advanced options include:

- `--social-platforms "x,facebook"`
- `--social-search-max-results 80`
- `--social-search-timeout-ms 30000`
- `--social-delay 2`
- `--social-output <path>`
- `--facebook-storage-state <path>`
- `--x-storage-state <path>`
- `--headless`

Existing single-post, batch, standalone X search, standalone Facebook search,
Facebook job, resume, retry, export, and debug-bundle modes remain available.
Run `main.py --help` for the full list.

## Troubleshooting

### Google PSE verification or timeout

Google may display human verification or return fewer results. Try headed mode,
reduce the search result count, or retry later. The project does not bypass
CAPTCHA or access controls.

### Missing database environment variables

`--init-db` and `--import-jsonl` report the missing variable names. Normal
collection keeps JSONL output when no complete MySQL configuration exists.

### MySQL connection failure

Verify that MySQL is running, the database exists, the host and port are
reachable, and the configured user has privileges on the selected database.
Passwords are never printed in errors or summaries.

### Facebook or X login issues

Recreate the relevant storage state if it has expired. The extractor does not
pause for interactive login and does not attempt to bypass verification.

### Partial records

A partial record means one or more core fields could not be confirmed from the
page. Review the JSONL output and platform diagnostics. Missing optional metrics
remain `null`; they are not changed to zero unless zero was explicitly observed.

### Duplicate imports

Duplicate `(platform, post_id)` records update the existing database row.
Metrics, content, status, collection time, context, and raw JSON are refreshed.

## Development

Do not commit:

- `.playwright/`
- `.env`
- `configs/*.local.json`
- `output/`
- cookies, passwords, tokens, or storage-state files

Run the offline verification suite with:

```powershell
.\.venv\Scripts\python.exe -m compileall src tests main.py scripts
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe main.py --help
```

The project currently supports X and Facebook. It does not implement CAPTCHA
bypass, proxy rotation, unrestricted private-content access, or every platform
DOM variant.
