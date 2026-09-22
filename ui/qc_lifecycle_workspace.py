"""QC lot lists, current details and explicit operation dialogs."""
from __future__ import annotations

from datetime import datetime
from functools import partial
from hashlib import sha1
import json
from uuid import uuid4

import pandas as pd
import streamlit as st
from services.search_service import fuzzy_match, SEARCH_HELP

from services.material_workflow_service import concentration_label, material_label
from services.project_config_service import QC_METHOD_LABELS
from services.qc_lifecycle_edit_service import (
    get_qc_binding_context, get_qc_config_context, list_qc_lifecycle_configs,
    parallel_target, replacement_materials, save_qc_lifecycle_action,
)
from services.terminology_service import QC_USAGE_LABELS

MODAL_KEY = 'qc_lifecycle_dialog'
_TITLES = {'prepare': '准备新质控品批次', 'verify': '登记质控批号验证',
           'state': '调整质控品使用状态', 'combination': '更换部分水平的质控批号'}


def _prefix(state):
    return 'qcl_' + state['token'] + '_'


def _field(state, name, label, widget='text_input', **kwargs):
    key = _prefix(state) + name
    if key not in st.session_state:
        st.session_state[key] = state['draft'][name]
    if widget == 'selectbox' and None in kwargs.get('options', []):
        kwargs.setdefault('placeholder', kwargs['format_func'](None))
    state['draft'][name] = getattr(st, widget)(label, key=key, **kwargs)
    return state['draft'][name]


def open_qc_lifecycle_dialog(kind, context_id):
    if kind not in _TITLES:
        raise ValueError('请选择要进行的操作。')
    context = get_qc_config_context(context_id) if kind == 'prepare' else get_qc_binding_context(context_id)
    now = datetime.now().replace(microsecond=0)
    draft = {'when': now, 'person': '', 'reason': ''}
    if kind == 'prepare':
        draft.update(target=None, items=[])
    elif kind == 'verify':
        draft.update(lot=None, conclusion=None, evidence='')
    elif kind == 'state':
        draft['state'] = context['state'] if context['state'] in ('parallel', 'active', 'ended') else 'parallel'
        draft.update({f'verification_{lot_id}': None for lot_id in context['actual_lots']})
    else:
        for i, level in enumerate(context['source']['levels']):
            draft[f'level_{i}'] = level['qc_level_id']
            draft[f'verification_{i}'] = None
    st.session_state[MODAL_KEY] = {'kind': kind, 'context_id': int(context_id), 'context': context,
        'fingerprint': context['fingerprint'], 'token': uuid4().hex, 'initial': dict(draft), 'draft': draft,
        'discard': False, 'review_after_close': None}
    st.rerun(scope='app')


def _open_review(config_id):
    from ui.common import open_global_page
    st.session_state['v11_pending_existing_config_id'] = int(config_id)
    st.session_state['v11_quality_review_notice'] = '请核对质控品批号、质量目标及各水平的均值和标准差，再确认批次设置。旧批次检测结果保留。'
    open_global_page('show_project_management_page')


def _close(review_id=None):
    state = st.session_state.pop(MODAL_KEY, None)
    if state:
        st.session_state.setdefault('qcl_cleanup', []).append(_prefix(state))
    if review_id is not None:
        _open_review(review_id)
    st.rerun(scope='app')


def _cancel(state, review_id=None):
    if state['draft'] != state['initial']:
        state['discard'] = True
        state['review_after_close'] = review_id
        st.rerun(scope='app')
    _close(review_id)


def _verification_label(row):
    return f"{'通过' if row['conclusion'] == 'pass' else '未通过'}｜{row['confirmed_at']}｜{row['confirmed_by']}｜{row['evidence']}"


def _verification_choice(state, field, label, rows):
    choices = {row['id']: _verification_label(row) for row in sorted(rows, key=lambda row: (row['confirmed_at'], row['id']), reverse=True)}
    return _field(state, field, label, 'selectbox', options=[None] + list(choices),
                  format_func=lambda value: '请选择验证记录' if value is None else choices[value])


