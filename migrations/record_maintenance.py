"""Append-only evidence for workbench record edits and inclusion decisions."""


def ensure_record_maintenance_schema(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS qc_record_maintenance_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_key TEXT NOT NULL UNIQUE,
        qc_method TEXT NOT NULL CHECK(qc_method IN ('lj','zscore','instant')),
        batch_id INTEGER NOT NULL,
        record_id INTEGER NOT NULL,
        action TEXT NOT NULL CHECK(action IN ('edit','keep','disable','restore')),
        reason TEXT NOT NULL CHECK(length(trim(reason))>0),
        before_json TEXT NOT NULL,
        after_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )''')
    connection.execute('''CREATE INDEX IF NOT EXISTS idx_record_maintenance_source
        ON qc_record_maintenance_events(qc_method,record_id,id)''')
