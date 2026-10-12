"""Persist one receipt for each confirmed unit-preparation file."""


def ensure_lab_preparation_schema(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS qc_lab_preparation_receipts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_key TEXT NOT NULL UNIQUE,
        file_sha256 TEXT NOT NULL UNIQUE,
        preview_hash TEXT NOT NULL,
        before_settings_json TEXT NOT NULL,
        after_settings_json TEXT NOT NULL,
        receipt_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )''')
