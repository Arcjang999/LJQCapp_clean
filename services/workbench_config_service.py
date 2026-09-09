from __future__ import annotations

import sqlite3

import pandas as pd

from database import PROJECT_METHOD_LJ, get_connection


LJ_METHOD = "lj"
SUPPORTED_LJ_TARGET_SOURCE = "building"


def _clean_text(value: object, fallback: str = "") -> str:
    text = " ".join(str(value or "").split()).strip()
    return text or fallback


def _read_dataframe(sql: str, params: tuple[object, ...] = ()) -> pd.DataFrame:
    with get_connection() as connection:
        return pd.read_sql_query(sql, connection, params=params)


def _active_lj_source_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT
            items.id AS lot_config_item_id,
            items.source_template_item_id AS project_template_item_id,
            items.lot_config_id,
            items.input_value_type,
            items.target_n,
            items.cv_limit,
            items.quality_target_source_text,
            configs.revision_no AS source_revision_no,
            configs.config_name,
            tests.chinese_name AS test_item_name,
            tests.standard_code,
            local.display_name AS instrument_name,
            local.department_name,
            COALESCE(NULLIF(reagents.trade_name, ''), reagents.generic_name, '') AS reagent_name,
            reagent_manufacturers.display_name AS reagent_manufacturer_name,
            COALESCE(NULLIF(materials.trade_name, ''), materials.generic_name, '') AS qc_material_name,
            material_manufacturers.display_name AS qc_material_manufacturer_name,
            lots.lot_no,
            lots.expiry_date,
            assigned.qc_level_id,
            levels.level_name,
            levels.concentration_label,
            assigned.target_source,
            assigned.target_mean,
            assigned.target_sd,
            units.symbol AS unit_symbol,
            methods.method_name
        FROM qc_lot_config_items AS items
        INNER JOIN qc_lot_configs AS configs ON configs.id = items.lot_config_id
        INNER JOIN qc_project_templates AS templates ON templates.id = configs.template_id
        INNER JOIN md_test_items AS tests ON tests.id = items.test_item_id
        INNER JOIN lab_instruments AS local ON local.id = configs.lab_instrument_id
        INNER JOIN md_qc_materials AS materials ON materials.id = configs.qc_material_id
        INNER JOIN md_qc_material_lots AS lots ON lots.id = configs.qc_material_lot_id
        INNER JOIN qc_lot_config_item_levels AS assigned
            ON assigned.lot_config_item_id = items.id
           AND assigned.is_disabled = 0
        INNER JOIN md_qc_levels AS levels
            ON levels.id = assigned.qc_level_id
           AND levels.is_disabled = 0
        LEFT JOIN md_reagents AS reagents ON reagents.id = items.reagent_id
        LEFT JOIN md_manufacturers AS reagent_manufacturers
            ON reagent_manufacturers.id = reagents.manufacturer_id
        LEFT JOIN md_manufacturers AS material_manufacturers
            ON material_manufacturers.id = materials.manufacturer_id
        LEFT JOIN md_units AS units ON units.id = items.unit_id
        LEFT JOIN md_methods AS methods ON methods.id = items.method_id
        WHERE items.qc_method = 'lj'
          AND items.level_count = 1
          AND items.is_enabled = 1
          AND items.is_disabled = 0
          AND configs.status = 'active'
          AND configs.is_disabled = 0
          AND templates.status = 'active'
          AND templates.is_disabled = 0
          AND tests.is_disabled = 0
          AND local.is_disabled = 0
          AND materials.is_disabled = 0
          AND lots.is_disabled = 0
          AND assigned.target_source = ?
        ORDER BY configs.id ASC, items.sort_order ASC, items.id ASC
        """,
        (SUPPORTED_LJ_TARGET_SOURCE,),
    ).fetchall()


def _runtime_project_exists(connection: sqlite3.Connection, project_id: int) -> bool:
    return connection.execute(
        "SELECT id FROM projects WHERE id = ? AND method_type = ?",
        (int(project_id), PROJECT_METHOD_LJ),
    ).fetchone() is not None


def _runtime_batch_exists(connection: sqlite3.Connection, batch_id: int) -> bool:
    return connection.execute(
        "SELECT id FROM batches WHERE id = ?",
        (int(batch_id),),
    ).fetchone() is not None


def _unique_runtime_project_name(
    connection: sqlite3.Connection,
    *,
    test_item_name: str,
    instrument_name: str,
    identity_id: int,
) -> str:
    base_name = _clean_text(
        f"{_clean_text(test_item_name, '未命名项目')}｜{_clean_text(instrument_name, '未命名仪器')}",
        "V1.2 LJ 项目",
    )
    candidate = base_name
    suffix = 0
    while connection.execute(
        "SELECT id FROM projects WHERE method_type = ? AND LOWER(TRIM(name)) = LOWER(TRIM(?))",
        (PROJECT_METHOD_LJ, candidate),
    ).fetchone() is not None:
        suffix += 1
        candidate = f"{base_name}｜V1.2-{identity_id}-{suffix}"
    return candidate


def _resolve_runtime_project_id(connection: sqlite3.Connection, source: sqlite3.Row) -> int:
    template_item_id = source["project_template_item_id"]
    if template_item_id is not None:
        existing = connection.execute(
            """
            SELECT bindings.runtime_project_id
            FROM qc_workbench_bindings AS bindings
            INNER JOIN projects ON projects.id = bindings.runtime_project_id
            WHERE bindings.qc_method = 'lj'
              AND bindings.project_template_item_id = ?
              AND projects.method_type = 'lj'
              AND projects.input_value_type = ?
            ORDER BY bindings.id ASC
            LIMIT 1
            """,
            (int(template_item_id), str(source["input_value_type"])),
        ).fetchone()
        if existing is not None:
            project_id = int(existing["runtime_project_id"])
            connection.execute(
                "UPDATE projects SET is_disabled = 0 WHERE id = ?",
                (project_id,),
            )
            return project_id

    project_name = _unique_runtime_project_name(
        connection,
        test_item_name=str(source["test_item_name"]),
        instrument_name=str(source["instrument_name"]),
        identity_id=int(template_item_id or source["lot_config_item_id"]),
    )
    cursor = connection.execute(
        """
        INSERT INTO projects (name, method_type, input_value_type, is_disabled)
        VALUES (?, 'lj', ?, 0)
        """,
        (project_name, str(source["input_value_type"])),
    )
    return int(cursor.lastrowid)


def _display_with_manufacturer(name: object, manufacturer: object) -> str:
    clean_name = _clean_text(name, "-")
    clean_manufacturer = _clean_text(manufacturer)
    if clean_manufacturer and clean_manufacturer.lower() not in clean_name.lower():
        return f"{clean_manufacturer}｜{clean_name}"
    return clean_name


def _materialize_runtime_batch(
    connection: sqlite3.Connection,
    *,
    source: sqlite3.Row,
    runtime_project_id: int,
    existing_batch_id: int | None,
) -> int:
    instrument = _clean_text(source["instrument_name"], "-")
    reagent = _display_with_manufacturer(
        source["reagent_name"], source["reagent_manufacturer_name"]
    )
    qc_material = _display_with_manufacturer(
        source["qc_material_name"], source["qc_material_manufacturer_name"]
    )
    concentration = _clean_text(source["concentration_label"] or source["level_name"], "-")
    values = (
        int(runtime_project_id),
        instrument,
        reagent,
        qc_material,
        concentration,
        _clean_text(source["lot_no"], "-"),
        int(source["target_n"]),
        source["cv_limit"],
    )
    if existing_batch_id is not None and _runtime_batch_exists(connection, existing_batch_id):
        connection.execute(
            """
            UPDATE batches
            SET project_id = ?, instrument = ?, reagent = ?, qc_material = ?,
                concentration = ?, lot_no = ?, target_n = ?, cv_limit = ?,
                is_disabled = 0, source_method = 'v11'
            WHERE id = ?
            """,
            (*values, int(existing_batch_id)),
        )
        return int(existing_batch_id)

    cursor = connection.execute(
        """
        INSERT INTO batches (
            project_id, instrument, reagent, qc_material, concentration,
            lot_no, target_n, cv_limit, is_disabled, source_method
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 'v11')
        """,
        values,
    )
    return int(cursor.lastrowid)


def sync_lj_workbench_bindings() -> int:
    """Materialize active V1.1 LJ configurations into the stable LJ result runtime."""

    with get_connection() as connection:
        connection.execute(
            "UPDATE qc_workbench_bindings SET binding_status = 'inactive', updated_at = CURRENT_TIMESTAMP WHERE qc_method = 'lj'"
        )
        sources = _active_lj_source_rows(connection)
        for source in sources:
            existing_binding = connection.execute(
                """
                SELECT *
                FROM qc_workbench_bindings
                WHERE lot_config_item_id = ?
                """,
                (int(source["lot_config_item_id"]),),
            ).fetchone()
            runtime_project_id = (
                int(existing_binding["runtime_project_id"])
                if existing_binding is not None
                and _runtime_project_exists(connection, int(existing_binding["runtime_project_id"]))
                else _resolve_runtime_project_id(connection, source)
            )
            existing_batch_id = (
                int(existing_binding["runtime_batch_id"])
                if existing_binding is not None
                else None
            )
            runtime_batch_id = _materialize_runtime_batch(
                connection,
                source=source,
                runtime_project_id=runtime_project_id,
                existing_batch_id=existing_batch_id,
            )
            connection.execute(
                """
                INSERT INTO qc_workbench_bindings (
                    qc_method, project_template_item_id, lot_config_id,
                    lot_config_item_id, runtime_project_id, runtime_batch_id,
                    binding_status, source_revision_no
                )
                VALUES ('lj', ?, ?, ?, ?, ?, 'active', ?)
                ON CONFLICT(lot_config_item_id)
                DO UPDATE SET
                    qc_method = 'lj',
                    project_template_item_id = excluded.project_template_item_id,
                    lot_config_id = excluded.lot_config_id,
                    runtime_project_id = excluded.runtime_project_id,
                    runtime_batch_id = excluded.runtime_batch_id,
                    binding_status = 'active',
                    source_revision_no = excluded.source_revision_no,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    source["project_template_item_id"],
                    int(source["lot_config_id"]),
                    int(source["lot_config_item_id"]),
                    runtime_project_id,
                    runtime_batch_id,
                    int(source["source_revision_no"]),
                ),
            )
        return len(sources)


