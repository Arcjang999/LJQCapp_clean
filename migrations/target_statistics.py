"""Explicit statistics preferences and frozen sources of parameter revisions."""


def ensure_target_statistics_schema(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS qc_target_statistics_policies (
        id INTEGER PRIMARY KEY,
        project_item_id INTEGER UNIQUE REFERENCES qc_project_template_items(id) ON DELETE CASCADE,
        lot_item_id INTEGER UNIQUE REFERENCES qc_lot_config_items(id) ON DELETE CASCADE,
        mode TEXT NOT NULL CHECK(mode IN ('monthly','cumulative')),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),
        confirmed_by TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK((project_item_id IS NULL) != (lot_item_id IS NULL))
    )''')
    columns = {row[1] for row in connection.execute('PRAGMA table_info(qc_target_profiles)')}
    if 'source_statistics_json' not in columns:
        connection.execute("ALTER TABLE qc_target_profiles ADD COLUMN source_statistics_json TEXT NOT NULL DEFAULT '{}'")
