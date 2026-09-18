from __future__ import annotations

from textwrap import dedent

import streamlit as st

from ui.common import render_html_block, render_section_intro


MAIN_ENTRY_LABEL = "主页"
MASTER_DATA_ENTRY_LABEL = "基础资料"
PROJECT_MANAGEMENT_ENTRY_LABEL = "项目/批次管理"
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
    "基础资料": MASTER_DATA_ENTRY_LABEL,
    "项目管理": PROJECT_MANAGEMENT_ENTRY_LABEL,
    "项目/批次管理": PROJECT_MANAGEMENT_ENTRY_LABEL,
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
        <div class="main-entry-card home-method-card">
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
    guides = {
        "首次使用：准备资料与项目": """
1. 在顶部“资料与批次 → 基础资料”中登记厂家、医院实际仪器、试剂、质控品、批号和水平。检验项目可搜索名称或别名，找不到时可新增。
2. 进入“资料与批次 → 项目/批次管理”，为仪器和质控品建立项目，添加检验项目，选择质控方法、试剂、单位及输入值类型，再启用项目。
3. 创建批次，核对各检验项目的 允许不精密度（CV）上限及依据，选择实际质控品批号和全部水平，设置均值和标准差建立数量或设定均值与 SD，检查后启用。设定变异系数（%） 自动计算；允许不精密度（CV）留空表示未设置。
4. 使用人工或厂家提供的均值和标准差时，还需在“批号使用与追溯 → 均值和标准差管理”确认全部水平参数、依据、确认人及生效时间。
5. 在“系统设置”填写实验室和报告信息。完成准备后，进入对应方法工作台选择项目与批次。

每个检验项目使用一种输入值类型：真实检测值、Ct 值或 log 值。录入与导入时按检验项目所选类型填写。
""",
        "单水平 LJ：录入、均值和标准差建立与日常判读": """
1. 进入“单水平（LJ法）”，选择设置已确认的项目与批次，再打开“当前批次”。
2. 核对仪器、单位和质控品批号，填写检测时间、检测人、检测值，并选择本次实际试剂批号。
3. 本批次均值和标准差建立时，查看有效点数、均值、SD、CV 和疑似离群提示。系统不会自动剔除记录，可在维护区保留、禁用或恢复。
4. 均值和标准差建立达到设定数量后，后续检测按正式参数判读。使用已确认的人工或厂家参数时，按参数生效时间进行判读。
5. 保存后查看 LJ 图和规则提示；月底在月报区选择月份，核对记录和异常处理情况，再生成 PDF。
""",
        "多水平 Z-score：一次录入全部水平": """
1. 选择设置已确认的 2 或 3 水平项目与批次，核对各水平的名称和实际质控品批号。
2. 一次检测同时填写全部水平结果，选择实际试剂批号后保存。
3. 参数建立期分别查看各水平统计；保留、禁用和恢复按整次检测联动处理。
4. 全部水平完成均值和标准差建立或确认正式参数后，查看整次检测的联合结论。任一水平触发拒绝规则时，整次检测判为失控。
5. 可切换单水平图与合并图。进入正式期后，参数建立记录只读；月报可在当前批次的月报区生成。
""",
        "即时法：积累数据并确认转入 LJ": """
1. 选择设置已确认、采用本批次均值和标准差建立的单水平即时法项目与批次，逐次录入检测结果和实际试剂批号。
2. 有 3 个有效点后开始格拉布斯检验，并显示 SI 提示。疑似离群点需人工复核，系统不会自动剔除。
3. 累计到 20 个有效建立点后，核对数据并按提示人工确认转入 LJ；系统不会自动转换。
4. 转入后在 LJ 工作台继续录入。原即时法记录保留供查询，不能继续修改。

即时法目前提供记录、统计和图表，月度 PDF 报告在 LJ 与 Z-score 工作台生成。
""",
        "更换试剂批号或质控品批号": """
“新旧批号比对”用于质控品两批材料的同时检测；当前提供分别查看和状态管理，专门比对图表与报告尚未提供。“试剂换批比对”指同一患者样本的新旧试剂成对结果，尚未实现。

在“资料与批次 → 项目/批次管理 → 批号使用与追溯”办理换批。

- **试剂换批**：登记生产批号及效期，保存本检测项的验证结论，选择受影响的检测项和启用时间，核对预览后确认切换。日常录入仍需选择实际批号；已保存的记录保留原批号。
- **质控品换批**：先在基础资料登记新批号及水平，再从原配置创建新批同时使用批次。新批从空记录开始，旧批保留；参数和各实际批号的验证全部确认后正式使用。
- **只换部分水平**：建立新的完整水平组合，核对每个水平的实际批号。未换水平的参数可作参考，仍需与新水平一起确认。
- **结束旧批**：核对结束时间后将旧批设为“停止使用”。结束后可查询、导出和查看报告，不能再录入或维护。

生效时间、最新验证结论和效期都需一致；未来计划到达生效时间后才应用。
""",
        "批量导入与历史查询": """
LJ 和 Z-score 可在当前批次的导入区下载模板，按对应方法、水平数和阶段填写数据。

1. 填写检测时间、检测人、检测值及可选的实际试剂批号、备注。
2. 上传文件，先检查预览和错误提示，确认无误后再导入。
3. 导入是追加记录；批号空白会保存为“未记录”，不会用当前默认批号补填历史资料。
4. 导入后核对记录和图表。如果预览后批号已切换，请刷新预览再确认。

历史批次可在“批号使用与追溯 → 历史记录”查询，包括已停用或停止使用的批次。缺失的历史批号保持“未记录”。
""",
        "月报与报告历史": """
1. 在 LJ 或 Z-score 当前批次的月报区选择月份，核对检测记录、异常原因、纠正措施和结论后生成 PDF。
2. 报告包含质控图、统计、异常记录，以及实际批号和控制参数来源等追溯信息。
3. 在顶部“报告历史”按项目、方法、批次或月份查找已生成报告的摘要。
4. “按当前数据重新生成”会生成新的 PDF 并保留新的报告记录，内容可能与原报告不同。
""",
        "数据备份与更换电脑": """
在顶部“系统设置”查看数据保存位置、创建备份或从备份恢复。定期备份，移动数据或更换电脑前先保存一份备份。

更改保存位置请使用文件夹选择按钮，完成后按提示重启。恢复备份会替换当前数据，操作前请核对备份文件和时间。
""",
    }
    for title, content in guides.items():
        with st.expander(title):
            st.markdown(content.strip())



