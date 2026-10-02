"""Explicit, versioned handling; navigation never edits the source result."""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4
import json
import logging

import pandas as pd
import streamlit as st

from services import out_of_control_service as service
from services.value_type_service import get_input_value_type_label

STATUS_LABELS = {'pending': '待处理', 'in_progress': '处理中',
                 'pending_confirmation': '待确认', 'completed': '已完成'}
CLASS_LABELS = {'reject': '失控', 'warning': '警告', 'accept': '在控'}
CONTENT_LABELS = {
    'cause_category': '原因分类', 'cause_analysis': '原因分析',
    'corrective_action': '纠正措施', 'supplementary_note': '补充说明',
    'effect_description': '处理效果说明', 'effect_evidence': '效果依据',
    'handler_text': '处理人', 'confirmer_text': '确认人', 'confirmed_at': '确认时间',
    'patient_impact_assessment': '患者影响评估', 'patient_impact_start': '影响开始时间',
    'patient_impact_end': '影响结束时间', 'patient_impact_scope': '影响范围',
    'patient_impact_actions': '患者影响处置措施', 'patient_impact_basis': '患者影响评估依据',
}


def display(value):
    if value is None or value == '':
        return '未记录'
    if isinstance(value, list):
        return '、'.join(display(v) for v in value) or '未记录'
    return CLASS_LABELS.get(str(value), str(value))


def show_error(exc):
    logging.getLogger(__name__).warning('Event handling failed', exc_info=True)
    st.error(str(exc) if isinstance(exc, ValueError) else '操作未完成，请保留填写内容后重试。')


def navigate_event(source_type, source_id, *, event_id=None, from_list=False, warning=False):
    from ui.common import GLOBAL_PAGE_SESSION_KEYS, open_global_page
    if not from_list:
        st.session_state['ooc_return_pages'] = {k: st.session_state.get(k, False) for k in GLOBAL_PAGE_SESSION_KEYS}
    st.session_state['ooc_selection'] = dict(source_type=source_type, source_id=int(source_id),
        event_id=event_id, from_list=from_list, warning=warning, request_id=uuid4().hex)
    open_global_page('show_out_of_control_page')


def render_abnormal_entry(source_type, source_id, *, warning=False, key='latest'):
    label = '打开警告处理' if warning else '打开失控处理'
    if st.button(label, key=f'ooc_open_{source_type}_{source_id}_{key}'):
        navigate_event(source_type, source_id, warning=warning)


def render_record_selector(source_type, records, *, key):
    """Rows already have formal abnormal classifications from the existing workbench."""
    if not records:
        return
    rows = {int(r.get('run_id', r.get('id'))): r for r in records}
    selected = st.selectbox('选择需要处理的异常记录', [None, *rows], key=f'ooc_source_{key}',
        format_func=lambda v: '请选择' if v is None else
        f"{rows[v].get('test_time', '')}｜{display(rows[v].get('run_status', rows[v].get('status')))}｜检测 {v}")
    if selected is not None:
        row = rows[selected]
        render_abnormal_entry(source_type, selected,
            warning=row.get('run_status', row.get('status')) in ('warning', '警告'), key=key)


