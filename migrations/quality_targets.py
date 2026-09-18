"""Additive quality-target storage; no legacy CV/result backfill."""
import json
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[1] / 'resources' / 'quality_targets_2024.json'


def ensure_quality_target_schema(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS qc_quality_catalog (
        id TEXT PRIMARY KEY, origin TEXT NOT NULL, payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    for table in ('qc_project_template_items', 'qc_lot_config_items'):
        columns = {r[1] for r in connection.execute(f'PRAGMA table_info({table})')}
        if 'quality_goal_json' not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN quality_goal_json TEXT NOT NULL DEFAULT '{{}}'")
    for record in json.loads(CATALOG_PATH.read_text(encoding='utf-8'))['requirements']:
        connection.execute('INSERT OR IGNORE INTO qc_quality_catalog(id,origin,payload_json) VALUES (?,?,?)',
            (record['id'], 'builtin', json.dumps(record, ensure_ascii=False, allow_nan=False)))
    connection.execute("INSERT OR IGNORE INTO schema_migrations(migration_key,app_version) VALUES ('quality_targets_2024_001','V1.3')")
