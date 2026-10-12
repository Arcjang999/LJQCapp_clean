"""Receipts for an all-or-nothing project, item and physical-batch setup."""


def ensure_quick_project_setup_schema(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS qc_quick_setup_receipts (
        request_key TEXT PRIMARY KEY,
        payload_sha256 TEXT NOT NULL,
        preview_hash TEXT NOT NULL,
        template_id INTEGER NOT NULL REFERENCES qc_project_templates(id),
        template_item_id INTEGER NOT NULL REFERENCES qc_project_template_items(id),
        lot_config_id INTEGER NOT NULL REFERENCES qc_lot_configs(id),
        lot_config_item_id INTEGER NOT NULL REFERENCES qc_lot_config_items(id),
        receipt_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )''')
