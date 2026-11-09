"""Separate dialogs for batch levels and quality requirements."""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pandas as pd
import streamlit as st

from services.batch_edit_service import get_batch_item_context, save_batch_item_settings
from services.material_workflow_service import material_label
from services.project_config_service import TARGET_SOURCE_LABELS
from services.quality_target_service import decode

MODAL_KEY = 'batch_item_dialog'
_CLEANUP_KEY = 'batch_dialog_cleanup_prefixes'


def _number(value):
    return None if value is None or pd.isna(value) else float(value)


def _assignment(row):
    return dict(qc_level_id=int(row['qc_level_id']), target_source=row.get('target_source') or 'building',
                target_mean=_number(row.get('target_mean')), target_sd=_number(row.get('target_sd')),
                target_confirmed=bool(row.get('target_confirmed')), notes=str(row.get('notes') or ''))


def _quality_prefix(item_id):
    return f'quality_lot_{int(item_id)}_'


def _restorable_quality_key(key):
    # Button triggers and uploader values cannot be assigned on rerender.
    return not key.endswith(('_adopt', '_save_pending', '_process_reset', '_confirm',
        '_save', '_relation_add', '_relation_edit', '_relation_status', '_keep',
        '_discard', '_cancel', '_file'))


def _clear_prefix(prefix):
    for key in list(st.session_state):
        if key.startswith(prefix):
            st.session_state.pop(key, None)


def open_batch_item_dialog(item_id, kind='levels'):
    if kind not in ('levels', 'quality', 'product'):
        raise ValueError('请选择水平设置、质控品核对或质量目标。')
    context = get_batch_item_context(int(item_id))
    levels = [_assignment(row) for row in context['levels']]
    initial = dict(selected=[row['qc_level_id'] for row in levels],
                   products=[int(row['qc_material_id']) for row in context['levels']],
                   levels={row['qc_level_id']: dict(row) for row in levels},
                   cv_limit=_number(context['item'].get('cv_limit')),
                   cv_source=str(context['item'].get('quality_target_source_text') or ''))
    if kind == 'quality':
        _clear_prefix(_quality_prefix(item_id))
    st.session_state[MODAL_KEY] = dict(item_id=int(item_id), kind=kind, token=uuid4().hex,
        revision=int(context['revision']), context=context, initial=initial,
        draft=dict(selected=list(initial['selected']), products=list(initial['products']),
                   levels={key: dict(value) for key, value in initial['levels'].items()},
                   cv_limit=initial['cv_limit'], cv_source=initial['cv_source']), discard=False)
    st.rerun(scope='app')


def _close(message='', *, rerun=True):
    state = st.session_state.pop(MODAL_KEY, None)
    if state:
        prefixes = st.session_state.get(_CLEANUP_KEY, [])
        prefixes.append(_quality_prefix(state['item_id']) if state['kind'] == 'quality' else
                        'batch_product_' + state['token'] if state['kind'] == 'product' else
                        'batch_level_' + state['token'])
        if state['kind'] == 'levels':
            prefixes.extend(('batch_control_' + state['token'] + '_',
                             'batch_control_register_' + state['token']))
        st.session_state[_CLEANUP_KEY] = prefixes
        st.session_state['v11_selected_lot_config_id'] = int(state['context']['config']['id'])
    if message:
        st.session_state['v11_copy_notice'] = message
    if rerun:
        st.rerun(scope='app')


def _key(state, suffix, value):
    key = 'batch_level_' + state['token'] + '_' + suffix
    if key not in st.session_state:
        st.session_state[key] = value
    return key


def _shown_assignments(draft):
    return [dict(draft['levels'][level_id], level_order=order)
            for order, level_id in enumerate(draft['selected'], start=1) if level_id is not None]


def _dirty(state):
    draft, initial = state['draft'], state['initial']
    if (draft['selected'] != initial['selected'] or draft.get('products', []) != initial.get('products', [])
            or draft['cv_limit'] != initial['cv_limit'] or draft['cv_source'] != initial['cv_source']):
        return True
    return any(draft['levels'][level_id] != initial['levels'].get(level_id) for level_id in draft['selected'] if level_id is not None)


