from __future__ import annotations

import sqlite3
import json

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
            items.test_item_id, items.unit_id, items.method_id, items.reagent_id,
            items.target_n,
            items.cv_limit,
            items.quality_target_source_text,
            items.quality_goal_json,
            items.quality_review_json,
            configs.revision_no AS source_revision_no,
            configs.config_name,
            configs.lab_instrument_id, configs.qc_material_id, levels.qc_material_lot_id,
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
            levels.level_code, levels.specification_id,
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
        INNER JOIN qc_lot_config_item_levels AS assigned
            ON assigned.lot_config_item_id = items.id
           AND assigned.is_disabled = 0
        INNER JOIN md_qc_levels AS levels
            ON levels.id = assigned.qc_level_id
           AND levels.is_disabled = 0
        INNER JOIN md_qc_material_lots AS lots ON lots.id=levels.qc_material_lot_id
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
          AND (configs.material_selection_mode=1 OR levels.qc_material_lot_id = configs.qc_material_lot_id)
          AND lots.qc_material_id=configs.qc_material_id
          AND COALESCE(reagents.is_disabled, 1) = 0
          AND COALESCE(units.is_disabled, 1) = 0
          AND COALESCE(methods.is_disabled, 1) = 0
          AND (assigned.target_source = ? OR (assigned.target_source IN ('manual','manufacturer') AND assigned.target_confirmed=1))
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
    from services.material_workflow_service import concentration_label
    concentration = concentration_label(source) if source.get('level_code') else _clean_text(source["concentration_label"] or source["level_name"], "-")
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
    """Keep established LJ batch context immutable while preserving runtime IDs."""
    from database import atomic_write
    with atomic_write() as connection:
        connection.execute("UPDATE qc_workbench_bindings SET binding_status = 'inactive' WHERE qc_method = 'lj'")
        accepted = 0
        for row in _active_lj_source_rows(connection):
            source = dict(row)
            source["identity"] = [source[k] for k in ("lab_instrument_id", "qc_material_id", "qc_material_lot_id",
                "test_item_id", "input_value_type", "unit_id", "method_id", "reagent_id", "qc_level_id", "target_n", "cv_limit")]
            source["levels"] = [{"qc_level_id": source["qc_level_id"], "level_order": 1,
                                 "level_name": source["level_name"], "level_code": source.get("level_code", ""), "specification_id": source.get("specification_id"), "expiry_date": source["expiry_date"], "qc_material_lot_id": source["qc_material_lot_id"],
                                 "lot_no": source["lot_no"], "target_source": source["target_source"],"target_mean":source["target_mean"],"target_sd":source["target_sd"]}]
            snapshot = connection.execute("SELECT id FROM qc_config_snapshots WHERE lot_config_id = ? AND revision_no = ? ORDER BY id DESC LIMIT 1",
                (source["lot_config_id"], source["source_revision_no"])).fetchone()
            source["config_snapshot_id"] = snapshot[0] if snapshot else None
            binding = connection.execute("SELECT * FROM qc_workbench_bindings WHERE lot_config_item_id = ?", (source["lot_config_item_id"],)).fetchone()
            if binding is not None and binding["qc_method"] != "lj":
                continue
            if binding is not None:
                previous = json.loads(binding["source_snapshot_json"] or "{}")
                has_results = connection.execute("SELECT 1 FROM results WHERE batch_id = ? LIMIT 1", (binding["runtime_batch_id"],)).fetchone()
                if previous and previous["input_value_type"] != source["input_value_type"]:
                    continue
                if has_results and previous and previous["identity"] != source["identity"]:
                    continue
                if has_results and previous:
                    source = previous
                elif has_results:
                    # No original snapshot existed: preserve available runtime information and label it honestly.
                    runtime = connection.execute("SELECT * FROM batches WHERE id = ?", (binding["runtime_batch_id"],)).fetchone()
                    for field, target in (("instrument", "instrument_name"), ("reagent", "reagent_name"), ("qc_material", "qc_material_name"),
                                          ("concentration", "concentration_label"), ("lot_no", "lot_no"), ("target_n", "target_n"), ("cv_limit", "cv_limit")):
                        source[target] = runtime[field]
                    source["provenance"] = "migration_available"
                project_id, batch_id = binding["runtime_project_id"], binding["runtime_batch_id"]
            else:
                project_id, batch_id = _resolve_runtime_project_id(connection, source), None
            batch_id = _materialize_runtime_batch(connection, source=source, runtime_project_id=project_id, existing_batch_id=batch_id)
            payload = json.dumps(source, ensure_ascii=False)
            connection.execute("UPDATE batches SET source_config_snapshot_json = ? WHERE id = ?", (payload, batch_id))
            connection.execute("""INSERT INTO qc_workbench_bindings
                (qc_method,project_template_item_id,lot_config_id,lot_config_item_id,runtime_project_id,runtime_batch_id,
                 binding_status,source_revision_no,source_snapshot_json)
                VALUES ('lj',?,?,?,?,?,'active',?,?) ON CONFLICT(lot_config_item_id) DO UPDATE SET
                binding_status='active',source_revision_no=excluded.source_revision_no,
                source_snapshot_json=excluded.source_snapshot_json,updated_at=CURRENT_TIMESTAMP""",
                (source["project_template_item_id"],source["lot_config_id"],source["lot_config_item_id"],project_id,batch_id,source["source_revision_no"],payload))
            accepted += 1
        return accepted


