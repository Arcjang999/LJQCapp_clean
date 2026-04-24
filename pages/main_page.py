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


def _open_global_page(page_key: str) -> None:
    st.session_state[page_key] = True
    if page_key == "show_settings_page":
        st.session_state["refresh_settings_form"] = True
    st.rerun()


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


def _render_instruction_block(title: str, items: list[str]) -> None:
    with st.container(border=True):
        st.markdown(f"**{title}**")
        st.markdown("\n".join(f"- {item}" for item in items))


def _render_instruction_module(
    *,
    scene_items: list[str],
    flow_items: list[str],
    statistics_items: list[str],
    judgment_items: list[str],
    note_items: list[str],
) -> None:
    _render_instruction_block("适用场景", scene_items)
    _render_instruction_block("操作流程", flow_items)
    _render_instruction_block("统计说明", statistics_items)
    _render_instruction_block("判定方法", judgment_items)
    _render_instruction_block("注意事项", note_items)


def _render_usage_guide_tabs() -> None:
    tabs = st.tabs(
        [
            LJ_ENTRY_LABEL,
            ZSCORE_ENTRY_LABEL,
            INSTANT_ENTRY_LABEL,
            "报告历史",
            "系统设置",
        ]
    )

    with tabs[0]:
        _render_instruction_module(
            scene_items=[
                "适用于单水平项目的日常室内质控。",
                "同一页面内完成建靶、正式质控、记录维护与月报生成。",
            ],
            flow_items=[
                "新建项目 → 新建批次 → 建靶 → 正式质控 → 记录维护 → 月报。",
                "进入“当前批次”后，可连续完成录入、图表查看、最新分析和维护。",
            ],
            statistics_items=[
                "建靶统计仅基于当前批次建靶期有效点。",
                "建靶禁用点不参与建靶统计。",
                "正式期实时统计仅基于正式期中在控数据。",
                "月报统计仅基于所选月份正式期数据。",
            ],
            judgment_items=[
                "建靶期显示离群值判断。",
                "正式期按 Westgard 规则判定。",
            ],
            note_items=[
                "录入第一条正式期数据后，建靶期数据锁定。",
                "离群点只做保留、禁用、恢复，不删除原始记录。",
            ],
        )

    with tabs[1]:
        _render_instruction_module(
            scene_items=[
                "适用于 2 水平或 3 水平项目的联合判断场景。",
                "适合需要同时查看各水平摘要、图表和本次检测结论的多水平质控流程。",
            ],
            flow_items=[
                "新建项目并确定水平数 → 新建批次 → 多水平建靶 → 全部水平满足条件后进入正式期 → 图表分析 → 月报。",
                "在“当前批次”页可切换单水平视图或合并视图，并查看建靶维护与历史记录。",
            ],
            statistics_items=[
                "建靶统计按各水平分别累计。",
                "本次检测维护按整次检测处理。",
                "月报统计仅基于所选月份正式期检测记录。",
            ],
            judgment_items=[
                "建靶期重点查看离群提示和各水平情况。",
                "正式期按当前规则给出本次检测结论。",
            ],
            note_items=[
                "全部水平达到建靶条件后才进入正式期。",
                "进入正式期后建靶记录锁定，仅保留查看。",
            ],
        )

    with tabs[2]:
        _render_instruction_module(
            scene_items=[
                "适用于样本量较少、短期内不易快速累积 20 个点的单水平项目。",
                "作为过渡方法使用，满足条件后由人工确认转入 LJ 法。",
            ],
            flow_items=[
                "新建项目 → 新建批次 → 逐步录入 → 即刻法 SI 判定 → 处理疑似离群点 → 达到 20 个有效点后确认转入 LJ 法。",
                "转入后继续到对应 LJ 批次完成后续建靶和正式质控。",
            ],
            statistics_items=[
                "统计仅基于有效点。",
                "禁用点不参与均值、SD、CV。",
            ],
            judgment_items=[
                "采用即刻法 SI 值判定。",
                "n < 3 不判定，3 <= n <= 20 按 SI 表提示在控、警告或疑似离群。",
            ],
            note_items=[
                "确认转入 LJ 法后源批次冻结为只读。",
                "前 20 个有效点进入 LJ 建靶，后续有效点进入 LJ 正式期。",
            ],
        )

    with tabs[3]:
        _render_instruction_module(
            scene_items=[
                "适用于查看已生成的 LJ 与 Z-score 月报记录。",
                "在统一页面中按项目名称定位报告，再用方法学、批次、月份和生成时间辅助识别。",
            ],
            flow_items=[
                "打开报告历史 → 按项目名称、方法学、批次或月份筛选 → 查看摘要 → 按当前数据重新生成。",
                "需要重新下载 PDF 时，先执行重新生成，再下载新生成的文件。",
            ],
            statistics_items=[
                "历史页展示的是快照摘要。",
                "重新生成使用当前数据库中的数据，不是下载旧 PDF 原件。",
            ],
            judgment_items=[
                "本页不重新执行历史判定规则，摘要区展示生成报告时保存的关键统计和结论摘要。",
                "选择“按当前数据重新生成”后，会调用对应月报生成逻辑并按当前数据重新计算。",
            ],
            note_items=[
                "可先按项目名称定位，再结合方法学和时间确认报告。",
                "若原始项目或批次不存在，无法重新生成。",
            ],
        )

    with tabs[4]:
        _render_instruction_module(
            scene_items=[
                "系统设置属于全局功能，不属于某一种方法学。",
                "用于统一维护报告默认信息，以及数据存储、迁移、备份和恢复入口。",
            ],
            flow_items=[
                "填写实验室默认信息 → 保存设置。",
                "查看当前数据库位置 → 迁移数据库 / 立即备份 / 从备份恢复。",
            ],
            statistics_items=[
                "本页不参与质控统计，也不影响报告统计结果。",
                "保存后的默认信息会用于后续新生成的月报。",
            ],
            judgment_items=[
                "本页不执行质控判定，相关操作主要进行路径可写性或 SQLite 有效性校验。",
                "数据库迁移和恢复都会先做必要校验，再执行对应操作。",
            ],
            note_items=[
                "数据库迁移和恢复成功后需重启应用生效。",
                "迁移和恢复均通过系统原生目录或文件选择窗口完成，不需手输路径。",
            ],
        )


