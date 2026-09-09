from __future__ import annotations

import pandas as pd
import streamlit as st

from services.value_type_service import get_input_value_type_label
from services.workbench_config_service import (
    list_lj_workbench_batches,
    list_lj_workbench_configuration_issues,
    list_lj_workbench_projects,
)
from ui.common import open_global_page, render_section_intro, render_workbench_context_bar


PROJECT_PLACEHOLDER = "请选择已启用的 LJ 项目"
BATCH_PLACEHOLDER = "请选择已启用的批号配置"


def _clean(value: object, fallback: str = "-") -> str:
    if value is None or pd.isna(value):
        return fallback
    text = " ".join(str(value or "").split()).strip()
    return text or fallback


def _is_from_instant(row: pd.Series) -> bool:
    source_method = str(row.get("source_method") or "").strip().lower()
    return source_method == "instant" or int(row.get("is_from_instant") or 0) == 1


def _ensure_selected_id(dataframe: pd.DataFrame, state_key: str) -> int | None:
    if dataframe.empty:
        st.session_state[state_key] = None
        return None
    valid_ids = set(dataframe["id"].astype(int).tolist())
    current = st.session_state.get(state_key)
    try:
        current_id = int(current) if current is not None else None
    except (TypeError, ValueError):
        current_id = None
    if current_id not in valid_ids:
        st.session_state[state_key] = None
        return None
    return current_id


def prepare_lj_v12_project_batch_context() -> tuple[pd.DataFrame, int | None, pd.DataFrame, int | None]:
    projects = list_lj_workbench_projects()
    selected_project_id = _ensure_selected_id(projects, "selected_project_id")
    batches = (
        list_lj_workbench_batches(selected_project_id)
        if selected_project_id is not None
        else pd.DataFrame()
    )
    selected_batch_id = _ensure_selected_id(batches, "selected_batch_id")
    return projects, selected_project_id, batches, selected_batch_id


def _project_options(projects: pd.DataFrame) -> dict[str, int | None]:
    options: dict[str, int | None] = {PROJECT_PLACEHOLDER: None}
    for _, row in projects.iterrows():
        label_parts = [
            _clean(row.get("name"), "未命名项目"),
            _clean(row.get("instrument_name"), "未命名仪器"),
            get_input_value_type_label(row.get("input_value_type")),
        ]
        if _is_from_instant(row):
            label_parts.append("由即时法转入")
        label = "｜".join(label_parts)
        if label in options:
            label = f"{label}｜#{int(row['id'])}"
        options[label] = int(row["id"])
    return options


def _batch_options(batches: pd.DataFrame) -> dict[str, int | None]:
    options: dict[str, int | None] = {BATCH_PLACEHOLDER: None}
    for _, row in batches.iterrows():
        if _is_from_instant(row):
            label_parts = [
                f"质控批号：{_clean(row.get('lot_no'))}",
                "由即时法转入",
                f"转入时间 {_clean(row.get('source_transfer_time'))}",
            ]
        else:
            label_parts = [
                f"批号 {_clean(row.get('lot_no'))}",
                _clean(row.get("v11_config_name"), "未命名配置"),
                f"效期 {_clean(row.get('expiry_date'))}",
            ]
        label = "｜".join(label_parts)
        if label in options:
            label = f"{label}｜#{int(row['id'])}"
        options[label] = int(row["id"])
    return options


def _selector_index(options: dict[str, int | None], selected_id: int | None) -> int:
    labels = list(options.keys())
    if selected_id is None:
        return 0
    for index, label in enumerate(labels):
        if options[label] == selected_id:
            return index
    return 0


