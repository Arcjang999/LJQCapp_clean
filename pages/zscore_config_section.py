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
            caption="请选择已启用的 2 或 3 水平配置。可按本批建靶，或在靶值版本中确认全部水平的控制参数。",
            badges=["多水平", "参数版本"], tone="accent",
        )
        if issues:
            st.warning(f"有 {len(issues)} 个配置尚不能进入工作台。")
            with st.expander("查看需要完善的配置"):
                st.dataframe(pd.DataFrame(issues).rename(columns={"config_name": "批次名称", "test_item_name": "检验项目", "lot_no": "质控品批号", "issue": "说明"}), hide_index=True)
        if projects.empty:
            st.info("当前没有可用的多水平配置。请先在项目/批次管理中建立并启用项目与批次。")
            return

        project_map = {"请选择已启用的 Z-score 项目": None}
        for row in projects.to_dict("records"):
            label = f"{row['name']}｜{row['instrument_name']}｜{row['level_count']} 水平｜{get_input_value_type_label(row['input_value_type'])}"
            if label in project_map:
                label += f"｜#{row['id']}"
            project_map[label] = row["id"]
        batch_map = {"请选择已启用的批次": None}
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
                st.session_state["v12_zscore_batch_selector"] = "请选择已启用的批次"
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
                "instrument": "仪器", "reagent": "试剂", "qc_material": "质控品", "unit_symbol": "单位", "method_name": "检测方法", "target_n": "建靶次数",
            }), hide_index=True, width="stretch")
