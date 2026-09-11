from __future__ import annotations

import json
import sqlite3

import pandas as pd

from database import get_connection, get_instant_batch


def _configuration_sources(connection: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    candidates = connection.execute("""
        SELECT items.*, configs.revision_no, configs.config_name, configs.qc_material_lot_id,
               tests.chinese_name AS test_item_name, lots.lot_no
        FROM qc_lot_config_items items
        JOIN qc_lot_configs configs ON configs.id = items.lot_config_id
        JOIN qc_project_templates templates ON templates.id = configs.template_id
        JOIN md_test_items tests ON tests.id = items.test_item_id
        JOIN lab_instruments instruments ON instruments.id = configs.lab_instrument_id
        JOIN md_qc_materials materials ON materials.id = configs.qc_material_id
        JOIN md_qc_material_lots lots ON lots.id = configs.qc_material_lot_id
        JOIN md_reagents reagents ON reagents.id = items.reagent_id
        JOIN md_units units ON units.id = items.unit_id
        JOIN md_methods methods ON methods.id = items.method_id
        WHERE items.qc_method = 'instant' AND items.is_enabled = 1 AND items.is_disabled = 0
          AND configs.status = 'active' AND configs.is_disabled = 0
          AND templates.status = 'active' AND templates.is_disabled = 0
          AND tests.is_disabled = 0 AND instruments.is_disabled = 0
          AND materials.is_disabled = 0 AND lots.is_disabled = 0
          AND reagents.is_disabled = 0 AND units.is_disabled = 0 AND methods.is_disabled = 0
        ORDER BY items.id
    """).fetchall()
    sources, issues = [], []
    for row in candidates:
        def reject(message: str) -> None:
            issues.append({"config_name": row["config_name"], "test_item_name": row["test_item_name"],
                           "lot_no": row["lot_no"], "issue": message})

        levels = connection.execute("""
            SELECT assigned.* FROM qc_lot_config_item_levels assigned
            JOIN md_qc_levels levels ON levels.id = assigned.qc_level_id
            WHERE assigned.lot_config_item_id = ? AND assigned.is_disabled = 0
              AND levels.is_disabled = 0 AND levels.qc_material_lot_id = ?
            ORDER BY assigned.level_order, assigned.id
        """, (row["id"], row["qc_material_lot_id"])).fetchall()
        if row["level_count"] != 1 or len(levels) != 1 or row["target_n"] != 20:
            reject("即时法必须配置 1 个有效水平，建靶有效点数固定为 20。")
            continue
        if any(level["target_source"] != "building" for level in levels):
            reject("当前仅支持本批次建靶；人工、厂家及待确认靶值暂未接入。")
            continue
        snapshot = connection.execute("""
            SELECT id, snapshot_json FROM qc_config_snapshots
            WHERE lot_config_id = ? AND revision_no = ? ORDER BY id DESC LIMIT 1
        """, (row["lot_config_id"], row["revision_no"])).fetchone()
        if snapshot is None:
            reject("未找到当前配置快照，请在项目/批次管理中重新保存并启用。")
            continue
        payload = json.loads(snapshot["snapshot_json"])
        item = next((i for i in payload["items"] if i["id"] == row["id"]), None)
        live_level_ids = [level["qc_level_id"] for level in levels]
        if item is None or [level["qc_level_id"] for level in item["levels"]] != live_level_ids:
            reject("水平配置与启用快照不一致，请重新保存并启用。")
            continue
        if (item["qc_method"] != "instant" or item["level_count"] != 1 or item["target_n"] != 20
                or any(level["target_source"] != "building" for level in item["levels"])):
            reject("配置快照尚不满足即时法单水平、20 点本批次建靶条件。")
            continue
        config = payload["config"]
        sources.append({
            "lot_config_item_id": row["id"], "lot_config_id": row["lot_config_id"],
            "project_template_item_id": item["source_template_item_id"],
            "source_revision_no": row["revision_no"], "config_snapshot_id": snapshot["id"],
            "config_name": config["config_name"], "test_item_name": item["test_item_name"],
            "input_value_type": item["input_value_type"], "level_count": item["level_count"],
            "target_n": item["target_n"], "cv_limit": item["cv_limit"],
            "unit_symbol": item["unit_symbol"], "method_name": item["method_name"],
            "instrument_name": config["instrument_name"], "reagent_name": item["reagent_name"],
            "reagent_manufacturer_name": "", "qc_material_name": config["qc_material_name"],
            "qc_material_manufacturer_name": "", "lot_no": config["lot_no"],
            "expiry_date": config["expiry_date"], "levels": item["levels"],
            "concentration_label": item["levels"][0].get("concentration_label") or item["levels"][0]["level_name"],
            "quality_target_source_text": item["quality_target_source_text"],
            "identity": [config["lab_instrument_id"], config["qc_material_id"],
                         config["qc_material_lot_id"], item["test_item_id"], item["input_value_type"],
                         item["unit_id"], item["method_id"], item["reagent_id"],
                         item["level_count"], item["target_n"], item["cv_limit"], *live_level_ids],
        })
    return sources, issues


def sync_instant_workbench_bindings() -> list[dict]:
    """Materialize single-level configs; never rewrite an established result context."""
    with get_connection() as connection:
        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")
        connection.execute("UPDATE qc_workbench_bindings SET binding_status = 'inactive' WHERE qc_method = 'instant'")
        sources, issues = _configuration_sources(connection)
        for source in sources:
            binding = connection.execute(
                "SELECT * FROM qc_workbench_bindings WHERE lot_config_item_id = ?",
                (source["lot_config_item_id"],),
            ).fetchone()
            if binding is not None:
                previous = json.loads(binding["source_snapshot_json"])
                if binding["qc_method"] != "instant":
                    issues.append({"config_name": source["config_name"], "issue": "该配置已绑定其他质控方法，请新建配置。"})
                    continue
                if previous.get("input_value_type") != source["input_value_type"]:
                    issues.append({"config_name": source["config_name"], "issue": "已绑定项目的输入值类型不可更改，请新建模板项目。"})
                    continue
                has_results = connection.execute(
                    "SELECT 1 FROM instant_results WHERE batch_id = ? LIMIT 1", (binding["runtime_batch_id"],)
                ).fetchone() is not None
                if has_results and previous.get("identity") != source["identity"]:
                    issues.append({"config_name": source["config_name"], "issue": "已有检测记录，仪器、试剂、质控品、水平和计算配置不可变更，请新建批号配置。"})
                    continue
                if has_results:
                    source = previous
                project_id, batch_id = binding["runtime_project_id"], binding["runtime_batch_id"]
            else:
                existing = connection.execute("""
                    SELECT p.id FROM qc_workbench_bindings b
                    JOIN instant_projects p ON p.id = b.runtime_project_id
                    WHERE b.qc_method = 'instant' AND b.project_template_item_id = ?
                      AND p.input_value_type = ? ORDER BY b.id LIMIT 1
                """, (source["project_template_item_id"], source["input_value_type"])).fetchone()
                if existing:
                    project_id = existing["id"]
                else:
                    base = f"{source['test_item_name']}｜{source['instrument_name']}"
                    name, suffix = base, 0
                    while connection.execute("SELECT 1 FROM instant_projects WHERE lower(trim(name)) = lower(trim(?))", (name,)).fetchone():
                        suffix += 1
                        name = f"{base}｜{suffix}"
                    project_id = connection.execute(
                        "INSERT INTO instant_projects (name, input_value_type) VALUES (?, ?)",
                        (name, source["input_value_type"]),
                    ).lastrowid
                batch_id = None
            values = (project_id, source["input_value_type"], source["instrument_name"], source["reagent_name"],
                      source["qc_material_name"], source["concentration_label"], source["lot_no"])
            if batch_id is None:
                batch_id = connection.execute("""
                    INSERT INTO instant_batches (project_id, input_value_type, instrument, reagent, qc_material, concentration, lot_no)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, values).lastrowid
            else:
                connection.execute("""
                    UPDATE instant_batches SET project_id = ?, input_value_type = ?, instrument = ?, reagent = ?,
                        qc_material = ?, concentration = ?, lot_no = ? WHERE id = ?
                """, (*values, batch_id))
            connection.execute("""
                INSERT INTO qc_workbench_bindings (qc_method, project_template_item_id, lot_config_id, lot_config_item_id,
                    runtime_project_id, runtime_batch_id, binding_status, source_revision_no, source_snapshot_json)
                VALUES ('instant', ?, ?, ?, ?, ?, 'active', ?, ?)
                ON CONFLICT(lot_config_item_id) DO UPDATE SET binding_status = 'active',
                    source_revision_no = excluded.source_revision_no, source_snapshot_json = excluded.source_snapshot_json,
                    updated_at = CURRENT_TIMESTAMP
            """, (source["project_template_item_id"], source["lot_config_id"], source["lot_config_item_id"],
                  project_id, batch_id, source["source_revision_no"], json.dumps(source, ensure_ascii=False)))
        return issues


def list_instant_workbench_projects() -> pd.DataFrame:
    with get_connection() as connection:
        rows = connection.execute("""
            SELECT b.* FROM qc_workbench_bindings b
            JOIN instant_projects p ON p.id = b.runtime_project_id
            JOIN instant_batches batches ON batches.id = b.runtime_batch_id
            WHERE b.qc_method = 'instant' AND b.binding_status = 'active'
              AND p.is_disabled = 0 AND batches.is_disabled = 0 ORDER BY b.id
        """).fetchall()
    projects = {}
    for row in rows:
        source = json.loads(row["source_snapshot_json"])
        projects.setdefault(row["runtime_project_id"], {
            "id": row["runtime_project_id"], "name": source["test_item_name"],
            "input_value_type": source["input_value_type"], "instrument_name": source["instrument_name"],
        })
    return pd.DataFrame(projects.values(), columns=["id", "name", "input_value_type", "instrument_name"])


def list_instant_workbench_batches(project_id: int) -> pd.DataFrame:
    with get_connection() as connection:
        rows = connection.execute("""
            SELECT b.runtime_batch_id FROM qc_workbench_bindings b
            JOIN instant_batches batches ON batches.id = b.runtime_batch_id
            WHERE b.qc_method = 'instant' AND b.binding_status = 'active' AND b.runtime_project_id = ?
              AND batches.is_disabled = 0 ORDER BY b.id DESC
        """, (project_id,)).fetchall()
    return pd.DataFrame([dict(get_instant_batch(row[0])) for row in rows])