def _readonly(context):
    st.info(context['read_only_reason'] or '本批次设置已确认，不能直接修改。')
    rows = []
    for row in context['levels']:
        rows.append({'水平': row['level_order'], '浓度水平': row['level_name'], '浓度编号': row.get('level_code', ''),
            '批号': row.get('lot_no', ''), '效期': row.get('expiry_date', ''),
            '均值和SD的设置方式': TARGET_SOURCE_LABELS.get(row['target_source'], row['target_source']),
            '均值': row.get('target_mean'), '标准差': row.get('target_sd'),
            '已确认': '是' if row.get('target_confirmed') else '否', '备注': row.get('notes', '')})
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
    if st.button('关闭', key='batch_levels_close', width='stretch'):
        _close()


def _render_levels(state, current):
    from ui.control_selection import restore_registration_inputs
    from ui.material_registration import render_material_registration_view
    registration_key = 'batch_control_register_' + state['token']
    result = restore_registration_inputs(registration_key)
    if result:
        position = int(result['slot'])
        selected = state['draft']['selected']
        selected += [None] * max(0, position + 1 - len(selected))
        selected[position] = result['level_id']
        products = state['draft'].setdefault('products', [])
        products += [None] * max(0, position + 1 - len(products))
        products[position] = result['product_id']
        prefix = 'batch_control_' + state['token'] + '_' + str(position)
        st.session_state[prefix + '_product'] = result['product_id']
        st.session_state[prefix + '_level'] = result['level_id']
        st.session_state[prefix + '_last_product'] = result['product_id']
        state['context'] = current
    if render_material_registration_view(registration_key):
        return
    original = state['context']
    item = original['item']
    st.subheader('水平设置 · ' + item['test_item_name'])
    st.caption(original['config']['config_name'])
    if not current['editable']:
        _readonly(current)
        return
    if int(current['revision']) != state['revision']:
        st.warning('这个批次已在别处修改。请关闭窗口后重新打开，再填写本次修改。')
    if state['discard']:
        st.warning('本次修改尚未保存，是否放弃？')
        left, right = st.columns(2)
        if left.button('继续编辑', type='primary', key='batch_levels_continue'):
            state['discard'] = False
            st.rerun(scope='app')
        if right.button('放弃修改', key='batch_levels_discard'):
            _close()
        return
    draft = state['draft']
    update_cv = not bool(decode(item.get('quality_goal_json')))
    preview_limit = st.session_state.get('batch_level_' + state['token'] + '_cv', draft['cv_limit'])
    available = {int(row.get('qc_level_id') or row['id']): dict(row) for row in original['available_levels']}
    active_ids = set(available)
    for row in original['levels']:
        available.setdefault(int(row['qc_level_id']), dict(row))
    for level_id, row in draft['levels'].items():
        for suffix, field in (('source','target_source'), ('mean','target_mean'), ('sd','target_sd'),
                              ('confirmed','target_confirmed'), ('notes','notes')):
            widget = 'batch_level_' + state['token'] + f'_{level_id}_{suffix}'
            if widget in st.session_state:
                row[field] = st.session_state[widget]
    from ui.control_selection import control_products, render_control_slot
    products = control_products()
    prior = list(draft['selected'])
    prior_products = list(draft.get('products', []))
    selected, selected_products = [], []
    for position in range(int(item['level_count'])):
        product_id, level_id = render_control_slot(
            prefix='batch_control_' + state['token'] + '_' + str(position), order=position+1,
            selected_level=prior[position] if position < len(prior) else None,
            selected_product=prior_products[position] if position < len(prior_products) else None,
            registration_key=registration_key, slot=position, products=products,
            materials=list(available.values()),
            preserve_prefixes=('batch_level_' + state['token'] + '_', 'batch_control_' + state['token'] + '_'))
        selected.append(level_id)
        selected_products.append(product_id)
    draft['selected'] = selected
    draft['products'] = selected_products
    complete = all(level_id is not None for level_id in selected) and len(set(selected)) == len(selected)
    if len(set(level_id for level_id in selected if level_id is not None)) != sum(level_id is not None for level_id in selected):
        st.warning('各水平不能选择同一条质控品记录。')
    def label(level_id):
        return material_label(available[level_id]) + ('（当前不可选用）' if level_id not in active_ids else '')
    rendered_levels = set()
    for order, level_id in enumerate(draft['selected'], start=1):
        if level_id is None or level_id in rendered_levels:
            continue
        rendered_levels.add(level_id)
        row = draft['levels'].setdefault(level_id, dict(qc_level_id=level_id, target_source='building',
            target_mean=None, target_sd=None, target_confirmed=False, notes=''))
        st.markdown(f'**水平 {order}：{label(level_id)}**')
        row['target_source'] = st.selectbox('均值和SD的设置方式', list(TARGET_SOURCE_LABELS),
            format_func=TARGET_SOURCE_LABELS.get, key=_key(state, f'{level_id}_source', row['target_source']))
        left, right, cv_column = st.columns(3)
        row['target_mean'] = left.number_input('均值', format='%.6f',
            key=_key(state, f'{level_id}_mean', row['target_mean']))
        row['target_sd'] = right.number_input('标准差', min_value=0.0, format='%.6f',
            key=_key(state, f'{level_id}_sd', row['target_sd']))
        with cv_column:
            if row['target_source'] == 'building':
                st.metric('设定变异系数（%）', '待建立')
            else:
                from ui.cv import render_target_cv
                render_target_cv(row['target_mean'], row['target_sd'], preview_limit,
                    input_value_type=str(item['input_value_type']), pending=row['target_source'] == 'copied_pending')
        row['target_confirmed'] = st.checkbox('已确认本水平的均值和SD',
            key=_key(state, f'{level_id}_confirmed', row['target_confirmed']))
        row['notes'] = st.text_area('备注', key=_key(state, f'{level_id}_notes', row['notes']))
        if row['target_source'] == 'building':
            st.caption('录入达到本批次要求的有效建靶点数后，按有效结果建立均值和标准差。')
    if update_cv:
        from ui.cv import CV_REQUIREMENT_HELP
        draft['cv_limit'] = st.number_input('允许不精密度（CV%，选填）', min_value=0.0, format='%.4f',
            help=CV_REQUIREMENT_HELP, key=_key(state, 'cv', draft['cv_limit']))
        draft['cv_source'] = st.text_input('允许不精密度依据（选填）', key=_key(state, 'cv_source', draft['cv_source']))
    else:
        st.caption('本检验项目的CV要求随质量目标保存，请通过“质量目标”核对。')
    cancel, save = st.columns(2)
    if cancel.button('取消', key='batch_levels_cancel', width='stretch'):
        if _dirty(state):
            state['discard'] = True
            st.rerun(scope='app')
        _close()
    if save.button('保存水平设置', type='primary', key='batch_levels_save', width='stretch', disabled=not complete):
        try:
            save_batch_item_settings(state['item_id'], _shown_assignments(draft),
                expected_revision=state['revision'], cv_limit=draft['cv_limit'],
                source_text=draft['cv_source'], update_cv=update_cv)
        except ValueError as error:
            st.error(str(error))
        else:
            _close('水平设置已保存。')


