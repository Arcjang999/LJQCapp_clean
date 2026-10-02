from __future__ import annotations

from services.cv_service import calculate_cv_percent

from collections import OrderedDict
import hashlib
import json
import math

import pandas as pd

from database import atomic_write, get_connection, read_snapshot
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
    QC_METHOD_LEGACY_LABELS,
    TARGET_SOURCE_LABELS,
    get_lot_config,
    get_project_template,
    list_config_snapshots,
    list_lot_config_items,
    list_lot_item_levels,
    list_template_items,
    save_template_items,
    template_item_key,
    template_item_rows,
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
    "参数建立点数*",
    "允许不精密度(CV%)",
    "质量目标来源",
    "备注",
]

# Optional stable identities are emitted on export; the blank hand-entry template
# stays small. Database integer IDs are never used to resolve cross-database rows.
PROJECT_IDENTITY_COLUMNS = ['检验项目标识', '单位标识', '方法学标识', '试剂标识', '试剂厂家标识', '试剂厂家类别']

_QC_METHOD_BY_TEXT = {
    **{code.casefold(): code for code in QC_METHOD_LABELS},
    **{label.casefold(): code for code, label in QC_METHOD_LABELS.items()},
    **{label.casefold(): code for label, code in QC_METHOD_LEGACY_LABELS.items()},
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
            ["用途", "在“项目配置”工作表批量维护项目中的检验项目；星号列为必填。"],
            ["质控方法", "填写 LJ、Z-score 或 即时法。"],
            ["输入值类型", "填写 真实检测值、Ct值 或 log值；检验项目、质控方法和输入值类型的组合不得重复。"],
            ["水平数", "LJ/即时法固定 1；Z-score 填 2 或 3。"],
            ["参数建立点数", "LJ/Z-score 填 5–20；即时法固定按 20 个有效点。"],
            ["新增资料", "未登记的检验项目、单位、方法学、试剂及厂家会新增到基础资料中，请先核对名称，避免重复登记。"],
            ["导入方式", "合并会保留未出现在文件中的原检验项目；替换会以文件内容作为完整的检验项目表。"],
            ["设置确认", "导入后项目保持待确认。请回到项目设置，逐项核对后确认。"],
            ["质量目标确认", "“质量目标”和“标准适用情况”工作表供查阅，不参与导入。导入后请重新确认质量目标，不能使用文件中的确认人和状态代替确认。"],
            ["资料识别信息（选填）", "导出文件自动保留检验项目、单位、方法学及试剂标识；请勿修改。手填模板可留空，按完整名称及标准编码核对，不按相似名称合并。"],
            ["产品与材料", "产品目录的浓度文字不决定质控方法；实际材料批号及水平单独供核对。导入前，请核对文件中的产品编号、来源和原始资料是否与当前选择的产品一致。"],
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
                "参数建立点数*": int(row["target_n"]),
                "允许不精密度(CV%)": "" if pd.isna(row["cv_limit"]) else float(row["cv_limit"]),
                "质量目标来源": _text(row["quality_target_source_text"]),
                "备注": _text(row["notes"]),
            }
        )
    with get_connection() as connection:
        for exported, item in zip(rows, items.to_dict('records')):
            for column, table, field in [('检验项目标识', 'md_test_items', 'test_item_id'),
                                         ('单位标识', 'md_units', 'unit_id'), ('方法学标识', 'md_methods', 'method_id'),
                                         ('试剂标识', 'md_reagents', 'reagent_id')]:
                value = item.get(field)
                found = None if pd.isna(value) else connection.execute(f'SELECT uid FROM {table} WHERE id=?', (int(value),)).fetchone()
                exported[column] = found['uid'] if found else ''
            found = connection.execute('SELECT m.id,m.uid FROM md_reagents r JOIN md_manufacturers m ON m.id=r.manufacturer_id WHERE r.id=?',
                                       (item.get('reagent_id'),)).fetchone()
            exported['试剂厂家标识'] = found['uid'] if found else ''
            from services.master_data_service import get_manufacturer_categories
            exported['试剂厂家类别'] = ','.join(get_manufacturer_categories(found['id'], connection)) if found else ''
    return pd.DataFrame(rows, columns=PROJECT_IMPORT_COLUMNS + PROJECT_IDENTITY_COLUMNS)


