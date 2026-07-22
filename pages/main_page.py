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


def _render_frontline_user_guide() -> None:
    with st.expander("第一次使用：先做什么", expanded=True):
        st.markdown(
            dedent(
                """
                1. 打开软件后，先看页面上方的几个入口。
                2. 如果只做一个水平的日常质控，选择“单水平（LJ法）”。
                3. 如果一个项目有 2 个或 3 个水平一起判断，选择“多水平（Z-score法）”。
                4. 如果单水平项目仍处于建靶累计阶段，选择“即时法”先过渡，批次建靶次数可设为 5～20 次。
                5. 第一次使用前，建议先到“系统设置”确认实验室名称、科室名称、负责人和审核人。
                6. 如果只是练习，可以先使用演示数据。演示数据项目名称一般以“[DEMO]”开头。
                7. 正式录入前，请先确认当前选择的项目和批次，不要把数据录到错误批次里。
                """
            ).strip()
        )

    with st.expander("单水平（LJ法）：一步一步怎么做"):
        st.markdown(
            dedent(
                """
                适合情况：一个检测项目只有一个质控水平，需要做日常 LJ 图和 Westgard 规则判断。

                日常操作顺序：

                1. 点击顶部“单水平（LJ法）”。
                2. 在“项目与批次管理”里先新建项目。
                3. 项目名称建议写清楚检测项目，例如“ALT 质控”。
                4. 按页面要求填写单位、数值类型等信息，然后保存项目。
                5. 选中刚建好的项目，再新建批次。
                6. 批次里填写仪器、试剂、质控品、浓度水平、批号、建靶数量、CV 要求等信息。
                7. 保存批次后，进入当前批次页面。
                8. 每天检测后，在结果录入区填写检测时间、检测人和检测值。
                9. 建靶期主要看均值、SD、CV 和离群提示。
                10. 建靶数量达到要求后，后面的数据会进入正式期。
                11. 正式期主要看 LJ 图、Westgard 规则提示和最新结果分析。
                12. 如果发现录错了，或者需要处理离群点，到“记录维护”区域处理。
                13. 月底需要总结时，在月报区域选择月份并生成月报。

                小提醒：

                - 每次录入前先确认项目和批次。
                - 建靶期和正式期不要混着理解。
                - 录错数据不要重复录一条来“抵消”，应到记录维护里处理。
                - 月报通常看正式期数据，所以生成前先确认当月数据已经录完整。
                """
            ).strip()
        )

    with st.expander("多水平（Z-score法）：一步一步怎么做"):
        st.markdown(
            dedent(
                """
                适合情况：一个项目有 2 个或 3 个质控水平，需要一起判断。

                日常操作顺序：

                1. 点击顶部“多水平（Z-score法）”。
                2. 在“项目与批次管理”中新建项目。
                3. 新建项目时选择水平数：2 水平或 3 水平。
                4. 选中项目后，新建批次。
                5. 批次里填写仪器、试剂、质控品、批号、各水平名称和建靶数量。
                6. 进入当前批次页面。
                7. 每次检测作为一次记录。一次检测里要同时填写各水平结果。
                8. 例如 2 水平项目，一次检测要填低水平和高水平两个结果。
                9. 建靶期分别查看每个水平的均值、SD、CV 和离群提示。
                10. 所有水平都达到建靶要求后，后续检测进入正式期。
                11. 正式期可以查看单水平图、合并图、本次检测结论和规则证据。
                12. 如果某一次检测需要修改，到记录维护区按“本次检测”进行处理。
                13. 月底需要总结时，在月报区域选择月份并生成月报。

                小提醒：

                - Z-score 不是一条一条单独水平乱录，而是按“一次检测”录入多个水平。
                - 2 水平和 3 水平的模板不同，导入时不要用错。
                - 修改历史数据后，应重新查看图表和本次检测结论。
                """
            ).strip()
        )

    with st.expander("即时法：一步一步怎么做"):
        st.markdown(
            dedent(
                """
                适合情况：单水平项目刚开始做，需要按批次设定的建靶次数逐次累计和判断。

                日常操作顺序：

                1. 点击顶部“即时法”。
                2. 新建即时法项目。
                3. 新建批次，并设置本批次建靶所需次数（5～20 次，默认 20 次）。
                4. 每次检测后，录入检测时间、检测人和检测值。
                5. 数据点较少时，系统主要帮你累计数据。
                6. 达到可以判断的数量后，页面会显示即刻法 SI 提示。
                7. 如果页面提示疑似离群，先复核当天检测、仪器、试剂、质控品和录入值。
                8. 如果确认是异常点，按页面维护入口处理。
                9. 累计到本批次设定的有效点数后，可以按页面提示确认转入 LJ 法。
                10. 转入后，到“单水平（LJ法）”里查看对应项目和批次。

                小提醒：

                - 即时法是过渡用的，不是长期正式质控的最终入口。
                - 转入 LJ 法后，后续日常质控应在 LJ 法里继续做。
                - 转入前请确认用于本批次建靶的有效点没有明显录入错误。
                """
            ).strip()
        )

    with st.expander("如何导入数据"):
        st.markdown(
            dedent(
                """
                适合情况：已经有一批检测结果，不想一条一条手工录入。

                操作顺序：

                1. 先进入对应方法页面，例如“单水平（LJ法）”或“多水平（Z-score法）”。
                2. 选择正确的项目。
                3. 选择正确的批次。
                4. 找到页面里的“导入/导出”区域。
                5. 先下载当前批次、当前阶段对应的导入模板。
                6. 打开模板，按列填写检测时间、检测人、检测值和备注。
                7. 回到软件，上传填好的 CSV 文件。
                8. 先看导入审查结果。
                9. 如果有红色错误，先回到文件里修改，再重新上传。
                10. 审查通过后，再点击确认导入。
                11. 导入完成后，回到图表和记录区确认数据是否正确。

                导入前请特别注意：

                - 导入是追加写入，不是覆盖原数据。
                - 一定先确认项目和批次选对了。
                - 建靶期模板不要导入正式期。
                - 正式期模板不要导入建靶期。
                - LJ 法和 Z-score 法模板不能混用。
                - Z-score 的 2 水平和 3 水平模板不能混用。
                """
            ).strip()
        )

    with st.expander("如何导出数据和生成月报"):
        st.markdown(
            dedent(
                """
                导出数据：

                1. 进入对应方法页面。
                2. 选择项目和批次。
                3. 找到导出区域。
                4. 选择要导出的数据范围。
                5. 按页面按钮导出 Excel 或 CSV。
                6. 如需保存图表，使用图表导出按钮保存 PNG。

                生成月报：

                1. 进入对应方法页面。
                2. 选择项目和批次。
                3. 找到月报区域。
                4. 选择要生成月报的月份。
                5. 生成前确认该月正式期数据已经录完整。
                6. 点击生成月报。
                7. 生成后可以在“报告历史”里再次查看或重新生成。

                小提醒：

                - 月报主要用于月度回顾，生成前请先检查当月数据。
                - 如果后续修改了历史数据，建议重新生成月报。
                """
            ).strip()
        )

    with st.expander("如何查看报告历史"):
        st.markdown(
            dedent(
                """
                1. 点击页面右上角或全局入口中的“报告历史”。
                2. 在筛选区输入项目名称，或选择方法、批次、月份。
                3. 找到需要查看的报告记录。
                4. 先查看摘要，确认是不是目标报告。
                5. 如果需要当前最新数据的报告，点击按当前数据重新生成。
                6. 重新生成后，再下载或查看新的报告。

                小提醒：

                - 报告历史里的摘要用于帮助你确认报告。
                - 如果原始数据后来改过，旧报告摘要可能和当前数据不完全一致。
                - 需要最新结果时，请按当前数据重新生成。
                """
            ).strip()
        )

    with st.expander("如何使用演示数据"):
        st.markdown(
            dedent(
                """
                演示数据是用来练习的，不是真实检测数据。项目名称通常以“[DEMO]”开头。

                练习顺序：

                1. 在本区域勾选确认框，再点击“导入/刷新标准演示数据”。
                2. 导入完成后，进入“单水平（LJ法）”。
                3. 选择 “[DEMO] LJ ...” 开头的项目，练习建靶期、正式期、图表和月报。
                4. 再进入“多水平（Z-score法）”。
                5. 选择 “[DEMO] ZS ...” 开头的项目，练习多水平录入、合并图和本次检测结论。
                6. 再进入“即时法”。
                7. 选择 “[DEMO] Instant ...” 开头的项目，练习 SI 提示和转入 LJ 法流程。

                小提醒：

                - 刷新标准演示数据只处理演示项目，不应删除真实项目。
                - 正式使用时，请注意不要把真实检测结果录入演示项目。
                """
            ).strip()
        )
        confirm_demo_load = st.checkbox("我确认要导入/刷新演示数据", key="confirm_demo_data_refresh")
        if st.button(
            "导入/刷新标准演示数据",
            key="refresh_standard_demo_data",
            disabled=not confirm_demo_load,
            width="stretch",
        ):
            try:
                from scripts.generate_demo_qc_data import load_demo_data

                result = load_demo_data(use_current_app_db=True, on_conflict="replace-demo-only")
            except Exception as exc:
                st.error(f"演示数据导入失败：{exc}")
            else:
                st.success("演示数据已刷新。")
                st.markdown("**已写入或刷新以下演示项目：**")
                for summary in result.summaries:
                    st.markdown(f"- {summary.project_name}")
                st.caption(f"当前数据库：{result.db_path}")
                if result.backup_path:
                    st.caption(f"写入前备份：{result.backup_path}")
                st.info("下一步可以进入单水平（LJ法）、多水平（Z-score法）或即时法，选择 [DEMO] 开头的项目进行练习。")

    with st.expander("数据备份、恢复和重置说明"):
        st.markdown(
            dedent(
                """
                日常建议：

                1. 正式使用前，先到“系统设置”确认当前数据库位置。
                2. 重要数据录入后，建议定期备份。
                3. 更换电脑或移动数据前，先做备份。
                4. 如果需要恢复旧数据，到“系统设置”里使用恢复功能。
                5. 恢复或迁移数据库后，通常需要重启软件。

                请注意：

                - “刷新演示数据”只处理演示项目，适合培训和练习。
                - “重置数据库”会删除当前数据库，不只是删除演示数据。
                - 一线工作人员不要随便重置数据库。
                - 如果确实要重置，请先联系管理员或确认已有备份。
                """
            ).strip()
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

    st.caption("右上角保留报告历史和系统设置等全局入口，当前页面主要用于方法选择和操作帮助查看。")

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
            caption="适用于单水平项目的建靶过渡期，批次建靶次数可设为 5～20 次，满足条件后可人工确认转入 LJ 法。",
            bullet_points=[
                "3 个有效点后开始即刻法 SI 值提示。",
                "累计到批次设定的有效点数后可确认转入 LJ 法。",
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
        caption="给一线工作人员看的日常操作步骤：按顺序展开查看即可。",
        badges=["第一次使用", "LJ法", "Z-score法", "即时法", "导入导出", "月报"],
        tone="default",
    )
    _render_frontline_user_guide()


def render_instant_placeholder_page() -> None:
    render_section_intro(
        title="即时法",
        caption="即时法已经纳入顶部主导航，请直接从“即时法”入口进入正式工作台。",
        badges=["即时法", "过渡方法", "确认转入 LJ 法"],
        tone="accent",
    )
    st.info("请从顶部“即时法”入口进入正式页面；此占位页仅保留兼容说明。")
