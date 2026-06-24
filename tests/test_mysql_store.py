from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from src.storage.mysql_store import (
    SCHEMA_STATEMENTS,
    MySQLConfig,
    MySQLStoreError,
    import_jsonl_to_mysql,
)


class _FakeConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple | None]] = []
        self.authors: dict[tuple, int] = {}
        self.posts: dict[tuple[str, str], dict[str, object]] = {}
        self.keywords: set[tuple[int, str]] = set()
        self.run_count = 0
        self.author_count = 0
        self.post_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        self.closed = True


class _FakeCursor:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection
        self.lastrowid = 0
        self._fetchone = None

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple | None = None) -> int:
        compact = " ".join(sql.split())
        self.connection.statements.append((compact, params))
        if compact.startswith("CREATE TABLE"):
            return 0
        if compact.startswith("INSERT INTO collection_runs"):
            self.connection.run_count += 1
            self.lastrowid = self.connection.run_count
            return 1
        if compact.startswith("SELECT id FROM authors"):
            if "profile_url=%s" in compact:
                key = ("profile", params[0], params[1])
            elif "handle=%s" in compact:
                key = ("handle", params[0], params[1])
            else:
                key = ("name", params[0], params[1], params[2])
            author_id = self.connection.authors.get(key)
            self._fetchone = None if author_id is None else (author_id,)
            return 0
        if compact.startswith("INSERT INTO authors"):
            self.connection.author_count += 1
            self.lastrowid = self.connection.author_count
            platform, name, handle, profile_url, author_type, _raw = params
            if profile_url:
                key = ("profile", platform, profile_url)
            elif handle:
                key = ("handle", platform, handle)
            else:
                key = ("name", platform, name, author_type)
            self.connection.authors[key] = self.lastrowid
            return 1
        if compact.startswith("UPDATE authors"):
            return 1
        if compact.startswith("INSERT INTO posts"):
            platform, post_id = params[0], params[1]
            key = (platform, post_id)
            if key not in self.connection.posts:
                self.connection.post_count += 1
                self.lastrowid = self.connection.post_count
            else:
                self.lastrowid = int(self.connection.posts[key]["id"])
            self.connection.posts[key] = {
                "id": self.lastrowid,
                "comment_count": params[8],
                "like_count": params[9],
                "share_count": params[10],
                "repost_count": params[11],
                "view_count": params[12],
                "raw_json": params[18],
            }
            return 1
        if compact.startswith("INSERT IGNORE INTO post_keywords"):
            self.connection.keywords.add((params[0], params[1]))
            return 1
        raise AssertionError(f"Unexpected SQL: {compact}")

    def fetchone(self):
        return self._fetchone