def _quality_review_export_sheets(items: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    """Export frozen readable evidence, never an importable authorization token."""
    from services.quality_target_service import decode
    from services.quality_applicability_service import CONTEXT_OPTIONS, CONTEXT_LABELS

    review_rows = []
    candidate_rows = []
    for item in items.to_dict('records'):
        review = decode(item.get('quality_review_json'))
        goal = decode(item.get('quality_goal_json'))
        spec = goal.get('spec', {})
        recorded = review.get('recorded', {})
        conditions = '；'.join(CONTEXT_LABELS.get(k,k)+'：'+CONTEXT_OPTIONS.get(k,{}).get(v,str(v))
                              for k,v in review.get('context',{}).items())
        search = review.get('search_record',{})
        state = {'confirmed': '已确认', 'pending': '待重新确认'}.get(review.get('status'),
            '按原确认记录使用' if goal and not goal.get('pending') else '未确认')
        content = recorded.get('requirement_text', '')
        if spec:
            content = '；'.join(label + spec[key] for label, key in (
                ('允许不精密度：', 'imprecision_text'), ('允许偏倚：', 'bias_text'),
                ('允许总误差/可比性偏差：', 'tea_text')) if spec.get(key))
        review_rows.append({
            '检验项目': _text(item['test_item_name']),
            '输入值类型': INPUT_VALUE_TYPE_LABELS.get(_text(item['input_value_type']), _text(item['input_value_type'])),
            '单位': _text(item.get('unit_symbol')), '方法学': _text(item.get('method_name')),
            '核对状态': state,
            '来源类型': {'standard': '标准', 'custom': '实验室自定要求',
                       'record_only': '实验室自定要求（不自动评价）'}.get(review.get('decision'),
                           '原质量目标' if goal else '未确认'),
            '来源名称': recorded.get('source_name') or spec.get('standard', ''),
            '来源版本或编号': recorded.get('source_version') or spec.get('version', ''),
            '要求内容': content,
            '本实验室对照与质控要求': review.get('process_requirements', {}).get('requirement_text', ''),
            '来源条款': spec.get('source_clause', ''), '适用范围': spec.get('scope', ''),
            '来源实施日期': spec.get('effective_date', ''),
            '确认人': review.get('confirmed_by') or goal.get('confirmed_by', ''),
            '确认时间': review.get('reviewed_at') or goal.get('adopted_at', ''),
            '适用依据': review.get('evidence') or goal.get('evidence', ''),
            '结构化适用条件': conditions,
            '标准查找复核': '；'.join(str(search.get(k,'')) for k in ('query','official_url','checked_on','conclusion','rationale')) if search else '',
            '实验室更严CV': goal.get('supplement',{}).get('cv',''),
            '用途说明': '供查阅；本表不参与导入，导入后请重新确认质量目标。',
        })
        for candidate in review.get('candidates', []):
            candidate_rows.append({
                '检验项目': _text(item['test_item_name']),
                '输入值类型': INPUT_VALUE_TYPE_LABELS.get(_text(item['input_value_type']), _text(item['input_value_type'])),
                '标准名称及版本': candidate.get('source', ''),
                '适用情况': {'adopted': '已采用', 'not_applicable': '不适用', 'referenced': '参考来源',
                         'not_selected': '未选为参考', 'pending': '参考条件待核对'}.get(candidate.get('disposition'), '未确认'),
                '说明': candidate.get('reason', ''),
                '核对状态': state,
                '确认人': review.get('confirmed_by', ''),
                '确认时间': review.get('reviewed_at', ''),
            })
        for source in review.get('registered_standards', []):
            candidate_rows.append({'检验项目':_text(item['test_item_name']),
                '输入值类型':INPUT_VALUE_TYPE_LABELS.get(_text(item['input_value_type']),''),
                '标准名称及版本':source['standard']+' / '+source['version'],
                '适用情况':'补充登记，未自动评价','说明':source['source_clause']+'；'+source['requirement_text'],
                '核对状态':state,'确认人':source['verified_by'],'确认时间':source['checked_on']})
    return [
        ('质量目标（供查阅）', pd.DataFrame(review_rows, columns=[
            '检验项目', '输入值类型', '单位', '方法学', '核对状态', '来源类型', '来源名称',
            '来源版本或编号', '要求内容', '本实验室对照与质控要求', '来源条款', '适用范围', '来源实施日期', '确认人',
            '确认时间', '适用依据', '结构化适用条件', '标准查找复核', '实验室更严CV', '用途说明'])),
        ('标准适用情况（供查阅）', pd.DataFrame(candidate_rows, columns=[
            '检验项目', '输入值类型', '标准名称及版本', '适用情况', '说明', '核对状态', '确认人', '确认时间'])),
    ]


def build_project_template_xlsx(template_id: int) -> bytes:
    template = get_project_template(template_id)
    with get_connection() as connection:
        instrument_uid = connection.execute('SELECT uid FROM lab_instruments WHERE id=?', (template['lab_instrument_id'],)).fetchone()['uid']
    overview = pd.DataFrame(
        [
            ["项目名称", template["template_name"]],
            ["本地仪器", template["instrument_name"]],
            ["仪器标识", instrument_uid],
            ["仪器厂家", template["instrument_manufacturer_name"]],
            ["仪器型号", template["instrument_model"]],
            ["默认试剂", template["default_reagent_name"]],
            ["默认试剂商品名", template["default_reagent_trade_name"]],
            ["默认试剂厂家", template["default_reagent_manufacturer_name"]],
            ["质控品", template["qc_material_name"]],
            ["质控品商品名", template["qc_material_trade_name"]],
            ["质控品厂家", template["qc_manufacturer_name"]],
            ["状态", "项目设置已确认" if template["status"] == "active" else "待确认"],
            ["修订号", template["revision_no"]],
        ],
        columns=["字段", "值"],
    )
    return dataframes_to_xlsx_bytes(
        OrderedDict(
            [
                ("项目信息", overview),
                ("项目配置", _project_items_export_dataframe(template_id)),
                *_material_context_sheets(int(template['qc_material_id'])),
                ("填写说明", _instructions_dataframe()),
                *_quality_review_export_sheets(list_template_items(template_id)),
            ]
        )
    )


def build_lot_config_xlsx(lot_config_id: int) -> bytes:
    config = get_lot_config(lot_config_id)
    items = list_lot_config_items(lot_config_id)
    overview = pd.DataFrame(
        [
            ["批次名称", config["config_name"]],
            ["配置标识", config['uid']],
            ["项目", config["template_name"]],
            ["本地仪器", config["instrument_name"]],
            ["质控品", config["qc_material_name"]],
            ["质控品商品名", config["qc_material_trade_name"]],
            ["批号", config["lot_no"]],
            ["效期", config["expiry_date"]],
            ["状态", "批次设置已确认" if config["status"] == "active" else "待确认"],
            ["修订号", config["revision_no"]],
            ["复制来源批次编号", config["copied_from_config_id"]],
            ["设置确认时间", config["activated_at"]],
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
            "target_n": "参数建立点数",
            "cv_limit": "允许不精密度(CV%)",
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
        "参数建立点数",
        "允许不精密度(CV%)",
        "质量目标来源",
    ]
    item_export = item_export.reindex(columns=item_columns)

    level_rows: list[dict[str, object]] = []
    for _, item in items.iterrows():
        levels = list_lot_item_levels(int(item["id"]))
        for _, level in levels.iterrows():
            with get_connection() as connection:
                identities = connection.execute('''SELECT l.uid AS level_uid,b.uid AS lot_uid,s.uid AS specification_uid
                    FROM md_qc_levels l JOIN md_qc_material_lots b ON b.id=l.qc_material_lot_id
                    LEFT JOIN md_qc_material_specs s ON s.id=l.specification_id WHERE l.id=?''',
                    (int(level['qc_level_id']),)).fetchone()
            level_rows.append(
                {
                    "检验项目": _text(item["test_item_name"]),
                    "质控方法": QC_METHOD_LABELS[_text(item['qc_method'])],
                    "输入值类型": INPUT_VALUE_TYPE_LABELS[_text(item['input_value_type'])],
                    "单位": _text(item['unit_symbol']),
                    "方法学": _text(item['method_name']),
                    "水平顺序": int(level["level_order"]),
                    "浓度水平": _text(level["level_name"]),
                    "实际批号": _text(level["lot_no"]),
                    "效期": _text(level["expiry_date"]),
                    "浓度编号": _text(level["level_code"]),
                    "均值和标准差来源": TARGET_SOURCE_LABELS.get(
                        _text(level["target_source"]), _text(level["target_source"])
                    ),
                    "设定均值": "" if pd.isna(level["target_mean"]) else float(level["target_mean"]),
                    "SD": "" if pd.isna(level["target_sd"]) else float(level["target_sd"]),
                    "设定变异系数（%）": calculate_cv_percent(level["target_mean"], level["target_sd"]) if level["target_source"] != "building" else None,
                    "已确认": bool(level["target_confirmed"]),
                    "备注": _text(level["notes"]),
                    "材料批次标识": identities['lot_uid'],
                    "材料水平标识": identities['level_uid'],
                    "目录规格标识": identities['specification_uid'] or '',
                }
            )
    level_export = pd.DataFrame(
        level_rows,
        columns=["检验项目", "质控方法", "输入值类型", "单位", "方法学", "水平顺序", "浓度水平", "浓度编号", "实际批号", "效期", "均值和标准差来源", "设定均值", "SD", "设定变异系数（%）", "已确认", "备注", "材料批次标识", "材料水平标识", "目录规格标识"],
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
                ("批次信息", overview),
                ("项目配置", item_export),
                ("水平均值和标准差", level_export),
                *_material_context_sheets(int(config['qc_material_id'])),
                ("修订记录", snapshots),
                *_quality_review_export_sheets(items),
            ]
        )
    )


