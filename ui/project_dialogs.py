"""Project and test-item dialogs with isolated drafts and explicit save boundaries."""
from __future__ import annotations

from uuid import uuid4
import pandas as pd
import streamlit as st

from services.project_config_service import get_project_template, list_template_items, INPUT_VALUE_TYPE_LABELS
from services.project_workspace_service import (
    save_project_details, save_single_test_item, change_project_status,
    remove_test_item, project_identity_locked,
)

MODAL_KEY = 'project_workspace_dialog'


def _plain(row):
    return {k: None if v is None or (not isinstance(v, (dict, list)) and pd.isna(v)) else v
            for k, v in dict(row).items()}


def open_project_dialog(kind, template_id=None, item_id=None):
    # Historical callers must also enter the editor; no single-step status path.
    if kind in ('disable', 'restore'):
        kind = 'project'
    template = _plain(get_project_template(template_id)) if template_id is not None else {}
    if kind in ('item', 'remove_item', 'quality'):
        items = list_template_items(template_id)
        matches = items[items.id == item_id] if item_id is not None else pd.DataFrame()
        draft = _plain(matches.iloc[0]) if not matches.empty else {
            'test_item_id': None, 'qc_method': template['default_qc_method'], 'input_value_type': 'raw',
            'unit_id': None, 'method_id': template['default_method_id'],
            'reagent_id': template['default_reagent_id'], 'level_count': template['default_level_count'],
            'target_n': 20, 'cv_limit': None, 'quality_target_source_text': '', 'notes': '',
        }
    else:
        draft = template or {'template_name': '', 'lab_instrument_id': None, 'qc_material_id': None,
            'default_reagent_id': None, 'default_qc_method': 'lj', 'default_method_id': None,
            'default_level_count': 1, 'project_group': '', 'notes': ''}
    st.session_state[MODAL_KEY] = dict(kind=kind, template_id=template_id, item_id=item_id,
        revision=template.get('revision_no'), draft=dict(draft), initial=dict(draft), token=uuid4().hex,
        discard=False, template_name=template.get('template_name', ''))
    st.rerun()


def _finish(message=''):
    st.session_state.pop(MODAL_KEY, None)
    if message:
        st.session_state['project_workspace_notice'] = message
    st.session_state['project_table_version'] = st.session_state.get('project_table_version', 0) + 1
    st.rerun(scope='app')


def _key(ctx, field):
    key = 'project_draft_' + ctx['token'] + '_' + field
    if key not in st.session_state:
        st.session_state[key] = ctx['draft'].get(field)
    return key


def _text(ctx, field, label, *, multiline=False, **kwargs):
    ctx['draft'][field] = (st.text_area if multiline else st.text_input)(label, key=_key(ctx, field), **kwargs)


def _choice(ctx, field, label, frame, label_fields, *, disabled=False, optional=False):
    current = ctx['draft'].get(field)
    rows = {int(r['id']): r for r in frame.to_dict('records')
            if not r.get('is_disabled', 0) or r['id'] == current}
    if current not in rows:
        ctx['draft'][field] = None
    key = _key(ctx, field)
    if st.session_state[key] not in [None, *rows]:
        st.session_state[key] = None
    ctx['draft'][field] = st.selectbox(label, [None, *rows], key=key, disabled=disabled, filter_mode='fuzzy',
        placeholder='逐项设置' if optional else '请选择',
        format_func=lambda value: ('逐项设置' if optional else '请选择') if value is None else
        '｜'.join(str(rows[value].get(f) or '') for f in label_fields if rows[value].get(f)))


