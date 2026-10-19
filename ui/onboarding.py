"""A resumable setup guide that opens the existing editors and workbenches."""
from __future__ import annotations

import logging
import sqlite3

import streamlit as st

from database import get_db_path
from services.onboarding_service import (
    STEP_IDS, get_onboarding_readiness, get_onboarding_state,
    save_onboarding_progress, should_auto_show_onboarding,
)


STEP_TITLES = ('单位信息', '准备资料', '建立项目', '开始检测')


def onboarding_is_busy() -> bool:
    return any(bool(value) for key, value in st.session_state.items()
               if key.endswith('_dialog') or key in {
                   'workspace_create_batch_for', 'batch_workspace_create',
                   'batch_status_confirmation',
               })


def prepare_onboarding() -> None:
    """Check once per database/session; never interrupt an existing editor."""
    identity = str(get_db_path().resolve())
    previous = st.session_state.get('onboarding_checked_database')
    if previous == identity:
        return
    if previous is not None:
        st.session_state.pop('onboarding_open', None)
        st.session_state.pop('onboarding_return_pending', None)
    st.session_state['onboarding_checked_database'] = identity
    from ui.common import GLOBAL_PAGE_SESSION_KEYS
    if (onboarding_is_busy()
            or any(st.session_state.get(key) for key in GLOBAL_PAGE_SESSION_KEYS)
            or st.session_state.get('top_level_method_selector', '主页') != '主页'):
        return
    try:
        if should_auto_show_onboarding():
            st.session_state['onboarding_open'] = True
    except (sqlite3.Error, OSError, ValueError):
        logging.exception('First-use guide availability check failed')


def open_onboarding() -> None:
    if not onboarding_is_busy():
        st.session_state['onboarding_open'] = True
        st.rerun(scope='app')


def _save(step: str, status: str = 'active') -> bool:
    try:
        save_onboarding_progress(step, status=status)
    except (sqlite3.Error, OSError, ValueError):
        logging.exception('First-use guide progress could not be saved')
        st.error('引导进度未保存，请重试。')
        return False
    return True


def _leave(step: str) -> bool:
    if not _save(step):
        return False
    st.session_state['onboarding_open'] = False
    st.session_state['onboarding_return_pending'] = True
    return True


def _open_page(step: str, page: str, *, reference: str = '') -> None:
    if not _leave(step):
        return
    if reference:
        from ui.common import open_reference_management
        open_reference_management(reference, close_menu=False)
    else:
        from ui.common import open_global_page
        open_global_page(page)


def _project_home() -> None:
    from ui.common import GLOBAL_PAGE_SESSION_KEYS
    for key in GLOBAL_PAGE_SESSION_KEYS:
        st.session_state[key] = False
    st.session_state['top_level_method_selector'] = '主页'
    st.session_state['pending_top_level_method'] = '主页'
    st.session_state.pop('workspace_project_id', None)
    st.session_state.pop('daily_return_page', None)


def _render_unit(ready: dict) -> None:
    st.write('填写**实验室、科室、检验员和复核员**，点击**保存设置**；'
             '在下方核对**当前数据保存位置**。')
    if ready['unit']:
        st.caption('已填写单位信息。')
    if st.button('填写单位信息', key='onboarding_unit', type='primary', width='stretch'):
        _open_page('unit', 'show_settings_page')


def _render_materials(ready: dict) -> None:
    st.write('先搜索已有资料，缺少的再补充。**批号和效期按包装填写。**')
    actions = (
        ('仪器', '仪器管理', 'instrument', 'onboarding_instrument'),
        ('试剂批号', '试剂管理', 'reagent_lot', 'onboarding_reagent'),
        ('质控品批号与水平', '质控品管理', 'qc_lot', 'onboarding_qc'),
    )
    for column, (label, button, flag, key) in zip(st.columns(3), actions):
        with column:
            st.markdown('**' + label + '**')
            st.caption('已登记' if ready[flag] else '待登记')
            if st.button(button, key=key, width='stretch'):
                _open_page('materials', 'show_qc_materials_page',
                           reference={'instrument': 'instrument', 'reagent_lot': 'reagent'}.get(flag, ''))
    if st.button('用清单一次准备', key='onboarding_checklist', width='stretch'):
        if _leave('materials'):
            from ui.common import open_global_page
            from ui.lab_preparation import open_lab_preparation_dialog
            open_global_page('show_settings_page', rerun=False)
            open_lab_preparation_dialog()