def preview_project_template_xlsx(data: bytes) -> tuple[pd.DataFrame, list[str]]:
    sheets = xlsx_bytes_to_dataframes(data)
    if "项目配置" not in sheets:
        raise ValueError("XLSX 必须包含名为“项目配置”的工作表。")
    source = sheets["项目配置"].copy()
    # Accept older exported templates; reject ambiguous duplicate old/new fields.
    for old, new in {"建靶点数*": "参数建立点数*", "CV要求(%)": "允许不精密度(CV%)"}.items():
        if old in source.columns:
            if new in source.columns:
                raise ValueError(f"项目配置同时包含旧列“{old}”与新列“{new}”，请保留一列后导入。")
            source = source.rename(columns={old: new})
    missing_columns = [column for column in PROJECT_IMPORT_COLUMNS if column not in source.columns]
    if missing_columns:
        raise ValueError("项目配置工作表缺少列：" + "、".join(missing_columns))
    source = source.reindex(columns=PROJECT_IMPORT_COLUMNS + [c for c in PROJECT_IDENTITY_COLUMNS if c in source.columns])
    source = source[
        source.apply(lambda row: any(_text(value) for value in row.tolist()), axis=1)
    ]
    if source.empty:
        raise ValueError("项目配置工作表没有可导入的数据行。")

    normalized_rows: list[dict[str, object]] = []
    errors: list[str] = []
    seen_names: set[tuple] = set()
    for index, row in source.iterrows():
        excel_row = index + 2
        try:
            item_name = _text(row["检验项目*"])
            if not item_name:
                raise ValueError("检验项目不能为空。")
            qc_method = _QC_METHOD_BY_TEXT.get(_key(row["质控方法*"]))
            if qc_method is None:
                raise ValueError("质控方法必须为 LJ、Z-score 或 即时法。")
            input_value_type = _INPUT_VALUE_TYPE_BY_TEXT.get(_key(row["输入值类型*"]))
            if input_value_type is None:
                raise ValueError("输入值类型必须为真实检测值、Ct值或log值。")
            identity = _text(row.get("检验项目标识")) or _key(row["标准编码"]) or item_name.casefold()
            row_key = (identity, qc_method, input_value_type)
            if row_key in seen_names:
                raise ValueError("同一文件中检验项目、质控方法和输入值类型组合重复。")
            seen_names.add(row_key)
            unit_symbol = _text(row["单位*"])
            method_name = _text(row["方法学*"])
            reagent_name = _text(row["试剂通用名*"])
            if not unit_symbol or not method_name or not reagent_name:
                raise ValueError("单位、方法学和试剂通用名均为必填。")
            level_count = _required_integer(row["水平数*"], "水平数")
            target_n = _required_integer(row["参数建立点数*"], "参数建立点数")
            if qc_method in {"lj", "instant"} and level_count != 1:
                raise ValueError("LJ 和即时法只能配置 1 个水平。")
            if qc_method == "zscore" and level_count not in {2, 3}:
                raise ValueError("Z-score 只能配置 2 或 3 个水平。")
            if qc_method == "instant":
                target_n = 20
            elif not 5 <= target_n <= 20:
                raise ValueError("LJ 和 Z-score 参数建立点数必须在 5 至 20 之间。")
            cv_limit = _optional_float(row["允许不精密度(CV%)"])
            if cv_limit is not None and (not math.isfinite(cv_limit) or cv_limit <= 0):
                raise ValueError("允许不精密度（CV）必须大于 0。")
            normalized_rows.append(
                {
                    "文件行号": excel_row,
                    **{column: _text(row.get(column)) for column in PROJECT_IDENTITY_COLUMNS},
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
                    "参数建立点数": target_n,
                    "允许不精密度(CV%)": cv_limit,
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
                "excel_row": int(row["文件行号"]),
                "test_item_uid": _text(row["检验项目标识"]),
                "unit_uid": _text(row["单位标识"]),
                "method_uid": _text(row["方法学标识"]),
                "reagent_uid": _text(row["试剂标识"]),
                "manufacturer_uid": _text(row["试剂厂家标识"]),
                "manufacturer_categories": _text(row['试剂厂家类别']),
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
                "target_n": int(row["参数建立点数"]),
                "cv_limit": None if pd.isna(row["允许不精密度(CV%)"]) else float(row["允许不精密度(CV%)"]),
                "quality_target_source_text": _text(row["质量目标来源"]),
                "notes": _text(row["备注"]),
            }
        )
    return result


