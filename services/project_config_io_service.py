from __future__ import annotations

from collections import OrderedDict

import pandas as pd

from services.export_utils import dataframes_to_xlsx_bytes, xlsx_bytes_to_dataframes
from services.master_data_service import (
    create_manufacturer,
    create_method,
    create_reagent,
    create_test_item,
    create_unit,
    list_manufacturers,
    list_methods,
    list_reagents,
    list_test_items,
    list_units,
)
from services.project_config_service import (
    INPUT_VALUE_TYPE_LABELS,
    QC_METHOD_LABELS,
    TARGET_SOURCE_LABELS,
    get_lot_config,
    get_project_template,
    list_config_snapshots,
    list_lot_config_items,
    list_lot_item_levels,
    list_template_items,
    save_template_items,
)


PROJECT_IMPORT_COLUMNS = [
    "检验项目*",
    "项目缩写",
    "标准编码",
    "质控方法*",
    "输入值类型*",
    "单位*",
    "方法学*",
    "试剂厂家",
    "试剂通用名*",
    "试剂商品名",
    "水平数*",
    "建靶点数*",
    "CV要求(%)",
    "质量目标来源",
    "备注",
]

_QC_METHOD_BY_TEXT = {
    **{code.casefold(): code for code in QC_METHOD_LABELS},
    **{label.casefold(): code for code, label in QC_METHOD_LABELS.items()},
    "lj法": "lj",
    "z-score": "zscore",
    "z-score法": "zscore",
    "z score": "zscore",
}
_INPUT_VALUE_TYPE_BY_TEXT = {
    **{code.casefold(): code for code in INPUT_VALUE_TYPE_LABELS},
    **{label.casefold(): code for code, label in INPUT_VALUE_TYPE_LABELS.items()},
    "ct": "ct",
    "ct值": "ct",
    "log": "log",
    "log值": "log",
}


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return " ".join(str(value).split()).strip()


def _key(value: object) -> str:
    return _text(value).casefold()


def _optional_float(value: object) -> float | None:
    cleaned = _text(value)
    if not cleaned:
        return None
    return float(cleaned)


def _required_integer(value: object, label: str) -> int:
    cleaned = _text(value)
    if not cleaned:
        raise ValueError(f"{label}不能为空。")
    number = float(cleaned)
    if not number.is_integer():
        raise ValueError(f"{label}必须为整数。")
    return int(number)


def _instructions_dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ["用途", "在“项目配置”工作表批量维护项目模板；星号列为必填。"],
            ["质控方法", "填写 LJ、Z-score 或 即时法。"],
            ["输入值类型", "填写 真实检测值、Ct值 或 log值；同一项目配置只允许一种。"],
            ["水平数", "LJ/即时法固定 1；Z-score 填 2 或 3。"],
            ["建靶点数", "LJ/Z-score 填 5–20；即时法固定按 20 个有效点。"],
            ["本地词条", "找不到的检验项目、单位、方法学、试剂及厂家会作为医院自定义词条新增。"],
            ["导入方式", "合并会保留未出现在文件中的原项目；替换会以文件内容作为模板完整项目表。"],
            ["启用规则", "导入后模板保持草稿，必须回到项目模板页校验并人工启用。"],
        ],
        columns=["项目", "说明"],
    )


def build_project_import_template_xlsx() -> bytes:
    example = pd.DataFrame(
        [
            [
                "示例项目（导入前请替换或删除）",
                "EXAMPLE",
                "",
                "LJ",
                "真实检测值",
                "mmol/L",
                "比色法",
                "示例厂家",
                "示例试剂",
                "",
                1,
                20,
                "",
                "",
                "示例行",
            ]
        ],
        columns=PROJECT_IMPORT_COLUMNS,
    )
    return dataframes_to_xlsx_bytes(
        OrderedDict(
            [
                ("项目配置", example),
                ("填写说明", _instructions_dataframe()),
            ]
        )
    )


