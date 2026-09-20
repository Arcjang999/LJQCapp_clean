from __future__ import annotations

import json
import sqlite3

import pandas as pd

from database import get_connection, get_zscore_batch
from services.workbench_config_service import _materialize_runtime_batch


def _configuration_sources(connection: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    candidates = connection.execute("""
        SELECT items.*, configs.revision_no, configs.config_name, configs.qc_material_lot_id, configs.material_selection_mode,
               tests.chinese_name AS test_item_name, lots.lot_no
        FROM qc_lot_config_items items
        JOIN qc_lot_configs configs ON configs.id = items.lot_config_id
        JOIN qc_project_templates templates ON templates.id = configs.template_id
        JOIN md_test_items tests ON tests.id = items.test_item_id
        JOIN lab_instruments instruments ON instruments.id = configs.lab_instrument_id
        JOIN md_qc_materials materials ON materials.id = configs.qc_material_id
        JOIN md_qc_material_lots lots ON lots.id = configs.qc_material_lot_id
        WHERE items.qc_method = 'zscore' AND items.is_enabled = 1 AND items.is_disabled = 0
          AND configs.status = 'active' AND configs.is_disabled = 0
          AND templates.status = 'active' AND templates.is_disabled = 0
          AND tests.is_disabled = 0 AND instruments.is_disabled = 0
          AND materials.is_disabled = 0 AND (configs.material_selection_mode = 1 OR lots.is_disabled = 0)
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
              AND levels.is_disabled = 0 AND (?=1 OR levels.qc_material_lot_id = ? OR EXISTS (SELECT 1 FROM qc_level_combination_members m WHERE m.lot_config_item_id=assigned.lot_config_item_id AND m.qc_level_id=levels.id))
            ORDER BY assigned.level_order, assigned.id
        """, (row["id"], row["material_selection_mode"], row["qc_material_lot_id"])).fetchall()
        if row["level_count"] not in (2, 3) or len(levels) != row["level_count"]:
            reject("有效水平必须完整匹配项目的 2 或 3 个水平。")
            continue
        if any((level["target_source"] not in ("building","manual","manufacturer") or not level["target_confirmed"]) for level in levels):
            reject("所有水平均须使用均值和标准差建立或已确认的人工／厂家参数；复制待确认参数不能使用。")
            continue
        snapshot = connection.execute("""
            SELECT id, snapshot_json FROM qc_config_snapshots
            WHERE lot_config_id = ? AND revision_no = ? ORDER BY id DESC LIMIT 1
        """, (row["lot_config_id"], row["revision_no"])).fetchone()
        if snapshot is None:
            reject("当前配置尚未保存完成，请在项目/批次管理中重新保存并启用。")
            continue
        payload = json.loads(snapshot["snapshot_json"])
        item = next((i for i in payload["items"] if i["id"] == row["id"]), None)
        live_level_ids = [level["qc_level_id"] for level in levels]
        if item is None or [level["qc_level_id"] for level in item["levels"]] != live_level_ids:
            reject("水平设置在启用后发生变化，请重新保存并启用。")
            continue
        if (item["qc_method"] != "zscore" or item["level_count"] != len(levels)
                or any((level["target_source"] not in ("building","manual","manufacturer") or not level["target_confirmed"]) for level in item["levels"])):
            reject("当前配置未完成，请检查水平数量、均值和标准差来源及参数确认。")
            continue
        config = payload["config"]
        from services.material_workflow_service import concentration_label,validate_material_selection
        try: validate_material_selection(connection,config['qc_material_id'],live_level_ids,row['level_count'])
        except ValueError as exc:
            reject(str(exc));continue
        sources.append({
            "lot_config_item_id": row["id"], "lot_config_id": row["lot_config_id"],
            "project_template_item_id": item["source_template_item_id"],
            "source_revision_no": row["revision_no"], "config_snapshot_id": snapshot["id"],
            "config_name": config["config_name"], "test_item_name": item["test_item_name"],
            "input_value_type": item["input_value_type"], "level_count": item["level_count"],
            "target_n": item["target_n"], "cv_limit": item["cv_limit"],
            "quality_goal_json": item.get("quality_goal_json", "{}"),
            "quality_review_json": item.get("quality_review_json", "{}"),
            "unit_symbol": item["unit_symbol"], "method_name": item["method_name"],
            "instrument_name": config["instrument_name"], "reagent_name": item["reagent_name"],
            "reagent_manufacturer_name": "", "qc_material_name": config["qc_material_name"],
            "qc_material_manufacturer_name": "", "lot_no": " / ".join(dict.fromkeys(level.get("lot_no") or config["lot_no"] for level in item["levels"])),
            "expiry_date": min((l.get("expiry_date") or config["expiry_date"]) for l in item["levels"]), "levels": item["levels"],
            "concentration_label": " / ".join(concentration_label(level) for level in item["levels"]),
            "quality_target_source_text": item["quality_target_source_text"],
            "identity": [config["lab_instrument_id"], config["qc_material_id"],
                         config["qc_material_lot_id"], item["test_item_id"], item["input_value_type"],
                         item["unit_id"], item["method_id"], item["reagent_id"],
                         item["level_count"], item["target_n"], item["cv_limit"], *live_level_ids],
        })
    return sources, issues


def sync_zscore_workbench_bindings() -> list[dict]:
    """Bind active configurations atomically; existing results keep their original context."""
    with get_connection() as connection:
        connection.execute("UPDATE qc_workbench_bindings SET binding_status = 'inactive' WHERE qc_method = 'zscore'")
        sources, issues = _configuration_sources(connection)
        for source in sources:
            binding = connection.execute("SELECT * FROM qc_workbench_bindings WHERE lot_config_item_id = ?",
                                         (source["lot_config_item_id"],)).fetchone()
            if binding is not None and binding["qc_method"] != "zscore":
                issues.append({"config_name": source["config_name"], "issue": "该批次已用于其他质控方法，请新建批次。"})
                continue
            if binding is not None:
                previous = json.loads(binding["source_snapshot_json"])
                if (previous.get("input_value_type") != source["input_value_type"]
                        or previous.get("level_count") != source["level_count"]):
                    issues.append({"config_name": source["config_name"], "issue": "已使用项目的输入值类型和水平数不能变更，请新建项目与批次。"})
                    continue
                has_results = connection.execute("SELECT 1 FROM zscore_runs WHERE batch_id = ? LIMIT 1",
                                                 (binding["runtime_batch_id"],)).fetchone() is not None
                if has_results and previous.get("identity") != source["identity"]:
                    issues.append({"config_name": source["config_name"], "issue": "已有检测记录，仪器、方法、单位、水平及参数建立要求不能变更。请新建批次。"})
                    continue
                if has_results:
                    source = previous
                project_id, batch_id = binding["runtime_project_id"], binding["runtime_batch_id"]
            else:
                existing = connection.execute("""
                    SELECT p.id FROM qc_workbench_bindings b
                    JOIN projects p ON p.id = b.runtime_project_id
                    JOIN zscore_project_config z ON z.project_id = p.id
                    WHERE b.qc_method = 'zscore' AND b.project_template_item_id = ?
                      AND p.input_value_type = ? AND z.level_count = ?
                    ORDER BY b.id LIMIT 1
                """, (source["project_template_item_id"], source["input_value_type"], source["level_count"])).fetchone()
                if existing:
                    project_id = existing["id"]
                else:
                    base = f"{source['test_item_name']}｜{source['instrument_name']}"
                    name, suffix = base, 0
                    while connection.execute("SELECT 1 FROM projects WHERE method_type = 'zscore' AND lower(trim(name)) = lower(trim(?))", (name,)).fetchone():
                        suffix += 1
                        name = f"{base}｜{suffix}"
                    project_id = connection.execute("INSERT INTO projects (name, method_type, input_value_type) VALUES (?, 'zscore', ?)",
                                                    (name, source["input_value_type"])).lastrowid
                    connection.execute("INSERT INTO zscore_project_config (project_id, level_count) VALUES (?, ?)",
                                       (project_id, source["level_count"]))
                batch_id = None
            connection.execute("UPDATE projects SET is_disabled = 0 WHERE id = ?", (project_id,))
            batch_id = _materialize_runtime_batch(connection, source=source, runtime_project_id=project_id,
                                                 existing_batch_id=batch_id)
            labels = [level["level_name"] for level in source["levels"]]
            labels += [None] * (3 - len(labels))
            connection.execute("""
                INSERT INTO zscore_batch_config (batch_id, project_id, level_count, level_1_label, level_2_label, level_3_label, effective_building_count)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(batch_id) DO UPDATE SET level_count = excluded.level_count,
                    level_1_label = excluded.level_1_label, level_2_label = excluded.level_2_label,
                    level_3_label = excluded.level_3_label
            """, (batch_id, project_id, source["level_count"], *labels))
            connection.execute("""
                INSERT INTO qc_workbench_bindings (qc_method, project_template_item_id, lot_config_id, lot_config_item_id,
                    runtime_project_id, runtime_batch_id, binding_status, source_revision_no, source_snapshot_json)
                VALUES ('zscore', ?, ?, ?, ?, ?, 'active', ?, ?)
                ON CONFLICT(lot_config_item_id) DO UPDATE SET binding_status = 'active',
                    source_revision_no = excluded.source_revision_no, source_snapshot_json = excluded.source_snapshot_json,
                    updated_at = CURRENT_TIMESTAMP
            """, (source["project_template_item_id"], source["lot_config_id"], source["lot_config_item_id"],
                  project_id, batch_id, source["source_revision_no"], json.dumps(source, ensure_ascii=False)))
        return issues


def list_zscore_workbench_projects() -> pd.DataFrame:
    with get_connection() as connection:
        rows = connection.execute("SELECT * FROM qc_workbench_bindings WHERE qc_method = 'zscore' AND binding_status = 'active' ORDER BY id").fetchall()
    projects = {}
    for row in rows:
        source = json.loads(row["source_snapshot_json"])
        projects.setdefault(row["runtime_project_id"], {
            "id": row["runtime_project_id"], "name": source["test_item_name"],
            "input_value_type": source["input_value_type"], "level_count": source["level_count"],
            "instrument_name": source["instrument_name"],
        })
    return pd.DataFrame(projects.values(), columns=["id", "name", "input_value_type", "level_count", "instrument_name"])


def list_zscore_workbench_batches(project_id: int) -> pd.DataFrame:
    with get_connection() as connection:
        ids = connection.execute("SELECT runtime_batch_id FROM qc_workbench_bindings WHERE qc_method = 'zscore' AND binding_status = 'active' AND runtime_project_id = ? ORDER BY id DESC", (project_id,)).fetchall()
    return pd.DataFrame([dict(get_zscore_batch(row[0])) for row in ids])
