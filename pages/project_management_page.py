from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from database import get_connection
from ui.cv import CV_REQUIREMENT_HELP, render_target_cv

from services.master_data_service import (
    list_lab_instruments,
    list_methods,
    list_qc_levels,
    list_qc_lots,
    list_qc_materials,
    list_reagents,
    list_test_items,
    list_units,
)
from services.project_config_io_service import (
    build_lot_config_xlsx,
    build_project_import_template_xlsx,
    build_project_template_xlsx,
    import_project_template_xlsx,
    preview_project_template_xlsx,
)
from services.project_config_service import (
    INPUT_VALUE_TYPE_LABELS,
    QC_METHOD_LABELS,
    QC_METHOD_LEGACY_LABELS,
    TARGET_SOURCE_LABELS,
    activate_lot_config,
    activate_project_template,
    copy_lot_config,
    create_lot_config_from_template,
    create_project_template,
    get_lot_config,
    get_project_template,
    list_config_snapshots,
    list_lot_config_items,
    list_lot_configs,
    list_lot_item_levels,
    list_project_templates,
    list_template_items,
    save_lot_item_levels,
    save_lot_item_cv_requirement,
    save_template_items,
    set_lot_config_disabled,
    set_project_template_disabled,
    set_template_default_reagent,
    validate_lot_config,
    validate_project_template,
)
from ui.common import open_global_page, render_section_intro
from services.quality_review_service import validate_quality_review


QC_METHOD_BY_LABEL = {label: code for code, label in QC_METHOD_LABELS.items()}
INPUT_VALUE_TYPE_BY_LABEL = {
    label: code for code, label in INPUT_VALUE_TYPE_LABELS.items()
}
TARGET_SOURCE_BY_LABEL = {
    label: code for code, label in TARGET_SOURCE_LABELS.items()
}


def _safe_text(value: object, fallback: str = "-") -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return fallback
    cleaned = " ".join(str(value).split()).strip()
    return cleaned or fallback


def _safe_file_fragment(value: object, fallback: str = "configuration") -> str:
    cleaned = _safe_text(value, fallback)
    result = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in cleaned
    ).strip("_")
    return result[:80] or fallback


def _option_map(
    dataframe: pd.DataFrame,
    label_builder,
    *,
    placeholder: str,
) -> tuple[list[str], dict[str, int | None], dict[int, str]]:
    labels = [placeholder]
    by_label: dict[str, int | None] = {placeholder: None}
    by_id: dict[int, str] = {}
    for _, row in dataframe.iterrows():
        entity_id = int(row["id"])
        base_label = str(label_builder(row))
        label = base_label
        suffix = 2
        while label in by_label:
            label = f"{base_label}（{suffix}）"
            suffix += 1
        labels.append(label)
        by_label[label] = entity_id
        by_id[entity_id] = label
    return labels, by_label, by_id


def _instrument_label(row: pd.Series) -> str:
    manufacturer = _safe_text(row.get("manufacturer_name"), "未维护厂家")
    return (
        f"{_safe_text(row.get('display_name'))}｜{manufacturer}｜"
        f"{_safe_text(row.get('model'))}"
    )


def _material_label(row: pd.Series) -> str:
    manufacturer = _safe_text(row.get("manufacturer_name"), "未维护厂家")
    trade_name = _safe_text(row.get("trade_name"), "")
    label = f"{manufacturer}｜{_safe_text(row.get('generic_name'))}"
    return label + (f"｜{trade_name}" if trade_name else "")


def _reagent_label(row: pd.Series) -> str:
    manufacturer = _safe_text(row.get("manufacturer_name"), "未维护厂家")
    trade_name = _safe_text(row.get("trade_name"), "")
    label = f"{manufacturer}｜{_safe_text(row.get('generic_name'))}"
    return label + (f"｜{trade_name}" if trade_name else "")


def _test_item_label(row: pd.Series) -> str:
    code = _safe_text(row.get("standard_code"), "")
    abbreviation = _safe_text(row.get("abbreviation"), "")
    parts = [_safe_text(row.get("chinese_name"))]
    if abbreviation:
        parts.append(abbreviation)
    if code:
        parts.append(code)
    return "｜".join(parts)


