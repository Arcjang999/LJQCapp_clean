from __future__ import annotations

from textwrap import dedent

import streamlit as st

from ui.common import render_html_block, render_section_intro


MAIN_ENTRY_LABEL = "主页"
LJ_ENTRY_LABEL = "单水平（LJ法）"
ZSCORE_ENTRY_LABEL = "多水平（Z-score法）"
INSTANT_ENTRY_LABEL = "即时法"

METHOD_ENTRY_OPTIONS = [
    MAIN_ENTRY_LABEL,
    LJ_ENTRY_LABEL,
    ZSCORE_ENTRY_LABEL,
    INSTANT_ENTRY_LABEL,
]

LEGACY_METHOD_ENTRY_MAP = {
    "首页": MAIN_ENTRY_LABEL,
    "主页": MAIN_ENTRY_LABEL,
    "Main": MAIN_ENTRY_LABEL,
    "LJ": LJ_ENTRY_LABEL,
    "单水平（LJ法）": LJ_ENTRY_LABEL,
    "Z-score": ZSCORE_ENTRY_LABEL,
    "多水平（Z-score法）": ZSCORE_ENTRY_LABEL,
    "Instant": INSTANT_ENTRY_LABEL,
    "即时法": INSTANT_ENTRY_LABEL,
    "涓婚〉": MAIN_ENTRY_LABEL,
    "鍗曟按骞筹紙LJ娉曪級": LJ_ENTRY_LABEL,
    "澶氭按骞筹紙Z-score娉曪級": ZSCORE_ENTRY_LABEL,
    "鍗虫椂娉?": INSTANT_ENTRY_LABEL,
}


def switch_top_level_method(target_method: str) -> None:
    normalized_target = LEGACY_METHOD_ENTRY_MAP.get(str(target_method or "").strip(), str(target_method or "").strip())
    if normalized_target in METHOD_ENTRY_OPTIONS:
        st.session_state["pending_top_level_method"] = normalized_target
    st.rerun()


def normalize_top_level_method_selection() -> None:
    pending_value = str(st.session_state.pop("pending_top_level_method", "") or "").strip()
    current_value = str(st.session_state.get("top_level_method_selector", "") or "").strip()
    candidate_value = pending_value or current_value
    normalized_value = LEGACY_METHOD_ENTRY_MAP.get(candidate_value, candidate_value)

    if normalized_value in METHOD_ENTRY_OPTIONS:
        st.session_state["top_level_method_selector"] = normalized_value
        return

    st.session_state["top_level_method_selector"] = METHOD_ENTRY_OPTIONS[0]


def _render_method_card(
    *,
    eyebrow: str,
    title: str,
    caption: str,
    bullet_points: list[str],
    tags: list[str],
) -> None:
    html = dedent(
        f"""
        <div class="main-entry-card">
            <div class="main-entry-card-eyebrow">{eyebrow}</div>
            <div class="main-entry-card-title">{title}</div>
            <div class="main-entry-card-caption">{caption}</div>
            <ul class="main-entry-card-list">
                {''.join(f"<li>{item}</li>" for item in bullet_points)}
            </ul>
            <div class="main-entry-card-tags">
                {''.join(f'<span class="main-entry-card-tag">{tag}</span>' for tag in tags)}
            </div>
        </div>
        """
    ).strip()
    render_html_block(html)


def _render_muted_info_card(*, title: str, caption: str, items: list[str]) -> None:
    html = dedent(
        f"""
        <div class="main-entry-card main-entry-card-muted">
            <div class="main-entry-card-title" style="font-size:18px; margin-top:0;">{title}</div>
            <div class="main-entry-card-caption">{caption}</div>
            <ul class="main-entry-card-list">
                {''.join(f"<li>{item}</li>" for item in items)}
            </ul>
        </div>
        """
    ).strip()
    render_html_block(html)