def render_main_entry_page() -> None:
    hero_html = dedent(
        """
        <div class="home-hero">
            <div class="home-hero-title">实验室室内质控工作台</div>
            <div class="home-hero-caption">
                面向日常单机使用场景，统一提供单水平（LJ法）、多水平（Z-score法）、即时法、
                月报生成、报告历史与系统设置入口。
            </div>
            <div class="welcome-chip-row">
                <span class="welcome-chip">单水平（LJ法）</span>
                <span class="welcome-chip">多水平（Z-score法）</span>
                <span class="welcome-chip">即时法</span>
                <span class="welcome-chip">月报、报告历史与系统设置</span>
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
        if st.button("进入多水平（Z-score法）", key="hero_jump_zscore", type="primary", width="stretch"):
            switch_top_level_method(ZSCORE_ENTRY_LABEL)
    with action_col3:
        if st.button("进入即时法", key="hero_jump_instant", type="primary", width="stretch"):
            switch_top_level_method(INSTANT_ENTRY_LABEL)

    st.caption("右上角保留报告历史和系统设置等全局入口，当前页面主要用于方法选择和使用说明查看。")

    render_section_intro(
        title="方法入口",
        caption="按方法学进入对应工作台，并查看对应的主要使用场景。",
        badges=[LJ_ENTRY_LABEL, ZSCORE_ENTRY_LABEL, INSTANT_ENTRY_LABEL],
        tone="accent",
    )
    method_col1, method_col2, method_col3 = st.columns(3, gap="large")

    with method_col1:
        _render_method_card(
            eyebrow="单水平",
            title="LJ 法",
            caption="适用于单水平日常室内质控，页面重点突出建靶、Westgard 判读和月度回顾。",
            bullet_points=[
                "建靶期重点查看离群值判断，正式期聚焦 Westgard 规则。",
                "录入、统计、图表和最新分析集中在同一工作台。",
                "记录维护、导入导出和月报入口统一放在下部区域。",
            ],
            tags=["单水平", "Westgard", "月报"],
        )
        if st.button("打开单水平（LJ法）", key="open_main_lj_card", width="stretch"):
            switch_top_level_method(LJ_ENTRY_LABEL)

    with method_col2:
        _render_method_card(
            eyebrow="多水平",
            title="Z-score 法",
            caption="适用于 2 水平或 3 水平联合判断，重点查看各水平摘要、图表和本次检测结论。",
            bullet_points=[
                "支持单水平视图与合并视图切换。",
                "建靶统计按各水平分别累计，维护按本次检测处理。",
                "图表、各水平摘要、维护区和月报入口分区清晰。",
            ],
            tags=["多水平", "2 水平 / 3 水平", "联合判断"],
        )
        if st.button("打开多水平（Z-score法）", key="open_main_zscore_card", width="stretch"):
            switch_top_level_method(ZSCORE_ENTRY_LABEL)

    with method_col3:
        _render_method_card(
            eyebrow="过渡方法",
            title="即时法",
            caption="适用于短期内难以快速累积 20 个点的单水平项目，满足条件后可人工确认转入 LJ 法。",
            bullet_points=[
                "3 个有效点后开始即刻法 SI 值提示。",
                "累计到 20 个有效点后可确认转入 LJ 法。",
                "转入后源批次冻结为只读，去向 LJ 项目和批次可追溯。",
            ],
            tags=["单水平", "即刻法 SI", "转入 LJ"],
        )
        if st.button("打开即时法", key="open_main_instant_card", width="stretch"):
            switch_top_level_method(INSTANT_ENTRY_LABEL)

    render_section_intro(
        title="全局入口",
        caption="报告历史和系统设置属于全局功能，不进入某一种方法学页面内部；需要查看报告记录或维护默认配置时，可从这里或右上角入口进入。",
        badges=["报告历史", "系统设置"],
        tone="muted",
    )
    global_col1, global_col2 = st.columns(2, gap="large")
    with global_col1:
        _render_method_card(
            eyebrow="全局功能",
            title="报告历史",
            caption="统一查看 LJ 与 Z-score 月报记录，支持查看摘要和按当前数据重新生成。",
            bullet_points=[
                "支持项目名称、方法学、批次和月份筛选。",
                "摘要区可帮助快速确认是否为目标报告。",
                "重新生成得到的是按当前数据生成的新 PDF。",
            ],
            tags=["历史记录中心", "摘要查看", "重新生成"],
        )
        if st.button("打开报告历史", key="open_main_report_history_card", width="stretch"):
            _open_global_page("show_report_history_page")

    with global_col2:
        _render_method_card(
            eyebrow="全局功能",
            title="系统设置",
            caption="集中维护报告默认信息，以及数据库迁移、备份和恢复入口。",
            bullet_points=[
                "可维护实验室名称、科室名称、质控负责人、审核人和固定声明。",
                "迁移数据库、立即备份和从备份恢复都在这里完成。",
                "迁移和恢复成功后需按提示重启应用。",
            ],
            tags=["报告默认信息", "数据存储", "备份恢复"],
        )
        if st.button("打开系统设置", key="open_main_settings_card", width="stretch"):
            _open_global_page("show_settings_page")

    render_section_intro(
        title="使用说明",
        caption="按方法学和全局功能查看主要流程、统计说明和注意事项。",
        badges=["适用场景", "统计说明", "判定方法", "注意事项"],
        tone="default",
    )
    _render_usage_guide_tabs()


def render_instant_placeholder_page() -> None:
    render_section_intro(
        title="即时法",
        caption="即时法已经纳入顶部主导航，请直接从“即时法”入口进入正式工作台。",
        badges=["即时法", "过渡方法", "确认转入 LJ 法"],
        tone="accent",
    )
    st.info("请从顶部“即时法”入口进入正式页面；此占位页仅保留兼容说明。")
