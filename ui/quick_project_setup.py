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
    get_quick_setup_context, prepare_quick_project_setup, save_quick_project_setup,
)


def new_quick_setup_draft():
    return dict(template_name='', lab_instrument_id=None, qc_material_id=None,
        test_item_id=None, reagent_id=None, method_id=None, unit_id=None,
        qc_method='lj', input_value_type='raw', level_count=1, target_n=20,
        cv_limit=None, quality_target_source_text='', project_group='', notes='',
        level_ids=[], config_name='', quality={})


def render_quick_project_setup(ctx):
    from ui.project_dialogs import _cancel
    step = ctx.get('quick_step', 'details')
    st.subheader('新建项目')
    st.caption({'details': '1 检测设置与实际质控品', 'quality': '2 核对质量要求',
                'preview': '3 核对后保存并进入录入'}[step])
    if step == 'details':
        _render_details(ctx)
    elif step == 'quality':
        _render_quality(ctx)
    else:
        _render_preview(ctx)


def request_setup_mode(ctx, target):
    if any(key.endswith('_editor') and value for key, value in ctx.items()):
        st.warning('请先保存或取消正在填写的适用关系，再切换项目设置方式。')
        return
    if ctx['draft'] != ctx['initial']:
        ctx['setup_mode_switch'] = target
    else:
        _convert_setup_mode(ctx, target)
    st.rerun()


def render_setup_mode_switch(ctx):
    target = ctx['setup_mode_switch']
    st.warning('切换后保留项目名称、仪器、试剂、质控品和默认方法；检验项目、实际水平及质量要求需要重新核对。')
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
        kind = 'quick'
    if old.get('manual_choice_drafts'):
        draft['manual_choice_drafts'] = deepcopy(old['manual_choice_drafts'])
    ctx.update(kind=kind, draft=draft, token=uuid4().hex, quick_step='details')
    for key in ('setup_mode_switch', 'quick_preview', 'quick_quality_identity', 'quick_auto_name_item'):
        ctx.pop(key, None)


def _render_details(ctx):
    from services.master_data_service import (list_test_items, list_lab_instruments,
        list_reagents, list_methods, list_units)
    from services.project_config_service import INPUT_VALUE_TYPE_LABELS
    from services.material_workflow_service import available_materials, material_label
    from ui.project_dialogs import _choice, _text, _key, _qc_choices, _cancel
    from ui.qc_product_choice import render_product_choice
    draft = ctx['draft']
    if st.button('多个检验项目设置', key='quick_use_group'):
        request_setup_mode(ctx, 'group')
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
    _text(ctx, 'template_name', '项目名称 *')
    if draft['qc_method'] == 'instant':
        draft['target_n'] = 20
    else:
        draft['target_n'] = st.number_input('建靶所需有效点数', min_value=5, max_value=20,
            step=1, key=_key(ctx, 'target_n'))
    st.markdown('**试剂与质控品**')
    reviewed = render_product_choice(ctx, reagent_id=draft['reagent_id'], lab_instrument_id=draft['lab_instrument_id'],
        test_item_id=draft['test_item_id'], method_id=draft['method_id'])
    if draft.get('qc_material_id') is not None:
        materials = available_materials(draft['qc_material_id'])
        options = {int(row['id']): row for row in materials.to_dict('records')}
        if not options:
            st.info('此质控品尚无实际批号，请在质控品管理或单位资料准备中登记后再选择。')
        level_ids = []
        prior_levels = list(draft.get('level_ids', []))
        for order in range(1, draft['level_count'] + 1):
            key = 'quick_material_' + ctx['token'] + '_' + str(draft['qc_material_id']) + '_' + str(order)
            if key not in st.session_state:
                previous = prior_levels[order - 1] if len(prior_levels) >= order else None
                st.session_state[key] = previous if previous in options else None
            elif st.session_state.get(key) not in [None, *options]:
                st.session_state[key] = None
            selected = st.selectbox(f'水平 {order} 的实际质控品 *', [None, *options], key=key,
                format_func=lambda value: '请选择浓度水平、批号和效期' if value is None else material_label(options[value]))
            if selected is not None:
                level_ids.append(selected)
        draft['level_ids'] = level_ids
        st.caption('各水平分别核对，不能重复选择同一条材料。实际试剂批号在每次检测时选择。')
    _text(ctx, 'config_name', '批次名称（留空自动生成）')
    with st.expander('分组与项目备注'):
        _text(ctx, 'project_group', '分组')
        _text(ctx, 'notes', '项目备注', multiline=True)
    cancel, forward = st.columns(2)
    if cancel.button('取消', key='project_dialog_cancel'):
        _cancel(ctx)
    if forward.button('下一步：核对质量要求', type='primary', key='quick_details_next', disabled=not reviewed):
        try:
            get_quick_setup_context(draft)
        except (ValueError, TypeError) as error:
            st.error(str(error))
        else:
            identity = sha256(json.dumps({key: draft[key] for key in ('test_item_id', 'method_id', 'unit_id',
                'reagent_id', 'qc_material_id', 'input_value_type', 'qc_method', 'level_count', 'level_ids')},
                sort_keys=True).encode()).hexdigest()[:12]
            if ctx.get('quick_quality_identity') != identity:
                draft['quality'] = {}
                ctx['quick_quality_identity'] = identity
            ctx['quick_step'] = 'quality'
            st.rerun()


