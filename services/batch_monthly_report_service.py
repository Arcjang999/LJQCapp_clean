"""Read-only monthly selection and separately committed, retryable PDF archives."""
from datetime import datetime
from io import BytesIO, StringIO
import csv
import json
import logging
import re
import sqlite3
from functools import wraps
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from database import atomic_write, get_connection, read_snapshot
from services.monthly_report_source_service import digest, encode, monthly_source_version
from services.report_service import (
    build_lj_monthly_report_package, build_zscore_monthly_report_package,
    build_lj_monthly_report_pdf, build_zscore_monthly_report_pdf,
    save_lj_monthly_report_snapshot, save_zscore_monthly_report_snapshot,
    get_report_history_record, read_report_history_pdf, _normalize_report_month,
)

logger = logging.getLogger(__name__)
METHOD_LABELS = {'lj': '单水平（LJ）', 'zscore': '多水平法', 'instant': '即时法'}
STATUS_LABELS = {'pending': '待生成', 'generating': '处理中', 'succeeded': '已生成', 'failed': '未生成'}


def _rows(c, sql, args=()):
    return [dict(row) for row in c.execute(sql, args)]


def _candidates(c):
    candidates = _rows(c, '''SELECT p.method_type AS qc_method,b.id AS batch_id,b.project_id,
        p.name AS project_name,COALESCE(t.template_name,'') AS template_name,c.template_id,
        c.lab_instrument_id,COALESCE(l.display_name,b.instrument,'未记录') AS instrument_name,
        b.lot_no,b.qc_material,i.id AS lot_config_item_id,c.id AS lot_config_id
        FROM batches b JOIN projects p ON p.id=b.project_id
        LEFT JOIN qc_workbench_bindings w ON (w.qc_method=p.method_type AND w.runtime_batch_id=b.id)
            OR (b.source_method='instant' AND w.qc_method='instant' AND w.runtime_batch_id=b.source_instant_batch_id)
        LEFT JOIN qc_lot_config_items i ON i.id=w.lot_config_item_id
        LEFT JOIN qc_lot_configs c ON c.id=i.lot_config_id
        LEFT JOIN qc_project_templates t ON t.id=c.template_id
        LEFT JOIN lab_instruments l ON l.id=c.lab_instrument_id ORDER BY p.name,b.id''')
    candidates += _rows(c, '''SELECT 'instant' AS qc_method,b.id AS batch_id,b.project_id,p.name AS project_name,
        COALESCE(t.template_name,'') AS template_name,c.template_id,c.lab_instrument_id,
        COALESCE(l.display_name,b.instrument,'未记录') AS instrument_name,b.lot_no,b.qc_material,
        i.id AS lot_config_item_id,c.id AS lot_config_id
        FROM instant_batches b JOIN instant_projects p ON p.id=b.project_id
        LEFT JOIN qc_workbench_bindings w ON w.qc_method='instant' AND w.runtime_batch_id=b.id
        LEFT JOIN qc_lot_config_items i ON i.id=w.lot_config_item_id
        LEFT JOIN qc_lot_configs c ON c.id=i.lot_config_id
        LEFT JOIN qc_project_templates t ON t.id=c.template_id
        LEFT JOIN lab_instruments l ON l.id=c.lab_instrument_id ORDER BY p.name,b.id''')
    result = {}
    for row in candidates:
        result[(row['qc_method'], row['batch_id'])] = row
    unbound = _rows(c, '''SELECT i.qc_method,NULL AS batch_id,NULL AS project_id,d.chinese_name AS project_name,
        t.template_name,c.template_id,c.lab_instrument_id,l.display_name AS instrument_name,
        q.lot_no,m.generic_name AS qc_material,i.id AS lot_config_item_id,c.id AS lot_config_id
        FROM qc_lot_config_items i JOIN qc_lot_configs c ON c.id=i.lot_config_id
        JOIN qc_project_templates t ON t.id=c.template_id JOIN md_test_items d ON d.id=i.test_item_id
        JOIN lab_instruments l ON l.id=c.lab_instrument_id JOIN md_qc_materials m ON m.id=c.qc_material_id
        LEFT JOIN md_qc_material_lots q ON q.id=c.qc_material_lot_id
        WHERE NOT EXISTS(SELECT 1 FROM qc_workbench_bindings w WHERE w.lot_config_item_id=i.id)
        AND i.is_disabled=0 AND c.is_disabled=0 ORDER BY i.id''')
    return list(result.values()) + unbound