def render_snapshot(snapshot):
    st.markdown('**原检测依据**')
    fields = [('project_name', '项目'), ('test_item_name', '检验项目'), ('instrument_name', '仪器'),
              ('test_time', '检测时间'), ('method_name', '方法学'), ('unit_symbol', '单位')]
    rows = [{'项目': label, '记录内容': display(snapshot.get(k))} for k, label in fields]
    rows += [{'项目': '输入值类型', '记录内容': get_input_value_type_label(snapshot['input_value_type'])
              if snapshot.get('input_value_type') else '未记录'},
             {'项目': '原检测结论', '记录内容': display(snapshot.get('classification'))},
             {'项目': '触发规则', '记录内容': display(snapshot.get('rule_names'))}]
    context = snapshot.get('context') or {}
    profile = snapshot.get('target_profile') or {}
    rows += [{'项目': '实际试剂批号', '记录内容': display(context.get('reagent_lot_no'))},
             {'项目': '参数版本', '记录内容': f"第 {profile['version_no']} 版" if profile.get('version_no') else '未记录'}]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
    levels = []
    for level in snapshot.get('levels', []):
        levels.append({'质控水平': display(level.get('level_name') or str(level.get('level_id', '')).replace('Level ', '水平 ')),
            '检测值': display(level.get('log_value') if snapshot.get('input_value_type') == 'log' else level.get('value')),
            '质控品批号': display(level.get('lot_no')),
            '设定均值': display(level.get('target_mean')), 'SD': display(level.get('target_sd')),
            '水平判读': display(level.get('classification')), '触发规则': display(level.get('rule_names'))})
    if levels:
        st.dataframe(pd.DataFrame(levels), hide_index=True, width='stretch')
    if snapshot.get('manual_note'):
        st.markdown('**已有备注**')
        st.text(str(snapshot['manual_note']))
    with st.expander('检测时采用的质量要求'):
        from ui.quality_targets import render_review_summary, render_spec
        config = snapshot.get('config_snapshot') or {}
        def decode(value):
            if isinstance(value, dict): return value
            try: return json.loads(value or '{}')
            except (ValueError, TypeError): return {}
        goal = decode(config.get('quality_goal_json'))
        review = decode(config.get('quality_review_json'))
        if goal.get('spec'): render_spec(goal['spec'])
        if review: render_review_summary(review, historical=True)
        if not goal.get('spec') and not review: st.write('未记录')
    if snapshot.get('missing_fields'):
        st.info('标为“未记录”的检测资料尚不完整，请结合实验室原始记录核对。')


def _draft(event):
    drafts = st.session_state.setdefault('ooc_drafts', {})
    key = str(event['event_id'])
    if key not in drafts:
        content = deepcopy(event.get('content') or {})
        for field in CONTENT_LABELS:
            content.setdefault(field, '')
        content.setdefault('confirmation_checked', False)
        if event['status'] in ('pending', 'in_progress') and not content['patient_impact_assessment']:
            content['patient_impact_assessment'] = '待评估'
        content.setdefault('attachment_ids', list(event.get('attachment_ids') or []))
        content.setdefault('retest_refs', deepcopy(event.get('retest_refs') or []))
        drafts[key] = dict(content=content, initial=deepcopy(content),
            revision_no=event['current_revision_no'], token=uuid4().hex, discard=False,
            request_id=uuid4().hex, change_reason='')
    return drafts[key]


def _changed(ctx):
    return ctx['content'] != ctx['initial']


def _widget_key(ctx, field):
    key = 'ooc_draft_' + ctx['token'] + '_' + field
    if key not in st.session_state:
        st.session_state[key] = ctx['content'].get(field, '')
    return key


def _text(ctx, field, *, multiline=False, disabled=False, label=None):
    key = _widget_key(ctx, field)
    def remember():
        ctx['content'][field] = st.session_state[key]
    value = (st.text_area if multiline else st.text_input)(label or CONTENT_LABELS[field], key=key,
        disabled=disabled, on_change=remember)
    ctx['content'][field] = value


def _save(event, ctx, action):
    try:
        content = deepcopy(ctx['content'])
        actor = content.get('confirmer_text') if action == 'confirm' else content.get('handler_text')
        if action in ('return', 'revise'):
            actor = ctx.get('action_actor', '')
        service.save_handling(event['event_id'], ctx['revision_no'], ctx['request_id'],
            action, content, actor or '', change_reason=ctx.get('change_reason', ''))
    except Exception as exc:
        # A corrected request is a new logical operation; uncertain transport retries keep their ID.
        if isinstance(exc, ValueError): ctx['request_id'] = uuid4().hex
        show_error(exc)
        return
    st.session_state['ooc_drafts'].pop(str(event['event_id']), None)
    st.session_state['ooc_notice'] = '处理记录已保存。'
    st.rerun()


def _leave(selection):
    st.session_state.pop('ooc_selection', None)
    if not selection.get('from_list'):
        for key, value in st.session_state.pop('ooc_return_pages', {}).items():
            st.session_state[key] = value
        st.session_state['show_out_of_control_page'] = False
    st.rerun()