def render_main_entry_page() -> None:
    render_section_intro(
        title="日常质控",
        caption="选择质控方法，进入项目与批次，开始录入或查看结果。均值和标准差建立期间简称“参数建立期”。",
        tone="accent",
    )
    cards = [
        ("单水平", "LJ 法", "适用于单水平质控，查看 LJ 图、Westgard 判读和月度报告。",
         ["支持本批次均值和标准差建立及已确认的人工、厂家参数。", "记录实际批号，保留参数变更前后的检测历史。"],
         ["单水平", "LJ 图", "月报"], "打开单水平（LJ法）", "open_main_lj_card", LJ_ENTRY_LABEL),
        ("多水平", "Z-score 法", "适用于 2 或 3 水平质控，按整次检测给出联合结论。",
         ["一次填写全部水平，查看单水平图或合并图。", "支持部分水平换批及各水平实际批号追溯。"],
         ["2 / 3 水平", "联合判读", "月报"], "打开多水平（Z-score法）", "open_main_zscore_card", ZSCORE_ENTRY_LABEL),
        ("单水平", "即时法", "从少量有效点开始观察，逐步积累参数建立数据。",
         ["3 个有效点后开始格拉布斯检验和 SI 提示。", "20 个有效建立点后可人工确认转入 LJ。"],
         ["3 点检验", "20 点转入", "人工确认"], "打开即时法", "open_main_instant_card", INSTANT_ENTRY_LABEL),
    ]
    for column, card in zip(st.columns(3, gap="large"), cards):
        eyebrow, title, caption, bullets, tags, label, key, method = card
        with column:
            _render_method_card(eyebrow=eyebrow,title=title,caption=caption,bullet_points=bullets,tags=tags)
            if st.button(label,key=key,type="primary",width="stretch"):
                switch_top_level_method(method)
    st.divider()
    render_section_intro(title="使用指南",caption="首次使用先完成资料和项目准备；日常操作可按需要展开查看。")
    _render_frontline_user_guide()


def render_instant_placeholder_page() -> None:
    from pages.instant_page import render_instant_page
    render_instant_page()
