"""Candidates use the user's numeric scale and preserve the initial-building gate."""
from pathlib import Path
import json
import sys
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database as db
from services.lot_lifecycle_service import create_target_profile
from services.quick_project_setup_service import get_quick_setup_context, prepare_quick_project_setup, save_quick_project_setup
from services.target_statistics_service import get_target_statistics_candidate, save_target_statistics_candidate
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.quick_project_setup_smoke_test import metadata_values, database_contents
from tests.quality_review_fixtures import fixture_conditions
from zscore_logic import create_zscore_run, get_template_id_for_level_count


NOW = datetime(2026, 11, 2, 10, 0)


def seed(method, scale='raw', *, initial=True):
    count = 1 if method == 'lj' else 2
    values = metadata_values(method, count)
    values['input_value_type'] = scale
    values['quality'].update(fixture_conditions(get_quick_setup_context(values)['item']))
    preview = prepare_quick_project_setup(values)
    saved = save_quick_project_setup(values, expected_preview_hash=preview['preview_hash'], request_key='statistics-scale')
    batch = saved['binding']['runtime_batch_id']
    base = {'raw': 101, 'ct': 21, 'log': -2}[scale]
    if initial:
        create_target_profile(method=method, batch_id=batch,
            levels=[dict(level_id=f'Level {index+1}', mean=base + index, sd=10) for index in range(count)],
            source='manual', evidence='原参数适用依据', confirmed_by='核对人', effective_at='2026-09-01')
    with db.read_snapshot() as c:
        binding = c.execute('SELECT id FROM qc_workbench_bindings WHERE qc_method=? AND runtime_batch_id=?', (method, batch)).fetchone()[0]
    return binding, batch, count, base


def add(method, batch, count, base, index, *, identical=False):
    value = base if identical else base + index - 1
    when = f'2026-10-0{index+1} 08:00:00'
    if method == 'lj':
        return db.add_result(batch, when, value, operator='尺度验收', log_value=999)
    return create_zscore_run(batch, when, '尺度验收',
        [dict(level_id=f'Level {order+1}', raw_value=value+order, log_value=999) for order in range(count)],
        template_id=get_template_id_for_level_count(count), required_n=20)


