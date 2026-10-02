"""Immutable event reports and downloadable monthly PDF archives."""
import sqlite3


def ensure_out_of_control_reports_schema(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS qc_event_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_uid TEXT NOT NULL UNIQUE,
        event_id INTEGER NOT NULL,
        revision_no INTEGER NOT NULL,
        report_no TEXT NOT NULL UNIQUE,
        generated_at TEXT NOT NULL,
        file_name TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        package_json TEXT NOT NULL,
        pdf_bytes BLOB NOT NULL CHECK(length(pdf_bytes)>0),
        UNIQUE(event_id,revision_no),
        FOREIGN KEY(event_id,revision_no)
            REFERENCES qc_ooc_revisions(event_id,revision_no) ON DELETE RESTRICT
    )""")
    connection.execute("""CREATE INDEX IF NOT EXISTS idx_event_reports_revision
        ON qc_event_reports(event_id,revision_no,id)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS report_export_files (
        export_id INTEGER PRIMARY KEY REFERENCES report_exports(id) ON DELETE RESTRICT,
        pdf_bytes BLOB NOT NULL CHECK(length(pdf_bytes)>0),
        sha256 TEXT NOT NULL
    )""")
