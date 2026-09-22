from __future__ import annotations

from textwrap import dedent

import streamlit as st

from ui.common import render_html_block, render_section_intro


MAIN_ENTRY_LABEL = "主页"
MASTER_DATA_ENTRY_LABEL = "基础资料"
PROJECT_MANAGEMENT_ENTRY_LABEL = "项目/批次管理"
LJ_ENTRY_LABEL = "单水平（LJ）"
ZSCORE_ENTRY_LABEL = "多水平法"
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
    "单水平（LJ）": LJ_ENTRY_LABEL,
    "单水平 LJ": LJ_ENTRY_LABEL,
    "Z-score": ZSCORE_ENTRY_LABEL,
    "多水平（Z-score法）": ZSCORE_ENTRY_LABEL,
    "多水平法": ZSCORE_ENTRY_LABEL,
    "多水平 Z-score": ZSCORE_ENTRY_LABEL,
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
    st.caption('使用说明 · 从准备项目和批次，到录入检测结果、查看质控图与生成报告。')
    guides = {
        "日常从首页进入：选择项目 → 检验项目 → 批次": """
1. 在首页按项目名称、仪器、分组、质控方法或方法学查找项目，勾选行首选择框进入项目。
2. 选择具体检验项目和批次，点击“进入当前批次”，打开相应质控工作台。返回列表后保留原选择。
3. 同一项目可以包含单水平和多水平检验项目，也可以采用不同方法学；项目默认设置只带入新增检验项目，之后可逐项调整。
4. 单水平使用“单水平（LJ）”，数据积累不足时可在单水平设置中选择“使用即时法”；2 或 3 水平使用“多水平法”。按具体检验项目设置，不按“分子／生化／免疫”分组自动决定方法。

基础资料、项目、批次和换批操作：先在列表选中一行看详情，再点新增或编辑打开弹窗。点击保存才提交；按 Enter 不保存这些弹窗。取消时可选择继续填写或放弃，校验失败保留输入。

停用项目请打开“编辑项目”，填写原因后两次确认。停用保留历史记录；查询时可勾选“显示已停用项目”。关闭或强制刷新浏览器前，请先保存需要保留的内容。
""",
        "首次使用：准备资料与项目": """
1. 在“资料与批次 → 基础资料”中登记厂家、仪器和试剂。新增质控品批号时，一并填写浓度水平、浓度编号、批号及效期。检验项目可按名称或别名搜索，找不到时再新增。
2. 在首页点击“新建项目”，选择仪器和质控品，设置默认质控方法和方法学，保存后再添加检验项目。各检验项目可分别选择方法学、试剂、单位、质控方式及输入值类型，并设置质量目标。
3. 在“批次管理”点击“新增批次”，选择各水平的质控品批号。选中批次及检验项目，打开水平设置弹窗，填写均值和标准差，或设置建立均值和标准差所需的数据点数；在“本批次质量目标”逐水平确认适用条件。完成核对后确认批次设置。
4. 使用人工或厂家提供的均值和标准差时，还需在“批号使用与追溯 → 均值和标准差管理”确认全部水平参数、依据、确认人及生效时间。
5. 在“系统设置”填写实验室和报告信息。准备完成后，选择检验项目及批次，点击“进入当前批次”开始录入。

每个检验项目使用一种输入值类型：真实检测值、Ct 值或 log 值。录入与导入时按检验项目所选类型填写。
""",
        "质量目标：先核对适用标准，再确认批次": """
在“资料与批次 → 质量目标”查看标准条目、来源、版本和适用范围；项目设置选择质量目标，新批次还需逐水平确认。

- 先补齐检测技术、结果性质、结果尺度、用途及标本/基质。明确适用的标准自动带入且必须采用，不能用自由填写的排除理由或较宽自定值替代；单位或条件不明确时可保存待确认资料。
- 按检验项目名称、常用缩写或标准编号查找现行要求，分别核对项目的数值要求和检测过程要求。WS/T 属于卫生行业标准。
- Ct、log、浓度、S/CO 与阳性/阴性结果分别核对，不能互套 CV。过程及定性要求需由实验室逐项核对；阳性/阴性结果不能作为数值录入。
- 目录未覆盖时须记录官方标准查找与复核；找到未收录标准可补充全文、版本、条款及核查记录。有关过程条款仍须关联，不能把“目录没有”当作“无适用标准”。
- 实验室可补充更严的同尺度 CV，保留标准基准和补充依据。软件比较适用的 CV 要求；SD、偏倚、总误差及文字条款须另行核对。采用标准本身不代表检测在控。
- 已确认批次保留原采用记录。复制、导入或换批后须按现行标准重新确认。参考区间不作为质量限值。

找不到对应要求时，请按实际检验项目查找官方标准并核对适用范围。
""",
        "单水平（LJ）：录入、均值和标准差建立与日常判读": """
1. 从首页进入项目，选择采用单水平（LJ）的检验项目和设置已确认的批次，再点击“进入当前批次”。
2. 核对仪器、单位和质控品批号，填写检测时间、检测人、检测值，并选择本次实际试剂批号。
3. 本批次均值和标准差建立时，查看有效点数、均值、SD、CV 和疑似离群提示。系统不会自动剔除记录，可在维护区保留、禁用或恢复。
4. 均值和标准差建立达到设定数量后，后续检测按正式参数判读。使用已确认的人工或厂家参数时，按参数生效时间进行判读。
5. 保存后查看 LJ 图和规则提示；月底在月报区选择月份，核对记录和异常处理情况，再生成 PDF。
""",
        "多水平法：一次录入全部水平": """
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
4. 转入后在单水平（LJ）工作台继续录入。原即时法记录保留供查询，不能继续修改。

即时法目前提供记录、统计和图表，月度 PDF 报告在单水平（LJ）与多水平法工作台生成。
""",
        "更换试剂批号或质控品批号": """
“新旧批号比对”是同时检测新旧两批质控品；目前可以分别查看两批结果，尚未提供专门的比对图表和报告。“试剂换批比对”是用两批试剂检测同一患者样本，目前尚未提供。

在“资料与批次 → 项目/批次管理 → 批号使用与追溯”办理换批。

- **试剂换批**：在“试剂批号与换批”点击“登记试剂批号”，填写批号、效期和来源。选中该批号，在弹窗中登记检验项目的验证结论；再点击“切换到此试剂批号”，明确勾选本次切换的检验项目，核对新旧批号、最新验证和启用时间后确认。日常录入仍需选择实际批号；已保存的记录保留原批号。
- **质控品换批**：先登记新批号及浓度水平，再在“更换质控品批次”中选择旧批次和新批号。核对质量目标、各水平的均值和标准差，以及新批号验证结果后，确认新批次设置。旧批检测记录保留。
- **只换部分水平**：选择需要更换的水平及新批号，同时核对其余水平。原有均值和标准差可供参考，使用前仍需确认。
- **结束旧批**：核对结束时间后将旧批设为“停止使用”。结束后可查询、导出和查看报告，不能再录入或维护。

新批号须在效期内且验证通过；提前安排换批时，到指定时间后才生效。
""",
        "均值和标准差调整、历史更正": """
- 在“批号使用与追溯 → 均值和标准差管理”选中检验项目的批次，查看当前参数及历史版本；确认或调整时打开弹窗，核对各水平实际批号、均值、标准差、来源、依据、确认人及生效时间。未来版本显示“尚未生效”。
- 在“批号使用与追溯 → 历史记录”搜索并选择一条事件查看详情；试剂换批记录可点击“更正试剂使用记录”，填写正确批号、验证记录、生效时间、操作者和更正原因，核对后保存。
- 更正会新增一条关联原事件的记录，保留原事件。这里只更正试剂的默认使用安排，不修改既往检测时保存的实际批号、检测值或原判读。
- 保存后选中新记录；离开页面再返回，保留筛选和原选择。已停用或停止使用的历史批次仍可查询。
""",
        "批量导入与历史查询": """
单水平（LJ）和多水平法可在当前批次的导入区下载模板，按对应方法、水平数和阶段填写数据。

1. 填写检测时间、检测人、检测值及可选的实际试剂批号、备注。
2. 上传文件，先检查预览和错误提示，确认无误后再导入。
3. 导入是追加记录；批号空白会保存为“未记录”，不会用当前默认批号补填历史资料。
4. 导入后核对记录和图表。如果预览后批号已切换，请刷新预览再确认。

历史批次可在“批号使用与追溯 → 历史记录”查询，包括已停用或停止使用的批次。缺失的历史批号保持“未记录”。
""",
        "月报与报告历史": """
1. 在单水平（LJ）或多水平法当前批次的月报区选择月份，核对检测记录、异常原因、纠正措施和结论后生成 PDF。
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
    st.subheader("项目工作台")
    st.caption("先选择项目，再选择检验项目和批次开始质控。操作步骤见下方“使用指南”。")
    from ui.project_navigation import render_project_navigation
    render_project_navigation()
    with st.expander('质控方法说明'):
        cards = [
            ("单水平", "单水平（LJ）", "适用于单水平质控，查看 LJ 图、Westgard 判读和月度报告。",
             ["支持本批次均值和标准差建立及已确认的人工、厂家参数。", "记录实际批号，保留参数变更前后的检测历史。"],
             ["单水平", "LJ 图", "月报"], "打开单水平（LJ）", "open_main_lj_card", LJ_ENTRY_LABEL),
            ("多水平", "多水平法", "适用于 2 或 3 水平质控，按整次检测给出联合结论。",
             ["一次填写全部水平，查看单水平图或合并图。", "支持部分水平换批及各水平实际批号追溯。"],
             ["2 / 3 水平", "联合判读", "月报"], "打开多水平法", "open_main_zscore_card", ZSCORE_ENTRY_LABEL),
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
    with st.expander("使用指南"):
        _render_frontline_user_guide()


def render_instant_placeholder_page() -> None:
    from pages.instant_page import render_instant_page
    render_instant_page()
