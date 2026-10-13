"""Frozen QC PDFs, selected handling revisions and original attachments in one archive."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
from io import BytesIO, StringIO
import csv
import json
from pathlib import PurePosixPath
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from database import read_snapshot
from services.monthly_report_source_service import encode, digest, range_source_version
from services.out_of_control_service import _get_event, STATUS_LABELS
from services.out_of_control_attachment_service import resolve_managed_path
from services.out_of_control_report_service import (
    build_event_report_package_from_snapshot, render_event_report_pdf,
)
from services.report_period_service import resolve_report_period


FORMAT_VERSION = 1
SOURCE_TYPES = {'lj': 'lj_result', 'zscore': 'zscore_run'}


def _positive(value, label):
    try:
        result = int(value)
        if isinstance(value, bool) or str(result) != str(value).strip() or result < 1:
            raise ValueError
        return result
    except (ValueError, TypeError):
        raise ValueError('请重新选择有效的' + label + '。') from None


def _versions(values):
    if values is None:
        return None
    if not isinstance(values, dict):
        raise ValueError('处理版本选择无效，请重新核对。')
    result = {}
    for key, value in values.items():
        event, revision = _positive(key, '处理记录'), _positive(value, '处理版本')
        if event in result and result[event] != revision:
            raise ValueError('同一处理记录选择了不同版本，请重新核对。')
        result[event] = revision
    return result


def _period(report):
    return resolve_report_period(start_date=report.period_start, end_date=report.period_end)


def _source_ids(package, method):
    if method not in SOURCE_TYPES:
        raise ValueError('此质控方法暂不支持完整报告归档。')
    if method == 'lj':
        rows = package.formal_df.to_dict('records')
        identifiers = [row['id'] for row in rows]
    else:
        rows = package.formal_runs
        identifiers = [row.get('run_id') or row['id'] for row in rows]
    ids = [_positive(value, '检测记录') for value in identifiers]
    if len(set(ids)) != len(ids):
        raise ValueError('报告检测清单重复，请重新核对。')
    return ids


def _scope_events(connection, source_type, source_ids):
    result = []
    ids = sorted(set(source_ids))
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        result.extend(dict(row) for row in connection.execute(
            'SELECT * FROM qc_ooc_events WHERE source_type=? AND source_id IN (' +
            ','.join('?' for _ in chunk) + ') ORDER BY id', [source_type, *chunk]))
    return result


def _frozen_versions(package):
    report = package.report
    explicit = _versions(getattr(report, 'handling_versions', None))
    if explicit is None:
        explicit = _versions((report.source_version or {}).get('handling_versions')) or {}
    for row in [*getattr(report, 'handling_summaries', []), *getattr(report, 'processing_candidates', [])]:
        if row.get('event_id') and row.get('revision_no'):
            explicit.setdefault(int(row['event_id']), int(row['revision_no']))
    return explicit


def list_report_handling_versions(package, *, method):
    """Choices are scoped by the report's original detection IDs, not handling dates."""
    ids, frozen = _source_ids(package, method), _frozen_versions(package)
    with read_snapshot() as connection:
        result = []
        for row in _scope_events(connection, SOURCE_TYPES[method], ids):
            revision = frozen.get(row['id'])
            if revision is None:
                raise ValueError('处理清单已变化，请重新核对报告。')
            versions = [dict(value) for value in connection.execute(
                'SELECT revision_no,status,saved_at,saved_by FROM qc_ooc_revisions WHERE event_id=? ORDER BY revision_no DESC',
                (row['id'],))]
            selected = next((value for value in versions if value['revision_no'] == revision), None)
            if selected is None:
                raise ValueError('所选处理版本不存在，请重新核对报告。')
            for value in versions:
                value['status_label'] = STATUS_LABELS[value['status']]
            result.append(dict(event_id=row['id'], source_type=row['source_type'], source_id=row['source_id'],
                test_time=json.loads(row['origin_snapshot_json']).get('test_time'),
                current_revision_no=row['current_revision_no'], selected_revision_no=revision,
                status=selected['status'], status_label=selected['status_label'], versions=versions))
        return result