def list_lj_workbench_projects() -> pd.DataFrame:
    sync_lj_workbench_bindings()
    return _read_dataframe(
        """
        WITH v11_projects AS (
        SELECT
            bindings.runtime_project_id AS id,
            tests.chinese_name AS name,
            items.input_value_type,
            local.display_name AS instrument_name,
            materials.generic_name AS qc_material_name,
            units.symbol AS unit_symbol,
            methods.method_name,
            COUNT(DISTINCT bindings.runtime_batch_id) AS batch_count,
            MIN(bindings.created_at) AS created_at,
            0 AS is_from_instant
        FROM qc_workbench_bindings AS bindings
        INNER JOIN qc_lot_config_items AS items ON items.id = bindings.lot_config_item_id
        INNER JOIN qc_lot_configs AS configs ON configs.id = bindings.lot_config_id
        INNER JOIN md_test_items AS tests ON tests.id = items.test_item_id
        INNER JOIN lab_instruments AS local ON local.id = configs.lab_instrument_id
        INNER JOIN md_qc_materials AS materials ON materials.id = configs.qc_material_id
        LEFT JOIN md_units AS units ON units.id = items.unit_id
        LEFT JOIN md_methods AS methods ON methods.id = items.method_id
        WHERE bindings.qc_method = 'lj'
          AND bindings.binding_status = 'active'
          AND configs.status = 'active'
          AND configs.is_disabled = 0
          AND items.is_enabled = 1
          AND items.is_disabled = 0
        GROUP BY bindings.runtime_project_id
        ),
        instant_projects AS (
        SELECT
            projects.id,
            projects.name,
            projects.input_value_type,
            MIN(batches.instrument) AS instrument_name,
            MIN(batches.qc_material) AS qc_material_name,
            '' AS unit_symbol,
            '' AS method_name,
            COUNT(DISTINCT batches.id) AS batch_count,
            projects.created_at,
            1 AS is_from_instant
        FROM projects
        INNER JOIN batches
            ON batches.project_id = projects.id
           AND LOWER(TRIM(COALESCE(batches.source_method, ''))) = 'instant'
           AND batches.is_disabled = 0
        WHERE projects.method_type = 'lj'
          AND projects.is_disabled = 0
          AND NOT EXISTS (
              SELECT 1
              FROM qc_workbench_bindings AS existing_bindings
              WHERE existing_bindings.qc_method = 'lj'
                AND existing_bindings.binding_status = 'active'
                AND existing_bindings.runtime_project_id = projects.id
          )
        GROUP BY projects.id
        )
        SELECT * FROM v11_projects
        UNION ALL
        SELECT * FROM instant_projects
        ORDER BY name ASC, instrument_name ASC, id ASC
        """
    )