def _render_product_recheck(state, current):
    item, config = current['item'], current['config']
    st.caption('｜'.join(str(value) for value in (
        config.get('instrument_name'), item.get('reagent_name'), item.get('test_item_name'),
        item.get('method_name')) if value))
    from database import get_connection
    with get_connection() as connection:
        materials = [dict(row) for row in connection.execute('''SELECT a.level_order,
            q.qc_material_id,m.generic_name,m.catalog_no,f.display_name AS manufacturer_name,
            l.level_name,l.level_code,q.lot_no,q.expiry_date
            FROM qc_lot_config_item_levels a JOIN md_qc_levels l ON l.id=a.qc_level_id
            JOIN md_qc_material_lots q ON q.id=l.qc_material_lot_id
            JOIN md_qc_materials m ON m.id=q.qc_material_id
            LEFT JOIN md_manufacturers f ON f.id=m.manufacturer_id
            WHERE a.lot_config_item_id=? AND a.is_disabled=0 ORDER BY a.level_order''',
            (state['item_id'],))]
    if materials:
        st.dataframe(pd.DataFrame([{
            '水平': row['level_order'], '产品': row['generic_name'], '货号': row['catalog_no'],
            '厂家': row['manufacturer_name'], '浓度水平': row['level_name'],
            '浓度编号': row['level_code'], '原厂批号': row['lot_no'], '效期': row['expiry_date']
        } for row in materials]), hide_index=True, width='stretch')
    if not current['editable']:
        st.info(current['read_only_reason'])
        if st.button('关闭', key='batch_product_close'):
            _close()
        return
    if int(current['revision']) != state['revision']:
        st.warning('批次资料已修改，请关闭后重新打开核对。')
        if st.button('关闭', key='batch_product_close'):
            _close()
        return
    if state['discard']:
        st.warning('适用依据尚未保存，是否放弃？')
        keep, discard = st.columns(2)
        if keep.button('继续填写', key='batch_product_keep', type='primary'):
            state['discard'] = False
            st.rerun(scope='app')
        if discard.button('放弃修改', key='batch_product_discard'):
            _close()
        return
    product_ids = list(dict.fromkeys(row['qc_material_id'] for row in materials))
    if not product_ids:
        st.info('请先在“编辑水平设置”中选择各水平的质控品。')
        if st.button('关闭', key='batch_product_close'):
            _close()
        return
    from services.qc_product_recommendation_service import save_draft_product_choice
    from ui.qc_product_choice import render_product_reviews
    ctx = state.setdefault('product_recheck', dict(token=state['token'], draft={
        'product_choice_review': decode(item.get('product_choice_review_json'))}))
    prefix = 'batch_product_' + state['token']

    def save(review):
        save_draft_product_choice('lot', state['item_id'], review,
                                  expected_revision=state['revision'])
        _close('质控品适用性已确认。')

    render_product_reviews(ctx, product_ids, reagent_id=item.get('reagent_id'),
        lab_instrument_id=config['lab_instrument_id'], test_item_id=item['test_item_id'],
        method_id=item.get('method_id'), prefix=prefix,
        confirm_label='保存质控品适用依据', on_confirm=save, allow_relation_edit=False)
    if st.button('取消', key='batch_product_cancel', width='stretch'):
        if ctx.get(prefix + '_dirty'):
            state['discard'] = True
            st.rerun(scope='app')
        _close()