class _ImportResolver:
    """One exact-identity plan, shared by read-only preview and atomic import."""
    def __init__(self, connection, *, create=False):
        self.connection, self.create = connection, create
        self.created = {"检验项目": 0, "单位": 0, "方法学": 0, "厂家": 0, "试剂": 0}
        self.records = {table: [dict(r) for r in connection.execute(f'SELECT * FROM {table}')]
                        for table in ('md_units', 'md_methods', 'md_manufacturers', 'md_reagents', 'md_test_items')}
        self.next_virtual = -1
        self.planned_manufacturer_categories = {}

    def resolve(self, table, uid, key_values, label, factory):
        records = self.records[table]
        identified = next((r for r in records if uid and r['uid'] == uid), None)
        natural = [r for r in records if all(_key(r.get(k)) == _key(v) for k, v in key_values.items())]
        if identified and any(_key(identified.get(k)) != _key(v) for k, v in key_values.items()):
            raise ValueError(f'{label}标识与名称或关联不一致。')
        if len(natural) > 1:
            active = [r for r in natural if not r['is_disabled']]
            if len(active) != 1 or identified is not None:
                raise ValueError(f'{label}存在多条同名资料，请使用明确标识。')
            natural = active
        found = identified or (natural[0] if natural else None)
        if found is not None:
            if found['is_disabled']:
                raise ValueError(f'{label}已停用，请先核对资料。')
            return int(found['id'])
        if self.create:
            new_id = factory()
            if uid:
                self.connection.execute(f'UPDATE {table} SET uid=? WHERE id=?', (uid, new_id))
        else:
            new_id = self.next_virtual
            self.next_virtual -= 1
        records.append(dict(id=new_id, uid=uid, is_disabled=0, **key_values))
        self.created[label] += 1
        return new_id

    def test_item(self, row, unit_id=None):
        uid, code, name = row.get('test_item_uid', ''), row.get('standard_code', ''), row['item_name']
        records = self.records['md_test_items']
        if code:
            code_matches = [r for r in records if _key(r.get('standard_code')) == _key(code)]
            if any(_key(r.get('chinese_name')) != _key(name) for r in code_matches):
                raise ValueError('检验项目标准编码与名称不一致。')
        same_name = [r for r in records if _key(r.get('chinese_name')) == _key(name)]
        if code and any(_key(r.get('standard_code')) != _key(code) for r in same_name):
            raise ValueError('同名检验项目的标准编码不一致。')
        keys = dict(chinese_name=name)
        if code:
            keys['standard_code'] = code
        return self.resolve('md_test_items', uid, keys, '检验项目', lambda: create_test_item(
            chinese_name=name, standard_code=code, abbreviation=row.get('abbreviation', ''), default_unit_id=unit_id))

    def configuration_rows(self, imported):
        rows, errors = [], []
        for order, row in enumerate(imported, 1):
            try:
                unit_id = self.resolve('md_units', row['unit_uid'], dict(symbol=row['unit_symbol']), '单位',
                                       lambda: create_unit(symbol=row['unit_symbol']))
                method_id = self.resolve('md_methods', row['method_uid'], dict(method_name=row['method_name']), '方法学',
                                         lambda: create_method(method_name=row['method_name']))
                manufacturer_id = None
                categories = [c.strip() for c in row['manufacturer_categories'].split(',') if c.strip()] or ['reagent']
                if set(categories) - {'instrument', 'reagent', 'qc_material'} or 'reagent' not in categories:
                    raise ValueError('试剂厂家类别未知或未包含试剂类别。')
                if row['reagent_manufacturer']:
                    manufacturer_id = self.resolve('md_manufacturers', row['manufacturer_uid'],
                        dict(display_name=row['reagent_manufacturer']), '厂家',
                        lambda: create_manufacturer(display_name=row['reagent_manufacturer'], categories=categories))
                    if manufacturer_id > 0:
                        from services.master_data_service import require_manufacturer_category, get_manufacturer_categories
                        require_manufacturer_category(self.connection, manufacturer_id, 'reagent')
                        if not set(categories).issubset(get_manufacturer_categories(manufacturer_id, self.connection)):
                            raise ValueError('文件所列试剂厂家类别与现有厂家资料不一致，请先核对类别。')
                    else:
                        known = self.planned_manufacturer_categories.setdefault(manufacturer_id, set(categories))
                        if not set(categories).issubset(known):
                            raise ValueError('同一文件中的试剂厂家类别不一致，请统一核对。')
                else:
                    raise ValueError('请填写已明确的试剂厂家；厂家必须具有试剂类别。')
                reagent_id = self.resolve('md_reagents', row['reagent_uid'],
                    dict(manufacturer_id=manufacturer_id, generic_name=row['reagent_name'], trade_name=row['reagent_trade_name']),
                    '试剂', lambda: create_reagent(generic_name=row['reagent_name'], manufacturer_id=manufacturer_id,
                                                trade_name=row['reagent_trade_name']))
                test_item_id = self.test_item(row, unit_id)
                rows.append(dict(test_item_id=test_item_id, unit_id=unit_id, method_id=method_id, reagent_id=reagent_id,
                    **{k: row[k] for k in ('qc_method', 'input_value_type', 'level_count', 'target_n', 'cv_limit',
                                          'quality_target_source_text', 'notes')}, sort_order=order))
            except (ValueError, TypeError) as exc:
                errors.append(f"第 {row['excel_row']} 行：{exc}")
        return rows, errors


