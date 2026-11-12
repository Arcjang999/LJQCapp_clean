"""Authorization protects first entry, fragment writes and maintenance commands."""
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
import database
import run_app
from desktop_licensing.client import AccessResult
from desktop_licensing import runtime
from tests.instant_v12_integration_smoke_test import IsolatedDatabase


class SessionClient:
    def __init__(self):
        self.allowed = False

    def check(self, code=None):
        if code == 'ISOLATED-VALID-CODE':
            self.allowed = True
        if self.allowed:
            return AccessResult(True, 'online', '验证通过。', 1796054340, 1796054340)
        if code:
            return AccessResult(False, 'denied', '验证码无效，请核对后重试。')
        return AccessResult(False, 'needs_code', '请输入验证码。')


class DesktopLicenseAccessTests(unittest.TestCase):
    def test_frozen_and_native_runtime_cannot_become_unlicensed_by_cloud_environment(self):
        with patch.dict('os.environ', {'LJQC_DEPLOYMENT_MODE': 'cloud', 'LJQC_DATA_ROOT': '/tmp/elsewhere'}):
            with patch.object(sys, 'frozen', True, create=True):
                self.assertTrue(runtime.authorization_required())
            with patch.object(runtime, '__file__', '/isolated/desktop_licensing/runtime.cpython-313.so'):
                self.assertTrue(runtime.authorization_required())
        self.assertFalse(runtime.authorization_required())

    def test_unconfigured_gate_returns_before_initializing_business_database(self):
        source = '''
from unittest.mock import patch
import app_main
from desktop_licensing.client import LicenseClient
with patch('ui.license_access.authorization_required', return_value=True), \\
     patch('ui.license_access.create_client', return_value=LicenseClient(None, '/unused-state')), \\
     patch.object(app_main, 'init_db', side_effect=AssertionError('Business DB opened before activation')):
    app_main.main()
'''
        app = AppTest.from_string(source, default_timeout=30).run()
        self.assertFalse(list(app.exception), list(app.exception))
        self.assertEqual(app.title[0].value, '软件激活')
        self.assertTrue(any('尚未配置' in item.value for item in app.error))

    def test_activation_form_requires_explicit_submit_and_wrong_code_can_be_retried(self):
        source = '''
import streamlit as st
from ui.license_access import render_license_gate
if render_license_gate(client=st.session_state['isolated_client'], required=True):
    st.success('已进入业务')
'''
        client = SessionClient()
        app = AppTest.from_string(source, default_timeout=30)
        app.session_state['isolated_client'] = client
        app.run()
        app.text_input[0].set_value('WRONG-CODE').run()
        self.assertFalse(client.allowed)
        next(button for button in app.button if button.label == '验证并进入软件').click().run()
        self.assertFalse(list(app.exception), list(app.exception))
        self.assertTrue(any('验证码无效' in item.value for item in app.error))
        app.text_input[0].set_value('ISOLATED-VALID-CODE')
        next(button for button in app.button if button.label == '验证并进入软件').click().run()
        self.assertFalse(list(app.exception), list(app.exception))
        self.assertTrue(client.allowed)
        self.assertEqual(app.success[0].value, '已进入业务')

    def test_expired_fragment_transaction_rolls_back_and_releases_its_lock(self):
        for use_context in (False, True):
            with self.subTest(use_context=use_context), IsolatedDatabase():
                client = SessionClient()
                client.allowed = True
                target = Path(database.DB_PATH)
                with patch.object(runtime, 'authorization_required', return_value=True), \
                     patch.object(runtime, '_business_client', return_value=client):
                    if use_context:
                        with self.assertRaises(runtime.BusinessAccessDenied):
                            with database.get_connection() as connection:
                                connection.execute('INSERT INTO app_settings(key,value) VALUES (?,?)',
                                                   ('license-expiry-probe', 'not-saved'))
                                client.allowed = False
                    else:
                        connection = database.get_connection()
                        connection.execute('INSERT INTO app_settings(key,value) VALUES (?,?)',
                                           ('license-expiry-probe', 'not-saved'))
                        client.allowed = False
                        try:
                            with self.assertRaises(runtime.BusinessAccessDenied):
                                connection.commit()
                        finally:
                            connection.close()
                with sqlite3.connect(target, timeout=1) as reader:
                    self.assertEqual(0, reader.execute(
                        "SELECT count(*) FROM app_settings WHERE key='license-expiry-probe'").fetchone()[0])
                    reader.execute('BEGIN IMMEDIATE')
                    reader.rollback()

    def test_denial_blocks_new_connections_and_nested_atomic_writes(self):
        with IsolatedDatabase():
            client = SessionClient()
            with patch.object(runtime, 'authorization_required', return_value=True), \
                 patch.object(runtime, '_business_client', return_value=client):
                with self.assertRaises(runtime.BusinessAccessDenied):
                    database.get_connection()
                client.allowed = True
                with self.assertRaises(runtime.BusinessAccessDenied):
                    with database.atomic_write():
                        client.allowed = False
                        with database.atomic_write():
                            self.fail('An expired fragment received a write transaction')

    def test_candidate_scope_only_allows_its_exact_database_and_does_not_linger(self):
        import json
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            target = root / 'data' / 'qc_lj_app.db'
            (root / 'update-candidate.json').write_text(json.dumps(dict(
                format='ljqc-update-candidate', base_release='1.0.0-2026110606')))
            with runtime.candidate_preflight_access(target):
                runtime.assert_business_access(target)
                with self.assertRaises(runtime.BusinessAccessDenied):
                    runtime.assert_business_access(root / 'other.db')
            with patch.object(runtime, 'authorization_required', return_value=True), \
                 patch.object(runtime, '_business_client', return_value=SessionClient()):
                with self.assertRaises(runtime.BusinessAccessDenied):
                    runtime.assert_business_access(target)

    def test_packaged_reset_and_demo_commands_stop_before_storage_is_opened(self):
        with patch.object(runtime, 'authorization_required', return_value=True), \
             patch.object(run_app, 'reset_configured_database', side_effect=AssertionError('reset called')), \
             patch.object(run_app, 'seed_demo_database', side_effect=AssertionError('seed called')):
            self.assertEqual(1, run_app.main(['--reset-db']))
            self.assertEqual(1, run_app.main(['--seed-demo']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
