from __future__ import annotations

import csv
from pathlib import Path
import sqlite3
from uuid import uuid4


MIGRATION_KEY = "v1_1_master_data_001"


def _new_uid() -> str:
    return str(uuid4())


def ensure_v11_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            migration_key TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            app_version TEXT NOT NULL DEFAULT '',
            checksum TEXT NOT NULL DEFAULT ''
        )
        """
    )

    _create_master_data_tables(connection)
    _create_project_configuration_tables(connection)
    _create_indexes(connection)
    _seed_builtin_sources(connection)
    _seed_builtin_units(connection)
    _seed_builtin_methods(connection)
    _seed_wst886_test_items(connection)
    connection.execute(
        """
        INSERT OR IGNORE INTO schema_migrations (migration_key, app_version)
        VALUES (?, ?)
        """,
        (MIGRATION_KEY, "V1.1"),
    )


def _create_master_data_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS md_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL UNIQUE,
            source_code TEXT NOT NULL UNIQUE,
            source_name TEXT NOT NULL,
            source_kind TEXT NOT NULL CHECK (
                source_kind IN (
                    'standard', 'nmpa_registration', 'nmpa_udi',
                    'vendor', 'hospital', 'legacy', 'import', 'builtin'
                )
            ),
            publisher TEXT NOT NULL DEFAULT '',
            version_label TEXT NOT NULL DEFAULT '',
            effective_date TEXT,
            source_url TEXT NOT NULL DEFAULT '',
            checksum TEXT NOT NULL DEFAULT '',
            imported_at TEXT,
            is_disabled INTEGER NOT NULL DEFAULT 0 CHECK (is_disabled IN (0, 1)),
            disabled_at TEXT,
            disabled_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS md_manufacturers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL UNIQUE,
            origin_type TEXT NOT NULL CHECK (origin_type IN ('official', 'hospital', 'import')),
            legal_name TEXT NOT NULL,
            display_name TEXT NOT NULL,
            country_or_region TEXT NOT NULL DEFAULT '',
            registration_holder_name TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            is_disabled INTEGER NOT NULL DEFAULT 0 CHECK (is_disabled IN (0, 1)),
            disabled_at TEXT,
            disabled_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS md_units (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL UNIQUE,
            origin_type TEXT NOT NULL CHECK (origin_type IN ('official', 'hospital', 'import')),
            symbol TEXT NOT NULL,
            unit_name TEXT NOT NULL DEFAULT '',
            ucum_code TEXT NOT NULL DEFAULT '',
            quantity_kind TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT '',
            is_disabled INTEGER NOT NULL DEFAULT 0 CHECK (is_disabled IN (0, 1)),
            disabled_at TEXT,
            disabled_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS md_methods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL UNIQUE,
            origin_type TEXT NOT NULL CHECK (origin_type IN ('official', 'hospital', 'import')),
            method_code TEXT NOT NULL DEFAULT '',
            method_name TEXT NOT NULL,
            method_category TEXT NOT NULL DEFAULT '',
            principle TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            is_disabled INTEGER NOT NULL DEFAULT 0 CHECK (is_disabled IN (0, 1)),
            disabled_at TEXT,
            disabled_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS md_test_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL UNIQUE,
            origin_type TEXT NOT NULL CHECK (origin_type IN ('official', 'hospital', 'import')),
            standard_code TEXT NOT NULL DEFAULT '',
            chinese_name TEXT NOT NULL,
            english_name TEXT NOT NULL DEFAULT '',
            abbreviation TEXT NOT NULL DEFAULT '',
            category_code TEXT NOT NULL DEFAULT '',
            category_name TEXT NOT NULL DEFAULT '',
            specimen_type TEXT NOT NULL DEFAULT '',
            result_type TEXT NOT NULL DEFAULT 'quantitative'
                CHECK (result_type IN ('quantitative', 'qualitative', 'semi_quantitative')),
            default_unit_id INTEGER,
            notes TEXT NOT NULL DEFAULT '',
            is_disabled INTEGER NOT NULL DEFAULT 0 CHECK (is_disabled IN (0, 1)),
            disabled_at TEXT,
            disabled_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (default_unit_id) REFERENCES md_units (id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS md_instrument_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL UNIQUE,
            origin_type TEXT NOT NULL CHECK (origin_type IN ('official', 'hospital', 'import')),
            manufacturer_id INTEGER,
            generic_name TEXT NOT NULL,
            brand_name TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL,
            registration_no TEXT NOT NULL DEFAULT '',
            device_category_code TEXT NOT NULL DEFAULT '',
            catalog_no TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            is_disabled INTEGER NOT NULL DEFAULT 0 CHECK (is_disabled IN (0, 1)),
            disabled_at TEXT,
            disabled_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (manufacturer_id) REFERENCES md_manufacturers (id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS lab_instruments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL UNIQUE,
            origin_type TEXT NOT NULL DEFAULT 'hospital'
                CHECK (origin_type IN ('hospital', 'import')),
            instrument_model_id INTEGER NOT NULL,
            display_name TEXT NOT NULL,
            asset_code TEXT NOT NULL DEFAULT '',
            serial_number TEXT NOT NULL DEFAULT '',
            department_name TEXT NOT NULL DEFAULT '',
            instrument_group TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            is_disabled INTEGER NOT NULL DEFAULT 0 CHECK (is_disabled IN (0, 1)),
            disabled_at TEXT,
            disabled_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (instrument_model_id) REFERENCES md_instrument_models (id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS md_reagents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL UNIQUE,
            origin_type TEXT NOT NULL CHECK (origin_type IN ('official', 'hospital', 'import')),
            manufacturer_id INTEGER,
            generic_name TEXT NOT NULL,
            trade_name TEXT NOT NULL DEFAULT '',
            specification TEXT NOT NULL DEFAULT '',
            registration_no TEXT NOT NULL DEFAULT '',
            catalog_no TEXT NOT NULL DEFAULT '',
            applicable_instrument_text TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            is_disabled INTEGER NOT NULL DEFAULT 0 CHECK (is_disabled IN (0, 1)),
            disabled_at TEXT,
            disabled_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (manufacturer_id) REFERENCES md_manufacturers (id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS md_qc_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL UNIQUE,
            origin_type TEXT NOT NULL CHECK (origin_type IN ('official', 'hospital', 'import')),
            manufacturer_id INTEGER,
            generic_name TEXT NOT NULL,
            trade_name TEXT NOT NULL DEFAULT '',
            matrix TEXT NOT NULL DEFAULT '',
            physical_form TEXT NOT NULL DEFAULT '',
            catalog_no TEXT NOT NULL DEFAULT '',
            registration_no TEXT NOT NULL DEFAULT '',
            nominal_level_count INTEGER CHECK (
                nominal_level_count IS NULL OR nominal_level_count BETWEEN 1 AND 9
            ),
            notes TEXT NOT NULL DEFAULT '',
            is_disabled INTEGER NOT NULL DEFAULT 0 CHECK (is_disabled IN (0, 1)),
            disabled_at TEXT,
            disabled_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (manufacturer_id) REFERENCES md_manufacturers (id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS md_qc_material_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL UNIQUE,
            origin_type TEXT NOT NULL CHECK (origin_type IN ('hospital', 'import')),
            qc_material_id INTEGER NOT NULL,
            lot_no TEXT NOT NULL,
            manufacture_date TEXT,
            expiry_date TEXT,
            received_date TEXT,
            opened_date TEXT,
            notes TEXT NOT NULL DEFAULT '',
            is_disabled INTEGER NOT NULL DEFAULT 0 CHECK (is_disabled IN (0, 1)),
            disabled_at TEXT,
            disabled_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (qc_material_id) REFERENCES md_qc_materials (id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS md_qc_levels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL UNIQUE,
            origin_type TEXT NOT NULL CHECK (origin_type IN ('hospital', 'import')),
            qc_material_lot_id INTEGER NOT NULL,
            level_code TEXT NOT NULL DEFAULT '',
            level_name TEXT NOT NULL,
            level_order INTEGER NOT NULL CHECK (level_order BETWEEN 1 AND 9),
            concentration_label TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            is_disabled INTEGER NOT NULL DEFAULT 0 CHECK (is_disabled IN (0, 1)),
            disabled_at TEXT,
            disabled_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (qc_material_lot_id) REFERENCES md_qc_material_lots (id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS md_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL UNIQUE,
            origin_type TEXT NOT NULL CHECK (origin_type IN ('official', 'hospital', 'import')),
            entity_type TEXT NOT NULL CHECK (
                entity_type IN (
                    'manufacturer', 'test_item', 'instrument_model',
                    'reagent', 'qc_material', 'method', 'unit'
                )
            ),
            entity_id INTEGER NOT NULL,
            alias_text TEXT NOT NULL,
            normalized_alias TEXT NOT NULL,
            alias_type TEXT NOT NULL CHECK (
                alias_type IN (
                    'short_name', 'english', 'brand', 'lis_code',
                    'historical', 'vendor_text', 'custom'
                )
            ),
            source_id INTEGER,
            is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
            is_disabled INTEGER NOT NULL DEFAULT 0 CHECK (is_disabled IN (0, 1)),
            disabled_at TEXT,
            disabled_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (source_id) REFERENCES md_sources (id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS md_source_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL UNIQUE,
            origin_type TEXT NOT NULL DEFAULT 'official'
                CHECK (origin_type IN ('official', 'import')),
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            source_id INTEGER NOT NULL,
            external_record_id TEXT NOT NULL,
            source_status TEXT NOT NULL DEFAULT 'active'
                CHECK (source_status IN ('active', 'expired', 'revoked', 'unknown')),
            source_updated_at TEXT,
            raw_display_name TEXT NOT NULL DEFAULT '',
            record_hash TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            is_disabled INTEGER NOT NULL DEFAULT 0 CHECK (is_disabled IN (0, 1)),
            disabled_at TEXT,
            disabled_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (source_id) REFERENCES md_sources (id) ON DELETE RESTRICT,
            UNIQUE (source_id, external_record_id)
        );
        """
    )


