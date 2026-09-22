"""Reviewed analyte identities; search similarity never authorizes adoption."""
from __future__ import annotations

import json
import unicodedata
from functools import lru_cache
from pathlib import Path


INDEX_PATH = Path(__file__).resolve().parents[1] / 'resources' / 'quality_identity_2026.json'


def _key(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', str(value or '')).casefold() if c.isalnum())


@lru_cache(maxsize=1)
def identity_index():
    return json.loads(INDEX_PATH.read_text(encoding='utf-8'))


def requirement_identity(spec):
    identifier = spec.get('id') if isinstance(spec, dict) else spec
    return next((row for row in identity_index()['requirements'] if row['requirement_id'] == identifier), {})


def numeric_identity_matches(item, spec, extra_aliases=()):
    """Resolve known dictionary identities before considering exact custom aliases.

    In particular a urine or CSF dictionary record cannot inherit the blood
    analyte's standard by adding its abbreviation. Unknown local codes may use
    the exact, reviewed names. Method, specimen and units still require checks.
    """
    identity = requirement_identity(spec)
    dictionary = identity_index()['dictionary_items']
    known_codes = {row['code'] for row in dictionary}
    code = str(item.get('standard_code') or '').strip().upper()
    if code in known_codes:
        return code in identity.get('dictionary_codes', [])
    name = _key(item.get('test_item_name') or item.get('chinese_name'))
    named_codes = {row['code'] for row in dictionary if _key(row['name']) == name}
    if named_codes:
        return bool(named_codes.intersection(identity.get('dictionary_codes', [])))
    aliases = [spec.get('name'), *spec.get('aliases', []), *extra_aliases,
               *identity.get('dictionary_names', []), *identity.get('identity_aliases', [])]
    keys = {_key(value) for value in aliases if value}
    item_names = [item.get('test_item_name'), item.get('chinese_name'), item.get('abbreviation'),
                  *item.get('test_aliases', [])]
    names = {_key(value) for value in item_names if value}
    # Serum/plasma prefixes describe context, but the specimen check must still
    # validate them. Never strip urine/CSF or generically remove method suffixes.
    for value in list(names):
        for prefix in ('血清', '血浆'):
            if value.startswith(prefix):
                names.add(value[len(prefix):])
    return bool(names.intersection(keys))


def process_identity_matches(item, rule):
    """Specialized guidelines require the exact reviewed analyte identity."""
    if not rule.get('test_item_codes') and not rule.get('test_item_names'):
        return True
    primary_name = _key(item.get('test_item_name') or item.get('chinese_name'))
    if any(_key(token) in primary_name for token in rule.get('excluded_name_tokens', [])):
        return False
    allowed_codes = set(rule.get('test_item_codes', []))
    dictionary = identity_index()['dictionary_items']
    code = str(item.get('standard_code') or '').strip().upper()
    if code in {row['code'] for row in dictionary}:
        return code in allowed_codes
    name = _key(item.get('test_item_name') or item.get('chinese_name'))
    named_codes = {row['code'] for row in dictionary if _key(row['name']) == name}
    if named_codes:
        return bool(named_codes & allowed_codes)
    names = {_key(item.get(key)) for key in ('test_item_name', 'chinese_name', 'abbreviation')}
    names.update(_key(value) for value in item.get('test_aliases', []))
    return bool(names & {_key(value) for value in rule.get('test_item_names', [])})


def _specimens(value):
    text = _key(value)
    found = {word for word in ('血清', '血浆', '全血', '尿液', '脑脊液', '唾液', '胸水',
                               '腹水', '乳汁', '粪便', '组织', '血斑') if word in text}
    if any(word in text for word in ('静脉血', '末梢血', '毛细血管血', '指尖血')):
        found.add('全血')
    if 'serum' in text:
        found.add('血清')
    if 'plasma' in text:
        found.add('血浆')
    if 'wholeblood' in text:
        found.add('全血')
    if 'urine' in text:
        found.add('尿液')
    if text == 'csf' or 'cerebrospinalfluid' in text:
        found.add('脑脊液')
    return found


def specimen_issue(item, context, spec):
    """Return a pending-review reason, not a fabricated standard exclusion.

    WS/T 403 does not enumerate specimen restrictions for each table row. The
    identity matrix therefore defines the presently reviewed routine contexts;
    a different matrix needs review instead of an automatic applicability claim.
    """
    identity = requirement_identity(spec)
    if not identity:
        return '该项目的标本适用情况尚待核对，请补充对应依据。'
    expected = set(identity.get('reviewed_specimens', []))
    actual = _specimens(context.get('specimen'))
    if not actual:
        return '请明确标本类型或质控品对应的基质，再核对质量要求。'
    # A conflicting specimen in an explicitly named local analyte must not be
    # hidden by editing the context to a blood specimen.
    name = str(item.get('test_item_name') or item.get('chinese_name') or '')
    for prefix in ('尿液', '脑脊液', '唾液', '胸水', '腹水', '乳汁', '粪便'):
        if name.startswith(prefix) and prefix not in actual:
            return '检验项目名称与所填标本不一致，请核对后再采用质量要求。'
    declared = _specimens(item.get('specimen_type'))
    if declared and not actual.issubset(declared):
        return '所填标本与检验项目资料中的标本不一致，请核对后再采用质量要求。'
    if not actual.issubset(expected):
        return ('当前标本或基质尚未完成本条要求的适用核对；请结合标准原文及检测方法确认，'
                '不能仅凭项目名称采用。')
    return ''
