"""Add reusable concentration specifications without rewriting historical levels."""
import sqlite3


def ensure_material_workflow_schema(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE IF NOT EXISTS md_qc_material_specs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid TEXT NOT NULL UNIQUE,
        qc_material_id INTEGER NOT NULL REFERENCES md_qc_materials(id) ON DELETE RESTRICT,
        level_name TEXT NOT NULL,
        level_code TEXT NOT NULL DEFAULT '',
        catalog_no TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(qc_material_id,level_name,level_code)
    )''')
    additions = {
        'qc_result_context_levels': {'level_code': "TEXT NOT NULL DEFAULT ''", 'expiry_date': "TEXT NOT NULL DEFAULT ''"},
        'md_qc_levels': {'specification_id': 'INTEGER REFERENCES md_qc_material_specs(id) ON DELETE RESTRICT'},
        'qc_project_templates': {
            'project_group': "TEXT NOT NULL DEFAULT ''",
            'default_qc_method': "TEXT NOT NULL DEFAULT 'lj' CHECK(default_qc_method IN ('lj','zscore','instant'))",
            'default_method_id': 'INTEGER REFERENCES md_methods(id) ON DELETE RESTRICT',
            'default_level_count': 'INTEGER NOT NULL DEFAULT 1 CHECK(default_level_count BETWEEN 1 AND 3)',
        },
        'qc_lot_configs': {'material_selection_mode': 'INTEGER NOT NULL DEFAULT 0 CHECK(material_selection_mode IN (0,1))'},
    }
    for table, fields in additions.items():
        existing = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
        for name, definition in fields.items():
            if name not in existing:
                connection.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
    connection.execute("INSERT OR IGNORE INTO schema_migrations(migration_key,app_version) VALUES ('material_workflow_001','V1.2')")