def _selected_evidence(connection, source_type, source_ids, versions):
    events, raw = [], []
    scope = _scope_events(connection, source_type, source_ids)
    if set(versions) != {row['id'] for row in scope}:
        raise ValueError('处理清单或所选版本已变化，请重新核对报告。')
    for row in scope:
        event = _get_event(connection, row['id'], versions[row['id']])
        event['history'] = [value for value in event['history'] if value['revision_no'] <= event['revision_no']]
        event['status_history'] = [value for value in event['status_history'] if value['revision_no'] <= event['revision_no']]
        event.pop('latest_evaluation', None)
        event.pop('evaluation_changed', None)
        event.pop('report_refs', None)
        revision = dict(connection.execute('SELECT * FROM qc_ooc_revisions WHERE event_id=? AND revision_no=?',
                                           (row['id'], event['revision_no'])).fetchone())
        retests = [dict(value) for value in connection.execute(
            'SELECT * FROM qc_ooc_retests WHERE event_id=? AND revision_no=? ORDER BY source_type,source_id',
            (row['id'], event['revision_no']))]
        attachments = [dict(value) for value in connection.execute('''SELECT f.* FROM qc_event_revision_attachments a
            JOIN qc_managed_files f ON f.file_id=a.file_id AND f.event_id=a.event_id
            WHERE a.event_id=? AND a.revision_no=? ORDER BY f.uploaded_at,f.file_id''',
            (row['id'], event['revision_no']))]
        expected_ids = sorted(event.get('content', {}).get('attachment_ids', []))
        if sorted(value['file_id'] for value in attachments) != expected_ids:
            raise ValueError('所选处理版本的附件清单不完整，请核对或从完整备份恢复。')
        saved_pdf = connection.execute('SELECT * FROM qc_event_reports WHERE event_id=? AND revision_no=?',
                                       (row['id'], event['revision_no'])).fetchone()
        saved_pdf = dict(saved_pdf) if saved_pdf else None
        if saved_pdf:
            data = bytes(saved_pdf.pop('pdf_bytes'))
            if not data.startswith(b'%PDF-') or sha256(data).hexdigest() != saved_pdf['sha256']:
                raise ValueError('已归档处理报告校验不一致，请从完整备份恢复；本次尚未保存。')
        else:
            data = None
        origin = {key: value for key, value in row.items() if key != 'current_revision_no'}
        raw.append(dict(origin=origin, revision=revision, retests=retests, attachments=attachments,
                        saved_report=saved_pdf))
        events.append(dict(event=event, attachments=attachments, saved_pdf=data, saved_report=saved_pdf))
    return events, digest(raw)


def _read_original(record):
    try:
        data = resolve_managed_path(record['relative_path']).read_bytes()
    except (OSError, ValueError) as error:
        raise ValueError('所选处理版本的附件无法读取，请核对或从完整备份恢复；本次尚未保存。') from error
    if len(data) != record['size_bytes'] or sha256(data).hexdigest() != record['sha256']:
        raise ValueError('所选处理版本的附件内容已变化，请核对或从完整备份恢复；本次尚未保存。')
    return data


def _check_source(version):
    if not isinstance(version, dict) or not version.get('fingerprint'):
        raise ValueError('报告预览资料不完整，请重新打开报告页面并查看预览。')
    current = range_source_version(version.get('qc_method'), version.get('batch_id'),
        version.get('period_start'), version.get('period_end'), handling_versions=version.get('handling_versions'))
    if current['fingerprint'] != version['fingerprint']:
        raise ValueError('报告依据或处理资料已变化，请重新核对；本次尚未保存。')