def _template_label(row: pd.Series) -> str:
    status = "设置已确认" if str(row.get("status")) == "active" else "待确认"
    return (
        f"{_safe_text(row.get('template_name'))}｜"
        f"{_safe_text(row.get('instrument_name'))}｜"
        f"{int(row.get('item_count', 0) or 0)} 项｜{_safe_text(row.get('project_group'), '未分组')}｜"
        f"{', '.join(QC_METHOD_LABELS.get(x,x) for x in _safe_text(row.get('qc_methods'), '待配置').split(','))}｜{_safe_text(row.get('method_names'), '方法学待配置')}｜{status}"
    )


def _lot_label(row: pd.Series) -> str:
    return (
        f"批号 {_safe_text(row.get('lot_no'))}｜"
        f"效期 {_safe_text(row.get('expiry_date'), '未填写')}"
    )


def _config_label(row: pd.Series) -> str:
    status_labels = {
        "draft": "待确认",
        "active": "设置已确认",
        "superseded": "已替代",
        "disabled": "已停用",
    }
    return (
        f"{_safe_text(row.get('config_name'))}｜批号 {_safe_text(row.get('lot_no'))}｜"
        f"{status_labels.get(str(row.get('status')), str(row.get('status')))}"
    )


def _find_dataframe_row(dataframe: pd.DataFrame, entity_id: int | None) -> pd.Series | None:
    if entity_id is None or dataframe.empty:
        return None
    matched = dataframe[dataframe["id"].astype(int) == int(entity_id)]
    if matched.empty:
        return None
    return matched.iloc[0]


def _select_current_entity(
    *,
    dataframe: pd.DataFrame,
    label_builder,
    placeholder: str,
    label: str,
    key: str,
    state_key: str,
) -> int | None:
    labels, by_label, by_id = _option_map(
        dataframe,
        label_builder,
        placeholder=placeholder,
    )
    current_id = st.session_state.get(state_key)
    if current_id is not None and int(current_id) in by_id:
        expected_label = by_id[int(current_id)]
        if st.session_state.get(key) not in labels:
            st.session_state[key] = expected_label
    elif st.session_state.get(key) not in labels:
        st.session_state[key] = placeholder
    selected_label = st.selectbox(label, options=labels, key=key)
    selected_id = by_label[selected_label]
    st.session_state[state_key] = selected_id
    return selected_id


def _render_template_creation() -> None:
    instruments = list_lab_instruments()
    materials = list_qc_materials()
    reagents = list_reagents()
    instrument_labels, instrument_map, _ = _option_map(
        instruments,
        _instrument_label,
        placeholder="请选择本地仪器",
    )
    material_labels, material_map, _ = _option_map(
        materials,
        _material_label,
        placeholder="请选择质控品",
    )
    reagent_labels, reagent_map, _ = _option_map(
        reagents, _reagent_label, placeholder="请选择试剂",
    )
    with st.expander("新建项目", expanded=False):
        if instruments.empty or materials.empty or reagents.empty:
            st.warning("请先到“基础资料”完成本地仪器、试剂和质控品维护。")
        with st.container(border=True):
            template_name = st.text_input("项目名称 *")
            col1, col2, col3 = st.columns(3)
            with col1:
                instrument_label = st.selectbox("本地仪器 *", instrument_labels)
            with col2:
                reagent_label = st.selectbox("试剂 *", reagent_labels)
            with col3:
                material_label = st.selectbox("质控品 *", material_labels)
            st.caption("所选试剂将作为新增检验项目的默认试剂；不同检验项目可分别调整。")
            defaults_left,defaults_right=st.columns(2)
            default_kind=defaults_left.selectbox('默认质控方法',['单水平','多水平联合'],key='create_default_kind',
                format_func=lambda value: QC_METHOD_LABELS['lj' if value=='单水平' else 'zscore'])
            default_qc='zscore' if default_kind=='多水平联合' else 'lj'
            if default_kind=='单水平':
                approach=st.selectbox('单水平参数建立流程',['常规 LJ（建立参数或使用已确认参数）','即时法积累，满足条件后人工转入 LJ'],key='create_building_approach')
                if approach.startswith('即时法'): default_qc='instant'
            methods=list_methods()
            method_ids=[None]+methods['id'].tolist()
            method_names={r['id']:r['method_name'] for r in methods.to_dict('records')}
            default_method=defaults_right.selectbox('默认检测方法学',method_ids,format_func=lambda k:'请选择，或在检验项目中逐项设置' if k is None else method_names[k],key='create_default_method')
            default_count=st.selectbox('多水平使用数量',[2,3],key='create_default_count') if default_qc=='zscore' else 1
            project_group=st.text_input('工作分组（选填）',placeholder='例如：血筛、生化、分子；用于归类，不限制质控方式',key='create_project_group')
            st.caption('LJ 图与 Z-score 不按检测专业划分。这里的默认设置在添加检验项目时带入，每项仍可调整；生效方式以各检验项目为准。')
            notes = st.text_input("项目备注")
            submitted = st.button(
                "创建项目", key="create_project_submit",
                type="primary",
                width="stretch",
            )
            if submitted:
                instrument_id = instrument_map[instrument_label]
                material_id = material_map[material_label]
                reagent_id = reagent_map[reagent_label]
                if instrument_id is None or material_id is None or reagent_id is None:
                    st.error("请选择本地仪器、试剂和质控品。")
                else:
                    try:
                        template_id = create_project_template(
                            template_name=template_name,
                            lab_instrument_id=int(instrument_id),
                            qc_material_id=int(material_id),
                            default_reagent_id=int(reagent_id),
                            default_qc_method=default_qc,default_method_id=default_method,
                            default_level_count=default_count,project_group=project_group,
                            notes=notes,
                        )
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state["v11_selected_template_id"] = template_id
                        st.success("项目已创建，请继续批量添加检验项目。")
                        st.rerun()