def _read_material_context(data):
    sheets = xlsx_bytes_to_dataframes(data)
    if '产品来源' not in sheets:
        return None
    rows = sheets['产品来源'].to_dict('records')
    if len(rows) != 1:
        raise ValueError('产品来源工作表必须恰好包含所选产品的一条资料。')
    row = rows[0]
    product_fields = {'uid': '产品标识', 'product_code': '产品编号', 'product_name': '产品名称',
                      'concentration': '浓度', 'concentration_code': '浓度编号', 'external_key': '外部产品键',
                      'source_code': '来源编号', 'version_label': '来源版本', 'source_sha256': '来源校验值'}
    product = {key: ('' if pd.isna(row.get(label)) else str(row.get(label))) for key, label in product_fields.items()}
    manufacturer = dict(uid=_text(row.get('厂家标识')), display_name=_text(row.get('厂家名称')),
                        categories=[c for c in _text(row.get('厂家类别')).split(',') if c])
    coverage = []
    fields = {'test_item_id': '来源项目号', 'test_item_uid': '检验项目标识', 'standard_code': '标准编码',
              'test_item_name': '检验项目', 'source_kind': '关系来源', 'source_version': '依据版本',
              'evidence': '覆盖依据', 'confirmed_by': '关系确认人', 'confirmed_at': '关系确认时间',
              'method_id': '来源方法号', 'method_uid': '方法学标识', 'method_name': '方法学', 'is_disabled': '关系停用'}
    for entry in sheets.get('产品覆盖', pd.DataFrame()).to_dict('records'):
        item = {key: _text(entry.get(label)) for key, label in fields.items()}
        if not item['test_item_uid'] and not item['standard_code']:
            raise ValueError('适用检验项目缺少项目编号或标准编码，请补全后重新预览。')
        item['test_item_id'] = _required_integer(item['test_item_id'], '来源项目号')
        item['method_id'] = _required_integer(item['method_id'], '来源方法号') if item['method_id'] else None
        item['is_disabled'] = _key(item['is_disabled']) in ('1', 'true', '是')
        coverage.append(item)
    return dict(product=product, manufacturer=manufacturer, coverage=coverage)


