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
    product_choice_label, product_choice_markdown_label, product_concentration_caption, product_display_rows,
)
from services.qc_product_recommendation_service import (
    get_qc_product_recommendations, validate_qc_product_choice, validate_choice_review,
    choice_review_map, pack_choice_reviews, validate_choice_reviews,
    list_qc_product_relations, save_qc_product_relation, set_qc_product_relation_disabled,
)


def has_pending_product_relation(ctx):
    return any(key.endswith('_editor') and bool(value) for key, value in ctx.items())


def render_pending_product_relation_notice(ctx):
    pending = has_pending_product_relation(ctx)
    if pending:
        st.info('请先保存或取消“登记试剂配套的质控品”中的记录。')
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
        st.info('请先选择仪器和试剂。')
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
        st.warning('配套资料已更新，请核对后点击“确认所选质控品”。')
    candidates = {int(row['qc_material_id']): row for row in suggestions['candidates']}
    products = {int(row['id']): row for row in product_display_rows(
        list_qc_materials(include_disabled=False).to_dict('records'))}
    if not products and not locked:
        st.info('没有可选质控品，请在“补充基础资料”中登记“质控品产品”。')
    current = draft.get(field)
    if locked:
        row = products.get(current)
        st.caption('质控品：' + (product_choice_markdown_label(row) if row else '当前项目所选产品'))
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
        concentration = product_concentration_caption(products[current])
        if concentration:
            st.caption(concentration)
    if (draft.get('product_choice_review') or {}).get('qc_material_id') != current:
        draft.pop('product_choice_review', None)
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
            st.info('质控品确认人或使用说明已修改，请重新点击“确认所选质控品”。')
    if st.button('确认所选质控品', key=prefix + '_confirm', disabled=current is None):
        try:
            draft['product_choice_review'] = validate_qc_product_choice(qc_material_id=current,
                **scope, expected_revision=suggestions['revision'], manual_review=manual_review)
        except ValueError as error:
            st.error(str(error))
    review = draft.get('product_choice_review')
    valid = False
    if review:
        try:
            validate_choice_review(review, qc_material_id=current, **scope)
        except ValueError as error:
            st.warning(str(error))
        else:
            valid = True
            st.caption('本次选品依据已核对，随项目或批次设置一起保存。')
    if not valid:
        if current is None:
            st.info('请选择“质控品”，再点击“确认所选质控品”。')
        elif manual and not all(str(manual_review.get(name) or '').strip() for name in ('confirmed_by', 'evidence')):
            missing = [label for name, label in (('confirmed_by', '质控品确认人'), ('evidence', '为什么选择此质控品'))
                       if not str(manual_review.get(name) or '').strip()]
            st.info('请填写上方的“' + '”、“'.join(missing) + '”，再点击“确认所选质控品”。')
        else:
            st.info('所选质控品尚待确认，确认后用于当前仪器、试剂和检测方法。')
    if allow_relation_edit:
        with st.expander('登记试剂配套的质控品'):
            st.caption('保存产品的适用范围和依据；实际批号另行登记。')
            _render_relation_editor(ctx, scope, current, prefix)
    return valid