def _template_item_editor_rows(items: pd.DataFrame, lookups: dict[str, object]) -> pd.DataFrame:
    if items.empty:
        return pd.DataFrame(
            columns=[
                "保留",
                "test_item_id",
                "检验项目",
                "质控方法",
                "输入值类型",
                "单位",
                "方法学",
                "试剂",
                "水平数",
                "参数建立点数",
                "允许不精密度(CV%)",
                "质量目标来源",
            ]
        )
    return pd.DataFrame(
        {
            "保留": True,
            "test_item_id": items["test_item_id"].astype(int),
            "检验项目": items["test_item_name"].astype(str),
            "质控方法": items["qc_method"].map(QC_METHOD_LABELS),
            "输入值类型": items["input_value_type"].map(INPUT_VALUE_TYPE_LABELS),
            "单位": items["unit_symbol"].fillna("").astype(str),
            "方法学": items["method_name"].fillna("").astype(str),
            "试剂": items["reagent_id"].map(
                lambda value: "" if pd.isna(value) else lookups["reagent_label_by_id"].get(
                    int(value), f"已停用或不可用的试剂（#{int(value)}），请重新选择"
                )
            ),
            "水平数": items["level_count"].astype(int),
            "参数建立点数": items["target_n"].astype(int),
            "允许不精密度(CV%)": items["cv_limit"],
            "质量目标来源": items["quality_target_source_text"].fillna("").astype(str),
        }
    )


def _build_editor_lookup_options() -> dict[str, object]:
    units = list_units()
    methods = list_methods()
    reagents = list_reagents()
    unit_options = [_safe_text(value) for value in units["symbol"].tolist()]
    method_options = [_safe_text(value) for value in methods["method_name"].tolist()]
    reagent_labels, reagent_ids, reagent_label_by_id = _option_map(
        reagents, _reagent_label, placeholder=""
    )
    return {
        "units": units,
        "methods": methods,
        "reagents": reagents,
        "unit_options": unit_options,
        "method_options": method_options,
        "reagent_options": reagent_labels[1:],
        "reagent_label_by_id": reagent_label_by_id,
        "unit_id_by_label": {
            _safe_text(row.get("symbol")): int(row["id"]) for _, row in units.iterrows()
        },
        "method_id_by_label": {
            _safe_text(row.get("method_name")): int(row["id"])
            for _, row in methods.iterrows()
        },
        "reagent_id_by_label": reagent_ids,
    }