def _qc_choices(ctx, *, defaults=False):
    draft = ctx['draft']
    field = 'default_qc_method' if defaults else 'qc_method'
    count_field = 'default_level_count' if defaults else 'level_count'
    kind_key = 'project_kind_' + ctx['token']
    if kind_key not in st.session_state:
        st.session_state[kind_key] = '多水平' if draft[field] == 'zscore' else '单水平'
    kind = st.radio('默认质控方法' if defaults else '质控方法', ['单水平', '多水平'],
        format_func=lambda value: '单水平（LJ）' if value == '单水平' else '多水平法',
        horizontal=True, key=kind_key)
    if kind == '多水平':
        draft[field] = 'zscore'
        key = _key(ctx, count_field)
        if st.session_state[key] not in (2, 3):
            st.session_state[key] = 2
        draft[count_field] = st.selectbox('质控水平数', [2, 3], key=key)
    else:
        instant_key = 'project_instant_' + ctx['token']
        if instant_key not in st.session_state:
            st.session_state[instant_key] = draft[field] == 'instant'
        use_instant = st.checkbox('使用即时法', key=instant_key,
            help='质控结果较少、尚未建立均值和标准差时，可选择即时法。')
        draft[field] = 'instant' if use_instant else 'lj'
        draft[count_field] = 1
        if draft[field] == 'instant':
            st.caption('累计 3 个有效结果后开始即时法判断；达到 20 个后，可确认转入 LJ法。')
        else:
            st.caption('可用本批质控结果建立均值和标准差，也可使用经确认的均值和标准差。')


def _cancel(ctx):
    if ctx['draft'] != ctx['initial']:
        ctx['discard'] = True
        st.rerun()
    _finish()


def _render_discard(ctx):
    st.warning('本次填写尚未保存。是否放弃修改？')
    left, right = st.columns(2)
    if left.button('继续编辑', type='primary', key='project_continue_edit'):
        ctx['discard'] = False
        st.rerun()
    if right.button('放弃修改', key='project_discard_changes'):
        _finish()


def _render_project_form(ctx):
    from services.master_data_service import list_lab_instruments, list_qc_materials, list_reagents, list_methods
    st.subheader('编辑项目' if ctx['template_id'] else '新建项目')
    if ctx['initial'].get('is_disabled'):
        _render_disabled_project(ctx)
        return
    locked = ctx['template_id'] is not None and project_identity_locked(ctx['template_id'])
    _text(ctx, 'template_name', '项目名称 *')
    left, right = st.columns(2)
    with left:
        _choice(ctx, 'lab_instrument_id', '仪器 *', list_lab_instruments(include_disabled=True), ['display_name'], disabled=locked)
        _choice(ctx, 'default_reagent_id', '默认试剂 *', list_reagents(include_disabled=True), ['generic_name', 'trade_name'])
    with right:
        _choice(ctx, 'qc_material_id', '质控品 *', list_qc_materials(include_disabled=True), ['generic_name', 'trade_name'], disabled=locked)
        _choice(ctx, 'default_method_id', '默认方法学', list_methods(include_disabled=True), ['method_name'], optional=True)
    if locked:
        st.caption('已添加检验项目或批次，不能更换仪器和质控品。如需更换，请新建项目。')
    _text(ctx, 'project_group', '分组', placeholder='例如：血筛、生化、分子')
    _qc_choices(ctx, defaults=True)
    st.caption('默认设置只带入以后新增的检验项目，各项可分别调整。')
    _text(ctx, 'notes', '项目备注', multiline=True)
    cancel, save = st.columns(2)
    if cancel.button('取消', key='project_dialog_cancel'):
        _cancel(ctx)
    if save.button('保存项目', type='primary', key='project_dialog_save'):
        try:
            tid = save_project_details(ctx['draft'], template_id=ctx['template_id'], expected_revision=ctx['revision'])
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state['workspace_project_id'] = tid
            st.session_state['home_selected_project_id'] = tid
            st.session_state['v11_selected_template_id'] = tid
            _finish('项目已保存，请添加检验项目。' if not ctx['template_id'] else '项目资料已保存。')
    if ctx['template_id'] is not None:
        st.divider()
        if ctx['draft'] != ctx['initial']:
            st.caption('停用按已保存的项目资料执行，本次未保存的修改不会一并保存。')
        if st.button('停用项目', key='project_editor_disable'):
            _begin_project_status(ctx, disabled=True)


