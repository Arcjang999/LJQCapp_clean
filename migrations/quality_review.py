"""Add explicit applicability reviews without changing adopted goals or historical results."""


def ensure_quality_review_schema(connection):
    for table in ('qc_project_template_items', 'qc_lot_config_items'):
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
        if 'quality_review_json' not in columns:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN quality_review_json TEXT NOT NULL DEFAULT '{{}}'"
            )
    connection.execute("""INSERT OR IGNORE INTO schema_migrations(migration_key,app_version)
        VALUES ('quality_applicability_review_001','V1.3')""")
