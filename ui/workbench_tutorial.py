"""Short method-specific guidance, acknowledged independently in each library."""
from __future__ import annotations

import logging
import sqlite3

import streamlit as st

from services.onboarding_service import mark_workbench_tutorial_seen, should_show_workbench_tutorial


METHOD_DESCRIPTIONS = {
    'lj': ('单水平（L-J）',
        '使用一个浓度水平的质控品，以该水平的均值和标准差（SD）建立 L-J 图，观察连续检测结果的波动与偏移。'),
    'zscore': ('多水平法',
        '同次检测使用两个或三个不同浓度水平的质控品，各按自身均值和 SD 评价，结合本次和连续检测结果，按已启用的 Westgard 规则综合判读。'),
}


TUTORIALS = {
    'lj': ('单水平（L-J）新手教程', (
        '核对检验项目、质控品批号及该浓度水平的均值和 SD；建靶未完成时先积累建靶数据。',
        '填写检测时间、检测人和质控值，点击“保存检测结果”，在 L-J 图中查看结果相对均值和 SD 界限的位置。',
        '结合当前结果与连续检测记录查看判读；“质控报告”可生成所选期间的报告。',
    )),
    'zscore': ('多水平法新手教程', (
        '核对各浓度水平的质控品、原厂批号、均值和 SD；每个水平分别建立参数。',
        '同一次检测填写全部水平结果，点击“保存本次检测”。',
        '查看各水平的标准化偏差及本次检测的综合判读；“质控报告”可生成所选期间的报告。',
    )),
}


def _render_content(method: str) -> None:
    title, instructions = TUTORIALS[method]
    st.markdown('**' + title + '**')
    st.write(METHOD_DESCRIPTIONS[method][1])
    if method == 'zscore':
        st.caption('Z =（实测值－该水平的均值）÷ 该水平的 SD，用于在同一尺度上比较不同浓度水平。')
    for number, instruction in enumerate(instructions, 1):
        st.write(f'{number}. {instruction}')
    st.caption('1₂s 为警告信号，需结合其他已启用规则复核；出现失控时排查原因，并保留原始结果和处理记录。')


def render_workbench_tutorial(method: str) -> None:
    """Called only after the real workbench has a usable batch, never on setup."""
    if method not in TUTORIALS:
        return
    try:
        visible = should_show_workbench_tutorial(method)
    except (sqlite3.Error, OSError, ValueError):
        logging.exception('Workbench tutorial availability could not be read')
        return
    if not visible:
        return
    with st.container(border=True):
        _render_content(method)
        if st.button('知道了，开始录入', key='workbench_tutorial_seen_' + method, type='primary'):
            try:
                mark_workbench_tutorial_seen(method)
            except (sqlite3.Error, OSError, ValueError):
                logging.exception('Workbench tutorial acknowledgement could not be saved')
                st.error('阅读进度未保存，请重试。')
            else:
                st.rerun(scope='app')


def open_workbench_tutorial(*, rerun: bool = True) -> None:
    from ui.common import open_global_page
    open_global_page('show_workbench_tutorial_page', rerun=rerun)


def render_workbench_tutorial_page() -> None:
    """The Help entry can revisit either method without changing first-use state."""
    from ui.common import render_module_header
    if st.button('返回项目工作台', key='workbench_tutorial_home'):
        from ui.daily_navigation import return_to_project_workspace
        return_to_project_workspace()
    render_module_header('新手教程', tone='guide')
    for tab, method in zip(st.tabs(['单水平（L-J）', '多水平法']), TUTORIALS):
        with tab:
            _render_content(method)
    st.caption('质控水平按浓度区分；Westgard 多规则指联合使用多条判读规则。')
    st.caption('参考：Westgard [L-J 质控图](https://www.westgard.com/lessons/basic-qc-practices/lesson12.html)、'
               '[统计计算与 Z 值](https://www.westgard.com/lessons/basic-qc-practices/lesson14.html)、'
               '[多规则判读](https://www.westgard.com/lessons/basic-qc-practices/lesson18.html)。')