def _render_quality(ctx):
    from ui.project_dialogs import _cancel
    try:
        context = get_quick_setup_context(ctx['draft'])
    except (ValueError, TypeError) as error:
        st.error(str(error))
        if st.button('返回检测设置', key='quick_invalid_back'):
            ctx['quick_step'] = 'details'
            st.rerun()
        if st.button('取消', key='project_dialog_cancel'):
            _cancel(ctx)
        return
    ctx['draft']['quality'] = _quality_form(context, ctx)
    back, cancel, forward = st.columns(3)
    if back.button('返回检测设置', key='quick_quality_back'):
        ctx['quick_step'] = 'details'
        st.rerun()
    if cancel.button('取消', key='project_dialog_cancel'):
        _cancel(ctx)
    if forward.button('核对保存预览', key='quick_quality_next', type='primary',
                      disabled=not ctx['draft']['quality'].get('checked')):
        try:
            ctx['quick_preview'] = prepare_quick_project_setup(ctx['draft'])
        except (ValueError, TypeError) as error:
            st.error(str(error))
        else:
            ctx['quick_step'] = 'preview'
            st.rerun()


def _quality_form(context, ctx):
    from ui.quality_applicability import render_conditions
    from ui.quality_targets import render_spec
    from services.quality_review_service import standard_candidates
    from services.quality_catalog_service import browse_quality_catalog
    item = context['item']
    prior = ctx['draft'].get('quality') or {}
    prefix = 'quick_quality_' + ctx['token'] + '_' + ctx['quick_quality_identity']
    st.caption(f"{item['test_item_name']}｜{item['method_name']}｜{item['unit_symbol']}｜{item['level_count']} 水平")
    condition_args, assessments = render_conditions(item, prior, prefix)
    numerical = [row['id'] for row in assessments if row['kind'] == 'numeric' and row['status'] == 'applicable']
    source_mode = item['input_value_type'] == 'raw' and condition_args['context'].get('result_scale') == 'concentration'
    if source_mode:
        mode_key = prefix + '_mode'
        if mode_key not in st.session_state:
            st.session_state[mode_key] = prior.get('mode', 'source')
        mode = st.radio('质量要求来源', ['source', 'recorded'], horizontal=True, key=prefix + '_mode',
            format_func=lambda value: '选择分析质量要求' if value == 'source' else '填写实验室自定要求')
    else:
        mode = 'recorded'
        st.caption('Ct、log及信号尺度按实际说明书、SOP或实验室要求核对，不套用浓度CV限值。')
    result = dict(condition_args, mode=mode, levels=[])
    if mode == 'source':
        candidates = {row['id'] for row in standard_candidates(item)}
        sources = {row['id']: row['spec'] for row in browse_quality_catalog() if row['kind'] in ('numeric', 'custom')
                   and (row['spec']['origin'] != 'builtin' or row['id'] in candidates)}
        key = prefix + '_requirement'
        if key not in st.session_state or st.session_state.get(key) not in [None, *sources]:
            st.session_state[key] = (prior['requirement_id'] if prior.get('requirement_id') in sources else
                                     numerical[0] if len(numerical) == 1 else None)
        selected = st.selectbox('选择分析质量要求', [None, *sources], key=key,
            format_func=lambda value: '请选择' if value is None else sources[value]['name'] + '｜' + sources[value]['standard'])
        result['requirement_id'] = selected
        if selected:
            spec = sources[selected]
            render_spec(spec)
            categories = list(dict.fromkeys(row['category'] for row in spec['imprecision'] if row.get('category')))
            for order, level in enumerate(context['levels'], 1):
                from services.material_workflow_service import material_label
                st.markdown(f'**水平 {order}：{material_label(level)}**')
                prior_level = next((row for row in prior.get('levels', []) if row['level_order'] == order), {})
                concentration = st.number_input('质控品浓度（选填，用于核对适用范围）', min_value=0.0, value=prior_level.get('concentration'),
                    format='%.4f', key=f'{prefix}_{selected}_{order}_concentration')
                category = st.selectbox('水平类别', ['', *categories],
                    index=categories.index(prior_level['category']) + 1 if prior_level.get('category') in categories else 0,
                    key=f'{prefix}_{selected}_{order}_category',
                    format_func=lambda value: value or '请选择') if categories else ''
                result['levels'].append(dict(level_order=order, concentration=concentration, category=category))
            if spec['origin'] == 'builtin':
                result['supplemental_cv'] = st.number_input('实验室更严CV要求（选填，%）', min_value=0.0,
                    value=prior.get('supplemental_cv'), key=prefix + '_supplemental_cv')
    else:
        for field, label in [('source_name', '依据名称（说明书、SOP等）'), ('source_version', '依据版本或编号')]:
            result[field] = st.text_input(label, value=prior.get(field, ''), key=prefix + '_' + field)
        result['requirement_text'] = st.text_area('实验室自定要求', value=prior.get('requirement_text', ''), key=prefix + '_recorded')
    seed = (ctx['draft'].get('product_choice_review') or {}).get('confirmed_by', '')
    result['confirmed_by'] = st.text_input('质量要求确认人', value=prior.get('confirmed_by', seed), key=prefix + '_person')
    result['evidence'] = st.text_area('质量要求适用依据', value=prior.get('evidence', ''), key=prefix + '_evidence')
    result['checked'] = st.checkbox('已核对检测条件、质量要求和全部水平的适用性',
        value=bool(prior.get('checked')), key=prefix + '_checked')
    return result


def _render_preview(ctx):
    from ui.project_dialogs import MODAL_KEY, _cancel
    preview = ctx['quick_preview']
    st.table(pd.DataFrame(preview['summary'], columns=['核对内容', '本次设置']))
    for order, label in enumerate(preview['material_labels'], 1):
        st.caption(f'水平 {order}：{label}')
    quality = ctx['draft']['quality']
    st.caption('质量要求确认人：' + quality['confirmed_by'])
    st.caption('确认保存后会同时建立项目、检验项目和批次，并进入检测录入。')
    back, cancel, save = st.columns(3)
    if back.button('返回质量要求', key='quick_preview_back'):
        ctx['quick_step'] = 'quality'
        st.rerun()
    if cancel.button('取消', key='project_dialog_cancel'):
        _cancel(ctx)
    if save.button('确认保存并进入录入', key='quick_save', type='primary'):
        try:
            receipt = save_quick_project_setup(deepcopy(ctx['draft']),
                expected_preview_hash=preview['preview_hash'], request_key=ctx['token'])
        except (ValueError, TypeError) as error:
            st.error(str(error))
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
