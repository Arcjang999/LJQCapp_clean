"""Structured standard applicability. This module never evaluates QC results."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

INDEX = Path(__file__).resolve().parents[1] / 'resources' / 'quality_standards_2026.json'
CONTEXT_OPTIONS = {
    'technique': {'': '请选择', 'clinical_chemistry': '临床化学定量方法',
                  'immunoassay': '免疫测定', 'realtime_pcr': '实时荧光 PCR',
                  'sequencing': '测序', 'isothermal': '恒温扩增', 'hybridization': '核酸杂交',
                  'hematology': '血细胞分析', 'coagulation': '凝血初筛', 'other': '其他方法'},
    'result_kind': {'': '请选择', 'quantitative': '定量检测', 'qualitative': '定性检测'},
    'result_scale': {'': '请选择', 'concentration': '浓度、活性或计数等原始定量值',
                     'ct': 'Ct 值', 'log': 'log 值', 's_co': 'S/CO 或 COI 信号比值',
                     'qualitative': '阳性 / 阴性', 'other_numeric': '其他数值尺度'},
    'purpose': {'': '请选择', 'clinical': '临床检验', 'research': '研究用途',
                'blood_screening': '献血筛查'},
}
CONTEXT_LABELS = {'technique': '检测技术', 'result_kind': '检测结果性质',
                  'result_scale': '结果尺度', 'purpose': '检测用途', 'specimen': '标本或基质'}


def catalog_index():
    return json.loads(INDEX.read_text(encoding='utf-8'))


def source_status(source, on_date=None):
    today = (on_date or date.today()).isoformat()
    if source.get('withdrawn_on') and today >= source['withdrawn_on']:
        return 'withdrawn'
    if source.get('effective_date', '9999') > today:
        return 'future'
    if source.get('verification') != 'verified':
        return 'unverified'
    if source.get('kind') == 'reference_interval':
        return 'reference_interval'
    return 'current'


def infer_technique(item):
    text = ' '.join(str(item.get(k) or '') for k in ('method_name', 'method_code', 'method_principle')).casefold()
    for key, terms in (
        ('sequencing', ('测序', 'sequencing', 'ngs')),
        ('isothermal', ('恒温', '等温', 'lamp', 'tma', '转录介导')),
        ('hybridization', ('杂交', 'hybridization')),
        ('realtime_pcr', ('实时荧光', '荧光 pcr', 'fluorescent_pcr', 'real-time', 'qpcr')),
        ('immunoassay', ('免疫', '化学发光', '电化学发光', 'elisa', 'eclia', 'clia')),
        ('hematology', ('血细胞', '血液分析', 'hematology')),
        ('coagulation', ('凝血', 'coagulation')),
        ('clinical_chemistry', ('比色', '酶法', '生化', '电极', '血气', '血糖仪')),
    ):
        if any(term in text for term in terms):
            return key
    return ''


def default_context(item):
    mode = item.get('input_value_type')
    unit = str(item.get('unit_symbol') or '').upper().replace(' ', '')
    scale = mode if mode in ('ct', 'log') else ('s_co' if unit in ('S/CO', 'COI', 'S/CO值') else '')
    return dict(technique=infer_technique(item), specimen=item.get('specimen_type') or '',
                result_kind='qualitative' if scale == 's_co' else '', result_scale=scale, purpose='')


def context_errors(item, context):
    errors = []
    for key, options in CONTEXT_OPTIONS.items():
        if context.get(key) not in options or not context.get(key):
            errors.append('请补充' + CONTEXT_LABELS[key] + '。')
    if not str(context.get('specimen') or '').strip():
        errors.append('请补充标本或基质。')
    elif len(str(context['specimen'])) > 200:
        errors.append('标本或基质不能超过 200 字。')
    inferred = infer_technique(item)
    if inferred and context.get('technique') and context['technique'] != inferred:
        errors.append('检测技术与已配置的方法学不一致，请先核对方法学。')
    mode, scale = item.get('input_value_type'), context.get('result_scale')
    if mode in ('ct', 'log') and scale != mode:
        errors.append('结果尺度必须与项目的 Ct / log 输入值类型一致。')
    if mode == 'raw' and scale in ('ct', 'log'):
        errors.append('请先将项目输入值类型设置为对应的 Ct 或 log 值。')
    unit = str(item.get('unit_symbol') or '').upper().replace(' ', '')
    if unit in ('S/CO', 'COI') and scale != 's_co':
        errors.append('当前单位为信号比值，不能按浓度尺度采用质量要求。')
    if scale == 's_co' and context.get('result_kind') != 'qualitative':
        errors.append('S/CO / COI 属于定性测定的信号比值，请核对结果性质。')
    if scale == 'qualitative' and context.get('result_kind') != 'qualitative':
        errors.append('阳性 / 阴性结果须选择定性检测。')
    return errors


def numeric_candidates(item):
    from services.quality_target_service import list_catalog
    from services.quality_identity_service import numeric_identity_matches
    aliases = catalog_index()['aliases']
    return [r for r in list_catalog() if r['origin'] == 'builtin' and
            numeric_identity_matches(item, r, aliases.get(r['id'], []))]


def assess(item, context=None, *, on_date=None):
    """Return applicable / not_applicable / pending with reproducible reasons."""
    from services.quality_target_service import normalize_unit
    from services.quality_identity_service import specimen_issue, process_identity_matches
    context = context or {}
    index = catalog_index()
    sources = {s['id']: s for s in index['sources']}
    missing = context_errors(item, context)
    rows = []
    numeric = numeric_candidates(item)
    for spec in numeric:
        source = sources['wst403-2024' if spec['id'].startswith('wst403') else 'wst406-2024']
        reasons = []
        if context.get('purpose') and context['purpose'] != 'clinical':
            reasons.append('当前用途不属于已核查的医疗机构临床检验范围')
        if context.get('result_scale') and context['result_scale'] != 'concentration':
            reasons.append('当前结果尺度不是该条目采用的原始定量尺度')
        if context.get('result_kind') and context['result_kind'] != 'quantitative':
            reasons.append('该条目不用于定性结果')
        allowed = ('hematology',) if spec['id'].startswith('wst406') and spec['id'].split('-')[-1] not in ('pt', 'aptt', 'fib', 'tt') else (
            ('coagulation',) if spec['id'].startswith('wst406') else ('clinical_chemistry', 'immunoassay'))
        technique = context.get('technique')
        if technique and technique not in (*allowed, 'other'):
            reasons.append('检测技术不属于本条目的适用测定范围')
        status = 'not_applicable' if reasons else ('pending' if missing or technique == 'other' else 'applicable')
        specimen_reason = specimen_issue(item, context, spec) if not reasons else ''
        if specimen_reason:
            status = 'pending'
            reasons.append(specimen_reason)
        if status != 'not_applicable' and spec['id'] == 'wst403-2024-082' and '血糖仪' not in str(item.get('method_name', '')):
            status = 'pending'
            reasons.append('请确认本项目使用便携式血糖仪，并核对方法学资料。')
        # Unit incompatibility is unresolved, not a way to discard an otherwise
        # applicable analyte standard and enter a looser local limit.
        if status != 'not_applicable' and spec.get('measurement_unit') and normalize_unit(spec['measurement_unit']) != normalize_unit(item.get('unit_symbol')):
            # E.g. HbA1c has separately published NGSP and IFCC entries.
            alternative = any(other['id'] != spec['id'] and other.get('measurement_unit') and
                normalize_unit(other['measurement_unit']) == normalize_unit(item.get('unit_symbol')) for other in numeric)
            status = 'not_applicable' if alternative else 'pending'
            reasons.append(f"条目要求单位 {spec['measurement_unit']}，请核对检验项目单位和适用依据")
        if source_status(source, on_date) != 'current':
            status = 'pending'
            reasons.append('标准未生效、已被替代或全文尚待核查，不能确认采用')
        rows.append(dict(id=spec['id'], kind='numeric', spec=spec, source=source,
                         status=status, reason='；'.join(reasons) or ('待补充适用条件' if status == 'pending' else '检测对象、方法和结果尺度符合条目范围'),
                         clause=spec['source_clause'], pages=str(spec.get('source_page', ''))))
    for rule in index['requirements']:
        if not process_identity_matches(item, rule):
            continue
        reasons = []
        for key, values in [('technique', rule['techniques']), ('result_kind', rule['result_kinds']),
                            ('result_scale', rule['scales']), ('purpose', rule['purposes'])]:
            if context.get(key) and context[key] not in values:
                reasons.append(CONTEXT_LABELS[key] + '不属于该条款范围')
        # Keep clearly unrelated process standards out of the everyday list.
        if reasons:
            continue
        source = sources[rule['source_id']]
        state = source_status(source, on_date)
        # Version succession affects new confirmations only. Skip an inactive
        # edition only when its verified counterpart covers the current date;
        # otherwise leave a blocking pending item rather than lose the source.
        counterpart_id = source.get('replaces') if state == 'future' else source.get('replaced_by')
        if state in ('future', 'withdrawn') and source_status(sources.get(counterpart_id, {}), on_date) == 'current':
            continue
        terms = rule.get('method_terms', {}).get(context.get('technique'), [])
        method_text = ' '.join(str(item.get(key) or '') for key in ('method_name', 'method_principle', 'method_code')).casefold()
        method_pending = terms and not any(term.casefold() in method_text for term in terms)
        scale_pending = bool((rule.get('required_result_scale') and context.get('result_scale') != rule['required_result_scale'])
            or (rule.get('required_input_value_type') and item.get('input_value_type') != rule['required_input_value_type']))
        reason = (rule.get('method_review_message', '请核对是否为转录介导扩增（TMA），其他恒温扩增方法需另核对适用依据。')
                  if method_pending else ('待补充适用条件或核对现行依据' if missing or state != 'current'
                                         else '应采用本条款，并人工核对具体要求'))
        if scale_pending:
            reason = rule['scale_review_message']
        rows.append(dict(id=rule['id'], kind=rule['kind'], spec=rule, source=source,
                         status='pending' if missing or state != 'current' or method_pending or scale_pending else 'applicable',
                         reason=reason,
                         clause=rule['source_clause'], pages=rule['source_page']))
    return rows


def validate_search_record(record, *, confirmed_by, has_process):
    """A missing local catalog entry is never treated as proof of absence."""
    if not isinstance(record, dict):
        raise ValueError('目录尚未收录适用数值要求，请先登记官方标准查找与复核记录。')
    required = ('query', 'official_url', 'checked_on', 'conclusion', 'rationale')
    if not all(str(record.get(k) or '').strip() for k in required):
        raise ValueError('请完整填写标准查找词、官方来源、核查日期、结论及依据。')
    if any(len(str(record[k])) > 2000 for k in required):
        raise ValueError('标准查找记录单项不能超过 2000 字。')
    official_url(record['official_url'])
    try:
        checked = date.fromisoformat(record['checked_on'])
    except (TypeError, ValueError):
        raise ValueError('标准核查日期格式应为 YYYY-MM-DD。') from None
    if checked > date.today():
        raise ValueError('标准核查日期不能晚于今天。')
    if record['conclusion'] not in ('no_applicable', 'no_numeric', 'registered'):
        raise ValueError('标准目录未覆盖时，需完成复核，不能以未检索到直接确认。')
    if has_process and record['conclusion'] == 'no_applicable':
        raise ValueError('已有适用过程或定性标准，不能记录为没有适用标准；请核查是否仅缺数值要求。')
    return {**{k: str(record[k]).strip() for k in required}, 'confirmed_by': confirmed_by}


def official_url(value):
    parsed = urlparse(str(value))
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or not any(host == h or host.endswith('.' + h) for h in
            ('nhc.gov.cn', 'samr.gov.cn', 'sacinfo.org.cn', 'sac.gov.cn', 'chinacdc.cn', 'cdctj.com.cn')):
        raise ValueError('请填写卫健委、标准信息平台或疾控中心的 HTTPS 官方来源地址。')
    return str(value).strip()


def validate_registered_sources(records, context):
    """Human-verified additions retain full provenance; never create a CV limit."""
    result = []
    for row in records or []:
        required = ('standard', 'name', 'version', 'effective_date', 'source_url', 'sha256',
                    'source_clause', 'source_page', 'requirement_text', 'verified_by', 'checked_on', 'applicability_evidence')
        if not all(str(row.get(k) or '').strip() for k in required):
            raise ValueError('补充标准须填写名称、版本、实施日、官方链接、条款页码、要求、核查人与适用依据，并上传标准全文。')
        official_url(row['source_url'])
        normalized = re.sub(r'[^a-z0-9.]', '', row['standard'].casefold())
        for known in catalog_index()['sources']:
            if normalized == re.sub(r'[^a-z0-9.]', '', known['standard'].casefold()):
                if known['kind']=='reference_interval':
                    raise ValueError('参考区间不能登记为分析质量限值。')
                if source_status(known) != 'current':
                    raise ValueError('该标准尚未生效、已废止或全文待核查，不能补充为现行依据。')
        if row.get('kind') == 'numeric_record' and context.get('result_scale') == 'qualitative':
            raise ValueError('阳性 / 阴性结果不能采用数值型补充要求。')
        if not re.fullmatch(r'[0-9a-fA-F]{64}', row['sha256']):
            raise ValueError('标准全文记录不完整，请重新上传标准全文文件。')
        if row.get('kind') not in ('process', 'qualitative', 'numeric_record'):
            raise ValueError('参考区间及名称代码标准不能登记为分析质量限值。')
        for key in ('effective_date', 'checked_on'):
            try:
                value = date.fromisoformat(row[key])
            except (TypeError, ValueError):
                raise ValueError('补充标准日期格式错误。') from None
            if value > date.today():
                raise ValueError('补充标准尚未生效或核查日期晚于今天。')
        if row.get('withdrawn_on'):
            try:
                withdrawn = date.fromisoformat(row['withdrawn_on'])
            except (TypeError, ValueError):
                raise ValueError('补充标准废止日期格式错误。') from None
            if withdrawn <= date.fromisoformat(row['effective_date']):
                raise ValueError('补充标准废止日期须晚于实施日期。')
            if withdrawn <= date.today():
                raise ValueError('补充标准已废止，不能用于新配置。')
        if any(len(str(row[k])) > 2000 for k in required):
            raise ValueError('补充标准单项不能超过 2000 字。')
        result.append({**{k: str(row[k]).strip() for k in required}, 'kind': row['kind'],
                       'withdrawn_on': row.get('withdrawn_on', ''), 'context': dict(context),
                       'automatic_evaluation': False})
    return result