def list_monthly_report_choices():
    with read_snapshot() as c:
        candidates = _candidates(c)
        instruments = {r['lab_instrument_id']: r['instrument_name'] for r in candidates if r['lab_instrument_id'] is not None}
        templates = {r['template_id']: r['template_name'] for r in candidates if r['template_id'] is not None}
        months = {r[0] for r in c.execute('''SELECT substr(test_time,1,7) FROM results
            UNION SELECT substr(test_time,1,7) FROM zscore_runs UNION SELECT substr(test_time,1,7) FROM instant_results''') if r[0]}
        return dict(instruments=instruments, templates=templates, months=sorted(months, reverse=True))


def _build(method, batch_id, month):
    return (build_lj_monthly_report_package if method == 'lj' else build_zscore_monthly_report_package)(batch_id, month)


def _preview_item(row, month):
    item = dict(row, report_month=month, unit_key=f"{row['qc_method']}:{row['batch_id']}:{month}",
                method_label=METHOD_LABELS.get(row['qc_method'], '未知方法'))
    if row['batch_id'] is None:
        return dict(item, eligible=False, unit_key=f"config:{row['lot_config_item_id']}:{month}", reason='批次配置尚未确认并开始使用，不能生成正式期月报。')
    if row['qc_method'] not in ('lj', 'zscore'):
        return dict(item, eligible=False, reason='即时法不直接生成月报；转入 LJ 后，在对应 LJ 批次生成月报。')
    try:
        package = _build(row['qc_method'], row['batch_id'], month)
        report = package.report
        if row['qc_method'] == 'zscore':
            grouped = package.monthly_plot_df.groupby('run_id') if 'run_id' in package.monthly_plot_df else []
            if any(len(set(group.level_id)) != len(package.active_levels) for _, group in grouped):
                raise ValueError('本月存在水平不完整的检测，请先核对原始记录。')
        item.update(eligible=True, reason='', formal_count=report.statistics.formal_count,
            source_fingerprint=report.source_version['fingerprint'], source_version=report.source_version,
            parameter_versions=report.source_version['parameter_versions'],
            quality_note=report.quality_summary.get('evaluation_reason') or '按每个参数版本及水平核对质量要求')
    except ValueError as exc:
        item.update(eligible=False, reason=str(exc))
    except Exception:
        logger.exception('Monthly preview failed for %s', item['unit_key'])
        item.update(eligible=False, reason='该批次资料暂不能用于月报，请核对参数、水平和质量要求后重试。')
    return item


def preview_monthly_reports(report_month, lab_instrument_id=None, template_id=None, methods=None):
    month = _normalize_report_month(report_month)
    methods = sorted(set(methods if methods is not None else METHOD_LABELS))
    if set(methods) - set(METHOD_LABELS):
        raise ValueError('请选择支持的质控方法。')
    scope = dict(report_month=month, lab_instrument_id=lab_instrument_id, template_id=template_id, methods=methods)
    with read_snapshot() as c:
        selected = [row for row in _candidates(c) if row['qc_method'] in methods
            and (lab_instrument_id is None or row['lab_instrument_id'] == int(lab_instrument_id))
            and (template_id is None or row['template_id'] == int(template_id))]
        items = [_preview_item(row, month) for row in selected]
        return dict(scope=scope, items=[r for r in items if r['eligible']],
                    exclusions=[r for r in items if not r['eligible']], preview_fingerprint=digest(dict(scope=scope, items=items)))


