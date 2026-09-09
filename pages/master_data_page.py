from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from services.master_data_service import (
    create_alias,
    create_instrument_model,
    create_lab_instrument,
    create_manufacturer,
    create_method,
    create_qc_level,
    create_qc_lot,
    create_qc_material,
    create_reagent,
    create_test_item,
    create_unit,
    list_instrument_models,
    list_aliases,
    list_lab_instruments,
    list_manufacturers,
    list_methods,
    list_qc_levels,
    list_qc_lots,
    list_qc_materials,
    list_reagents,
    list_sources,
    list_test_items,
    list_units,
    set_master_entity_disabled,
)
from ui.common import render_section_intro


def _safe_text(value: object, fallback: str = "-") -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return fallback
    cleaned = " ".join(str(value).split()).strip()
    return cleaned or fallback


def _option_map(
    dataframe: pd.DataFrame,
    label_builder,
    *,
    placeholder: str,
) -> tuple[list[str], dict[str, int | None]]:
    labels = [placeholder]
    mapping: dict[str, int | None] = {placeholder: None}
    for _, row in dataframe.iterrows():
        base_label = str(label_builder(row))
        label = base_label
        suffix = 2
        while label in mapping:
            label = f"{base_label}（{suffix}）"
            suffix += 1
        labels.append(label)
        mapping[label] = int(row["id"])
    return labels, mapping


def _manufacturer_label(row: pd.Series) -> str:
    country = _safe_text(row.get("country_or_region"), "")
    return _safe_text(row.get("display_name")) + (f"｜{country}" if country else "")


def _instrument_model_label(row: pd.Series) -> str:
    manufacturer = _safe_text(row.get("manufacturer_name"), "未维护厂家")
    brand = _safe_text(row.get("brand_name"), "")
    model = _safe_text(row.get("model"))
    prefix = f"{manufacturer}｜"
    if brand:
        prefix += f"{brand}｜"
    return prefix + model


def _qc_material_label(row: pd.Series) -> str:
    manufacturer = _safe_text(row.get("manufacturer_name"), "未维护厂家")
    name = _safe_text(row.get("qc_material_name", row.get("generic_name")))
    trade = _safe_text(row.get("trade_name", row.get("qc_material_trade_name")), "")
    return f"{manufacturer}｜{name}" + (f"｜{trade}" if trade else "")


def _reagent_label(row: pd.Series) -> str:
    manufacturer = _safe_text(row.get("manufacturer_name"), "未维护厂家")
    name = _safe_text(row.get("generic_name"))
    trade = _safe_text(row.get("trade_name"), "")
    return f"{manufacturer}｜{name}" + (f"｜{trade}" if trade else "")


def _lot_label(row: pd.Series) -> str:
    return (
        f"{_safe_text(row.get('qc_material_name'))}｜批号 {_safe_text(row.get('lot_no'))}"
        f"｜效期 {_safe_text(row.get('expiry_date'), '未填写')}"
    )


def _display_table(dataframe: pd.DataFrame, columns: dict[str, str]) -> None:
    if dataframe.empty:
        st.info("暂无记录。")
        return
    available = [column for column in columns if column in dataframe.columns]
    display = dataframe[available].copy()
    if "is_disabled" in display.columns:
        display["is_disabled"] = display["is_disabled"].map({0: "启用", 1: "已停用"})
    if "origin_type" in display.columns:
        display["origin_type"] = display["origin_type"].map(
            {"official": "官方", "hospital": "医院本地", "import": "批量导入"}
        ).fillna(display["origin_type"])
    st.dataframe(
        display.rename(columns=columns),
        width="stretch",
        hide_index=True,
    )