def _merge_pdf(parts):
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    try:
        for index, data in enumerate(parts):
            reader = PdfReader(BytesIO(data), strict=True)
            if reader.is_encrypted or not reader.pages:
                raise ValueError('报告无法合并，请核对原PDF。')
            if index == 0 and reader.metadata:
                writer.add_metadata({key: str(value) for key, value in reader.metadata.items() if value is not None})
            for page in reader.pages:
                writer.add_page(page)
        output = BytesIO()
        writer.write(output)
        return output.getvalue()
    except (ValueError, OSError):
        raise
    except Exception as error:
        raise ValueError('报告附录无法合并，本次尚未保存。') from error
    finally:
        writer.close()


def _status_pdf(report, events, unregistered):
    """An explicit archive inventory keeps missing evidence distinct from completion."""
    if not events and not unregistered:
        return None
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from services import report_pdf_layout as layout
    from services.report_service import _resolve_pdf_font_name
    from services.out_of_control_report_service import _section
    lines = []
    for row in events:
        lines.append(f"处理记录 {row['event_id']} / 版本 {row['revision_no']}；原检测 {row['test_time']}；"
                     f"处理状态：{row['status_label']}；患者影响评估：{row['patient_impact_assessment'] or '未记录'}；"
                     f"关联复测：{row['retest_count']} 次；附件：{row['attachment_count']} 份。")
    for row in unregistered:
        lines.append(f"原检测 {row.get('test_time') or '未记录'}；检测编号 {row['source_id']}；尚未登记失控处理。")
    lines.append('按原检测日期列入处理记录。所选记录中的全部复测一并保存；报告日期以外的复测单独列出，检测次数只统计所选期间。')
    lines.append('缺项显示“未记录”，请结合原始记录核对实际评估、复测和处理完成情况。患者结果是否可放行，由实验室结合调查确认。')
    buffer = BytesIO()
    with plt.rc_context(layout._build_pdf_rc_params(_resolve_pdf_font_name())):
        with PdfPages(buffer) as pdf:
            figures = layout._build_text_pages(report_title=report.title, page_title='失控处理归档清单',
                subtitle_lines=[report.report_period_label], sections=[_section('本次采用的处理版本与资料', lines)])
            for figure in figures:
                pdf.savefig(figure)
                plt.close(figure)
    return buffer.getvalue()


def _safe_name(value):
    import re
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', str(value or '').strip()).strip('. ')
    return name[:160] or '附件'


def _write_zip(files, manifest):
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        for name, data in [*files, ('归档清单.json', encode(manifest).encode('utf-8'))]:
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, data)
    return output.getvalue()


def _request_digest(archive):
    manifest = archive['manifest']
    return digest(dict(format_version=manifest['format_version'], report_type=manifest['report_type'], source_version=archive['source_version'],
        source_ids=sorted(manifest['source_ids']), handling_versions=manifest['handling_versions'],
        archive_evidence_fingerprint=manifest['archive_evidence_fingerprint']))


def _payload_digest(archive):
    return digest(dict(source_version=archive['source_version'], manifest=archive['manifest'],
        pdf_sha256=sha256(archive['pdf_bytes']).hexdigest(), zip_sha256=sha256(archive['zip_bytes']).hexdigest(),
        file_name=archive['file_name']))


