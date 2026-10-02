"""Persistent isolated daily-workbench sample, using confirmed engineering fixtures."""
import argparse
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def seed(output):
    import database as db
    from tests.daily_entry_smoke_test import seed_twenty
    from services.daily_context_service import get_daily_context
    from services.daily_entry_service import validate_submission, submit_daily
    from services.lot_lifecycle_service import create_target_profile
    from services.instant_service import save_instant_result
    from services.value_type_service import parse_project_input_value
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=True)
    path = output / 'acceptance.db'
    assert not path.exists(), 'Fresh output directory required'
    db.DB_PATH = db.DEFAULT_DB_PATH = path; db.STORAGE_CONFIG_PATH = output / 'storage_config.json'
    db.LEGACY_DB_CANDIDATES = []; db.init_db()
    fixture = seed_twenty()
    def context():
        return get_daily_context(fixture['data']['lab_instrument_id'], fixture['data']['qc_material_id'],
            fixture['main_lot'], '2026-09-28 07:00:00', lot_config_id=fixture['config_id'])
    first = context()
    profiles = set()
    for item in first['items']:
        key = item['qc_method'], item['level_count']
        if item['qc_method'] == 'instant' or key in profiles:
            continue
        create_target_profile(method=item['qc_method'], batch_id=item['runtime_batch_id'],
            levels=[dict(level_id=level['level_id'], mean=100 + level['level_order']*10, sd=1) for level in item['levels']],
            source='manual', evidence='合成工程数据的人工确认参数，仅核验软件流程。',
            confirmed_by='独立验收', effective_at='2026-09-01')
        profiles.add(key)
    first = context()
    request = dict(submission_id='browser-seed', selection=first['selection'], context_revision=first['context_revision'],
        test_time=first['test_time'], operator='隔离浏览器验收', items=[dict(row_key=item['row_key'],
            reagent_lot_id=fixture['reagent'], levels=[dict(qc_level_id=l['qc_level_id'],
                value=str(130 if index == 0 else 100 + l['level_order']*10)) for l in item['levels']])
            for index, item in enumerate(first['items'])])
    validated = validate_submission(request)
    assert validated['valid'], validated['errors']
    receipt = submit_daily(validated['frozen_request'])
    instant = [r for r in first['items'] if r['qc_method'] == 'instant']
    for item, count in zip(instant, (2, 3, 20)):
        for index in range(1, count):
            value, log, error = parse_project_input_value(str(110 + (index % 5 - 2)*0.1), item['input_value_type'])
            assert error is None
            save_instant_result(batch_id=item['runtime_batch_id'], test_time=f'2026-09-28 07:{index:02d}:00',
                operator='即时法阶段验收', value=value, log_value=log,
                lot_selection={'reagent_lot_id': fixture['reagent']})
    result = dict(fixture=fixture, first_submission=receipt, path=str(path),
        instant_stage_samples=[dict(batch_id=item['runtime_batch_id'], row_key=item['row_key'], count=count)
                               for item, count in zip(instant, (2, 3, 20))])
    (output / 'fixture.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'database':str(path),'group_items':len(first['items']),
                      'instant_stage_samples':result['instant_stage_samples']},ensure_ascii=False,indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    seed(parser.parse_args().output)
