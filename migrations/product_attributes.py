"""Add editable product concentration text without changing imported sources."""
import sqlite3


def ensure_product_attributes_schema(connection: sqlite3.Connection) -> None:
    existing = {row[1] for row in connection.execute('PRAGMA table_info(md_qc_materials)')}
    for field in ('concentration_code', 'concentration'):
        if field not in existing:
            connection.execute(f"ALTER TABLE md_qc_materials ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
    connection.execute("INSERT OR IGNORE INTO schema_migrations(migration_key,app_version) VALUES ('product_attributes_001','V1.3')")
