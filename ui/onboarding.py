"""A resumable setup guide that opens the existing editors and workbenches."""
from __future__ import annotations

import logging
import sqlite3

import streamlit as st

from database import get_db_path
from ui.workbench_tutorial import METHOD_DESCRIPTIONS
from services.onboarding_service import (
    STEP_IDS, get_onboarding_method, get_onboarding_readiness, get_onboarding_state,
    save_onboarding_method, save_onboarding_progress, should_auto_show_onboarding,
)


STEP_TITLES = ('实验室信息', '仪器试剂管理', '新建项目')


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
        from services.project_setup_draft_service import load_project_setup_draft
        if load_project_setup_draft() is not None:
            return
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
    st.write('设置新报告中自动填写的**实验室、科室、检验员和复核员**。')
    if ready['unit']:
        st.caption('已填写实验室信息。')
    if st.button('填写实验室信息', key='onboarding_unit', type='primary', width='stretch'):
        _open_page('unit', 'show_settings_page')


def _render_materials(ready: dict) -> None:
    st.write('登记**仪器、试剂和质控品**，供项目设置和检测录入选择。已有资料可直接使用。')
    actions = (
        ('仪器', '登记和修改仪器名称、厂家、型号等资料。', '仪器管理', 'instrument', 'onboarding_instrument'),
        ('试剂', '管理试剂产品、实际批号、效期和使用记录。', '试剂管理', 'reagent_lot', 'onboarding_reagent'),
        ('质控品', '管理质控品产品、原厂批号、效期和浓度水平。', '质控品管理', 'qc_lot', 'onboarding_qc'),
    )
    for column, (label, instruction, button, flag, key) in zip(st.columns(3), actions):
        with column:
            st.markdown('**' + label + '**')
            st.caption(instruction)
            if ready[flag]:
                st.caption('已有登记')
            if st.button(button, key=key, width='stretch'):
                _open_page('materials', 'show_qc_materials_page',
                           reference={'instrument': 'instrument', 'reagent_lot': 'reagent'}.get(flag, ''))
    st.markdown('**单位资料准备（选用）**')
    st.caption('用一份Excel批量登记实验室、仪器、试剂和质控品资料。')
    if st.button('单位资料准备：下载填写表或导入Excel', key='onboarding_checklist', width='stretch'):
        if _leave('materials'):
            from ui.common import open_global_page
            from ui.lab_preparation import open_lab_preparation_dialog
            open_global_page('show_settings_page', rerun=False)
            open_lab_preparation_dialog()


def _render_project(ready: dict) -> None:
    st.markdown('建立项目的**检验项目、仪器、试剂、质控品批号和各水平**，设置**质量要求**。保存后可进入结果录入。')
    if ready['project']:
        st.caption('已有项目，可继续使用。')
    from ui.project_setup_draft import new_project_button_label
    if st.button(new_project_button_label(), key='onboarding_project', type='primary', width='stretch'):
        if _leave('project'):
            _project_home()
            from ui.project_dialogs import open_project_dialog
            open_project_dialog('project')


def _render_method_choice(state: dict) -> None:
    st.subheader('选择质控方法')
    st.caption('按本实验室的质控方案选择，后续项目可另选。')
    identity = str(get_db_path().resolve())
    if st.session_state.get('onboarding_method_choice_database') != identity:
        st.session_state['onboarding_method_choice_database'] = identity
        st.session_state.pop('onboarding_method_choice', None)
    choice = st.radio('本次设置', ['lj', 'zscore'], index=None,
        format_func=lambda value: '单水平（L-J）' if value == 'lj' else '多水平法',
        horizontal=True, key='onboarding_method_choice')
    for label, description in METHOD_DESCRIPTIONS.values():
        st.markdown(f'**{label}**：{description}')
    later, proceed = st.columns(2)
    if later.button('稍后再做', key='onboarding_skip', width='stretch'):
        if _save(state['step'], 'skipped'):
            st.session_state['onboarding_open'] = False
            st.session_state.pop('onboarding_return_pending', None)
            st.rerun(scope='app')
    if proceed.button('继续设置', key='onboarding_method_continue', type='primary',
                      width='stretch', disabled=choice is None):
        try:
            save_onboarding_method(choice)
        except (sqlite3.Error, OSError, ValueError):
            logging.exception('First-use method choice could not be saved')
            st.error('本次选择未保存，请重试。')
        else:
            st.rerun(scope='app')


@st.dialog('首次引导', width='large', dismissible=False)
def _render_dialog() -> None:
    try:
        state = get_onboarding_state()
        ready = get_onboarding_readiness()
        method = get_onboarding_method()
    except (sqlite3.Error, OSError, ValueError):
        logging.exception('First-use guide could not be read')
        st.error('引导暂时无法打开，请关闭后重试。')
        if st.button('关闭', key='onboarding_read_close'):
            st.session_state['onboarding_open'] = False
            st.rerun(scope='app')
        return
    # Old active progress resumes at its saved step. Ask for a method when that
    # flow reaches project setup; skipped/completed progress never auto-restarts.
    if not ready['project'] and method is None and (state['status'] == 'new' or state['step'] == 'project'):
        _render_method_choice(state)
        return
    step = state['step']
    index = STEP_IDS.index(step)
    for column, target, title in zip(st.columns(len(STEP_IDS)), STEP_IDS, STEP_TITLES):
        if column.button(title, key='onboarding_step_' + target, width='stretch',
                         type='primary' if target == step else 'secondary'):
            if _save(target):
                st.rerun(scope='app')
    from ui.workflow_steps import render_workflow_step
    render_workflow_step(index + 1, len(STEP_IDS), STEP_TITLES[index])
    {'unit': _render_unit, 'materials': _render_materials,
     'project': _render_project}[step](ready)
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
    elif forward.button('完成引导', key='onboarding_finish', width='stretch'):
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
        if st.button('继续首次引导', key='onboarding_continue', type='primary'):
            open_onboarding()