class MySQLStoreTests(unittest.TestCase):
    def test_schema_uses_mysql_utf8mb4_and_required_unique_constraints(self) -> None:
        schema = "\n".join(SCHEMA_STATEMENTS)
        self.assertIn("DEFAULT CHARSET=utf8mb4", schema)
        self.assertIn("uq_posts_platform_post_id", schema)
        self.assertIn("uq_authors_platform_profile_url", schema)
        self.assertIn("uq_post_keywords_post_keyword", schema)

    def test_imports_normalized_x_and_facebook_records(self) -> None:
        connection = _FakeConnection()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "posts.jsonl"
            path.write_text(
                "\n".join(
                    json.dumps(record)
                    for record in (_normalized_x_record(), _normalized_facebook_record())
                ),
                encoding="utf-8",
            )
            summary = import_jsonl_to_mysql(
                path,
                connection=connection,
                keywords=["camera"],
            )

        self.assertEqual(summary["imported_count"], 2)
        self.assertEqual(len(connection.posts), 2)
        x_post = connection.posts[("x", "123")]
        facebook_post = connection.posts[("facebook", "456")]
        self.assertEqual(x_post["repost_count"], 4)
        self.assertEqual(x_post["view_count"], 100)
        self.assertIsNone(facebook_post["repost_count"])
        self.assertIsNone(facebook_post["view_count"])

    def test_duplicate_post_is_upserted_and_keyword_is_not_duplicated(self) -> None:
        connection = _FakeConnection()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "posts.jsonl"
            record = _normalized_x_record()
            path.write_text(json.dumps(record), encoding="utf-8")
            import_jsonl_to_mysql(path, connection=connection, keywords=["camera"])
            record["like_count"] = 99
            path.write_text(json.dumps(record), encoding="utf-8")
            import_jsonl_to_mysql(path, connection=connection, keywords=["camera"])

        self.assertEqual(len(connection.posts), 1)
        self.assertEqual(connection.posts[("x", "123")]["like_count"], 99)
        self.assertEqual(connection.keywords, {(1, "camera")})
        post_sql = next(
            sql for sql, _params in connection.statements
            if sql.startswith("INSERT INTO posts")
        )
        self.assertIn("ON DUPLICATE KEY UPDATE", post_sql)

    def test_missing_optional_metrics_are_written_as_null(self) -> None:
        connection = _FakeConnection()
        record = _normalized_facebook_record()
        for field in ("comment_count", "like_count", "share_count"):
            record[field] = None
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "posts.jsonl"
            path.write_text(json.dumps(record), encoding="utf-8")
            import_jsonl_to_mysql(path, connection=connection)

        stored = connection.posts[("facebook", "456")]
        self.assertIsNone(stored["comment_count"])
        self.assertIsNone(stored["like_count"])
        self.assertIsNone(stored["share_count"])

    def test_missing_configuration_names_variables_without_printing_password(self) -> None:
        secret = "do-not-print-this-password"
        with self.assertRaises(MySQLStoreError) as caught:
            MySQLConfig.from_env({"SOCIAL_DB_PASSWORD": secret})
        message = str(caught.exception)
        self.assertIn("SOCIAL_DB_HOST", message)
        self.assertNotIn(secret, message)

    def test_cli_recognizes_database_modes(self) -> None:
        with patch.object(sys, "argv", ["main.py", "--init-db", "--db", "mysql"]):
            init_args = main.parse_args()
        with patch.object(
            sys,
            "argv",
            ["main.py", "--import-jsonl", "posts.jsonl", "--db", "mysql"],
        ):
            import_args = main.parse_args()

        self.assertTrue(init_args.init_db)
        self.assertEqual(import_args.import_jsonl, Path("posts.jsonl"))

    def test_collect_save_db_imports_the_generated_jsonl(self) -> None:
        collection_result = {
            "started_at": "2026-06-24T00:00:00+00:00",
            "finished_at": "2026-06-24T00:01:00+00:00",
            "keywords": ["camera"],
            "platform_order": ["x", "facebook"],
            "global_limit": 2,
            "output_path": Path("output/social.jsonl"),
            "summary": {"total_processed": 1},
        }
        with (
            patch.object(
                sys,
                "argv",
                ["main.py", "--collect", "camera", "--db", "mysql", "--save-db"],
            ),
            patch("main.run_social_collect", return_value=collection_result),
            patch("main.run_mysql_import") as importer,
        ):
            main.main()

        importer.assert_called_once_with(
            Path("output/social.jsonl"),
            run_metadata=collection_result,
            keywords=["camera"],
        )

    def test_mysql_cli_error_does_not_echo_password(self) -> None:
        secret = "very-secret-password"
        output = io.StringIO()
        with (
            patch.dict(
                "os.environ",
                {
                    "SOCIAL_DB_PASSWORD": secret,
                    "SOCIAL_DB_HOST": "",
                    "SOCIAL_DB_NAME": "",
                    "SOCIAL_DB_USER": "",
                },
                clear=True,
            ),
            patch.object(sys, "argv", ["main.py", "--init-db", "--db", "mysql"]),
            patch("sys.stdout", output),
        ):
            with self.assertRaises(SystemExit) as caught:
                main.main()

        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, output.getvalue())


def _normalized_x_record() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "platform": "x",
        "post_id": "123",
        "input_url": "https://x.com/openai/status/123",
        "canonical_url": "https://x.com/openai/status/123",
        "author": {
            "name": "OpenAI",
            "handle": "@OpenAI",
            "profile_url": "https://x.com/OpenAI",
            "type": "user",
        },
        "content": "X post",
        "publish_time": "2026-06-24T00:00:00Z",
        "comment_count": 2,
        "like_count": 3,
        "share_count": None,
        "repost_count": 4,
        "view_count": 100,
        "comments": [],
        "collected_at": "2026-06-24T00:01:00Z",
        "collection_status": "success",
        "error": None,
        "platform_metrics": {"reply_count": 2},
        "platform_context": {},
    }


def _normalized_facebook_record() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "platform": "facebook",
        "post_id": "456",
        "input_url": "https://www.facebook.com/example/posts/456",
        "canonical_url": "https://www.facebook.com/example/posts/456",
        "author": {
            "name": "Example",
            "handle": None,
            "profile_url": "https://www.facebook.com/example",
            "type": "page",
        },
        "content": "Facebook post",
        "publish_time": "2026-06-24T00:00:00Z",
        "comment_count": 5,
        "like_count": 6,
        "share_count": 7,
        "repost_count": None,
        "view_count": None,
        "comments": [],
        "collected_at": "2026-06-24T00:01:00Z",
        "collection_status": "success",
        "error": None,
        "platform_metrics": {},
        "platform_context": {},
    }


if __name__ == "__main__":
    unittest.main()