def render_main_entry_page() -> None:
    hero_html = dedent(
        """
        <div class="home-hero">
            <div class="home-hero-title">实验室室内质控工作台</div>
            <div class="home-hero-caption">
                面向单机版日常使用场景，统一承载单水平（LJ法）、多水平（Z-score法）与即时法流程。
                当前版本重点提供项目与批次管理、检测录入、图表判读、月报生成、报告历史与系统设置。
            </div>
            <div class="welcome-chip-row">
                <span class="welcome-chip">轻量医疗工作台</span>
                <span class="welcome-chip">固定模板月报</span>
                <span class="welcome-chip">本地 SQLite 数据</span>
                <span class="welcome-chip">报告历史与设置全局入口</span>
            </div>
        </div>
        """
    ).strip()
    render_html_block(hero_html)

    action_col1, action_col2, action_col3 = st.columns(3, gap="medium")
    with action_col1:
        if st.button("进入单水平（LJ法）", key="hero_jump_lj", type="primary", width="stretch"):
            switch_top_level_method(LJ_ENTRY_LABEL)
    with action_col2:
        if st.button("进入即时法", key="hero_jump_instant", type="primary", width="stretch"):
            switch_top_level_method(INSTANT_ENTRY_LABEL)
    with action_col3:
        if st.button("进入多水平（Z-score法）", key="hero_jump_zscore", type="primary", width="stretch"):
            switch_top_level_method(ZSCORE_ENTRY_LABEL)

    st.caption("右上角继续保留全局入口，用于打开报告历史与系统设置。")

    render_section_intro(
        title="三种方法工作台",
        caption="主页只负责快速进入不同方法学流程，不再堆叠说明文字；每张卡片强调方法定位、适用场景和核心动作。",
        badges=["主页", "统一入口", "方法学差异清晰"],
        tone="accent",
    )
    method_col1, method_col2, method_col3 = st.columns(3, gap="large")

    with method_col1:
        _render_method_card(
            eyebrow="单水平",
            title="LJ 法",
            caption="适用于单水平日常室内质控，强调建靶完成后的 Westgard 判读与月度回顾。",
            bullet_points=[
                "本次录入、当前统计、图表与最新分析集中在同一工作台。",
                "建靶期关注离群值判断，正式期聚焦 Westgard 规则。",
                "下部保留记录维护、导入导出和月报入口。",
            ],
            tags=["单水平", "Westgard", "月报"],
        )
        if st.button("打开 LJ 工作台", key="open_main_lj_card", width="stretch"):
            switch_top_level_method(LJ_ENTRY_LABEL)

    with method_col2:
        _render_method_card(
            eyebrow="过渡方法",
            title="即时法",
            caption="适用于短期内难以快速积累 20 个点的单水平项目，提供基础离群提示与转入 LJ 流程。",
            bullet_points=[
                "3 个有效点后开始格拉布斯法提示。",
                "累计到 20 个有效点后可人工确认转入 LJ 法。",
                "转入后保留追溯关系，并冻结即时法源批次。",
            ],
            tags=["单水平", "格拉布斯法", "转入 LJ"],
        )
        if st.button("打开即时法工作台", key="open_main_instant_card", width="stretch"):
            switch_top_level_method(INSTANT_ENTRY_LABEL)

    with method_col3:
        _render_method_card(
            eyebrow="多水平",
            title="Z-score 法",
            caption="适用于 2 水平或 3 水平联合判断场景，强调多水平结构、图表切换与 level 维护。",
            bullet_points=[
                "顶部上下文条显示当前阶段、水平数、模板和输入值类型。",
                "主区支持单水平视图与合并视图切换。",
                "下部保留 level 摘要、维护区、导入导出和月报入口。",
            ],
            tags=["多水平", "联合判断", "Level 管理"],
        )
        if st.button("打开 Z-score 工作台", key="open_main_zscore_card", width="stretch"):
            switch_top_level_method(ZSCORE_ENTRY_LABEL)

    render_section_intro(
        title="快速开始",
        caption="保持主页轻量，但仍提供最常用的操作路径，方便首次上手和重新进入时快速定位。",
        badges=["项目创建", "检测录入", "报告与历史"],
        tone="muted",
    )
    quick_col1, quick_col2, quick_col3 = st.columns(3, gap="large")
    with quick_col1:
        _render_muted_info_card(
            title="开始一个新项目",
            caption="先按方法学选择合适工作台，再创建项目与批次。",
            items=[
                "创建项目时确定输入值类型。",
                "按项目进入批次管理并完成批号、仪器、试剂等信息填写。",
                "完成后切到“当前批次”开始录入。",
            ],
        )
    with quick_col2:
        _render_muted_info_card(
            title="生成与回看报告",
            caption="LJ 与 Z-score 月报入口保留在各自工作台中，历史回看统一走全局入口。",
            items=[
                "在方法学页面生成固定模板月报 PDF。",
                "右上角“报告历史”统一查看 LJ 与 Z-score 报告记录。",
                "历史记录支持查看摘要并按同参数重新生成。",
            ],
        )
    with quick_col3:
        _render_muted_info_card(
            title="维护默认信息",
            caption="系统设置负责统一维护报告默认信息与数据存储入口。",
            items=[
                "实验室信息和报告声明影响后续新生成的月报。",
                "数据库迁移、备份和恢复入口全部收口到系统设置。",
                "危险操作在设置页内单独区分，不进入主导航。",
            ],
        )

    render_section_intro(
        title="说明入口",
        caption="把长说明收纳到折叠区，首页只保留高频信息和最近更新摘要，减少纯文字堆叠感。",
        badges=["最近更新", "使用边界", "后续入口"],
        tone="default",
    )
    info_col1, info_col2 = st.columns([0.9, 1.1], gap="large")
    with info_col1:
        _render_muted_info_card(
            title="最近更新",
            caption="当前版本已经完成主流程闭环，并进入发布前 UI/UX 收口阶段。",
            items=[
                "LJ、Z-score 月报已接入固定模板输出。",
                "报告历史已收口为全局统一页面。",
                "系统设置已包含实验室信息、数据库迁移、备份与恢复。",
            ],
        )
    with info_col2:
        with st.expander("查看使用说明与当前版本边界", expanded=False):
            st.markdown(
                "\n".join(
                    [
                        "- LJ 页面面向单水平常规质控，建靶期与正式期分析入口已经分离。",
                        "- Z-score 页面面向 2 水平或 3 水平场景，支持单水平视图和合并视图。",
                        "- 即时法用于过渡阶段，满足条件后由用户确认转入 LJ 法，而不是自动转入。",
                        "- 报告历史只做统一历史记录、摘要查看和按当前数据重新生成，不做旧 PDF 归档中心。",
                        "- 系统设置属于全局功能，不进入方法学导航。数据库迁移仍要求通过目录选择完成。",
                    ]
                )
            )


def render_instant_placeholder_page() -> None:
    render_section_intro(
        title="即时法",
        caption="即时法入口已经升级为正式工作台，请从顶部主导航直接进入。",
        badges=["过渡方法", "单水平", "确认转入 LJ"],
        tone="accent",
    )
    st.info("请从顶部“即时法”入口进入正式页面；此占位页仅保留兼容说明。")