def _save_editor_rows(template_id: int, edited: pd.DataFrame, lookups: dict[str, object]) -> None:
    rows: list[dict[str, object]] = []
    for index, row in edited.iterrows():
        if not bool(row.get("保留", True)):
            continue
        qc_method_label = str(row.get("质控方法") or "")
        if qc_method_label in QC_METHOD_LEGACY_LABELS:
            qc_method_label = QC_METHOD_LABELS[QC_METHOD_LEGACY_LABELS[qc_method_label]]
        input_type_label = str(row.get("输入值类型") or "")
        unit_label = str(row.get("单位") or "")
        method_label = str(row.get("方法学") or "")
        reagent_label = str(row.get("试剂") or "")
        for field, value, lookup_key in (
            ("单位", unit_label, "unit_id_by_label"),
            ("方法学", method_label, "method_id_by_label"),
            ("试剂", reagent_label, "reagent_id_by_label"),
        ):
            if value and value not in lookups[lookup_key]:
                raise ValueError(f"第 {index + 1} 行的{field}已不在可选字典中，请重新选择后保存。")
        rows.append(
            {
                "test_item_id": int(row["test_item_id"]),
                "qc_method": QC_METHOD_BY_LABEL.get(qc_method_label, ""),
                "input_value_type": INPUT_VALUE_TYPE_BY_LABEL.get(input_type_label, ""),
                "unit_id": lookups["unit_id_by_label"].get(unit_label),
                "method_id": lookups["method_id_by_label"].get(method_label),
                "reagent_id": lookups["reagent_id_by_label"].get(reagent_label),
                "level_count": int(row.get("水平数") or 1),
                "target_n": int(row.get("参数建立点数") or 20),
                "cv_limit": None if pd.isna(row.get("允许不精密度(CV%)")) else row.get("允许不精密度(CV%)"),
                "quality_target_source_text": str(row.get("质量目标来源") or ""),
                "sort_order": index + 1,
            }
        )
    save_template_items(template_id, rows)


def _render_add_template_items(template_id: int, default_reagent_id: int | None) -> None:
    all_items = list_test_items()
    existing = list_template_items(template_id)
    existing_ids = set(existing["test_item_id"].astype(int).tolist()) if not existing.empty else set()
    available_items = all_items[~all_items["id"].astype(int).isin(existing_ids)].copy()
    item_labels, item_map, _ = _option_map(
        available_items,
        _test_item_label,
        placeholder="请选择检验项目",
    )
    selectable_labels = item_labels[1:]
    lookups = _build_editor_lookup_options()
    template=get_project_template(template_id)
    unit_options = list(lookups["unit_options"])
    method_options = list(lookups["method_options"])
    reagent_options = [""] + list(lookups["reagent_options"])
    default_reagent_label = lookups["reagent_label_by_id"].get(default_reagent_id, "")
    with st.expander("批量添加检验项目", expanded=existing.empty):
        if available_items.empty:
            st.info("没有更多可添加的启用检验项目。")
            return
        selected_labels = st.multiselect(
            "批量勾选检验项目",
            options=selectable_labels,
            key=f"v11_add_template_items_{template_id}",
        )
        row1, row2, row3 = st.columns(3)
        with row1:
            qc_method_label = st.selectbox(
                "批量质控方法",
                list(QC_METHOD_BY_LABEL),
                index=list(QC_METHOD_LABELS).index(template["default_qc_method"]),
                key=f"v11_bulk_qc_method_{template_id}",
            )
            input_type_label = st.selectbox(
                "批量输入值类型",
                list(INPUT_VALUE_TYPE_BY_LABEL),
                key=f"v11_bulk_input_type_{template_id}",
            )
        with row2:
            unit_label = st.selectbox(
                "批量单位",
                unit_options,
                key=f"v11_bulk_unit_{template_id}",
            )
            method_label = st.selectbox(
                "批量方法学",
                method_options,
                index=next((i for i,name in enumerate(method_options) if lookups["method_id_by_label"][name]==template["default_method_id"]),0),
                key=f"v11_bulk_method_{template_id}",
            )
        with row3:
            reagent_label = st.selectbox(
                "批量试剂",
                reagent_options,
                index=reagent_options.index(default_reagent_label),
                key=f"v11_bulk_reagent_{template_id}",
            )
            count_key=f"v11_bulk_level_count_{template_id}"
            is_multi=QC_METHOD_BY_LABEL[qc_method_label]=='zscore'
            if is_multi and st.session_state.get(count_key,template['default_level_count'])<2:
                st.session_state[count_key]=2
            if not is_multi:st.session_state[count_key]=1
            level_count = st.number_input(
                "批量水平数",
                min_value=2 if is_multi else 1,
                max_value=3 if is_multi else 1,
                value=max(2,template['default_level_count']) if is_multi else 1,
                disabled=not is_multi,
                step=1,
                key=f"v11_bulk_level_count_{template_id}",
            )
        target_n = st.slider(
            "批量参数建立有效点数",
            min_value=5,
            max_value=20,
            value=20,
            key=f"v11_bulk_target_n_{template_id}",
        )
        cv_limit = st.number_input(
            "允许不精密度（CV%，选填）", value=None, min_value=0.0, format="%.4f",
            help=CV_REQUIREMENT_HELP, key=f"v11_bulk_cv_limit_{template_id}",
        )
        cv_source = st.text_input("允许不精密度依据（选填）", key=f"v11_bulk_cv_source_{template_id}")
        if st.button(
            "添加到项目",
            key=f"v11_add_items_button_{template_id}",
            type="primary",
            width="stretch",
            disabled=not selected_labels or not reagent_label,
        ):
            combined_rows: list[dict[str, object]] = []
            for _, row in existing.iterrows():
                combined_rows.append(
                    {
                        "test_item_id": int(row["test_item_id"]),
                        "qc_method": str(row["qc_method"]),
                        "input_value_type": str(row["input_value_type"]),
                        "unit_id": int(row["unit_id"]) if not pd.isna(row["unit_id"]) else None,
                        "method_id": int(row["method_id"]) if not pd.isna(row["method_id"]) else None,
                        "reagent_id": int(row["reagent_id"]) if not pd.isna(row["reagent_id"]) else None,
                        "level_count": int(row["level_count"]),
                        "target_n": int(row["target_n"]),
                        "cv_limit": None if pd.isna(row["cv_limit"]) else float(row["cv_limit"]),
                        "quality_target_source_text": str(
                            row["quality_target_source_text"] or ""
                        ),
                        "sort_order": int(row["sort_order"]),
                    }
                )
            next_sort = len(combined_rows) + 1
            for offset, selected_label in enumerate(selected_labels):
                selected_item_id = item_map[selected_label]
                selected_method = QC_METHOD_BY_LABEL[qc_method_label]
                normalized_level_count = int(level_count)
                if selected_method in {"lj", "instant"}:
                    normalized_level_count = 1
                combined_rows.append(
                    {
                        "test_item_id": int(selected_item_id),
                        "qc_method": selected_method,
                        "input_value_type": INPUT_VALUE_TYPE_BY_LABEL[input_type_label],
                        "unit_id": lookups["unit_id_by_label"].get(unit_label),
                        "method_id": lookups["method_id_by_label"].get(method_label),
                        "reagent_id": lookups["reagent_id_by_label"].get(reagent_label),
                        "level_count": normalized_level_count,
                        "target_n": 20 if selected_method == "instant" else int(target_n),
                        "cv_limit": cv_limit,
                        "quality_target_source_text": cv_source,
                        "sort_order": next_sort + offset,
                    }
                )
            try:
                save_template_items(template_id, combined_rows)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success(f"已添加 {len(selected_labels)} 个检验项目。")
                st.rerun()