def prepare_complete_report_archive(package, core_pdf, *, method, handling_versions=None):
    """Read, freeze and render everything; preparation never stores child reports."""
    core_pdf = bytes(core_pdf)
    if not core_pdf.startswith(b'%PDF-'):
        raise ValueError('质控报告无法读取，本次尚未保存。')
    report, period = package.report, _period(package.report)
    version = deepcopy(report.source_version)
    if (version.get('qc_method') != method or version.get('batch_id') != report.batch_id
            or version.get('period_start') != period.start_date or version.get('period_end') != period.end_date):
        raise ValueError('报告日期或检测来源不一致，请重新核对。')
    source_ids, source_type = _source_ids(package, method), SOURCE_TYPES[method]
    chosen = _frozen_versions(package)
    override = _versions(handling_versions)
    if override is not None:
        declared = _versions(version.get('handling_versions'))
        if declared != override:
            raise ValueError('所选处理版本与报告预览不一致，请按同一版本重新核对。')
        chosen.update(override)
    with read_snapshot() as connection:
        _check_source(version)
        frozen, evidence = _selected_evidence(connection, source_type, source_ids, chosen)
        files, appendix_pdf, summaries, original_files = [], [], [], []
        info = asdict(report.basic_info)
        info['report_statement'] = report.declaration
        for item in frozen:
            event = item['event']
            event_id, revision = event['event_id'], event['revision_no']
            records = [dict(row, id=row['file_id'], attachment_id=row['file_id'], available=True) for row in item['attachments']]
            data = item['saved_pdf'] or render_event_report_pdf(build_event_report_package_from_snapshot(
                event, records, lab_info=info, generated_at=report.generated_at))
            appendix_pdf.append(data)
            files.append((f'失控处理报告/处理-{event_id}-V{revision}.pdf', data))
            content = event.get('content') or {}
            summaries.append(dict(event_id=event_id, revision_no=revision, source_type=source_type,
                source_id=event['source_id'], test_time=event['origin_snapshot'].get('test_time'),
                status=event['status'], status_label=STATUS_LABELS[event['status']],
                patient_impact_assessment=content.get('patient_impact_assessment', ''),
                retest_count=len(event['retest_refs']), attachment_count=len(records),
                retests=[dict(source_type=ref['source_type'], source_id=ref['source_id'],
                    test_time=ref['snapshot'].get('test_time'), origin_context_id=ref.get('origin_context_id'),
                    origin_evaluation_id=ref.get('origin_evaluation_id'), snapshot=deepcopy(ref['snapshot']),
                    difference_reason=ref['difference_reason'], differences=deepcopy(ref['differences']))
                    for ref in event['retest_refs']]))
            for record in records:
                payload = _read_original(record)
                path = f"附件/处理-{event_id}/版本-{revision}/{record['file_id']}_{_safe_name(record['original_name'])}"
                files.append((path, payload))
                original_files.append(dict(event_id=event_id, revision_no=revision, file_id=record['file_id'],
                    original_name=record['original_name'], description=record['description'], path=path,
                    sha256=record['sha256'], size_bytes=record['size_bytes']))
        unregistered = [deepcopy(row) for row in getattr(report, 'processing_candidates', [])
                        if not row.get('event_id') and int(row['source_id']) in source_ids]
        note = _status_pdf(report, summaries, unregistered)
        complete_pdf = _merge_pdf([core_pdf, *([note] if note else []), *appendix_pdf])
        files.insert(0, ('质控报告.pdf', complete_pdf))
        csv_buffer = StringIO(newline='')
        columns = ['处理记录', '处理版本', '原文件名', '附件说明', '归档位置', '大小（字节）', 'SHA256']
        writer = csv.writer(csv_buffer)
        writer.writerow(columns)
        for record in original_files:
            writer.writerow([record['event_id'], record['revision_no'], record['original_name'], record['description'],
                             record['path'], record['size_bytes'], record['sha256']])
        files.append(('附件清单.csv', csv_buffer.getvalue().encode('utf-8-sig')))
        manifest = dict(format='ljqc-complete-report', format_version=FORMAT_VERSION,
            report_type=report.report_type, qc_method=method, batch_id=int(report.batch_id), period=period.to_payload(),
            generated_at=report.generated_at, source_ids=source_ids, source_version=version,
            handling_versions={str(key): value for key, value in sorted(chosen.items())},
            archive_evidence_fingerprint=evidence, events=summaries, unregistered_sources=unregistered,
            attachments=original_files, template_basis='本实验室结构化处理记录；未取得院方逐字段模板确认',
            files=[dict(path=name, size_bytes=len(data), sha256=sha256(data).hexdigest()) for name, data in files])
        result = dict(pdf_bytes=complete_pdf, zip_bytes=_write_zip(files, manifest),
            file_name=_safe_name(PurePosixPath(report.file_name).stem) + '_完整归档.zip', manifest=manifest,
            source_version=version)
        result['request_digest'] = _request_digest(result)
        result['payload_digest'] = _payload_digest(result)
        return result