def list_lj_workbench_projects() -> pd.DataFrame:
    sync_lj_workbench_bindings()
    with get_connection() as connection:
        rows = connection.execute("""SELECT p.*, b.id AS batch_id, b.source_method,
             b.instrument, b.qc_material, b.source_config_snapshot_json FROM projects p JOIN batches b ON b.project_id=p.id
             LEFT JOIN qc_workbench_bindings binding ON binding.runtime_batch_id=b.id AND binding.qc_method='lj'
             WHERE p.method_type='lj' AND p.is_disabled=0 AND b.is_disabled=0
               AND (binding.binding_status='active' OR b.source_method='instant') ORDER BY p.id,b.id""").fetchall()
    projects = {}
    for row in rows:
        source = json.loads(row["source_config_snapshot_json"] or "{}")
        project = projects.setdefault(row["id"], {"id": row["id"], "name": source.get("test_item_name",row["name"]),
            "input_value_type": row["input_value_type"], "instrument_name": row["instrument"],
            "qc_material_name": row["qc_material"], "unit_symbol": source.get("unit_symbol", ""),
            "method_name": source.get("method_name", ""), "batch_count": 0, "created_at": row["created_at"],
            "is_from_instant": int(row["source_method"] == 'instant')})
        project["batch_count"] += 1
    return pd.DataFrame(projects.values(), columns=["id","name","input_value_type","instrument_name","qc_material_name",
        "unit_symbol","method_name","batch_count","created_at","is_from_instant"])


def list_lj_workbench_batches(runtime_project_id: int) -> pd.DataFrame:
    from database import get_batch
    sync_lj_workbench_bindings()
    with get_connection() as connection:
        ids = connection.execute("""SELECT b.id FROM batches b
            LEFT JOIN qc_workbench_bindings binding ON binding.runtime_batch_id=b.id AND binding.qc_method='lj'
            WHERE b.project_id=? AND b.is_disabled=0 AND (binding.binding_status='active' OR b.source_method='instant')
            ORDER BY b.id DESC""", (runtime_project_id,)).fetchall()
    rows = []
    for entry in ids:
        row = dict(get_batch(entry[0]))
        row["expiry_date"] = row["v11_expiry_date"]
        if row["source_method"] == "instant":
            row["v11_config_name"] = "由即时法转入"
            row["v11_target_source"] = "instant_transfer"
        rows.append(row)
    return pd.DataFrame(rows)


def list_lj_workbench_configuration_issues() -> pd.DataFrame:
    return _read_dataframe(
        """
        SELECT
            configs.config_name,
            tests.chinese_name AS test_item_name,
            lots.lot_no,
            assigned.target_source,
            '控制参数尚未完整确认，请先完成水平配置。人工或厂家赋值另需在均值和标准差管理中确认依据、人员和生效时间。' AS issue
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
          AND assigned.target_source <> ? AND (assigned.target_source NOT IN ('manual','manufacturer') OR assigned.target_confirmed=0)
        ORDER BY configs.config_name ASC, tests.chinese_name ASC
        """,
        (SUPPORTED_LJ_TARGET_SOURCE,),
    )
