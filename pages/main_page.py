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
    chips = "".join(
        f'<div class="welcome-chip">{html_escape(label)}</div>'
        for label in [
            "LJ 曲线",
            "多水平 Z-score",
            "项目与批次管理",
            "建靶与正式质控",
            "图表分析与记录维护",
        ]
    )
    hero_html = dedent(
        f"""
        <div style="
            border:1px solid #d9e2ee;
            border-radius:18px;
            padding:20px 22px;
            background:linear-gradient(135deg, #f7fbff 0%, #eef5fb 55%, #f9fbfd 100%);
            margin:4px 0 14px 0;
        ">
            <div style="font-size:28px;font-weight:800;color:#1c3553;line-height:1.2;">
                实验室室内质控管理工具
            </div>
            <div style="margin-top:8px;font-size:14px;font-weight:600;color:#36587f;">
                用于实验室室内质控的 LJ 与多水平 Z-score 管理与判读工具
            </div>
            <div style="margin-top:10px;font-size:14px;line-height:1.7;color:#4e6076;">
                当前版本支持 LJ 曲线、多水平 Z-score、项目与批次管理、建靶与正式质控、
                图表查看、结果分析和记录维护，可作为内部试用、演示与小范围部署的交付基线。
            </div>
            <div class="welcome-chip-row">{chips}</div>
        </div>
        """
    ).strip()
    render_html_block(hero_html)

    render_html_block(
        dedent(
            """
            <div class="main-highlight-box">
                <div class="main-highlight-title">从哪里开始</div>
                <div class="main-highlight-body">
                    首次使用建议先选择 <strong>LJ</strong> 或 <strong>Z-score</strong> 页面，按“先建项目、再建批次、再录入结果”的顺序开始。
                    如果只是想了解方法差异和当前版本边界，可先阅读下方方法说明与使用教程。
                </div>
            </div>
            """
        ).strip()
    )

    st.divider()
    st.markdown("**功能入口与方法说明**")
    method_cards = [
        (
            "LJ",
            "适用于单水平 LJ 曲线质控。",
            [
                "支持项目与批次管理、建靶、正式质控与 Westgard 判读。",
                "支持标准视图 / 全范围视图、规则汇总、最新结果分析与记录维护。",
                "适合常规单水平室内质控流程。",
            ],
            "进入单水平页面",
            "单水平（LJ法）",
        ),
        (
            "Z-score",
            "适用于双水平 / 三水平多水平 IQC。",
            [
                "支持项目级水平数配置与批次级水平说明。",
                "支持建靶期 / 正式质控期、多水平检测记录录入、图表与结果分析。",
                "支持正式期记录维护，以及删除或编辑后的整批次重算。",
            ],
            "进入多水平页面",
            "多水平（Z-score法）",
        ),
        (
            "Instant",
            "当前为预留页面。",
            [
                "本版本尚未接入正式业务逻辑。",
                "页面仅保留模块定位说明，不参与当前单水平与多水平主流程。",
                "如需可用功能，请优先进入“单水平（LJ法）”或“多水平（Z-score法）”页面。",
            ],
            "查看即刻法说明",
            "即刻法",
        ),
    ]
    method_cols = st.columns(3, gap="large")
    for column, (title, caption, bullets, button_label, target_method) in zip(method_cols, method_cards):
        with column:
            bullet_html = "".join(f"<li>{html_escape(item)}</li>" for item in bullets)
            render_html_block(
                dedent(
                    f"""
                    <div class="main-entry-card">
                        <div class="main-entry-card-title">{html_escape(title)}</div>
                        <div class="main-entry-card-caption">{html_escape(caption)}</div>
                        <ul class="main-entry-card-list">{bullet_html}</ul>
                    </div>
                    """
                ).strip()
            )
            if st.button(button_label, key=f"main_jump_{target_method}", width="stretch"):
                switch_top_level_method(target_method)

    st.divider()
    st.markdown("**快速开始**")
    quick_start_col1, quick_start_col2 = st.columns(2, gap="large")
    with quick_start_col1:
        st.markdown("**LJ 快速开始**")
        st.markdown(
            "1. 新建 LJ 项目。\n"
            "2. 新建批次，并确认建靶所需次数。\n"
            "3. 录入检测结果，累计建靶数据。\n"
            "4. 建靶完成后自动进入正式质控，并开始 Westgard 判读。\n"
            "5. 在图表区查看趋势、规则汇总与最新结果分析；如需修正历史数据，可进入记录维护。"
        )
        if st.button("从 LJ 开始", key="main_quickstart_lj", width="stretch"):
            switch_top_level_method("单水平（LJ法）")
    with quick_start_col2:
        st.markdown("**Z-score 快速开始**")
        st.markdown(
            "1. 新建 Z-score 项目，并选择双水平或三水平。\n"
            "2. 新建批次并配置各水平名称或说明。\n"
            "3. 录入多水平检测记录，完成建靶。\n"
            "4. 建靶完成后进入正式质控，查看单水平图、合并图和最新结果分析。\n"
            "5. 如需修正正式期检测记录，可通过记录维护入口编辑或删除，系统会自动整批次重算。"
        )
        if st.button("从 Z-score 开始", key="main_quickstart_zscore", width="stretch"):
            switch_top_level_method("多水平（Z-score法）")

    st.divider()
    st.markdown("**使用说明与版本边界**")
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
                "- LJ 导出与月度质控图导出"
            )
        with limit_col:
            st.markdown("**暂未支持**")
            st.markdown(
                "- 批量导入\n"
                "- Z-score 导出\n"
                "- COA 解析\n"
                "- peer-group 数据\n"
                "- target freeze / re-establish 等高级流程\n"
                "- Instant 正式业务功能"
            )

    st.info("可直接点击上方按钮进入对应页面；也可以使用顶部“功能入口”在“主页 / 单水平（LJ法） / 多水平（Z-score法） / 即刻法”之间切换。")


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
