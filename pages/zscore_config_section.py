from __future__ import annotations

import pandas as pd
import streamlit as st

from pages.lj_config_section import _clean, _ensure_selected_id, _selector_index
from services.value_type_service import get_input_value_type_label
from services.zscore_workbench_service import (
    list_zscore_workbench_batches, list_zscore_workbench_projects, sync_zscore_workbench_bindings,
)
from ui.common import render_section_intro


def prepare_zscore_v12_project_batch_context():
    issues = sync_zscore_workbench_bindings()
    projects = list_zscore_workbench_projects()
    project_id = _ensure_selected_id(projects, "zscore_selected_project_id")
    batches = list_zscore_workbench_batches(project_id) if project_id is not None else pd.DataFrame()
    batch_id = _ensure_selected_id(batches, "zscore_selected_batch_id")
    return projects, project_id, batches, batch_id, issues


def render_zscore_v12_configuration_selection(manage_tab, projects, project_id, batches, batch_id, issues):
    with manage_tab:
        render_section_intro(
            title="Z-score 项目与批次选择",
            caption="请选择已确认设置的项目和批次。每批使用 2 或 3 个水平，可用本批结果建立均值和标准差，也可使用已确认的均值和标准差。",
            badges=["多水平", "均值和标准差"], tone="accent",
        )
        if issues:
            st.warning(f"有 {len(issues)} 项批次设置需要检查，暂不能录入。")
            with st.expander("查看待检查项目"):
                st.dataframe(pd.DataFrame(issues).rename(columns={"config_name": "批次名称", "test_item_name": "检验项目", "lot_no": "质控品批号", "issue": "说明"}), hide_index=True)
        if projects.empty:
            st.info("尚无可用的多水平项目。请先添加检验项目、选择质控品批号并确认批次设置。")
            return

        project_map = {"请选择设置已确认的 Z-score 项目": None}
        for row in projects.to_dict("records"):
            label = f"{row['name']}｜{row['instrument_name']}｜{row['level_count']} 水平｜{get_input_value_type_label(row['input_value_type'])}"
            if label in project_map:
                label += f"｜#{row['id']}"
            project_map[label] = row["id"]
        batch_map = {"请选择设置已确认的批次": None}
        for row in batches.to_dict("records"):
            label = f"质控批号：{row['lot_no']}｜{_clean(row['v11_config_name'])}｜效期 {_clean(row['v11_expiry_date'])}"
            if label in batch_map:
                label += f"｜#{row['id']}"
            batch_map[label] = row["id"]

        project_col, batch_col = st.columns(2)
        with project_col:
            label = st.selectbox("检验项目", list(project_map), index=_selector_index(project_map, project_id), key="v12_zscore_project_selector")
            if project_map[label] != project_id:
                st.session_state["zscore_selected_project_id"] = project_map[label]
                st.session_state["zscore_selected_batch_id"] = None
                st.session_state["v12_zscore_batch_selector"] = "请选择设置已确认的批次"
                st.rerun()
        with batch_col:
            label = st.selectbox("批次", list(batch_map), index=_selector_index(batch_map, batch_id), key="v12_zscore_batch_selector", disabled=project_id is None)
            if batch_map[label] != batch_id:
                st.session_state["zscore_selected_batch_id"] = batch_map[label]
                st.rerun()
        if batch_id is None:
            st.info("请选择批次后进入“当前批次”。")
        elif not batches.empty:
            st.dataframe(batches[["project_name", "v11_config_name", "lot_no", "level_count", "instrument", "reagent", "qc_material", "unit_symbol", "method_name", "target_n"]].rename(columns={
                "project_name": "检验项目", "v11_config_name": "批次名称", "lot_no": "质控品批号", "level_count": "水平数",
                "instrument": "仪器", "reagent": "试剂", "qc_material": "质控品", "unit_symbol": "单位", "method_name": "检测方法", "target_n": "参数建立点数",
            }), hide_index=True, width="stretch")