def _project_items_export_dataframe(template_id: int) -> pd.DataFrame:
    items = list_template_items(template_id)
    reagents = list_reagents()
    reagent_manufacturer = {
        int(row["id"]): _text(row.get("manufacturer_name"))
        for _, row in reagents.iterrows()
    }
    if items.empty:
        return pd.DataFrame(columns=PROJECT_IMPORT_COLUMNS)
    rows: list[dict[str, object]] = []
    for _, row in items.iterrows():
        reagent_id = None if pd.isna(row["reagent_id"]) else int(row["reagent_id"])
        rows.append(
            {
                "检验项目*": _text(row["test_item_name"]),
                "项目缩写": _text(row["abbreviation"]),
                "标准编码": _text(row["standard_code"]),
                "质控方法*": QC_METHOD_LABELS.get(_text(row["qc_method"]), _text(row["qc_method"])),
                "输入值类型*": INPUT_VALUE_TYPE_LABELS.get(
                    _text(row["input_value_type"]), _text(row["input_value_type"])
                ),
                "单位*": _text(row["unit_symbol"]),
                "方法学*": _text(row["method_name"]),
                "试剂厂家": reagent_manufacturer.get(reagent_id, ""),
                "试剂通用名*": _text(row["reagent_name"]),
                "试剂商品名": _text(row["reagent_trade_name"]),
                "水平数*": int(row["level_count"]),
                "建靶点数*": int(row["target_n"]),
                "CV要求(%)": "" if pd.isna(row["cv_limit"]) else float(row["cv_limit"]),
                "质量目标来源": _text(row["quality_target_source_text"]),
                "备注": _text(row["notes"]),
            }
        )
    return pd.DataFrame(rows, columns=PROJECT_IMPORT_COLUMNS)


def build_project_template_xlsx(template_id: int) -> bytes:
    template = get_project_template(template_id)
    overview = pd.DataFrame(
        [
            ["模板名称", template["template_name"]],
            ["本地仪器", template["instrument_name"]],
            ["仪器厂家", template["instrument_manufacturer_name"]],
            ["仪器型号", template["instrument_model"]],
            ["质控品", template["qc_material_name"]],
            ["质控品商品名", template["qc_material_trade_name"]],
            ["质控品厂家", template["qc_manufacturer_name"]],
            ["状态", "已启用" if template["status"] == "active" else "草稿"],
            ["修订号", template["revision_no"]],
        ],
        columns=["字段", "值"],
    )
    return dataframes_to_xlsx_bytes(
        OrderedDict(
            [
                ("模板信息", overview),
                ("项目配置", _project_items_export_dataframe(template_id)),
                ("填写说明", _instructions_dataframe()),
            ]
        )
    )