def _render_quality(state, current):
    st.subheader('质量目标 · ' + state['context']['item']['test_item_name'])
    st.caption(state['context']['config']['config_name'])
    if state['discard']:
        st.warning('本次质量目标修改尚未保存，是否放弃？')
        left, right = st.columns(2)
        if left.button('继续编辑', type='primary', key='batch_quality_continue'):
            state['discard'] = False
            state['restore_quality_inputs'] = True
            st.rerun(scope='app')
        if right.button('放弃修改', key='batch_quality_discard'):
            _close()
        return
    if state.pop('restore_quality_inputs', False):
        for key, value in state.get('quality_values', {}).items():
            if _restorable_quality_key(key):
                st.session_state[key] = value
    if int(current['revision']) != state['revision']:
        st.error('这个批次已在别处修改。请关闭窗口后重新打开“质量目标”。')
    else:
        from ui.quality_targets import render_adoption
        try:
            render_adoption('lot', state['item_id'], embedded=True, expected_revision=state['revision'],
                            on_saved=lambda: _close('本批次质量目标已保存，请继续确认批次设置。', rerun=False))
        except ValueError as error:
            st.error(str(error))
        prefix = _quality_prefix(state['item_id'])
        state['quality_values'] = {key: st.session_state[key] for key in st.session_state
                                   if key.startswith(prefix) and _restorable_quality_key(key)}
        state.setdefault('quality_initial', dict(state['quality_values']))
    if st.button('取消' if current['editable'] else '关闭', key='batch_quality_close', width='stretch'):
        if state.get('quality_values', {}) != state.get('quality_initial', {}) or state.get('product_recheck_dirty'):
            state['discard'] = True
            st.rerun(scope='app')
        _close()


@st.dialog('批次设置', width='large', dismissible=False)
def _render_dialog():
    state = st.session_state.get(MODAL_KEY)
    if not state:
        return
    try:
        current = get_batch_item_context(state['item_id'])
    except ValueError as error:
        st.error(str(error))
        if st.button('关闭', key='batch_missing_close'):
            _close()
        return
    if state['kind'] == 'levels':
        _render_levels(state, current)
    else:
        _render_quality(state, current)


@st.dialog('核对质控品', width='large', dismissible=False)
def _render_product_dialog():
    state = st.session_state.get(MODAL_KEY)
    if not state:
        return
    try:
        current = get_batch_item_context(state['item_id'])
    except ValueError as error:
        st.error(str(error))
        if st.button('关闭', key='batch_product_missing_close'):
            _close()
        return
    _render_product_recheck(state, current)


def render_batch_item_dialog():
    for prefix in st.session_state.pop(_CLEANUP_KEY, []):
        _clear_prefix(prefix)
    if st.session_state.get(MODAL_KEY):
        if st.session_state[MODAL_KEY]['kind'] == 'product':
            _render_product_dialog()
        else:
            _render_dialog()
