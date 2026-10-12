"""Immutable complete report bundles stored with the report's SQLite transaction."""
import sqlite3


def ensure_report_archives_schema(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE IF NOT EXISTS qc_report_archive_files (
        export_id INTEGER PRIMARY KEY REFERENCES report_exports(id) ON DELETE RESTRICT,
        request_key TEXT NOT NULL UNIQUE,
        request_digest TEXT NOT NULL,
        archive_name TEXT NOT NULL,
        archive_bytes BLOB NOT NULL CHECK(length(archive_bytes)>0),
        sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL CHECK(size_bytes>0),
        manifest_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )''')
