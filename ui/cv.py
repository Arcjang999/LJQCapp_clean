from __future__ import annotations

import streamlit as st

from services.cv_service import calculate_cv_percent


CV_REQUIREMENT_HELP = "填写本检验项目的精密度上限；留空表示未设置。该要求用于比较 CV，不代替 Westgard 规则的 SD。"


def render_target_cv(mean, sd, cv_limit=None, *, input_value_type="raw", pending=False):
    cv = calculate_cv_percent(mean, sd)
    st.metric("参考 CV%（待确认）" if pending else "靶值 CV%", "—" if cv is None else f"{cv:.2f}%",
        help="CV% = SD ÷ 靶均值 × 100；靶均值须大于 0。此值用于描述参数的相对离散程度，不代表本次检测的 Westgard 结论。")
    if input_value_type in {"ct", "log"}:
        st.caption("此 CV 按当前输入尺度计算；Ct / log 值不能直接套用浓度值的 CV 要求。")
    if cv_limit is not None and cv is not None:
        message = f"要求 ≤ {cv_limit:g}% · {'满足' if cv <= cv_limit else '超出'}"
        if cv > cv_limit:
            st.warning(message)
        else:
            st.caption(message)


def render_cv_requirement(cv_limit, source_text=""):
    st.caption("CV 要求：未设置" if cv_limit is None else f"CV 要求：≤ {cv_limit:g}%")
    if source_text:
        st.caption(f"要求依据：{source_text}")
