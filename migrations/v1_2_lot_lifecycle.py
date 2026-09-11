"""Additive lot provenance schema; original result tables and IDs remain intact."""
import sqlite3


def ensure_lot_lifecycle_schema(connection: sqlite3.Connection) -> None:
    columns={row[1] for row in connection.execute('PRAGMA table_info(qc_lot_configs)')}
    if 'combination_key' not in columns:
        connection.execute("ALTER TABLE qc_lot_configs ADD COLUMN combination_key TEXT NOT NULL DEFAULT ''")
        connection.execute('DROP INDEX IF EXISTS idx_qc_lot_config_active')
        connection.execute("CREATE UNIQUE INDEX idx_qc_lot_config_active ON qc_lot_configs(template_id,qc_material_lot_id,combination_key) WHERE is_disabled=0")
    connection.executescript('''
    CREATE TABLE IF NOT EXISTS qc_level_combination_members (
        lot_config_item_id INTEGER NOT NULL REFERENCES qc_lot_config_items(id) ON DELETE RESTRICT,
        qc_level_id INTEGER NOT NULL REFERENCES md_qc_levels(id) ON DELETE RESTRICT,
        source_profile_id INTEGER REFERENCES qc_target_profiles(id) ON DELETE RESTRICT,
        verification_id INTEGER REFERENCES qc_lot_verifications(id) ON DELETE RESTRICT,
        PRIMARY KEY(lot_config_item_id,qc_level_id)
    );
    CREATE TABLE IF NOT EXISTS qc_detection_systems (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_item_id INTEGER NOT NULL REFERENCES qc_project_template_items(id) ON DELETE RESTRICT,
        identity_json TEXT NOT NULL UNIQUE,
        snapshot_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS md_reagent_lots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reagent_id INTEGER NOT NULL REFERENCES md_reagents(id) ON DELETE RESTRICT,
        lot_no TEXT NOT NULL COLLATE NOCASE,
        expiry_date TEXT NOT NULL,
        source_text TEXT NOT NULL DEFAULT '',
        is_disabled INTEGER NOT NULL DEFAULT 0 CHECK(is_disabled IN (0,1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(reagent_id,lot_no)
    );
    CREATE TABLE IF NOT EXISTS qc_lot_verifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        system_id INTEGER NOT NULL REFERENCES qc_detection_systems(id) ON DELETE RESTRICT,
        template_item_id INTEGER NOT NULL REFERENCES qc_project_template_items(id) ON DELETE RESTRICT,
        reagent_lot_id INTEGER REFERENCES md_reagent_lots(id) ON DELETE RESTRICT,
        qc_lot_id INTEGER REFERENCES md_qc_material_lots(id) ON DELETE RESTRICT,
        conclusion TEXT NOT NULL CHECK(conclusion IN ('pass','fail')),
        evidence TEXT NOT NULL CHECK(length(trim(evidence))>0),
        confirmed_by TEXT NOT NULL CHECK(length(trim(confirmed_by))>0),
        confirmed_at TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK((reagent_lot_id IS NOT NULL)+(qc_lot_id IS NOT NULL)=1)
    );
    CREATE TABLE IF NOT EXISTS qc_lot_change_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        system_id INTEGER NOT NULL REFERENCES qc_detection_systems(id) ON DELETE RESTRICT,
        template_item_id INTEGER NOT NULL REFERENCES qc_project_template_items(id) ON DELETE RESTRICT,
        event_type TEXT NOT NULL CHECK(event_type IN ('reagent','qc','target','correction','ended','parallel','active')),
        previous_id INTEGER,
        next_id INTEGER,
        effective_at TEXT NOT NULL,
        reason TEXT NOT NULL CHECK(length(trim(reason))>0),
        operator TEXT NOT NULL CHECK(length(trim(operator))>0),
        verification_id INTEGER REFERENCES qc_lot_verifications(id) ON DELETE RESTRICT,
        corrects_event_id INTEGER REFERENCES qc_lot_change_events(id) ON DELETE RESTRICT,
        details_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS qc_reagent_lot_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        system_id INTEGER NOT NULL REFERENCES qc_detection_systems(id) ON DELETE RESTRICT,
        template_item_id INTEGER NOT NULL REFERENCES qc_project_template_items(id) ON DELETE RESTRICT,
        reagent_lot_id INTEGER NOT NULL REFERENCES md_reagent_lots(id) ON DELETE RESTRICT,
        effective_at TEXT NOT NULL,
        event_id INTEGER NOT NULL UNIQUE REFERENCES qc_lot_change_events(id) ON DELETE RESTRICT,
        verification_id INTEGER NOT NULL REFERENCES qc_lot_verifications(id) ON DELETE RESTRICT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS qc_config_item_lifecycle (
        lot_config_item_id INTEGER PRIMARY KEY REFERENCES qc_lot_config_items(id) ON DELETE RESTRICT,
        state TEXT NOT NULL CHECK(state IN ('parallel','active','ended')),
        effective_at TEXT NOT NULL,
        event_id INTEGER NOT NULL REFERENCES qc_lot_change_events(id) ON DELETE RESTRICT
    );
    CREATE TABLE IF NOT EXISTS qc_target_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        qc_method TEXT NOT NULL CHECK(qc_method IN ('lj','zscore')),
        batch_id INTEGER NOT NULL,
        version_no INTEGER NOT NULL,
        effective_at TEXT NOT NULL,
        source TEXT NOT NULL CHECK(source IN ('building','manual','manufacturer','revision')),
        levels_json TEXT NOT NULL,
        evidence TEXT NOT NULL,
        confirmed_by TEXT NOT NULL,
        source_result_ids_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(qc_method,batch_id,version_no)
    );
    CREATE TABLE IF NOT EXISTS qc_result_contexts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lj_result_id INTEGER UNIQUE REFERENCES results(id) ON DELETE RESTRICT,
        zscore_run_id INTEGER UNIQUE REFERENCES zscore_runs(id) ON DELETE RESTRICT,
        instant_result_id INTEGER UNIQUE REFERENCES instant_results(id) ON DELETE RESTRICT,
        config_snapshot_json TEXT NOT NULL,
        reagent_lot_id INTEGER REFERENCES md_reagent_lots(id) ON DELETE RESTRICT,
        reagent_lot_no TEXT NOT NULL DEFAULT '',
        reagent_expiry_date TEXT NOT NULL DEFAULT '',
        usage_id INTEGER REFERENCES qc_reagent_lot_usage(id) ON DELETE RESTRICT,
        target_profile_id INTEGER REFERENCES qc_target_profiles(id) ON DELETE RESTRICT,
        provenance TEXT NOT NULL CHECK(provenance IN ('recorded','migration_snapshot','migration_available','instant_transfer')),
        source_context_id INTEGER REFERENCES qc_result_contexts(id) ON DELETE RESTRICT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK((lj_result_id IS NOT NULL)+(zscore_run_id IS NOT NULL)+(instant_result_id IS NOT NULL)=1)
    );
    CREATE TABLE IF NOT EXISTS qc_result_context_levels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        context_id INTEGER NOT NULL REFERENCES qc_result_contexts(id) ON DELETE RESTRICT,
        level_order INTEGER NOT NULL,
        qc_level_id INTEGER REFERENCES md_qc_levels(id) ON DELETE RESTRICT,
        qc_lot_id INTEGER REFERENCES md_qc_material_lots(id) ON DELETE RESTRICT,
        lot_no TEXT NOT NULL,
        level_name TEXT NOT NULL,
        UNIQUE(context_id,level_order)
    );
    CREATE TABLE IF NOT EXISTS qc_result_evaluations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        context_id INTEGER NOT NULL REFERENCES qc_result_contexts(id) ON DELETE RESTRICT,
        evaluation_json TEXT NOT NULL,
        algorithm_version TEXT NOT NULL DEFAULT 'V1.2-existing-rules',
        reason TEXT NOT NULL,
        previous_id INTEGER REFERENCES qc_result_evaluations(id) ON DELETE RESTRICT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_lot_usage_system_time ON qc_reagent_lot_usage(template_item_id,effective_at,id);
    CREATE INDEX IF NOT EXISTS idx_lot_events_system_time ON qc_lot_change_events(template_item_id,effective_at,id);
    CREATE INDEX IF NOT EXISTS idx_evaluation_context ON qc_result_evaluations(context_id,id);
    INSERT OR IGNORE INTO schema_migrations(migration_key,app_version) VALUES('v1_2_lot_lifecycle_004','V1.2');
    ''')