def _render_disabled_project(ctx):
    saved = ctx['initial']
    st.info('项目已停用，资料仅供查看。恢复后请重新确认项目设置。')
    for field, label in [('template_name', '项目名称'), ('instrument_name', '仪器'),
                         ('qc_material_name', '质控品'), ('default_reagent_name', '默认试剂'),
                         ('project_group', '分组'), ('notes', '项目备注')]:
        st.write(f'{label}：{saved.get(field) or "未填写"}')
    cancel, restore = st.columns(2)
    if cancel.button('取消', key='project_dialog_cancel'):
        _finish()
    if restore.button('恢复项目', key='project_editor_restore'):
        _begin_project_status(ctx, disabled=False)


def _begin_project_status(ctx, *, disabled):
    ctx['status_confirmation'] = dict(
        template_id=ctx['template_id'], revision=ctx['revision'],
        saved_name=ctx['initial']['template_name'], editor_token=ctx['token'],
        disabled=disabled, step='reason' if disabled else 'restore',
        reason='', first_confirmed=False, token=uuid4().hex,
    )
    st.rerun()


def _render_project_status_confirmation(ctx):
    confirmation = ctx['status_confirmation']
    if (confirmation['template_id'] != ctx['template_id'] or
            confirmation['revision'] != ctx['revision'] or confirmation['editor_token'] != ctx['token']):
        ctx.pop('status_confirmation', None)
        st.rerun()
    disabled = confirmation['disabled']
    st.subheader(('停用项目' if disabled else '恢复项目') + '：' + confirmation['saved_name'])
    st.info('原有批次、检测结果和报告仍可查询。')
    if disabled and ctx['draft'] != ctx['initial']:
        st.caption('仅停用当前已保存的项目；本次未保存的修改不会一并保存。')
    if disabled and confirmation['step'] == 'reason':
        confirmation['reason'] = st.text_input('停用原因 *', value=confirmation['reason'],
            key='project_disable_reason_' + confirmation['token'])
        cancel, first = st.columns(2)
        if cancel.button('取消，返回编辑', type='primary', key='project_status_cancel'):
            ctx.pop('status_confirmation', None)
            st.rerun()
        if first.button('确认停用', key='project_disable_first_confirm'):
            if not confirmation['reason'].strip():
                st.error('请填写停用原因。')
            else:
                confirmation['reason'] = confirmation['reason'].strip()
                confirmation['first_confirmed'] = True
                confirmation['step'] = 'final'
                st.rerun()
        return
    if disabled and not (confirmation['step'] == 'final' and confirmation['first_confirmed'] and confirmation['reason']):
        confirmation.update(step='reason', first_confirmed=False)
        st.rerun()
    if disabled:
        st.warning('请再次确认停用。停用后不能继续在此项目下新增检测。')
        st.write('停用原因：' + confirmation['reason'])
    else:
        st.caption('恢复后请重新确认项目设置。')
    cancel, final = st.columns(2)
    if cancel.button('取消，返回编辑', type='primary', key='project_status_cancel'):
        ctx.pop('status_confirmation', None)
        st.rerun()
    button_key = 'project_disable_final_confirm_' + confirmation['token'] if disabled else 'project_restore_confirm'
    if final.button('再次确认停用' if disabled else '确认恢复', key=button_key):
        try:
            change_project_status(confirmation['template_id'], expected_revision=confirmation['revision'],
                                  disabled=disabled, reason=confirmation['reason'])
        except ValueError as exc:
            st.error(str(exc))
        else:
            if disabled:
                st.session_state.pop('workspace_project_id', None)
            _finish('项目已停用。' if disabled else '项目已恢复，请重新确认项目设置。')


