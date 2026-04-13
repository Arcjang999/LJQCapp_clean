from __future__ import annotations

import pandas as pd
import streamlit as st

from database import (
    count_project_batches,
    create_batch,
    create_project,
    create_zscore_batch,
    create_zscore_project,
    get_batch,
    get_project,
    get_zscore_batch,
    get_zscore_project,
    list_batches,
    list_projects,
    list_zscore_batches,
    list_zscore_projects,
    update_batch,
    update_project,
)
from services.value_type_service import (
    INPUT_VALUE_TYPE_OPTIONS,
    get_input_value_type_label,
    normalize_input_value_type,
)
from ui.common import (
    TEXT,
    format_datetime_column,
    format_optional_float,
    format_optional_input_value,
    get_saved_batch_cv_limit,
    parse_optional_cv_limit_input,
)
from zscore_logic import format_zscore_level_label_summary, resolve_zscore_batch_context


def _clean_selector_label_part(value, fallback: str = "") -> str:
    cleaned_value = " ".join(str(value or "").split()).strip()
    return cleaned_value or fallback


def _format_selector_datetime(value) -> str:
    cleaned_value = _clean_selector_label_part(value)
    if not cleaned_value:
        return ""
    try:
        return pd.to_datetime(cleaned_value).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return cleaned_value


def _join_display_parts(parts: list[str]) -> str:
    return " | ".join(part for part in parts if part)


def _is_instant_origin_flag(value) -> bool:
    if value is None:
        return False
    text = str(value).strip().lower()
    if not text:
        return False
    return text in {"1", "true", "instant"}


def _get_lj_project_source_label(row: pd.Series | dict[str, object]) -> str:
    return "由即时法转入" if _is_instant_origin_flag(dict(row).get("is_from_instant")) else "普通创建"


def _get_lj_batch_source_label(row: pd.Series | dict[str, object]) -> str:
    return "由即时法转入" if _is_instant_origin_flag(dict(row).get("source_method")) else "普通创建"


def build_project_label(row: pd.Series) -> str:
    row = dict(row)
    project_name = _clean_selector_label_part(row.get("name"), "未命名项目")
    input_value_type_label = get_input_value_type_label(row.get("input_value_type"))
    parts = [project_name, input_value_type_label]
    if _get_lj_project_source_label(row) == "由即时法转入":
        parts.append("由即时法转入")
    return _join_display_parts(parts)


def build_batch_label(row: pd.Series) -> str:
    row = dict(row)
    lot_no = _clean_selector_label_part(row.get("lot_no"))
    created_at = _format_selector_datetime(row.get("created_at"))
    parts: list[str] = []
    if lot_no:
        parts.append(f"质控批号：{lot_no}")
    else:
        fallback_summary = _join_display_parts(
            [
                _clean_selector_label_part(row.get("instrument")),
                _clean_selector_label_part(row.get("reagent")),
                _clean_selector_label_part(row.get("qc_material")),
            ]
        )
        if fallback_summary:
            parts.append(fallback_summary)
    if _get_lj_batch_source_label(row) == "由即时法转入":
        parts.append("由即时法转入")
    if created_at:
        parts.append(f"创建于 {created_at}")
    if not parts:
        parts.append("未命名批次")
    return _join_display_parts(parts)


def build_zscore_project_label(row: pd.Series) -> str:
    row = dict(row)
    project_name = _clean_selector_label_part(row.get("name"), "未命名项目")
    level_count = int(row.get("level_count", 0) or 0)
    input_value_type_label = get_input_value_type_label(row.get("input_value_type"))
    return _join_display_parts([project_name, f"{level_count} 水平", input_value_type_label])


def build_zscore_batch_label(row: pd.Series) -> str:
    row = dict(row)
    lot_no = _clean_selector_label_part(row.get("lot_no"))
    level_count = int(row.get("level_count", 0) or 0)
    created_at = _format_selector_datetime(row.get("created_at"))
    parts: list[str] = []
    if lot_no:
        parts.append(f"质控批号：{lot_no}")
    else:
        fallback_summary = _join_display_parts(
            [
                _clean_selector_label_part(row.get("instrument")),
                _clean_selector_label_part(row.get("reagent")),
            ]
        )
        if fallback_summary:
            parts.append(fallback_summary)
    parts.append(f"{level_count} 水平")
    if created_at:
        parts.append(f"创建于 {created_at}")
    if not parts:
        parts.append("未命名批次")
    return _join_display_parts(parts)


