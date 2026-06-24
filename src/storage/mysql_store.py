from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.utils.social_post_normalizer import normalize_social_post


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS collection_runs (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        started_at DATETIME(6) NULL,
        finished_at DATETIME(6) NULL,
        keywords_json JSON NULL,
        platform_order_json JSON NULL,
        global_limit INT NULL,
        output_path TEXT NULL,
        summary_json JSON NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        PRIMARY KEY (id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS authors (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        platform VARCHAR(32) NOT NULL,
        name VARCHAR(512) NULL,
        handle VARCHAR(255) NULL,
        profile_url VARCHAR(2048) NULL,
        type VARCHAR(32) NOT NULL DEFAULT 'unknown',
        raw_json JSON NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
            ON UPDATE CURRENT_TIMESTAMP(6),
        PRIMARY KEY (id),
        UNIQUE KEY uq_authors_platform_profile_url (platform, profile_url(512)),
        KEY idx_authors_platform_handle (platform, handle)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS posts (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        platform VARCHAR(32) NOT NULL,
        post_id VARCHAR(255) NOT NULL,
        input_url TEXT NULL,
        canonical_url TEXT NULL,
        author_id BIGINT UNSIGNED NULL,
        collection_run_id BIGINT UNSIGNED NULL,
        content LONGTEXT NULL,
        publish_time DATETIME(6) NULL,
        comment_count BIGINT NULL,
        like_count BIGINT NULL,
        share_count BIGINT NULL,
        repost_count BIGINT NULL,
        view_count BIGINT NULL,
        collection_status VARCHAR(32) NULL,
        error TEXT NULL,
        collected_at DATETIME(6) NULL,
        platform_metrics_json JSON NULL,
        platform_context_json JSON NULL,
        raw_json JSON NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
            ON UPDATE CURRENT_TIMESTAMP(6),
        PRIMARY KEY (id),
        UNIQUE KEY uq_posts_platform_post_id (platform, post_id),
        KEY idx_posts_author_id (author_id),
        KEY idx_posts_collection_run_id (collection_run_id),
        CONSTRAINT fk_posts_author
            FOREIGN KEY (author_id) REFERENCES authors(id) ON DELETE SET NULL,
        CONSTRAINT fk_posts_collection_run
            FOREIGN KEY (collection_run_id) REFERENCES collection_runs(id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS post_keywords (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        post_id BIGINT UNSIGNED NOT NULL,
        keyword VARCHAR(512) NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        PRIMARY KEY (id),
        UNIQUE KEY uq_post_keywords_post_keyword (post_id, keyword),
        CONSTRAINT fk_post_keywords_post
            FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
)


class MySQLStoreError(RuntimeError):
    """Raised when MySQL configuration or persistence fails."""


@dataclass(frozen=True)
class MySQLConfig:
    host: str
    port: int
    database: str
    user: str
    password: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> MySQLConfig:
        values = os.environ if environ is None else environ
        required = {
            "SOCIAL_DB_HOST": values.get("SOCIAL_DB_HOST"),
            "SOCIAL_DB_NAME": values.get("SOCIAL_DB_NAME"),
            "SOCIAL_DB_USER": values.get("SOCIAL_DB_USER"),
            "SOCIAL_DB_PASSWORD": values.get("SOCIAL_DB_PASSWORD"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise MySQLStoreError(
                "Missing MySQL environment variable(s): " + ", ".join(missing)
            )
        port_value = values.get("SOCIAL_DB_PORT", "3306")
        try:
            port = int(port_value)
        except ValueError as exc:
            raise MySQLStoreError("SOCIAL_DB_PORT must be an integer.") from exc
        if not 1 <= port <= 65535:
            raise MySQLStoreError("SOCIAL_DB_PORT must be between 1 and 65535.")
        return cls(
            host=str(required["SOCIAL_DB_HOST"]),
            port=port,
            database=str(required["SOCIAL_DB_NAME"]),
            user=str(required["SOCIAL_DB_USER"]),
            password=str(required["SOCIAL_DB_PASSWORD"]),
        )


def mysql_environment_configured(environ: Mapping[str, str] | None = None) -> bool:
    values = os.environ if environ is None else environ
    return all(
        values.get(name)
        for name in (
            "SOCIAL_DB_HOST",
            "SOCIAL_DB_NAME",
            "SOCIAL_DB_USER",
            "SOCIAL_DB_PASSWORD",
        )
    )


def connect_mysql(config: MySQLConfig | None = None):
    try:
        import pymysql
    except ModuleNotFoundError as exc:
        raise MySQLStoreError(
            "PyMySQL is not installed. Run `pip install -r requirements.txt`."
        ) from exc

    selected = config or MySQLConfig.from_env()
    try:
        return pymysql.connect(
            host=selected.host,
            port=selected.port,
            database=selected.database,
            user=selected.user,
            password=selected.password,
            charset="utf8mb4",
            autocommit=False,
        )
    except Exception as exc:
        raise MySQLStoreError(
            f"Could not connect to MySQL at {selected.host}:{selected.port}/"
            f"{selected.database} as {selected.user}: {type(exc).__name__}."
        ) from exc


class MySQLStore:
    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.last_author_action: str | None = None
        self.last_post_action: str | None = None
        self.last_keyword_action: str | None = None

    def init_db(self) -> None:
        try:
            with self.connection.cursor() as cursor:
                for statement in SCHEMA_STATEMENTS:
                    cursor.execute(statement)
            self.connection.commit()
        except Exception as exc:
            self.connection.rollback()
            raise MySQLStoreError(f"Could not initialize MySQL schema: {type(exc).__name__}.") from exc

    def insert_collection_run(
        self,
        *,
        started_at: str | datetime | None = None,
        finished_at: str | datetime | None = None,
        keywords: list[str] | None = None,
        platform_order: list[str] | None = None,
        global_limit: int | None = None,
        output_path: str | Path | None = None,
        summary: Mapping[str, Any] | None = None,
    ) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO collection_runs (
                    started_at, finished_at, keywords_json, platform_order_json,
                    global_limit, output_path, summary_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    _mysql_datetime(started_at),
                    _mysql_datetime(finished_at),
                    _json_or_none(keywords),
                    _json_or_none(platform_order),
                    global_limit,
                    str(output_path) if output_path is not None else None,
                    _json_or_none(summary),
                ),
            )
            return int(cursor.lastrowid)

    def upsert_author(self, record: Mapping[str, Any]) -> int | None:
        self.last_author_action = None
        author = record.get("author")
        if not isinstance(author, Mapping):
            return None
        platform = _string_or_none(record.get("platform"))
        name = _string_or_none(author.get("name"))
        handle = _string_or_none(author.get("handle"))
        profile_url = _string_or_none(author.get("profile_url"))
        author_type = _string_or_none(author.get("type")) or "unknown"
        if platform is None or not any((name, handle, profile_url)):
            return None

        with self.connection.cursor() as cursor:
            existing_id = self._find_author_id(
                cursor,
                platform=platform,
                name=name,
                handle=handle,
                profile_url=profile_url,
                author_type=author_type,
            )
            if existing_id is not None:
                cursor.execute(
                    """
                    UPDATE authors
                    SET name=%s, handle=%s, profile_url=%s, type=%s, raw_json=%s
                    WHERE id=%s
                    """,
                    (name, handle, profile_url, author_type, _json(author), existing_id),
                )
                self.last_author_action = "updated"
                return existing_id
            cursor.execute(
                """
                INSERT INTO authors (platform, name, handle, profile_url, type, raw_json)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (platform, name, handle, profile_url, author_type, _json(author)),
            )
            self.last_author_action = "inserted"
            return int(cursor.lastrowid)

    @staticmethod
    def _find_author_id(
        cursor: Any,
        *,
        platform: str,
        name: str | None,
        handle: str | None,
        profile_url: str | None,
        author_type: str,
    ) -> int | None:
        if profile_url:
            cursor.execute(
                "SELECT id FROM authors WHERE platform=%s AND profile_url=%s LIMIT 1",
                (platform, profile_url),
            )
        elif handle:
            cursor.execute(
                "SELECT id FROM authors WHERE platform=%s AND handle=%s LIMIT 1",
                (platform, handle),
            )
        else:
            cursor.execute(
                """
                SELECT id FROM authors
                WHERE platform=%s AND name=%s AND type=%s AND profile_url IS NULL
                LIMIT 1
                """,
                (platform, name, author_type),
            )
        row = cursor.fetchone()
        if row is None:
            return None
        if isinstance(row, Mapping):
            return int(row["id"])
        return int(row[0])

    def upsert_post(
        self,
        record: Mapping[str, Any],
        author_id: int | None,
        run_id: int | None,
    ) -> int:
        self.last_post_action = None
        platform = _string_or_none(record.get("platform"))
        post_id = _string_or_none(record.get("post_id"))
        if platform is None or post_id is None:
            raise MySQLStoreError("A normalized post requires platform and post_id.")
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM posts WHERE platform=%s AND post_id=%s LIMIT 1",
                (platform, post_id),
            )
            existing_row = cursor.fetchone()
            existing_id = _row_id(existing_row)
            cursor.execute(
                """
                INSERT INTO posts (
                    platform, post_id, input_url, canonical_url, author_id,
                    collection_run_id, content, publish_time, comment_count,
                    like_count, share_count, repost_count, view_count,
                    collection_status, error, collected_at, platform_metrics_json,
                    platform_context_json, raw_json
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON DUPLICATE KEY UPDATE
                    id=LAST_INSERT_ID(id),
                    input_url=VALUES(input_url),
                    canonical_url=VALUES(canonical_url),
                    author_id=VALUES(author_id),
                    collection_run_id=VALUES(collection_run_id),
                    content=VALUES(content),
                    publish_time=VALUES(publish_time),
                    comment_count=VALUES(comment_count),
                    like_count=VALUES(like_count),
                    share_count=VALUES(share_count),
                    repost_count=VALUES(repost_count),
                    view_count=VALUES(view_count),
                    collection_status=VALUES(collection_status),
                    error=VALUES(error),
                    collected_at=VALUES(collected_at),
                    platform_metrics_json=VALUES(platform_metrics_json),
                    platform_context_json=VALUES(platform_context_json),
                    raw_json=VALUES(raw_json)
                """,
                (
                    platform,
                    post_id,
                    _string_or_none(record.get("input_url")),
                    _string_or_none(record.get("canonical_url")),
                    author_id,
                    run_id,
                    _string_or_none(record.get("content")),
                    _mysql_datetime(record.get("publish_time")),
                    _count_or_none(record.get("comment_count")),
                    _count_or_none(record.get("like_count")),
                    _count_or_none(record.get("share_count")),
                    _count_or_none(record.get("repost_count")),
                    _count_or_none(record.get("view_count")),
                    _string_or_none(record.get("collection_status")),
                    _string_or_none(record.get("error")),
                    _mysql_datetime(record.get("collected_at")),
                    _json_or_none(record.get("platform_metrics")),
                    _json_or_none(record.get("platform_context")),
                    _json(record),
                ),
            )
            self.last_post_action = "updated" if existing_id is not None else "inserted"
            return existing_id if existing_id is not None else int(cursor.lastrowid)

    def insert_post_keyword(self, post_db_id: int, keyword: str) -> bool:
        self.last_keyword_action = None
        normalized = keyword.strip()
        if not normalized:
            return False
        with self.connection.cursor() as cursor:
            affected = cursor.execute(
                "INSERT IGNORE INTO post_keywords (post_id, keyword) VALUES (%s, %s)",
                (post_db_id, normalized),
            )
        inserted = bool(affected)
        self.last_keyword_action = "inserted" if inserted else "ignored"
        return inserted


def init_db(
    config: MySQLConfig | None = None,
    *,
    connection: Any | None = None,
) -> None:
    owns_connection = connection is None
    selected_connection = connection or connect_mysql(config)
    try:
        MySQLStore(selected_connection).init_db()
    finally:
        if owns_connection:
            selected_connection.close()


def import_jsonl_to_mysql(
    jsonl_path: str | Path,
    *,
    run_metadata: Mapping[str, Any] | None = None,
    keywords: list[str] | None = None,
    config: MySQLConfig | None = None,
    connection: Any | None = None,
) -> dict[str, int]:
    path = Path(jsonl_path)
    if not path.is_file():
        raise MySQLStoreError(f"JSONL input file does not exist: {path}")
    owns_connection = connection is None
    selected_connection = connection or connect_mysql(config)
    store = MySQLStore(selected_connection)
    metadata = dict(run_metadata or {})
    imported_count = 0
    records_read = 0
    failed_count = 0
    posts_inserted = 0
    posts_updated = 0
    authors_inserted = 0
    authors_updated = 0
    keyword_links_inserted = 0
    keyword_links_ignored = 0
    run_id = 0
    try:
        store.init_db()
        run_id = store.insert_collection_run(
            started_at=metadata.get("started_at"),
            finished_at=metadata.get("finished_at"),
            keywords=keywords or metadata.get("keywords"),
            platform_order=metadata.get("platform_order"),
            global_limit=metadata.get("global_limit"),
            output_path=path,
            summary=metadata.get("summary"),
        )
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            records_read += 1
            try:
                loaded = json.loads(line)
                if not isinstance(loaded, Mapping):
                    raise MySQLStoreError(
                        f"Line {line_number} is not a JSON object."
                    )
                record = dict(loaded)
                if record.get("schema_version") is None:
                    record = normalize_social_post(record)
                _validate_normalized_record(record)
                author_id = store.upsert_author(record)
                if store.last_author_action == "inserted":
                    authors_inserted += 1
                elif store.last_author_action == "updated":
                    authors_updated += 1
                post_db_id = store.upsert_post(record, author_id, run_id)
                if store.last_post_action == "inserted":
                    posts_inserted += 1
                else:
                    posts_updated += 1
                for keyword in keywords or metadata.get("keywords") or []:
                    if store.insert_post_keyword(post_db_id, str(keyword)):
                        keyword_links_inserted += 1
                    elif str(keyword).strip():
                        keyword_links_ignored += 1
                imported_count += 1
            except (json.JSONDecodeError, ValueError, MySQLStoreError):
                failed_count += 1
        selected_connection.commit()
    except Exception:
        selected_connection.rollback()
        raise
    finally:
        if owns_connection:
            selected_connection.close()
    return {
        "run_id": run_id,
        "records_read": records_read,
        "imported_count": imported_count,
        "failed_count": failed_count,
        "posts_inserted": posts_inserted,
        "posts_updated": posts_updated,
        "authors_inserted": authors_inserted,
        "authors_updated": authors_updated,
        "keyword_links_inserted": keyword_links_inserted,
        "keyword_links_ignored": keyword_links_ignored,
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _json_or_none(value: Any) -> str | None:
    return None if value is None else _json(value)


def _string_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _count_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) and value >= 0 else None


def _mysql_datetime(value: Any) -> datetime | date | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        if len(text) == 10:
            return date.fromisoformat(text)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def _row_id(row: Any) -> int | None:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return int(row["id"])
    return int(row[0])


def _validate_normalized_record(record: Mapping[str, Any]) -> None:
    platform = _string_or_none(record.get("platform"))
    post_id = _string_or_none(record.get("post_id"))
    if platform not in {"x", "facebook"}:
        raise MySQLStoreError("A normalized post requires platform 'x' or 'facebook'.")
    if post_id is None:
        raise MySQLStoreError("A normalized post requires post_id.")