def _render_project(ready: dict) -> None:
    st.write('选择**检验项目、仪器、试剂和质控品**，核对质量要求后，点击**确认保存并进入录入**。')
    if ready['project']:
        st.caption('已有项目，可继续使用。')
    if st.button('新建项目', key='onboarding_project', type='primary', width='stretch'):
        if _leave('project'):
            _project_home()
            from ui.project_dialogs import open_project_dialog
            open_project_dialog('project')


def _render_start(ready: dict) -> None:
    st.markdown('1. 在**试剂管理**登记批号验证，通过后切换到本次使用的批号。\n'
                '2. 回到**项目工作台**，选择项目和批次。\n'
                '3. 填写**检测时间、检测人和结果**。LJ 和即时法点**保存检测结果**；'
                '多水平填齐各水平后点**保存本次检测**。')
    if not ready['project']:
        st.caption('还没有项目，可返回上一步新建。')
    if st.button('登记试剂批号验证', key='onboarding_verification', width='stretch'):
        _open_page('start', '', reference='reagent')


@st.dialog('首次引导', width='large', dismissible=False)
def _render_dialog() -> None:
    try:
        state = get_onboarding_state()
        ready = get_onboarding_readiness()
    except (sqlite3.Error, OSError, ValueError):
        logging.exception('First-use guide could not be read')
        st.error('引导暂时无法打开，请关闭后重试。')
        if st.button('关闭', key='onboarding_read_close'):
            st.session_state['onboarding_open'] = False
            st.rerun(scope='app')
        return
    step = state['step']
    index = STEP_IDS.index(step)
    st.caption(f'第 {index + 1} 步，共 {len(STEP_IDS)} 步')
    for column, target, title in zip(st.columns(4), STEP_IDS, STEP_TITLES):
        if column.button(title, key='onboarding_step_' + target, width='stretch',
                         type='primary' if target == step else 'secondary'):
            if _save(target):
                st.rerun(scope='app')
    st.subheader(STEP_TITLES[index])
    {'unit': _render_unit, 'materials': _render_materials,
     'project': _render_project, 'start': _render_start}[step](ready)
    st.divider()
    later, back, forward = st.columns(3)
    if later.button('稍后再做', key='onboarding_skip', width='stretch'):
        if _save(step, 'skipped'):
            st.session_state['onboarding_open'] = False
            st.session_state.pop('onboarding_return_pending', None)
            st.rerun(scope='app')
    if back.button('上一步', key='onboarding_back', width='stretch', disabled=index == 0):
        if _save(STEP_IDS[index - 1]):
            st.rerun(scope='app')
    if index < len(STEP_IDS) - 1:
        if forward.button('下一步', key='onboarding_next', width='stretch'):
            if _save(STEP_IDS[index + 1]):
                st.rerun(scope='app')
    elif forward.button('完成引导，进入项目工作台', key='onboarding_finish', type='primary', width='stretch'):
        if _save(step, 'completed'):
            st.session_state['onboarding_open'] = False
            st.session_state.pop('onboarding_return_pending', None)
            _project_home()
            st.rerun(scope='app')


def render_onboarding() -> None:
    if onboarding_is_busy():
        return
    if st.session_state.get('onboarding_open'):
        _render_dialog()
    elif st.session_state.get('onboarding_return_pending'):
        if st.button('继续首次引导', key='onboarding_continue'):
            open_onboarding()