def _build_lj_project_table(projects_df: pd.DataFrame) -> pd.DataFrame:
    if projects_df.empty:
        return projects_df
    display_df = format_datetime_column(projects_df, "created_at").copy()
    display_df["name"] = display_df["name"].map(lambda value: _clean_selector_label_part(value, "未命名项目"))
    display_df["input_value_type"] = display_df["input_value_type"].map(get_input_value_type_label)
    display_df["source"] = display_df.apply(_get_lj_project_source_label, axis=1)
    return display_df[["name", "input_value_type", "source", "created_at"]].rename(
        columns={
            "name": "项目名称",
            "input_value_type": "输入值类型",
            "source": "来源",
            "created_at": "创建时间",
        }
    )


def _build_lj_batch_table(batches_df: pd.DataFrame) -> pd.DataFrame:
    if batches_df.empty:
        return batches_df
    display_df = format_datetime_column(batches_df, "created_at").copy()
    for column_name in ["lot_no", "instrument", "reagent", "qc_material", "concentration"]:
        display_df[column_name] = display_df[column_name].map(_clean_selector_label_part)
    display_df["source"] = display_df.apply(_get_lj_batch_source_label, axis=1)
    return display_df[
        ["lot_no", "instrument", "reagent", "qc_material", "concentration", "source", "created_at"]
    ].rename(
        columns={
            "lot_no": "质控品批号",
            "instrument": "仪器",
            "reagent": "试剂",
            "qc_material": "质控品",
            "concentration": "浓度",
            "source": "来源",
            "created_at": "创建时间",
        }
    )


def _build_zscore_project_table(projects_df: pd.DataFrame) -> pd.DataFrame:
    if projects_df.empty:
        return projects_df
    display_df = format_datetime_column(projects_df, "created_at").copy()
    display_df["name"] = display_df["name"].map(lambda value: _clean_selector_label_part(value, "未命名项目"))
    display_df["level_count"] = display_df["level_count"].map(lambda value: f"{int(value or 0)} 水平")
    display_df["input_value_type"] = display_df["input_value_type"].map(get_input_value_type_label)
    return display_df[["name", "level_count", "input_value_type", "created_at"]].rename(
        columns={
            "name": "项目名称",
            "level_count": "水平数",
            "input_value_type": "输入值类型",
            "created_at": "创建时间",
        }
    )


def _build_zscore_batch_table(batches_df: pd.DataFrame) -> pd.DataFrame:
    if batches_df.empty:
        return batches_df
    display_df = format_datetime_column(batches_df, "created_at").copy()
    for column_name in ["lot_no", "instrument", "reagent", "qc_material", "concentration"]:
        display_df[column_name] = display_df[column_name].map(_clean_selector_label_part)
    display_df["level_count"] = display_df["level_count"].map(lambda value: f"{int(value or 0)} 水平")
    return display_df[
        ["lot_no", "level_count", "instrument", "reagent", "qc_material", "concentration", "created_at"]
    ].rename(
        columns={
            "lot_no": "质控品批号",
            "level_count": "水平数",
            "instrument": "仪器",
            "reagent": "试剂",
            "qc_material": "质控品",
            "concentration": "浓度",
            "created_at": "创建时间",
        }
    )


def ensure_selected_project(projects_df: pd.DataFrame) -> int | None:
    if projects_df.empty:
        st.session_state["selected_project_id"] = None
        st.session_state["project_selector"] = "请选择项目"
        return None

    valid_ids = set(projects_df["id"].astype(int).tolist())
    current_id = st.session_state.get("selected_project_id")
    if current_id is not None and current_id not in valid_ids:
        st.session_state["selected_project_id"] = None
        st.session_state["project_selector"] = "请选择项目"
        return None
    return None if current_id is None else int(current_id)


