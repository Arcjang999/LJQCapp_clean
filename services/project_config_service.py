from __future__ import annotations

import json
import sqlite3
from typing import Any
from uuid import uuid4

import pandas as pd

from database import get_connection


QC_METHOD_LABELS = {
    "lj": "单水平（LJ法）",
    "zscore": "多水平（Z-score法）",
    "instant": "即时法",
}

INPUT_VALUE_TYPE_LABELS = {
    "raw": "真实检测值",
    "ct": "Ct值",
    "log": "log值",
}

TARGET_SOURCE_LABELS = {
    "building": "建靶计算",
    "manufacturer": "厂家赋值",
    "manual": "手工设定",
    "copied_pending": "复制待确认",
}


def _new_uid() -> str:
    return str(uuid4())


def _clean_required(value: object, label: str) -> str:
    cleaned = " ".join(str(value or "").split()).strip()
    if not cleaned:
        raise ValueError(f"{label}不能为空。")
    return cleaned


def _clean_optional(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _read_dataframe(sql: str, params: tuple[object, ...] = ()) -> pd.DataFrame:
    with get_connection() as connection:
        return pd.read_sql_query(sql, connection, params=params)


def create_project_template(
    *,
    template_name: str,
    lab_instrument_id: int,
    qc_material_id: int,
    notes: str = "",
) -> int:
    with get_connection() as connection:
        instrument = connection.execute(
            """
            SELECT department_name
            FROM lab_instruments
            WHERE id = ? AND is_disabled = 0
            """,
            (int(lab_instrument_id),),
        ).fetchone()
        if instrument is None:
            raise ValueError("请选择启用中的本地仪器。")
        material = connection.execute(
            "SELECT id FROM md_qc_materials WHERE id = ? AND is_disabled = 0",
            (int(qc_material_id),),
        ).fetchone()
        if material is None:
            raise ValueError("请选择启用中的质控品。")
        try:
            cursor = connection.execute(
                """
                INSERT INTO qc_project_templates (
                    uid, origin_type, template_name, lab_instrument_id,
                    qc_material_id, department_name_snapshot, notes
                )
                VALUES (?, 'hospital', ?, ?, ?, ?, ?)
                """,
                (
                    _new_uid(),
                    _clean_required(template_name, "模板名称"),
                    int(lab_instrument_id),
                    int(qc_material_id),
                    str(instrument["department_name"] or "").strip(),
                    _clean_optional(notes),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("已存在同名的启用项目模板。") from exc
        return int(cursor.lastrowid)


def list_project_templates(include_disabled: bool = False) -> pd.DataFrame:
    where_clause = "" if include_disabled else "WHERE templates.is_disabled = 0"
    return _read_dataframe(
        f"""
        SELECT
            templates.id,
            templates.uid,
            templates.template_name,
            local.display_name AS instrument_name,
            manufacturers.display_name AS qc_manufacturer_name,
            materials.generic_name AS qc_material_name,
            materials.trade_name AS qc_material_trade_name,
            templates.status,
            templates.revision_no,
            templates.is_disabled,
            templates.created_at,
            COUNT(
                CASE WHEN items.is_disabled = 0 THEN 1 END
            ) AS item_count
        FROM qc_project_templates AS templates
        LEFT JOIN lab_instruments AS local ON local.id = templates.lab_instrument_id
        LEFT JOIN md_qc_materials AS materials ON materials.id = templates.qc_material_id
        LEFT JOIN md_manufacturers AS manufacturers ON manufacturers.id = materials.manufacturer_id
        LEFT JOIN qc_project_template_items AS items ON items.template_id = templates.id
        {where_clause}
        GROUP BY templates.id
        ORDER BY templates.is_disabled ASC, templates.updated_at DESC, templates.id DESC
        """
    )


def get_project_template(template_id: int) -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                templates.*,
                local.display_name AS instrument_name,
                local.department_name,
                models.model AS instrument_model,
                instrument_manufacturers.display_name AS instrument_manufacturer_name,
                materials.generic_name AS qc_material_name,
                materials.trade_name AS qc_material_trade_name,
                qc_manufacturers.display_name AS qc_manufacturer_name
            FROM qc_project_templates AS templates
            LEFT JOIN lab_instruments AS local ON local.id = templates.lab_instrument_id
            LEFT JOIN md_instrument_models AS models ON models.id = local.instrument_model_id
            LEFT JOIN md_manufacturers AS instrument_manufacturers
                ON instrument_manufacturers.id = models.manufacturer_id
            LEFT JOIN md_qc_materials AS materials ON materials.id = templates.qc_material_id
            LEFT JOIN md_manufacturers AS qc_manufacturers
                ON qc_manufacturers.id = materials.manufacturer_id
            WHERE templates.id = ?
            """,
            (int(template_id),),
        ).fetchone()
    if row is None:
        raise ValueError("未找到项目模板。")
    return row


def list_template_items(template_id: int, include_disabled: bool = False) -> pd.DataFrame:
    disabled_clause = "" if include_disabled else "AND items.is_disabled = 0"
    return _read_dataframe(
        f"""
        SELECT
            items.id,
            items.uid,
            items.template_id,
            items.test_item_id,
            tests.standard_code,
            tests.chinese_name AS test_item_name,
            tests.abbreviation,
            items.qc_method,
            items.input_value_type,
            items.unit_id,
            units.symbol AS unit_symbol,
            items.method_id,
            methods.method_name,
            items.reagent_id,
            reagents.generic_name AS reagent_name,
            reagents.trade_name AS reagent_trade_name,
            items.level_count,
            items.target_n,
            items.cv_limit,
            items.quality_target_source_text,
            items.sort_order,
            items.notes,
            items.is_disabled
        FROM qc_project_template_items AS items
        INNER JOIN md_test_items AS tests ON tests.id = items.test_item_id
        LEFT JOIN md_units AS units ON units.id = items.unit_id
        LEFT JOIN md_methods AS methods ON methods.id = items.method_id
        LEFT JOIN md_reagents AS reagents ON reagents.id = items.reagent_id
        WHERE items.template_id = ?
        {disabled_clause}
        ORDER BY items.is_disabled ASC, items.sort_order ASC, items.id ASC
        """,
        (int(template_id),),
    )


def _normalize_template_item(row: dict[str, object], sort_order: int) -> dict[str, object]:
    qc_method = str(row.get("qc_method") or "").strip().lower()
    if qc_method not in QC_METHOD_LABELS:
        raise ValueError("质控方法必须为 LJ、Z-score 或即时法。")
    input_value_type = str(row.get("input_value_type") or "").strip().lower()
    if input_value_type not in INPUT_VALUE_TYPE_LABELS:
        raise ValueError("输入值类型必须为真实检测值、Ct值或log值。")

    level_count = int(row.get("level_count") or 1)
    target_n = int(row.get("target_n") or 20)
    if qc_method in {"lj", "instant"} and level_count != 1:
        raise ValueError(f"{QC_METHOD_LABELS[qc_method]}只能配置 1 个水平。")
    if qc_method == "zscore" and level_count not in {2, 3}:
        raise ValueError("Z-score 只能配置 2 或 3 个水平。")
    if qc_method == "instant":
        target_n = 20
    elif not 5 <= target_n <= 20:
        raise ValueError("LJ 和 Z-score 建靶有效点数必须在 5 至 20 之间。")

    cv_limit = _optional_float(row.get("cv_limit"))
    if cv_limit is not None and cv_limit <= 0:
        raise ValueError("CV 要求必须大于 0。")

    return {
        "test_item_id": int(row["test_item_id"]),
        "qc_method": qc_method,
        "input_value_type": input_value_type,
        "unit_id": int(row["unit_id"]) if row.get("unit_id") not in (None, "") else None,
        "method_id": int(row["method_id"]) if row.get("method_id") not in (None, "") else None,
        "reagent_id": int(row["reagent_id"]) if row.get("reagent_id") not in (None, "") else None,
        "level_count": level_count,
        "target_n": target_n,
        "cv_limit": cv_limit,
        "quality_target_source_text": _clean_optional(row.get("quality_target_source_text")),
        "sort_order": int(row.get("sort_order") or sort_order),
        "notes": _clean_optional(row.get("notes")),
    }


def save_template_items(template_id: int, rows: list[dict[str, object]]) -> None:
    normalized_rows = [
        _normalize_template_item(dict(row), sort_order=index)
        for index, row in enumerate(rows, start=1)
    ]
    normalized_keys = {
        (
            int(row["test_item_id"]),
            str(row["qc_method"]),
            str(row["input_value_type"]),
        )
        for row in normalized_rows
    }
    if len(normalized_keys) != len(normalized_rows):
        raise ValueError("同一模板内存在重复的项目、质控方法和输入值类型组合。")

    with get_connection() as connection:
        template = connection.execute(
            "SELECT id FROM qc_project_templates WHERE id = ? AND is_disabled = 0",
            (int(template_id),),
        ).fetchone()
        if template is None:
            raise ValueError("未找到启用中的项目模板。")

        connection.execute(
            """
            UPDATE qc_project_template_items
            SET is_disabled = 1,
                disabled_at = CURRENT_TIMESTAMP,
                disabled_reason = '已从模板当前配置移除',
                updated_at = CURRENT_TIMESTAMP
            WHERE template_id = ? AND is_disabled = 0
            """,
            (int(template_id),),
        )

        for row in normalized_rows:
            connection.execute(
                """
                INSERT INTO qc_project_template_items (
                    uid, origin_type, template_id, test_item_id, qc_method,
                    input_value_type, unit_id, method_id, reagent_id,
                    level_count, target_n, cv_limit, quality_target_source_text,
                    sort_order, notes
                )
                VALUES (
                    ?, 'hospital', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(template_id, test_item_id, qc_method, input_value_type)
                DO UPDATE SET
                    unit_id = excluded.unit_id,
                    method_id = excluded.method_id,
                    reagent_id = excluded.reagent_id,
                    level_count = excluded.level_count,
                    target_n = excluded.target_n,
                    cv_limit = excluded.cv_limit,
                    quality_target_source_text = excluded.quality_target_source_text,
                    sort_order = excluded.sort_order,
                    notes = excluded.notes,
                    is_disabled = 0,
                    disabled_at = NULL,
                    disabled_reason = '',
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    _new_uid(),
                    int(template_id),
                    row["test_item_id"],
                    row["qc_method"],
                    row["input_value_type"],
                    row["unit_id"],
                    row["method_id"],
                    row["reagent_id"],
                    row["level_count"],
                    row["target_n"],
                    row["cv_limit"],
                    row["quality_target_source_text"],
                    row["sort_order"],
                    row["notes"],
                ),
            )

        connection.execute(
            """
            UPDATE qc_project_templates
            SET status = 'draft',
                revision_no = revision_no + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (int(template_id),),
        )


def validate_project_template(template_id: int) -> list[str]:
    template = get_project_template(template_id)
    items = list_template_items(template_id)
    errors: list[str] = []
    if template["lab_instrument_id"] is None:
        errors.append("未选择本地仪器。")
    if template["qc_material_id"] is None:
        errors.append("未选择质控品。")
    if items.empty:
        errors.append("至少需要配置 1 个检验项目。")
        return errors

    for _, row in items.iterrows():
        item_name = str(row["test_item_name"])
        if pd.isna(row["unit_id"]):
            errors.append(f"{item_name}：未配置单位。")
        if pd.isna(row["method_id"]):
            errors.append(f"{item_name}：未配置方法学。")
        if pd.isna(row["reagent_id"]):
            errors.append(f"{item_name}：未配置试剂。")
    return errors


def activate_project_template(template_id: int) -> None:
    errors = validate_project_template(template_id)
    if errors:
        raise ValueError("模板暂不能启用：\n" + "\n".join(f"- {item}" for item in errors))
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE qc_project_templates
            SET status = 'active',
                revision_no = revision_no + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND is_disabled = 0
            """,
            (int(template_id),),
        )


def set_project_template_disabled(
    template_id: int,
    *,
    is_disabled: bool,
    reason: str = "",
) -> None:
    try:
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE qc_project_templates
                SET is_disabled = ?,
                    status = CASE WHEN ? = 1 THEN 'draft' ELSE status END,
                    disabled_at = CASE WHEN ? = 1 THEN CURRENT_TIMESTAMP ELSE NULL END,
                    disabled_reason = CASE WHEN ? = 1 THEN ? ELSE '' END,
                    revision_no = revision_no + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    int(bool(is_disabled)),
                    int(bool(is_disabled)),
                    int(bool(is_disabled)),
                    int(bool(is_disabled)),
                    _clean_optional(reason),
                    int(template_id),
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError("存在同名启用模板，当前模板不能恢复。") from exc


def create_lot_config_from_template(
    *,
    template_id: int,
    qc_material_lot_id: int,
    config_name: str = "",
) -> int:
    with get_connection() as connection:
        template = connection.execute(
            """
            SELECT *
            FROM qc_project_templates
            WHERE id = ? AND is_disabled = 0 AND status = 'active'
            """,
            (int(template_id),),
        ).fetchone()
        if template is None:
            raise ValueError("请选择已启用的项目模板。")
        lot = connection.execute(
            """
            SELECT lots.*, materials.generic_name AS material_name
            FROM md_qc_material_lots AS lots
            INNER JOIN md_qc_materials AS materials ON materials.id = lots.qc_material_id
            WHERE lots.id = ? AND lots.is_disabled = 0
            """,
            (int(qc_material_lot_id),),
        ).fetchone()
        if lot is None:
            raise ValueError("请选择启用中的质控品批号。")
        if int(lot["qc_material_id"]) != int(template["qc_material_id"]):
            raise ValueError("所选质控品批号与项目模板的质控品不一致。")
        items = connection.execute(
            """
            SELECT *
            FROM qc_project_template_items
            WHERE template_id = ? AND is_disabled = 0
            ORDER BY sort_order ASC, id ASC
            """,
            (int(template_id),),
        ).fetchall()
        if not items:
            raise ValueError("项目模板中没有可复制的启用项目。")

        normalized_config_name = _clean_optional(config_name)
        if not normalized_config_name:
            normalized_config_name = (
                f"{template['template_name']}｜{lot['material_name']}｜{lot['lot_no']}"
            )
        try:
            cursor = connection.execute(
                """
                INSERT INTO qc_lot_configs (
                    uid, origin_type, template_id, qc_material_lot_id,
                    lab_instrument_id, qc_material_id, config_name
                )
                VALUES (?, 'hospital', ?, ?, ?, ?, ?)
                """,
                (
                    _new_uid(),
                    int(template_id),
                    int(qc_material_lot_id),
                    int(template["lab_instrument_id"]),
                    int(template["qc_material_id"]),
                    normalized_config_name,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("当前模板与质控品批号已存在启用配置。") from exc
        lot_config_id = int(cursor.lastrowid)

        for item in items:
            connection.execute(
                """
                INSERT INTO qc_lot_config_items (
                    uid, origin_type, lot_config_id, source_template_item_id,
                    test_item_id, qc_method, input_value_type,
                    unit_id, method_id, reagent_id, level_count, target_n,
                    cv_limit, quality_target_source_text, sort_order, notes
                )
                VALUES (
                    ?, 'hospital', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    _new_uid(),
                    lot_config_id,
                    int(item["id"]),
                    int(item["test_item_id"]),
                    str(item["qc_method"]),
                    str(item["input_value_type"]),
                    item["unit_id"],
                    item["method_id"],
                    item["reagent_id"],
                    int(item["level_count"]),
                    int(item["target_n"]),
                    item["cv_limit"],
                    str(item["quality_target_source_text"] or ""),
                    int(item["sort_order"]),
                    str(item["notes"] or ""),
                ),
            )
        _save_snapshot(connection, lot_config_id, action_type="create", change_summary="创建批号配置")
        return lot_config_id


def list_lot_configs(
    template_id: int | None = None,
    include_disabled: bool = False,
) -> pd.DataFrame:
    clauses: list[str] = []
    params: list[object] = []
    if not include_disabled:
        clauses.append("configs.is_disabled = 0")
    if template_id is not None:
        clauses.append("configs.template_id = ?")
        params.append(int(template_id))
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return _read_dataframe(
        f"""
        SELECT
            configs.id,
            configs.uid,
            configs.template_id,
            templates.template_name,
            configs.config_name,
            local.display_name AS instrument_name,
            materials.generic_name AS qc_material_name,
            lots.lot_no,
            lots.expiry_date,
            configs.status,
            configs.revision_no,
            configs.copied_from_config_id,
            configs.is_disabled,
            configs.created_at,
            COUNT(
                CASE WHEN items.is_disabled = 0 AND items.is_enabled = 1 THEN 1 END
            ) AS item_count
        FROM qc_lot_configs AS configs
        INNER JOIN qc_project_templates AS templates ON templates.id = configs.template_id
        INNER JOIN lab_instruments AS local ON local.id = configs.lab_instrument_id
        INNER JOIN md_qc_materials AS materials ON materials.id = configs.qc_material_id
        LEFT JOIN md_qc_material_lots AS lots ON lots.id = configs.qc_material_lot_id
        LEFT JOIN qc_lot_config_items AS items ON items.lot_config_id = configs.id
        {where_clause}
        GROUP BY configs.id
        ORDER BY configs.is_disabled ASC, configs.updated_at DESC, configs.id DESC
        """,
        tuple(params),
    )


def get_lot_config(lot_config_id: int) -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                configs.*,
                templates.template_name,
                local.display_name AS instrument_name,
                materials.generic_name AS qc_material_name,
                materials.trade_name AS qc_material_trade_name,
                lots.lot_no,
                lots.expiry_date
            FROM qc_lot_configs AS configs
            INNER JOIN qc_project_templates AS templates ON templates.id = configs.template_id
            INNER JOIN lab_instruments AS local ON local.id = configs.lab_instrument_id
            INNER JOIN md_qc_materials AS materials ON materials.id = configs.qc_material_id
            LEFT JOIN md_qc_material_lots AS lots ON lots.id = configs.qc_material_lot_id
            WHERE configs.id = ?
            """,
            (int(lot_config_id),),
        ).fetchone()
    if row is None:
        raise ValueError("未找到批号配置。")
    return row


def list_lot_config_items(lot_config_id: int) -> pd.DataFrame:
    return _read_dataframe(
        """
        SELECT
            items.id,
            items.uid,
            items.lot_config_id,
            items.test_item_id,
            tests.chinese_name AS test_item_name,
            tests.standard_code,
            items.qc_method,
            items.input_value_type,
            units.symbol AS unit_symbol,
            methods.method_name,
            reagents.generic_name AS reagent_name,
            items.level_count,
            items.target_n,
            items.cv_limit,
            items.quality_target_source_text,
            items.sort_order,
            items.is_enabled,
            COUNT(
                CASE WHEN levels.is_disabled = 0 THEN 1 END
            ) AS assigned_level_count
        FROM qc_lot_config_items AS items
        INNER JOIN md_test_items AS tests ON tests.id = items.test_item_id
        LEFT JOIN md_units AS units ON units.id = items.unit_id
        LEFT JOIN md_methods AS methods ON methods.id = items.method_id
        LEFT JOIN md_reagents AS reagents ON reagents.id = items.reagent_id
        LEFT JOIN qc_lot_config_item_levels AS levels
            ON levels.lot_config_item_id = items.id
        WHERE items.lot_config_id = ?
          AND items.is_disabled = 0
        GROUP BY items.id
        ORDER BY items.sort_order ASC, items.id ASC
        """,
        (int(lot_config_id),),
    )


def list_lot_item_levels(lot_config_item_id: int) -> pd.DataFrame:
    return _read_dataframe(
        """
        SELECT
            assigned.id,
            assigned.uid,
            assigned.lot_config_item_id,
            assigned.qc_level_id,
            levels.level_name,
            levels.level_code,
            assigned.level_order,
            assigned.target_source,
            assigned.target_mean,
            assigned.target_sd,
            assigned.target_confirmed,
            assigned.notes
        FROM qc_lot_config_item_levels AS assigned
        INNER JOIN md_qc_levels AS levels ON levels.id = assigned.qc_level_id
        WHERE assigned.lot_config_item_id = ?
          AND assigned.is_disabled = 0
        ORDER BY assigned.level_order ASC, assigned.id ASC
        """,
        (int(lot_config_item_id),),
    )


def save_lot_item_levels(
    lot_config_item_id: int,
    assignments: list[dict[str, object]],
) -> None:
    with get_connection() as connection:
        item = connection.execute(
            """
            SELECT items.*, configs.qc_material_lot_id, configs.id AS config_id
            FROM qc_lot_config_items AS items
            INNER JOIN qc_lot_configs AS configs ON configs.id = items.lot_config_id
            WHERE items.id = ?
              AND items.is_disabled = 0
              AND configs.is_disabled = 0
            """,
            (int(lot_config_item_id),),
        ).fetchone()
        if item is None:
            raise ValueError("未找到批号项目配置。")
        expected_count = int(item["level_count"])
        if len(assignments) != expected_count:
            raise ValueError(f"当前项目必须配置 {expected_count} 个水平。")

        normalized: list[dict[str, object]] = []
        seen_levels: set[int] = set()
        for order, raw in enumerate(assignments, start=1):
            level_id = int(raw["qc_level_id"])
            if level_id in seen_levels:
                raise ValueError("同一项目不能重复选择同一水平。")
            seen_levels.add(level_id)
            level = connection.execute(
                """
                SELECT id
                FROM md_qc_levels
                WHERE id = ?
                  AND qc_material_lot_id = ?
                  AND is_disabled = 0
                """,
                (level_id, int(item["qc_material_lot_id"])),
            ).fetchone()
            if level is None:
                raise ValueError("所选水平不属于当前质控品批号。")

            target_source = str(raw.get("target_source") or "building").strip().lower()
            if target_source not in TARGET_SOURCE_LABELS:
                raise ValueError("不支持的靶值来源。")
            target_mean = _optional_float(raw.get("target_mean"))
            target_sd = _optional_float(raw.get("target_sd"))
            if target_sd is not None and target_sd <= 0:
                raise ValueError("SD 必须大于 0。")
            target_confirmed = int(bool(raw.get("target_confirmed")))
            if target_source == "building":
                target_confirmed = 1
            normalized.append(
                {
                    "qc_level_id": level_id,
                    "level_order": order,
                    "target_source": target_source,
                    "target_mean": target_mean,
                    "target_sd": target_sd,
                    "target_confirmed": target_confirmed,
                    "notes": _clean_optional(raw.get("notes")),
                }
            )

        connection.execute(
            """
            UPDATE qc_lot_config_item_levels
            SET is_disabled = 1,
                disabled_at = CURRENT_TIMESTAMP,
                disabled_reason = '已从当前水平配置移除',
                updated_at = CURRENT_TIMESTAMP
            WHERE lot_config_item_id = ? AND is_disabled = 0
            """,
            (int(lot_config_item_id),),
        )
        for row in normalized:
            existing = connection.execute(
                """
                SELECT id
                FROM qc_lot_config_item_levels
                WHERE lot_config_item_id = ? AND qc_level_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(lot_config_item_id), row["qc_level_id"]),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO qc_lot_config_item_levels (
                        uid, origin_type, lot_config_item_id, qc_level_id,
                        level_order, target_source, target_mean, target_sd,
                        target_confirmed, notes
                    )
                    VALUES (?, 'hospital', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _new_uid(),
                        int(lot_config_item_id),
                        row["qc_level_id"],
                        row["level_order"],
                        row["target_source"],
                        row["target_mean"],
                        row["target_sd"],
                        row["target_confirmed"],
                        row["notes"],
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE qc_lot_config_item_levels
                    SET level_order = ?,
                        target_source = ?,
                        target_mean = ?,
                        target_sd = ?,
                        target_confirmed = ?,
                        notes = ?,
                        is_disabled = 0,
                        disabled_at = NULL,
                        disabled_reason = '',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        row["level_order"],
                        row["target_source"],
                        row["target_mean"],
                        row["target_sd"],
                        row["target_confirmed"],
                        row["notes"],
                        int(existing["id"]),
                    ),
                )
        config_id = int(item["config_id"])
        connection.execute(
            """
            UPDATE qc_lot_configs
            SET status = 'draft',
                revision_no = revision_no + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (config_id,),
        )
        _save_snapshot(
            connection,
            config_id,
            action_type="edit",
            change_summary="更新项目水平、靶值和 SD",
        )


def validate_lot_config(lot_config_id: int) -> list[str]:
    config = get_lot_config(lot_config_id)
    items = list_lot_config_items(lot_config_id)
    errors: list[str] = []
    if not str(config["expiry_date"] or "").strip():
        errors.append("质控品批号未填写效期。")
    if items.empty:
        errors.append("批号配置没有检验项目。")
        return errors

    for _, item in items.iterrows():
        item_name = str(item["test_item_name"])
        expected_count = int(item["level_count"])
        assigned = list_lot_item_levels(int(item["id"]))
        if len(assigned.index) != expected_count:
            errors.append(f"{item_name}：应配置 {expected_count} 个水平。")
            continue
        for _, level in assigned.iterrows():
            level_label = str(level["level_name"])
            source = str(level["target_source"])
            if source in {"manufacturer", "manual", "copied_pending"}:
                if pd.isna(level["target_mean"]) or pd.isna(level["target_sd"]):
                    errors.append(f"{item_name} / {level_label}：靶值和 SD 未完整填写。")
                if not bool(int(level["target_confirmed"] or 0)):
                    errors.append(f"{item_name} / {level_label}：靶值和 SD 尚未确认。")
            if source == "copied_pending":
                errors.append(f"{item_name} / {level_label}：复制值仍处于待确认状态。")
    return list(dict.fromkeys(errors))


def activate_lot_config(lot_config_id: int) -> None:
    errors = validate_lot_config(lot_config_id)
    if errors:
        raise ValueError("批号配置暂不能启用：\n" + "\n".join(f"- {item}" for item in errors))
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE qc_lot_configs
            SET status = 'active',
                activated_at = CURRENT_TIMESTAMP,
                revision_no = revision_no + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND is_disabled = 0
            """,
            (int(lot_config_id),),
        )
        _save_snapshot(
            connection,
            int(lot_config_id),
            action_type="activate",
            change_summary="启用批号配置",
        )


def copy_lot_config(
    *,
    source_lot_config_id: int,
    target_qc_material_lot_id: int,
    config_name: str = "",
) -> int:
    with get_connection() as connection:
        source = connection.execute(
            """
            SELECT *
            FROM qc_lot_configs
            WHERE id = ? AND is_disabled = 0
            """,
            (int(source_lot_config_id),),
        ).fetchone()
        if source is None:
            raise ValueError("未找到复制来源批号配置。")
        target_lot = connection.execute(
            """
            SELECT lots.*, materials.generic_name AS material_name
            FROM md_qc_material_lots AS lots
            INNER JOIN md_qc_materials AS materials ON materials.id = lots.qc_material_id
            WHERE lots.id = ? AND lots.is_disabled = 0
            """,
            (int(target_qc_material_lot_id),),
        ).fetchone()
        if target_lot is None:
            raise ValueError("未找到目标质控品批号。")
        if int(target_lot["qc_material_id"]) != int(source["qc_material_id"]):
            raise ValueError("目标批号必须属于与来源配置相同的质控品。")

        normalized_name = _clean_optional(config_name)
        if not normalized_name:
            normalized_name = f"{source['config_name']}｜复制到 {target_lot['lot_no']}"
        try:
            cursor = connection.execute(
                """
                INSERT INTO qc_lot_configs (
                    uid, origin_type, template_id, qc_material_lot_id,
                    lab_instrument_id, qc_material_id, config_name,
                    copied_from_config_id
                )
                VALUES (?, 'hospital', ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_uid(),
                    int(source["template_id"]),
                    int(target_qc_material_lot_id),
                    int(source["lab_instrument_id"]),
                    int(source["qc_material_id"]),
                    normalized_name,
                    int(source_lot_config_id),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("目标批号已存在当前模板的启用配置。") from exc
        target_config_id = int(cursor.lastrowid)

        source_items = connection.execute(
            """
            SELECT *
            FROM qc_lot_config_items
            WHERE lot_config_id = ? AND is_disabled = 0
            ORDER BY sort_order ASC, id ASC
            """,
            (int(source_lot_config_id),),
        ).fetchall()
        target_levels = connection.execute(
            """
            SELECT *
            FROM md_qc_levels
            WHERE qc_material_lot_id = ? AND is_disabled = 0
            ORDER BY level_order ASC, id ASC
            """,
            (int(target_qc_material_lot_id),),
        ).fetchall()
        target_level_by_order = {int(row["level_order"]): row for row in target_levels}

        for source_item in source_items:
            item_cursor = connection.execute(
                """
                INSERT INTO qc_lot_config_items (
                    uid, origin_type, lot_config_id, source_template_item_id,
                    test_item_id, qc_method, input_value_type,
                    unit_id, method_id, reagent_id, level_count, target_n,
                    cv_limit, quality_target_source_text, sort_order,
                    is_enabled, notes
                )
                VALUES (
                    ?, 'hospital', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    _new_uid(),
                    target_config_id,
                    source_item["source_template_item_id"],
                    int(source_item["test_item_id"]),
                    str(source_item["qc_method"]),
                    str(source_item["input_value_type"]),
                    source_item["unit_id"],
                    source_item["method_id"],
                    source_item["reagent_id"],
                    int(source_item["level_count"]),
                    int(source_item["target_n"]),
                    source_item["cv_limit"],
                    str(source_item["quality_target_source_text"] or ""),
                    int(source_item["sort_order"]),
                    int(source_item["is_enabled"]),
                    str(source_item["notes"] or ""),
                ),
            )
            target_item_id = int(item_cursor.lastrowid)
            source_levels = connection.execute(
                """
                SELECT *
                FROM qc_lot_config_item_levels
                WHERE lot_config_item_id = ? AND is_disabled = 0
                ORDER BY level_order ASC, id ASC
                """,
                (int(source_item["id"]),),
            ).fetchall()
            for source_level in source_levels:
                level_order = int(source_level["level_order"])
                target_level = target_level_by_order.get(level_order)
                if target_level is None:
                    continue
                source_target_kind = str(source_level["target_source"] or "building")
                if source_target_kind == "building":
                    copied_target_kind = "building"
                    copied_target_mean = None
                    copied_target_sd = None
                    copied_target_confirmed = 1
                else:
                    copied_target_kind = "copied_pending"
                    copied_target_mean = source_level["target_mean"]
                    copied_target_sd = source_level["target_sd"]
                    copied_target_confirmed = 0
                connection.execute(
                    """
                    INSERT INTO qc_lot_config_item_levels (
                        uid, origin_type, lot_config_item_id, qc_level_id,
                        level_order, target_source, target_mean, target_sd,
                        target_confirmed, notes
                    )
                    VALUES (?, 'hospital', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _new_uid(),
                        target_item_id,
                        int(target_level["id"]),
                        level_order,
                        copied_target_kind,
                        copied_target_mean,
                        copied_target_sd,
                        copied_target_confirmed,
                        str(source_level["notes"] or ""),
                    ),
                )
        _save_snapshot(
            connection,
            target_config_id,
            action_type="copy",
            change_summary=f"从批号配置 {source_lot_config_id} 复制",
        )
        return target_config_id


def set_lot_config_disabled(
    lot_config_id: int,
    *,
    is_disabled: bool,
    reason: str = "",
) -> None:
    try:
        with get_connection() as connection:
            config = connection.execute(
                "SELECT id FROM qc_lot_configs WHERE id = ?",
                (int(lot_config_id),),
            ).fetchone()
            if config is None:
                raise ValueError("未找到批号配置。")
            connection.execute(
                """
                UPDATE qc_lot_configs
                SET is_disabled = ?,
                    status = CASE WHEN ? = 1 THEN 'disabled' ELSE 'draft' END,
                    disabled_at = CASE WHEN ? = 1 THEN CURRENT_TIMESTAMP ELSE NULL END,
                    disabled_reason = CASE WHEN ? = 1 THEN ? ELSE '' END,
                    revision_no = revision_no + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    int(bool(is_disabled)),
                    int(bool(is_disabled)),
                    int(bool(is_disabled)),
                    int(bool(is_disabled)),
                    _clean_optional(reason),
                    int(lot_config_id),
                ),
            )
            _save_snapshot(
                connection,
                int(lot_config_id),
                action_type="disable" if is_disabled else "reactivate",
                change_summary="停用批号配置" if is_disabled else "恢复批号配置",
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError("同一模板和批号已有启用配置，当前配置不能恢复。") from exc


def list_config_snapshots(lot_config_id: int) -> pd.DataFrame:
    return _read_dataframe(
        """
        SELECT id, uid, lot_config_id, revision_no, action_type,
               change_summary, created_by, created_at
        FROM qc_config_snapshots
        WHERE lot_config_id = ?
        ORDER BY revision_no DESC, id DESC
        """,
        (int(lot_config_id),),
    )


def _save_snapshot(
    connection: sqlite3.Connection,
    lot_config_id: int,
    *,
    action_type: str,
    change_summary: str,
) -> int:
    payload = _build_snapshot_payload(connection, lot_config_id)
    revision_no = int(payload["config"]["revision_no"])
    cursor = connection.execute(
        """
        INSERT INTO qc_config_snapshots (
            uid, lot_config_id, revision_no, action_type,
            snapshot_json, change_summary
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            _new_uid(),
            int(lot_config_id),
            revision_no,
            str(action_type),
            json.dumps(payload, ensure_ascii=False),
            _clean_optional(change_summary),
        ),
    )
    return int(cursor.lastrowid)


def _build_snapshot_payload(
    connection: sqlite3.Connection,
    lot_config_id: int,
) -> dict[str, Any]:
    config = connection.execute(
        """
        SELECT
            configs.*,
            templates.uid AS template_uid,
            templates.template_name,
            local.uid AS instrument_uid,
            local.display_name AS instrument_name,
            materials.uid AS qc_material_uid,
            materials.generic_name AS qc_material_name,
            lots.uid AS qc_lot_uid,
            lots.lot_no,
            lots.expiry_date
        FROM qc_lot_configs AS configs
        INNER JOIN qc_project_templates AS templates ON templates.id = configs.template_id
        INNER JOIN lab_instruments AS local ON local.id = configs.lab_instrument_id
        INNER JOIN md_qc_materials AS materials ON materials.id = configs.qc_material_id
        LEFT JOIN md_qc_material_lots AS lots ON lots.id = configs.qc_material_lot_id
        WHERE configs.id = ?
        """,
        (int(lot_config_id),),
    ).fetchone()
    if config is None:
        raise ValueError("未找到批号配置，无法生成快照。")

    item_rows = connection.execute(
        """
        SELECT
            items.*,
            tests.uid AS test_item_uid,
            tests.chinese_name AS test_item_name,
            units.uid AS unit_uid,
            units.symbol AS unit_symbol,
            methods.uid AS method_uid,
            methods.method_name,
            reagents.uid AS reagent_uid,
            reagents.generic_name AS reagent_name
        FROM qc_lot_config_items AS items
        INNER JOIN md_test_items AS tests ON tests.id = items.test_item_id
        LEFT JOIN md_units AS units ON units.id = items.unit_id
        LEFT JOIN md_methods AS methods ON methods.id = items.method_id
        LEFT JOIN md_reagents AS reagents ON reagents.id = items.reagent_id
        WHERE items.lot_config_id = ? AND items.is_disabled = 0
        ORDER BY items.sort_order ASC, items.id ASC
        """,
        (int(lot_config_id),),
    ).fetchall()

    items: list[dict[str, Any]] = []
    for item in item_rows:
        levels = connection.execute(
            """
            SELECT
                assigned.*,
                levels.uid AS qc_level_uid,
                levels.level_name,
                levels.level_code
            FROM qc_lot_config_item_levels AS assigned
            INNER JOIN md_qc_levels AS levels ON levels.id = assigned.qc_level_id
            WHERE assigned.lot_config_item_id = ? AND assigned.is_disabled = 0
            ORDER BY assigned.level_order ASC, assigned.id ASC
            """,
            (int(item["id"]),),
        ).fetchall()
        item_payload = dict(item)
        item_payload["levels"] = [dict(level) for level in levels]
        items.append(item_payload)
    return {
        "config": dict(config),
        "items": items,
    }