def _render_template_default_reagent(template) -> None:
    template_id = int(template["id"])
    labels, by_label, by_id = _option_map(
        list_reagents(), _reagent_label, placeholder="请选择试剂",
    )
    current = by_id.get(template["default_reagent_id"])
    with st.expander("项目默认试剂", expanded=current is None):
        st.caption("用于预填新增加的检验项目。修改默认值不会覆盖已有检验项目、批次或历史记录。")
        if current is None:
            st.warning("请从字典补选项目默认试剂；已有检验项目的试剂保留原设置。")
        selected = st.selectbox(
            "默认试剂 *", labels, index=labels.index(current) if current else 0,
            key=f"v12_default_reagent_{template_id}_{template['revision_no']}",
        )
        if st.button("保存默认试剂", key=f"v12_save_default_reagent_{template_id}",
                     disabled=by_label[selected] is None):
            try:
                set_template_default_reagent(template_id, int(by_label[selected]))
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state.pop(f"v11_bulk_reagent_{template_id}", None)
                st.rerun()


def _render_template_editor(template_id: int) -> None:
    template = get_project_template(template_id)
    items = list_template_items(template_id)
    st.caption(
        f"当前项目：{template['template_name']}｜仪器：{template['instrument_name']}｜"
        f"默认试剂：{_safe_text(template['default_reagent_name'], '尚未设置')}｜"
        f"质控品：{template['qc_material_name']}｜"
        f"资料状态：{'设置已确认' if template['status'] == 'active' else '待确认'}"
    )
    from ui.project_navigation import render_project_defaults
    render_project_defaults(template)
    _render_template_default_reagent(template)
    _render_add_template_items(template_id, template["default_reagent_id"])

    items = list_template_items(template_id)
    if items.empty:
        st.info("请先批量添加检验项目。")
    else:
        lookups = _build_editor_lookup_options()
        editor_df = _template_item_editor_rows(items, lookups)
        edited = st.data_editor(
            editor_df,
            hide_index=True,
            width="stretch",
            key=f"v11_template_item_editor_{template_id}_{int(template['revision_no'])}",
            column_config={
                "保留": st.column_config.CheckboxColumn("保留", default=True, width="small"),
                "test_item_id": None,
                "检验项目": st.column_config.TextColumn("检验项目", disabled=True, width="medium"),
                "质控方法": st.column_config.SelectboxColumn(
                    "质控方法",
                    options=list(QC_METHOD_BY_LABEL),
                    required=True,
                    width="medium",
                ),
                "输入值类型": st.column_config.SelectboxColumn(
                    "输入值类型",
                    options=list(INPUT_VALUE_TYPE_BY_LABEL),
                    required=True,
                    width="medium",
                ),
                "单位": st.column_config.SelectboxColumn(
                    "单位",
                    options=list(lookups["unit_options"]),
                    required=True,
                    width="small",
                ),
                "方法学": st.column_config.SelectboxColumn(
                    "方法学",
                    options=list(lookups["method_options"]),
                    required=True,
                    width="medium",
                ),
                "试剂": st.column_config.SelectboxColumn(
                    "试剂",
                    options=list(lookups["reagent_options"]),
                    required=True,
                    width="large",
                ),
                "水平数": st.column_config.NumberColumn(
                    "水平数",
                    min_value=1,
                    max_value=3,
                    step=1,
                    required=True,
                    width="small",
                ),
                "参数建立点数": st.column_config.NumberColumn(
                    "参数建立点数",
                    min_value=5,
                    max_value=20,
                    step=1,
                    required=True,
                    width="small",
                ),
                "允许不精密度(CV%)": st.column_config.NumberColumn(
                    "允许不精密度(CV%)",
                    min_value=0.01,
                    format="%.2f",
                    width="small",
                ),
            },
        )
        if st.button(
            "保存检验项目设置",
            key=f"v11_save_template_editor_{template_id}",
            type="primary",
            width="stretch",
        ):
            try:
                _save_editor_rows(template_id, edited, lookups)
            except (ValueError, TypeError) as exc:
                st.error(str(exc))
            else:
                st.success("检验项目设置已保存，项目回到待确认状态。")
                st.rerun()

    errors = validate_project_template(template_id)
    if errors:
        st.warning("项目尚未满足启用条件：\n\n" + "\n".join(f"- {item}" for item in errors))
    action1, action2 = st.columns(2)
    with action1:
        if st.button(
            "校验并确认项目设置",
            key=f"v11_activate_template_{template_id}",
            type="primary",
            width="stretch",
            disabled=bool(errors),
        ):
            try:
                activate_project_template(template_id)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success("项目设置已确认，可以创建批次。")
                st.rerun()
    with action2:
        if st.button(
            "编辑项目",
            key=f"v11_disable_template_{template_id}",
            width="stretch",
        ):
            from ui.project_dialogs import open_project_dialog
            open_project_dialog('project', template_id)

    from ui.project_dialogs import render_pending_project_dialog
    render_pending_project_dialog()