def ensure_selected_batch(batches_df: pd.DataFrame) -> int | None:
    if batches_df.empty:
        st.session_state["selected_batch_id"] = None
        st.session_state["batch_selector"] = "请选择批次"
        return None

    valid_ids = set(batches_df["id"].astype(int).tolist())
    current_id = st.session_state.get("selected_batch_id")
    if current_id is not None and current_id not in valid_ids:
        st.session_state["selected_batch_id"] = None
        st.session_state["batch_selector"] = "请选择批次"
        return None
    return None if current_id is None else int(current_id)


def guard_work_tab_selection(
    work_tab,
    selected_project_id: int | None,
    selected_batch_id: int | None,
) -> None:
    if selected_project_id is None:
        with work_tab:
            st.info(TEXT["choose_project"])
        st.stop()

    if selected_batch_id is None:
        with work_tab:
            st.info(TEXT["choose_batch"])
        st.stop()


def prepare_project_batch_context() -> tuple[pd.DataFrame, int | None, pd.DataFrame, int | None]:
    projects_df = list_projects()
    selected_project_id = ensure_selected_project(projects_df)
    batches_df = list_batches(selected_project_id) if selected_project_id is not None else pd.DataFrame()
    selected_batch_id = ensure_selected_batch(batches_df)
    return projects_df, selected_project_id, batches_df, selected_batch_id


def ensure_selected_zscore_project(projects_df: pd.DataFrame) -> int | None:
    if projects_df.empty:
        st.session_state["zscore_selected_project_id"] = None
        st.session_state["zscore_project_selector"] = "请选择 Z-score 项目"
        return None

    valid_ids = set(projects_df["id"].astype(int).tolist())
    current_id = st.session_state.get("zscore_selected_project_id")
    if current_id is not None and current_id not in valid_ids:
        st.session_state["zscore_selected_project_id"] = None
        st.session_state["zscore_project_selector"] = "请选择 Z-score 项目"
        return None
    return None if current_id is None else int(current_id)


def ensure_selected_zscore_batch(batches_df: pd.DataFrame) -> int | None:
    if batches_df.empty:
        st.session_state["zscore_selected_batch_id"] = None
        st.session_state["zscore_batch_selector"] = "请选择 Z-score 批次"
        return None

    valid_ids = set(batches_df["id"].astype(int).tolist())
    current_id = st.session_state.get("zscore_selected_batch_id")
    if current_id is not None and current_id not in valid_ids:
        st.session_state["zscore_selected_batch_id"] = None
        st.session_state["zscore_batch_selector"] = "请选择 Z-score 批次"
        return None
    return None if current_id is None else int(current_id)


def prepare_zscore_project_batch_context() -> tuple[pd.DataFrame, int | None, pd.DataFrame, int | None]:
    projects_df = list_zscore_projects()
    selected_project_id = ensure_selected_zscore_project(projects_df)
    batches_df = list_zscore_batches(selected_project_id) if selected_project_id is not None else pd.DataFrame()
    selected_batch_id = ensure_selected_zscore_batch(batches_df)
    return projects_df, selected_project_id, batches_df, selected_batch_id