def _material_context_sheets(material_id):
    from services.product_directory_service import export_material_catalog_context
    context = export_material_catalog_context(material_id)
    product, manufacturer = context['product'], context['manufacturer']
    labels = {'uid': '产品标识', 'product_code': '产品编号', 'product_name': '产品名称',
              'concentration': '浓度', 'concentration_code': '浓度编号', 'external_key': '外部产品键',
              'source_code': '来源编号', 'version_label': '来源版本', 'source_sha256': '来源校验值'}
    row = {label: product.get(key, '') for key, label in labels.items()}
    row.update({'厂家标识': manufacturer.get('uid', ''), '厂家名称': manufacturer.get('display_name', ''),
                '厂家类别': ','.join(manufacturer.get('categories', []))})
    fields = {'test_item_id': '来源项目号', 'test_item_uid': '检验项目标识', 'standard_code': '标准编码',
              'test_item_name': '检验项目', 'source_kind': '关系来源', 'source_version': '依据版本',
              'evidence': '覆盖依据', 'confirmed_by': '关系确认人', 'confirmed_at': '关系确认时间',
              'method_id': '来源方法号', 'method_uid': '方法学标识', 'method_name': '方法学', 'is_disabled': '关系停用'}
    coverage = [{label: item.get(key, '') for key, label in fields.items()} for item in context.get('coverage', [])]
    return [('产品来源', pd.DataFrame([row])), ('产品覆盖', pd.DataFrame(coverage, columns=list(fields.values())))]