def _render_templates_tab() -> None:
    from ui.project_navigation import render_project_navigation
    # Both entry points share the same list, entity IDs, dialogs and return state.
    render_project_navigation()


def _render_create_lot_config() -> None:
    active_templates = list_project_templates()
    active_templates = active_templates[active_templates["status"] == "active"].copy()
    if active_templates.empty:
        st.info("请先启用至少一个项目。")
        return
    template_labels, template_map, _ = _option_map(
        active_templates,
        _template_label,
        placeholder="请选择设置已确认项目",
    )
    with st.expander("新建批次", expanded=False):
        template_label = st.selectbox(
            "项目",
            template_labels,
            key="v11_create_config_template",
        )
        template_id = template_map[template_label]
        if template_id is not None:
            from ui.materials import render_material_config_creation
            render_material_config_creation(int(template_id))



def _render_item_level_form(lot_config_id: int, item: pd.Series) -> None:
    # Compatibility entry used by older tests and embedded details.
    from ui.batch_workspace import render_batch_item_detail, render_pending_batch_dialogs
    if int(item['lot_config_id']) != int(lot_config_id):
        raise ValueError('检验项目不属于当前批次。')
    render_batch_item_detail(int(item['id']))
    render_pending_batch_dialogs()


def _render_lot_config_editor(lot_config_id: int) -> None:
    from ui.batch_workspace import render_batch_detail, render_pending_batch_dialogs
    render_batch_detail(lot_config_id)
    render_pending_batch_dialogs()