class TargetStatisticsScaleTests(unittest.TestCase):
    def test_raw_ct_and_log_use_entered_values_for_both_methods_and_freeze_scale(self):
        for method in ('lj', 'zscore'):
            for scale in ('raw', 'ct', 'log'):
                with self.subTest(method=method, scale=scale), IsolatedDatabase(), patch('services.target_statistics_service.local_now', return_value=NOW):
                    binding, batch, count, base = seed(method, scale)
                    for index in range(3):
                        add(method, batch, count, base, index)
                    before = database_contents()
                    candidate = get_target_statistics_candidate(binding, 'monthly')
                    self.assertEqual(before, database_contents())
                    self.assertTrue(candidate['ready'], candidate['issues'])
                    self.assertEqual(scale, candidate['input_value_type'])
                    for order, level in enumerate(candidate['levels']):
                        self.assertEqual(3, level['n'])
                        self.assertEqual(base+order, level['mean'])
                        self.assertEqual(1, level['sd'])
                        self.assertEqual(scale, level['input_value_type'])
                    profile = save_target_statistics_candidate(binding, candidate,
                        expected_fingerprint=candidate['fingerprint'], confirmed_by='尺度核对人',
                        evidence='按本项目输入尺度确认', effective_at=NOW, confirmed=True)
                    with db.read_snapshot() as c:
                        frozen = json.loads(c.execute('SELECT source_statistics_json FROM qc_target_profiles WHERE id=?', (profile,)).fetchone()[0])
                    self.assertEqual(candidate, frozen)

    def test_two_building_points_cannot_bypass_original_twenty_point_requirement(self):
        for method in ('lj', 'zscore'):
            with self.subTest(method=method), IsolatedDatabase(), patch('services.target_statistics_service.local_now', return_value=NOW):
                binding, batch, count, base = seed(method, initial=False)
                for index in range(2):
                    add(method, batch, count, base, index)
                candidate = get_target_statistics_candidate(binding, 'cumulative')
                self.assertFalse(candidate['ready'])
                self.assertFalse(candidate['initial_parameters_ready'])
                before = database_contents()
                with self.assertRaises(ValueError):
                    save_target_statistics_candidate(binding, candidate, expected_fingerprint=candidate['fingerprint'],
                        confirmed_by='核对人', evidence='不能提前绕过原建靶', effective_at=NOW, confirmed=True)
                self.assertEqual(before, database_contents())

    def test_one_sample_or_zero_sd_remains_unusable_after_initial_parameters_exist(self):
        for count_samples in (1, 2):
            with self.subTest(n=count_samples), IsolatedDatabase(), patch('services.target_statistics_service.local_now', return_value=NOW):
                binding, batch, count, base = seed('zscore')
                for index in range(count_samples):
                    add('zscore', batch, count, base, index, identical=True)
                candidate = get_target_statistics_candidate(binding, 'monthly')
                self.assertFalse(candidate['ready'])
                self.assertTrue(candidate['initial_parameters_ready'])
                before = database_contents()
                with self.assertRaises(ValueError):
                    save_target_statistics_candidate(binding, candidate, expected_fingerprint=candidate['fingerprint'],
                        confirmed_by='核对人', evidence='不足条件不得启用', effective_at=NOW, confirmed=True)
                self.assertEqual(before, database_contents())

    def test_core_statistics_activation_changes_policy_atomically_and_failure_rolls_back(self):
        with IsolatedDatabase(), patch('services.target_statistics_service.local_now', return_value=NOW):
            from services.target_statistics_policy_service import get_target_statistics_policy, save_target_statistics_policy
            binding, batch, count, base = seed('zscore')
            for index in range(3):
                add('zscore', batch, count, base, index)
            candidate = get_target_statistics_candidate(binding, 'monthly')
            parameters = [dict(level_id=row['level_id'], mean=row['mean'], sd=row['sd']) for row in candidate['levels']]
            profile = create_target_profile(method='zscore', batch_id=batch, levels=parameters, source='revision',
                evidence='直接底层调用也须同步策略', confirmed_by='核对人', effective_at=NOW, source_statistics=candidate)
            with db.read_snapshot() as c:
                item = c.execute('SELECT lot_config_item_id FROM qc_workbench_bindings WHERE id=?', (binding,)).fetchone()[0]
            self.assertEqual('monthly', get_target_statistics_policy('lot', item)['mode'])
            self.assertGreater(profile, 0)
            cumulative = get_target_statistics_candidate(binding, 'cumulative')
            next_parameters = [dict(level_id=row['level_id'], mean=row['mean'], sd=row['sd']) for row in cumulative['levels']]
            def fail_after_policy(*args, **kwargs):
                save_target_statistics_policy(*args, **kwargs)
                raise RuntimeError('injected policy failure')
            before = database_contents()
            with patch('services.target_statistics_policy_service.save_target_statistics_policy', side_effect=fail_after_policy):
                with self.assertRaises(RuntimeError):
                    create_target_profile(method='zscore', batch_id=batch, levels=next_parameters,
                        source='revision', evidence='写策略后中断', confirmed_by='核对人',
                        effective_at=NOW+timedelta(seconds=1), source_statistics=cumulative)
            self.assertEqual(before, database_contents())

    def test_historical_and_future_cutoffs_are_readonly_in_both_activation_paths(self):
        with IsolatedDatabase(), patch('services.target_statistics_service.local_now', return_value=NOW):
            binding, batch, count, base = seed('zscore')
            for index in range(3):
                add('zscore', batch, count, base, index)
            create_zscore_run(batch, '2026-11-01 09:00:00', '本月最新有效结果',
                [dict(level_id=f'Level {order+1}', raw_value=base+order) for order in range(count)],
                template_id=get_template_id_for_level_count(count), required_n=20)
            before = database_contents()
            for cutoff in (NOW+timedelta(hours=1), datetime(2026, 10, 20), datetime(2026, 11, 1, 8)):
                candidate = get_target_statistics_candidate(binding, 'cumulative', as_of=cutoff)
                self.assertTrue(candidate['ready'], candidate['issues'])
                parameters = [dict(level_id=row['level_id'], mean=row['mean'], sd=row['sd']) for row in candidate['levels']]
                with self.assertRaises(ValueError):
                    save_target_statistics_candidate(binding, candidate, expected_fingerprint=candidate['fingerprint'],
                        confirmed_by='核对人', evidence='只读截止范围不得启用', effective_at=NOW, confirmed=True)
                with self.assertRaises(ValueError):
                    create_target_profile(method='zscore', batch_id=batch, levels=parameters, source='revision',
                        evidence='底层同样拒绝截止范围', confirmed_by='核对人', effective_at=NOW, source_statistics=candidate)
                self.assertEqual(before, database_contents())


if __name__ == '__main__':
    unittest.main(verbosity=2)