def find_existing_archive(request_key, request_digest, connection):
    key = str(request_key or '').strip()
    if not key or len(key) > 200 or not request_digest:
        raise ValueError('请重新核对报告后保存。')
    row = connection.execute('SELECT export_id,request_digest FROM qc_report_archive_files WHERE request_key=?', (key,)).fetchone()
    if row is not None and row['request_digest'] != request_digest:
        raise ValueError('本次操作已经保存了另一份报告。请到报告历史查看，再重新核对要生成的报告。')
    return int(row['export_id']) if row is not None else None


def persist_complete_report_archive(export_id, archive, *, request_key, connection):
    """The caller's transaction also stores report_exports and the full PDF blob."""
    export_id = _positive(export_id, '报告')
    if _request_digest(archive) != archive.get('request_digest'):
        raise ValueError('归档资料已变化，请重新核对报告。')
    if _payload_digest(archive) != archive.get('payload_digest'):
        raise ValueError('归档文件已变化，请重新核对报告。')
    existing = find_existing_archive(request_key, archive['request_digest'], connection)
    if existing is not None:
        if existing != export_id:
            raise ValueError('本次报告已经保存，请到报告历史下载。')
        return export_id
    manifest = archive['manifest']
    _check_source(archive['source_version'])
    chosen = _versions(manifest['handling_versions']) or {}
    frozen, evidence = _selected_evidence(connection, SOURCE_TYPES[manifest['qc_method']], manifest['source_ids'], chosen)
    if evidence != manifest['archive_evidence_fingerprint']:
        raise ValueError('处理版本或附件资料已变化，请重新核对报告；本次尚未保存。')
    for item in frozen:
        for record in item['attachments']:
            _read_original(record)
    pdf = connection.execute('SELECT pdf_bytes FROM report_export_files WHERE export_id=?', (export_id,)).fetchone()
    if pdf is None or bytes(pdf['pdf_bytes']) != archive['pdf_bytes']:
        raise ValueError('完整PDF与归档不一致，本次尚未保存。')
    data = bytes(archive['zip_bytes'])
    connection.execute('''INSERT INTO qc_report_archive_files
        (export_id,request_key,request_digest,archive_name,archive_bytes,sha256,size_bytes,manifest_json)
        VALUES(?,?,?,?,?,?,?,?)''', (export_id, str(request_key).strip(), archive['request_digest'],
        archive['file_name'], data, sha256(data).hexdigest(), len(data), encode(manifest)))
    return export_id


def get_report_archive_metadata(export_id):
    identifier = _positive(export_id, '报告')
    with read_snapshot() as connection:
        row = connection.execute('''SELECT export_id,request_key,request_digest,archive_name,sha256,size_bytes,
            manifest_json,created_at FROM qc_report_archive_files WHERE export_id=?''', (identifier,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result['manifest'] = json.loads(result.pop('manifest_json'))
    result['handling_versions'] = result['manifest'].get('handling_versions', {})
    return result


def read_report_archive(export_id):
    identifier = _positive(export_id, '报告')
    with read_snapshot() as connection:
        row = connection.execute('SELECT archive_bytes,sha256,size_bytes FROM qc_report_archive_files WHERE export_id=?',
                                 (identifier,)).fetchone()
    if row is None:
        raise ValueError('这份报告没有已保存的完整归档，请按原范围另存新报告。')
    data = bytes(row['archive_bytes'])
    if len(data) != row['size_bytes'] or sha256(data).hexdigest() != row['sha256']:
        raise ValueError('完整归档校验不一致，请从完整备份恢复；未生成替代内容。')
    return data