def list_lj_workbench_batches(runtime_project_id: int) -> pd.DataFrame:
    sync_lj_workbench_bindings()
    return _read_dataframe(
        """
        WITH v11_batches AS (
        SELECT
            bindings.runtime_batch_id AS id,
            bindings.runtime_project_id AS project_id,
            tests.chinese_name AS project_name,
            items.input_value_type,
            local.display_name AS instrument,
            COALESCE(NULLIF(reagents.trade_name, ''), reagents.generic_name, '') AS reagent,
            materials.generic_name AS qc_material,
            COALESCE(NULLIF(levels.concentration_label, ''), levels.level_name) AS concentration,
            lots.lot_no,
            items.target_n,
            items.cv_limit,
            runtime_batches.created_at,
            'v11' AS source_method,
            '' AS source_transfer_time,
            configs.id AS v11_lot_config_id,
            items.id AS v11_lot_config_item_id,
            configs.config_name AS v11_config_name,
            lots.expiry_date,
            units.symbol AS unit_symbol,
            methods.method_name,
            assigned.target_source AS v11_target_source,
            items.quality_target_source_text
        FROM qc_workbench_bindings AS bindings
        INNER JOIN batches AS runtime_batches ON runtime_batches.id = bindings.runtime_batch_id
        INNER JOIN qc_lot_config_items AS items ON items.id = bindings.lot_config_item_id
        INNER JOIN qc_lot_configs AS configs ON configs.id = bindings.lot_config_id
        INNER JOIN md_test_items AS tests ON tests.id = items.test_item_id
        INNER JOIN lab_instruments AS local ON local.id = configs.lab_instrument_id
        INNER JOIN md_qc_materials AS materials ON materials.id = configs.qc_material_id
        INNER JOIN md_qc_material_lots AS lots ON lots.id = configs.qc_material_lot_id
        INNER JOIN qc_lot_config_item_levels AS assigned
            ON assigned.lot_config_item_id = items.id AND assigned.is_disabled = 0
        INNER JOIN md_qc_levels AS levels ON levels.id = assigned.qc_level_id
        LEFT JOIN md_reagents AS reagents ON reagents.id = items.reagent_id
        LEFT JOIN md_units AS units ON units.id = items.unit_id
        LEFT JOIN md_methods AS methods ON methods.id = items.method_id
        WHERE bindings.qc_method = 'lj'
          AND bindings.binding_status = 'active'
          AND bindings.runtime_project_id = ?
          AND configs.status = 'active'
          AND configs.is_disabled = 0
          AND items.is_enabled = 1
          AND items.is_disabled = 0
        ),
        instant_batches AS (
        SELECT
            runtime_batches.id,
            runtime_batches.project_id,
            projects.name AS project_name,
            projects.input_value_type,
            runtime_batches.instrument,
            runtime_batches.reagent,
            runtime_batches.qc_material,
            runtime_batches.concentration,
            runtime_batches.lot_no,
            runtime_batches.target_n,
            runtime_batches.cv_limit,
            runtime_batches.created_at,
            'instant' AS source_method,
            COALESCE(runtime_batches.source_transfer_time, '') AS source_transfer_time,
            NULL AS v11_lot_config_id,
            NULL AS v11_lot_config_item_id,
            '由即时法转入' AS v11_config_name,
            NULL AS expiry_date,
            '' AS unit_symbol,
            '' AS method_name,
            'instant_transfer' AS v11_target_source,
            '即时法前 20 个有效点转入 LJ 建靶' AS quality_target_source_text
        FROM batches AS runtime_batches
        INNER JOIN projects ON projects.id = runtime_batches.project_id
        WHERE runtime_batches.project_id = ?
          AND runtime_batches.is_disabled = 0
          AND projects.method_type = 'lj'
          AND projects.is_disabled = 0
          AND LOWER(TRIM(COALESCE(runtime_batches.source_method, ''))) = 'instant'
        )
        SELECT * FROM v11_batches
        UNION ALL
        SELECT * FROM instant_batches
        ORDER BY expiry_date DESC, created_at DESC, id DESC
        """,
        (int(runtime_project_id), int(runtime_project_id)),
    )