def _render_retests(event, ctx, *, disabled):
    st.markdown('**复测证据**')
    saved = ctx['content'].get('retest_refs', [])
    if disabled:
        for index, ref in enumerate(event.get('retest_refs') or saved):
            with st.expander(f"复测 {index + 1}：{(ref.get('snapshot') or {}).get('test_time', '')}"):
                render_snapshot(ref.get('snapshot') or ref.get('origin_snapshot') or {})
                st.write('批号或参数差异说明：' + display(ref.get('difference_reason')))
        if not saved: st.caption('尚未选择复测记录。')
        return
    try: candidates = service.list_retest_candidates(event['event_id'])
    except Exception as exc:
        show_error(exc)
        return
    by_key = {(r['source_type'], int(r['source_id'])): r for r in candidates}
    current = [(r['source_type'], int(r['source_id'])) for r in saved]
    # Preserve an already selected reference even if it is no longer a valid candidate.
    for ref, pair in zip(saved, current):
        by_key.setdefault(pair, dict(ref, unavailable=True))
    key = _widget_key(ctx, 'retest_selection')
    if not isinstance(st.session_state[key], list): st.session_state[key] = current
    selection = st.multiselect('选择后续复测', list(by_key), key=key,
        format_func=lambda v: f"{by_key[v].get('test_time', (by_key[v].get('snapshot') or {}).get('test_time', '已关联检测'))}｜{display(by_key[v].get('classification'))}｜检测 {v[1]}")
    refs = []
    for pair in selection:
        candidate = by_key[pair]
        prior = next((r for r in saved if (r['source_type'], int(r['source_id'])) == pair), {})
        with st.expander(f"复测资料：{candidate.get('test_time', pair[1])}", expanded=True):
            if candidate.get('unavailable'): st.warning('此项复测资料需要重新核对；保存处理记录前会再次检查是否可用。')
            render_snapshot(candidate.get('snapshot') or {})
            differences = candidate.get('differences') or []
            if differences:
                st.dataframe(pd.DataFrame([{'差异项目': d.get('label', '材料或参数'),
                    '原检测': display(d.get('original')), '复测': display(d.get('retest'))} for d in differences]),
                    hide_index=True, width='stretch')
            reason_key = f"ooc_retest_reason_{ctx['token']}_{pair[0]}_{pair[1]}"
            if reason_key not in st.session_state: st.session_state[reason_key] = prior.get('difference_reason', '')
            reason = st.text_area('批号或参数差异说明', key=reason_key)
        refs.append(dict(source_type=pair[0], source_id=pair[1], difference_reason=reason))
    if current != selection:
        ctx['request_id'] = uuid4().hex
    ctx['content']['retest_refs'] = refs
    persisted = {(r['source_type'], int(r['source_id'])) for r in event.get('content', {}).get('retest_refs', [])}
    if persisted - set(selection):
        ctx['change_reason'] = st.text_input('取消复测关联的原因', key=f"ooc_unlink_reason_{ctx['token']}")
    if not by_key: st.caption('暂无可确认属于同一检测系统的后续复测。')


def _render_attachments(event, ctx, *, disabled):
    from services.out_of_control_attachment_service import (stage_attachment, list_attachments,
        list_staged_attachments, read_attachment, ALLOWED_EXTENSIONS, UPLOAD_POLICY_TEXT)
    st.markdown('**处理附件**')
    attachments = list_attachments(event['event_id'])
    staged = list_staged_attachments(event['event_id'])
    staged_ids = {a['attachment_id'] for a in staged}
    attachments += staged
    ids = list(ctx['content'].get('attachment_ids') or [])
    if not disabled:
        st.caption(UPLOAD_POLICY_TEXT)
        upload = st.file_uploader('选择附件', type=list(ALLOWED_EXTENSIONS), max_upload_size=20,
            key=f"ooc_upload_{ctx['token']}")
        description = st.text_input('附件说明', key=f"ooc_upload_description_{ctx['token']}")
        if st.button('加入附件草稿', key=f"ooc_stage_{ctx['token']}", disabled=upload is None):
            try:
                result = stage_attachment(event['event_id'], upload.name, upload.getvalue(),
                    uploaded_by=ctx['content'].get('handler_text', ''), description=description)
                file_id = result.get('attachment_id', result.get('file_id', result.get('id')))
                if file_id not in ids: ids.append(file_id)
                ctx['content']['attachment_ids'] = ids
                st.rerun()
            except Exception as exc: show_error(exc)
        st.caption('加入附件后，请保存处理记录。此前已保存的处理记录中使用过的附件仍可查阅。')
    if disabled: ids = list(event.get('attachment_ids') or [])
    for attachment in attachments:
        identifier = attachment.get('attachment_id', attachment.get('file_id', attachment.get('id')))
        if identifier not in ids: continue
        name = attachment.get('original_name') or attachment.get('filename') or attachment.get('file_name') or '附件'
        left, right = st.columns([4, 1])
        with left:
            if identifier in staged_ids:
                st.write(name + '（本次草稿）')
            else:
                try:
                    data = read_attachment(event['event_id'], identifier)
                    st.download_button(name, data, file_name=name, key=f"ooc_file_{ctx['token']}_{identifier}")
                except Exception as exc: show_error(exc)
            if attachment.get('description'): st.caption(attachment['description'])
        if not disabled and right.button('移出本次', key=f"ooc_remove_{ctx['token']}_{identifier}"):
            ctx['content']['attachment_ids'] = [v for v in ids if v != identifier]
            st.rerun()


