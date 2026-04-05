from __future__ import annotations

from html import escape as html_escape
from textwrap import dedent

import streamlit as st

from ui.common import render_html_block


METHOD_ENTRY_OPTIONS = [
    "主页",
    "单水平（LJ法）",
    "多水平（Z-score法）",
    "即刻法",
]

LEGACY_METHOD_ENTRY_MAP = {
    "首页": "主页",
    "Main": "主页",
    "LJ": "单水平（LJ法）",
    "Z-score": "多水平（Z-score法）",
    "Instant": "即刻法",
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


def render_main_entry_page() -> None:
    def render_entry_card(
        title: str,
        caption: str,
        tags: list[str],
        *,
        eyebrow: str,
        muted: bool = False,
    ) -> None:
        tags_html = "".join(
            f'<span class="main-entry-card-tag">{html_escape(tag)}</span>'
            for tag in tags
        )
        muted_class = " main-entry-card-muted" if muted else ""
        render_html_block(
            dedent(
                f"""
                <div class="main-entry-card{muted_class}">
                    <div class="main-entry-card-eyebrow">{html_escape(eyebrow)}</div>
                    <div class="main-entry-card-title">{html_escape(title)}</div>
                    <div class="main-entry-card-caption">{html_escape(caption)}</div>
                    <div class="main-entry-card-tags">{tags_html}</div>
                </div>
                """
            ).strip()
        )

    hero_html = dedent(
        """
        <div class="home-hero">
            <div class="home-hero-title">实验室室内质控管理工具</div>
            <div class="home-hero-caption">
                用于实验室室内质控的 LJ 与多水平 Z-score 管理与判读。
                首页优先保留导航与高频入口，详细说明后移到下半区。
            </div>
        </div>
        """
    ).strip()
    render_html_block(hero_html)

    hero_action_cols = st.columns([1, 1, 1.4], gap="medium")
    with hero_action_cols[0]:
        if st.button("进入 LJ", key="hero_jump_lj", type="primary", width="stretch"):
            switch_top_level_method("单水平（LJ法）")
    with hero_action_cols[1]:
        if st.button("进入 Z-score", key="hero_jump_zscore", type="primary", width="stretch"):
            switch_top_level_method("多水平（Z-score法）")
    with hero_action_cols[2]:
        st.caption("Instant 当前仅作预留入口，不参与首屏主操作。")

    st.divider()
    st.markdown("**主入口**")
    entry_col1, entry_col2 = st.columns(2, gap="large")
    with entry_col1:
        render_entry_card(
            "LJ",
            "单水平质控工作页，适合日常结果录入、图表查看与 Westgard 判读。",
            ["单水平", "保存结果", "latest analysis"],
            eyebrow="主入口",
        )
        if st.button("进入 LJ 工作页", key="main_jump_lj_primary", type="primary", width="stretch"):
            switch_top_level_method("单水平（LJ法）")
    with entry_col2:
        render_entry_card(
            "Z-score",
            "多水平质控工作页，保留与 LJ 接近的阅读节奏，只增加 level 维度。",
            ["2/3 水平", "保存 run", "图表判读"],
            eyebrow="主入口",
        )
        if st.button("进入 Z-score 工作页", key="main_jump_zscore_primary", type="primary", width="stretch"):
            switch_top_level_method("多水平（Z-score法）")

    st.markdown("**预留模块**")
    render_entry_card(
        "Instant",
        "当前仅作预留模块展示，不影响 LJ 与 Z-score 现有主流程。",
        ["预留", "暂未接入", "后续扩展"],
        eyebrow="预留模块",
        muted=True,
    )
    instant_col1, instant_col2 = st.columns([1, 2.2], gap="medium")
    with instant_col1:
        if st.button("查看 Instant", key="main_jump_instant_reserved", width="stretch"):
            switch_top_level_method("即刻法")
    with instant_col2:
        st.caption("如需立即开展质控，请优先从上方 LJ 或 Z-score 入口进入。")

    st.divider()
    st.markdown("**快速开始**")
    quick_start_col1, quick_start_col2 = st.columns(2, gap="large")
    with quick_start_col1:
        st.markdown("**LJ 快速开始**")
        st.markdown(
            "1. 新建 LJ 项目与批次。\n"
            "2. 确认建靶所需次数。\n"
            "3. 录入检测结果并保存。\n"
            "4. 在图表与 latest analysis 中查看当前判读。"
        )
    with quick_start_col2:
        st.markdown("**Z-score 快速开始**")
        st.markdown(
            "1. 新建 Z-score 项目并确定水平数。\n"
            "2. 新建批次并配置水平说明。\n"
            "3. 录入本次 run 的各 level 结果。\n"
            "4. 在图表与 latest analysis 中查看当前判读。"
        )

    st.divider()
    st.markdown("**说明与版本信息**")
    st.caption("使用说明、更新记录和版本边界后置在此，避免首屏被说明文字占满。")
    with st.expander("LJ 使用说明", expanded=False):
        st.markdown(
            "- 适用场景：单水平室内质控、LJ 曲线查看与 Westgard 规则判读。\n"
            "- 基本概念：项目用于区分分析物或方法，批次用于承载同一组质控材料、批号和建靶参数。\n"
            "- 建靶所需次数：以批次中的“建靶所需次数”为准；完成前仅累计统计，不启用正式规则判读。\n"
            "- 正式质控后：可在图表区查看建靶图、正式质控图、全部数据图，以及标准视图 / 全范围视图。\n"
            "- 记录维护：可修改或删除历史检测记录；删除后会重算后续检测序号、阶段和判读结果。"
        )

    with st.expander("Z-score 使用说明", expanded=False):
        st.markdown(
            "- 适用场景：双水平 / 三水平多水平 IQC 管理与判读。\n"
            "- 项目级水平数配置：决定该项目固定采用双水平或三水平流程；创建后批次会自动继承。\n"
            "- 批次级水平说明：用于给默认的水平 1 / 水平 2 / 水平 3 添加业务名称，便于录入与维护。\n"
            "- 建靶期与正式质控期：建靶期用于累计实验室正式靶值；只有全部水平达到建靶条件后，才进入正式质控期。\n"
            "- 图表理解：单水平图用于查看单个水平趋势；合并图用于对比多个水平；数据范围可切换为建靶期图、正式质控图或全图。\n"
            "- 厂家参考值：仅供参考，不直接替代实验室正式靶值；当前版本仅支持手工录入。\n"
            "- 正式期实时统计：只基于正式期在控数据计算，警告和失控结果不纳入统计。"
        )

    with st.expander("2026-04-05 更新：数据的导入导出", expanded=False):
        st.markdown(
            "**更新内容**\n"
            "- LJ：当前批次已支持按阶段导出建靶期 / 正式期数据，格式可选 Excel / CSV；已支持当前 LJ 图 PNG 导出，以及仅基于正式数据的月度质控图 PNG 导出。\n"
            "- LJ：已提供建靶期 / 正式期 CSV 模板下载、CSV 审查与确认导入；审查结果会返回总行数、可导入行数、错误行数、警告行数，并展示逐行问题。\n"
            "- Z-score：当前批次已支持按阶段导出建靶期 / 正式期 run 宽表，格式可选 Excel / CSV；已支持当前图 PNG 导出，以及正式期月度图 PNG 导出。\n"
            "- Z-score：已提供建靶期 / 正式期 CSV 模板下载、CSV 审查与确认导入；模板会按当前批次 2 水平 / 3 水平自动生成，审查结果同样返回摘要与逐行问题。\n"
            "- 导入审查：阻断错误会禁止确认导入；非阻断项保留为提醒，覆盖模板不匹配、缺少必填列、模板外列、检测时间重复、备注为空等常见情况。\n\n"
            "**当前限制 / 已知边界**\n"
            "- 导入入口当前仅支持标准 CSV 模板，不支持 Excel 导入，也不支持跨批次批量导入。\n"
            "- LJ 月度质控图与 Z-score 月度图当前都只导正式期数据，日期范围最长 30 天。\n"
            "- Z-score 建靶期导入仅在当前批次未完成建靶时开放；正式期导入需建靶完成后再执行，建靶期文件不能跨阶段直接导入到正式期。\n"
            "- 导入为追加写入当前批次；确认导入后会按现有业务口径刷新统计、判定、图表与最新结果分析。"
        )

    with st.expander("2026-03-31 更新：批次级 CV 要求（%）", expanded=False):
        st.markdown(
            "**更新内容**\n"
            "- LJ 与 Z-score 的新建批次均支持可选填写“CV 要求（%）”。\n"
            "- 当前已有的编辑批次入口支持回显和修改“CV 要求（%）”。\n"
            "- 该值保存为批次属性，工作区只读取已保存值做提醒，不提供第二套临时输入入口。\n"
            "- LJ：建靶过程中一旦已有足够数据可计算 SD/CV，就开始显示“当前累计 CV%”与“批次要求”的对照提醒。\n"
            "- Z-score：建靶过程中按各水平分别显示 provisional CV% 与“批次要求”的对照提醒。\n"
            "- 提醒只作提示，不阻断保存，不改变现有判读逻辑与建靶/正式期切换逻辑。\n\n"
            "**使用方法**\n"
            "1. 在“项目与批次管理”中先选择项目，再新建批次。\n"
            "2. 新建批次时可选填写“CV 要求（%）”；留空则表示该批次不启用此提醒。\n"
            "3. 如需调整，可在当前已有的“编辑当前批次 / 当前 Z-score 批次配置”入口修改并保存。\n"
            "4. 进入工作区后，系统只读取该批次已保存的 CV 要求并显示提醒，不需要也不能在工作区再次录入。\n"
            "5. 当批次未设置 CV 要求时，系统保持兼容，不报错，也不强制显示空提示。\n\n"
            "**手工测试步骤**\n"
            "1. LJ：新建批次时填写 CV 要求，例如 5.00，保存后进入当前批次页。\n"
            "2. LJ：连续录入至少 2 条建靶结果，确认开始显示“当前累计建靶 CV% / 批次要求 / 是否满足要求”。\n"
            "3. LJ：新建批次时不填写 CV 要求，进入工作区确认不报错、无空提示。\n"
            "4. LJ：通过编辑批次修改 CV 要求后再次进入工作区，确认提醒读取的是修改后的已保存值。\n"
            "5. Z-score：新建批次时填写 CV 要求，录入多水平建靶 run，确认各 level 分别显示 provisional CV% 对照提醒。\n"
            "6. Z-score：旧批次或未设置 CV 要求的批次进入工作区，确认不报错。\n"
            "7. 回归一遍现有 LJ 主流程。\n"
            "8. 回归一遍现有实际生效的 Z-score 主流程。"
        )

    with st.expander("2026-04-01 更新：备注、检测人记忆与 Z-score 一致性修复", expanded=False):
        st.markdown(
            "**更新内容**\n"
            "- LJ 与 Z-score 已接入手动备注字段：维护区可查看和编辑，图上对含备注的点增加描边提示。\n"
            "- LJ 新增结果录入支持备注；Z-score 备注入口统一为“最新结果分析”下快捷补备注 + 维护区编辑，录入区不再保留常驻备注输入框。\n"
            "- Z-score 最新结果分析改为按检测序号取最新 run，修复“图上已判异常，但卡片显示无规则触发”的不一致。\n"
            "- Z-score 检测人输入改为与 LJ 一致的最近使用记忆模式，支持下拉选择最近姓名，也支持手动输入新姓名。\n"
            "- 修复旧库升级后 batches 子表仍引用 batches_legacy 的问题，create_zscore_batch 不再因外键残留报错。\n\n"
            "**手工测试**\n"
            "- LJ：新增一条结果并填写备注，保存后检查维护区显示与图上描边；新增一条不填备注的结果，确认不报错且图上不加描边。\n"
            "- LJ：在维护区修改已有备注，确认刷新后仍存在；若最新结果为警告或失控，从“最新结果分析”下快捷补备注，确认维护区和图上同步。\n"
            "- Z-score：录入区不再显示常驻备注输入框；检测人支持从最近使用列表下拉选择，也支持直接输入新名字，保存后新名字会进入最近使用列表。\n"
            "- Z-score：新增一条 run 并保存，确认不依赖录入区备注也能正常保存；在维护区查看和编辑备注，确认刷新后仍存在，图上描边状态同步。\n"
            "- Z-score：当最新 formal run 为 warning 或 reject 时，从“最新结果分析”下快捷补备注，确认维护区和图上同步，不再保留第三个分散入口。\n"
            "- Z-score：选取一个最新异常 run，确认图上最后一个 run 的状态样式与“最新结果分析”卡片中的规则判定一致，不再出现“图上像失控，但卡片显示无规则触发”。\n"
            "- 旧库升级：使用旧库副本执行数据库初始化后，新建 Z-score 批次，确认不再出现 main.batches_legacy 相关报错。\n"
            "- 回归一遍现有 LJ 主流程与当前实际生效的 Z-score 主流程。\n\n"
            "**已知问题 / 暂未处理**\n"
            "- 本轮未新增图上点选交互，备注全文仍只通过维护入口或异常快捷入口查看与修改。\n"
            "- Z-score 录入区检测人新交互已完成代码接入，但尚未单独做一次完整浏览器手点回归。\n"
            "- database.py 前半段旧 Z-score 重复定义仍保留，继续以后半段实际生效定义为准。"
        )

    with st.expander("常见说明 / 注意事项", expanded=False):
        st.markdown(
            "- 建靶期不启用正式规则判读。\n"
            "- Z-score 批次进入正式质控后，建靶期检测记录会被锁定，只可查看，不可再维护。\n"
            "- 删除 Z-score 检测记录后会触发整批次重算，包括建靶统计、正式靶值、正式期实时统计和图表基础数据。\n"
            "- 检测序号是业务序号，与数据库内部编号不同。\n"
            "- 厂家参考值当前仅支持手工录入，不支持 COA 自动解析。"
        )

    with st.expander("当前版本说明", expanded=False):
        st.caption("当前版本已具备主流程使用能力，但仍以内部试用、演示和小范围部署为主要交付场景。")
        support_col, limit_col = st.columns(2, gap="large")
        with support_col:
            st.markdown("**已支持**")
            st.markdown(
                "- LJ 主流程\n"
                "- Z-score 双水平 / 三水平主流程\n"
                "- 多水平检测记录持久化\n"
                "- 建靶 / 正式质控\n"
                "- 批次级 CV 要求（%）保存与建靶提醒\n"
                "- 图表查看、结果分析与记录维护\n"
                "- LJ / Z-score 分阶段数据导出、CSV 导入与图表 PNG 导出"
            )
        with limit_col:
            st.markdown("**暂未支持**")
            st.markdown(
                "- 跨批次批量导入\n"
                "- Excel 导入\n"
                "- COA 解析\n"
                "- peer-group 数据\n"
                "- target freeze / re-establish 等高级流程\n"
                "- Instant 正式业务功能"
            )

    st.caption("当前版本保持可继续试用的稳定基线；首页建议直接从 LJ 或 Z-score 主入口进入。")


def render_instant_placeholder_page() -> None:
    st.subheader("Instant")
    st.caption("Instant 页面目前作为预留入口保留。当前版本暂未接入正式业务流程。")

    status_col, guide_col = st.columns(2, gap="large")
    with status_col:
        st.markdown("**当前版本状态**")
        st.markdown(
            "- 本页当前仅用于说明模块定位。\n"
            "- 暂不提供正式数据录入、规则判读、图表输出或导出能力。\n"
            "- 该页面不会影响 LJ 与 Z-score 现有主流程。"
        )
    with guide_col:
        st.markdown("**建议使用路径**")
        st.markdown(
            "- 如需立即开展单水平室内质控，请进入“单水平（LJ法）”页面。\n"
            "- 如需开展双水平 / 三水平多水平 IQC，请进入“多水平（Z-score法）”页面。\n"
            "- Instant 后续若接入正式功能，会在此页面补充明确说明。"
        )

    st.info("当前可用的质控流程请从顶部“功能入口”进入“单水平（LJ法）”或“多水平（Z-score法）”页面。")