def render_project_batch_management(
    manage_tab,
    projects_df: pd.DataFrame,
    selected_project_id: int | None,
    batches_df: pd.DataFrame,
    selected_batch_id: int | None,
) -> None:
    with manage_tab:
        top_left, top_right = st.columns([1, 1.4])

        with top_left:
            st.subheader("新建项目")
            with st.form("create_project_form", clear_on_submit=True):
                project_name = st.text_input("项目名称")
                input_value_type = st.radio(
                    "输入值类型",
                    options=INPUT_VALUE_TYPE_OPTIONS,
                    format_func=get_input_value_type_label,
                    horizontal=True,
                )
                project_submitted = st.form_submit_button("创建项目", width="stretch")

                if project_submitted:
                    if not project_name.strip():
                        st.error(TEXT["fill_project"])
                    else:
                        try:
                            project_id = create_project(
                                project_name.strip(),
                                input_value_type=input_value_type,
                            )
                        except ValueError as exc:
                            st.error(str(exc))
                        else:
                            st.session_state["selected_project_id"] = project_id
                            st.success(f"项目“{project_name.strip()}”已创建。")
                            st.rerun()

            st.subheader("项目列表与选择")
            if projects_df.empty:
                st.info(TEXT["no_project"])
            else:
                project_labels, project_options = build_project_select_options(projects_df)
                sync_selector_state(
                    selector_key="project_selector",
                    selected_id_key="selected_project_id",
                    options_map=project_options,
                    placeholder=project_labels[0],
                )
                selected_project_label = st.selectbox(
                    "选择项目",
                    options=project_labels,
                    key="project_selector",
                )
                new_project_id = project_options[selected_project_label]
                if new_project_id != selected_project_id:
                    st.session_state["selected_project_id"] = new_project_id
                    st.session_state["selected_batch_id"] = None
                    st.session_state["batch_selector"] = "请选择批次"
                    st.rerun()

                project_table = _build_lj_project_table(projects_df)
                st.dataframe(project_table, width="stretch", hide_index=True)

                if selected_project_id is not None:
                    current_project = get_project(selected_project_id)
                    current_input_value_type = normalize_input_value_type(
                        current_project["input_value_type"]
                    )
                    has_existing_batches = count_project_batches(selected_project_id) > 0
                    with st.expander("编辑当前项目"):
                        with st.form("edit_project_form"):
                            edit_project_name = st.text_input(
                                "项目名称",
                                value=current_project["name"],
                            )
                            edit_input_value_type = st.radio(
                                "输入值类型",
                                options=INPUT_VALUE_TYPE_OPTIONS,
                                format_func=get_input_value_type_label,
                                index=INPUT_VALUE_TYPE_OPTIONS.index(current_input_value_type),
                                horizontal=True,
                                disabled=has_existing_batches,
                            )
                            if has_existing_batches:
                                st.info(
                                    "当前项目下已存在批次。为避免批次口径与历史数据不一致，输入值类型已锁定为只读显示。"
                                )
                            edit_project_submitted = st.form_submit_button(
                                "保存项目修改",
                                width="stretch",
                            )
                            if edit_project_submitted:
                                cleaned_name = edit_project_name.strip()
                                if not cleaned_name:
                                    st.error(TEXT["fill_project"])
                                else:
                                    try:
                                        update_project(
                                            selected_project_id,
                                            cleaned_name,
                                            input_value_type=edit_input_value_type,
                                        )
                                    except ValueError as exc:
                                        st.error(str(exc))
                                    else:
                                        st.success("项目名称已更新。")
                                        st.rerun()

        with top_right:
            st.subheader("新建批次")
            if selected_project_id is None:
                st.info(TEXT["choose_project"])
            else:
                current_project = get_project(selected_project_id)
                current_input_value_type_label = get_input_value_type_label(
                    current_project["input_value_type"]
                )
                st.caption(
                    f"当前批次将归属于项目：{current_project['name']}｜输入值类型固定为 {current_input_value_type_label}。"
                )
                with st.form("create_batch_form", clear_on_submit=True):
                    instrument = st.text_input("仪器")
                    reagent = st.text_input("试剂")
                    qc_material = st.text_input("质控品")
                    concentration = st.text_input("浓度")
                    lot_no = st.text_input("质控品批号")
                    target_n = st.selectbox(
                        "建靶所需次数",
                        options=list(range(5, 21)),
                        index=15,
                    )
                    cv_limit_text = st.text_input(
                        "CV 要求（%）（可选）",
                        placeholder="例如：5.00",
                    )
                    create_submitted = st.form_submit_button("创建批次", width="stretch")

                    if create_submitted:
                        fields = [instrument, reagent, qc_material, concentration, lot_no]
                        validation_errors: list[str] = []
                        cv_limit, cv_limit_error = parse_optional_cv_limit_input(cv_limit_text)
                        if any(not field.strip() for field in fields):
                            validation_errors.append(TEXT["fill_batch"])
                        if cv_limit_error:
                            validation_errors.append(cv_limit_error)
                        if validation_errors:
                            st.error("\n".join(dict.fromkeys(validation_errors)))
                        else:
                            try:
                                batch_id = create_batch(
                                    project_id=selected_project_id,
                                    instrument=instrument.strip(),
                                    reagent=reagent.strip(),
                                    qc_material=qc_material.strip(),
                                    concentration=concentration.strip(),
                                    lot_no=lot_no.strip(),
                                    target_n=int(target_n),
                                    cv_limit=cv_limit,
                                )
                            except ValueError as exc:
                                st.error(str(exc))
                            else:
                                st.session_state["selected_batch_id"] = batch_id
                                st.success(f"批次“{lot_no.strip()}”已创建。")
                                st.rerun()

            st.subheader("批次列表与选择")
            if selected_project_id is None:
                st.info(TEXT["choose_project"])
            elif batches_df.empty:
                st.info(TEXT["no_batch"])
            else:
                batch_labels, batch_options = build_batch_select_options(batches_df)
                sync_selector_state(
                    selector_key="batch_selector",
                    selected_id_key="selected_batch_id",
                    options_map=batch_options,
                    placeholder=batch_labels[0],
                )
                selected_batch_label = st.selectbox(
                    "选择批次",
                    options=batch_labels,
                    key="batch_selector",
                )
                new_batch_id = batch_options[selected_batch_label]
                if new_batch_id != selected_batch_id:
                    st.session_state["selected_batch_id"] = new_batch_id
                    st.rerun()

                batch_table = _build_lj_batch_table(batches_df)
                st.dataframe(batch_table, width="stretch", hide_index=True)

                if selected_batch_id is not None:
                    current_batch = get_batch(selected_batch_id)
                    with st.expander("编辑当前批次"):
                        st.markdown("**批次固定信息**")
                        st.text(f"仪器：{current_batch['instrument']}")
                        st.text(f"试剂：{current_batch['reagent']}")
                        st.text(f"质控品：{current_batch['qc_material']}")
                        st.text(f"浓度：{current_batch['concentration']}")
                        st.text(f"输入值类型：{get_input_value_type_label(current_batch['input_value_type'])}")
                        st.text(f"建靶所需次数：{current_batch['target_n']}")
                        st.text(
                            f"CV 要求：{format_optional_float(get_saved_batch_cv_limit(current_batch), digits=2, suffix='%')}"
                        )
                        st.markdown("**可编辑信息**")
                        with st.form("edit_batch_form"):
                            edit_lot_no = st.text_input(
                                "质控品批号",
                                value=current_batch["lot_no"],
                            )
                            edit_cv_limit_text = st.text_input(
                                "CV 要求（%）（可选）",
                                value=format_optional_input_value(
                                    get_saved_batch_cv_limit(current_batch),
                                    digits=2,
                                ),
                            )
                            edit_batch_submitted = st.form_submit_button(
                                "保存批次修改",
                                width="stretch",
                            )
                            if edit_batch_submitted:
                                validation_errors: list[str] = []
                                edit_cv_limit, edit_cv_limit_error = parse_optional_cv_limit_input(edit_cv_limit_text)
                                if not edit_lot_no.strip():
                                    validation_errors.append("请填写质控品批号。")
                                if edit_cv_limit_error:
                                    validation_errors.append(edit_cv_limit_error)
                                if validation_errors:
                                    st.error("\n".join(dict.fromkeys(validation_errors)))
                                else:
                                    update_batch(
                                        selected_batch_id,
                                        edit_lot_no.strip(),
                                        cv_limit=edit_cv_limit,
                                    )
                                    st.success("批次质控品批号已更新。")
                                    st.rerun()


