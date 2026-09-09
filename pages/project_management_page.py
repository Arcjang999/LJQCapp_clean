from __future__ import annotations

import math

import pandas as pd
import streamlit as st

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
    save_template_items,
    set_lot_config_disabled,
    set_project_template_disabled,
    validate_lot_config,
    validate_project_template,
)
from ui.common import render_section_intro


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
    status = "已启用" if str(row.get("status")) == "active" else "草稿"
    return (
        f"{_safe_text(row.get('template_name'))}｜"
        f"{_safe_text(row.get('instrument_name'))}｜"
        f"{int(row.get('item_count', 0) or 0)} 项｜{status}"
    )


def _lot_label(row: pd.Series) -> str:
    return (
        f"批号 {_safe_text(row.get('lot_no'))}｜"
        f"效期 {_safe_text(row.get('expiry_date'), '未填写')}"
    )


def _config_label(row: pd.Series) -> str:
    status_labels = {
        "draft": "草稿",
        "active": "已启用",
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
    with st.expander("新建项目模板", expanded=False):
        if instruments.empty or materials.empty:
            st.warning("请先到“基础资料”完成本地仪器和质控品维护。")
        with st.form("v11_create_project_template_form", clear_on_submit=True):
            template_name = st.text_input("模板名称 *")
            col1, col2 = st.columns(2)
            with col1:
                instrument_label = st.selectbox("本地仪器 *", instrument_labels)
            with col2:
                material_label = st.selectbox("质控品 *", material_labels)
            notes = st.text_input("模板备注")
            submitted = st.form_submit_button(
                "创建模板",
                type="primary",
                width="stretch",
            )
            if submitted:
                instrument_id = instrument_map[instrument_label]
                material_id = material_map[material_label]
                if instrument_id is None or material_id is None:
                    st.error("请选择本地仪器和质控品。")
                else:
                    try:
                        template_id = create_project_template(
                            template_name=template_name,
                            lab_instrument_id=int(instrument_id),
                            qc_material_id=int(material_id),
                            notes=notes,
                        )
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state["v11_selected_template_id"] = template_id
                        st.success("项目模板已创建，请继续批量添加检验项目。")
                        st.rerun()


def _template_item_editor_rows(items: pd.DataFrame) -> pd.DataFrame:
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
                "建靶点数",
                "CV要求(%)",
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
            "试剂": items.apply(
                lambda row: (
                    _safe_text(row.get("reagent_name"), "")
                    + (
                        f"｜{_safe_text(row.get('reagent_trade_name'), '')}"
                        if _safe_text(row.get("reagent_trade_name"), "")
                        else ""
                    )
                ),
                axis=1,
            ),
            "水平数": items["level_count"].astype(int),
            "建靶点数": items["target_n"].astype(int),
            "CV要求(%)": items["cv_limit"],
            "质量目标来源": items["quality_target_source_text"].fillna("").astype(str),
        }
    )


def _build_editor_lookup_options() -> dict[str, object]:
    units = list_units()
    methods = list_methods()
    reagents = list_reagents()
    unit_options = [_safe_text(value) for value in units["symbol"].tolist()]
    method_options = [_safe_text(value) for value in methods["method_name"].tolist()]
    reagent_options = [_reagent_label(row) for _, row in reagents.iterrows()]
    return {
        "units": units,
        "methods": methods,
        "reagents": reagents,
        "unit_options": unit_options,
        "method_options": method_options,
        "reagent_options": reagent_options,
        "unit_id_by_label": {
            _safe_text(row.get("symbol")): int(row["id"]) for _, row in units.iterrows()
        },
        "method_id_by_label": {
            _safe_text(row.get("method_name")): int(row["id"])
            for _, row in methods.iterrows()
        },
        "reagent_id_by_label": {
            _reagent_label(row): int(row["id"]) for _, row in reagents.iterrows()
        },
    }


