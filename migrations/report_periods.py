"""Persist explicit periods without replacing previously archived reports."""


def ensure_report_periods_schema(connection):
    existing = {row[1] for row in connection.execute('PRAGMA table_info(report_exports)')}
    for name in ('period_start', 'period_end'):
        if name not in existing:
            connection.execute(f"ALTER TABLE report_exports ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
    connection.execute('CREATE INDEX IF NOT EXISTS idx_report_export_period ON report_exports(period_start,period_end,id)')