def build_lot_config_xlsx(lot_config_id: int) -> bytes:
    config = get_lot_config(lot_config_id)
    items = list_lot_config_items(lot_config_id)
    overview = pd.DataFrame(
        [
            ["配置名称", config["config_name"]],
            ["项目模板", config["template_name"]],
            ["本地仪器", config["instrument_name"]],
            ["质控品", config["qc_material_name"]],
            ["质控品商品名", config["qc_material_trade_name"]],
            ["批号", config["lot_no"]],
            ["效期", config["expiry_date"]],
            ["状态", config["status"]],
            ["修订号", config["revision_no"]],
            ["复制来源配置ID", config["copied_from_config_id"]],
            ["启用时间", config["activated_at"]],
        ],
        columns=["字段", "值"],
    )
    item_export = items.rename(
        columns={
            "test_item_name": "检验项目",
            "qc_method": "质控方法",
            "input_value_type": "输入值类型",
            "unit_symbol": "单位",
            "method_name": "方法学",
            "reagent_name": "试剂",
            "level_count": "水平数",
            "assigned_level_count": "已配置水平数",
            "target_n": "建靶点数",
            "cv_limit": "CV要求(%)",
            "quality_target_source_text": "质量目标来源",
        }
    ).copy()
    if not item_export.empty:
        item_export["质控方法"] = item_export["质控方法"].map(QC_METHOD_LABELS)
        item_export["输入值类型"] = item_export["输入值类型"].map(INPUT_VALUE_TYPE_LABELS)
    item_columns = [
        "检验项目",
        "质控方法",
        "输入值类型",
        "单位",
        "方法学",
        "试剂",
        "水平数",
        "已配置水平数",
        "建靶点数",
        "CV要求(%)",
        "质量目标来源",
    ]
    item_export = item_export.reindex(columns=item_columns)

    level_rows: list[dict[str, object]] = []
    for _, item in items.iterrows():
        levels = list_lot_item_levels(int(item["id"]))
        for _, level in levels.iterrows():
            level_rows.append(
                {
                    "检验项目": _text(item["test_item_name"]),
                    "水平顺序": int(level["level_order"]),
                    "水平名称": _text(level["level_name"]),
                    "水平编码": _text(level["level_code"]),
                    "靶值来源": TARGET_SOURCE_LABELS.get(
                        _text(level["target_source"]), _text(level["target_source"])
                    ),
                    "靶值": "" if pd.isna(level["target_mean"]) else float(level["target_mean"]),
                    "SD": "" if pd.isna(level["target_sd"]) else float(level["target_sd"]),
                    "已确认": bool(level["target_confirmed"]),
                    "备注": _text(level["notes"]),
                }
            )
    level_export = pd.DataFrame(
        level_rows,
        columns=["检验项目", "水平顺序", "水平名称", "水平编码", "靶值来源", "靶值", "SD", "已确认", "备注"],
    )
    snapshots = list_config_snapshots(lot_config_id).rename(
        columns={
            "revision_no": "修订号",
            "action_type": "动作",
            "change_summary": "变更说明",
            "created_by": "操作者",
            "created_at": "时间",
        }
    )
    snapshots = snapshots.reindex(columns=["修订号", "动作", "变更说明", "操作者", "时间"])
    return dataframes_to_xlsx_bytes(
        OrderedDict(
            [
                ("批号信息", overview),
                ("项目配置", item_export),
                ("水平靶值", level_export),
                ("修订记录", snapshots),
            ]
        )
    )


def preview_project_template_xlsx(data: bytes) -> tuple[pd.DataFrame, list[str]]:
    sheets = xlsx_bytes_to_dataframes(data)
    if "项目配置" not in sheets:
        raise ValueError("XLSX 必须包含名为“项目配置”的工作表。")
    source = sheets["项目配置"].copy()
    missing_columns = [column for column in PROJECT_IMPORT_COLUMNS if column not in source.columns]
    if missing_columns:
        raise ValueError("项目配置工作表缺少列：" + "、".join(missing_columns))
    source = source[PROJECT_IMPORT_COLUMNS]
    source = source[
        source.apply(lambda row: any(_text(value) for value in row.tolist()), axis=1)
    ].reset_index(drop=True)
    if source.empty:
        raise ValueError("项目配置工作表没有可导入的数据行。")

    normalized_rows: list[dict[str, object]] = []
    errors: list[str] = []
    seen_names: set[str] = set()
    for index, row in source.iterrows():
        excel_row = index + 2
        try:
            item_name = _text(row["检验项目*"])
            if not item_name:
                raise ValueError("检验项目不能为空。")
            name_key = item_name.casefold()
            if name_key in seen_names:
                raise ValueError("同一文件中检验项目重复。")
            seen_names.add(name_key)
            qc_method = _QC_METHOD_BY_TEXT.get(_key(row["质控方法*"]))
            if qc_method is None:
                raise ValueError("质控方法必须为 LJ、Z-score 或 即时法。")
            input_value_type = _INPUT_VALUE_TYPE_BY_TEXT.get(_key(row["输入值类型*"]))
            if input_value_type is None:
                raise ValueError("输入值类型必须为真实检测值、Ct值或log值。")
            unit_symbol = _text(row["单位*"])
            method_name = _text(row["方法学*"])
            reagent_name = _text(row["试剂通用名*"])
            if not unit_symbol or not method_name or not reagent_name:
                raise ValueError("单位、方法学和试剂通用名均为必填。")
            level_count = _required_integer(row["水平数*"], "水平数")
            target_n = _required_integer(row["建靶点数*"], "建靶点数")
            if qc_method in {"lj", "instant"} and level_count != 1:
                raise ValueError("LJ 和即时法只能配置 1 个水平。")
            if qc_method == "zscore" and level_count not in {2, 3}:
                raise ValueError("Z-score 只能配置 2 或 3 个水平。")
            if qc_method == "instant":
                target_n = 20
            elif not 5 <= target_n <= 20:
                raise ValueError("LJ 和 Z-score 建靶点数必须在 5 至 20 之间。")
            cv_limit = _optional_float(row["CV要求(%)"])
            if cv_limit is not None and cv_limit <= 0:
                raise ValueError("CV要求必须大于 0。")
            normalized_rows.append(
                {
                    "检验项目": item_name,
                    "项目缩写": _text(row["项目缩写"]),
                    "标准编码": _text(row["标准编码"]),
                    "质控方法": qc_method,
                    "输入值类型": input_value_type,
                    "单位": unit_symbol,
                    "方法学": method_name,
                    "试剂厂家": _text(row["试剂厂家"]),
                    "试剂通用名": reagent_name,
                    "试剂商品名": _text(row["试剂商品名"]),
                    "水平数": level_count,
                    "建靶点数": target_n,
                    "CV要求(%)": cv_limit,
                    "质量目标来源": _text(row["质量目标来源"]),
                    "备注": _text(row["备注"]),
                }
            )
        except (TypeError, ValueError) as exc:
            errors.append(f"第 {excel_row} 行：{exc}")
    preview = pd.DataFrame(normalized_rows)
    if not preview.empty:
        preview["质控方法"] = preview["质控方法"].map(QC_METHOD_LABELS)
        preview["输入值类型"] = preview["输入值类型"].map(INPUT_VALUE_TYPE_LABELS)
    return preview, errors


