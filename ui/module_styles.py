"""Shared presentation colors; clinical result colors remain independent."""
from __future__ import annotations

from html import escape

import streamlit as st


MODULE_PALETTES = {
    "projects": {"label": "项目管理", "accent": "#2459A9", "soft": "#F0F5FE", "border": "#C7D8F2"},
    "daily": {"label": "日常质控", "accent": "#0F6B58", "soft": "#EDF8F3", "border": "#B9DFD1"},
    "handling": {"label": "异常处理", "accent": "#A34C14", "soft": "#FFF5EC", "border": "#EDCCAF"},
    "reports": {"label": "报告与回顾", "accent": "#6945A5", "soft": "#F5F0FB", "border": "#D8C9EA"},
    "materials": {"label": "资料管理", "accent": "#17677E", "soft": "#EEF7FA", "border": "#BCDCE6"},
    "settings": {"label": "系统设置", "accent": "#526174", "soft": "#F2F4F7", "border": "#D3DAE3"},
    "guide": {"label": "使用说明", "accent": "#4E627D", "soft": "#F1F5FA", "border": "#CEDAE8"},
}


def _tone(value: str) -> str:
    return value if value in MODULE_PALETTES else "projects"


def module_styles(tone: str = "projects") -> str:
    palette = MODULE_PALETTES[_tone(tone)]
    variables = "\n".join(
        f".module-tone-{name} {{ --module-accent:{colors['accent']}; --module-soft:{colors['soft']}; --module-border:{colors['border']}; }}"
        for name, colors in MODULE_PALETTES.items()
    )
    css = f"""<style>
    :root {{ --module-accent:{palette['accent']}; --module-soft:{palette['soft']}; --module-border:{palette['border']}; }}
    {variables}
    .stApp, [data-testid="stAppViewContainer"] {{ background:#F5F7FA; color:#203247; }}
    [data-testid="stHeader"] {{ background:rgba(245,247,250,.96); }}
    [data-testid="stMainBlockContainer"] {{ padding-top:4rem; padding-bottom:2.4rem; }}
    .st-key-module_topbar {{
        border:1px solid #DCE3EB; border-radius:16px; background:#FFF;
        padding:15px 18px; box-shadow:0 2px 8px rgba(27,45,67,.04);
    }}
    .app-wordmark {{ display:flex; align-items:center; gap:12px; min-height:48px; }}
    .app-wordmark-mark {{
        display:flex; align-items:center; justify-content:center; flex:0 0 42px;
        width:42px; height:42px; border-radius:12px; background:#2459A9;
        color:#FFF; font-size:15px; font-weight:750; letter-spacing:1px;
    }}
    .app-wordmark-title {{ font-size:clamp(17px,1.55vw,22px); font-weight:750; line-height:1.35; color:#18324D; overflow-wrap:anywhere; }}
    .app-wordmark-caption {{ margin-top:3px; font-size:12px; line-height:1.45; color:#586A7C; }}
    .module-header {{
        border:1px solid var(--module-border); border-left:5px solid var(--module-accent);
        border-radius:14px; padding:21px 24px; margin:4px 0 18px; background:var(--module-soft);
    }}
    .module-eyebrow {{
        display:inline-flex; align-items:center; border-radius:6px; padding:4px 9px;
        margin-bottom:10px; background:var(--module-soft); color:var(--module-accent);
        font-size:12px; font-weight:700; line-height:1.5;
    }}
    .module-header .module-heading {{ margin:0; padding:0; font-size:clamp(23px,2.2vw,29px); font-weight:750; line-height:1.35; color:var(--module-accent); overflow-wrap:anywhere; }}
    .module-caption {{ margin:9px 0 0; color:#475C71; font-size:14px; line-height:1.65; white-space:pre-line; overflow-wrap:anywhere; }}
    .module-card {{
        border:1px solid var(--module-border); border-top:4px solid var(--module-accent);
        border-radius:13px; padding:18px 19px; background:var(--module-soft);
        min-height:140px; margin:0 0 8px;
    }}
    .module-card .module-card-label {{ color:var(--module-accent); font-size:11px; font-weight:700; margin-bottom:9px; line-height:1.5; }}
    .module-card .module-card-heading {{ margin:0; padding:0; font-size:20px; font-weight:750; color:#1D334D; line-height:1.4; overflow-wrap:anywhere; }}
    .module-card .module-caption {{ font-size:13px; margin-top:7px; }}
    .st-key-home_module_daily, .st-key-project_daily_actions {{ --module-accent:#0F6B58; --module-soft:#EDF8F3; --module-border:#B9DFD1; }}
    .st-key-home_module_handling {{ --module-accent:#A34C14; --module-soft:#FFF5EC; --module-border:#EDCCAF; }}
    .st-key-home_module_reports {{ --module-accent:#6945A5; --module-soft:#F5F0FB; --module-border:#D8C9EA; }}
    .st-key-home_module_daily, .st-key-home_module_handling, .st-key-home_module_reports, .st-key-project_daily_actions {{
        background:var(--module-soft); border:1px solid var(--module-border);
        border-left:5px solid var(--module-accent); border-radius:14px; padding:16px 18px;
    }}
    .st-key-home_module_daily .module-card, .st-key-home_module_handling .module-card,
    .st-key-home_module_reports .module-card {{ background:transparent; border:0; border-radius:0; padding:0 0 6px; margin:0; min-height:132px; }}
    .st-key-home_module_daily div.stButton > button[kind="secondary"],
    .st-key-home_module_handling div.stButton > button[kind="secondary"],
    .st-key-home_module_reports div.stButton > button[kind="secondary"],
    .st-key-project_daily_actions div.stButton > button[kind="secondary"] {{ border-color:var(--module-accent); color:var(--module-accent); background:#FFF; }}
    div[data-testid="stVerticalBlockBorderWrapper"] {{ border-radius:13px !important; box-shadow:0 2px 7px rgba(25,45,65,.035); }}
    div[data-testid="stExpander"] {{ border-radius:12px; box-shadow:none; background:#FFF; }}
    div[data-testid="stExpander"] details summary {{ background:#F7F9FC; }}
    div[data-testid="stDataFrame"] {{ border-radius:11px; box-shadow:none; }}
    .home-hero, .workbench-context-shell, .section-shell.section-shell-accent {{
        background:var(--module-soft); border-color:var(--module-border); box-shadow:none;
    }}
    .section-shell, .main-entry-card, .level-summary-card {{ background:#FFF; box-shadow:none; }}
    .section-shell.section-shell-muted, .main-entry-card.main-entry-card-muted {{ background:#F5F7FA; }}
    .workbench-context-shell, .section-shell {{ border-left:4px solid var(--module-accent); border-radius:13px; }}
    .section-eyebrow, .main-entry-card-eyebrow {{ background:var(--module-soft); color:var(--module-accent); }}
    div.stButton > button, div.stDownloadButton > button, div.stFormSubmitButton > button,
    div[data-testid="stFormSubmitButton"] > button {{ border-radius:9px; box-shadow:none; }}
    div.stButton > button[kind="primary"],
    div.stFormSubmitButton > button[kind="primary"],
    div[data-testid="stFormSubmitButton"] > button[kind="primary"],
    div.stFormSubmitButton > button[kind="primaryFormSubmit"],
    div[data-testid="stFormSubmitButton"] > button[kind="primaryFormSubmit"],
    div[data-testid="stFormSubmitButton"] > button[data-testid="stBaseButton-primaryFormSubmit"] {{
        background:var(--module-accent); border-color:var(--module-accent); color:#FFF;
    }}
    div.stButton > button[kind="primary"]:hover,
    div[data-testid="stFormSubmitButton"] > button[kind="primaryFormSubmit"]:hover {{
        background:var(--module-accent); border-color:var(--module-accent); color:#FFF; filter:brightness(.91);
    }}
    div.stButton > button:focus-visible, div.stDownloadButton > button:focus-visible,
    div[data-testid="stPopover"] > button:focus-visible {{ outline:3px solid var(--module-border); outline-offset:2px; }}
    div[data-baseweb="tab-list"] {{ border-radius:11px; background:#EAF0F5; padding:5px; }}
    button[data-baseweb="tab"] {{ border-radius:7px; }}
    button[data-baseweb="tab"][aria-selected="true"] {{ color:var(--module-accent); box-shadow:none; border-bottom:3px solid var(--module-accent); }}
    [data-testid="stMultiSelect"] [data-tag] {{
        background:var(--module-soft) !important; color:var(--module-accent) !important;
        border:1px solid var(--module-border); border-radius:6px;
    }}
    [data-testid="stMultiSelect"] [data-tag] button {{ color:inherit !important; background:transparent !important; }}
    [data-testid="stMultiSelect"] [data-tag] svg {{ color:inherit; }}
    .st-key-module_nav_materials button, .st-key-open_master_data_page button {{ background:#EEF7FA !important; border-color:#BCDCE6 !important; color:#17677E !important; }}
    .st-key-open_project_management_page button, .st-key-open_quality_targets_page button {{ background:#F0F5FE !important; border-color:#C7D8F2 !important; color:#2459A9 !important; }}
    .st-key-open_out_of_control_page button {{ background:#FFF5EC !important; border-color:#EDCCAF !important; color:#A34C14 !important; }}
    .st-key-open_report_history_page button {{ background:#F5F0FB !important; border-color:#D8C9EA !important; color:#6945A5 !important; }}
    .st-key-open_system_settings button, .st-key-module_nav_help button {{ background:#F2F4F7 !important; border-color:#D3DAE3 !important; color:#526174 !important; }}
    .st-key-module_navigation button {{ font-size:13px; font-weight:650; white-space:normal; min-height:40px; }}
    @media(max-width:900px) {{
        .st-key-module_topbar > [data-testid="stHorizontalBlock"] {{ flex-wrap:wrap; }}
        .st-key-module_topbar > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{ flex:1 1 100% !important; width:100% !important; }}
        .st-key-module_navigation [data-testid="stHorizontalBlock"] {{ flex-wrap:wrap; gap:8px; }}
        .st-key-module_navigation [data-testid="stColumn"] {{ flex:1 1 110px !important; width:auto !important; min-width:0; }}
    }}
    @media(max-width:640px) {{
        [data-testid="stMainBlockContainer"] {{ padding-left:1rem; padding-right:1rem; }}
        .st-key-module_topbar {{ padding:12px; }}
        .module-header {{ padding:17px; }}
        .module-card {{ min-height:0; padding:16px; }}
        .module-caption {{ font-size:13px; }}
    }}
    </style>"""
    # Keep the entire style element one HTML block. Markdown otherwise treats
    # indented lines after the unindented palette rules as a visible code block.
    return "".join(line.strip() for line in css.splitlines())


def render_module_header(title: str, caption: str = "", tone: str = "projects", eyebrow: str = "") -> None:
    """Render one page heading, after the shared global styles are injected."""
    tone = _tone(tone)
    badge = f'<div class="module-eyebrow">{escape(str(eyebrow))}</div>' if eyebrow else ""
    description = f'<p class="module-caption">{escape(str(caption))}</p>' if caption else ""
    st.markdown(
        f'<header class="module-header module-tone-{tone}">{badge}'
        f'<h1 class="module-heading">{escape(str(title))}</h1>{description}</header>',
        unsafe_allow_html=True,
    )


def render_module_card(title: str, caption: str, tone: str = "projects") -> None:
    """Render a module card; callers retain their existing navigation buttons."""
    tone = _tone(tone)
    label = MODULE_PALETTES[tone]["label"]
    st.markdown(
        f'<section class="module-card module-tone-{tone}"><div class="module-card-label">{label}</div>'
        f'<h2 class="module-card-heading">{escape(str(title))}</h2>'
        f'<p class="module-caption">{escape(str(caption))}</p></section>',
        unsafe_allow_html=True,
    )
