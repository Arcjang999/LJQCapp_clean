"""Manufacturer roles and immutable directory/coverage provenance."""


def ensure_product_directory_schema(connection):
    statements = [
        '''CREATE TABLE IF NOT EXISTS md_manufacturer_categories (
            manufacturer_id INTEGER NOT NULL REFERENCES md_manufacturers(id),
            category TEXT NOT NULL CHECK(category IN ('instrument','reagent','qc_material')),
            provenance TEXT NOT NULL DEFAULT 'local_confirmation',
            PRIMARY KEY(manufacturer_id,category))''',
        '''CREATE TABLE IF NOT EXISTS md_product_directory_releases (
            id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES md_sources(id),
            source_code TEXT NOT NULL,version_label TEXT NOT NULL,payload_sha256 TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,manufacturer_id INTEGER NOT NULL REFERENCES md_manufacturers(id),
            metadata_json TEXT NOT NULL,published_by TEXT NOT NULL,published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            is_test INTEGER NOT NULL DEFAULT 0,UNIQUE(source_code,version_label))''',
        '''CREATE TABLE IF NOT EXISTS md_product_directory_items (
            release_id INTEGER NOT NULL REFERENCES md_product_directory_releases(id),
            product_code TEXT NOT NULL,product_name TEXT NOT NULL,concentration TEXT NOT NULL,
            concentration_code TEXT NOT NULL,source_rows_json TEXT NOT NULL,
            product_id INTEGER REFERENCES md_qc_materials(id),specification_id INTEGER REFERENCES md_qc_material_specs(id),
            excluded_reason TEXT NOT NULL DEFAULT '',PRIMARY KEY(release_id,product_code))''',
        '''CREATE TABLE IF NOT EXISTS md_product_directory_keys (
            source_code TEXT NOT NULL,product_code TEXT NOT NULL,
            product_id INTEGER NOT NULL UNIQUE REFERENCES md_qc_materials(id),
            current_release_id INTEGER NOT NULL REFERENCES md_product_directory_releases(id),
            PRIMARY KEY(source_code,product_code))''',
        '''CREATE TABLE IF NOT EXISTS md_product_coverage (
            id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES md_qc_materials(id),
            test_item_id INTEGER NOT NULL REFERENCES md_test_items(id),
            method_id INTEGER REFERENCES md_methods(id),source_kind TEXT NOT NULL CHECK(source_kind IN ('local','directory')),
            source_version TEXT NOT NULL DEFAULT '',evidence TEXT NOT NULL,confirmed_by TEXT NOT NULL,
            confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,is_disabled INTEGER NOT NULL DEFAULT 0,
            UNIQUE(product_id,test_item_id))''',
        '''CREATE TABLE IF NOT EXISTS md_product_coverage_history (
            id INTEGER PRIMARY KEY,product_id INTEGER NOT NULL REFERENCES md_qc_materials(id),
            snapshot_json TEXT NOT NULL,changed_by TEXT NOT NULL,evidence TEXT NOT NULL,
            saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''',
    ]
    for statement in statements:
        connection.execute(statement)
    # A product number distinguishes equal names at different concentrations.
    existing = connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_md_qc_material_name_active'").fetchone()
    if existing and 'catalog_no' not in existing[0]:
        connection.execute('DROP INDEX idx_md_qc_material_name_active')
    connection.execute('''CREATE UNIQUE INDEX IF NOT EXISTS idx_md_qc_material_name_active
        ON md_qc_materials(COALESCE(manufacturer_id,0),LOWER(TRIM(generic_name)),
        LOWER(TRIM(COALESCE(trade_name,''))),LOWER(TRIM(COALESCE(catalog_no,'')))) WHERE is_disabled=0''')
    for table, category in [('md_instrument_models','instrument'),('md_reagents','reagent'),('md_qc_materials','qc_material')]:
        for operation in ('INSERT','UPDATE OF manufacturer_id'):
            suffix = 'insert' if operation == 'INSERT' else 'update'
            connection.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_category_{suffix}
                BEFORE {operation} ON {table} WHEN NEW.manufacturer_id IS NOT NULL
                AND NOT EXISTS(SELECT 1 FROM md_manufacturers m JOIN md_manufacturer_categories c ON c.manufacturer_id=m.id
                    WHERE m.id=NEW.manufacturer_id AND m.is_disabled=0 AND c.category='{category}')
                BEGIN SELECT RAISE(ABORT,'所选厂家未具备对应业务类别或已停用。'); END''')
    connection.execute("INSERT OR IGNORE INTO schema_migrations(migration_key,app_version) VALUES ('product_directory_001','V1.3')")
