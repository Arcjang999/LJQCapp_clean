"""Incomplete new-project forms survive restart without creating business rows."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database
from services.project_setup_draft_service import (
    PROJECT_SETUP_DRAFT_KEY, clear_project_setup_draft,
    load_project_setup_draft, save_project_setup_draft,
)
from tests.instant_v12_integration_smoke_test import IsolatedDatabase


def context(**updates):
    value = dict(kind='quick', template_id=None, item_id=None, token=uuid4().hex,
        creation_draft_id=uuid4().hex, draft={}, initial={}, discard=False,
        quick_step='details')
    value.update(updates)
    return value


def snapshot(*, exclude_draft=False):
    with database.read_snapshot() as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        result = {}
        for table in tables:
            rows = list(connection.execute('SELECT * FROM "' + table.replace('"', '""') + '"'))
            if exclude_draft and table == 'app_settings':
                rows = [row for row in rows if row['key'] != PROJECT_SETUP_DRAFT_KEY]
            result[table] = sorted((tuple(row) for row in rows), key=repr)
        return result


class ProjectSetupDraftTests(unittest.TestCase):
    def setUp(self):
        self.isolated = IsolatedDatabase()
        self.isolated.__enter__()

    def tearDown(self):
        self.isolated.__exit__(None, None, None)

    def test_empty_incomplete_form_only_writes_its_setting_and_clear_restores_tables(self):
        before = snapshot()
        business_before = snapshot(exclude_draft=True)
        self.assertIsNone(load_project_setup_draft())
        value = context(draft={'template_name': '', 'lab_instrument_id': None, 'quality': {}})
        save_project_setup_draft(value, {})
        self.assertEqual({'context': value, 'widgets': {}}, load_project_setup_draft())
        self.assertEqual(business_before, snapshot(exclude_draft=True))
        clear_project_setup_draft(value['creation_draft_id'])
        self.assertIsNone(load_project_setup_draft())
        self.assertEqual(before, snapshot())

    def test_dates_tuples_numpy_scalars_and_nested_quality_round_trip(self):
        value = context(draft={'notes': '首次填写', 'quality': {
            'checked_on': date(2026, 11, 9),
            'updated': datetime(2026, 11, 9, 8, 30, tzinfo=timezone.utc),
            'levels': [{'concentration': np.float64(1.25), 'level_order': np.int64(2)}],
            'checked': np.bool_(True)}})
        widgets = {'quality_catalog_level_identity': [(1, 23), (2, 24)],
                   'registered_date': date(2026, 11, 9), 'blank': None,
                   'multiple': ('第一项', [np.int64(3), ('第二项',)])}
        save_project_setup_draft(value, widgets)
        loaded = load_project_setup_draft()
        self.assertEqual(value, loaded['context'])
        self.assertEqual(widgets, loaded['widgets'])
        self.assertIs(type(loaded['widgets']['quality_catalog_level_identity'][0]), tuple)
        self.assertIs(type(loaded['context']['draft']['quality']['levels'][0]['level_order']), int)
        loaded['context']['draft']['notes'] = '只改读取结果'
        self.assertEqual('首次填写', load_project_setup_draft()['context']['draft']['notes'])

    def test_saved_form_is_readable_in_a_new_process_with_request_token_unchanged(self):
        value = context(draft={'notes': '重启后继续', 'check_date': date(2026, 11, 9)})
        save_project_setup_draft(value, {'catalog_identity': [(1, 2)]})
        code = '''from pathlib import Path
import json, sys
import database
database.DB_PATH = Path(sys.argv[1])
database.LEGACY_DB_CANDIDATES = []
from services.project_setup_draft_service import load_project_setup_draft
saved = load_project_setup_draft()
print(json.dumps({'token': saved['context']['token'],
    'notes': saved['context']['draft']['notes'],
    'date': saved['context']['draft']['check_date'].isoformat(),
    'tuple': type(saved['widgets']['catalog_identity'][0]).__name__}))
'''
        fresh = subprocess.run([sys.executable, '-c', code, str(database.DB_PATH)],
            cwd=ROOT, check=True, capture_output=True, text=True)
        self.assertEqual({'token': value['token'], 'notes': '重启后继续',
            'date': '2026-11-09', 'tuple': 'tuple'}, json.loads(fresh.stdout))

    def test_same_draft_can_update_and_other_identity_cannot_overwrite_or_clear_it(self):
        value = context(draft={'notes': '先写的内容'})
        save_project_setup_draft(value, {})
        value['kind'] = 'project'
        value['draft']['notes'] = '切换设置方式后继续写'
        save_project_setup_draft(value, {'project_notes': '当前输入'})
        expected = load_project_setup_draft()
        before = snapshot()
        other = context()
        with self.assertRaisesRegex(ValueError, '已有未完成的项目草稿'):
            save_project_setup_draft(other, {})
        clear_project_setup_draft(other['creation_draft_id'])
        self.assertEqual(expected, load_project_setup_draft())
        self.assertEqual(before, snapshot())

    def test_failed_save_and_clear_roll_back_without_losing_the_previous_form(self):
        value = context(draft={'notes': '原有草稿'})
        save_project_setup_draft(value, {})
        before = snapshot()

        @contextmanager
        def fail_before_commit():
            with database.atomic_write() as connection:
                yield connection
                raise sqlite3.OperationalError('Injected failure before draft commit')

        changed = deepcopy(value)
        changed['draft']['notes'] = '尚未提交的新内容'
        with patch('services.project_setup_draft_service.atomic_write', fail_before_commit):
            for action in (lambda: save_project_setup_draft(changed, {}),
                           lambda: clear_project_setup_draft(value['creation_draft_id'])):
                with self.assertRaises(sqlite3.OperationalError):
                    action()
                self.assertEqual(before, snapshot())
        self.assertEqual(value, load_project_setup_draft()['context'])

    def test_corrupt_or_unsupported_saved_payload_is_not_overwritten_or_deleted(self):
        value = context()
        for raw, message in (('{', '无法读取'), ('{}', '无法读取'),
                (json.dumps({'version': 2, 'context': value, 'widgets': {}}), '版本不受支持'),
                (json.dumps({'version': 1, 'context': {}, 'widgets': {}}), '无法读取'),
                (json.dumps({'version': 1, 'context': value, 'widgets': {
                    'bad': {'__project_setup_draft_type__': 'tuple', 'items': 'wrong'}}}), '无法读取')):
            with self.subTest(raw=raw):
                database.save_app_settings({PROJECT_SETUP_DRAFT_KEY: raw})
                before = snapshot()
                for action in (load_project_setup_draft, lambda: save_project_setup_draft(value, {}),
                               lambda: clear_project_setup_draft(value['creation_draft_id'])):
                    with self.assertRaisesRegex(ValueError, message):
                        action()
                    self.assertEqual(before, snapshot())

    def test_existing_editors_invalid_identity_files_and_unsupported_values_are_rejected(self):
        value = context()
        cases = [
            (context(kind='item'), {}),
            (context(template_id=1), {}),
            (context(item_id=1), {}),
            (context(creation_draft_id='invalid'), {}),
            (context(token=''), {}),
            (context(quick_step='unknown'), {}),
            (context(draft=[]), {}),
            (value, {'standard_file': None}),
            (value, {'bad': b'file data'}),
            (value, {'bad': float('nan')}),
            (value, {'bad': object()}),
            (value, {1: 'invalid key'}),
            (value, {'bad': {'__project_setup_draft_type__': 'date', 'value': '2026-11-09'}}),
        ]
        before = snapshot()
        for ctx, widgets in cases:
            with self.subTest(ctx=ctx, widgets=widgets), self.assertRaises(ValueError):
                save_project_setup_draft(ctx, widgets)
            self.assertEqual(before, snapshot())


if __name__ == '__main__':
    unittest.main(verbosity=2)
