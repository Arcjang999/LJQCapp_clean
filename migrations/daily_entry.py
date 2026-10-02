"""Atomic daily-entry receipts. Result identities remain in the existing method tables."""


def ensure_daily_entry_schema(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS qc_daily_submissions (
        submission_id TEXT PRIMARY KEY,
        payload_hash TEXT NOT NULL,
        request_json TEXT NOT NULL,
        receipt_json TEXT NOT NULL,
        saved_at TEXT NOT NULL,
        operator TEXT NOT NULL
    )''')
    connection.execute('''CREATE TABLE IF NOT EXISTS qc_daily_submission_items (
        submission_id TEXT NOT NULL REFERENCES qc_daily_submissions(submission_id) ON DELETE RESTRICT,
        row_key TEXT NOT NULL,
        lot_config_item_id INTEGER NOT NULL REFERENCES qc_lot_config_items(id) ON DELETE RESTRICT,
        qc_method TEXT NOT NULL CHECK(qc_method IN ('lj','zscore','instant')),
        result_id INTEGER NOT NULL,
        context_id INTEGER NOT NULL REFERENCES qc_result_contexts(id) ON DELETE RESTRICT,
        evaluation_id INTEGER NOT NULL REFERENCES qc_result_evaluations(id) ON DELETE RESTRICT,
        PRIMARY KEY(submission_id,row_key),
        UNIQUE(qc_method,result_id)
    )''')