def render_product_reviews(ctx, qc_material_ids, *, reagent_id, lab_instrument_id,
                           test_item_id=None, method_id=None, prefix=None,
                           on_confirm=None, confirm_label='确认所选质控品', allow_relation_edit=True):
    """Confirm the chosen products together; a lot change alone needs no new evidence."""
    prefix = prefix or 'product_reviews_' + ctx['token']
    draft = ctx['draft']
    products = list(dict.fromkeys(int(value) for value in qc_material_ids if value is not None))
    scope = dict(reagent_id=reagent_id, lab_instrument_id=lab_instrument_id,
                 test_item_id=test_item_id, method_id=method_id)
    if allow_relation_edit:
        pending = next(((key[:-7], value) for key, value in ctx.items()
                        if key.startswith(prefix) and key.endswith('_editor') and value), None)
        if pending:
            relation_prefix, editor = pending
            frozen = {field: editor['values'].get(field) for field in scope}
            _render_relation_editor(ctx, frozen, editor['values'].get('qc_material_id'), relation_prefix)
            return False
    reviews = choice_review_map(draft.get('product_choice_reviews') or draft.get('product_choice_review'))
    reviews = {str(product): reviews[str(product)] for product in products if str(product) in reviews}
    draft['product_choice_reviews'] = reviews
    draft['product_choice_review'] = pack_choice_reviews(reviews)
    if not products:
        return False
    if reagent_id is None or lab_instrument_id is None:
        st.info('请先选择仪器和试剂。')
        return False
    suggestions = get_qc_product_recommendations(**scope)
    candidates = {int(row['qc_material_id']): row for row in suggestions['candidates']}
    catalog = {int(row['id']): row for row in product_display_rows(
        list_qc_materials(include_disabled=True).to_dict('records'))}
    st.markdown('**质控品适用性确认**')
    prepared, complete, all_valid, dirty_inputs = {}, True, True, False
    cache = draft.setdefault('product_manual_drafts', {})
    for product in products:
        product_key = str(product)
        old = reviews.get(product_key, {})
        pfx = prefix if len(products) == 1 else prefix + '_' + product_key
        label = product_choice_markdown_label(catalog[product]) if product in catalog else '质控品已不存在'
        st.markdown('**' + label + '**')
        error = ''
        try:
            current = validate_choice_review(old, qc_material_id=product, **scope)
        except (ValueError, TypeError) as exc:
            current, error = None, str(exc)
        editing = bool(ctx.get(pfx + '_editing'))
        if current and not editing:
            prepared[product_key] = current
            st.caption('已确认：' + str(current.get('confirmed_by') or '') + '｜' + str(current.get('evidence') or ''))
            if st.button('修改适用依据', key=pfx + '_edit'):
                ctx[pfx + '_editing'] = True
                st.rerun(scope='app')
            if allow_relation_edit:
                with st.expander('登记试剂配套的质控品'):
                    _render_relation_editor(ctx, scope, product, pfx)
            continue
        all_valid = False
        candidate = candidates.get(product)
        manual = candidate is None or old.get('basis') == 'manual' or editing
        instruction = ('请填写下方的“质控品确认人”和“适用依据”，再点击“' + confirm_label + '”。'
            if manual else '请核对下方已登记的依据，再点击“' + confirm_label + '”。')
        # Keep the reason a previous review expired, and point to the controls
        # that are rendered immediately below it.
        if old and error:
            st.warning(error + ' ' + instruction)
        else:
            st.info(instruction)
        fields, fields_ready = None, True
        if manual:
            identity = sha256(json.dumps(dict(scope, qc_material_id=product), sort_keys=True).encode()).hexdigest()[:16]
            initial = cache.get(identity) or old or {}
            person_key, evidence_key = pfx + '_person', pfx + '_evidence'
            if ctx.get(pfx + '_identity') != identity:
                st.session_state[person_key] = initial.get('confirmed_by', '')
                st.session_state[evidence_key] = initial.get('evidence', '')
                ctx[pfx + '_identity'] = identity
            else:
                # A discard prompt temporarily removes the widgets from the page.
                # Streamlit clears their keys, while the explicit draft must survive.
                if person_key not in st.session_state:
                    st.session_state[person_key] = initial.get('confirmed_by', '')
                if evidence_key not in st.session_state:
                    st.session_state[evidence_key] = initial.get('evidence', '')
            from ui.required_fields import required_field
            with required_field(person_key, missing=not str(st.session_state.get(person_key) or '').strip(),
                                message='请填写质控品确认人。'):
                person = st.text_input('质控品确认人', key=person_key, max_chars=80)
            with required_field(evidence_key, missing=not str(st.session_state.get(evidence_key) or '').strip(),
                                message='请填写此产品的适用依据。'):
                evidence = st.text_area('适用依据', key=evidence_key, max_chars=2000,
                    placeholder='说明书名称或实验室使用记录。')
            fields = dict(confirmed_by=person, evidence=evidence)
            dirty_inputs = dirty_inputs or any(str(fields[key]) != str(old.get(key) or '')
                                              for key in ('confirmed_by', 'evidence'))
            cache[identity] = fields
            if not person.strip() or not evidence.strip():
                complete = False
                fields_ready = False
        else:
            st.caption('已登记依据：' + '；'.join(candidate['evidence']))
            st.caption('核对人：' + '；'.join(candidate['confirmed_by']))
        if fields_ready:
            try:
                prepared[product_key] = validate_qc_product_choice(qc_material_id=product, **scope,
                    expected_revision=suggestions['revision'], manual_review=fields)
            except (ValueError, TypeError) as exc:
                complete = False
                st.error(str(exc))
        if allow_relation_edit:
            with st.expander('登记试剂配套的质控品'):
                _render_relation_editor(ctx, scope, product, pfx)
    ctx[prefix + '_dirty'] = not all_valid and (any(
        key.startswith(prefix) and key.endswith('_editing') and value for key, value in ctx.items()) or
        dirty_inputs)
    if not all_valid and st.button(confirm_label, key=prefix + '_confirm', disabled=not complete):
        try:
            confirmed = validate_choice_reviews(prepared, qc_material_ids=products, **scope)
            if on_confirm is not None:
                on_confirm(confirmed)
            draft['product_choice_reviews'] = choice_review_map(confirmed)
            draft['product_choice_review'] = confirmed
            for product in products:
                pfx = prefix if len(products) == 1 else prefix + '_' + str(product)
                ctx.pop(pfx + '_editing', None)
            st.rerun(scope='app')
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
    return all_valid


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
            st.info('勾选上方确认项后，即可确认各项目的质控品适用依据。')
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
        text = product_choice_markdown_label(row) if row and field == 'qc_material_id' else str(row[name]) if row else '全部'
        summaries.append(label + '：' + text)
    st.caption('｜'.join(summaries))
    st.caption('适用范围按上方条件登记；“全部”涵盖该类别的所有选项。请填写说明书或使用记录作为依据。')
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
            reviews = choice_review_map(ctx['draft'].get('product_choice_reviews') or
                                        ctx['draft'].get('product_choice_review'))
            reviews.pop(str(values['qc_material_id']), None)
            ctx['draft']['product_choice_reviews'] = reviews
            ctx['draft']['product_choice_review'] = pack_choice_reviews(reviews)
            st.rerun()