def _render_disable_control(
    *,
    entity_type: str,
    dataframe: pd.DataFrame,
    label_builder,
    key_prefix: str,
) -> None:
    if dataframe.empty:
        return
    with st.expander("停用 / 恢复", expanded=False):
        labels, mapping = _option_map(
            dataframe,
            lambda row: (
                f"{label_builder(row)}｜"
                f"{'已停用' if int(row.get('is_disabled', 0) or 0) else '启用'}"
            ),
            placeholder="请选择记录",
        )
        selected_label = st.selectbox(
            "记录",
            options=labels,
            key=f"{key_prefix}_disable_selector",
        )
        selected_id = mapping[selected_label]
        selected_row = None
        if selected_id is not None:
            selected_row = dataframe[dataframe["id"].astype(int) == int(selected_id)].iloc[0]
        reason = st.text_input(
            "停用原因",
            key=f"{key_prefix}_disable_reason",
            disabled=selected_row is None or bool(int(selected_row.get("is_disabled", 0) or 0)),
        )
        action_left, action_right = st.columns(2)
        with action_left:
            if st.button(
                "停用所选记录",
                key=f"{key_prefix}_disable_button",
                width="stretch",
                disabled=selected_row is None or bool(int(selected_row.get("is_disabled", 0) or 0)),
            ):
                if not reason.strip():
                    st.error("请填写停用原因。")
                else:
                    set_master_entity_disabled(
                        entity_type,
                        int(selected_id),
                        is_disabled=True,
                        reason=reason,
                    )
                    st.success("记录已软停用，历史引用不受影响。")
                    st.rerun()
        with action_right:
            if st.button(
                "恢复所选记录",
                key=f"{key_prefix}_restore_button",
                width="stretch",
                disabled=selected_row is None or not bool(int(selected_row.get("is_disabled", 0) or 0)),
            ):
                try:
                    set_master_entity_disabled(
                        entity_type,
                        int(selected_id),
                        is_disabled=False,
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success("记录已恢复。")
                    st.rerun()


def _render_manufacturers_tab() -> None:
    show_disabled = st.checkbox("显示已停用厂家", key="md_show_disabled_manufacturers")
    manufacturers = list_manufacturers(include_disabled=show_disabled)
    with st.form("md_create_manufacturer_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            display_name = st.text_input("厂家显示名称 *")
            legal_name = st.text_input("法定名称")
        with col2:
            country = st.text_input("国家或地区")
            holder = st.text_input("注册人 / 备案人")
        notes = st.text_input("备注")
        if st.form_submit_button("新增厂家", type="primary", width="stretch"):
            try:
                create_manufacturer(
                    display_name=display_name,
                    legal_name=legal_name,
                    country_or_region=country,
                    registration_holder_name=holder,
                    notes=notes,
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success("厂家已新增。")
                st.rerun()
    _display_table(
        manufacturers,
        {
            "display_name": "厂家",
            "legal_name": "法定名称",
            "country_or_region": "国家 / 地区",
            "origin_type": "来源类型",
            "is_disabled": "状态",
            "created_at": "创建时间",
        },
    )
    _render_disable_control(
        entity_type="manufacturer",
        dataframe=list_manufacturers(include_disabled=True),
        label_builder=_manufacturer_label,
        key_prefix="manufacturer",
    )


def _render_test_items_tab() -> None:
    st.caption(
        "已内置已发布、将于 2026-11-01 实施的 WS/T 886—2026 中 296 个定量检验项目；"
        "定性和定序项目留到 V1.3，医院仍可新增本地定量项目。"
    )
    top_left, top_right = st.columns([0.72, 0.28])
    with top_left:
        query = st.text_input(
            "搜索项目名称、缩写、代码或别名",
            key="md_test_item_query",
        )
    with top_right:
        show_disabled = st.checkbox(
            "显示已停用项目",
            key="md_show_disabled_test_items",
        )
    units = list_units()
    unit_labels, unit_map = _option_map(
        units,
        lambda row: f"{_safe_text(row.get('symbol'))}｜{_safe_text(row.get('unit_name'), '')}",
        placeholder="不设置默认单位",
    )
    with st.form("md_create_test_item_form", clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            chinese_name = st.text_input("检验项目名称 *")
            standard_code = st.text_input("标准代码")
        with col2:
            abbreviation = st.text_input("常用缩写")
            english_name = st.text_input("英文名称")
        with col3:
            category_name = st.text_input("专业分类")
            specimen_type = st.text_input("样本类型")
        default_unit_label = st.selectbox("默认单位", options=unit_labels)
        notes = st.text_input("备注", key="md_test_item_notes")
        if st.form_submit_button("新增检验项目", type="primary", width="stretch"):
            try:
                create_test_item(
                    chinese_name=chinese_name,
                    standard_code=standard_code,
                    english_name=english_name,
                    abbreviation=abbreviation,
                    category_name=category_name,
                    specimen_type=specimen_type,
                    default_unit_id=unit_map[default_unit_label],
                    notes=notes,
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success("检验项目已新增。")
                st.rerun()

    items = list_test_items(include_disabled=show_disabled, query=query)
    _display_table(
        items,
        {
            "standard_code": "标准代码",
            "chinese_name": "检验项目",
            "abbreviation": "缩写",
            "english_name": "英文名称",
            "category_name": "专业分类",
            "specimen_type": "样本类型",
            "default_unit": "默认单位",
            "origin_type": "来源类型",
            "is_disabled": "状态",
        },
    )
    with st.expander("项目别名与来源", expanded=False):
        active_items = list_test_items()
        item_labels, item_map = _option_map(
            active_items,
            lambda row: (
                f"{_safe_text(row.get('chinese_name'))}｜"
                f"{_safe_text(row.get('standard_code'), '无标准代码')}"
            ),
            placeholder="请选择检验项目",
        )
        alias_type_labels = {
            "简称": "short_name",
            "英文名称": "english",
            "LIS代码": "lis_code",
            "历史名称": "historical",
            "厂家文本": "vendor_text",
            "自定义": "custom",
        }
        with st.form("md_create_test_item_alias_form", clear_on_submit=True):
            alias_item_label = st.selectbox("检验项目", item_labels)
            alias_type_label = st.selectbox("别名类型", list(alias_type_labels))
            alias_text = st.text_input("别名内容 *")
            if st.form_submit_button("添加项目别名", width="stretch"):
                alias_item_id = item_map[alias_item_label]
                if alias_item_id is None:
                    st.error("请选择检验项目。")
                else:
                    try:
                        create_alias(
                            entity_type="test_item",
                            entity_id=int(alias_item_id),
                            alias_text=alias_text,
                            alias_type=alias_type_labels[alias_type_label],
                        )
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.success("项目别名已添加，可用于项目搜索。")
                        st.rerun()
        aliases = list_aliases(entity_type="test_item", include_disabled=True)
        if not aliases.empty:
            item_names = {
                int(row["id"]): _safe_text(row.get("chinese_name"))
                for _, row in list_test_items(include_disabled=True).iterrows()
            }
            alias_display = aliases.copy()
            alias_display["entity_id"] = alias_display["entity_id"].map(item_names)
            _display_table(
                alias_display,
                {
                    "entity_id": "检验项目",
                    "alias_text": "别名",
                    "alias_type": "别名类型",
                    "origin_type": "来源类型",
                    "is_disabled": "状态",
                },
            )
            _render_disable_control(
                entity_type="alias",
                dataframe=aliases,
                label_builder=lambda row: _safe_text(row.get("alias_text")),
                key_prefix="test_item_alias",
            )
        sources = list_sources()
        st.markdown("**词库来源**")
        _display_table(
            sources,
            {
                "source_code": "来源代码",
                "source_name": "来源名称",
                "publisher": "发布者",
                "version_label": "版本",
                "effective_date": "实施日期",
                "active_record_count": "有效来源记录数",
                "is_disabled": "状态",
            },
        )
    _render_disable_control(
        entity_type="test_item",
        dataframe=list_test_items(include_disabled=True),
        label_builder=lambda row: _safe_text(row.get("chinese_name")),
        key_prefix="test_item",
    )


def _render_instruments_tab() -> None:
    manufacturers = list_manufacturers()
    manufacturer_labels, manufacturer_map = _option_map(
        manufacturers,
        _manufacturer_label,
        placeholder="请选择厂家",
    )
    with st.expander("第一步：新增仪器型号", expanded=False):
        with st.form("md_create_instrument_model_form", clear_on_submit=True):
            manufacturer_label = st.selectbox("厂家 *", manufacturer_labels)
            col1, col2 = st.columns(2)
            with col1:
                generic_name = st.text_input("仪器通用名称 *")
                brand_name = st.text_input("品牌")
            with col2:
                model = st.text_input("型号 *")
                registration_no = st.text_input("注册证 / 备案编号")
            if st.form_submit_button("新增仪器型号", width="stretch"):
                if manufacturer_map[manufacturer_label] is None:
                    st.error("请先选择厂家；如列表中没有，请到“厂家”页新增。")
                else:
                    try:
                        create_instrument_model(
                            manufacturer_id=manufacturer_map[manufacturer_label],
                            generic_name=generic_name,
                            brand_name=brand_name,
                            model=model,
                            registration_no=registration_no,
                        )
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.success("仪器型号已新增。")
                        st.rerun()

    models = list_instrument_models()
    model_labels, model_map = _option_map(
        models,
        _instrument_model_label,
        placeholder="请选择仪器型号",
    )
    with st.form("md_create_lab_instrument_form", clear_on_submit=True):
        st.markdown("**第二步：登记医院实际仪器**")
        selected_model_label = st.selectbox("仪器型号 *", model_labels)
        col1, col2, col3 = st.columns(3)
        with col1:
            display_name = st.text_input("本地显示名称 *")
            asset_code = st.text_input("资产编号")
        with col2:
            serial_number = st.text_input("序列号")
            department_name = st.text_input("所属科室")
        with col3:
            instrument_group = st.text_input("仪器组")
            location = st.text_input("放置位置")
        if st.form_submit_button("新增本地仪器", type="primary", width="stretch"):
            if model_map[selected_model_label] is None:
                st.error("请选择仪器型号。")
            else:
                try:
                    create_lab_instrument(
                        instrument_model_id=int(model_map[selected_model_label]),
                        display_name=display_name,
                        asset_code=asset_code,
                        serial_number=serial_number,
                        department_name=department_name,
                        instrument_group=instrument_group,
                        location=location,
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success("本地仪器已新增。")
                    st.rerun()

    instruments = list_lab_instruments(
        include_disabled=st.checkbox(
            "显示已停用本地仪器",
            key="md_show_disabled_lab_instruments",
        )
    )
    _display_table(
        instruments,
        {
            "display_name": "本地仪器",
            "manufacturer_name": "厂家",
            "brand_name": "品牌",
            "model": "型号",
            "asset_code": "资产编号",
            "serial_number": "序列号",
            "department_name": "科室",
            "instrument_group": "仪器组",
            "location": "位置",
            "is_disabled": "状态",
        },
    )
    _render_disable_control(
        entity_type="lab_instrument",
        dataframe=list_lab_instruments(include_disabled=True),
        label_builder=lambda row: _safe_text(row.get("display_name")),
        key_prefix="lab_instrument",
    )


def _render_reagents_tab() -> None:
    manufacturers = list_manufacturers()
    manufacturer_labels, manufacturer_map = _option_map(
        manufacturers,
        _manufacturer_label,
        placeholder="请选择厂家",
    )
    with st.form("md_create_reagent_form", clear_on_submit=True):
        manufacturer_label = st.selectbox("厂家 *", manufacturer_labels)
        col1, col2, col3 = st.columns(3)
        with col1:
            generic_name = st.text_input("试剂通用名称 *")
            trade_name = st.text_input("商品名称")
        with col2:
            specification = st.text_input("规格型号")
            registration_no = st.text_input("注册证 / 备案编号")
        with col3:
            catalog_no = st.text_input("产品货号")
            applicable_instrument = st.text_input("适用仪器")
        if st.form_submit_button("新增试剂", type="primary", width="stretch"):
            if manufacturer_map[manufacturer_label] is None:
                st.error("请选择厂家。")
            else:
                try:
                    create_reagent(
                        manufacturer_id=manufacturer_map[manufacturer_label],
                        generic_name=generic_name,
                        trade_name=trade_name,
                        specification=specification,
                        registration_no=registration_no,
                        catalog_no=catalog_no,
                        applicable_instrument_text=applicable_instrument,
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success("试剂已新增。")
                    st.rerun()
    reagents = list_reagents(
        include_disabled=st.checkbox(
            "显示已停用试剂",
            key="md_show_disabled_reagents",
        )
    )
    _display_table(
        reagents,
        {
            "manufacturer_name": "厂家",
            "generic_name": "通用名称",
            "trade_name": "商品名称",
            "specification": "规格",
            "registration_no": "注册证 / 备案编号",
            "catalog_no": "货号",
            "origin_type": "来源类型",
            "is_disabled": "状态",
        },
    )
    _render_disable_control(
        entity_type="reagent",
        dataframe=list_reagents(include_disabled=True),
        label_builder=_reagent_label,
        key_prefix="reagent",
    )


def _render_qc_materials_tab() -> None:
    manufacturers = list_manufacturers()
    manufacturer_labels, manufacturer_map = _option_map(
        manufacturers,
        _manufacturer_label,
        placeholder="请选择厂家",
    )
    with st.expander("新增质控品", expanded=False):
        with st.form("md_create_qc_material_form", clear_on_submit=True):
            manufacturer_label = st.selectbox("厂家 *", manufacturer_labels)
            col1, col2, col3 = st.columns(3)
            with col1:
                generic_name = st.text_input("质控品名称 *")
                trade_name = st.text_input("商品名称")
            with col2:
                matrix = st.text_input("基质")
                physical_form = st.text_input("物理形态")
            with col3:
                catalog_no = st.text_input("产品货号")
                registration_no = st.text_input("注册证 / 备案编号")
            nominal_level_count = st.number_input(
                "通常水平数",
                min_value=1,
                max_value=9,
                value=1,
                step=1,
            )
            if st.form_submit_button("新增质控品", width="stretch"):
                if manufacturer_map[manufacturer_label] is None:
                    st.error("请选择厂家。")
                else:
                    try:
                        create_qc_material(
                            manufacturer_id=manufacturer_map[manufacturer_label],
                            generic_name=generic_name,
                            trade_name=trade_name,
                            matrix=matrix,
                            physical_form=physical_form,
                            catalog_no=catalog_no,
                            registration_no=registration_no,
                            nominal_level_count=int(nominal_level_count),
                        )
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.success("质控品已新增。")
                        st.rerun()

    materials = list_qc_materials()
    material_labels, material_map = _option_map(
        materials,
        _qc_material_label,
        placeholder="请选择质控品",
    )
    with st.expander("新增质控品批号", expanded=True):
        with st.form("md_create_qc_lot_form", clear_on_submit=True):
            material_label = st.selectbox("质控品 *", material_labels)
            col1, col2 = st.columns(2)
            with col1:
                lot_no = st.text_input("批号 *")
            with col2:
                expiry = st.date_input(
                    "效期 *",
                    value=date.today(),
                    format="YYYY-MM-DD",
                )
            if st.form_submit_button("新增批号", type="primary", width="stretch"):
                if material_map[material_label] is None:
                    st.error("请选择质控品。")
                else:
                    try:
                        create_qc_lot(
                            qc_material_id=int(material_map[material_label]),
                            lot_no=lot_no,
                            expiry_date=expiry,
                        )
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.success("质控品批号已新增。")
                        st.rerun()

    lots = list_qc_lots()
    lot_labels, lot_map = _option_map(
        lots,
        _lot_label,
        placeholder="请选择质控品批号",
    )
    with st.expander("为批号新增水平", expanded=True):
        with st.form("md_create_qc_level_form", clear_on_submit=True):
            lot_label = st.selectbox("质控品批号 *", lot_labels)
            col1, col2, col3 = st.columns(3)
            with col1:
                level_name = st.text_input("水平名称 *", placeholder="例如：低值")
            with col2:
                level_order = st.number_input(
                    "水平顺序 *",
                    min_value=1,
                    max_value=9,
                    value=1,
                    step=1,
                )
            with col3:
                concentration_label = st.text_input("厂家浓度说明")
            if st.form_submit_button("新增水平", width="stretch"):
                if lot_map[lot_label] is None:
                    st.error("请选择质控品批号。")
                else:
                    try:
                        create_qc_level(
                            qc_material_lot_id=int(lot_map[lot_label]),
                            level_name=level_name,
                            level_order=int(level_order),
                            concentration_label=concentration_label,
                        )
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.success("质控水平已新增。")
                        st.rerun()

    show_disabled = st.checkbox(
        "显示已停用质控品、批号和水平",
        key="md_show_disabled_qc",
    )
    sub1, sub2, sub3 = st.tabs(["质控品", "批号", "水平"])
    with sub1:
        all_materials = list_qc_materials(include_disabled=show_disabled)
        _display_table(
            all_materials,
            {
                "manufacturer_name": "厂家",
                "generic_name": "质控品",
                "trade_name": "商品名称",
                "matrix": "基质",
                "physical_form": "形态",
                "nominal_level_count": "通常水平数",
                "is_disabled": "状态",
            },
        )
        _render_disable_control(
            entity_type="qc_material",
            dataframe=list_qc_materials(include_disabled=True),
            label_builder=_qc_material_label,
            key_prefix="qc_material",
        )
    with sub2:
        all_lots = list_qc_lots(include_disabled=show_disabled)
        _display_table(
            all_lots,
            {
                "manufacturer_name": "厂家",
                "qc_material_name": "质控品",
                "lot_no": "批号",
                "expiry_date": "效期",
                "is_disabled": "状态",
                "created_at": "创建时间",
            },
        )
        _render_disable_control(
            entity_type="qc_lot",
            dataframe=list_qc_lots(include_disabled=True),
            label_builder=_lot_label,
            key_prefix="qc_lot",
        )
    with sub3:
        all_levels = list_qc_levels(include_disabled=show_disabled)
        _display_table(
            all_levels,
            {
                "qc_material_name": "质控品",
                "lot_no": "批号",
                "level_name": "水平",
                "level_order": "顺序",
                "concentration_label": "浓度说明",
                "is_disabled": "状态",
            },
        )
        _render_disable_control(
            entity_type="qc_level",
            dataframe=list_qc_levels(include_disabled=True),
            label_builder=lambda row: (
                f"{_safe_text(row.get('qc_material_name'))}｜"
                f"{_safe_text(row.get('lot_no'))}｜"
                f"{_safe_text(row.get('level_name'))}"
            ),
            key_prefix="qc_level",
        )


def _render_methods_units_tab() -> None:
    left, right = st.columns(2)
    with left:
        st.markdown("**方法学字典**")
        with st.form("md_create_method_form", clear_on_submit=True):
            method_name = st.text_input("方法学名称 *")
            method_code = st.text_input("方法代码")
            method_category = st.text_input("专业分类")
            principle = st.text_input("检测原理")
            if st.form_submit_button("新增方法学", width="stretch"):
                try:
                    create_method(
                        method_name=method_name,
                        method_code=method_code,
                        method_category=method_category,
                        principle=principle,
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success("方法学已新增。")
                    st.rerun()
        methods = list_methods(
            include_disabled=st.checkbox(
                "显示已停用方法学",
                key="md_show_disabled_methods",
            )
        )
        _display_table(
            methods,
            {
                "method_name": "方法学",
                "method_code": "代码",
                "method_category": "分类",
                "principle": "原理",
                "origin_type": "来源类型",
                "is_disabled": "状态",
            },
        )
        _render_disable_control(
            entity_type="method",
            dataframe=list_methods(include_disabled=True),
            label_builder=lambda row: _safe_text(row.get("method_name")),
            key_prefix="method",
        )

    with right:
        st.markdown("**单位字典**")
        with st.form("md_create_unit_form", clear_on_submit=True):
            symbol = st.text_input("单位符号 *")
            unit_name = st.text_input("单位名称")
            ucum_code = st.text_input("UCUM 代码")
            quantity_kind = st.text_input("量纲 / 类型")
            if st.form_submit_button("新增单位", width="stretch"):
                try:
                    create_unit(
                        symbol=symbol,
                        unit_name=unit_name,
                        ucum_code=ucum_code,
                        quantity_kind=quantity_kind,
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success("单位已新增。")
                    st.rerun()
        units = list_units(
            include_disabled=st.checkbox(
                "显示已停用单位",
                key="md_show_disabled_units",
            )
        )
        _display_table(
            units,
            {
                "symbol": "符号",
                "unit_name": "单位名称",
                "ucum_code": "UCUM",
                "quantity_kind": "类型",
                "origin_type": "来源类型",
                "is_disabled": "状态",
            },
        )
        _render_disable_control(
            entity_type="unit",
            dataframe=list_units(include_disabled=True),
            label_builder=lambda row: _safe_text(row.get("symbol")),
            key_prefix="unit",
        )


def render_master_data_page() -> None:
    action_column, _ = st.columns([0.24, 0.76], gap="small")
    with action_column:
        if st.button("返回当前工作台", key="close_master_data_page", use_container_width=True):
            st.session_state["show_master_data_page"] = False
            st.rerun()

    render_section_intro(
        title="基础资料",
        eyebrow="全局入口",
        caption=(
            "维护新版项目管理使用的检验项目、厂家、仪器、试剂、质控品、方法学和单位。"
            "本页面只使用 V1.1 新数据表，不导入旧测试项目。"
        ),
        badges=["本地可新增", "软停用", "官方与本地隔离"],
        tone="accent",
    )
    st.info(
        "建议先按“厂家 → 仪器 / 试剂 / 质控品 → 检验项目 → 项目模板”的顺序维护。"
        "官方词库后续更新时不会覆盖医院本地新增内容。"
    )
    tabs = st.tabs(["厂家", "检验项目", "仪器", "试剂", "质控品与批号", "方法与单位"])
    with tabs[0]:
        _render_manufacturers_tab()
    with tabs[1]:
        _render_test_items_tab()
    with tabs[2]:
        _render_instruments_tab()
    with tabs[3]:
        _render_reagents_tab()
    with tabs[4]:
        _render_qc_materials_tab()
    with tabs[5]:
        _render_methods_units_tab()
