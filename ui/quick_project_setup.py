"""A single-item project, physical controls and quality review in one guided draft."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import logging
import sqlite3

import pandas as pd
import streamlit as st

from services.quick_project_setup_service import (
    QuickSetupDetailsError, prepare_quick_project_details,
    prepare_quick_project_setup, save_quick_project_setup,
)


def new_quick_setup_draft(qc_method='lj'):
    if qc_method not in ('lj', 'zscore', 'instant'):
        raise ValueError('请选择可用的质控方法。')
    count = 2 if qc_method == 'zscore' else 1
    return dict(template_name='', lab_instrument_id=None, qc_material_id=None,
        test_item_id=None, reagent_id=None, method_id=None, unit_id=None,
        qc_method=qc_method, input_value_type='raw', level_count=count, target_n=20,
        target_statistics_mode=None,
        cv_limit=None, quality_target_source_text='', project_group='', notes='',
        level_ids=[None] * count, level_product_ids=[None] * count, product_choice_reviews={}, product_choice_review={},
        product_manual_drafts={}, config_name='', quality={})


def _quality_context_identity(context):
    """Track selected business inputs, excluding reference sync timestamps."""
    bookkeeping = {'created_at', 'updated_at', 'last_seen_at', 'refreshed_at', 'target_statistics_mode'}

    def semantic(value):
        if isinstance(value, dict):
            return {key: semantic(item) for key, item in value.items() if key not in bookkeeping}
        if isinstance(value, (list, tuple)):
            return [semantic(item) for item in value]
        return value

    return sha256(json.dumps(semantic(context), sort_keys=True, default=str).encode()).hexdigest()[:12]


def _use_quality_context(ctx, context):
    identity = _quality_context_identity(context)
    if ctx.get('quick_quality_identity') != identity:
        ct_alert = deepcopy((ctx['draft'].get('quality') or {}).get('ct_alert'))
        ctx['draft']['quality'] = ({'ct_alert': ct_alert}
            if context['item'].get('input_value_type') == 'ct' and ct_alert else {})
        ctx.pop('quick_quality_inputs', None)
        ctx.pop('quick_preview', None)
        prefix = 'quick_quality_' + ctx['token'] + '_'
        ctx['setup_draft_widgets'] = {key: value
            for key, value in ctx.get('setup_draft_widgets', {}).items()
            if not key.startswith(prefix)}
        for key in list(st.session_state):
            if key.startswith(prefix):
                st.session_state.pop(key, None)
        ctx['quick_quality_identity'] = identity


def render_quick_project_setup(ctx):
    from ui.project_dialogs import _cancel
    from ui.material_registration import render_material_registration_view
    from ui.control_selection import restore_registration_inputs
    registration_key = 'quick_' + ctx['token']
    result = restore_registration_inputs(registration_key)
    if result:
        position = int(result['slot'])
        for field, value in (('level_ids', result['level_id']), ('level_product_ids', result['product_id'])):
            values = list(ctx['draft'].get(field, []))
            values += [None] * max(0, ctx['draft']['level_count'] - len(values))
            values[position] = value
            ctx['draft'][field] = values
        prefix = 'quick_control_' + ctx['token'] + '_' + str(position)
        st.session_state[prefix + '_product'] = result['product_id']
        st.session_state[prefix + '_level'] = result['level_id']
        st.session_state[prefix + '_last_product'] = result['product_id']
    if render_material_registration_view(registration_key):
        return
    step = ctx.get('quick_step', 'details')
    st.subheader('新建项目')
    from ui.workflow_steps import render_workflow_step
    number, title, instruction = {
        'details': (1, '填写检测设置', '选择检验项目、仪器、试剂和各水平质控品，并确认适用依据。'),
        'quality': (2, '填写质量要求', '填写检测条件和质量要求，核对本页设置后勾选确认。'),
        'preview': (3, '检查并保存项目', '检查下表，点击“保存项目并开始录入”后，建立项目和首个批次，并打开结果录入页。'),
    }[step]
    render_workflow_step(number, 3, title, instruction)
    if step == 'details':
        _render_details(ctx)
    elif step == 'quality':
        _render_quality(ctx)
    else:
        _render_preview(ctx)


def request_setup_mode(ctx, target):
    if ctx.get('dependency_registration'):
        st.warning('请先保存或取消下方资料登记，再切换设置方式。')
        return
    if any(key.endswith('_editor') and value for key, value in ctx.items()):
        st.warning('请先保存或取消下方配套记录，再切换设置方式。')
        return
    if ctx['draft'] != ctx['initial']:
        ctx['setup_mode_switch'] = target
    else:
        _convert_setup_mode(ctx, target)
    st.rerun()


def render_setup_mode_switch(ctx):
    target = ctx['setup_mode_switch']
    st.warning('切换后保留项目名称、仪器、试剂、质控品和默认方法。请重新选择检验项目和各水平，并填写质量要求。')
    keep, change = st.columns(2)
    if keep.button('继续当前设置', key='setup_mode_keep', type='primary'):
        ctx.pop('setup_mode_switch', None)
        st.rerun()
    if change.button('切换为多个检验项目' if target == 'group' else '切换为单检验项目', key='setup_mode_change'):
        _convert_setup_mode(ctx, target)
        st.rerun()


def _convert_setup_mode(ctx, target):
    from uuid import uuid4
    old = ctx['draft']
    if target == 'group':
        draft = dict(template_name=old['template_name'], lab_instrument_id=old['lab_instrument_id'],
            qc_material_id=old['qc_material_id'], default_reagent_id=old['reagent_id'],
            default_method_id=old['method_id'], default_qc_method=old['qc_method'],
            default_level_count=old['level_count'], project_group=old['project_group'], notes=old['notes'])
        kind = 'project'
    else:
        draft = new_quick_setup_draft()
        for field in ('template_name', 'lab_instrument_id', 'qc_material_id', 'project_group', 'notes'):
            draft[field] = old.get(field)
        for field, original in [('reagent_id', 'default_reagent_id'), ('method_id', 'default_method_id'),
                                ('qc_method', 'default_qc_method'), ('level_count', 'default_level_count')]:
            draft[field] = old[original]
        count = int(draft['level_count']) if draft['qc_method'] == 'zscore' else 1
        count = count if count in (1, 2, 3) and (draft['qc_method'] != 'zscore' or count > 1) else 2
        draft['level_count'] = count
        draft['level_ids'] = [None] * count
        draft['level_product_ids'] = [draft.get('qc_material_id')] + [None] * (count - 1)
        kind = 'quick'
    for cache in ('manual_choice_drafts', 'product_manual_drafts'):
        if old.get(cache):
            draft[cache] = deepcopy(old[cache])
    ctx.update(kind=kind, draft=draft, token=uuid4().hex, quick_step='details')
    for key in ('setup_mode_switch', 'quick_preview', 'quick_quality_identity', 'quick_quality_inputs', 'quick_auto_name_item'):
        ctx.pop(key, None)


def _render_details(ctx):
    from services.master_data_service import (list_test_items, list_lab_instruments,
        list_reagents, list_methods, list_units)
    from services.project_config_service import INPUT_VALUE_TYPE_LABELS
    from services.material_workflow_service import available_materials, material_label
    from ui.project_dialogs import _choice, _text, _key, _qc_choices, _cancel
    from ui.qc_product_choice import render_product_choice
    from ui.material_registration import render_material_registration_shortcut
    from ui.required_fields import required_field
    from ui.project_dependencies import project_dependency_pending, render_project_dependencies
    from ui.qc_product_choice import render_pending_product_relation_notice
    draft = ctx['draft']
    registration_key = 'quick_' + ctx['token']
    registration_pending = bool(st.session_state.get(registration_key + '_registration'))
    dependency_pending = project_dependency_pending(ctx)
    switch_requested = st.button('多个检验项目设置', key='quick_use_group', disabled=registration_pending or dependency_pending)
    tests = list_test_items(include_disabled=False)
    _choice(ctx, 'test_item_id', '检验项目 *', tests, ['chinese_name', 'abbreviation', 'aliases'])
    left, right = st.columns(2)
    with left:
        _choice(ctx, 'lab_instrument_id', '仪器 *', list_lab_instruments(), ['display_name'])
        _choice(ctx, 'reagent_id', '试剂 *', list_reagents(), ['generic_name', 'trade_name'])
        _choice(ctx, 'method_id', '方法学 *', list_methods(), ['method_name'])
    with right:
        selected_test = next((row for row in tests.to_dict('records') if row['id'] == draft['test_item_id']), None)
        if selected_test and ctx.get('quick_test_id') != draft['test_item_id']:
            ctx['quick_test_id'] = draft['test_item_id']
            if selected_test.get('default_unit_id') is not None and not pd.isna(selected_test['default_unit_id']):
                draft['unit_id'] = int(selected_test['default_unit_id'])
                st.session_state[_key(ctx, 'unit_id')] = draft['unit_id']
        with required_field(_key(ctx, 'unit_id'),
                missing=not st.session_state.get(_key(ctx, 'unit_id')), message='请选择单位。'):
            _choice(ctx, 'unit_id', '单位 *', list_units(), ['symbol', 'unit_name'])
        draft['input_value_type'] = st.selectbox('输入值类型', list(INPUT_VALUE_TYPE_LABELS),
            key=_key(ctx, 'input_value_type'), format_func=INPUT_VALUE_TYPE_LABELS.get)
        _qc_choices(ctx)
    if selected_test:
        auto_name = selected_test['chinese_name']
        name_key = _key(ctx, 'template_name')
        current_name = st.session_state[name_key]
        if ctx.get('quick_auto_name_item') != draft['test_item_id'] and current_name in ('', ctx.get('quick_auto_name', '')):
            draft['template_name'] = auto_name
            st.session_state[name_key] = auto_name
        ctx['quick_auto_name'] = auto_name
        ctx['quick_auto_name_item'] = draft['test_item_id']
    with required_field(_key(ctx, 'template_name'),
            missing=not str(st.session_state.get(_key(ctx, 'template_name')) or '').strip(),
            message='请填写项目名称。'):
        _text(ctx, 'template_name', '项目名称 *')
    if draft['qc_method'] == 'instant':
        draft['target_n'] = 20
    else:
        draft['target_n'] = st.number_input('建靶所需有效点数', min_value=5, max_value=20,
            step=1, key=_key(ctx, 'target_n'))
    from ui.target_statistics import render_target_statistics_mode
    draft['target_statistics_mode'] = render_target_statistics_mode(draft.get('target_statistics_mode'),
        key=_key(ctx, 'target_statistics_mode'))
    st.markdown('**各水平质控品**')
    from ui.control_selection import control_products, render_control_slot
    from ui.qc_product_choice import render_product_reviews
    products = control_products()
    materials = available_materials().to_dict('records')
    previous_levels = list(draft.get('level_ids', []))
    previous_products = list(draft.get('level_product_ids', []))
    level_ids, product_ids = [], []
    for position in range(draft['level_count']):
        level = previous_levels[position] if position < len(previous_levels) else None
        product = previous_products[position] if position < len(previous_products) else (
            draft.get('qc_material_id') if position == 0 else None)
        product, level = render_control_slot(
            prefix='quick_control_' + ctx['token'] + '_' + str(position), order=position + 1,
            selected_level=level, selected_product=product, registration_key=registration_key,
            slot=position, products=products, materials=materials,
            preserve_prefixes=('project_draft_' + ctx['token'] + '_', 'quick_control_' + ctx['token'] + '_',
                               'project_product_' + ctx['token'],
                               'project_kind_' + ctx['token'], 'project_instant_' + ctx['token']),
            disabled=dependency_pending)
        level_ids.append(level)
        product_ids.append(product)
    draft['level_ids'], draft['level_product_ids'] = level_ids, product_ids
    draft['qc_material_id'] = product_ids[0] if product_ids else None
    chosen = [value for value in level_ids if value is not None]
    materials_ready = len(chosen) == draft['level_count'] and len(set(chosen)) == len(chosen)
    duplicate_levels = len(set(chosen)) != len(chosen)
    reviewed = render_product_reviews(ctx, [product for product in product_ids if product is not None],
        reagent_id=draft['reagent_id'], lab_instrument_id=draft['lab_instrument_id'],
        test_item_id=draft['test_item_id'], method_id=draft['method_id'],
        prefix='project_product_' + ctx['token'])
    registration_open = bool(st.session_state.get(registration_key + '_registration'))
    with st.expander('分组与项目备注'):
        _text(ctx, 'project_group', '分组')
        _text(ctx, 'notes', '项目备注', multiline=True)
    dependency_open = render_project_dependencies(ctx, kind='quick', disabled=registration_open)
    relation_pending = render_pending_product_relation_notice(ctx)
    from ui.project_setup_draft import render_save_setup_draft
    render_save_setup_draft(ctx, disabled=registration_open or dependency_open or relation_pending)
    if duplicate_levels:
        st.error('各水平不能选择同一条质控品记录，请调整上方的原厂批号与浓度。')
    if (not reviewed and any(product is not None for product in product_ids)
            and draft['lab_instrument_id'] is not None and draft['reagent_id'] is not None
            and not (registration_open or dependency_open or relation_pending)):
        st.info('请先在上方“质控品适用性确认”中完成核对，再点击“确认所选质控品”。')
    cancel, forward = st.columns(2)
    if cancel.button('取消', key='project_dialog_cancel', disabled=registration_open or dependency_open):
        _cancel(ctx)
    if forward.button('下一步：填写质量要求', type='primary', key='quick_details_next',
                      disabled=not reviewed or not materials_ready or not draft['target_statistics_mode']
                      or registration_open or dependency_open or relation_pending):
        try:
            context = prepare_quick_project_details(draft)
        except (ValueError, TypeError) as error:
            st.error(str(error))
        else:
            _use_quality_context(ctx, context)
            ctx['quick_step'] = 'quality'
            st.rerun()
    if switch_requested:
        request_setup_mode(ctx, 'group')


def _remember_quality_inputs(ctx):
    """Keep current widget input even when an upstream reference becomes invalid."""
    import re
    identity = ctx.get('quick_quality_identity')
    if not identity:
        return
    prefix = 'quick_quality_' + ctx['token'] + '_' + identity + '_'
    saved = ctx.get('quick_quality_inputs') or {}
    same_draft = saved.get('identity') == identity and saved.get('token') == ctx['token']
    registering_sources = (bool(st.session_state.get(prefix+'advanced_search')) and
                           st.session_state.get(prefix+'search_conclusion') == 'registered')
    registered_count = st.session_state.get(prefix+'registered_count', 0)
    def active_input(key):
        row = re.match(re.escape(prefix) + r'registered_(\d+)_', key)
        return not row or (registering_sources and int(row.group(1)) < registered_count)
    values = {key: deepcopy(value) for key, value in saved.get('values', {}).items()
              if key.startswith(prefix) and active_input(key)} if same_draft else {}
    upload_errors = {}
    for key, value in list(st.session_state.items()):
        if not key.startswith(prefix) or not active_input(key):
            continue
        upload = re.fullmatch(re.escape(prefix) + r'registered_(\d+)_file', key)
        if upload and value is not None:
            # File-upload widgets cannot be assigned on a later rerun. Retain
            # the same PDF identity used by the existing standard form instead.
            index = int(upload.group(1))
            quality = ctx['draft'].setdefault('quality', {})
            sources = quality.setdefault('registered_standards', [])
            while len(sources) <= index:
                sources.append({})
            contents = value.getvalue()
            valid = isinstance(contents, bytes) and contents.startswith(b'%PDF-')
            sources[index]['sha256'] = sha256(contents).hexdigest() if valid else ''
            error_key = prefix + 'registered_' + str(index) + '_upload_error'
            upload_errors[error_key] = '' if valid else '请上传可打开的标准全文 PDF 文件。'
        elif not key.endswith(('_file', '_process_reset')):
            values[key] = deepcopy(value)
    for key, message in upload_errors.items():
        st.session_state[key] = message
        values[key] = message
    ctx['quick_quality_inputs'] = dict(identity=identity, token=ctx['token'], values=values)


def _render_quality(ctx):
    from ui.project_dialogs import _cancel
    _remember_quality_inputs(ctx)
    try:
        context = prepare_quick_project_details(ctx['draft'])
    except (ValueError, TypeError) as error:
        st.error(str(error))
        st.info('上述检测设置需要调整，请返回检测设置核对；本次填写内容已保留。')
        if st.button('返回检测设置', key='quick_invalid_back'):
            ctx['quick_step'] = 'details'
            st.rerun()
        if st.button('取消', key='project_dialog_cancel'):
            _cancel(ctx)
        from ui.project_setup_draft import render_save_setup_draft
        render_save_setup_draft(ctx)
        return
    _use_quality_context(ctx, context)
    saved = ctx.get('quick_quality_inputs') or {}
    if saved.get('identity') == ctx.get('quick_quality_identity'):
        for key, value in saved.get('values', {}).items():
            if key not in st.session_state:
                st.session_state[key] = deepcopy(value)
    ctx['draft']['quality'] = _quality_form(context, ctx)
    _remember_quality_inputs(ctx)
    from ui.project_setup_draft import render_save_setup_draft
    render_save_setup_draft(ctx)
    back, cancel, forward = st.columns(3)
    if back.button('返回检测设置', key='quick_quality_back'):
        ctx['quick_step'] = 'details'
        st.rerun()
    if cancel.button('取消', key='project_dialog_cancel'):
        _cancel(ctx)
    if forward.button('下一步：检查并保存项目', key='quick_quality_next', type='primary',
                      disabled=not ctx['draft']['quality'].get('checked') or ctx['draft']['quality'].get('blocked', False)):
        try:
            ctx['quick_preview'] = prepare_quick_project_setup(ctx['draft'])
        except QuickSetupDetailsError as error:
            st.error(str(error))
            st.info('上述检测设置需要调整，请返回检测设置核对；本次填写内容已保留。')
        except (ValueError, TypeError) as error:
            st.error(str(error))
            st.info('请按上方提示补齐质量要求，再点击“下一步：检查并保存项目”。')
        else:
            ctx['quick_step'] = 'preview'
            st.rerun()


def _quality_form(context, ctx):
    from ui.quality_targets import render_quality_setup
    item = context['item']
    prior = ctx['draft'].get('quality') or {}
    prefix = 'quick_quality_' + ctx['token'] + '_' + ctx['quick_quality_identity']
    st.caption(f"{item['test_item_name']}｜{item['method_name']}｜{item['unit_symbol']}｜{item['level_count']} 水平")
    actor = (ctx['draft'].get('product_choice_review') or {}).get('confirmed_by', '')
    return render_quality_setup(item, prior, prefix, levels=context['levels'], quick=True, actor=actor)


def _render_preview(ctx):
    from ui.project_dialogs import MODAL_KEY, _cancel
    preview = ctx['quick_preview']
    st.table(pd.DataFrame(preview['summary'], columns=['保存内容', '设置值']))
    for order, label in enumerate(preview['material_labels'], 1):
        st.caption(f'水平 {order}：{label}')
    quality = ctx['draft']['quality']
    st.caption('质量要求确认人：' + quality['confirmed_by'])
    from ui.project_setup_draft import render_save_setup_draft, clear_saved_setup
    render_save_setup_draft(ctx)
    details, back, cancel, save = st.columns([1, 1, 0.7, 1.6])
    if details.button('返回检测设置', key='quick_preview_details'):
        ctx['quick_step'] = 'details'
        st.rerun()
    if back.button('返回质量要求', key='quick_preview_back'):
        ctx['quick_step'] = 'quality'
        st.rerun()
    if cancel.button('取消', key='project_dialog_cancel'):
        _cancel(ctx)
    if save.button('保存项目并开始录入', key='quick_save', type='primary'):
        try:
            from database import atomic_write
            with atomic_write():
                receipt = save_quick_project_setup(deepcopy(ctx['draft']),
                    expected_preview_hash=preview['preview_hash'], request_key=ctx['token'])
                clear_saved_setup(ctx)
        except QuickSetupDetailsError as error:
            st.error(str(error))
            st.info('上述检测设置需要调整，请返回检测设置核对；本次填写内容已保留。')
        except (ValueError, TypeError) as error:
            st.error(str(error))
            st.info('质量要求有误请点击“返回质量要求”；项目、单位或批号有误请点击“返回检测设置”。')
        except (sqlite3.Error, OSError):
            logging.exception('Quick project setup failed')
            st.error('项目尚未保存，全部填写内容仍保留。请检查保存位置后重试。')
        else:
            st.session_state['workspace_project_id'] = receipt['template_id']
            st.session_state['home_selected_project_id'] = receipt['template_id']
            st.session_state['v11_selected_template_id'] = receipt['template_id']
            st.session_state.pop(MODAL_KEY, None)
            from ui.project_navigation import open_configured_batch
            open_configured_batch(receipt['binding'])
