"""First-use progress survives restart and does not change laboratory records."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database
from services import master_data_service as master
from services.onboarding_service import (
    ONBOARDING_SETTINGS_KEY, STEP_IDS, get_onboarding_readiness, get_onboarding_state,
    has_existing_projects, save_onboarding_progress, should_auto_show_onboarding,
)
from services.product_directory_service import ensure_builtin_bondson_directory


class OnboardingServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        names = ('DB_PATH', 'DEFAULT_DB_PATH', 'STORAGE_CONFIG_PATH', 'LEGACY_DB_CANDIDATES')
        self.original = {name: getattr(database, name) for name in names}
        self.path = Path(self.temporary.name) / 'first_use.db'
        database.DB_PATH = database.DEFAULT_DB_PATH = self.path
        database.STORAGE_CONFIG_PATH = Path(self.temporary.name) / 'storage.json'
        database.LEGACY_DB_CANDIDATES = []
        database.init_db()

    def tearDown(self):
        for name, value in self.original.items():
            setattr(database, name, value)
        self.temporary.cleanup()

    def dump_without_progress(self):
        with database.read_snapshot() as connection:
            return '\n'.join(line for line in connection.iterdump()
                             if ONBOARDING_SETTINGS_KEY not in line)

    def test_new_database_shows_guide_and_progress_survives_new_process(self):
        self.assertEqual(('unit', 'materials', 'project', 'start'), STEP_IDS)
        self.assertEqual({'version': 1, 'step': 'unit', 'status': 'new'}, get_onboarding_state())
        self.assertTrue(should_auto_show_onboarding())
        saved = save_onboarding_progress('project')
        code = ('import json,sys; from pathlib import Path; import database; '
                'database.DB_PATH=Path(sys.argv[1]); '
                'from services.onboarding_service import get_onboarding_state; '
                'print(json.dumps(get_onboarding_state()))')
        fresh = subprocess.run([sys.executable, '-c', code, str(self.path)], cwd=ROOT,
                               check=True, capture_output=True, text=True)
        self.assertEqual(saved, json.loads(fresh.stdout))

    def test_skip_and_complete_suppress_automatic_open_but_allow_resuming(self):
        save_onboarding_progress('materials', status='skipped')
        self.assertFalse(should_auto_show_onboarding())
        self.assertEqual('materials', get_onboarding_state()['step'])
        save_onboarding_progress(get_onboarding_state()['step'])
        self.assertEqual('active', get_onboarding_state()['status'])
        self.assertTrue(should_auto_show_onboarding())
        save_onboarding_progress('start', status='completed')
        self.assertFalse(should_auto_show_onboarding())
        # Completing the guide is navigation only and needs no detection data.
        self.assertFalse(get_onboarding_readiness()['project'])
        save_onboarding_progress('unit')
        self.assertEqual('active', get_onboarding_state()['status'])

    def test_any_registered_project_including_draft_or_disabled_suppresses_auto_open(self):
        cases = (
            "INSERT INTO qc_project_templates(uid,template_name,status,is_disabled) VALUES('guide-draft','草稿项目','draft',1)",
            "INSERT INTO projects(name,method_type,is_disabled) VALUES('停用单水平项目','lj',1)",
            "INSERT INTO projects(name,method_type,is_disabled) VALUES('停用多水平项目','zscore',1)",
            "INSERT INTO instant_projects(name,is_disabled) VALUES('停用即时法项目',1)",
            "INSERT INTO zscore_projects(name) VALUES('多水平项目')",
        )
        with database.get_connection() as connection:
            # Optional independent project table is included when present.
            connection.execute('CREATE TABLE zscore_projects (id INTEGER PRIMARY KEY, name TEXT)')
        for sql in cases:
            with self.subTest(sql=sql):
                with database.get_connection() as connection:
                    for table in ('qc_project_templates', 'projects', 'instant_projects', 'zscore_projects'):
                        connection.execute('DELETE FROM ' + table)
                    connection.execute(sql)
                self.assertTrue(has_existing_projects())
                self.assertFalse(should_auto_show_onboarding())
                self.assertTrue(get_onboarding_readiness()['project'])

    def test_builtin_products_are_not_actual_lots_or_existing_projects(self):
        ensure_builtin_bondson_directory()
        with database.read_snapshot() as connection:
            self.assertGreater(connection.execute('SELECT COUNT(*) FROM md_qc_materials').fetchone()[0], 1000)
        self.assertFalse(has_existing_projects())
        self.assertTrue(should_auto_show_onboarding())
        self.assertEqual({key: False for key in ('unit', 'instrument', 'reagent_lot', 'qc_lot', 'project', 'active_batch')},
                         get_onboarding_readiness())

    def test_corrupt_state_recovers_without_writing_or_losing_valid_skip(self):
        for bad in ('{', 'null', '[]', '"text"', '{"step":[],"status":{}}'):
            with self.subTest(value=bad):
                database.save_app_settings({ONBOARDING_SETTINGS_KEY: bad})
                with database.read_snapshot() as connection:
                    before = '\n'.join(connection.iterdump())
                self.assertEqual({'version': 1, 'step': 'unit', 'status': 'new'}, get_onboarding_state())
                with database.read_snapshot() as connection:
                    self.assertEqual(before, '\n'.join(connection.iterdump()))
        database.save_app_settings({ONBOARDING_SETTINGS_KEY: json.dumps(
            {'version': 'invalid', 'step': 'unknown', 'status': 'skipped'})})
        self.assertEqual({'version': 1, 'step': 'unit', 'status': 'skipped'}, get_onboarding_state())
        self.assertFalse(should_auto_show_onboarding())

    def test_save_changes_only_own_setting_and_invalid_save_changes_nothing(self):
        database.save_app_settings({'lab_name': '现有单位', 'report_statement': '原报告说明', 'reviewer_name': '原复核员'})
        with database.get_connection() as connection:
            connection.execute("INSERT INTO projects(name) VALUES('已有业务项目')")
        before = self.dump_without_progress()
        for step, status in (('materials', 'active'), ('project', 'skipped'), ('start', 'completed')):
            save_onboarding_progress(step, status=status)
            self.assertEqual(before, self.dump_without_progress())
        saved = get_onboarding_state()
        for step, status in (('unknown', 'active'), ('unit', 'unknown')):
            with self.assertRaises(ValueError):
                save_onboarding_progress(step, status=status)
            self.assertEqual(saved, get_onboarding_state())
            self.assertEqual(before, self.dump_without_progress())

    def test_readiness_requires_actual_instrument_reagent_lot_and_qc_lot_level(self):
        maker = master.create_manufacturer(display_name='引导资料厂商', categories=['instrument', 'reagent', 'qc_material'])
        model = master.create_instrument_model(manufacturer_id=maker, generic_name='分析仪', model='引导型号')
        instrument = master.create_lab_instrument(instrument_model_id=model, display_name='实际仪器')
        reagent = master.create_reagent(manufacturer_id=maker, generic_name='实际试剂')
        material = master.create_qc_material(manufacturer_id=maker, generic_name='实际质控品')
        lot = master.create_qc_lot(qc_material_id=material, lot_no='QC-01', expiry_date='2028-12-31')
        database.save_app_settings({'lab_name': '  实验室  '})
        state = get_onboarding_readiness()
        self.assertTrue(state['unit'] and state['instrument'])
        self.assertFalse(state['reagent_lot'] or state['qc_lot'] or state['project'] or state['active_batch'])
        with database.get_connection() as connection:
            connection.execute('INSERT INTO md_reagent_lots(reagent_id,lot_no,expiry_date) VALUES(?,?,?)',
                               (reagent, 'R-01', '2028-12-31'))
        level = master.create_qc_level(qc_material_lot_id=lot, level_name='低值', level_order=1)
        before = self.dump_without_progress()
        self.assertTrue(get_onboarding_readiness()['reagent_lot'])
        self.assertTrue(get_onboarding_readiness()['qc_lot'])
        self.assertEqual(before, self.dump_without_progress())
        with database.get_connection() as connection:
            connection.execute('UPDATE md_qc_levels SET is_disabled=1 WHERE id=?', (level,))
            connection.execute('UPDATE lab_instruments SET is_disabled=1 WHERE id=?', (instrument,))
            connection.execute('UPDATE md_reagents SET is_disabled=1 WHERE id=?', (reagent,))
        state = get_onboarding_readiness()
        self.assertFalse(state['instrument'] or state['reagent_lot'] or state['qc_lot'])
        with database.read_snapshot() as connection:
            for table in ('qc_lot_verifications', 'qc_target_profiles', 'qc_project_templates'):
                self.assertEqual(0, connection.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0])


if __name__ == '__main__':
    unittest.main()