def _create_project_configuration_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS qc_project_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL UNIQUE,
            origin_type TEXT NOT NULL DEFAULT 'hospital'
                CHECK (origin_type IN ('hospital', 'import')),
            template_name TEXT NOT NULL,
            lab_instrument_id INTEGER,
            qc_material_id INTEGER,
            department_name_snapshot TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active')),
            revision_no INTEGER NOT NULL DEFAULT 1 CHECK (revision_no >= 1),
            notes TEXT NOT NULL DEFAULT '',
            is_disabled INTEGER NOT NULL DEFAULT 0 CHECK (is_disabled IN (0, 1)),
            disabled_at TEXT,
            disabled_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lab_instrument_id) REFERENCES lab_instruments (id) ON DELETE RESTRICT,
            FOREIGN KEY (qc_material_id) REFERENCES md_qc_materials (id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS qc_project_template_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL UNIQUE,
            origin_type TEXT NOT NULL DEFAULT 'hospital'
                CHECK (origin_type IN ('hospital', 'import')),
            template_id INTEGER NOT NULL,
            test_item_id INTEGER NOT NULL,
            qc_method TEXT NOT NULL CHECK (qc_method IN ('lj', 'zscore', 'instant')),
            input_value_type TEXT NOT NULL CHECK (input_value_type IN ('raw', 'ct', 'log')),
            unit_id INTEGER,
            method_id INTEGER,
            reagent_id INTEGER,
            level_count INTEGER NOT NULL CHECK (level_count BETWEEN 1 AND 3),
            target_n INTEGER NOT NULL CHECK (target_n BETWEEN 5 AND 20),
            cv_limit REAL CHECK (cv_limit IS NULL OR cv_limit > 0),
            quality_target_source_text TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT '',
            is_disabled INTEGER NOT NULL DEFAULT 0 CHECK (is_disabled IN (0, 1)),
            disabled_at TEXT,
            disabled_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (template_id) REFERENCES qc_project_templates (id) ON DELETE RESTRICT,
            FOREIGN KEY (test_item_id) REFERENCES md_test_items (id) ON DELETE RESTRICT,
            FOREIGN KEY (unit_id) REFERENCES md_units (id) ON DELETE RESTRICT,
            FOREIGN KEY (method_id) REFERENCES md_methods (id) ON DELETE RESTRICT,
            FOREIGN KEY (reagent_id) REFERENCES md_reagents (id) ON DELETE RESTRICT,
            UNIQUE (template_id, test_item_id, qc_method, input_value_type)
        );

        CREATE TABLE IF NOT EXISTS qc_lot_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL UNIQUE,
            origin_type TEXT NOT NULL DEFAULT 'hospital'
                CHECK (origin_type IN ('hospital', 'import')),
            template_id INTEGER NOT NULL,
            qc_material_lot_id INTEGER,
            lab_instrument_id INTEGER NOT NULL,
            qc_material_id INTEGER NOT NULL,
            config_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'active', 'superseded', 'disabled')),
            revision_no INTEGER NOT NULL DEFAULT 1 CHECK (revision_no >= 1),
            copied_from_config_id INTEGER,
            effective_from TEXT,
            effective_to TEXT,
            activated_at TEXT,
            notes TEXT NOT NULL DEFAULT '',
            is_disabled INTEGER NOT NULL DEFAULT 0 CHECK (is_disabled IN (0, 1)),
            disabled_at TEXT,
            disabled_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (template_id) REFERENCES qc_project_templates (id) ON DELETE RESTRICT,
            FOREIGN KEY (qc_material_lot_id) REFERENCES md_qc_material_lots (id) ON DELETE RESTRICT,
            FOREIGN KEY (lab_instrument_id) REFERENCES lab_instruments (id) ON DELETE RESTRICT,
            FOREIGN KEY (qc_material_id) REFERENCES md_qc_materials (id) ON DELETE RESTRICT,
            FOREIGN KEY (copied_from_config_id) REFERENCES qc_lot_configs (id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS qc_lot_config_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL UNIQUE,
            origin_type TEXT NOT NULL DEFAULT 'hospital'
                CHECK (origin_type IN ('hospital', 'import')),
            lot_config_id INTEGER NOT NULL,
            source_template_item_id INTEGER,
            test_item_id INTEGER NOT NULL,
            qc_method TEXT NOT NULL CHECK (qc_method IN ('lj', 'zscore', 'instant')),
            input_value_type TEXT NOT NULL CHECK (input_value_type IN ('raw', 'ct', 'log')),
            unit_id INTEGER,
            method_id INTEGER,
            reagent_id INTEGER,
            level_count INTEGER NOT NULL CHECK (level_count BETWEEN 1 AND 3),
            target_n INTEGER NOT NULL CHECK (target_n BETWEEN 5 AND 20),
            cv_limit REAL CHECK (cv_limit IS NULL OR cv_limit > 0),
            quality_target_source_text TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_enabled INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0, 1)),
            notes TEXT NOT NULL DEFAULT '',
            is_disabled INTEGER NOT NULL DEFAULT 0 CHECK (is_disabled IN (0, 1)),
            disabled_at TEXT,
            disabled_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lot_config_id) REFERENCES qc_lot_configs (id) ON DELETE RESTRICT,
            FOREIGN KEY (source_template_item_id) REFERENCES qc_project_template_items (id) ON DELETE RESTRICT,
            FOREIGN KEY (test_item_id) REFERENCES md_test_items (id) ON DELETE RESTRICT,
            FOREIGN KEY (unit_id) REFERENCES md_units (id) ON DELETE RESTRICT,
            FOREIGN KEY (method_id) REFERENCES md_methods (id) ON DELETE RESTRICT,
            FOREIGN KEY (reagent_id) REFERENCES md_reagents (id) ON DELETE RESTRICT,
            UNIQUE (lot_config_id, test_item_id, qc_method, input_value_type)
        );

        CREATE TABLE IF NOT EXISTS qc_lot_config_item_levels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL UNIQUE,
            origin_type TEXT NOT NULL DEFAULT 'hospital'
                CHECK (origin_type IN ('hospital', 'import')),
            lot_config_item_id INTEGER NOT NULL,
            qc_level_id INTEGER NOT NULL,
            level_order INTEGER NOT NULL CHECK (level_order BETWEEN 1 AND 3),
            target_source TEXT NOT NULL DEFAULT 'building'
                CHECK (target_source IN ('building', 'manufacturer', 'manual', 'copied_pending')),
            target_mean REAL,
            target_sd REAL CHECK (target_sd IS NULL OR target_sd > 0),
            target_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (target_confirmed IN (0, 1)),
            notes TEXT NOT NULL DEFAULT '',
            is_disabled INTEGER NOT NULL DEFAULT 0 CHECK (is_disabled IN (0, 1)),
            disabled_at TEXT,
            disabled_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lot_config_item_id) REFERENCES qc_lot_config_items (id) ON DELETE RESTRICT,
            FOREIGN KEY (qc_level_id) REFERENCES md_qc_levels (id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS qc_config_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL UNIQUE,
            lot_config_id INTEGER NOT NULL,
            revision_no INTEGER NOT NULL CHECK (revision_no >= 1),
            action_type TEXT NOT NULL CHECK (
                action_type IN (
                    'create', 'edit', 'copy', 'activate',
                    'disable', 'reactivate', 'import'
                )
            ),
            snapshot_json TEXT NOT NULL,
            change_summary TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lot_config_id) REFERENCES qc_lot_configs (id) ON DELETE RESTRICT,
            UNIQUE (lot_config_id, revision_no)
        );
        """
    )


def _create_indexes(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_md_manufacturer_name_active
        ON md_manufacturers (LOWER(TRIM(display_name)))
        WHERE is_disabled = 0;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_md_unit_symbol_active
        ON md_units (LOWER(TRIM(symbol)))
        WHERE is_disabled = 0;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_md_method_name_active
        ON md_methods (LOWER(TRIM(method_name)))
        WHERE is_disabled = 0;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_md_test_item_name_active
        ON md_test_items (LOWER(TRIM(chinese_name)))
        WHERE is_disabled = 0;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_md_instrument_model_active
        ON md_instrument_models (
            COALESCE(manufacturer_id, 0),
            LOWER(TRIM(model))
        )
        WHERE is_disabled = 0;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_lab_instrument_display_active
        ON lab_instruments (LOWER(TRIM(display_name)))
        WHERE is_disabled = 0;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_md_reagent_name_active
        ON md_reagents (
            COALESCE(manufacturer_id, 0),
            LOWER(TRIM(generic_name)),
            LOWER(TRIM(COALESCE(trade_name, '')))
        )
        WHERE is_disabled = 0;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_md_qc_material_name_active
        ON md_qc_materials (
            COALESCE(manufacturer_id, 0),
            LOWER(TRIM(generic_name)),
            LOWER(TRIM(COALESCE(trade_name, '')))
        )
        WHERE is_disabled = 0;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_md_qc_lot_active
        ON md_qc_material_lots (qc_material_id, LOWER(TRIM(lot_no)))
        WHERE is_disabled = 0;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_md_qc_level_order_active
        ON md_qc_levels (qc_material_lot_id, level_order)
        WHERE is_disabled = 0;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_md_alias_active
        ON md_aliases (entity_type, entity_id, normalized_alias, alias_type)
        WHERE is_disabled = 0;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_qc_template_name_active
        ON qc_project_templates (LOWER(TRIM(template_name)))
        WHERE is_disabled = 0;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_qc_lot_config_active
        ON qc_lot_configs (template_id, qc_material_lot_id)
        WHERE is_disabled = 0;

        CREATE INDEX IF NOT EXISTS idx_md_test_items_search
        ON md_test_items (chinese_name, abbreviation, standard_code);

        CREATE INDEX IF NOT EXISTS idx_md_aliases_search
        ON md_aliases (normalized_alias, entity_type, entity_id);

        CREATE INDEX IF NOT EXISTS idx_qc_template_items_template
        ON qc_project_template_items (template_id, sort_order, id);

        CREATE INDEX IF NOT EXISTS idx_qc_lot_configs_template
        ON qc_lot_configs (template_id, created_at, id);

        CREATE INDEX IF NOT EXISTS idx_qc_lot_config_items_config
        ON qc_lot_config_items (lot_config_id, sort_order, id);

        CREATE INDEX IF NOT EXISTS idx_qc_lot_item_levels_item
        ON qc_lot_config_item_levels (lot_config_item_id, level_order, id);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_qc_lot_item_level_active
        ON qc_lot_config_item_levels (lot_config_item_id, qc_level_id)
        WHERE is_disabled = 0;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_qc_lot_item_order_active
        ON qc_lot_config_item_levels (lot_config_item_id, level_order)
        WHERE is_disabled = 0;
        """
    )


