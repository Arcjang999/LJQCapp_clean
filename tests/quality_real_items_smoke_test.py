"""Select shipped clinical identities, adopt sources and confirm new batches."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import get_connection
from services.master_data_service import list_test_items, list_units, create_unit, create_method
from services.project_config_service import (
    create_project_template, save_template_items, list_template_items, activate_project_template,
    create_lot_config_from_template, list_lot_config_items, save_lot_item_levels, activate_lot_config,
)
from services.quality_target_service import item_context, adopt_requirement, decode
from services.quality_review_service import standard_candidates, validate_project_quality
from tests.project_management_v11_smoke_test import TemporaryDatabaseContext, _seed_v11_configuration_dependencies
from tests.instant_v12_integration_smoke_test import rejected


# Assertions are independent of the identity mapping data file.
CASES = [
    ('1101804A', '免疫球蛋白G测定', 'wst403-2024-042', 'g/L', 'immunoassay', '血清'),
    ('1200604A', '促甲状腺激素测定', 'wst403-2024-055', 'mU/L', 'immunoassay', '血清'),
    ('1600104A', '甲胎蛋白测定', 'wst403-2024-072', 'ng/mL', 'immunoassay', '血清'),
    ('1400605A', '钾离子测定', 'wst403-2024-001', 'mmol/L', 'clinical_chemistry', '血清'),
    ('1500104A', '丙氨酸氨基转移酶测定', 'wst403-2024-021', 'U/L', 'clinical_chemistry', '血清'),
    ('0500103A', '凝血酶原时间检测', 'wst406-2024-pt', 's', 'coagulation', '血浆'),
    ('0500203A', '活化部分凝血活酶时间检测', 'wst406-2024-aptt', 's', 'coagulation', '血浆'),
    ('0500403A', '纤维蛋白原检测', 'wst406-2024-fib', 'g/L', 'coagulation', '血浆'),
    ('0500303A', '凝血酶时间检测', 'wst406-2024-tt', 's', 'coagulation', '血浆'),
]


def context(case):
    return dict(technique=case[4], specimen=case[5], result_kind='quantitative',
                result_scale='concentration', purpose='clinical')


def test_select_real_items_adopt_and_confirm_project_and_batch():
    with TemporaryDatabaseContext():
        dependencies = _seed_v11_configuration_dependencies()
        dictionary = list_test_items()
        codes = [case[0] for case in CASES]
        with get_connection() as connection:
            before = [tuple(row) for row in connection.execute(
                'SELECT * FROM md_test_items WHERE standard_code IN (' + ','.join('?' for _ in codes) + ') ORDER BY id', codes)]
        units = {row['symbol']: int(row['id']) for row in list_units().to_dict('records')}
        for case in CASES:
            if case[3] not in units:
                units[case[3]] = create_unit(symbol=case[3])
        methods = {
            'immunoassay': create_method(method_name='核查用化学发光免疫法'),
            'clinical_chemistry': create_method(method_name='核查用生化定量法'),
            'coagulation': create_method(method_name='核查用凝血初筛法'),
        }
        project = create_project_template(template_name='真实内置项目标准核查',
            lab_instrument_id=dependencies['lab_instrument_id'], qc_material_id=dependencies['qc_material_id'],
            default_reagent_id=dependencies['reagent_id'])
        rows = []
        by_item = {}
        for case in CASES:
            found = dictionary.loc[dictionary.standard_code == case[0]]
            assert len(found) == 1 and found.iloc[0]['chinese_name'] == case[1]
            test_item_id = int(found.iloc[0]['id'])
            by_item[test_item_id] = case
            rows.append(dict(test_item_id=test_item_id, qc_method='lj', input_value_type='raw',
                unit_id=units[case[3]], method_id=methods[case[4]], reagent_id=dependencies['reagent_id'],
                level_count=1, target_n=20, cv_limit=None))
        save_template_items(project, rows)
        rejected(lambda: activate_project_template(project), '质量目标待确认')
        for row in list_template_items(project).to_dict('records'):
            case = by_item[row['test_item_id']]
            item = item_context('project', row['id'])
            assert item['test_item_name'] == case[1] and item['standard_code'] == case[0]
            assert [source['id'] for source in standard_candidates(item)] == [case[2]]
            goal = adopt_requirement('project', row['id'], case[2], confirmed_by='隔离服务验收',
                evidence='已核对实际内置项目、方法、基质、单位与现行分析质量要求。', context=context(case))
            assert goal['spec']['id'] == case[2] and not validate_project_quality(row['id'])
        activate_project_template(project)

        batch = create_lot_config_from_template(template_id=project,
            qc_material_lot_id=dependencies['source_lot_id'], config_name='真实项目新批次')
        for row in list_lot_config_items(batch).to_dict('records'):
            case = by_item[row['test_item_id']]
            save_lot_item_levels(row['id'], [dict(qc_level_id=dependencies['source_levels'][0],
                target_source='building', target_mean=3, target_sd=0.1, target_confirmed=True)])
            assert decode(item_context('lot', row['id'])['quality_goal_json'])['pending']
            category = '正常' if case[4] == 'coagulation' else ''
            goal = adopt_requirement('lot', row['id'], case[2], confirmed_by='隔离服务验收',
                evidence='已逐水平核对正常浓度质控品与适用标准。', context=context(case),
                levels=[dict(level_order=1, concentration=3, category=category)])
            assert goal['levels'][0]['rule']['kind'] == 'cv'
        activate_lot_config(batch)
        with get_connection() as connection:
            assert connection.execute('SELECT status FROM qc_lot_configs WHERE id=?', (batch,)).fetchone()[0] == 'active'
            after = [tuple(row) for row in connection.execute(
                'SELECT * FROM md_test_items WHERE standard_code IN (' + ','.join('?' for _ in codes) + ') ORDER BY id', codes)]
        assert before == after, 'Selecting/adopting a standard must not rename the official test item.'


def test_real_urine_igg_cannot_adopt_the_blood_item_by_name():
    with TemporaryDatabaseContext():
        dependencies = _seed_v11_configuration_dependencies()
        dictionary = list_test_items()
        urine_id = int(dictionary.loc[dictionary.standard_code == '1101810A'].iloc[0]['id'])
        unit_id = int(list_units().loc[lambda frame: frame.symbol == 'g/L'].iloc[0]['id'])
        method_id = create_method(method_name='尿液免疫比浊法核查')
        project = create_project_template(template_name='尿液IgG适用核查',
            lab_instrument_id=dependencies['lab_instrument_id'], qc_material_id=dependencies['qc_material_id'])
        save_template_items(project, [dict(test_item_id=urine_id, qc_method='lj', input_value_type='raw',
            unit_id=unit_id, method_id=method_id, reagent_id=dependencies['reagent_id'], level_count=1, target_n=20)])
        item_id = int(list_template_items(project).iloc[0]['id'])
        assert not standard_candidates(item_context('project', item_id))
        rejected(lambda: adopt_requirement('project', item_id, 'wst403-2024-042',
            confirmed_by='隔离服务验收', evidence='尿液项目必须另核对适用依据。',
            context=dict(technique='immunoassay', specimen='尿液', result_kind='quantitative',
                         result_scale='concentration', purpose='clinical')), '不符')
        assert decode(item_context('project', item_id)['quality_goal_json']) == {}
        rejected(lambda: activate_project_template(project), '质量目标待确认')


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
            print('PASS', name, flush=True)