def render_lj_v12_configuration_selection(
    manage_tab,
    projects: pd.DataFrame,
    selected_project_id: int | None,
    batches: pd.DataFrame,
    selected_batch_id: int | None,
) -> None:
    with manage_tab:
        render_section_intro(
            title="LJ 项目与批号选择",
            caption=(
                "这里只显示全局“项目/批次管理”中已启用且使用本批次建靶的 LJ 新版配置，"
                "以及已经确认转入的即时法批次。项目和批号不再在工作台内新建。"
            ),
            badges=["新版配置", "即时法转入", "只读选择", "不读取普通旧测试项目"],
            tone="accent",
        )

        action_col1, action_col2, _ = st.columns([0.24, 0.24, 0.52], gap="small")
        with action_col1:
            if st.button("打开项目/批次管理", key="lj_open_v11_project_management", use_container_width=True):
                open_global_page("show_project_management_page")
        with action_col2:
            if st.button("打开基础资料", key="lj_open_v11_master_data", use_container_width=True):
                open_global_page("show_master_data_page")

        issues = list_lj_workbench_configuration_issues()
        if not issues.empty:
            st.warning(
                f"另有 {len(issues)} 个已启用 LJ 配置使用人工或厂家靶值，"
                "本轮为保持现有计算核心不变，暂不进入工作台。"
            )
            with st.expander("查看暂未接入的配置", expanded=False):
                st.dataframe(
                    issues.rename(
                        columns={
                            "config_name": "批号配置",
                            "test_item_name": "检验项目",
                            "lot_no": "质控品批号",
                            "target_source": "靶值来源",
                            "issue": "说明",
                        }
                    ),
                    hide_index=True,
                    width="stretch",
                )

        if projects.empty:
            st.info(
                "当前没有可进入 LJ 工作台的新版配置。请先在全局项目/批次管理中"
                "创建 LJ 项目、配置一个水平并启用批号配置，或从即时法确认转入。"
            )
            return

        project_map = _project_options(projects)
        project_labels = list(project_map.keys())
        batch_map = _batch_options(batches)
        batch_labels = list(batch_map.keys())
        selector_col1, selector_col2 = st.columns(2, gap="large")
        with selector_col1:
            selected_project_label = st.selectbox(
                "检验项目",
                options=project_labels,
                index=_selector_index(project_map, selected_project_id),
                key="v12_lj_project_selector",
            )
            new_project_id = project_map[selected_project_label]
            if new_project_id != selected_project_id:
                st.session_state["selected_project_id"] = new_project_id
                st.session_state["selected_batch_id"] = None
                st.rerun()

        with selector_col2:
            selected_batch_label = st.selectbox(
                "批号配置",
                options=batch_labels,
                index=_selector_index(batch_map, selected_batch_id),
                key="v12_lj_batch_selector",
                disabled=selected_project_id is None,
            )
            new_batch_id = batch_map[selected_batch_label]
            if new_batch_id != selected_batch_id:
                st.session_state["selected_batch_id"] = new_batch_id
                st.rerun()

        if selected_batch_id is None or batches.empty:
            st.info("请选择一个已启用的批号配置后进入“当前批次”。")
            return

        selected = batches[batches["id"].astype(int) == int(selected_batch_id)].iloc[0]
        is_from_instant = _is_from_instant(selected)
        render_workbench_context_bar(
            title="当前即时法转入批次" if is_from_instant else "当前新版配置",
            caption=(
                "该批次由即时法确认转入，来源与转入记录保持可追溯。"
                if is_from_instant
                else "以下信息来自启用时的项目与批号配置，工作台内只读。"
            ),
            items=[
                ("检验项目", selected["project_name"]),
                ("配置名称", _clean(selected["v11_config_name"])),
                ("仪器", selected["instrument"]),
                ("试剂", selected["reagent"]),
                ("质控品", selected["qc_material"]),
                ("质控品批号", selected["lot_no"]),
                ("水平", selected["concentration"]),
                ("单位", _clean(selected.get("unit_symbol"))),
                ("检测方法", _clean(selected.get("method_name"))),
                ("输入值类型", get_input_value_type_label(selected["input_value_type"])),
                ("建靶有效点数", f"{int(selected['target_n'])} 个"),
                ("CV 要求", "-" if pd.isna(selected["cv_limit"]) else f"≤ {float(selected['cv_limit']):.2f}%"),
            ],
            badges=[
                "由即时法转入" if is_from_instant else "已启用",
                "LJ",
                f"批号 {selected['lot_no']}",
            ],
        )

        display_batches = batches.copy()
        display_batches["source_label"] = display_batches["source_method"].map(
            lambda value: "由即时法转入" if str(value or "").strip().lower() == "instant" else "新版配置"
        )
        st.dataframe(
            display_batches[
                [
                    "project_name",
                    "v11_config_name",
                    "lot_no",
                    "expiry_date",
                    "instrument",
                    "reagent",
                    "qc_material",
                    "concentration",
                    "unit_symbol",
                    "method_name",
                    "target_n",
                    "cv_limit",
                    "source_label",
                ]
            ].rename(
                columns={
                    "project_name": "检验项目",
                    "v11_config_name": "配置名称",
                    "lot_no": "质控品批号",
                    "expiry_date": "效期",
                    "instrument": "仪器",
                    "reagent": "试剂",
                    "qc_material": "质控品",
                    "concentration": "水平",
                    "unit_symbol": "单位",
                    "method_name": "检测方法",
                    "target_n": "建靶点数",
                    "cv_limit": "CV要求(%)",
                    "source_label": "来源",
                }
            ),
            hide_index=True,
            width="stretch",
        )