def _prepare_fields(state):
    context, draft = state['context'], state['draft']
    lots = {row['id']: row for row in context['lots'] if not row['is_disabled']
            and row['id'] != context['config']['qc_material_lot_id']}
    target = _field(state, 'target', '新质控品批号 *', 'selectbox', options=[None] + list(lots),
        format_func=lambda value: '请选择新批号' if value is None else f"{lots[value]['lot_no']}｜效期 {lots[value]['expiry_date'] or '未填写'}")
    existing, eligible = parallel_target(context, target) if target is not None else (None, [])
    if existing:
        st.info('此批号已有待确认批次，可打开核对；尚未加入的检验项目可在下方选择。' if eligible
                else '此批号已有批次，请打开已有批次核对或继续使用。')
        if st.button('打开已有新批次核对', key='qcl_open_existing'):
            _cancel(state, existing['id'])
    names = {row['source_template_item_id']: row['test_item_name'] + ' · ' + QC_METHOD_LABELS[row['qc_method']] for row in eligible}
    chosen = [item for item in draft['items'] if item in names]
    if chosen != draft['items']:
        draft['items'] = chosen
        st.session_state[_prefix(state) + 'items'] = chosen
    _field(state, 'items', '本次更换质控批号的检验项目 *', 'multiselect', options=list(names), format_func=names.get, placeholder='请选择检验项目')
    if not lots:
        st.info('请先在基础资料中登记该质控品的新批号和浓度水平。')
    st.caption('准备后进入新批次核对。质量目标及各水平设置确认后再录入，旧批检测结果保留。')
    return {'target_qc_lot_id': target, 'template_item_ids': draft['items']}


def _verify_fields(state):
    context = state['context']
    lots = {row['id']: row for row in context['lots'] if not row['is_disabled']}
    lot = _field(state, 'lot', '待验证质控品批号 *', 'selectbox', options=[None] + list(lots),
        format_func=lambda value: '请选择批号' if value is None else f"{lots[value]['lot_no']}｜效期 {lots[value]['expiry_date'] or '未填写'}")
    conclusion = _field(state, 'conclusion', '验证结论 *', 'selectbox', options=[None, 'pass', 'fail'],
        format_func=lambda value: {None: '请选择验证结论', 'pass': '通过', 'fail': '未通过'}[value])
    evidence = _field(state, 'evidence', '新旧批同时使用观察、控制参数及适用性验证依据 *', 'text_area')
    return {'qc_lot_id': lot, 'conclusion': conclusion, 'evidence': evidence}


def _state_fields(state):
    context = state['context']
    target = _field(state, 'state', '目标使用状态 *', 'selectbox', options=['parallel', 'active', 'ended'], format_func=QC_USAGE_LABELS.get)
    selected = {}
    if target == 'active':
        st.caption('正式使用须逐一核对全部实际批号，采用生效时间之前最新通过的验证，并核对效期与均值和标准差。')
        for lot_id, lot in context['actual_lots'].items():
            selected[lot_id] = _verification_choice(state, f'verification_{lot_id}', f"关联验证：{lot['lot_no']} *",
                [v for v in context['verifications'] if v['qc_lot_id'] == lot_id])
    else:
        st.caption('新旧批同时使用或停止使用无需关联验证。停止使用后原有检测结果仍可查询。')
    return {'state': target, 'verification_ids': selected}


def _combination_fields(state):
    context, draft = state['context'], state['draft']
    chosen, verification_ids = [], {}
    for i, level in enumerate(context['source']['levels']):
        choices = {row['id']: row for row in replacement_materials(context, i)}
        if draft[f'level_{i}'] is not None and draft[f'level_{i}'] not in choices:
            draft[f'level_{i}'] = None
            st.session_state[_prefix(state) + f'level_{i}'] = None
        old_lot = next((row for row in context['levels'] if row['id'] == level['qc_level_id']), {})
        st.caption(f"第 {i + 1} 个水平：{concentration_label(old_lot)}")
        lid = _field(state, f'level_{i}', '质控品浓度水平及批号 *', 'selectbox', options=[None] + list(choices),
                     format_func=lambda value, choices=choices: '请选择质控品批号' if value is None else material_label(choices[value]))
        chosen.append(lid)
        if lid is not None and lid != level['qc_level_id']:
            lot_id = choices[lid]['qc_material_lot_id']
            records = [v for v in context['verifications'] if v['qc_lot_id'] == lot_id]
            prior = draft[f'verification_{i}']
            if prior is not None and prior not in {v['id'] for v in records}:
                draft[f'verification_{i}'] = None
                st.session_state[_prefix(state) + f'verification_{i}'] = None
            verification_ids[lid] = _verification_choice(state, f'verification_{i}', f'第 {i + 1} 个水平换批验证 *', records)
    st.caption('新组合独立收集检测结果。未更换水平的原参数保留为参考，所有水平参数和质量目标仍需重新核对。')
    return {'level_ids': chosen, 'verification_ids': verification_ids}


