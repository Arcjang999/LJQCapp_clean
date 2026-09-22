"""Current quality requirements, browsed by test item or general QC procedure."""
from datetime import date

from services.quality_applicability_service import catalog_index, source_status, CONTEXT_OPTIONS
from services.quality_target_service import list_catalog
from services.search_service import fuzzy_match
from services.quality_identity_service import requirement_identity

KIND_LABELS = {'numeric': '数值要求', 'process': '对照与质控要求',
               'qualitative': '定性要求', 'signal_precision': '信号精密度',
               'custom': '实验室自定要求'}
CATEGORIES = ['分子检测', '免疫检测', '生化检测', '血液与凝血', '实验室自定']


def _numeric_category(spec):
    if spec['origin'] != 'builtin':
        return '实验室自定'
    if spec['id'].startswith('wst406'):
        return '血液与凝血'
    number = int(spec['id'].rsplit('-', 1)[-1])
    return '免疫检测' if 42 <= number <= 81 else '生化检测'


def browse_quality_catalog(query='', *, kind='', category='', section='projects', status='', on_date=None):
    """Inactive editions and reference intervals never enter the working catalog."""
    today = (on_date or date.today()).isoformat()
    index = catalog_index()
    sources = {s['id']: s for s in index['sources']}
    rows = []
    if section == 'projects':
        for spec in list_catalog():
            builtin = spec['origin'] == 'builtin'
            source = sources.get('wst403-2024' if spec['id'].startswith('wst403') else 'wst406-2024', {}) if builtin else {}
            if builtin and source_status(source, on_date) != 'current':
                continue
            if not builtin and (spec.get('effective_date', '9999') > today or
                    (spec.get('withdrawn_on') and spec['withdrawn_on'] <= today)):
                continue
            rows.append(dict(id=spec['id'], name=spec['name'], standard=spec['standard'],
                category=_numeric_category(spec), kind='numeric' if builtin else 'custom',
                status='current' if builtin else 'custom', effective_date=spec['effective_date'],
                scope=spec['scope'], requirements=spec['imprecision_text'],
                aliases=[*spec.get('aliases', []), *index['aliases'].get(spec['id'], []),
                         *requirement_identity(spec).get('dictionary_names', []),
                         *requirement_identity(spec).get('identity_aliases', [])],
                source=source, spec=spec))
        for entry in index.get('project_entries', []):
            source = sources[entry['source_id']]
            if source_status(source, on_date) != 'current':
                continue
            related = [sources[identifier] for identifier in entry.get('related_source_ids', [])
                       if source_status(sources[identifier], on_date) == 'current']
            related_rules = [rule for rule in index['requirements'] if rule['id'] in entry.get('rule_ids', [])
                             and rule['source_id'] in {r['id'] for r in related}]
            requirements = [*entry['requirements'], *(requirement for rule in related_rules for requirement in rule['requirements'])]
            rows.append(dict(id=entry['id'], name=entry['name'], standard='；'.join(s['standard'] for s in [source, *related]),
                category=entry['category'], kind=entry['kind'], status='current',
                effective_date=source['effective_date'], scope=entry['scope'],
                requirements='；'.join(requirements), aliases=entry['aliases'],
                source=source, related_sources=related, related_rules=related_rules, spec=entry))
    elif section == 'general':
        for rule in index['requirements']:
            if rule.get('test_item_codes') or rule.get('test_item_names'):
                continue
            source = sources[rule['source_id']]
            if source_status(source, on_date) != 'current':
                continue
            scope = '；'.join(' / '.join(CONTEXT_OPTIONS[key][v] for v in rule[field])
                for key, field in [('technique', 'techniques'), ('result_kind', 'result_kinds'),
                                   ('result_scale', 'scales'), ('purpose', 'purposes')])
            rows.append(dict(id=rule['id'], name=rule['name'], standard=source['standard'],
                category='通用要求', kind=rule['kind'], status='current',
                effective_date=source['effective_date'], scope=scope,
                requirements='；'.join(rule['requirements']), aliases=[], source=source, spec=rule))
    return [r for r in rows if (not kind or r['kind'] == kind) and
        (not category or r['category'] == category) and (not status or r['status'] == status) and
        fuzzy_match(query, r['name'], r['standard'], r['source'].get('name', ''),
            r['category'], KIND_LABELS[r['kind']], r['scope'], r['requirements'], *r['aliases'])]


def catalog_display_rows(records):
    return [{'检验项目 / 要求': r['name'], '类别': r['category'], '现行依据': r['standard'],
        '要求类型': KIND_LABELS[r['kind']], '质量要求': r['requirements'], '适用条件': r['scope']}
        for r in records]
