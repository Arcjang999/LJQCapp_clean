"""Append-only handling records, independent of the original QC results."""
import sqlite3


def ensure_out_of_control_schema(connection: sqlite3.Connection) -> None:
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS qc_ooc_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_type TEXT NOT NULL CHECK(source_type IN ('lj_result','zscore_run')),
        source_id INTEGER NOT NULL CHECK(source_id > 0),
        qc_method TEXT NOT NULL CHECK(qc_method IN ('lj','zscore')),
        record_type TEXT NOT NULL DEFAULT 'routine' CHECK(record_type='routine'),
        origin_context_id INTEGER REFERENCES qc_result_contexts(id) ON DELETE RESTRICT,
        origin_evaluation_id INTEGER REFERENCES qc_result_evaluations(id) ON DELETE RESTRICT,
        original_classification TEXT NOT NULL CHECK(original_classification IN ('reject','warning')),
        origin_snapshot_json TEXT NOT NULL,
        current_revision_no INTEGER NOT NULL DEFAULT 1,
        opened_by TEXT NOT NULL,
        opened_at TEXT NOT NULL,
        UNIQUE(source_type,source_id)
    );
    CREATE TABLE IF NOT EXISTS qc_ooc_revisions (
        event_id INTEGER NOT NULL REFERENCES qc_ooc_events(id) ON DELETE RESTRICT,
        revision_no INTEGER NOT NULL,
        previous_revision_no INTEGER,
        status TEXT NOT NULL CHECK(status IN ('pending','in_progress','pending_confirmation','completed')),
        action TEXT NOT NULL,
        content_json TEXT NOT NULL,
        saved_by TEXT NOT NULL,
        saved_at TEXT NOT NULL,
        change_reason TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(event_id,revision_no),
        FOREIGN KEY(event_id,previous_revision_no) REFERENCES qc_ooc_revisions(event_id,revision_no)
    );
    CREATE TABLE IF NOT EXISTS qc_ooc_status_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id INTEGER NOT NULL,
        revision_no INTEGER NOT NULL,
        from_status TEXT,
        to_status TEXT NOT NULL,
        actor_text TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        FOREIGN KEY(event_id,revision_no) REFERENCES qc_ooc_revisions(event_id,revision_no)
    );
    CREATE TABLE IF NOT EXISTS qc_ooc_retests (
        event_id INTEGER NOT NULL,
        revision_no INTEGER NOT NULL,
        source_type TEXT NOT NULL,
        source_id INTEGER NOT NULL,
        origin_context_id INTEGER REFERENCES qc_result_contexts(id),
        origin_evaluation_id INTEGER REFERENCES qc_result_evaluations(id),
        difference_reason TEXT NOT NULL DEFAULT '',
        snapshot_json TEXT NOT NULL,
        differences_json TEXT NOT NULL,
        PRIMARY KEY(event_id,revision_no,source_type,source_id),
        FOREIGN KEY(event_id,revision_no) REFERENCES qc_ooc_revisions(event_id,revision_no)
    );
    CREATE TABLE IF NOT EXISTS qc_ooc_requests (
        request_id TEXT PRIMARY KEY,
        payload_digest TEXT NOT NULL,
        event_id INTEGER NOT NULL,
        revision_no INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(event_id,revision_no) REFERENCES qc_ooc_revisions(event_id,revision_no)
    );
    CREATE INDEX IF NOT EXISTS idx_ooc_status ON qc_ooc_revisions(status,event_id,revision_no);
    INSERT OR IGNORE INTO schema_migrations(migration_key,app_version)
        VALUES('out_of_control_001','V1.3');
    """)
