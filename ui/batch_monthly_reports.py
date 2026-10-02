"""Routine monthly reports: review the scope, generate separately, retrieve originals."""
from datetime import date
from uuid import uuid4

import pandas as pd
import streamlit as st

from services.batch_monthly_report_service import (
    METHOD_LABELS, STATUS_LABELS, list_monthly_report_choices, preview_monthly_reports,
    create_monthly_report_job, list_monthly_report_jobs, get_monthly_report_job,
    run_monthly_report_item, read_monthly_report_item, build_monthly_report_zip, monthly_job_summary,
)
from services.search_service import SEARCH_HELP, filter_frame


def _preview_table(rows):
    return pd.DataFrame([{'项目': row['project_name'], '仪器': row['instrument_name'], '质控批号': row['lot_no'],
        '月份': row['report_month'], '质控方法': row['method_label'], '正式期检测次数': row.get('formal_count'),
        '说明': row.get('reason') or row.get('quality_note', '')} for row in rows])


def _run_selected(job, ids):
    status, progress = st.empty(), st.progress(0)
    for index, item in enumerate([r for r in job['items'] if r['id'] in ids], 1):
        status.info('正在生成：' + item['selection']['project_name'])
        try:
            run_monthly_report_item(job['id'], item['id'])
        except ValueError as exc:
            st.warning(str(exc))
        progress.progress(index / len(ids))
    status.empty()


def _render_job(job_id):
    job = get_monthly_report_job(job_id)
    counts = job['counts']
    st.subheader('生成结果')
    st.caption(f"已生成 {counts['succeeded']} 份；未生成 {counts['failed']} 份；待继续 {counts['pending'] + counts['generating']} 份。")
    search = st.text_input('查找本次报告', key='monthly_job_search', help=SEARCH_HELP, placeholder='项目、仪器或批号')
    frame = pd.DataFrame([dict(item_id=item['id'], 项目=item['selection']['project_name'],
        仪器=item['selection']['instrument_name'], 质控批号=item['selection']['lot_no'],
        质控方法=METHOD_LABELS[item['qc_method']], 状态=STATUS_LABELS[item['status']], 说明=item['error_message']) for item in job['items']])
    if not frame.empty:
        if search.strip():
            frame = filter_frame(frame, search, ['项目', '仪器', '质控批号', '质控方法'])
        st.dataframe(frame.drop(columns='item_id'), hide_index=True, width='stretch')
    retryable = {item['id']: item for item in job['items'] if item['status'] != 'succeeded'}
    if retryable:
        chosen = st.multiselect('本次继续生成的报告', list(retryable), default=list(retryable),
            format_func=lambda value: retryable[value]['selection']['project_name'] + '｜' + retryable[value]['selection']['lot_no'],
            key='monthly_retry_' + str(job_id))
        if st.button('继续未完成或重试所选报告', type='primary', disabled=not chosen, key='monthly_retry_run'):
            _run_selected(job, chosen)
            st.rerun()
    if job['exclusions']:
        with st.expander(f"本次不生成的项目（{len(job['exclusions'])} 项）"):
            st.dataframe(_preview_table(job['exclusions']), hide_index=True, width='stretch')
    succeeded = {item['id']: item for item in job['items'] if item['status'] == 'succeeded'}
    if not succeeded:
        return
    try:
        archive = build_monthly_report_zip(job_id)
        st.download_button('下载已生成报告及完整清单', archive, file_name=f"{job['report_month']}_月报汇集_{job_id}.zip",
                           mime='application/zip', key='monthly_zip_download')
    except Exception:
        st.error('下载文件暂未准备好，已保存报告仍保留。可重新打开或逐份下载。')
    selected = st.selectbox('查看单份报告', list(succeeded),
        format_func=lambda value: succeeded[value]['selection']['project_name'] + '｜' + succeeded[value]['selection']['lot_no'] +
            '｜' + succeeded[value]['selection']['instrument_name'], key='monthly_individual_' + str(job_id))
    chosen = succeeded[selected]
    try:
        report = read_monthly_report_item(job_id, selected)
        st.download_button('下载此份原报告', report['pdf_bytes'], file_name=report['file_name'], mime='application/pdf', key='monthly_single_download')
    except ValueError as exc:
        st.error(str(exc))
    if st.button('查看此份报告历史', key='monthly_open_history'):
        from ui.common import open_global_page
        st.session_state['report_history_selected_export_id'] = chosen['export_id']
        st.session_state['report_history_return_page'] = 'show_batch_monthly_reports_page'
        open_global_page('show_report_history_page')
        st.rerun()
    summary = monthly_job_summary(job_id)
    st.subheader('月度回顾')
    st.caption(summary['scope_note'])
    st.dataframe(pd.DataFrame([{'项目': row['project_name'], '仪器': row['instrument_name'], '批次': row['batch_label'],
        '计数依据': row['counting_unit'], '正式期检测次数': row['formal_count'], '在控': row['in_control_count'],
        '警告': row['warning_count'], '失控': row['out_of_control_count'], '待判读': row['undetermined_count'],
        '待处理检测': row['pending_event_count'], '尚未登记处理': row['unopened_count'], '已登记待完成': row['registered_pending_count']} for row in summary['reports']]), hide_index=True, width='stretch')
    st.markdown('**各参数版本的 CV 评价**')
    st.dataframe(pd.DataFrame([{'项目': row['project_name'], '批次': row['batch_label'], '水平及参数版本': row['level'],
        '在控结果数': row['count'], '检测日数': row['days'], 'CV（%）': row['cv'], '采用要求': row['requirement'],
        '评价': row['decision']} for row in summary['cv_rows']]), hide_index=True, width='stretch')
    if summary['events']:
        from services.out_of_control_service import STATUS_LABELS as EVENT_STATUS_LABELS
        events = {(row['source_type'], row['source_id']): row for row in summary['events']}
        event_id = st.selectbox('选择要查看的异常检测及处理记录', list(events), key='monthly_event_link',
            format_func=lambda value: events[value]['project_name'] + '｜' + str(events[value]['test_time']) +
                '｜' + EVENT_STATUS_LABELS.get(events[value]['status'], '待核对'))
        if st.button('打开处理记录', key='monthly_open_event'):
            from ui.out_of_control import navigate_event
            event = events[event_id]
            navigate_event(event['source_type'], event['source_id'], event_id=event['event_id'])