def _render_item_form(ctx):
    from services.master_data_service import list_test_items, list_units, list_methods, list_reagents
    st.subheader('编辑检验项目' if ctx['item_id'] else '添加检验项目')
    st.caption(ctx['template_name'])
    _choice(ctx, 'test_item_id', '检验项目 *', list_test_items(include_disabled=True), ['chinese_name', 'abbreviation', 'aliases'])
    left, right = st.columns(2)
    with left:
        _choice(ctx, 'method_id', '方法学 *', list_methods(include_disabled=True), ['method_name'])
        _choice(ctx, 'unit_id', '单位 *', list_units(include_disabled=True), ['symbol', 'unit_name'])
    with right:
        _choice(ctx, 'reagent_id', '试剂 *', list_reagents(include_disabled=True), ['generic_name', 'trade_name'])
        ctx['draft']['input_value_type'] = st.selectbox('输入值类型', list(INPUT_VALUE_TYPE_LABELS),
            format_func=INPUT_VALUE_TYPE_LABELS.get, key=_key(ctx, 'input_value_type'))
    _qc_choices(ctx)
    if ctx['draft']['qc_method'] == 'instant':
        ctx['draft']['target_n'] = 20
    else:
        ctx['draft']['target_n'] = st.number_input('建立均值和标准差所需数据点数', min_value=5, max_value=20, step=1, key=_key(ctx, 'target_n'))
    if ctx['draft']['test_item_id']:
        from ui.quality_applicability import render_draft_standard_preview
        render_draft_standard_preview(ctx['draft'])
    _text(ctx, 'notes', '备注', multiline=True)
    cancel, save = st.columns(2)
    if cancel.button('取消', key='project_dialog_cancel'):
        _cancel(ctx)
    if save.button('保存', type='primary', key='project_item_save'):
        try:
            iid = save_single_test_item(ctx['template_id'], ctx['draft'], expected_revision=ctx['revision'], item_id=ctx['item_id'])
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
        else:
            st.session_state[f"workspace_item_{ctx['template_id']}"] = iid
            # The next app run renders one new dialog, never a nested dialog.
            st.session_state['pending_project_quality_item'] = (ctx['template_id'], iid)
            _finish('检验项目已保存，请设置质量目标。')


def _render_confirmation(ctx):
    if ctx['kind'] != 'remove_item':
        st.error('请从编辑项目中选择停用或恢复。')
        if st.button('关闭', key='project_unsupported_confirmation_close'):
            _finish()
        return
    label = '移除检验项目'
    name = ctx['draft'].get('test_item_name')
    st.subheader(f'{label}：{name}')
    st.info('原有批次、检测结果和报告仍可查询。')
    reason = st.text_input('原因', key='project_confirm_reason')
    cancel, confirm = st.columns(2)
    if cancel.button('取消', type='primary', key='project_confirm_cancel'):
        _finish()
    if confirm.button('确认' + label, key='project_confirm_apply'):
        try:
            if not reason.strip():
                raise ValueError('请填写移除原因。')
            remove_test_item(ctx['template_id'], ctx['item_id'], expected_revision=ctx['revision'], reason=reason)
        except ValueError as exc:
            st.error(str(exc))
        else:
            _finish(label + '已完成。')


@st.dialog('项目设置', width='large', dismissible=False)
def render_project_dialog():
    ctx = st.session_state.get(MODAL_KEY)
    if not ctx:
        return
    if ctx['discard']:
        _render_discard(ctx)
    elif ctx.get('status_confirmation'):
        _render_project_status_confirmation(ctx)
    elif ctx['kind'] == 'project':
        _render_project_form(ctx)
    elif ctx['kind'] == 'item':
        _render_item_form(ctx)
    elif ctx['kind'] == 'quality':
        from ui.quality_targets import render_adoption
        st.subheader('质量目标 · ' + str(ctx['draft'].get('test_item_name', '')))
        render_adoption('project', ctx['item_id'], embedded=True)
        if st.button('返回项目', key='project_quality_close'):
            _finish()
    else:
        _render_confirmation(ctx)


def render_pending_project_dialog():
    pending = st.session_state.pop('pending_project_quality_item', None)
    if pending:
        open_project_dialog('quality', pending[0], pending[1])
    if st.session_state.get(MODAL_KEY):
        render_project_dialog()