def _render_lot_configs_tab() -> None:
    from ui.batch_workspace import render_batch_workspace
    render_batch_workspace()


def _render_copy_tab() -> None:
    from ui.qc_replacement_workspace import render_qc_replacement_workspace
    render_qc_replacement_workspace()


def _render_import_export_tab() -> None:
    st.markdown("**批量导入项目配置**")
    st.caption(
        "使用系统 XLSX 模板批量维护项目中的检验项目。搜索不到的检验项目、单位、"
        "方法学、试剂和厂家会作为医院本地词条新增；官方词条不会被覆盖。"
    )
    st.download_button(
        "下载项目配置导入模板",
        data=build_project_import_template_xlsx(),
        file_name="LJQC_项目导入模板.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
        key="v11_download_project_import_template",
    )

    templates = list_project_templates()
    template_labels, template_map, _ = _option_map(
        templates,
        _template_label,
        placeholder="请选择目标项目",
    )
    target_label = st.selectbox(
        "导入到项目",
        template_labels,
        key="v11_import_target_template",
    )
    target_template_id = template_map[target_label]
    if target_template_id is not None:
        target_template = get_project_template(int(target_template_id))
        st.download_button(
            "导出当前项目 XLSX",
            data=build_project_template_xlsx(int(target_template_id)),
            file_name=(
                f"{_safe_file_fragment(target_template['template_name'], 'project_template')}.xlsx"
            ),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
            key="v11_export_selected_template",
        )

    uploaded = st.file_uploader(
        "上传项目配置 XLSX",
        type=["xlsx"],
        key="v11_project_config_upload",
        help="单个文件最大 10 MB；必须包含“项目配置”工作表。",
    )
    if uploaded is not None:
        uploaded_bytes = uploaded.getvalue()
        if len(uploaded_bytes) > 10 * 1024 * 1024:
            st.error("上传文件超过 10 MB，无法导入。")
        else:
            try:
                preview, errors = preview_project_template_xlsx(uploaded_bytes)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.markdown("**导入预览**")
                if not preview.empty:
                    st.dataframe(preview, hide_index=True, width="stretch")
                if errors:
                    st.error("文件中存在以下问题：\n\n" + "\n".join(f"- {item}" for item in errors))
                mode_label = st.radio(
                    "导入方式",
                    options=["合并检验项目", "替换项目内全部检验项目"],
                    horizontal=True,
                    key="v11_project_import_mode",
                    help="替换只会停用当前项目内未出现在文件中的检验项目，不删除基础资料。",
                )
                if st.button(
                    "确认批量导入",
                    type="primary",
                    width="stretch",
                    key="v11_confirm_project_import",
                    disabled=bool(errors) or target_template_id is None,
                ):
                    try:
                        result = import_project_template_xlsx(
                            int(target_template_id),
                            uploaded_bytes,
                            mode=(
                                "replace"
                                if mode_label == "替换项目内全部检验项目"
                                else "merge"
                            ),
                        )
                    except (TypeError, ValueError) as exc:
                        st.error(str(exc))
                    else:
                        created_text = "、".join(
                            f"{name} {count} 条"
                            for name, count in result["created"].items()
                            if int(count) > 0
                        )
                        suffix = f"；新增本地词条：{created_text}" if created_text else ""
                        st.success(
                            f"已导入 {result['imported_count']} 个检验项目，"
                            f"当前项目共包含 {result['saved_count']} 个检验项目{suffix}。"
                            "项目已回到待确认状态，请校验后确认设置。"
                        )
                        st.rerun()

    st.divider()
    st.markdown("**导出批次**")
    st.caption("导出批号、项目、各水平均值和标准差及修订记录；结果数据和质控计算不包含在此文件中。")
    configs = list_lot_configs()
    config_labels, config_map, _ = _option_map(
        configs,
        _config_label,
        placeholder="请选择批次",
    )
    config_label = st.selectbox(
        "要导出的批次",
        config_labels,
        key="v11_export_lot_config_selector",
    )
    export_config_id = config_map[config_label]
    if export_config_id is not None:
        config = get_lot_config(int(export_config_id))
        st.download_button(
            "导出批次 XLSX",
            data=build_lot_config_xlsx(int(export_config_id)),
            file_name=(
                f"{_safe_file_fragment(config['config_name'], 'lot_configuration')}.xlsx"
            ),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
            key="v11_export_selected_lot_config",
        )