def render_zscore_project_batch_management(
    manage_tab,
    projects_df: pd.DataFrame,
    selected_project_id: int | None,
    batches_df: pd.DataFrame,
    selected_batch_id: int | None,
) -> None:
    with manage_tab:
        top_left, top_right = st.columns([1, 1.4])

        with top_left:
            st.subheader("新建 Z-score 项目")
            with st.form("create_zscore_project_form", clear_on_submit=True):
                project_name = st.text_input("项目名称")
                input_value_type = st.radio(
                    "输入值类型",
                    options=INPUT_VALUE_TYPE_OPTIONS,
                    format_func=get_input_value_type_label,
                    horizontal=True,
                )
                level_count = st.radio(
                    "项目水平数",
                    options=[2, 3],
                    horizontal=True,
                )
                project_submitted = st.form_submit_button("创建 Z-score 项目", width="stretch")

                if project_submitted:
                    if not project_name.strip():
                        st.error(TEXT["fill_project"])
                    else:
                        try:
                            project_id = create_zscore_project(
                                project_name.strip(),
                                int(level_count),
                                input_value_type=input_value_type,
                            )
                        except ValueError as exc:
                            st.error(str(exc))
                        else:
                            st.session_state["zscore_selected_project_id"] = project_id
                            st.session_state["zscore_selected_batch_id"] = None
                            st.success(f"Z-score 项目“{project_name.strip()}”已创建。")
                            st.rerun()

            st.subheader("Z-score 项目列表与选择")
            if projects_df.empty:
                st.info("当前还没有 Z-score 项目，请先创建项目并确定双水平或三水平流程。")
            else:
                project_labels, project_options = build_zscore_project_select_options(projects_df)
                sync_selector_state(
                    selector_key="zscore_project_selector",
                    selected_id_key="zscore_selected_project_id",
                    options_map=project_options,
                    placeholder=project_labels[0],
                )
                selected_project_label = st.selectbox(
                    "选择 Z-score 项目",
                    options=project_labels,
                    key="zscore_project_selector",
                )
                new_project_id = project_options[selected_project_label]
                if new_project_id != selected_project_id:
                    st.session_state["zscore_selected_project_id"] = new_project_id
                    st.session_state["zscore_selected_batch_id"] = None
                    st.session_state["zscore_batch_selector"] = "请选择 Z-score 批次"
                    st.rerun()

                project_table = _build_zscore_project_table(projects_df)
                st.dataframe(project_table, width="stretch", hide_index=True)

                if selected_project_id is not None:
                    current_project = get_zscore_project(selected_project_id)
                    current_input_value_type = normalize_input_value_type(
                        current_project["input_value_type"]
                    )
                    has_existing_batches = count_project_batches(selected_project_id) > 0
                    with st.expander("当前 Z-score 项目配置"):
                        st.text(f"项目名称：{current_project['name']}")
                        st.text(f"水平数：{int(current_project['level_count'])} 水平")
                        st.text(f"输入值类型：{get_input_value_type_label(current_project['input_value_type'])}")
                        with st.form("edit_zscore_project_form"):
                            edit_project_name = st.text_input(
                                "项目名称",
                                value=current_project["name"],
                            )
                            edit_input_value_type = st.radio(
                                "输入值类型",
                                options=INPUT_VALUE_TYPE_OPTIONS,
                                format_func=get_input_value_type_label,
                                index=INPUT_VALUE_TYPE_OPTIONS.index(current_input_value_type),
                                horizontal=True,
                                disabled=has_existing_batches,
                            )
                            if has_existing_batches:
                                st.info(
                                    "当前项目下已存在批次。为避免已建批次的录入、模板和图表口径被动变化，输入值类型已锁定。"
                                )
                            edit_project_submitted = st.form_submit_button("保存项目修改", width="stretch")
                            if edit_project_submitted:
                                cleaned_name = edit_project_name.strip()
                                if not cleaned_name:
                                    st.error(TEXT["fill_project"])
                                else:
                                    try:
                                        update_project(
                                            selected_project_id,
                                            cleaned_name,
                                            input_value_type=edit_input_value_type,
                                        )
                                    except ValueError as exc:
                                        st.error(str(exc))
                                    else:
                                        st.success("项目名称已更新，水平数保持不变。")
                                        st.rerun()

        with top_right:
            st.subheader("新建 Z-score 批次")
            if selected_project_id is None:
                st.info("请先选择 Z-score 项目。")
            else:
                current_project = get_zscore_project(selected_project_id)
                project_level_count = int(current_project["level_count"])
                current_input_value_type_label = get_input_value_type_label(
                    current_project["input_value_type"]
                )
                st.caption(
                    f"当前批次将归属于项目：{current_project['name']}｜固定为 {project_level_count} 水平｜输入值类型为 {current_input_value_type_label}。"
                )
                with st.form("create_zscore_batch_form", clear_on_submit=True):
                    instrument = st.text_input("仪器")
                    reagent = st.text_input("试剂")
                    qc_material = st.text_input("质控品")
                    concentration = st.text_input("浓度")
                    lot_no = st.text_input("质控品批号")
                    st.markdown("**各水平名称与说明**")
                    level_1_label = st.text_input("水平 1 说明", placeholder="例如：低值质控")
                    level_2_label = st.text_input("水平 2 说明", placeholder="例如：中值或高值质控")
                    level_3_label = None
                    if project_level_count == 3:
                        level_3_label = st.text_input("水平 3 说明", placeholder="例如：高值质控")
                    target_n = st.selectbox(
                        "建靶所需次数",
                        options=list(range(5, 21)),
                        index=15,
                    )
                    cv_limit_text = st.text_input(
                        "CV 要求（%）（可选）",
                        placeholder="例如：5.00",
                    )
                    create_submitted = st.form_submit_button("创建 Z-score 批次", width="stretch")

                    if create_submitted:
                        fields = [instrument, reagent, qc_material, concentration, lot_no]
                        validation_errors: list[str] = []
                        cv_limit, cv_limit_error = parse_optional_cv_limit_input(cv_limit_text)
                        if any(not field.strip() for field in fields):
                            validation_errors.append(TEXT["fill_batch"])
                        if cv_limit_error:
                            validation_errors.append(cv_limit_error)
                        if validation_errors:
                            st.error("\n".join(dict.fromkeys(validation_errors)))
                        else:
                            try:
                                batch_id = create_zscore_batch(
                                    project_id=selected_project_id,
                                    instrument=instrument.strip(),
                                    reagent=reagent.strip(),
                                    qc_material=qc_material.strip(),
                                    concentration=concentration.strip(),
                                    lot_no=lot_no.strip(),
                                    target_n=int(target_n),
                                    level_1_label=level_1_label,
                                    level_2_label=level_2_label,
                                    level_3_label=level_3_label,
                                    cv_limit=cv_limit,
                                )
                            except ValueError as exc:
                                st.error(str(exc))
                            else:
                                st.session_state["zscore_selected_batch_id"] = batch_id
                                st.success(f"Z-score 批次“{lot_no.strip()}”已创建。")
                                st.rerun()

            st.subheader("Z-score 批次列表与选择")
            if selected_project_id is None:
                st.info("请先选择 Z-score 项目。")
            elif batches_df.empty:
                st.info("当前项目下还没有 Z-score 批次，请先创建批次。")
            else:
                batch_labels, batch_options = build_zscore_batch_select_options(batches_df)
                sync_selector_state(
                    selector_key="zscore_batch_selector",
                    selected_id_key="zscore_selected_batch_id",
                    options_map=batch_options,
                    placeholder=batch_labels[0],
                )
                selected_batch_label = st.selectbox(
                    "选择 Z-score 批次",
                    options=batch_labels,
                    key="zscore_batch_selector",
                )
                new_batch_id = batch_options[selected_batch_label]
                if new_batch_id != selected_batch_id:
                    st.session_state["zscore_selected_batch_id"] = new_batch_id
                    st.rerun()

                batch_table = _build_zscore_batch_table(batches_df)
                st.dataframe(batch_table, width="stretch", hide_index=True)

                if selected_batch_id is not None:
                    current_batch = get_zscore_batch(selected_batch_id)
                    with st.expander("当前 Z-score 批次配置"):
                        current_level_ids = list(resolve_zscore_batch_context(selected_batch_id)["required_level_ids"])
                        st.text(f"项目：{current_batch['project_name']}")
                        st.text(f"质控品批号：{current_batch['lot_no']}")
                        st.text(f"水平数：{int(current_batch['level_count'])} 水平")
                        st.text(f"输入值类型：{get_input_value_type_label(current_batch['input_value_type'])}")
                        st.text(f"水平说明：{format_zscore_level_label_summary(current_batch, current_level_ids)}")
                        st.text(f"建靶所需次数：{current_batch['target_n']}")
                        st.text(
                            f"CV 要求：{format_optional_float(get_saved_batch_cv_limit(current_batch), digits=2, suffix='%')}"
                        )
                        with st.form("edit_zscore_batch_form"):
                            edit_lot_no = st.text_input(
                                "质控品批号",
                                value=current_batch["lot_no"],
                            )
                            edit_cv_limit_text = st.text_input(
                                "CV 要求（%）（可选）",
                                value=format_optional_input_value(
                                    get_saved_batch_cv_limit(current_batch),
                                    digits=2,
                                ),
                            )
                            edit_batch_submitted = st.form_submit_button("保存批次修改", width="stretch")
                            if edit_batch_submitted:
                                validation_errors: list[str] = []
                                edit_cv_limit, edit_cv_limit_error = parse_optional_cv_limit_input(edit_cv_limit_text)
                                if not edit_lot_no.strip():
                                    validation_errors.append("请填写质控品批号。")
                                if edit_cv_limit_error:
                                    validation_errors.append(edit_cv_limit_error)
                                if validation_errors:
                                    st.error("\n".join(dict.fromkeys(validation_errors)))
                                else:
                                    update_batch(
                                        selected_batch_id,
                                        edit_lot_no.strip(),
                                        cv_limit=edit_cv_limit,
                                    )
                                    st.success("批次质控品批号已更新，水平数保持不变。")
                                    st.rerun()