def _render_history(event):
    with st.expander('处理历史与报告'):
        revisions = event.get('history') or []
        if revisions:
            st.dataframe(pd.DataFrame([{'处理版本': r.get('revision_no'),
                '处理状态': STATUS_LABELS.get(r.get('status'), '未记录'), '记录人': r.get('saved_by'),
                '保存时间': r.get('saved_at'), '修改原因': r.get('change_reason') or '—'} for r in revisions]),
                hide_index=True, width='stretch')
            selected = st.selectbox('查看已保存处理版本', [r['revision_no'] for r in revisions],
                index=len(revisions)-1, key=f"ooc_history_{event['event_id']}_{event['current_revision_no']}",
                format_func=lambda n: f'第 {n} 版')
            historical = service.get_event(event['event_id'], selected)
            for field, label in CONTENT_LABELS.items():
                if historical.get('content', {}).get(field):
                    st.markdown(f'**{label}**')
                    st.text(str(historical['content'][field]))
            from services.out_of_control_attachment_service import list_attachments, read_attachment
            for attachment in list_attachments(event['event_id'], selected):
                try:
                    data = read_attachment(event['event_id'], attachment['attachment_id'], selected)
                    name = attachment.get('original_name') or '处理附件'
                    st.download_button('历史附件：' + name, data, file_name=name,
                        key=f"ooc_history_attachment_{event['event_id']}_{selected}_{attachment['attachment_id']}")
                except Exception as exc: show_error(exc)
            from services.out_of_control_report_service import generate_event_report, list_event_reports, read_event_report
            if st.button('生成此版本处理报告', key=f"ooc_report_{event['event_id']}"):
                try:
                    generate_event_report(event['event_id'], selected)
                    st.success('处理报告已归档。')
                except Exception as exc: show_error(exc)
            for report in list_event_reports(event['event_id']):
                try:
                    data = read_event_report(report['report_id'])
                    st.download_button(f"下载第 {report['revision_no']} 版处理报告", data,
                        file_name=report['file_name'], mime='application/pdf', key=f"ooc_pdf_{report['report_id']}")
                except Exception as exc: show_error(exc)