def _normalized_import_rows(data: bytes) -> list[dict[str, object]]:
    preview, errors = preview_project_template_xlsx(data)
    if errors:
        raise ValueError("导入文件校验未通过：\n" + "\n".join(f"- {item}" for item in errors))
    result: list[dict[str, object]] = []
    for _, row in preview.iterrows():
        result.append(
            {
                "item_name": _text(row["检验项目"]),
                "abbreviation": _text(row["项目缩写"]),
                "standard_code": _text(row["标准编码"]),
                "qc_method": _QC_METHOD_BY_TEXT[_key(row["质控方法"])],
                "input_value_type": _INPUT_VALUE_TYPE_BY_TEXT[_key(row["输入值类型"])],
                "unit_symbol": _text(row["单位"]),
                "method_name": _text(row["方法学"]),
                "reagent_manufacturer": _text(row["试剂厂家"]),
                "reagent_name": _text(row["试剂通用名"]),
                "reagent_trade_name": _text(row["试剂商品名"]),
                "level_count": int(row["水平数"]),
                "target_n": int(row["建靶点数"]),
                "cv_limit": None if pd.isna(row["CV要求(%)"]) else float(row["CV要求(%)"]),
                "quality_target_source_text": _text(row["质量目标来源"]),
                "notes": _text(row["备注"]),
            }
        )
    return result


