"""Exercise the shipped item dictionary rather than renamed synthetic items."""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.quality_identity_service import (
    identity_index, numeric_identity_matches, requirement_identity, specimen_issue,
)


def main():
    specs = json.loads((ROOT / 'resources/quality_targets_2024.json').read_text())['requirements']
    by_spec = {spec['id']: spec for spec in specs}
    with (ROOT / 'data/dictionaries/wst_886_2026_quantitative.csv').open() as stream:
        items = list(csv.DictReader(stream))
    by_code = {row['standard_code']: row for row in items}
    matrix = identity_index()['requirements']
    assert {row['requirement_id'] for row in matrix} == set(by_spec)
    assert len(matrix) == 94 and len(identity_index()['dictionary_items']) == 296
    assert len([row for row in matrix if row['dictionary_codes']]) == 93
    assert {row['requirement_id'] for row in matrix if not row['dictionary_codes']} == {'wst403-2024-082'}

    # Every one of the 296 shipped records is checked against every numeric
    # requirement. Recognized codes cannot be widened by a conflicting alias.
    for item in items:
        item = {**item, 'test_item_name': item['chinese_name']}
        expected = {row['requirement_id'] for row in matrix if item['standard_code'] in row['dictionary_codes']}
        actual = {spec['id'] for spec in specs if numeric_identity_matches(item, spec)}
        assert actual == expected, (item['test_item_name'], actual, expected)
        without_code = {**item, 'standard_code': ''}
        assert {spec['id'] for spec in specs if numeric_identity_matches(without_code, spec)} == expected

    examples = {
        '1101804A': 'wst403-2024-042', '1200604A': 'wst403-2024-055',
        '1600104A': 'wst403-2024-072', '1400605A': 'wst403-2024-001',
        '1500104A': 'wst403-2024-021', '0500103A': 'wst406-2024-pt',
        '0500203A': 'wst406-2024-aptt', '0500403A': 'wst406-2024-fib',
        '0500303A': 'wst406-2024-tt',
    }
    for code, identifier in examples.items():
        item = by_code[code]
        assert numeric_identity_matches(item, by_spec[identifier])
        assert not specimen_issue(item, {'specimen': item['specimen_type']}, by_spec[identifier])

    igg = by_spec['wst403-2024-042']
    for code in ('1101810A', '1101812A'):
        urine_or_csf = {**by_code[code], 'test_item_name': by_code[code]['chinese_name'], 'abbreviation': 'IgG'}
        assert not numeric_identity_matches(urine_or_csf, igg)
        assert not numeric_identity_matches({**urine_or_csf, 'standard_code': ''}, igg)
    assert numeric_identity_matches({'test_item_name': 'IgG'}, igg)
    assert not specimen_issue({'test_item_name': 'IgG'}, {'specimen': '血清质控品'}, igg)
    assert specimen_issue({'test_item_name': 'IgG'}, {'specimen': '尿液'}, igg)
    assert specimen_issue({'test_item_name': 'IgG'}, {'specimen': '待定'}, igg)
    assert specimen_issue({'test_item_name': '尿液IgG'}, {'specimen': '血清'}, igg)
    assert specimen_issue({'test_item_name': 'IgG', 'specimen_type': '尿液'}, {'specimen': '血清'}, igg)
    assert not numeric_identity_matches({'test_item_name': 'TSH受体抗体'}, by_spec['wst403-2024-055'])
    assert not numeric_identity_matches(by_code['1400805A'], by_spec['wst403-2024-004'])
    assert not numeric_identity_matches(by_code['1500304A'], by_spec['wst403-2024-022'])
    assert not numeric_identity_matches(by_code['2600104A'], igg)
    assert numeric_identity_matches({'test_item_name': 'ＩｇＧ'}, igg)
    assert numeric_identity_matches({'test_item_name': '血清免疫球蛋白G'}, igg)
    assert not numeric_identity_matches({'test_item_name': '免球蛋白G'}, igg)
    # Distinct result kinds retain distinct identities even for one analyte.
    assert not numeric_identity_matches(by_code['2100104A'], by_spec['wst403-2024-034'])
    assert not numeric_identity_matches(by_code['2100204A'], by_spec['wst403-2024-033'])
    assert numeric_identity_matches(by_code['1100301A'], by_spec['wst403-2024-037'])
    assert numeric_identity_matches(by_code['1100301A'], by_spec['wst403-2024-038'])
    assert not numeric_identity_matches(by_code['1700104A'], by_spec['wst403-2024-082'])
    assert numeric_identity_matches({'test_item_name': '葡萄糖（便携式血糖仪）'}, by_spec['wst403-2024-082'])
    print('quality identity smoke passed: 94 requirements, 296 dictionary items, exact identity and specimen checks')


if __name__ == '__main__':
    main()