def _save_editor_rows(template_id: int, edited: pd.DataFrame, lookups: dict[str, object]) -> None:
    rows: list[dict[str, object]] = []
    for index, row in edited.iterrows():
        if not bool(row.get("保留", True)):
            continue
        qc_method_label = str(row.get("质控方法") or "")
        input_type_label = str(row.get("输入值类型") or "")
        unit_label = str(row.get("单位") or "")
        method_label = str(row.get("方法学") or "")
        reagent_label = str(row.get("试剂") or "")
        rows.append(
            {
                "test_item_id": int(row["test_item_id"]),
                "qc_method": QC_METHOD_BY_LABEL.get(qc_method_label, ""),
                "input_value_type": INPUT_VALUE_TYPE_BY_LABEL.get(input_type_label, ""),
                "unit_id": lookups["unit_id_by_label"].get(unit_label),
                "method_id": lookups["method_id_by_label"].get(method_label),
                "reagent_id": lookups["reagent_id_by_label"].get(reagent_label),
                "level_count": int(row.get("水平数") or 1),
                "target_n": int(row.get("建靶点数") or 20),
                "cv_limit": row.get("CV要求(%)"),
                "quality_target_source_text": str(row.get("质量目标来源") or ""),
                "sort_order": index + 1,
            }
        )
    save_template_items(template_id, rows)