def _database_busy_as_business_error(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except sqlite3.OperationalError as exc:
            logger.exception('Monthly report database operation unavailable')
            raise ValueError('资料正在保存或暂时无法读取，请稍后重试。已保存报告仍保留，未完成项可以继续。') from exc
    return wrapped


@_database_busy_as_business_error
def create_monthly_report_job(preview, selected_keys, request_id):
    if not str(request_id or '').strip():
        raise ValueError('请重新核对月报清单后生成。')
    if not isinstance(selected_keys, (list, tuple)) or not selected_keys or len(set(selected_keys)) != len(selected_keys):
        raise ValueError('请至少选择一份不重复的月报。')
    selected = {row['unit_key']: row for row in preview.get('items', [])}
    if any(key not in selected for key in selected_keys):
        raise ValueError('所选报告不在可生成清单中，请重新预览。')
    request_digest = digest(dict(scope=preview['scope'], selection=[selected[k] for k in sorted(selected_keys)],
                                 exclusions=preview.get('exclusions', [])))
    with atomic_write() as c:
        prior = c.execute('SELECT * FROM qc_monthly_report_jobs WHERE request_id=?', (request_id,)).fetchone()
        if prior:
            if prior['request_digest'] != request_digest:
                raise ValueError('同次生成选择已变化，请重新核对清单。')
            return int(prior['id'])
        for key in selected_keys:
            item = selected[key]
            fresh = monthly_source_version(item['qc_method'], item['batch_id'], item['report_month'])
            if fresh['fingerprint'] != item['source_fingerprint']:
                raise ValueError(item['project_name'] + '的数据或设置已变化，请重新预览。')
        job = c.execute('''INSERT INTO qc_monthly_report_jobs(request_id,request_digest,report_month,scope_json,exclusions_json)
            VALUES(?,?,?,?,?)''', (request_id, request_digest, preview['scope']['report_month'], encode(preview['scope']),
                                  encode(preview.get('exclusions', [])))).lastrowid
        for key in selected_keys:
            item = selected[key]
            c.execute('''INSERT INTO qc_monthly_report_job_items(job_id,unit_key,qc_method,batch_id,report_month,
                source_fingerprint,selection_json) VALUES(?,?,?,?,?,?,?)''',
                (job, key, item['qc_method'], item['batch_id'], item['report_month'], item['source_fingerprint'], encode(item)))
        return int(job)


def get_monthly_report_job(job_id):
    with read_snapshot() as c:
        job = c.execute('SELECT * FROM qc_monthly_report_jobs WHERE id=?', (int(job_id),)).fetchone()
        if job is None:
            raise ValueError('未找到所选月报任务。')
        job = dict(job)
        job['scope'] = json.loads(job.pop('scope_json'))
        job['exclusions'] = json.loads(job.pop('exclusions_json'))
        job['items'] = _rows(c, 'SELECT * FROM qc_monthly_report_job_items WHERE job_id=? ORDER BY id', (job_id,))
        for item in job['items']:
            item['selection'] = json.loads(item.pop('selection_json'))
        job['counts'] = {state: sum(row['status'] == state for row in job['items']) for state in STATUS_LABELS}
        return job


def list_monthly_report_jobs():
    with read_snapshot() as c:
        return _rows(c, '''SELECT j.id,j.report_month,j.created_at,COUNT(i.id) AS item_count,
            SUM(CASE WHEN i.status='succeeded' THEN 1 ELSE 0 END) AS succeeded_count
            FROM qc_monthly_report_jobs j LEFT JOIN qc_monthly_report_job_items i ON i.job_id=j.id
            GROUP BY j.id ORDER BY j.id DESC''')


@_database_busy_as_business_error
def run_monthly_report_item(job_id, item_id):
    # The initial committed state makes interruptions visible. The next complete
    # per-report write transaction serializes concurrent retries, even across
    # processes; after restart an unfinished item has no half-committed archive.
    with atomic_write() as c:
        row = c.execute('SELECT * FROM qc_monthly_report_job_items WHERE id=? AND job_id=?', (item_id, job_id)).fetchone()
        if row is None:
            raise ValueError('未找到本次选择的月报。')
        if row['status'] == 'succeeded':
            return dict(row)
        c.execute("UPDATE qc_monthly_report_job_items SET status='generating',error_message='',updated_at=CURRENT_TIMESTAMP WHERE id=?", (item_id,))
    try:
        with atomic_write() as c:
            row = dict(c.execute('SELECT * FROM qc_monthly_report_job_items WHERE id=?', (item_id,)).fetchone())
            if row['status'] == 'succeeded':
                return row
            current = monthly_source_version(row['qc_method'], row['batch_id'], row['report_month'])
            if current['fingerprint'] != row['source_fingerprint']:
                raise ValueError('源数据、参数或处理资料已变化，请重新预览后另存新报告。')
            package = _build(row['qc_method'], row['batch_id'], row['report_month'])
            renderer, saver = ((build_lj_monthly_report_pdf, save_lj_monthly_report_snapshot) if row['qc_method'] == 'lj'
                               else (build_zscore_monthly_report_pdf, save_zscore_monthly_report_snapshot))
            pdf = renderer(package)
            if monthly_source_version(row['qc_method'], row['batch_id'], row['report_month'])['fingerprint'] != row['source_fingerprint']:
                raise ValueError('生成期间数据发生变化，请重新预览后生成。')
            export_id = saver(package, pdf)
            c.execute("""UPDATE qc_monthly_report_job_items SET status='succeeded',export_id=?,error_message='',
                attempts=attempts+1,updated_at=CURRENT_TIMESTAMP WHERE id=?""", (export_id, item_id))
    except Exception as exc:
        logger.exception('Monthly PDF generation failed: job=%s item=%s', job_id, item_id)
        message = str(exc) if isinstance(exc, ValueError) else '报告未能生成，已成功的报告仍保留。请重试本项；如仍失败，请核对批次资料。'
        with atomic_write() as c:
            c.execute("""UPDATE qc_monthly_report_job_items SET status='failed',error_message=?,attempts=attempts+1,
                updated_at=CURRENT_TIMESTAMP WHERE id=? AND status!='succeeded'""", (message, item_id))
    return next(item for item in get_monthly_report_job(job_id)['items'] if item['id'] == item_id)


def read_monthly_report_item(job_id, item_id):
    item = next((row for row in get_monthly_report_job(job_id)['items'] if row['id'] == int(item_id)), None)
    if not item or item['status'] != 'succeeded':
        raise ValueError('此份月报尚未成功生成。')
    record = get_report_history_record(item['export_id'])
    return dict(export_id=item['export_id'], file_name=record.file_name, pdf_bytes=read_report_history_pdf(record))


def _zip_name(item):
    row = item['selection']
    value = '_'.join(str(row.get(key) or '未记录') for key in ('project_name', 'instrument_name', 'lot_no', 'report_month'))
    safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', value).strip(' .')
    safe = safe.encode('utf-8')[:220].decode('utf-8', errors='ignore').rstrip(' .')
    return f"{safe}_{item['id']:04d}.pdf"


def build_monthly_report_zip(job_id):
    job = get_monthly_report_job(job_id)
    buffer, manifest = BytesIO(), StringIO()
    writer = csv.writer(manifest)
    writer.writerow(['项目', '仪器', '质控批号', '月份', '质控方法', '状态', '文件', '报告编号', '说明'])
    created = datetime.fromisoformat(job['created_at'])
    def write_member(archive, name, data):
        entry = ZipInfo(name, date_time=created.timetuple()[:6])
        entry.compress_type = ZIP_DEFLATED
        archive.writestr(entry, data)
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as archive:
        for item in job['items']:
            row = item['selection']
            name = ''
            if item['status'] == 'succeeded':
                result = read_monthly_report_item(job_id, item['id'])
                name = _zip_name(item)
                write_member(archive, name, result['pdf_bytes'])
            writer.writerow([row['project_name'], row['instrument_name'], row['lot_no'], row['report_month'],
                METHOD_LABELS[item['qc_method']], STATUS_LABELS[item['status']], name, item['export_id'] or '', item['error_message']])
        for row in job['exclusions']:
            writer.writerow([row['project_name'], row['instrument_name'], row['lot_no'], row['report_month'],
                row['method_label'], '不生成', '', '', row['reason']])
        write_member(archive, '月报清单.csv', manifest.getvalue().encode('utf-8-sig'))
    return buffer.getvalue()


def monthly_job_summary(job_id):
    job = get_monthly_report_job(job_id)
    reports, cv_rows, events = [], [], []
    for item in job['items']:
        if item['status'] != 'succeeded':
            continue
        record = get_report_history_record(item['export_id'])
        summary = record.summary_json
        base = dict(export_id=item['export_id'], item_id=item['id'], project_name=record.project_name,
            batch_label=record.batch_label, report_month=record.report_month, method_label=METHOD_LABELS[item['qc_method']],
            instrument_name=item['selection']['instrument_name'])
        handling = summary.get('processing_candidates', [])
        reports.append(dict(base, **{key: summary['statistics'].get(key, 0) for key in (
            'formal_count', 'in_control_count', 'warning_count', 'out_of_control_count', 'undetermined_count')},
            pending_event_count=sum(row['status'] != 'completed' for row in handling),
            unopened_count=sum(row['event_id'] is None for row in handling),
            registered_pending_count=sum(row['event_id'] is not None and row['status'] != 'completed' for row in handling),
            counting_unit='完整检测次数' if item['qc_method'] == 'zscore' else '单水平检测记录数'))
        quality = summary.get('quality_summary') or {}
        if quality.get('rows'):
            cv_rows.extend(dict(base, **row) for row in quality['rows'])
        else:
            cv_rows.append(dict(base, level='各水平', parameter_version='未评价', count=None, days=None, cv=None,
                requirement='未采用可自动评价的 CV 要求', decision=quality.get('evaluation_reason') or '仅登记依据，暂不自动评价 CV'))
        events.extend(dict(base, **row) for row in handling)
    return dict(reports=reports, cv_rows=cv_rows, events=events,
        scope_note='仅所选常规正式期；多水平法以完整检测计数，处理完成不改变原判读。临时质控不合并。')