@st.dialog('质控品批号', width='large', dismissible=False)
def _render_dialog():
    state = st.session_state.get(MODAL_KEY)
    if not state:
        return
    st.subheader(_TITLES[state['kind']])
    if state['discard']:
        st.warning('本次填写尚未保存，是否放弃？')
        left, right = st.columns(2)
        if left.button('继续填写', key='qcl_continue', type='primary', width='stretch'):
            state['discard'] = False
            st.rerun(scope='app')
        if right.button('放弃填写', key='qcl_discard', width='stretch'):
            _close(state['review_after_close'])
        return
    context = state['context']
    if state['kind'] == 'prepare':
        st.caption('来源批次：' + context['config']['config_name'])
    else:
        st.caption(context['source']['test_item_name'] + '｜' + context['source']['instrument_name'])
        st.caption('实际质控品：' + '；'.join(f"{lot['lot_no']}（{'、'.join(lot['level_names'])}）" for lot in context['actual_lots'].values()))
    values = {'prepare': _prepare_fields, 'verify': _verify_fields, 'state': _state_fields,
              'combination': _combination_fields}[state['kind']](state)
    when = _field(state, 'when', '验证完成时间 *' if state['kind'] == 'verify' else '生效时间 *', 'datetime_input')
    person = _field(state, 'person', '确认人 *')
    if state['kind'] == 'verify':
        values.update(confirmed_at=when, confirmed_by=person)
    else:
        reason = _field(state, 'reason', '换批原因或状态调整依据 *', 'text_area')
        values.update(effective_at=when, operator=person, reason=reason)
    left, right = st.columns(2)
    if left.button('取消', key='qcl_cancel', width='stretch'):
        _cancel(state)
    label = {'prepare': '准备新批次并核对', 'verify': '保存验证记录', 'state': '确认使用状态', 'combination': '建立新组合并核对'}[state['kind']]
    if right.button(label, key='qcl_save', type='primary', width='stretch'):
        try:
            if state['kind'] == 'prepare' and values['target_qc_lot_id'] is None:
                raise ValueError('请选择新质控品批号。')
            if state['kind'] == 'verify' and values['qc_lot_id'] is None:
                raise ValueError('请选择待验证质控品批号。')
            result = save_qc_lifecycle_action(state['kind'], state['context_id'], values, expected_fingerprint=state['fingerprint'])
        except (ValueError, TypeError) as error:
            st.error(str(error))
        else:
            st.session_state['qc_lifecycle_notice'] = '已保存，原有检测结果和批号记录保留。'
            st.session_state['qc_lifecycle_version'] = st.session_state.get('qc_lifecycle_version', 0) + 1
            _close(result if state['kind'] in ('prepare', 'combination') else None)


def render_pending_qc_lifecycle_dialog():
    for prefix in st.session_state.pop('qcl_cleanup', []):
        for key in list(st.session_state):
            if key.startswith(prefix):
                st.session_state.pop(key, None)
    if st.session_state.get(MODAL_KEY):
        _render_dialog()


def _select(widget_key, ids, state_key):
    rows = st.session_state.get(widget_key, {}).get('selection', {}).get('rows', [])
    st.session_state[state_key] = ids[rows[0]] if rows and 0 <= rows[0] < len(ids) else None


def _table(rows, columns, state_key):
    ids = [row['id'] for row in rows]
    selected = st.session_state.get(state_key)
    if selected not in ids:
        st.session_state[state_key] = selected = None
    signature = sha1((str(ids) + str(st.session_state.get('qc_lifecycle_version', 0))).encode()).hexdigest()[:10]
    key = state_key + '_table_' + signature
    frame = pd.DataFrame(rows, columns=['id', *columns])[list(columns)].rename(columns=columns)
    st.dataframe(frame, hide_index=True, width='stretch', height=min(300, max(115, 36 * len(rows) + 38)),
        key=key, selection_mode='single-row', on_select=partial(_select, key, ids, state_key),
        selection_default={'selection': {'rows': [ids.index(selected)] if selected in ids else []}})
    return st.session_state.get(state_key)