def _resolve_material_context(connection, template, context, resolver, *, apply=False, expected_version=None):
    if context is None:
        return None
    from services.product_directory_service import (
        validate_material_catalog_context, apply_material_catalog_context, get_product_relationships)
    material_id = int(template['qc_material_id'])
    validate_material_catalog_context(connection, material_id, context)
    version = get_product_relationships(material_id)['edit_version']
    if expected_version is not None and version != expected_version:
        raise ValueError('此质控品的适用项目资料已变化，请重新预览后导入。')
    mapping, method_mapping = {}, {}
    for entry in context.get('coverage', []):
        if entry.get('source_kind') not in ('local', 'directory') or not _text(entry.get('confirmed_by')) or not _text(entry.get('evidence')):
            raise ValueError('适用检验项目缺少有效的资料来源、确认人或依据，请补全后重新预览。')
        if entry['source_kind'] == 'directory' and entry.get('source_version') != context['product'].get('version_label'):
            raise ValueError('适用检验项目所用厂家资料的版本与此质控品目录不一致，请核对后重新预览。')
        source_id = int(entry['test_item_id'])
        mapping[source_id] = resolver.test_item(dict(test_item_uid=entry.get('test_item_uid', ''),
            standard_code=entry.get('standard_code', ''), item_name=entry['test_item_name']))
        if entry.get('method_uid') or entry.get('method_name'):
            if not entry.get('method_name') or entry.get('method_id') is None:
                raise ValueError('适用检验项目的方法学缺少名称，请在文件中补全后重新预览。')
            method_mapping[int(entry['method_id'])] = resolver.resolve('md_methods', entry.get('method_uid', ''),
                dict(method_name=entry['method_name']), '方法学', lambda: create_method(method_name=entry['method_name']))
    if apply:
        apply_material_catalog_context(material_id, context, mapping, expected_version=version, method_id_map=method_mapping)
    return version


def prepare_project_template_import(template_id: int, data: bytes, *, mode='merge') -> dict:
    with read_snapshot():
        return _prepare_project_template_import(template_id, data, mode=mode)


