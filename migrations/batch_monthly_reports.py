"""Independent report jobs with compatible dates for existing monthly archives."""
def ensure_batch_monthly_reports_schema(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS qc_monthly_report_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT NOT NULL UNIQUE,
        request_digest TEXT NOT NULL,
        report_month TEXT NOT NULL,
        period_start TEXT NOT NULL DEFAULT '',
        period_end TEXT NOT NULL DEFAULT '',
        scope_json TEXT NOT NULL,
        exclusions_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )''')
    connection.execute('''CREATE TABLE IF NOT EXISTS qc_monthly_report_job_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL REFERENCES qc_monthly_report_jobs(id) ON DELETE RESTRICT,
        unit_key TEXT NOT NULL,
        qc_method TEXT NOT NULL CHECK(qc_method IN ('lj','zscore')),
        batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE RESTRICT,
        report_month TEXT NOT NULL,
        period_start TEXT NOT NULL DEFAULT '',
        period_end TEXT NOT NULL DEFAULT '',
        source_fingerprint TEXT NOT NULL,
        selection_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','generating','succeeded','failed')),
        error_message TEXT NOT NULL DEFAULT '',
        attempts INTEGER NOT NULL DEFAULT 0,
        export_id INTEGER UNIQUE REFERENCES report_exports(id) ON DELETE RESTRICT,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(job_id,unit_key),
        CHECK(status!='succeeded' OR export_id IS NOT NULL)
    )''')
    connection.execute('CREATE INDEX IF NOT EXISTS idx_monthly_job_status ON qc_monthly_report_job_items(job_id,status,id)')
    for table in ('qc_monthly_report_jobs', 'qc_monthly_report_job_items'):
        columns = {row[1] for row in connection.execute('PRAGMA table_info(' + table + ')')}
        for name in ('period_start', 'period_end'):
            if name not in columns:
                connection.execute('ALTER TABLE ' + table + ' ADD COLUMN ' + name + " TEXT NOT NULL DEFAULT ''")
        # Only existing natural months can be inferred. Custom reports always
        # supply explicit dates and never use a synthetic month marker.
        connection.execute('UPDATE ' + table + " SET period_start=report_month||'-01',"
            "period_end=date(report_month||'-01','+1 month','-1 day') "
            "WHERE period_start='' AND report_month GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'")
