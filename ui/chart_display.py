"""Keep the workbench chart readable without changing nested form layouts."""
from __future__ import annotations

import streamlit as st


_WORKBENCH_KEYS = {
    "lj": "lj_chart_workbench",
    "zscore": "zscore_chart_workbench",
    "instant": "instant_chart_workbench",
}

_WORKBENCH_LAYOUT_CSS = """
<style>
@media (max-width: 1200px) {
    :is(.st-key-lj_chart_workbench, .st-key-zscore_chart_workbench,
        .st-key-instant_chart_workbench) > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] {
        flex-direction: column;
        gap: 1.5rem;
    }
    :is(.st-key-lj_chart_workbench, .st-key-zscore_chart_workbench,
        .st-key-instant_chart_workbench) > [data-testid="stLayoutWrapper"] > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        flex: 1 1 100% !important;
        width: 100% !important;
        min-width: 0 !important;
    }
}
</style>
"""


def workbench_chart_columns(method: str):
    """Return entry/chart columns; the keyed outer pair alone stacks on small screens."""
    key = _WORKBENCH_KEYS[method]
    st.markdown(_WORKBENCH_LAYOUT_CSS, unsafe_allow_html=True)
    # Streamlit exposes container keys as st-key-* CSS classes. Direct-child
    # selectors pass through the layout wrapper used by Streamlit 1.63 and
    # keep controls, metrics and entry form columns unchanged.
    with st.container(key=key):
        return st.columns([1.0, 1.7], gap="large")
