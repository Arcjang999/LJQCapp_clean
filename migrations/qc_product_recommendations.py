"""Confirmed, advisory product applicability and the basis of local choices."""


def ensure_qc_product_recommendation_schema(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS qc_product_recommendations (
        id INTEGER PRIMARY KEY, reagent_id INTEGER NOT NULL REFERENCES md_reagents(id),
        qc_material_id INTEGER NOT NULL REFERENCES md_qc_materials(id),
        lab_instrument_id INTEGER REFERENCES lab_instruments(id),
        test_item_id INTEGER REFERENCES md_test_items(id), method_id INTEGER REFERENCES md_methods(id),
        scope_note TEXT NOT NULL, evidence TEXT NOT NULL, confirmed_by TEXT NOT NULL,
        confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, revision_no INTEGER NOT NULL DEFAULT 1,
        is_disabled INTEGER NOT NULL DEFAULT 0, disabled_reason TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    connection.execute('''CREATE UNIQUE INDEX IF NOT EXISTS qc_product_recommendation_scope
        ON qc_product_recommendations(reagent_id,qc_material_id,COALESCE(lab_instrument_id,0),
        COALESCE(test_item_id,0),COALESCE(method_id,0))''')
    connection.execute('''CREATE TABLE IF NOT EXISTS qc_product_recommendation_history (
        id INTEGER PRIMARY KEY, relation_id INTEGER NOT NULL REFERENCES qc_product_recommendations(id),
        action TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
        changed_by TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    for table in ('qc_project_templates', 'qc_project_template_items', 'qc_lot_config_items'):
        columns = {row[1] for row in connection.execute('PRAGMA table_info(' + table + ')')}
        if 'product_choice_review_json' not in columns:
            connection.execute('ALTER TABLE ' + table + " ADD COLUMN product_choice_review_json TEXT NOT NULL DEFAULT '{}'")
    connection.execute("INSERT OR IGNORE INTO schema_migrations(migration_key,app_version) VALUES ('qc_product_recommendations_001','V1.3')")