def _prepare_project_template_import(template_id: int, data: bytes, *, mode='merge') -> dict:
    if mode not in {'merge', 'replace'}:
        raise ValueError('导入方式必须为合并或替换。')
    template = get_project_template(template_id)
    if template['is_disabled']:
        raise ValueError('目标项目已停用。')
    preview, errors = preview_project_template_xlsx(data)
    current = template_item_rows(template_id)
    resolved, created, product_version = [], {}, None
    if not errors:
        with get_connection() as connection:
            resolver = _ImportResolver(connection)
            resolved, errors = resolver.configuration_rows(_normalized_import_rows(data))
            try:
                product_version = _resolve_material_context(connection, template, _read_material_context(data), resolver)
            except (TypeError, ValueError) as exc:
                errors.append('产品来源或适用检验项目：' + str(exc))
            created = resolver.created
    imported_keys = {template_item_key(row) for row in resolved}
    if len(imported_keys) != len(resolved):
        errors.append('文件中的不同名称或标识实际指向同一检验项目、质控方法和输入值类型，请核对重复行。')
    removals = [row for row in current if template_item_key(row) not in imported_keys] if mode == 'replace' else []
    return dict(template_id=int(template_id), expected_revision=int(template['revision_no']),
        file_sha256=hashlib.sha256(data).hexdigest(), mode=mode, preview=preview, errors=errors,
        removals=removals, created=created, product_version=product_version)


def preview_panel_import_rows(template_id: int, data: bytes) -> list[dict]:
    if len(data) > 10 * 1024 * 1024:
        raise ValueError('上传文件超过 10 MB。')
    prepared = prepare_project_template_import(template_id, data)
    if prepared['errors']:
        raise ValueError('\n'.join(prepared['errors']))
    if any(prepared['created'].values()):
        raise ValueError('清单包含尚未登记的基础资料，请先到项目与批次的“导入导出”核对并导入。')
    with get_connection() as connection:
        rows, errors = _ImportResolver(connection).configuration_rows(_normalized_import_rows(data))
        if errors:
            raise ValueError('\n'.join(errors))
        for row in rows:
            row['test_item_name'] = connection.execute('SELECT chinese_name FROM md_test_items WHERE id=?',
                                                       (row['test_item_id'],)).fetchone()['chinese_name']
        return rows


def import_project_template_xlsx(template_id: int, data: bytes, *, mode='merge', expected_revision=None,
                                 file_sha256=None, product_version=None, replace_confirmed=False) -> dict:
    if file_sha256 is not None and hashlib.sha256(data).hexdigest() != file_sha256:
        raise ValueError('导入文件已变化，请重新预览。')
    if mode == 'replace' and expected_revision is not None and not replace_confirmed:
        raise ValueError('请再次确认将移除的检验项目后再替换。')
    # Includes every vocabulary creation and every new relationship. A failure in
    # the last row leaves no partial dictionaries, coverage, or panel rows behind.
    with atomic_write() as connection:
        prepared = prepare_project_template_import(template_id, data, mode=mode)
        if expected_revision is not None and int(expected_revision) != prepared['expected_revision']:
            raise ValueError('目标项目已发生变化，请重新预览。文件与本次填写仍保留。')
        if prepared['errors']:
            raise ValueError('导入文件校验未通过：\n' + '\n'.join(prepared['errors']))
        template = get_project_template(template_id)
        resolver = _ImportResolver(connection, create=True)
        imported, errors = resolver.configuration_rows(_normalized_import_rows(data))
        if errors:
            raise ValueError('导入失败：\n' + '\n'.join(errors))
        _resolve_material_context(connection, template, _read_material_context(data), resolver,
                                  apply=True, expected_version=product_version)
        merged = {template_item_key(row): row for row in template_item_rows(template_id)} if mode == 'merge' else {}
        merged.update({template_item_key(row): row for row in imported})
        rows = list(merged.values())
        for order, row in enumerate(rows, 1):
            row['sort_order'] = order
        save_template_items(template_id, rows, expected_revision=prepared['expected_revision'])
        from services.quality_review_service import pending_review_copy
        for row in imported:
            existing = connection.execute('''SELECT id,quality_review_json FROM qc_project_template_items
                WHERE template_id=? AND test_item_id=? AND qc_method=? AND input_value_type=? AND is_disabled=0''',
                (template_id, *template_item_key(row))).fetchone()
            connection.execute('UPDATE qc_project_template_items SET quality_review_json=? WHERE id=?',
                (pending_review_copy(existing['quality_review_json']), existing['id']))
        return dict(imported_count=len(imported), saved_count=len(rows), mode=mode, created=resolver.created)