def _render_binding_detail(context):
    binding, source = context['binding'], context['source']
    st.write('质控品使用状态：' + QC_USAGE_LABELS[context['state']])
    st.caption('批次资料状态：' + ('批次设置已确认' if binding['binding_status'] == 'active' else '当前设置不可用于录入'))
    profile = context['profile']
    st.caption('均值和标准差确认状态：' + (f"已生效 V{profile['version_no']}（{profile['effective_at']}）" if profile else '按本批次数据建立，尚无生效的确认版本'))
    plan = context['state_plan']
    if plan is None:
        st.caption('尚未登记使用状态变更，当前为初始使用状态，不表示换批验证已通过。')
    elif plan['state'] != context['state']:
        st.caption(f"另有待生效安排：{QC_USAGE_LABELS[plan['state']]}，生效时间 {plan['effective_at']}。")
    lot_rows = [{'浓度水平': '、'.join(lot['level_names']), '批号': lot['lot_no'], '效期': lot['expiry_date'],
                 '资料状态': '已停用' if lot['is_disabled'] or lot['level_disabled'] else '可用'} for lot in context['actual_lots'].values()]
    st.dataframe(pd.DataFrame(lot_rows), hide_index=True, width='stretch')
    left, middle, right = st.columns(3)
    for column, kind, label in ((left, 'verify', '登记批号验证'), (middle, 'state', '调整使用状态'), (right, 'combination', '部分水平换批')):
        disabled = kind == 'combination' and (binding['qc_method'] != 'zscore' or binding['binding_status'] != 'active')
        if column.button(label, key='qc_lifecycle_' + kind, disabled=disabled, width='stretch'):
            open_qc_lifecycle_dialog(kind, binding['id'])
    if context['verifications']:
        lot_names = {lot['id']: lot['lot_no'] for lot in context['lots']}
        history = [{'批号': lot_names.get(v['qc_lot_id'], '未记录'), '结论': '通过' if v['conclusion'] == 'pass' else '未通过',
                    '完成时间': v['confirmed_at'], '确认人': v['confirmed_by'], '依据': v['evidence']}
                   for v in reversed(context['verifications'])]
        with st.expander('质控批号验证记录'):
            st.dataframe(pd.DataFrame(history), hide_index=True, width='stretch')


def render_qc_lifecycle_workspace():
    notice = st.session_state.pop('qc_lifecycle_notice', None)
    if notice:
        st.success(notice)
    st.caption('在此登记同一质控品新旧批号的验证结果和使用状态。请分别查看各批检测数据，依据实验室的比对记录确认；此处不生成比对结论或比对报告。更换质控品产品请另建项目。')
    rows = list_qc_lifecycle_configs()
    settings = st.session_state.setdefault('qc_lifecycle_filters', {})
    projects = {row['template_id']: row['template_name'] for row in rows}
    for key, default in (('qc_lifecycle_search', ''), ('qc_lifecycle_project', None)):
        if key not in st.session_state:
            st.session_state[key] = settings.get(key, default)
    if st.session_state['qc_lifecycle_project'] not in projects:
        st.session_state['qc_lifecycle_project'] = None
    left, right = st.columns(2)
    query = left.text_input('搜索项目、批次或质控品批号', key='qc_lifecycle_search', help=SEARCH_HELP)
    project = right.selectbox('项目', [None] + list(projects), key='qc_lifecycle_project', placeholder='全部项目',
        format_func=lambda value: '全部项目' if value is None else projects[value])
    settings.update(qc_lifecycle_search=query, qc_lifecycle_project=project)
    if project is not None:
        rows = [row for row in rows if row['template_id'] == project]
    if query.strip():
        rows = [row for row in rows if fuzzy_match(query, *(row[field] for field in ('template_name', 'config_name', 'material_summary')))]
    config_id = _table(rows, {'template_name': '项目', 'config_name': '批次', 'material_summary': '质控品浓度水平及批号'}, 'qc_lifecycle_config_id')
    if config_id is None:
        st.info('请选择批次，查看检验项目及批号使用情况。')
        return
    context = get_qc_config_context(config_id)
    left, right = st.columns(2)
    modern = bool(context['config']['material_selection_mode'])
    if left.button('选择各水平的新批号' if modern else '准备新质控品批次', key='qc_lifecycle_prepare', width='stretch'):
        if modern:
            from ui.qc_replacement_workspace import open_qc_replacement_dialog
            open_qc_replacement_dialog(config_id)
        else:
            open_qc_lifecycle_dialog('prepare', config_id)
    if right.button('打开批次设置', key='qc_lifecycle_review', width='stretch'):
        _open_review(config_id)
        st.rerun()
    bindings = []
    item_names = {row['id']: row['test_item_name'] for row in context['items']}
    for binding in context['bindings']:
        if binding['lot_config_id'] != config_id:
            continue
        source = json.loads(binding['source_snapshot_json'] or '{}')
        bindings.append({**binding, 'test_item_name': item_names.get(binding['lot_config_item_id'], source.get('test_item_name', '')),
                         'method_label': QC_METHOD_LABELS.get(binding['qc_method'], '即时法'),
                         'lot_summary': '；'.join(material_label(level) for level in source.get('levels', []))})
    if not bindings:
        st.info('此批次尚不能录入检测结果，请先打开批次设置完成核对并确认。')
        return
    selected = _table(bindings, {'test_item_name': '检验项目', 'method_label': '质控方法', 'lot_summary': '各水平实际质控品'}, 'qc_lifecycle_binding_id')
    if selected is not None:
        try:
            _render_binding_detail(get_qc_binding_context(selected))
        except ValueError as error:
            st.info(str(error))