def _render_add_template_items(template_id: int) -> None:
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
    unit_options = list(lookups["unit_options"])
    method_options = list(lookups["method_options"])
    reagent_options = list(lookups["reagent_options"])
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
                key=f"v11_bulk_method_{template_id}",
            )
        with row3:
            reagent_label = st.selectbox(
                "批量试剂",
                reagent_options,
                key=f"v11_bulk_reagent_{template_id}",
            )
            level_count = st.number_input(
                "批量水平数",
                min_value=1,
                max_value=3,
                value=1,
                step=1,
                key=f"v11_bulk_level_count_{template_id}",
            )
        target_n = st.slider(
            "批量建靶有效点数",
            min_value=5,
            max_value=20,
            value=20,
            key=f"v11_bulk_target_n_{template_id}",
        )
        if st.button(
            "添加到模板",
            key=f"v11_add_items_button_{template_id}",
            type="primary",
            width="stretch",
            disabled=not selected_labels,
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


def _render_template_editor(template_id: int) -> None:
    template = get_project_template(template_id)
    items = list_template_items(template_id)
    st.caption(
        f"当前模板：{template['template_name']}｜仪器：{template['instrument_name']}｜"
        f"质控品：{template['qc_material_name']}｜"
        f"状态：{'已启用' if template['status'] == 'active' else '草稿'}"
    )
    _render_add_template_items(template_id)

    items = list_template_items(template_id)
    if items.empty:
        st.info("请先批量添加检验项目。")
    else:
        lookups = _build_editor_lookup_options()
        editor_df = _template_item_editor_rows(items)
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
                "建靶点数": st.column_config.NumberColumn(
                    "建靶点数",
                    min_value=5,
                    max_value=20,
                    step=1,
                    required=True,
                    width="small",
                ),
                "CV要求(%)": st.column_config.NumberColumn(
                    "CV要求(%)",
                    min_value=0.01,
                    format="%.2f",
                    width="small",
                ),
            },
        )
        if st.button(
            "保存项目配置表",
            key=f"v11_save_template_editor_{template_id}",
            type="primary",
            width="stretch",
        ):
            try:
                _save_editor_rows(template_id, edited, lookups)
            except (ValueError, TypeError) as exc:
                st.error(str(exc))
            else:
                st.success("项目配置表已保存，模板回到草稿状态。")
                st.rerun()

    errors = validate_project_template(template_id)
    if errors:
        st.warning("模板尚未满足启用条件：\n\n" + "\n".join(f"- {item}" for item in errors))
    action1, action2 = st.columns(2)
    with action1:
        if st.button(
            "校验并启用模板",
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
                st.success("项目模板已启用，可以创建批号配置。")
                st.rerun()
    with action2:
        if st.button(
            "软停用模板",
            key=f"v11_disable_template_{template_id}",
            width="stretch",
        ):
            set_project_template_disabled(
                template_id,
                is_disabled=True,
                reason="用户在新版项目管理页停用",
            )
            st.session_state["v11_selected_template_id"] = None
            st.success("模板已软停用。")
            st.rerun()


def _render_templates_tab() -> None:
    _render_template_creation()
    templates = list_project_templates()
    if templates.empty:
        st.info("当前没有新版项目模板。")
    else:
        template_id = _select_current_entity(
            dataframe=templates,
            label_builder=_template_label,
            placeholder="请选择项目模板",
            label="选择模板",
            key="v11_template_selector",
            state_key="v11_selected_template_id",
        )
        display = templates[
            [
                "template_name",
                "instrument_name",
                "qc_material_name",
                "status",
                "item_count",
                "revision_no",
                "created_at",
            ]
        ].copy()
        display["status"] = display["status"].map({"draft": "草稿", "active": "已启用"})
        st.dataframe(
            display.rename(
                columns={
                    "template_name": "模板名称",
                    "instrument_name": "本地仪器",
                    "qc_material_name": "质控品",
                    "status": "状态",
                    "item_count": "项目数",
                    "revision_no": "修订号",
                    "created_at": "创建时间",
                }
            ),
            hide_index=True,
            width="stretch",
        )
        if template_id is not None:
            _render_template_editor(int(template_id))

    all_templates = list_project_templates(include_disabled=True)
    disabled_templates = all_templates[all_templates["is_disabled"].astype(int) == 1]
    if not disabled_templates.empty:
        with st.expander("恢复已停用项目模板", expanded=False):
            labels, mapping, _ = _option_map(
                disabled_templates,
                _template_label,
                placeholder="请选择已停用模板",
            )
            selected = st.selectbox(
                "已停用模板",
                labels,
                key="v11_restore_template_selector",
            )
            if st.button(
                "恢复为草稿模板",
                key="v11_restore_template_button",
                width="stretch",
                disabled=mapping[selected] is None,
            ):
                try:
                    set_project_template_disabled(
                        int(mapping[selected]),
                        is_disabled=False,
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success("项目模板已恢复为草稿。")
                    st.rerun()


def _render_create_lot_config() -> None:
    active_templates = list_project_templates()
    active_templates = active_templates[active_templates["status"] == "active"].copy()
    if active_templates.empty:
        st.info("请先启用至少一个项目模板。")
        return
    template_labels, template_map, _ = _option_map(
        active_templates,
        _template_label,
        placeholder="请选择已启用模板",
    )
    with st.expander("从模板创建新批号配置", expanded=False):
        template_label = st.selectbox(
            "项目模板",
            template_labels,
            key="v11_create_config_template",
        )
        template_id = template_map[template_label]
        lots = pd.DataFrame()
        if template_id is not None:
            template = get_project_template(int(template_id))
            lots = list_qc_lots(int(template["qc_material_id"]))
        lot_labels, lot_map, _ = _option_map(
            lots,
            _lot_label,
            placeholder="请选择质控品批号",
        )
        lot_label = st.selectbox(
            "质控品批号",
            lot_labels,
            key="v11_create_config_lot",
        )
        config_name = st.text_input(
            "配置名称（留空自动生成）",
            key="v11_create_config_name",
        )
        if st.button(
            "创建批号配置",
            key="v11_create_config_button",
            type="primary",
            width="stretch",
        ):
            lot_id = lot_map[lot_label]
            if template_id is None or lot_id is None:
                st.error("请选择项目模板和质控品批号。")
            else:
                try:
                    config_id = create_lot_config_from_template(
                        template_id=int(template_id),
                        qc_material_lot_id=int(lot_id),
                        config_name=config_name,
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["v11_selected_lot_config_id"] = config_id
                    st.success("批号配置已创建，请继续配置各项目水平、靶值和 SD。")
                    st.rerun()


def _render_item_level_form(lot_config_id: int, item: pd.Series) -> None:
    config = get_lot_config(lot_config_id)
    all_levels = list_qc_levels(int(config["qc_material_lot_id"]))
    level_labels, level_map, level_by_id = _option_map(
        all_levels,
        lambda row: (
            f"{int(row.get('level_order', 0) or 0)}｜"
            f"{_safe_text(row.get('level_name'))}｜"
            f"{_safe_text(row.get('concentration_label'), '')}"
        ),
        placeholder="请选择水平",
    )
    selectable_labels = level_labels[1:]
    existing = list_lot_item_levels(int(item["id"]))
    existing_level_ids = (
        existing["qc_level_id"].astype(int).tolist() if not existing.empty else []
    )
    default_level_labels = [
        level_by_id[level_id] for level_id in existing_level_ids if level_id in level_by_id
    ]
    expected_count = int(item["level_count"])
    title = (
        f"{item['test_item_name']}｜{QC_METHOD_LABELS.get(str(item['qc_method']), item['qc_method'])}"
        f"｜需要 {expected_count} 个水平"
    )
    with st.expander(title, expanded=int(item["assigned_level_count"] or 0) != expected_count):
        if len(selectable_labels) < expected_count:
            st.error(
                f"当前质控品批号只有 {len(selectable_labels)} 个启用水平，"
                f"不能满足本项目需要的 {expected_count} 个水平。"
            )
            return
        selected_labels = st.multiselect(
            "选择水平",
            options=selectable_labels,
            default=default_level_labels,
            max_selections=expected_count,
            key=f"v11_item_levels_{item['id']}_{int(config['revision_no'])}",
        )
        existing_by_level = {
            int(row["qc_level_id"]): row for _, row in existing.iterrows()
        }
        assignments: list[dict[str, object]] = []
        for order, selected_label in enumerate(selected_labels, start=1):
            level_id = int(level_map[selected_label])
            saved = existing_by_level.get(level_id)
            default_source = (
                TARGET_SOURCE_LABELS.get(str(saved["target_source"]))
                if saved is not None
                else TARGET_SOURCE_LABELS["building"]
            )
            row1, row2, row3, row4 = st.columns([1.2, 1, 1, 0.8])
            with row1:
                st.text(selected_label)
            with row2:
                source_label = st.selectbox(
                    "靶值来源",
                    options=list(TARGET_SOURCE_BY_LABEL),
                    index=list(TARGET_SOURCE_BY_LABEL).index(default_source),
                    key=f"v11_target_source_{item['id']}_{level_id}",
                )
            with row3:
                target_mean = st.number_input(
                    "靶值",
                    value=(
                        None
                        if saved is None or pd.isna(saved["target_mean"])
                        else float(saved["target_mean"])
                    ),
                    key=f"v11_target_mean_{item['id']}_{level_id}",
                )
            with row4:
                target_sd = st.number_input(
                    "SD",
                    min_value=0.0,
                    value=(
                        None
                        if saved is None or pd.isna(saved["target_sd"])
                        else float(saved["target_sd"])
                    ),
                    key=f"v11_target_sd_{item['id']}_{level_id}",
                )
            confirmed_default = bool(
                saved is not None and int(saved["target_confirmed"] or 0)
            )
            confirmed = st.checkbox(
                f"确认 {selected_label} 的靶值与 SD",
                value=confirmed_default,
                key=f"v11_target_confirmed_{item['id']}_{level_id}",
            )
            assignments.append(
                {
                    "qc_level_id": level_id,
                    "target_source": TARGET_SOURCE_BY_LABEL[source_label],
                    "target_mean": target_mean,
                    "target_sd": target_sd,
                    "target_confirmed": confirmed,
                }
            )
        if st.button(
            "保存本项目水平配置",
            key=f"v11_save_item_levels_{item['id']}",
            width="stretch",
            disabled=len(selected_labels) != expected_count,
        ):
            try:
                save_lot_item_levels(int(item["id"]), assignments)
            except (ValueError, TypeError) as exc:
                st.error(str(exc))
            else:
                st.success("本项目水平配置已保存。")
                st.rerun()


def _render_lot_config_editor(lot_config_id: int) -> None:
    config = get_lot_config(lot_config_id)
    items = list_lot_config_items(lot_config_id)
    st.caption(
        f"当前批号配置：{config['config_name']}｜模板：{config['template_name']}｜"
        f"批号：{config['lot_no']}｜效期：{_safe_text(config['expiry_date'], '未填写')}｜"
        f"状态：{config['status']}｜修订 {config['revision_no']}"
    )
    if items.empty:
        st.error("当前批号配置没有项目。")
        return
    summary = items[
        [
            "test_item_name",
            "qc_method",
            "input_value_type",
            "unit_symbol",
            "method_name",
            "reagent_name",
            "level_count",
            "assigned_level_count",
            "target_n",
            "cv_limit",
        ]
    ].copy()
    summary["qc_method"] = summary["qc_method"].map(QC_METHOD_LABELS)
    summary["input_value_type"] = summary["input_value_type"].map(INPUT_VALUE_TYPE_LABELS)
    st.dataframe(
        summary.rename(
            columns={
                "test_item_name": "检验项目",
                "qc_method": "质控方法",
                "input_value_type": "输入值类型",
                "unit_symbol": "单位",
                "method_name": "方法学",
                "reagent_name": "试剂",
                "level_count": "所需水平数",
                "assigned_level_count": "已配置水平数",
                "target_n": "建靶点数",
                "cv_limit": "CV要求(%)",
            }
        ),
        hide_index=True,
        width="stretch",
    )
    st.markdown("**逐项目配置水平、靶值和 SD**")
    for _, item in items.iterrows():
        _render_item_level_form(lot_config_id, item)

    errors = validate_lot_config(lot_config_id)
    if errors:
        st.warning("批号配置尚未满足启用条件：\n\n" + "\n".join(f"- {item}" for item in errors))
    action1, action2 = st.columns(2)
    with action1:
        if st.button(
            "校验并启用批号配置",
            key=f"v11_activate_lot_config_{lot_config_id}",
            type="primary",
            width="stretch",
            disabled=bool(errors),
        ):
            try:
                activate_lot_config(lot_config_id)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success("批号配置已启用。")
                st.rerun()
    with action2:
        if st.button(
            "软停用批号配置",
            key=f"v11_disable_lot_config_{lot_config_id}",
            width="stretch",
        ):
            set_lot_config_disabled(
                lot_config_id,
                is_disabled=True,
                reason="用户在新版项目管理页停用",
            )
            st.session_state["v11_selected_lot_config_id"] = None
            st.success("批号配置已软停用。")
            st.rerun()

    snapshots = list_config_snapshots(lot_config_id)
    with st.expander("配置修订记录", expanded=False):
        st.dataframe(
            snapshots.rename(
                columns={
                    "revision_no": "修订号",
                    "action_type": "动作",
                    "change_summary": "变更说明",
                    "created_by": "操作者",
                    "created_at": "时间",
                }
            )[
                ["修订号", "动作", "变更说明", "操作者", "时间"]
            ],
            hide_index=True,
            width="stretch",
        )


def _render_lot_configs_tab() -> None:
    _render_create_lot_config()
    configs = list_lot_configs()
    if configs.empty:
        st.info("当前没有新版批号配置。")
    else:
        config_id = _select_current_entity(
            dataframe=configs,
            label_builder=_config_label,
            placeholder="请选择批号配置",
            label="选择批号配置",
            key="v11_lot_config_selector",
            state_key="v11_selected_lot_config_id",
        )
        display = configs[
            [
                "config_name",
                "template_name",
                "instrument_name",
                "qc_material_name",
                "lot_no",
                "expiry_date",
                "status",
                "item_count",
                "revision_no",
            ]
        ].copy()
        display["status"] = display["status"].map(
            {
                "draft": "草稿",
                "active": "已启用",
                "superseded": "已替代",
                "disabled": "已停用",
            }
        )
        st.dataframe(
            display.rename(
                columns={
                    "config_name": "配置名称",
                    "template_name": "模板",
                    "instrument_name": "仪器",
                    "qc_material_name": "质控品",
                    "lot_no": "批号",
                    "expiry_date": "效期",
                    "status": "状态",
                    "item_count": "项目数",
                    "revision_no": "修订号",
                }
            ),
            hide_index=True,
            width="stretch",
        )
        if config_id is not None:
            _render_lot_config_editor(int(config_id))

    all_configs = list_lot_configs(include_disabled=True)
    disabled_configs = all_configs[all_configs["is_disabled"].astype(int) == 1]
    if not disabled_configs.empty:
        with st.expander("恢复已停用批号配置", expanded=False):
            labels, mapping, _ = _option_map(
                disabled_configs,
                _config_label,
                placeholder="请选择已停用批号配置",
            )
            selected = st.selectbox(
                "已停用批号配置",
                labels,
                key="v11_restore_lot_config_selector",
            )
            if st.button(
                "恢复为草稿配置",
                key="v11_restore_lot_config_button",
                width="stretch",
                disabled=mapping[selected] is None,
            ):
                try:
                    set_lot_config_disabled(
                        int(mapping[selected]),
                        is_disabled=False,
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success("批号配置已恢复为草稿，请重新校验后启用。")
                    st.rerun()


def _render_copy_tab() -> None:
    configs = list_lot_configs()
    if configs.empty:
        st.info("还没有可复制的批号配置。")
        return
    source_labels, source_map, _ = _option_map(
        configs,
        _config_label,
        placeholder="请选择来源批号配置",
    )
    source_label = st.selectbox(
        "来源批号配置",
        source_labels,
        key="v11_copy_source_config",
    )
    source_id = source_map[source_label]
    target_lots = pd.DataFrame()
    if source_id is not None:
        source = get_lot_config(int(source_id))
        target_lots = list_qc_lots(int(source["qc_material_id"]))
    lot_labels, lot_map, _ = _option_map(
        target_lots,
        _lot_label,
        placeholder="请选择目标新批号",
    )
    target_lot_label = st.selectbox(
        "目标新批号",
        lot_labels,
        key="v11_copy_target_lot",
    )
    config_name = st.text_input(
        "新配置名称（留空自动生成）",
        key="v11_copy_config_name",
    )
    st.info(
        "复制会带入项目、单位、方法学、试剂、水平结构、建靶点数和 CV 要求。"
        "人工或厂家靶值会作为待确认值；建靶计算项目会清空靶值与 SD，"
        "由新批号重新建靶。结果、建靶状态、异常记录和报告不会复制。"
    )
    if st.button(
        "复制为新批号配置",
        key="v11_copy_config_button",
        type="primary",
        width="stretch",
    ):
        target_lot_id = lot_map[target_lot_label]
        if source_id is None or target_lot_id is None:
            st.error("请选择来源批号配置和目标新批号。")
        else:
            try:
                new_config_id = copy_lot_config(
                    source_lot_config_id=int(source_id),
                    target_qc_material_lot_id=int(target_lot_id),
                    config_name=config_name,
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state["v11_selected_lot_config_id"] = new_config_id
                st.success("新批号配置已复制，请逐项确认靶值和 SD。")
                st.rerun()


def _render_import_export_tab() -> None:
    st.markdown("**批量导入项目配置**")
    st.caption(
        "使用系统 XLSX 模板批量维护一个项目模板。搜索不到的检验项目、单位、"
        "方法学、试剂和厂家会作为医院本地词条新增；官方词条不会被覆盖。"
    )
    st.download_button(
        "下载项目配置导入模板",
        data=build_project_import_template_xlsx(),
        file_name="LJQC_V1_1_project_import_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
        key="v11_download_project_import_template",
    )

    templates = list_project_templates()
    template_labels, template_map, _ = _option_map(
        templates,
        _template_label,
        placeholder="请选择目标项目模板",
    )
    target_label = st.selectbox(
        "导入到项目模板",
        template_labels,
        key="v11_import_target_template",
    )
    target_template_id = template_map[target_label]
    if target_template_id is not None:
        target_template = get_project_template(int(target_template_id))
        st.download_button(
            "导出当前项目模板 XLSX",
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
                    options=["合并到现有项目", "替换模板全部项目"],
                    horizontal=True,
                    key="v11_project_import_mode",
                    help="替换只会软停用未出现在文件中的模板项目，不删除字典词条。",
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
                                if mode_label == "替换模板全部项目"
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
                            f"已导入 {result['imported_count']} 个项目，"
                            f"模板当前共保存 {result['saved_count']} 个项目{suffix}。"
                            "模板已回到草稿状态，请校验后启用。"
                        )
                        st.rerun()

    st.divider()
    st.markdown("**导出批号配置**")
    st.caption("导出批号、项目、水平靶值和修订记录；结果数据和质控计算不包含在此文件中。")
    configs = list_lot_configs()
    config_labels, config_map, _ = _option_map(
        configs,
        _config_label,
        placeholder="请选择批号配置",
    )
    config_label = st.selectbox(
        "要导出的批号配置",
        config_labels,
        key="v11_export_lot_config_selector",
    )
    export_config_id = config_map[config_label]
    if export_config_id is not None:
        config = get_lot_config(int(export_config_id))
        st.download_button(
            "导出批号配置 XLSX",
            data=build_lot_config_xlsx(int(export_config_id)),
            file_name=(
                f"{_safe_file_fragment(config['config_name'], 'lot_configuration')}.xlsx"
            ),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
            key="v11_export_selected_lot_config",
        )


def render_project_management_page() -> None:
    action_column, _ = st.columns([0.24, 0.76], gap="small")
    with action_column:
        if st.button("返回当前工作台", key="close_project_management_page", use_container_width=True):
            st.session_state["show_project_management_page"] = False
            st.rerun()

    render_section_intro(
        title="新版项目 / 批次管理",
        eyebrow="全局入口",
        caption=(
            "按本地仪器与质控品建立多项目模板，再为具体质控品批号配置水平、靶值和 SD。"
            "本页面使用全新的 V1.1 数据链路，不读取旧测试项目。"
        ),
        badges=["多项目批量配置", "复制上一批号", "配置快照"],
        tone="accent",
    )
    st.info(
        "推荐顺序：先在“基础资料”维护字典，再创建项目模板，最后创建或复制批号配置。"
        "本阶段只换上游管理，不调用质控计算。"
    )
    tabs = st.tabs(["项目模板", "批号配置", "复制上一批号", "导入导出"])
    with tabs[0]:
        _render_templates_tab()
    with tabs[1]:
        _render_lot_configs_tab()
    with tabs[2]:
        _render_copy_tab()
    with tabs[3]:
        _render_import_export_tab()