def import_project_template_xlsx(
    template_id: int,
    data: bytes,
    *,
    mode: str = "merge",
) -> dict[str, object]:
    if mode not in {"merge", "replace"}:
        raise ValueError("导入方式必须为 merge 或 replace。")
    get_project_template(template_id)
    imported = _normalized_import_rows(data)
    created = {"检验项目": 0, "单位": 0, "方法学": 0, "厂家": 0, "试剂": 0}

    units = list_units()
    unit_by_key = {_key(row["symbol"]): int(row["id"]) for _, row in units.iterrows()}
    methods = list_methods()
    method_by_key = {_key(row["method_name"]): int(row["id"]) for _, row in methods.iterrows()}
    manufacturers = list_manufacturers()
    manufacturer_by_key = {
        _key(row["display_name"]): int(row["id"]) for _, row in manufacturers.iterrows()
    }
    reagents = list_reagents()
    reagent_by_key = {
        (
            _key(row.get("manufacturer_name")),
            _key(row["generic_name"]),
            _key(row["trade_name"]),
        ): int(row["id"])
        for _, row in reagents.iterrows()
    }
    test_items = list_test_items()
    test_by_name = {_key(row["chinese_name"]): int(row["id"]) for _, row in test_items.iterrows()}
    test_by_code = {
        _key(row["standard_code"]): int(row["id"])
        for _, row in test_items.iterrows()
        if _text(row["standard_code"])
    }

    imported_config_rows: list[dict[str, object]] = []
    for order, row in enumerate(imported, start=1):
        unit_key = _key(row["unit_symbol"])
        unit_id = unit_by_key.get(unit_key)
        if unit_id is None:
            unit_id = create_unit(symbol=str(row["unit_symbol"]))
            unit_by_key[unit_key] = unit_id
            created["单位"] += 1

        method_key = _key(row["method_name"])
        method_id = method_by_key.get(method_key)
        if method_id is None:
            method_id = create_method(method_name=str(row["method_name"]))
            method_by_key[method_key] = method_id
            created["方法学"] += 1

        manufacturer_name = str(row["reagent_manufacturer"])
        manufacturer_key = _key(manufacturer_name)
        manufacturer_id: int | None = None
        if manufacturer_key:
            manufacturer_id = manufacturer_by_key.get(manufacturer_key)
            if manufacturer_id is None:
                manufacturer_id = create_manufacturer(display_name=manufacturer_name)
                manufacturer_by_key[manufacturer_key] = manufacturer_id
                created["厂家"] += 1

        reagent_key = (
            manufacturer_key,
            _key(row["reagent_name"]),
            _key(row["reagent_trade_name"]),
        )
        reagent_id = reagent_by_key.get(reagent_key)
        if reagent_id is None:
            reagent_id = create_reagent(
                generic_name=str(row["reagent_name"]),
                manufacturer_id=manufacturer_id,
                trade_name=str(row["reagent_trade_name"]),
            )
            reagent_by_key[reagent_key] = reagent_id
            created["试剂"] += 1

        standard_code_key = _key(row["standard_code"])
        item_name_key = _key(row["item_name"])
        test_item_id = (
            test_by_code.get(standard_code_key) if standard_code_key else None
        ) or test_by_name.get(item_name_key)
        if test_item_id is None:
            test_item_id = create_test_item(
                chinese_name=str(row["item_name"]),
                abbreviation=str(row["abbreviation"]),
                standard_code=str(row["standard_code"]),
                default_unit_id=unit_id,
            )
            test_by_name[item_name_key] = test_item_id
            if standard_code_key:
                test_by_code[standard_code_key] = test_item_id
            created["检验项目"] += 1

        imported_config_rows.append(
            {
                "test_item_id": test_item_id,
                "qc_method": row["qc_method"],
                "input_value_type": row["input_value_type"],
                "unit_id": unit_id,
                "method_id": method_id,
                "reagent_id": reagent_id,
                "level_count": row["level_count"],
                "target_n": row["target_n"],
                "cv_limit": row["cv_limit"],
                "quality_target_source_text": row["quality_target_source_text"],
                "sort_order": order,
                "notes": row["notes"],
            }
        )

    rows_to_save = imported_config_rows
    if mode == "merge":
        current = list_template_items(template_id)
        merged: dict[int, dict[str, object]] = {}
        for _, row in current.iterrows():
            merged[int(row["test_item_id"])] = {
                "test_item_id": int(row["test_item_id"]),
                "qc_method": _text(row["qc_method"]),
                "input_value_type": _text(row["input_value_type"]),
                "unit_id": None if pd.isna(row["unit_id"]) else int(row["unit_id"]),
                "method_id": None if pd.isna(row["method_id"]) else int(row["method_id"]),
                "reagent_id": None if pd.isna(row["reagent_id"]) else int(row["reagent_id"]),
                "level_count": int(row["level_count"]),
                "target_n": int(row["target_n"]),
                "cv_limit": None if pd.isna(row["cv_limit"]) else float(row["cv_limit"]),
                "quality_target_source_text": _text(row["quality_target_source_text"]),
                "notes": _text(row["notes"]),
            }
        for row in imported_config_rows:
            merged[int(row["test_item_id"])] = row
        rows_to_save = list(merged.values())
        for order, row in enumerate(rows_to_save, start=1):
            row["sort_order"] = order

    save_template_items(template_id, rows_to_save)
    return {
        "imported_count": len(imported_config_rows),
        "saved_count": len(rows_to_save),
        "mode": mode,
        "created": created,
    }
