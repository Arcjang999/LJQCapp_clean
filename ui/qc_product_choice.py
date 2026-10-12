"""Explicit product candidates and local suitability evidence for project editors."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from uuid import uuid4
import json
import sqlite3

import pandas as pd
import streamlit as st

from services.master_data_service import list_qc_materials
from services.qc_product_recommendation_service import (
    get_qc_product_recommendations, validate_qc_product_choice, validate_choice_review,
    list_qc_product_relations, save_qc_product_relation, set_qc_product_relation_disabled,
)


def render_product_choice(ctx, *, reagent_id, lab_instrument_id, test_item_id=None,
                          method_id=None, field='qc_material_id', locked=False, prefix=None):
    prefix = prefix or 'project_product_' + ctx['token']
    draft = ctx['draft']
    scope = dict(reagent_id=reagent_id, lab_instrument_id=lab_instrument_id,
                 test_item_id=test_item_id, method_id=method_id)
    if reagent_id is None or lab_instrument_id is None:
        st.info('先选择仪器和试剂，再核对质控品。')
        draft.pop('product_choice_review', None)
        return False
    try:
        suggestions = get_qc_product_recommendations(**scope)
    except ValueError as error:
        st.error(str(error))
        return False
    if ctx.get(prefix + '_context') != suggestions['context']:
        ctx[prefix + '_context'] = suggestions['context']
        draft.pop('product_choice_review', None)
    review = draft.get('product_choice_review') or {}
    if review and review.get('revision') != suggestions['revision']:
        draft.pop('product_choice_review', None)
        st.warning('适用资料已更新，请重新核对候选及选品依据。')
    candidates = {int(row['qc_material_id']): row for row in suggestions['candidates']}
    products = {int(row['id']): row for row in list_qc_materials(include_disabled=False).to_dict('records')}
    current = draft.get(field)
    if locked:
        row = products.get(current)
        st.caption('质控品：' + (str(row['generic_name']) if row else '当前项目所选产品'))
    else:
        modes = ['已核对的候选', '人工选品'] if candidates else ['人工选品']
        mode_key = prefix + '_mode'
        if st.session_state.get(mode_key) not in modes:
            prior_mode = ctx.get(prefix + '_mode')
            st.session_state[mode_key] = prior_mode if prior_mode in modes else modes[0]
        mode = st.radio('质控品选择', modes, horizontal=True, key=mode_key)
        ctx[prefix + '_mode'] = mode
        choices = candidates if mode == '已核对的候选' else products
        key = prefix + '_product'
        if current != ctx.get(prefix + '_last_product'):
            st.session_state[key] = current if current in choices else None
        elif st.session_state.get(key) not in [None, *choices]:
            st.session_state[key] = current if current in choices else None
        elif key not in st.session_state:
            st.session_state[key] = current if current in choices else None
        draft[field] = st.selectbox('质控品 *', [None, *choices], key=key, filter_mode='fuzzy',
            format_func=lambda value: '请选择' if value is None else (
                candidates[value]['label'] if mode == '已核对的候选' else '｜'.join(
                    str(products[value].get(name) or '') for name in ('manufacturer_name', 'catalog_no', 'generic_name')
                    if products[value].get(name))))
        current = draft[field]
        ctx[prefix + '_last_product'] = current
    if (draft.get('product_choice_review') or {}).get('qc_material_id') != current:
        draft.pop('product_choice_review', None)
    if candidates:
        st.caption('选择试剂后已列出匹配的质控品；实际批号和全部水平还需核对。')
    else:
        st.caption('尚无匹配的已核对适用关系。可人工选品，并登记本次适用依据。')
    candidate = candidates.get(current)
    if candidate:
        st.caption('适用依据：' + '；'.join(candidate['evidence']))
        st.caption('核对人：' + '；'.join(candidate['confirmed_by']))
    manual = current is not None and (candidate is None or (not locked and mode == '人工选品'))
    manual_review = None
    if manual:
        identity = sha256(json.dumps(dict(scope, qc_material_id=current), sort_keys=True).encode()).hexdigest()[:16]
        cache = draft.setdefault('manual_choice_drafts', {})
        saved = cache.get(identity) or (draft.get('product_choice_review') if
            (draft.get('product_choice_review') or {}).get('basis') == 'manual' else {}) or {}
        if ctx.get(prefix + '_manual_identity') != identity:
            st.session_state[prefix + '_person'] = saved.get('confirmed_by', '')
            st.session_state[prefix + '_evidence'] = saved.get('evidence', '')
            ctx[prefix + '_manual_identity'] = identity
        manual_review = dict(
            confirmed_by=st.text_input('选品核对人', value=saved.get('confirmed_by', ''), key=prefix + '_person', max_chars=80),
            evidence=st.text_area('试剂与质控品适用依据', value=saved.get('evidence', ''), key=prefix + '_evidence', max_chars=2000,
                placeholder='填写说明书、已核对的材料或实验室使用依据，说明当前检测项和方法的适用性。'))
        cache[identity] = manual_review
    if st.button('确认所选质控品', key=prefix + '_confirm', disabled=current is None):
        try:
            draft['product_choice_review'] = validate_qc_product_choice(qc_material_id=current,
                **scope, expected_revision=suggestions['revision'], manual_review=manual_review)
        except ValueError as error:
            st.error(str(error))
        else:
            st.success('选品依据已核对，实际批号和水平在下一步确认。')
    review = draft.get('product_choice_review')
    valid = False
    if review:
        try:
            validate_choice_review(review, qc_material_id=current, **scope)
        except ValueError as error:
            st.warning(str(error))
        else:
            valid = True
            st.caption('已保留本次选品核对。')
    with st.expander('维护试剂与质控品适用关系'):
        st.caption('适用关系供以后选品参考；保存关系不创建项目、不生成实际批号。')
        _render_relation_editor(ctx, scope, current, prefix)
    return valid


def render_panel_product_review(ctx, template):
    rows = ctx['draft']['rows']
    if not rows:
        return
    summary, unresolved = [], []
    for row in rows:
        scope = dict(qc_material_id=template['qc_material_id'], reagent_id=row.get('reagent_id'),
            lab_instrument_id=template['lab_instrument_id'], test_item_id=row['test_item_id'], method_id=row.get('method_id'))
        if scope['reagent_id'] is None:
            unresolved.append((row, scope, None))
            summary.append({'检验项目': row.get('test_item_name', ''), '适用核对': '先选择试剂'})
            continue
        try:
            validate_choice_review(row.get('product_choice_review'), **scope)
        except (ValueError, TypeError):
            suggestions = get_qc_product_recommendations(**{key: value for key, value in scope.items() if key != 'qc_material_id'})
            matched = any(value['qc_material_id'] == template['qc_material_id'] for value in suggestions['candidates'])
            unresolved.append((row, scope, suggestions))
            summary.append({'检验项目': row.get('test_item_name', ''),
                '适用核对': '已有候选，请核对' if matched else '需登记人工核对依据'})
        else:
            summary.append({'检验项目': row.get('test_item_name', ''), '适用核对': '已核对'})
    with st.expander('整组选品核对', expanded=bool(unresolved)):
        st.dataframe(pd.DataFrame(summary), hide_index=True, width='stretch')
        if not unresolved:
            return
        prefix = 'panel_product_' + ctx['token']
        person = st.text_input('整组选品核对人', key=prefix + '_person', max_chars=80)
        evidence = st.text_area('整组试剂与质控品适用依据', key=prefix + '_evidence', max_chars=2000)
        checked = st.checkbox('已逐项核对以上试剂、检测项、方法及质控品的适用性', key=prefix + '_checked')
        if st.button('保存本次整组选品核对', key=prefix + '_confirm', disabled=not checked):
            try:
                proposed = []
                for row, scope, suggestions in unresolved:
                    if suggestions is None:
                        raise ValueError('请先为全部检验项目选择试剂。')
                    matched = any(value['qc_material_id'] == template['qc_material_id'] for value in suggestions['candidates'])
                    review = validate_qc_product_choice(**scope, expected_revision=suggestions['revision'],
                        manual_review=None if matched else dict(confirmed_by=person, evidence=evidence))
                    proposed.append((row, review))
                for row, review in proposed:
                    row['product_choice_review'] = review
            except (ValueError, TypeError) as error:
                st.error(str(error))
            else:
                st.rerun()


def _render_relation_editor(ctx, scope, current_product, prefix):
    editor_key = prefix + '_editor'
    busy = bool(ctx.get(editor_key))
    rows = [row for row in list_qc_product_relations(include_disabled=True) if row['reagent_id'] == scope['reagent_id']]
    selected = None
    if rows:
        from ui.record_workspace import render_record_selection
        frame = pd.DataFrame([{'质控品': row['qc_material_name'], '仪器': row['instrument_name'] or '全部仪器',
            '检验项目': row['test_item_name'] or '全部检验项目', '方法学': row['method_name'] or '全部方法学',
            '范围说明': row['scope_note'], '状态': '已停用' if row['is_disabled'] else '在用'} for row in rows])
        selected_id = render_record_selection(frame, [row['id'] for row in rows], scope=prefix + '_relations')
        selected = next((row for row in rows if row['id'] == selected_id), None)
    left, middle, right = st.columns(3)
    if left.button('新增适用关系', key=prefix + '_relation_add', disabled=busy or current_product is None):
        values = dict(scope, qc_material_id=current_product, scope_note='', evidence='', confirmed_by='')
        ctx[editor_key] = dict(token=uuid4().hex, values=values, initial=deepcopy(values), action='new', discard=False)
        st.rerun()
    if middle.button('编辑适用关系', key=prefix + '_relation_edit', disabled=busy or not selected or bool(selected['is_disabled'])):
        ctx[editor_key] = dict(token=uuid4().hex, values=dict(selected), initial=dict(selected), action='edit', discard=False)
        st.rerun()
    if right.button('恢复适用关系' if selected and selected['is_disabled'] else '停用适用关系',
                    key=prefix + '_relation_status', disabled=busy or selected is None):
        values = dict(selected, confirmed_by='', status_reason='')
        ctx[editor_key] = dict(token=uuid4().hex, values=values, initial=deepcopy(values), action='status', discard=False)
        st.rerun()
    editor = ctx.get(editor_key)
    if not editor:
        return
    values = editor['values']
    ep = prefix + '_' + editor['token']
    if editor['discard']:
        st.warning('适用关系填写尚未保存。')
        a, b = st.columns(2)
        if a.button('继续填写关系', key=ep + '_keep'):
            editor['discard'] = False
            st.rerun()
        if b.button('放弃关系填写', key=ep + '_discard'):
            ctx.pop(editor_key, None)
            st.rerun()
        return
    st.markdown('**' + {'new': '新增适用关系', 'edit': '编辑适用关系', 'status': '调整适用关系状态'}[editor['action']] + '**')
    from services.master_data_service import list_reagents, list_lab_instruments, list_test_items, list_methods
    summaries = []
    for field, label, frame, name in (
        ('reagent_id', '试剂', list_reagents(include_disabled=True), 'generic_name'),
        ('qc_material_id', '质控品', list_qc_materials(include_disabled=True), 'generic_name'),
        ('lab_instrument_id', '仪器', list_lab_instruments(include_disabled=True), 'display_name'),
        ('test_item_id', '检验项目', list_test_items(include_disabled=True), 'chinese_name'),
        ('method_id', '方法学', list_methods(include_disabled=True), 'method_name'),
    ):
        row = next((row for row in frame.to_dict('records') if row['id'] == values.get(field)), None)
        summaries.append(label + '：' + (str(row[name]) if row else '全部适用'))
    st.caption('｜'.join(summaries))
    st.caption('仪器、检测项及方法范围按打开此表单时的选择核对；留空范围表示全部适用，请在范围说明中明确。')
    # Identity is frozen for this form, so changing the surrounding project cannot rebind it.
    if editor['action'] != 'status':
        for field, label in [('scope_note', '适用范围说明'), ('evidence', '适用依据'), ('confirmed_by', '关系核对人')]:
            values[field] = st.text_input(label, value=values.get(field, ''), key=ep + '_' + field)
    else:
        values['confirmed_by'] = st.text_input('关系核对人', value=values.get('confirmed_by', ''), key=ep + '_status_person')
        values['status_reason'] = st.text_input('停用或恢复原因', value=values.get('status_reason', ''), key=ep + '_status_reason')
    cancel, save = st.columns(2)
    if cancel.button('取消关系填写', key=ep + '_cancel'):
        if values != editor['initial']:
            editor['discard'] = True
        else:
            ctx.pop(editor_key, None)
        st.rerun()
    if save.button('保存适用关系' if editor['action'] != 'status' else '确认调整状态', key=ep + '_save', type='primary'):
        try:
            if editor['action'] == 'status':
                set_qc_product_relation_disabled(values['id'], disabled=not bool(values['is_disabled']),
                    reason=values.get('status_reason', ''), confirmed_by=values['confirmed_by'], expected_revision=values['revision_no'])
            else:
                save_qc_product_relation(values, relation_id=values.get('id'), expected_revision=values.get('revision_no'))
        except (ValueError, TypeError) as error:
            st.error(str(error))
        except sqlite3.Error:
            st.error('适用关系尚未保存，填写内容仍保留，请检查保存位置后重试。')
        else:
            ctx.pop(editor_key, None)
            ctx['draft'].pop('product_choice_review', None)
            st.rerun()
