from __future__ import annotations

import sqlite3


def ensure_template_reagent_schema(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(qc_project_templates)")}
    if "default_reagent_id" not in columns:
        connection.execute(
            "ALTER TABLE qc_project_templates ADD COLUMN default_reagent_id INTEGER "
            "REFERENCES md_reagents (id) ON DELETE RESTRICT"
        )
    # Existing per-item choices remain authoritative; do not guess a shared reagent.
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (migration_key, app_version) VALUES (?, 'V1.2')",
        ("v1_2_template_reagent_001",),
    )
