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
from services.qc_product_display_service import (
    product_choice_label, product_concentration_summary, product_display_rows,
)
from services.qc_product_recommendation_service import (
    get_qc_product_recommendations, validate_qc_product_choice, validate_choice_review,
    list_qc_product_relations, save_qc_product_relation, set_qc_product_relation_disabled,
)


def has_pending_product_relation(ctx):
    return any(key.endswith('_editor') and bool(value) for key, value in ctx.items())


def render_pending_product_relation_notice(ctx):
    pending = has_pending_product_relation(ctx)
    if pending:
        st.info('“登记试剂配套的质控品”中有配套记录尚未完成。请先保存或取消这条记录，再继续项目设置。')
    return pending


def render_product_choice(ctx, *, reagent_id, lab_instrument_id, test_item_id=None,
                          method_id=None, field='qc_material_id', locked=False, prefix=None,
                          allow_relation_edit=True):
    prefix = prefix or 'project_product_' + ctx['token']
    draft = ctx['draft']
    scope = dict(reagent_id=reagent_id, lab_instrument_id=lab_instrument_id,
                 test_item_id=test_item_id, method_id=method_id)
    def pending_editor():
        editor = ctx.get(prefix + '_editor')
        if editor and allow_relation_edit:
            frozen = {name: editor['values'].get(name) for name in scope}
            _render_relation_editor(ctx, frozen, editor['values'].get('qc_material_id'), prefix)
    if reagent_id is None or lab_instrument_id is None:
        st.info('选择仪器和试剂后，在这里选择本次使用的质控品。')
        draft.pop('product_choice_review', None)
        pending_editor()
        return False
    try:
        suggestions = get_qc_product_recommendations(**scope)
    except ValueError as error:
        st.error(str(error))
        pending_editor()
        return False
    if ctx.get(prefix + '_context') != suggestions['context']:
        ctx[prefix + '_context'] = suggestions['context']
        draft.pop('product_choice_review', None)
    review = draft.get('product_choice_review') or {}
    if review and review.get('revision') != suggestions['revision']:
        draft.pop('product_choice_review', None)
        st.warning('试剂与质控品的使用记录已更新，请重新选择并点击“确认所选质控品”。')
    candidates = {int(row['qc_material_id']): row for row in suggestions['candidates']}
    products = {int(row['id']): row for row in product_display_rows(
        list_qc_materials(include_disabled=False).to_dict('records'))}
    if not products and not locked:
        st.info('还没有可选的质控品产品。展开“补充基础资料”，选择“质控品产品”并登记；保存后再选择本次批号。')
    current = draft.get(field)
    if locked:
        row = products.get(current)
        st.caption('质控品：' + (product_choice_label(row) if row else '当前项目所选产品'))
    else:
        modes = ['已核对的候选', '人工选品'] if candidates else ['人工选品']
        mode_key = prefix + '_mode'
        if st.session_state.get(mode_key) not in modes:
            prior_mode = ctx.get(prefix + '_mode')
            st.session_state[mode_key] = prior_mode if prior_mode in modes else modes[0]
        mode = st.radio('质控品选择', modes, horizontal=True, key=mode_key,
            format_func=lambda value: {'已核对的候选': '已登记的配套质控品', '人工选品': '全部质控品'}[value])
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
                candidates[value]['label'] if mode == '已核对的候选' else product_choice_label(products[value])))
        current = draft[field]
        ctx[prefix + '_last_product'] = current
    if current in products:
        concentration = product_concentration_summary(products[current])
        if concentration:
            st.caption(concentration)
    if (draft.get('product_choice_review') or {}).get('qc_material_id') != current:
        draft.pop('product_choice_review', None)
    if candidates:
        st.caption('已找到本次仪器、试剂和检验项目的配套记录。请选择产品并点击“确认所选质控品”。' if not locked else '此质控品已有配套使用记录，请点击“确认所选质控品”。')
    else:
        st.caption('还没有这套仪器和试剂的质控品使用记录。请填写确认人和使用说明。' if locked else '还没有这套仪器和试剂的质控品使用记录。请选择质控品，并填写确认人和使用说明。')
    candidate = candidates.get(current)
    if candidate:
        st.caption('使用说明：' + '；'.join(candidate['evidence']))
        st.caption('确认人：' + '；'.join(candidate['confirmed_by']))
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
            confirmed_by=st.text_input('质控品确认人', value=saved.get('confirmed_by', ''), key=prefix + '_person', max_chars=80),
            evidence=st.text_area('为什么选择此质控品', value=saved.get('evidence', ''), key=prefix + '_evidence', max_chars=2000,
                placeholder='填写说明书名称或实验室使用记录，说明此质控品用于哪台仪器、哪种试剂和哪个检验项目。'))
        cache[identity] = manual_review
        confirmed = draft.get('product_choice_review') or {}
        if confirmed and (confirmed.get('basis') != 'manual' or any(
                str(confirmed.get(name) or '').strip() != str(manual_review[name] or '').strip()
                for name in ('confirmed_by', 'evidence'))):
            draft.pop('product_choice_review', None)
            st.info('质控品确认人或使用说明已修改。请点击“确认所选质控品”，重新确认本次填写。')
    if st.button('确认所选质控品', key=prefix + '_confirm', disabled=current is None):
        try:
            draft['product_choice_review'] = validate_qc_product_choice(qc_material_id=current,
                **scope, expected_revision=suggestions['revision'], manual_review=manual_review)
        except ValueError as error:
            st.error(str(error))
        else:
            st.success('已确认此质控品。')
    review = draft.get('product_choice_review')
    valid = False
    if review:
        try:
            validate_choice_review(review, qc_material_id=current, **scope)
        except ValueError as error:
            st.warning(str(error))
        else:
            valid = True
            st.caption('本次质控品已确认。')
    if not valid:
        if current is None:
            st.info('请先在上方“质控品”中选择产品，再点击“确认所选质控品”。')
        elif manual and not all(str(manual_review.get(name) or '').strip() for name in ('confirmed_by', 'evidence')):
            missing = [label for name, label in (('confirmed_by', '质控品确认人'), ('evidence', '为什么选择此质控品'))
                       if not str(manual_review.get(name) or '').strip()]
            st.info('请填写上方的“' + '”、“'.join(missing) + '”，再点击“确认所选质控品”。')
        else:
            st.info('请点击上方“确认所选质控品”，确认完成后才能继续项目设置。')
    if allow_relation_edit:
        with st.expander('登记试剂配套的质控品'):
            st.caption('在这里记录这套仪器和试剂可使用哪些质控品，方便下次选择。质控品批号另行登记。')
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
            summary.append({'检验项目': row.get('test_item_name', ''), '质控品选择': '请先选择试剂'})
            continue
        try:
            validate_choice_review(row.get('product_choice_review'), **scope)
        except (ValueError, TypeError):
            suggestions = get_qc_product_recommendations(**{key: value for key, value in scope.items() if key != 'qc_material_id'})
            matched = any(value['qc_material_id'] == template['qc_material_id'] for value in suggestions['candidates'])
            unresolved.append((row, scope, suggestions))
            summary.append({'检验项目': row.get('test_item_name', ''),
                '质控品选择': '已有配套记录，请确认' if matched else '请填写质控品使用说明'})
        else:
            summary.append({'检验项目': row.get('test_item_name', ''), '质控品选择': '已确认'})
    with st.expander('确认各检验项目使用的质控品', expanded=bool(unresolved)):
        st.dataframe(pd.DataFrame(summary), hide_index=True, width='stretch')
        if not unresolved:
            return
        prefix = 'panel_product_' + ctx['token']
        person = st.text_input('质控品确认人', key=prefix + '_person', max_chars=80)
        evidence = st.text_area('各检验项目选择此质控品的原因', key=prefix + '_evidence', max_chars=2000)
        checked = st.checkbox('已确认此质控品可用于以上各项的试剂和检测方法', key=prefix + '_checked')
        missing_reagents = [row.get('test_item_name', '') for row, scope, suggestions in unresolved if suggestions is None]
        if missing_reagents:
            st.info('请在上方表格的“试剂”列为以下项目选择试剂：' + '、'.join(missing_reagents) + '。')
        if not checked:
            st.info('请核对以上项目，并勾选“已确认此质控品可用于以上各项的试剂和检测方法”，再点击“确认以上项目的质控品”。')
        if st.button('确认以上项目的质控品', key=prefix + '_confirm', disabled=not checked):
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
        frame = pd.DataFrame([{'质控品': product_choice_label(row), '仪器': row['instrument_name'] or '全部仪器',
            '检验项目': row['test_item_name'] or '全部检验项目', '方法学': row['method_name'] or '全部方法学',
            '范围说明': row['scope_note'], '状态': '已停用' if row['is_disabled'] else '在用'} for row in rows])
        selected_id = render_record_selection(frame, [row['id'] for row in rows], scope=prefix + '_relations')
        selected = next((row for row in rows if row['id'] == selected_id), None)
    left, middle, right = st.columns(3)
    if left.button('新增配套记录', key=prefix + '_relation_add', disabled=busy or current_product is None):
        values = dict(scope, qc_material_id=current_product, scope_note='', evidence='', confirmed_by='')
        ctx[editor_key] = dict(token=uuid4().hex, values=values, initial=deepcopy(values), action='new', discard=False)
        st.rerun()
    if middle.button('编辑配套记录', key=prefix + '_relation_edit', disabled=busy or not selected or bool(selected['is_disabled'])):
        ctx[editor_key] = dict(token=uuid4().hex, values=dict(selected), initial=dict(selected), action='edit', discard=False)
        st.rerun()
    if right.button('恢复配套记录' if selected and selected['is_disabled'] else '停用配套记录',
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
        st.warning('这条质控品使用记录还没有保存。')
        a, b = st.columns(2)
        if a.button('继续填写', key=ep + '_keep'):
            editor['discard'] = False
            st.rerun()
        if b.button('放弃填写', key=ep + '_discard'):
            ctx.pop(editor_key, None)
            st.rerun()
        return
    st.markdown('**' + {'new': '新增配套记录', 'edit': '编辑配套记录', 'status': '停用或恢复配套记录'}[editor['action']] + '**')
    from services.master_data_service import list_reagents, list_lab_instruments, list_test_items, list_methods
    summaries = []
    for field, label, frame, name in (
        ('reagent_id', '试剂', list_reagents(include_disabled=True), 'generic_name'),
        ('qc_material_id', '质控品', list_qc_materials(include_disabled=True), 'generic_name'),
        ('lab_instrument_id', '仪器', list_lab_instruments(include_disabled=True), 'display_name'),
        ('test_item_id', '检验项目', list_test_items(include_disabled=True), 'chinese_name'),
        ('method_id', '方法学', list_methods(include_disabled=True), 'method_name'),
    ):
        records = frame.to_dict('records')
        if field == 'qc_material_id':
            records = product_display_rows(records)
        row = next((row for row in records if row['id'] == values.get(field)), None)
        text = product_choice_label(row) if row and field == 'qc_material_id' else str(row[name]) if row else '全部'
        summaries.append(label + '：' + text)
    st.caption('｜'.join(summaries))
    st.caption('上方列出这条记录使用的仪器、检验项目和方法。“全部”表示没有限制，请在下方说明。')
    # Identity is frozen for this form, so changing the surrounding project cannot rebind it.
    if editor['action'] != 'status':
        for field, label in [('scope_note', '可使用的仪器、项目和方法'), ('evidence', '说明书或使用记录'), ('confirmed_by', '确认人')]:
            values[field] = st.text_input(label, value=values.get(field, ''), key=ep + '_' + field)
    else:
        values['confirmed_by'] = st.text_input('确认人', value=values.get('confirmed_by', ''), key=ep + '_status_person')
        values['status_reason'] = st.text_input('停用或恢复原因', value=values.get('status_reason', ''), key=ep + '_status_reason')
    cancel, save = st.columns(2)
    if cancel.button('取消', key=ep + '_cancel'):
        if values != editor['initial']:
            editor['discard'] = True
        else:
            ctx.pop(editor_key, None)
        st.rerun()
    if save.button('保存配套记录' if editor['action'] != 'status' else '确认停用或恢复', key=ep + '_save', type='primary'):
        try:
            if editor['action'] == 'status':
                set_qc_product_relation_disabled(values['id'], disabled=not bool(values['is_disabled']),
                    reason=values.get('status_reason', ''), confirmed_by=values['confirmed_by'], expected_revision=values['revision_no'])
            else:
                save_qc_product_relation(values, relation_id=values.get('id'), expected_revision=values.get('revision_no'))
        except (ValueError, TypeError) as error:
            st.error(str(error))
        except sqlite3.Error:
            st.error('这条配套记录没有保存成功。填写内容仍保留，请检查保存位置后重试。')
        else:
            ctx.pop(editor_key, None)
            ctx['draft'].pop('product_choice_review', None)
            st.rerun()