def build_project_select_options(projects_df: pd.DataFrame) -> tuple[list[str], dict[str, int | None]]:
    option_map = {"请选择项目": None}
    for _, row in projects_df.iterrows():
        option_map[build_project_label(row)] = int(row["id"])
    return list(option_map.keys()), option_map


def build_batch_select_options(batches_df: pd.DataFrame) -> tuple[list[str], dict[str, int | None]]:
    option_map = {"请选择批次": None}
    for _, row in batches_df.iterrows():
        option_map[build_batch_label(row)] = int(row["id"])
    return list(option_map.keys()), option_map


def build_zscore_project_select_options(projects_df: pd.DataFrame) -> tuple[list[str], dict[str, int | None]]:
    option_map = {"请选择 Z-score 项目": None}
    for _, row in projects_df.iterrows():
        option_map[build_zscore_project_label(row)] = int(row["id"])
    return list(option_map.keys()), option_map


def build_zscore_batch_select_options(batches_df: pd.DataFrame) -> tuple[list[str], dict[str, int | None]]:
    option_map = {"请选择 Z-score 批次": None}
    for _, row in batches_df.iterrows():
        option_map[build_zscore_batch_label(row)] = int(row["id"])
    return list(option_map.keys()), option_map


def sync_selector_state(
    selector_key: str,
    selected_id_key: str,
    options_map: dict[str, int | None],
    placeholder: str,
) -> None:
    current_id = st.session_state.get(selected_id_key)
    valid_ids = {value for value in options_map.values() if value is not None}
    if current_id is not None and current_id not in valid_ids:
        st.session_state[selected_id_key] = None

    current_label = st.session_state.get(selector_key)
    if current_label not in options_map:
        if current_id in valid_ids:
            for label, value in options_map.items():
                if value == current_id:
                    st.session_state[selector_key] = label
                    return
        st.session_state[selector_key] = placeholder
        return

    if current_label == placeholder and current_id in valid_ids:
        for label, value in options_map.items():
            if value == current_id:
                st.session_state[selector_key] = label
                return

    if current_label == placeholder:
        st.session_state[selected_id_key] = None
