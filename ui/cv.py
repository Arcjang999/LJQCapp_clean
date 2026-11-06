from __future__ import annotations

import streamlit as st

from services.cv_service import calculate_cv_percent


CV_REQUIREMENT_HELP = "填写CV上限（%），留空表示未设置。"


def render_target_cv(mean, sd, cv_limit=None, *, input_value_type="raw", pending=False):
    cv = calculate_cv_percent(mean, sd)
    st.metric("参考变异系数（%）（待确认）" if pending else "设定变异系数（%）", "—" if cv is None else f"{cv:.2f}%",
        help="CV% = SD ÷ 设定均值 × 100，均值须大于0。")
    if input_value_type in {"ct", "log"}:
        st.caption("Ct、log结果按本批次确认的均值、SD和质控规则核对。")
    if cv_limit is not None and cv is not None:
        message = f"要求 ≤ {cv_limit:g}% · {'满足' if cv <= cv_limit else '超出'}"
        if cv > cv_limit:
            st.warning(message)
        else:
            st.caption(message)


def render_cv_requirement(cv_limit, source_text="", quality_goal=None):
    if quality_goal:
        from services.quality_target_service import decode,requirement_text
        goal=decode(quality_goal)
        if goal:
            if goal.get('pending'):st.caption('请在批次设置中确认每个水平的质量要求。')
            else:
                for entry in goal['levels']:
                    st.caption(f"水平 {entry['level_order']} 允许不精密度：{requirement_text(entry['rule'])}")
            if source_text:st.caption(f"要求依据：{source_text}")
            return
    st.caption("允许不精密度（CV）：未设置" if cv_limit is None else f"允许不精密度（CV）：≤ {cv_limit:g}%")
    if source_text:
        st.caption(f"要求依据：{source_text}")