def list_lj_workbench_configuration_issues() -> pd.DataFrame:
    return _read_dataframe(
        """
        SELECT
            configs.config_name,
            tests.chinese_name AS test_item_name,
            lots.lot_no,
            assigned.target_source,
            '当前 LJ 接入仅支持本批次建靶；人工或厂家靶值将在后续目标值接入中启用。' AS issue
        FROM qc_lot_config_items AS items
        INNER JOIN qc_lot_configs AS configs ON configs.id = items.lot_config_id
        INNER JOIN qc_project_templates AS templates ON templates.id = configs.template_id
        INNER JOIN md_test_items AS tests ON tests.id = items.test_item_id
        INNER JOIN md_qc_material_lots AS lots ON lots.id = configs.qc_material_lot_id
        INNER JOIN qc_lot_config_item_levels AS assigned
            ON assigned.lot_config_item_id = items.id AND assigned.is_disabled = 0
        WHERE items.qc_method = 'lj'
          AND items.is_enabled = 1
          AND items.is_disabled = 0
          AND configs.status = 'active'
          AND configs.is_disabled = 0
          AND templates.status = 'active'
          AND templates.is_disabled = 0
          AND assigned.target_source <> ?
        ORDER BY configs.config_name ASC, tests.chinese_name ASC
        """,
        (SUPPORTED_LJ_TARGET_SOURCE,),
    )