def render_event_detail(selection):
    if selection.get('event_id') is None:
        st.subheader('警告处理' if selection.get('warning') else '失控处理')
        st.text_input('登记人', key='ooc_open_actor')
        if st.button('登记并打开处理记录', type='primary'):
            try:
                event = service.open_event(selection['source_type'], selection['source_id'],
                    selection['request_id'], st.session_state['ooc_open_actor'], explicit_warning=selection.get('warning', False))
                selection['event_id'] = event['event_id']
                st.rerun()
            except Exception as exc: show_error(exc)
        if st.button('返回', key='ooc_before_open_back'): _leave(selection)
        return
    try: event = service.get_event(selection['event_id'])
    except Exception as exc:
        show_error(exc)
        if st.button('返回', key='ooc_missing_back'): _leave(selection)
        return
    ctx = _draft(event)
    if st.button('返回原记录' if not selection.get('from_list') else '返回处理列表', key='ooc_detail_back'):
        if _changed(ctx):
            ctx['discard'] = True
            st.rerun()
        _leave(selection)
    if ctx['discard']:
        st.warning('本次填写尚未保存。')
        a, b, c = st.columns(3)
        if a.button('继续填写'):
            ctx['discard'] = False
            st.rerun()
        if b.button('保留本次草稿并返回'):
            ctx['discard'] = False
            _leave(selection)
        if c.button('放弃本次修改'):
            st.session_state['ooc_drafts'].pop(str(event['event_id']), None)
            _leave(selection)
        return
    if notice := st.session_state.pop('ooc_notice', ''): st.success(notice)
    classification = display(event['original_classification'])
    st.subheader(f'{classification}处理')
    st.caption(f"处理状态：{STATUS_LABELS[event['status']]}｜已保存第 {event['current_revision_no']} 版")
    if event.get('evaluation_changed'): st.info('这次检测的判读已有更新。这里保留登记时的检测依据和处理记录，请结合最新判读核对。')
    stale = ctx['revision_no'] != event['current_revision_no']
    if stale:
        st.warning('处理资料已更新。本次未保存输入仍保留，请核对后重新打开。')
        if st.button('放弃本次输入并载入最新处理记录'):
            st.session_state['ooc_drafts'].pop(str(event['event_id']), None)
            st.rerun()
    render_snapshot(event['origin_snapshot'])
    disabled = event['status'] in ('pending_confirmation', 'completed') or stale
    st.markdown('**原因与纠正措施**')
    key = _widget_key(ctx, 'cause_category')
    categories = ['', *getattr(service, 'CAUSE_CATEGORIES', ['试剂', '仪器', '质控品', '操作', '环境', '其他'])]
    if st.session_state[key] not in categories: categories.append(st.session_state[key])
    ctx['content']['cause_category'] = st.selectbox('原因分类', categories, key=key, disabled=disabled,
        format_func=lambda v: v or '请选择')
    for field in ('cause_analysis', 'corrective_action', 'supplementary_note'):
        _text(ctx, field, multiline=True, disabled=disabled)
    _text(ctx, 'handler_text', disabled=disabled)
    with st.expander('患者影响评估', expanded=True):
        key = _widget_key(ctx, 'patient_impact_assessment')
        assessments = ['', '待评估', '需要评估', '无需评估']
        if not disabled and st.session_state[key] == '': st.session_state[key] = '待评估'
        if st.session_state[key] not in assessments: assessments.append(st.session_state[key])
        ctx['content']['patient_impact_assessment'] = st.selectbox('是否需要评估患者影响',
            assessments, key=key, disabled=disabled, format_func=lambda v: v or '未记录')
        st.caption('记录影响范围和评估依据，由实验室判断后续处置。')
        for field in ('patient_impact_start', 'patient_impact_end'):
            _text(ctx, field, disabled=disabled, label=CONTENT_LABELS[field] + '（可选，年-月-日 时:分:秒）')
        for field in ('patient_impact_scope', 'patient_impact_actions', 'patient_impact_basis'):
            _text(ctx, field, multiline=True, disabled=disabled)
    _render_retests(event, ctx, disabled=disabled)
    _render_attachments(event, ctx, disabled=disabled)
    for field in ('effect_description', 'effect_evidence'):
        _text(ctx, field, multiline=True, disabled=disabled)
    if event['status'] in ('pending', 'in_progress'):
        a, b = st.columns(2)
        if a.button('保存处理草稿', type='primary', disabled=stale): _save(event, ctx, 'save_draft')
        if b.button('提交效果确认', disabled=stale or event['status'] == 'pending',
            help='请先保存处理草稿，再提交效果确认。' if event['status'] == 'pending' else None):
            _save(event, ctx, 'submit')
    elif event['status'] == 'pending_confirmation':
        st.markdown('**人工确认**')
        _text(ctx, 'confirmer_text', disabled=stale)
        _text(ctx, 'confirmed_at', disabled=stale, label='确认时间（年-月-日 时:分:秒）')
        ctx['content']['confirmation_checked'] = st.checkbox('已核对处理效果及相关依据', key=f"ooc_confirm_{ctx['token']}", disabled=stale)
        if st.button('确认完成', type='primary', disabled=stale): _save(event, ctx, 'confirm')
        ctx['change_reason'] = st.text_input('退回原因', key=f"ooc_return_reason_{ctx['token']}")
        ctx['action_actor'] = st.text_input('本次退回人', key=f"ooc_return_actor_{ctx['token']}")
        if st.button('退回继续处理', disabled=stale): _save(event, ctx, 'return')
    else:
        ctx['change_reason'] = st.text_input('补充或更正原因', key=f"ooc_revision_reason_{ctx['token']}")
        ctx['action_actor'] = st.text_input('本次修订人', key=f"ooc_revision_actor_{ctx['token']}")
        if st.button('新增处理修订', disabled=stale): _save(event, ctx, 'revise')
    _render_history(event)