def render_batch_monthly_reports_page():
    if st.button('返回当前页面', key='monthly_close'):
        st.session_state['show_batch_monthly_reports_page'] = False
        st.rerun()
    from ui.common import render_module_header
    render_module_header('批量月报',
        '选择月份、仪器和项目，核对清单后生成单水平和多水平月报。已生成的报告会保留，未生成项可单独重试；即时法及临时质控不计入本页。',
        tone='reports',eyebrow='报告管理')
    choices = list_monthly_report_choices()
    selected_month = st.date_input('报告月份', value=date.today().replace(day=1), key='monthly_month')
    month = selected_month.strftime('%Y-%m')
    left, right = st.columns(2)
    instrument = left.selectbox('仪器', [None, *choices['instruments']],
        format_func=lambda value: '全部仪器' if value is None else choices['instruments'][value], key='monthly_instrument')
    template = right.selectbox('项目', [None, *choices['templates']],
        format_func=lambda value: '全部项目' if value is None else choices['templates'][value], key='monthly_template')
    methods = st.multiselect('质控方法', list(METHOD_LABELS), default=list(METHOD_LABELS),
                            format_func=METHOD_LABELS.get, key='monthly_methods')
    if st.button('核对待生成清单', type='primary', disabled=not methods, key='monthly_preview'):
        try:
            preview = preview_monthly_reports(month, lab_instrument_id=instrument, template_id=template, methods=methods)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state['monthly_prepared'] = preview
            st.session_state['monthly_request_id'] = uuid4().hex
    preview = st.session_state.get('monthly_prepared')
    if preview:
        valid_scope = preview['scope'] == dict(report_month=month, lab_instrument_id=instrument, template_id=template, methods=sorted(methods))
        if not valid_scope:
            st.info('月份或范围已变化，请重新核对清单。')
        else:
            st.subheader('待生成清单')
            if preview['items']:
                st.dataframe(_preview_table(preview['items']), hide_index=True, width='stretch')
                candidates = {row['unit_key']: row for row in preview['items']}
                chosen = st.multiselect('选择本次生成的报告', list(candidates), default=list(candidates),
                    format_func=lambda key: candidates[key]['project_name'] + '｜' + candidates[key]['lot_no'],
                    key='monthly_selection_' + st.session_state['monthly_request_id'])
                if st.button('生成所选月报并分别归档', type='primary', disabled=not chosen, key='monthly_generate'):
                    try:
                        job_id = create_monthly_report_job(preview, chosen, st.session_state['monthly_request_id'])
                        st.session_state['monthly_current_job'] = job_id
                        st.session_state.pop('monthly_job_selector', None)
                        _run_selected(get_monthly_report_job(job_id), [r['id'] for r in get_monthly_report_job(job_id)['items'] if r['status'] != 'succeeded'])
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.rerun()
            else:
                st.info('所选月份和范围内没有可生成的正式期月报。请查看下方原因，或调整月份、仪器和项目。')
            if preview['exclusions']:
                st.markdown('**本范围不生成的项目**')
                st.dataframe(_preview_table(preview['exclusions']), hide_index=True, width='stretch')
    jobs = list_monthly_report_jobs()
    if jobs:
        labels = {r['id']: f"{r['report_month']}｜{r['created_at']}｜已生成 {r['succeeded_count']} / {r['item_count']} 份" for r in jobs}
        current = st.session_state.get('monthly_current_job')
        job_id = st.selectbox('查看历次月报生成结果', list(labels), index=list(labels).index(current) if current in labels else 0,
                             format_func=labels.get, key='monthly_job_selector')
        st.session_state['monthly_current_job'] = job_id
        _render_job(job_id)