def _seed_builtin_sources(connection: sqlite3.Connection) -> None:
    seeds = [
        {
            "source_code": "LJQC_BUILTIN",
            "source_name": "LJQC 内置基础词条",
            "source_kind": "builtin",
            "publisher": "LJQCApp",
            "version_label": "V1.1",
            "effective_date": None,
            "source_url": "",
        },
        {
            "source_code": "WST886_2026",
            "source_name": "WS/T 886—2026 临床检验常用项目名称及代码",
            "source_kind": "standard",
            "publisher": "中华人民共和国国家卫生健康委员会",
            "version_label": "WS/T 886—2026",
            "effective_date": "2026-11-01",
            "source_url": (
                "https://www.nhc.gov.cn/fzs/c100048/202606/"
                "1d8e67475848413cb4447e1b49037888/files/"
                "WST%20886%E2%80%942026.pdf"
            ),
        },
        {
            "source_code": "NMPA_UDI",
            "source_name": "国家药品监督管理局医疗器械唯一标识数据库",
            "source_kind": "nmpa_udi",
            "publisher": "国家药品监督管理局",
            "version_label": "在线数据源",
            "effective_date": None,
            "source_url": "https://udi.nmpa.gov.cn/",
        },
    ]
    for seed in seeds:
        connection.execute(
            """
            INSERT INTO md_sources (
                uid, source_code, source_name, source_kind, publisher,
                version_label, effective_date, source_url
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_code) DO UPDATE SET
                source_name = excluded.source_name,
                source_kind = excluded.source_kind,
                publisher = excluded.publisher,
                version_label = excluded.version_label,
                effective_date = excluded.effective_date,
                source_url = excluded.source_url,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                _new_uid(),
                seed["source_code"],
                seed["source_name"],
                seed["source_kind"],
                seed["publisher"],
                seed["version_label"],
                seed["effective_date"],
                seed["source_url"],
            ),
        )


def _seed_builtin_units(connection: sqlite3.Connection) -> None:
    seeds = [
        ("1", "无量纲", "1", "dimensionless", 10),
        ("Ct", "循环阈值", "", "cycle_threshold", 20),
        ("log", "对数值", "", "dimensionless", 30),
        ("mmol/L", "毫摩尔每升", "mmol/L", "substance_concentration", 40),
        ("mg/L", "毫克每升", "mg/L", "mass_concentration", 50),
        ("g/L", "克每升", "g/L", "mass_concentration", 60),
        ("U/L", "单位每升", "[IU]/L", "catalytic_activity", 70),
        ("%", "百分比", "%", "ratio", 80),
        ("10^9/L", "十的九次方每升", "10*9/L", "number_concentration", 90),
    ]
    for symbol, unit_name, ucum_code, quantity_kind, sort_order in seeds:
        exists = connection.execute(
            """
            SELECT id FROM md_units
            WHERE LOWER(TRIM(symbol)) = LOWER(TRIM(?))
              AND is_disabled = 0
            """,
            (symbol,),
        ).fetchone()
        if exists is not None:
            continue
        connection.execute(
            """
            INSERT INTO md_units (
                uid, origin_type, symbol, unit_name, ucum_code, quantity_kind, sort_order
            )
            VALUES (?, 'official', ?, ?, ?, ?, ?)
            """,
            (_new_uid(), symbol, unit_name, ucum_code, quantity_kind, sort_order),
        )


def _seed_builtin_methods(connection: sqlite3.Connection) -> None:
    seeds = [
        ("COLORIMETRY", "比色法", "生化"),
        ("IMMUNOTURBIDIMETRY", "免疫比浊法", "免疫"),
        ("ECLIA", "电化学发光法", "免疫"),
        ("FLUORESCENT_PCR", "荧光 PCR 法", "分子"),
        ("OTHER", "其他方法", "其他"),
    ]
    for method_code, method_name, method_category in seeds:
        exists = connection.execute(
            """
            SELECT id FROM md_methods
            WHERE LOWER(TRIM(method_name)) = LOWER(TRIM(?))
              AND is_disabled = 0
            """,
            (method_name,),
        ).fetchone()
        if exists is not None:
            continue
        connection.execute(
            """
            INSERT INTO md_methods (
                uid, origin_type, method_code, method_name, method_category
            )
            VALUES (?, 'official', ?, ?, ?)
            """,
            (_new_uid(), method_code, method_name, method_category),
        )


def _seed_wst886_test_items(connection: sqlite3.Connection) -> None:
    catalog_path = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "dictionaries"
        / "wst_886_2026_quantitative.csv"
    )
    if not catalog_path.is_file():
        return
    source = connection.execute(
        "SELECT id FROM md_sources WHERE source_code = ?",
        ("WST886_2026",),
    ).fetchone()
    if source is None:
        return
    source_id = int(source["id"])
    with catalog_path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))

    for row in rows:
        standard_code = str(row.get("standard_code") or "").strip()
        chinese_name = str(row.get("chinese_name") or "").strip()
        if not standard_code or not chinese_name:
            continue
        existing = connection.execute(
            """
            SELECT id, origin_type
            FROM md_test_items
            WHERE standard_code = ?
               OR LOWER(TRIM(chinese_name)) = LOWER(TRIM(?))
            ORDER BY CASE WHEN standard_code = ? THEN 0 ELSE 1 END, id ASC
            LIMIT 1
            """,
            (standard_code, chinese_name, standard_code),
        ).fetchone()
        if existing is None:
            cursor = connection.execute(
                """
                INSERT INTO md_test_items (
                    uid, origin_type, standard_code, chinese_name,
                    category_name, specimen_type, result_type, notes
                )
                VALUES (?, 'official', ?, ?, ?, ?, 'quantitative', ?)
                """,
                (
                    _new_uid(),
                    standard_code,
                    chinese_name,
                    str(row.get("category_name") or "").strip(),
                    str(row.get("specimen_type") or "").strip(),
                    f"分析物：{str(row.get('analyte_name') or '').strip()}",
                ),
            )
            test_item_id = int(cursor.lastrowid)
        else:
            test_item_id = int(existing["id"])
            if str(existing["origin_type"]) == "official":
                connection.execute(
                    """
                    UPDATE md_test_items
                    SET standard_code = ?,
                        chinese_name = ?,
                        category_name = ?,
                        specimen_type = ?,
                        result_type = 'quantitative',
                        notes = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        standard_code,
                        chinese_name,
                        str(row.get("category_name") or "").strip(),
                        str(row.get("specimen_type") or "").strip(),
                        f"分析物：{str(row.get('analyte_name') or '').strip()}",
                        test_item_id,
                    ),
                )
        external_record_id = str(row.get("source_record_id") or "").strip()
        if not external_record_id:
            external_record_id = f"WST886_2026:{standard_code}"
        connection.execute(
            """
            INSERT INTO md_source_records (
                uid, origin_type, entity_type, entity_id, source_id,
                external_record_id, source_status, source_updated_at,
                raw_display_name, last_seen_at
            )
            VALUES (
                ?, 'official', 'test_item', ?, ?, ?, 'active',
                '2026-05-25', ?, CURRENT_TIMESTAMP
            )
            ON CONFLICT(source_id, external_record_id) DO UPDATE SET
                entity_id = excluded.entity_id,
                source_status = 'active',
                source_updated_at = excluded.source_updated_at,
                raw_display_name = excluded.raw_display_name,
                last_seen_at = CURRENT_TIMESTAMP,
                is_disabled = 0,
                disabled_at = NULL,
                disabled_reason = '',
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                _new_uid(),
                test_item_id,
                source_id,
                external_record_id,
                chinese_name,
            ),
        )