def render_project_management_page() -> None:
    if 'v11_management_tabs' not in st.session_state and st.session_state.get('project_management_active_tab'):
        st.session_state['v11_management_tabs'] = st.session_state['project_management_active_tab']
    action_column, _ = st.columns([0.24, 0.76], gap="small")
    with action_column:
        if st.button("返回当前工作台", key="close_project_management_page", use_container_width=True):
            st.session_state["show_project_management_page"] = False
            st.rerun()

    render_section_intro(
        title="项目 / 批次管理",
        eyebrow="资料管理",
        caption=(
            "从基础资料字典选择本地仪器、试剂和质控品，建立项目；"
            "再为具体质控品批号建立批次，设置水平、均值和标准差。各检验项目可分别调整试剂。"
        ),
        badges=["检验项目批量设置", "更换质控品批次", "变更记录"],
        tone="accent",
    )
    dictionary_counts = {
        "本地仪器": len(list_lab_instruments()),
        "试剂": len(list_reagents()),
        "质控品": len(list_qc_materials()),
    }
    missing = [name for name, count in dictionary_counts.items() if count == 0]
    if missing:
        st.warning(
            "、".join(missing) + "字典暂无可选记录。请先在基础资料中新增实际使用的产品，再回到这里选择。"
            "当前内置检验项目、方法学和单位，尚未内置仪器、试剂及质控品产品目录。"
        )
    if st.button("维护仪器、试剂和质控品字典", key="v11_open_product_dictionaries"):
        open_global_page("show_master_data_page")
    copied_id = st.session_state.pop("v11_pending_copied_config_id", None)
    if copied_id is not None:
        copied = get_lot_config(int(copied_id))
        st.session_state["v11_selected_lot_config_id"] = int(copied_id)
        st.session_state['batch_project_filter'] = int(copied['template_id'])
        st.session_state['batch_search'] = ''
        st.session_state['batch_show_disabled'] = bool(copied['is_disabled'])
        st.session_state["v11_lot_config_selector"] = _config_label(pd.Series(dict(copied)))
        st.session_state["v11_management_tabs"] = "批次管理"
        st.session_state["v11_copy_notice"] = f"已建立新批次“{copied['config_name']}”，资料待确认。请选择检验项目，核对各水平批号、均值和标准差后再确认批次设置。"
    existing_id = st.session_state.pop("v11_pending_existing_config_id", None)
    if existing_id is not None:
        existing = get_lot_config(int(existing_id))
        st.session_state["v11_selected_lot_config_id"] = int(existing_id)
        st.session_state['batch_project_filter'] = int(existing['template_id'])
        st.session_state['batch_search'] = ''
        st.session_state['batch_show_disabled'] = bool(existing['is_disabled'])
        st.session_state["v11_lot_config_selector"] = _config_label(pd.Series(dict(existing)))
        st.session_state["v11_management_tabs"] = "批次管理"
        st.session_state["v11_copy_notice"] = st.session_state.pop("v11_quality_review_notice", "已打开所选批次，可查看资料和质量目标。")
    tabs = st.tabs(["新建项目", "批次管理", "更换质控品批次", "导入导出", "批号使用与追溯"],
        key="v11_management_tabs", on_change="rerun")
    st.session_state['project_management_active_tab'] = st.session_state.get('v11_management_tabs', '新建项目')
    with tabs[0]:
        _render_templates_tab()
    with tabs[1]:
        _render_lot_configs_tab()
    with tabs[2]:
        _render_copy_tab()
    with tabs[3]:
        _render_import_export_tab()

    with tabs[4]:
        from pages.lot_lifecycle_section import render_lot_management
        render_lot_management(render_dialogs=False)
    _render_pending_management_dialogs()


def _render_pending_management_dialogs():
    # The existing project/batch renderers already handle their own dialog.
    # Queue the new workflows so a pending draft never renders a second window.
    if any(st.session_state.get(key) for key in (
            'project_workspace_dialog', 'workspace_create_batch_for',
            'batch_workspace_create', 'batch_item_dialog', 'batch_status_confirmation')):
        return
    from ui.qc_replacement_workspace import MODAL_KEY, render_pending_qc_replacement_dialog
    if st.session_state.get(MODAL_KEY):
        render_pending_qc_replacement_dialog()
        return
    from pages.lot_lifecycle_section import render_pending_lifecycle_dialogs
    render_pending_lifecycle_dialogs()
