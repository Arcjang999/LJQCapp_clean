"""Shared calendar selection and explicit preview/archive report actions."""
from datetime import date
import sqlite3
from uuid import uuid4

import streamlit as st

from services.report_period_service import resolve_report_period


def render_report_period(prefix, *, months=None, default_start=None, default_end=None):
    """Return one inclusive period; month shortcuts use the same date contract."""
    mode = st.radio('日期范围', ['按月快捷', '自选日期'], horizontal=True, key=prefix + '_period_mode')
    options = sorted(set(months or []), reverse=True)
    try:
        if mode == '按月快捷':
            if options:
                month = st.selectbox('报告月份', options, key=prefix + '_month',
                    format_func=lambda value: value.replace('-', '年') + '月')
            else:
                chosen = st.date_input('选择月份', value=date.today().replace(day=1), key=prefix + '_month_date')
                month = chosen.strftime('%Y-%m')
            period = resolve_report_period(month)
        else:
            initial_month = st.session_state.get(prefix + '_month') or (options[0] if options else date.today().strftime('%Y-%m'))
            initial = resolve_report_period(initial_month)
            left, right = st.columns(2)
            start = left.date_input('开始日期', value=date.fromisoformat(str(default_start or initial.start_date)), key=prefix + '_start')
            end = right.date_input('结束日期', value=date.fromisoformat(str(default_end or initial.end_date)), key=prefix + '_end')
            period = resolve_report_period(start_date=start, end_date=end)
    except ValueError as error:
        st.warning(str(error))
        return None
    st.caption('所选期间：' + period.label + '，包含起止日期当日。')
    return period


def render_handling_versions(options, *, prefix):
    selected = {}
    if options:
        with st.expander('选择报告要使用的处理记录', expanded=False):
            st.caption('原检测日期在所选期间内的处理记录会列入报告；所选记录中的复测和附件也会一并保存。')
            for item in options:
                versions = {int(row['revision_no']): row for row in item['versions']}
                keys = sorted(versions, reverse=True)
                prior = int(item['selected_revision_no'])
                key = f"{prefix}_handling_{item['event_id']}"
                if st.session_state.get(key) not in keys:
                    st.session_state[key] = prior if prior in keys else keys[0]
                selected[int(item['event_id'])] = st.selectbox(
                    '原检测 ' + str(item['test_time']), keys, key=key,
                    format_func=lambda value, rows=versions: f"第 {value} 版｜{rows[value]['status_label']}｜{rows[value].get('saved_at') or ''}")
    return selected


def _versions(package, method):
    from services.report_archive_service import list_report_handling_versions
    return list_report_handling_versions(package, method=method)


def render_saved_report_downloads(export_id, *, prefix, include_pdf=True):
    from services.report_service import get_report_history_record, read_report_history_pdf
    from services.report_archive_service import get_report_archive_metadata, read_report_archive
    try:
        record = get_report_history_record(export_id)
        pdf = read_report_history_pdf(record)
        if include_pdf:
            st.download_button('下载 PDF', pdf, file_name=record.file_name, mime='application/pdf', key=prefix + '_download')
        metadata = get_report_archive_metadata(export_id)
        if metadata:
            st.download_button('下载报告和附件（ZIP）', read_report_archive(export_id), file_name=metadata['archive_name'],
                               mime='application/zip', key=prefix + '_archive_download')
    except (ValueError, sqlite3.Error, OSError) as error:
        st.error(str(error) if isinstance(error, ValueError) else '已保存报告暂时无法读取，请稍后重试。')


def render_single_report(method, batch_id, *, months, preview_renderer):
    from services import report_service as service
    prefix = f'{method}_monthly_report_{batch_id}'
    period = render_report_period(prefix, months=months)
    if period is None:
        return
    build = getattr(service, f'build_{method}_period_report_package')
    render = getattr(service, f'build_{method}_period_report_pdf')
    save = getattr(service, f'save_{method}_period_report_snapshot')
    preview_key = prefix + '_preview'
    if st.button('核对报告', key=prefix + '_generate', width='stretch'):
        try:
            package = build(batch_id, period.start_date, period.end_date, report_month=period.report_month or None)
            options = _versions(package, method)
        except (ValueError, sqlite3.Error, OSError) as error:
            st.warning(str(error) if isinstance(error, ValueError) else '资料暂时无法读取，请稍后重新核对。')
        else:
            st.session_state[preview_key] = dict(period=period.to_payload(), package=package,
                options=options, request_key=uuid4().hex, batch_id=batch_id)
    state = st.session_state.get(preview_key)
    if not state or state.get('period') != period.to_payload():
        return
    if state.get('snapshot_id'):
        render_saved_report_downloads(state['snapshot_id'], prefix=prefix)
        st.caption(f"报告已保存，编号：{state['snapshot_id']}。可在“报告历史”查找和下载。")
        with st.expander('已保存报告预览', expanded=False):
            preview_renderer(state['package'])
        if st.button('核对并生成另一份报告', key=prefix + '_new'):
            st.session_state.pop(preview_key, None)
            st.rerun()
        return
    chosen = render_handling_versions(state['options'], prefix=prefix + '_' + state['request_key'])
    frozen = {int(row['event_id']): int(row['selected_revision_no']) for row in state['options']}
    changed = chosen != frozen
    if changed:
        st.info('您选了另一版处理记录，请先点“更新报告预览”，再生成报告。')
        if st.button('更新报告预览', key=prefix + '_refresh'):
            try:
                package = build(batch_id, period.start_date, period.end_date,
                    report_month=period.report_month or None, handling_versions=chosen)
                options = _versions(package, method)
            except (ValueError, sqlite3.Error, OSError) as error:
                st.warning(str(error) if isinstance(error, ValueError) else '资料暂时无法读取，已核对内容仍保留，请稍后重试。')
            else:
                state.update(package=package, options=options, request_key=uuid4().hex)
                st.rerun()
    with st.expander('报告预览', expanded=False):
        preview_renderer(state['package'])
    left, right = st.columns(2)
    if left.button('生成并保存报告', key=prefix + '_archive', disabled=changed, type='primary', width='stretch'):
        try:
            pdf = render(state['package'])
            state['snapshot_id'] = save(state['package'], pdf, request_key=state['request_key'])
        except (ValueError, sqlite3.Error, OSError) as error:
            st.error(str(error) if isinstance(error, ValueError) else '报告暂未保存，请稍后重试。已核对的内容仍保留。')
        else:
            st.rerun()
    if right.button('取消预览', key=prefix + '_cancel', width='stretch'):
        st.session_state.pop(preview_key, None)
        st.rerun()
