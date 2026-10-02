"""Reviewed product text, immutable releases and explicitly confirmed coverage.

No directory operation selects a QC method or creates physical lots/targets.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from functools import wraps
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

import pandas as pd
from database import atomic_write, get_connection, read_snapshot
from services.master_data_service import create_manufacturer, get_manufacturer_categories, require_manufacturer_category
from services.search_service import filter_frame

PRODUCT_FIELDS = ('product_code', 'product_name', 'concentration', 'concentration_code')
PRODUCT_LABELS = dict(zip(PRODUCT_FIELDS, ('产品编号', '产品名称', '浓度', '浓度编号')))
BUILTIN_BONDSON_SOURCE_CODE = 'bondson-iqc'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _safe_write(function):
    @wraps(function)
    def run(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except sqlite3.Error as exc:
            raise ValueError('资料保存未完成，原资料保持不变。请保留填写内容，核对后重新保存。') from exc
    return run


def load_bondson_directory_package():
    path = Path(__file__).resolve().parents[1] / 'data' / 'catalogs' / 'bondson_2026_09_28.json'
    source = json.loads(path.read_text(encoding='utf-8'))
    def rows(values):
        return [{**dict(zip(PRODUCT_FIELDS, row['values'])), 'source_rows': row['sourceRows'],
                 **({'excluded_reason': row['reason']} if row.get('reason') else {})} for row in values]
    return dict(source_code='bondson-iqc', version_label='2026-09-28-reviewed-1054',
        publisher='邦德盛（用户提供目录）', source_file=source['sourceFile'], source_sha256=source['sourceSha256'],
        obtained_at='2026-09-28', verified_by='用户范围确认与原表逐字段核对',
        notes='保留1054项，252项按已确认范围及未确认项排除；不包含检验项目覆盖映射。',
        is_test=False, records=rows(source['records']), excluded=rows(source['excluded']))


def _available_bondson_directory(connection):
    release = connection.execute('''SELECT id,source_code,version_label,manufacturer_id
        FROM md_product_directory_releases WHERE source_code=? AND is_test=0
        ORDER BY id DESC LIMIT 1''', (BUILTIN_BONDSON_SOURCE_CODE,)).fetchone()
    if release is None:
        return None
    counts = connection.execute('''SELECT SUM(excluded_reason='') AS products,
        SUM(excluded_reason<>'') AS excluded FROM md_product_directory_items WHERE release_id=?''',
        (release['id'],)).fetchone()
    return dict(release_id=release['id'], source_code=release['source_code'],
                version_label=release['version_label'], manufacturer_id=release['manufacturer_id'],
                product_count=int(counts['products'] or 0), excluded_count=int(counts['excluded'] or 0),
                reused=True)


@_safe_write
def ensure_builtin_bondson_directory():
    """Install reviewed reference products once, without replacing any formal release."""
    with read_snapshot() as connection:
        available = _available_bondson_directory(connection)
    if available is not None:
        return available

    package = load_bondson_directory_package()
    with atomic_write() as connection:
        available = _available_bondson_directory(connection)
        if available is not None:
            return available
        manufacturers = connection.execute('''SELECT id,is_disabled FROM md_manufacturers
            WHERE TRIM(display_name)=? ORDER BY id''', ('邦德盛',)).fetchall()
        if len(manufacturers) > 1:
            raise ValueError('邦德盛厂家资料有重名，请在基础资料中核对后重新打开软件；原资料未更改。')
        if manufacturers and manufacturers[0]['is_disabled']:
            raise ValueError('邦德盛厂家已停用，目录未添加。请在基础资料中核对厂家状态后重试；原资料未更改。')
        manufacturer_id = manufacturers[0]['id'] if manufacturers else create_manufacturer(
            display_name='邦德盛', categories=['qc_material'], notes='随软件提供的已确认邦德盛产品目录。')
        connection.execute('''INSERT OR IGNORE INTO md_manufacturer_categories
            (manufacturer_id,category,provenance) VALUES (?,'qc_material','builtin_directory')''',
            (manufacturer_id,))
        preview = preview_product_directory(package, manufacturer_id)
        result = publish_product_directory(package, manufacturer_id,
            expected_preview_hash=preview['preview_hash'], published_by='系统预置（按已确认目录）')
        return dict(result, source_code=package['source_code'], version_label=package['version_label'],
                    manufacturer_id=manufacturer_id)


def list_catalog_product_choices(query=''):
    """Current enabled directory products, with reviewed text and stable product IDs."""
    with read_snapshot() as connection:
        frame = pd.read_sql_query('''SELECT i.product_code,i.product_name,i.concentration,
            i.concentration_code,k.product_id,m.display_name AS manufacturer_name
            FROM md_product_directory_keys k
            JOIN md_product_directory_items i ON i.release_id=k.current_release_id
                AND i.product_code=k.product_code AND i.product_id=k.product_id
            JOIN md_product_directory_releases r ON r.id=k.current_release_id
                AND r.source_code=k.source_code
            JOIN md_qc_materials p ON p.id=k.product_id
            JOIN md_manufacturers m ON m.id=p.manufacturer_id
            WHERE r.is_test=0 AND i.excluded_reason='' AND p.is_disabled=0 AND m.is_disabled=0
                AND EXISTS(SELECT 1 FROM md_manufacturer_categories c
                    WHERE c.manufacturer_id=m.id AND c.category='qc_material')
                AND r.id=(SELECT latest.id FROM md_product_directory_releases latest
                    WHERE latest.source_code=r.source_code AND latest.is_test=0
                    ORDER BY latest.id DESC LIMIT 1)
            ORDER BY m.display_name,i.product_code''', connection)
    return filter_frame(frame, query, [*PRODUCT_FIELDS, 'manufacturer_name'])


def list_managed_material_products(query='', include_disabled=False):
    """Management list for current formal directory and locally entered products.

    Directory fields retain their published text. Local names and product numbers
    are read from their editable product record; no physical lot is inferred.
    Retired/excluded directory products remain outside the daily management list,
    while a current product disabled by the user can be shown for restoration.
    """
    with read_snapshot() as connection:
        frame = pd.read_sql_query('''SELECT
            COALESCE(i.product_code,p.catalog_no,'') AS product_code,
            COALESCE(i.product_name,p.generic_name) AS product_name,
            COALESCE(i.concentration,'') AS concentration,
            COALESCE(i.concentration_code,'') AS concentration_code,
            p.id AS product_id,COALESCE(m.display_name,'') AS manufacturer_name,
            p.is_disabled
            FROM md_qc_materials p
            LEFT JOIN md_manufacturers m ON m.id=p.manufacturer_id
            LEFT JOIN md_product_directory_keys k ON k.product_id=p.id
            LEFT JOIN md_product_directory_releases r ON r.id=k.current_release_id
                AND r.source_code=k.source_code AND r.is_test=0
                AND r.id=(SELECT latest.id FROM md_product_directory_releases latest
                    WHERE latest.source_code=k.source_code AND latest.is_test=0
                    ORDER BY latest.id DESC LIMIT 1)
            LEFT JOIN md_product_directory_items i ON i.release_id=r.id
                AND i.product_code=k.product_code AND i.product_id=p.id
                AND i.excluded_reason=''
            WHERE (k.product_id IS NULL OR i.product_id IS NOT NULL)''' +
            ('' if include_disabled else ' AND p.is_disabled=0') + '''
            ORDER BY p.is_disabled,m.display_name,product_code,product_name,p.id''', connection)
    return filter_frame(frame, query, [*PRODUCT_FIELDS, 'manufacturer_name'])


def _normalize_package(package):
    if not isinstance(package, dict):
        raise ValueError('目录资料格式无效。')
    result = deepcopy(package)
    for field in ('source_code', 'version_label', 'publisher', 'source_file', 'source_sha256', 'obtained_at', 'verified_by'):
        if not isinstance(result.get(field), str) or not result[field].strip():
            raise ValueError('目录资料不完整，请核对来源、版本、取得日期和核查人，并重新选择完整的目录资料包。')
        result[field] = result[field].strip()
    if not re.fullmatch('[a-zA-Z0-9_.-]{1,100}', result['source_code']):
        raise ValueError('无法识别这份资料的目录来源，请重新选择完整的目录资料包。')
    if not re.fullmatch('[0-9a-f]{64}', result['source_sha256']):
        raise ValueError('无法核对目录文件是否完整，请重新取得完整的目录资料包。')
    try:
        date.fromisoformat(result['obtained_at'])
    except ValueError as exc:
        raise ValueError('取得日期应采用年-月-日。') from exc
    if not isinstance(result.get('is_test', False), bool):
        raise ValueError('目录资料用途无效。')
    result['is_test'] = result.get('is_test', False)
    result['notes'] = str(result.get('notes') or '').strip()
    for collection in ('records', 'excluded'):
        values = result.get(collection, [])
        if not isinstance(values, list) or len(values) > 100000:
            raise ValueError('目录记录清单无效或数量过多。')
        cleaned = []
        for index, row in enumerate(values, 2):
            if not isinstance(row, dict):
                raise ValueError(f'第{index}行格式无效。')
            clean = {}
            for field in PRODUCT_FIELDS:
                value = row.get(field, '')
                if value is None:
                    value = ''
                if not isinstance(value, str):
                    raise ValueError(f'第{index}行的{PRODUCT_LABELS[field]}须保留原文。')
                clean[field] = value.strip()
            source_rows = row.get('source_rows', [index])
            if not isinstance(source_rows, list) or not source_rows or any(type(v) is not int or v < 1 for v in source_rows):
                raise ValueError(f'第{index}行的来源行号无效。')
            clean['source_rows'] = sorted(set(source_rows))
            clean['excluded_reason'] = str(row.get('excluded_reason') or '').strip() if collection == 'excluded' else ''
            cleaned.append(clean)
        result[collection] = cleaned
    if not result['records']:
        raise ValueError('目录中没有可发布的产品。')
    return result


def _preview(connection, package, manufacturer_id):
    require_manufacturer_category(connection, manufacturer_id, 'qc_material')
    payload_sha = _hash(package)
    existing = connection.execute('SELECT * FROM md_product_directory_releases WHERE source_code=? AND version_label=?',
                                 (package['source_code'], package['version_label'])).fetchone()
    problems, rows, seen = [], [], set()
    if existing and (existing['payload_sha256'] != payload_sha or existing['manufacturer_id'] != manufacturer_id):
        problems.append(dict(row='', product_code='', field='来源版本', message='相同来源版本已经发布不同内容或厂家，请使用真实新版本。'))
    current = {row['product_code']: dict(row) for row in connection.execute('''SELECT k.*,i.product_name,i.concentration,i.concentration_code
        FROM md_product_directory_keys k JOIN md_product_directory_items i ON i.release_id=k.current_release_id AND i.product_code=k.product_code
        WHERE k.source_code=?''', (package['source_code'],))}
    for excluded, collection in ((False, package['records']), (True, package['excluded'])):
        for row in collection:
            code = row['product_code']
            def problem(field, message):
                problems.append(dict(row=','.join(map(str, row['source_rows'])), product_code=code, field=field, message=message))
            if not code:
                problem('产品编号', '请填写产品编号。')
            if code in seen:
                problem('产品编号', '同一来源中产品编号重复或同时列入保留与排除。')
            seen.add(code)
            if not row['product_name']:
                problem('产品名称', '请填写产品名称。')
            if not excluded and all(row[field] in ('', '/') for field in ('concentration', 'concentration_code')):
                problem('浓度 / 浓度编号', '至少一项须有明确内容；两项均缺少有效内容的产品应移入排除清单并保留原因。')
            if excluded and not row['excluded_reason']:
                problem('排除原因', '请保留原排除原因。')
            old = current.get(code)
            if old:
                owned = connection.execute('SELECT manufacturer_id FROM md_qc_materials WHERE id=?', (old['product_id'],)).fetchone()
                if owned['manufacturer_id'] != manufacturer_id:
                    problem('厂家', '此来源的产品已属于另一厂家，不能更换身份。')
            if not old and not excluded and not package['is_test']:
                collision = connection.execute('SELECT generic_name FROM md_qc_materials WHERE manufacturer_id=? AND LOWER(TRIM(catalog_no))=LOWER(TRIM(?))',
                                               (manufacturer_id, code)).fetchone()
                if collision:
                    problem('产品编号', '本机已有同厂家同编号产品，需要先人工核对来源，目录不会覆盖本地产品。')
            action = '排除' if excluded else ('不变' if old and all(row[k] == old[k] for k in PRODUCT_FIELDS[1:]) else '修改' if old else '新增')
            rows.append({**row, 'action': action, 'product_id': old['product_id'] if old else None})
    active_codes = {row['product_code'] for row in package['records']}
    retired = sorted(set(current) - active_codes)
    token = _hash(dict(payload_sha=payload_sha, manufacturer_id=manufacturer_id, current=current,
                       existing=dict(existing) if existing else None, problems=problems))
    return dict(records=rows, issues=problems, retired_codes=retired, preview_hash=token,
                package_hash=payload_sha, reused=bool(existing and not problems),
                release_id=existing['id'] if existing and not problems else None,
                included_count=len(package['records']), excluded_count=len(package['excluded']), is_test=package['is_test'])


def preview_product_directory(package, manufacturer_id):
    package = _normalize_package(package)
    with read_snapshot() as connection:
        return _preview(connection, package, int(manufacturer_id))


@_safe_write
def publish_product_directory(package, manufacturer_id, *, expected_preview_hash, published_by):
    package = _normalize_package(package)
    actor = str(published_by or '').strip()
    if not actor:
        raise ValueError('请填写本次目录核对人。')
    with atomic_write() as connection:
        preview = _preview(connection, package, int(manufacturer_id))
        if preview['issues']:
            first = preview['issues'][0]
            raise ValueError(f"目录未发布。{first['product_code']} {first['field']}：{first['message']}")
        if preview['reused']:
            return dict(release_id=preview['release_id'], reused=True, product_count=preview['included_count'], excluded_count=preview['excluded_count'])
        if not expected_preview_hash or expected_preview_hash != preview['preview_hash']:
            raise ValueError('目录预览已过期，请重新预览核对后发布。')
        source_id = connection.execute('''INSERT INTO md_sources(uid,source_code,source_name,source_kind,publisher,
            version_label,checksum,imported_at) VALUES (?,?,?,'vendor',?,?,?,CURRENT_TIMESTAMP)''',
            (str(uuid4()),package['source_code'] + ':' + package['version_label'], package['source_file'],
             package['publisher'],package['version_label'],package['source_sha256'])).lastrowid
        metadata = {key: value for key,value in package.items() if key not in ('records','excluded')}
        release_id = connection.execute('''INSERT INTO md_product_directory_releases
            (source_id,source_code,version_label,payload_sha256,source_sha256,manufacturer_id,metadata_json,published_by,is_test)
            VALUES (?,?,?,?,?,?,?,?,?)''', (source_id,package['source_code'],package['version_label'],preview['package_hash'],
            package['source_sha256'],manufacturer_id,_json(metadata),actor,int(package['is_test']))).lastrowid
        for row in preview['records']:
            product_id = specification_id = None
            if not row['excluded_reason'] and not package['is_test']:
                product_id = row['product_id']
                if product_id is None:
                    product_id = connection.execute('''INSERT INTO md_qc_materials(uid,origin_type,manufacturer_id,generic_name,catalog_no)
                        VALUES (?,'import',?,?,?)''', (str(uuid4()),manufacturer_id,row['product_name'],row['product_code'])).lastrowid
                else:
                    connection.execute('''UPDATE md_qc_materials SET generic_name=?,is_disabled=0,disabled_reason='',disabled_at=NULL,
                        updated_at=CURRENT_TIMESTAMP WHERE id=?''', (row['product_name'],product_id))
                # Directory text never creates or splits actual material levels/lots.
                name = row['concentration'] if row['concentration'] not in ('','/') else row['concentration_code']
                specification = connection.execute('SELECT id FROM md_qc_material_specs WHERE qc_material_id=? AND level_name=? AND level_code=?',
                                                  (product_id,name,row['concentration_code'])).fetchone()
                specification_id = specification[0] if specification else connection.execute('''INSERT INTO md_qc_material_specs
                    (uid,qc_material_id,level_name,level_code,catalog_no) VALUES (?,?,?,?,?)''',
                    (str(uuid4()),product_id,name,row['concentration_code'],row['product_code'])).lastrowid
                connection.execute('''INSERT INTO md_product_directory_keys(source_code,product_code,product_id,current_release_id) VALUES (?,?,?,?)
                    ON CONFLICT(source_code,product_code) DO UPDATE SET current_release_id=excluded.current_release_id''',
                    (package['source_code'],row['product_code'],product_id,release_id))
                connection.execute('''INSERT INTO md_source_records(uid,origin_type,entity_type,entity_id,source_id,external_record_id,raw_display_name,record_hash)
                    VALUES (?,'import','qc_material',?,?,?,?,?)''',
                    (str(uuid4()),product_id,source_id,row['product_code'],row['product_name'],_hash({k:row[k] for k in PRODUCT_FIELDS})))
            connection.execute('''INSERT INTO md_product_directory_items(release_id,product_code,product_name,concentration,concentration_code,
                source_rows_json,product_id,specification_id,excluded_reason) VALUES (?,?,?,?,?,?,?,?,?)''',
                (release_id,*[row[k] for k in PRODUCT_FIELDS],_json(row['source_rows']),product_id,specification_id,row['excluded_reason']))
        if not package['is_test']:
            for code in preview['retired_codes']:
                connection.execute('''UPDATE md_qc_materials SET is_disabled=1,disabled_reason='最新来源目录不再收录',disabled_at=CURRENT_TIMESTAMP
                    WHERE id=(SELECT product_id FROM md_product_directory_keys WHERE source_code=? AND product_code=?)''',
                    (package['source_code'],code))
        return dict(release_id=release_id,reused=False,product_count=preview['included_count'],excluded_count=preview['excluded_count'])


def list_product_directory_releases():
    with get_connection() as connection:
        return pd.read_sql_query('''SELECT r.*,s.source_name,s.publisher FROM md_product_directory_releases r
            JOIN md_sources s ON s.id=r.source_id ORDER BY r.id DESC''',connection)


def list_directory_products(release_id, *, include_excluded=False, query=''):
    with get_connection() as connection:
        frame = pd.read_sql_query('SELECT * FROM md_product_directory_items WHERE release_id=?' +
            ('' if include_excluded else " AND excluded_reason='' ") + ' ORDER BY product_code',connection,params=(int(release_id),))
    return filter_frame(frame, query, list(PRODUCT_FIELDS))


def _relationships(connection, product_id):
    product = connection.execute('SELECT * FROM md_qc_materials WHERE id=?',(int(product_id),)).fetchone()
    if product is None:
        raise ValueError('未找到所选质控品。')
    coverage = [dict(row) for row in connection.execute('''SELECT c.*,t.uid AS test_item_uid,t.standard_code,t.chinese_name AS test_item_name,
        t.is_disabled AS test_item_disabled,m.uid AS method_uid,m.method_name FROM md_product_coverage c JOIN md_test_items t ON t.id=c.test_item_id
        LEFT JOIN md_methods m ON m.id=c.method_id
        WHERE c.product_id=? ORDER BY c.test_item_id''',(product_id,))]
    usage = [dict(row) for row in connection.execute('''SELECT p.id AS template_id,p.template_name,p.status,p.is_disabled AS template_disabled,
        p.lab_instrument_id,l.display_name AS instrument_name,t.id AS template_item_id,t.test_item_id,i.chinese_name AS test_item_name,
        t.qc_method,t.input_value_type,t.is_disabled,lc.id AS lot_config_id,li.id AS lot_config_item_id
        FROM qc_project_templates p JOIN qc_project_template_items t ON t.template_id=p.id
        JOIN md_test_items i ON i.id=t.test_item_id LEFT JOIN lab_instruments l ON l.id=p.lab_instrument_id
        LEFT JOIN qc_lot_configs lc ON lc.template_id=p.id LEFT JOIN qc_lot_config_items li ON li.lot_config_id=lc.id AND li.source_template_item_id=t.id
        WHERE p.qc_material_id=? ORDER BY p.id,t.id,lc.id''',(product_id,))]
    return dict(coverage=coverage,usage=usage,edit_version=_hash(dict(product=dict(product),coverage=coverage)))


def get_product_relationships(product_id):
    with get_connection() as connection:
        return _relationships(connection,int(product_id))


@_safe_write
def save_product_coverage(product_id, entries, *, expected_version, confirmed_by, evidence,
                          source_kind='local', source_version=''):
    actor, reason = str(confirmed_by or '').strip(), str(evidence or '').strip()
    if not actor or not reason:
        raise ValueError('请填写适用检验项目的确认人和依据。')
    if not isinstance(entries,list) or source_kind not in ('local','directory'):
        raise ValueError('项目清单无效，请重新选择适用检验项目后保存。')
    with atomic_write() as connection:
        before = _relationships(connection,int(product_id))
        if not expected_version or before['edit_version'] != expected_version:
            raise ValueError('此质控品的适用项目已修改，当前选择仍可保留，请重新核对后保存。')
        product = connection.execute('SELECT is_disabled FROM md_qc_materials WHERE id=?',(product_id,)).fetchone()
        if product['is_disabled']:
            raise ValueError('请先恢复此质控品，再维护适用检验项目。')
        if source_kind == 'directory':
            directory = _export_context(connection,product_id)['product']
            if not directory.get('source_code') or directory['version_label'] != source_version:
                raise ValueError('适用检验项目的厂家依据须与此质控品的目录版本一致。')
        normalized, seen = [], set()
        for entry in entries:
            entry = {'test_item_id':entry} if type(entry) is int else dict(entry)
            item_id = int(entry['test_item_id'])
            if item_id in seen:
                raise ValueError('同一检验项目不能重复选择。')
            seen.add(item_id)
            if not connection.execute('SELECT 1 FROM md_test_items WHERE id=? AND is_disabled=0',(item_id,)).fetchone():
                raise ValueError('所选检验项目不存在或已停用，请重新核对。')
            method_id = entry.get('method_id')
            if method_id is not None and not connection.execute('SELECT 1 FROM md_methods WHERE id=? AND is_disabled=0',(method_id,)).fetchone():
                raise ValueError('所选方法学不存在或已停用，请重新选择。')
            item_kind = entry.get('source_kind',source_kind)
            item_version = str(entry.get('source_version',source_version))
            item_evidence = str(entry.get('evidence',reason)).strip()
            item_actor = str(entry.get('confirmed_by',actor)).strip()
            if item_kind not in ('local','directory') or not item_evidence or not item_actor:
                raise ValueError('请为适用检验项目补齐资料来源、确认人和依据。')
            if item_kind == 'directory':
                directory = _export_context(connection,product_id)['product']
                if not directory.get('source_code') or item_version != directory['version_label']:
                    raise ValueError('适用检验项目的厂家依据须与此质控品的目录版本一致。')
            normalized.append(dict(test_item_id=item_id,method_id=method_id,source_kind=item_kind,
                source_version=item_version,evidence=item_evidence,confirmed_by=item_actor))
        old = {row['test_item_id']:row for row in before['coverage']}
        if set(seen) == {key for key,row in old.items() if not row['is_disabled']} and all(
                all(old[row['test_item_id']].get(key) == value for key,value in row.items()) for row in normalized):
            return before
        connection.execute('INSERT INTO md_product_coverage_history(product_id,snapshot_json,changed_by,evidence) VALUES (?,?,?,?)',
                           (product_id,_json(before['coverage']),actor,reason))
        connection.execute('UPDATE md_product_coverage SET is_disabled=1 WHERE product_id=?',(product_id,))
        for row in normalized:
            connection.execute('''INSERT INTO md_product_coverage(product_id,test_item_id,method_id,source_kind,source_version,evidence,confirmed_by)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(product_id,test_item_id) DO UPDATE SET method_id=excluded.method_id,
                source_kind=excluded.source_kind,source_version=excluded.source_version,evidence=excluded.evidence,
                confirmed_by=excluded.confirmed_by,confirmed_at=CURRENT_TIMESTAMP,is_disabled=0''',
                (product_id,*[row[k] for k in ('test_item_id','method_id','source_kind','source_version','evidence','confirmed_by')]))
        return _relationships(connection,product_id)


def _export_context(connection,material_id):
    raw = connection.execute('SELECT * FROM md_qc_materials WHERE id=?',(material_id,)).fetchone()
    if raw is None:
        raise ValueError('所选质控品不存在。')
    row = connection.execute('''SELECT i.*,r.source_code,r.version_label,r.source_sha256 FROM md_product_directory_keys k
        JOIN md_product_directory_items i ON i.release_id=k.current_release_id AND i.product_code=k.product_code
        JOIN md_product_directory_releases r ON r.id=i.release_id WHERE k.product_id=?''',(material_id,)).fetchone()
    product = {key:row[key] for key in (*PRODUCT_FIELDS,'source_code','version_label','source_sha256')} if row else dict(
        product_code=raw['catalog_no'],product_name=raw['generic_name'],concentration='',concentration_code='',
        source_code='',version_label='',source_sha256='',uid=raw['uid'])
    product['external_key'] = product['source_code'] + ':' + product['product_code'] if row else raw['uid']
    manufacturer = connection.execute('SELECT uid,display_name FROM md_manufacturers WHERE id=?',(raw['manufacturer_id'],)).fetchone()
    return dict(product=product,manufacturer={**dict(manufacturer),'categories':get_manufacturer_categories(raw['manufacturer_id'],connection)} if manufacturer else {},
                coverage=_relationships(connection,material_id)['coverage'])


def export_material_catalog_context(material_id):
    with get_connection() as connection:
        return _export_context(connection,int(material_id))


def validate_material_catalog_context(connection,material_id,payload):
    if not isinstance(payload,dict) or not isinstance(payload.get('product'),dict):
        raise ValueError('导入文件缺少质控品目录资料，请重新导出完整的项目文件后再导入。')
    expected = _export_context(connection,int(material_id))
    for field,value in expected['product'].items():
        if payload['product'].get(field) != value:
            raise ValueError('配置中的质控品编号、来源版本或原始资料与当前选择不一致，请重新核对；不会自动替换产品。')
    incoming_manufacturer = payload.get('manufacturer')
    target_manufacturer = expected['manufacturer']
    if not isinstance(incoming_manufacturer,dict) or incoming_manufacturer.get('display_name') != target_manufacturer.get('display_name') or sorted(incoming_manufacturer.get('categories',[])) != sorted(target_manufacturer.get('categories',[])):
        raise ValueError('配置中的厂家或业务类别与当前产品不一致，请核对厂家资料。')
    if not expected['product'].get('source_code') and incoming_manufacturer.get('uid') != target_manufacturer.get('uid'):
        raise ValueError('导入文件中的厂家与已登记厂家不一致，请核对厂家资料后重新预览。')
    return expected


@_safe_write
def apply_material_catalog_context(material_id,payload,test_item_id_map,*,expected_version,method_id_map=None):
    with atomic_write() as connection:
        validate_material_catalog_context(connection,material_id,payload)
        before = _relationships(connection,material_id)
        if before['edit_version'] != expected_version:
            raise ValueError('此质控品的适用项目已修改，请重新预览导入文件。')
        records = payload.get('coverage',[])
        if not isinstance(records,list) or len({row.get('test_item_id') for row in records}) != len(records):
            raise ValueError('适用检验项目清单无效或包含重复项目，请核对后重新预览。')
        for row in records:
            if int(row['test_item_id']) not in test_item_id_map:
                raise ValueError('导入清单中的适用检验项目尚未对应到已登记项目，请核对项目编号和名称。')
            target = test_item_id_map[int(row['test_item_id'])]
            match = connection.execute('SELECT * FROM md_test_items WHERE id=? AND is_disabled=0',(target,)).fetchone()
            if not match or not (match['uid']==row.get('test_item_uid') or (row.get('standard_code') and match['standard_code']==row['standard_code'])):
                raise ValueError('此质控品的适用检验项目与已登记项目不一致，请核对项目编号和名称后重新预览。')
            if row.get('method_id') is not None:
                target_method = (method_id_map or {}).get(int(row['method_id']))
                method = connection.execute('SELECT * FROM md_methods WHERE id=? AND is_disabled=0',(target_method,)).fetchone()
                if not method or not (method['uid']==row.get('method_uid') or method['method_name']==row.get('method_name')):
                    raise ValueError('适用检验项目的方法学与已登记资料不一致，请在基础资料中核对后重新预览。')
        old = {row['test_item_id']:row for row in before['coverage']}
        fields = ('source_kind','source_version','evidence','confirmed_by','confirmed_at','is_disabled')
        if all(test_item_id_map[int(row['test_item_id'])] in old and
               all(old[test_item_id_map[int(row['test_item_id'])]].get(field)==row.get(field) for field in fields) and
               old[test_item_id_map[int(row['test_item_id'])]].get('method_id') == ((method_id_map or {}).get(int(row['method_id'])) if row.get('method_id') is not None else None)
               for row in records):
            return before
        # Preserve each explicitly provided source and evidence; append import history.
        connection.execute('INSERT INTO md_product_coverage_history(product_id,snapshot_json,changed_by,evidence) VALUES (?,?,?,?)',
                           (material_id,_json(before['coverage']),'配置导入','按配置文件中明确检验项目身份带入覆盖资料'))
        for row in records:
            if row.get('source_kind') not in ('local','directory') or not str(row.get('confirmed_by') or '').strip() or not str(row.get('evidence') or '').strip():
                raise ValueError('请为适用检验项目补齐有效的资料来源、确认人和依据。')
            if row['source_kind']=='directory' and row.get('source_version') != payload['product'].get('version_label'):
                raise ValueError('适用检验项目所用厂家资料的版本与此质控品目录不一致，请核对后重新预览。')
            connection.execute('''INSERT INTO md_product_coverage(product_id,test_item_id,source_kind,source_version,evidence,confirmed_by,confirmed_at,is_disabled,method_id)
                VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(product_id,test_item_id) DO UPDATE SET source_kind=excluded.source_kind,
                source_version=excluded.source_version,evidence=excluded.evidence,confirmed_by=excluded.confirmed_by,
                confirmed_at=excluded.confirmed_at,is_disabled=excluded.is_disabled,method_id=excluded.method_id''',
                (material_id,test_item_id_map[int(row['test_item_id'])],row['source_kind'],str(row.get('source_version') or ''),
                 row['evidence'],row['confirmed_by'],row['confirmed_at'],int(bool(row.get('is_disabled'))),
                 (method_id_map or {}).get(int(row['method_id'])) if row.get('method_id') is not None else None))
        return _relationships(connection,material_id)
