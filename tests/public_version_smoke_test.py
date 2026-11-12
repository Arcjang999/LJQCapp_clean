"""Public labels remain separate from signed internal release comparisons."""
from __future__ import annotations

from contextlib import closing, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'packaging')]

from build_update_patch import export_patch
from desktop_updates.integrated_install import _current
from services.release_info import ReleaseInfo, display_version_label, get_release_info
from services.runtime_edition import runtime_version_label
from services.software_download_service import get_software_download_catalog
from tests import desktop_update_manager_smoke_test as manager_fixture
from tests import software_downloads_smoke_test as download_fixture
import run_app


class PublicVersionTests(unittest.TestCase):
    def test_metadata_label_is_bound_to_its_own_internal_version(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            release = dict(version='1.1.1', display_version='1.01.1', build_number=2026111201)
            (root / 'release.json').write_text(json.dumps(release))
            info = get_release_info(root)
            self.assertEqual(('1.1.1', '1.01.1', '1.1.1'),
                             (info.version, info.display_version, info.as_dict()['app_version']))
            for metadata, expected in ((dict(version='1.0.1'), '1.0.1'),
                    (dict(app_version='1.0.1'), '1.0.1'),
                    (dict(version='1.0.1', display_version='1.01'), '1.01'),
                    (dict(version='1.2.0', display_version='1.02'), '1.02'),
                    (dict(version='1.1.1'), '1.01.1'),
                    (dict(version='1.1.1', display_version='<script>'), '1.1.1')):
                with self.subTest(metadata=metadata):
                    (root / 'build_info.json').write_text(json.dumps(metadata))
                    self.assertEqual(expected, get_release_info(root).display_version)

    def test_legacy_identity_and_runtime_diagnostics_keep_internal_metadata(self):
        legacy = ReleaseInfo('1.0.1', 2026110901)
        self.assertEqual('1.0.1', legacy.as_dict()['app_version'])
        self.assertEqual('1.0.1', display_version_label(legacy.as_dict()))
        current = ReleaseInfo('1.1.1', 2026111201, display_version='1.01.1')
        with patch('services.runtime_edition.get_release_info', return_value=current), \
                patch('services.runtime_edition.get_runtime_edition', return_value='desktop'):
            self.assertEqual('桌面版 · 版本 1.01.1 · 构建 2026111201', runtime_version_label())
        output = io.StringIO()
        with patch('run_app._read_build_info', return_value=current.as_dict()), redirect_stdout(output):
            self.assertEqual(0, run_app.print_version())
        self.assertIn('LJQCApp 版本：1.01.1', output.getvalue())
        self.assertEqual('1.1.1', current.as_dict()['version'])
        for invalid in ('<b>1.01.1</b>', '1.01.1\nnew', 'x' * 200, None, True):
            self.assertEqual('1.1.1', display_version_label(dict(version='1.1.1', display_version=invalid)))

    def test_signed_patch_through_state_switch_preserves_public_label_and_internal_version(self):
        fixture = manager_fixture.UpdateManagerTests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        payload = fixture.folder / 'new-labelled-program'
        payload.mkdir()
        (payload / 'LJQCApp.exe').write_bytes(b'isolated inert version fixture')
        metadata = dict(version='1.1.1', display_version='1.01.1', build_number=2026111201,
            data_format_version=1, target='windows-x86_64', git_commit='a' * 40,
            source_digest='b' * 64, source_dirty=False)
        output = fixture.folder / 'new-labelled-export'
        fixture.manifest = export_patch(payload, metadata, output)
        fixture.archive = output / fixture.manifest['artifact']['url'].rsplit('/', 1)[-1]
        accepted = fixture.accept()
        self.assertEqual(('1.1.1', '1.01.1'), (accepted['version'], accepted['display_version']))
        with patch.object(fixture.manager, '_preflight', side_effect=fixture.fake_preflight):
            fixture.manager.apply(accepted, fixture.archive)
        current = fixture.manager.get_state()['current']
        self.assertEqual(('1.1.1', '1.01.1'), (current['version'], current['display_version']))
        self.assertEqual('1.01.1', display_version_label(current))
        self.assertEqual('1.0.0', fixture.manager.get_state()['previous']['version'])

    def test_fresh_installer_state_supports_labelled_and_legacy_manifests(self):
        value = dict(version='1.1.1', display_version='1.01.1', build_number=2026111201,
            payload={'entrypoint': 'LJQCApp.exe'}, source_digest='b' * 64, source_commit='a' * 40)
        current = _current(value, Path('/isolated-program'), 'windows-x86_64')
        self.assertEqual(('1.1.1', '1.01.1'), (current['version'], current['display_version']))
        value.pop('display_version')
        value['version'] = '1.0.1'
        legacy = _current(value, Path('/isolated-program'), 'windows-x86_64')
        self.assertNotIn('display_version', legacy)
        self.assertEqual('1.0.1', display_version_label(legacy))

    def test_legacy_integrated_journal_recovers_signed_program_and_original_data(self):
        from desktop_updates.bootstrap import BootstrapError
        from desktop_updates.data_guard import atomic_json, logical_digest, snapshot
        from desktop_updates.integrated_install import JOURNAL_NAME, _fixed_manifest, recover_integrated_install
        from desktop_updates.payload import extract_verified_payload

        fixture = manager_fixture.UpdateManagerTests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        manager = fixture.manager
        helper = fixture.folder / 'installed-updater'
        helper.mkdir()
        updater = helper / 'LJQCAppUpdater.exe'
        updater.write_bytes(b'inert existing updater fixture')
        old_state = manager.get_state()
        old_state['updater_entrypoint'] = str(updater)
        atomic_json(manager.state_path, old_state)
        fixture.manifest.pop('display_version', None)
        raw = fixture.signed()
        manifest = _fixed_manifest(raw, manager.trust, manager.target)
        program = manager.directory / 'versions' / (str(manifest['build_number']) + '-' + manifest['source_digest'][:12])
        extract_verified_payload(fixture.archive, program, manifest)
        identifier = 'c' * 32
        run = manager.directory / 'integrated' / identifier
        run.mkdir(parents=True)
        (run / 'manifest.json').write_bytes(raw)
        receipt = snapshot(fixture.root, run / 'backup')
        candidate = fixture.fake_preflight(program, manifest, run, run / 'backup', receipt)
        with closing(sqlite3.connect(candidate)) as connection:
            connection.execute('CREATE TABLE recovered_feature(id INTEGER PRIMARY KEY)')
            connection.commit()
        # This is the exact original state shape, without an optional label.
        current = dict(path=str(program), entrypoint=manifest['payload']['entrypoint'],
            version=manifest['version'], build_number=manifest['build_number'], target=manager.target,
            source_digest=manifest['source_digest'], source_commit=manifest['source_commit'])
        new_state = {**old_state, 'current': current, 'previous': old_state['current']}
        journal = dict(format='ljqc-integrated-installation', format_version=1, id=identifier,
            target=manager.target, phase='prepared', old_state=old_state, new_state=new_state,
            program_changed=True, replace_helper=False, helper_destination=str(helper),
            manifest_sha256=hashlib.sha256(raw).hexdigest(), receipt=receipt,
            expected_data_digest=logical_digest(candidate), installer_build_number=manifest['build_number'],
            old_program_entry=str(manager._entry(old_state['base'])), backup=str(run / 'backup'))
        journal_path = manager.directory / JOURNAL_NAME
        for key, invalid in (('version', '9.9.9'), ('source_digest', 'e' * 64)):
            with self.subTest(tampered_identity=key):
                original = current[key]
                current[key] = invalid
                atomic_json(journal_path, journal)
                with self.assertRaises(BootstrapError):
                    recover_integrated_install(manager)
                self.assertEqual(fixture.original, logical_digest(fixture.db))
                current[key] = original
        atomic_json(journal_path, journal)
        with patch('desktop_updates.integrated_install.install_windows_shortcuts', return_value={}):
            result = recover_integrated_install(manager)
        self.assertEqual('installed', result['status'])
        self.assertEqual(new_state, manager.get_state())
        self.assertNotIn('display_version', manager.get_state()['current'])
        self.assertEqual('1.0.1', display_version_label(manager.get_state()['current']))
        self.assertFalse(journal_path.exists())
        with closing(sqlite3.connect(fixture.db)) as connection:
            self.assertEqual('customer project', connection.execute('SELECT name FROM projects').fetchone()[0])
            self.assertEqual(b'%PDF original frozen report', connection.execute('SELECT pdf FROM report_exports').fetchone()[0])
            self.assertIsNotNone(connection.execute("SELECT name FROM sqlite_master WHERE name='recovered_feature'").fetchone())
        self.assertEqual(fixture.attachment, (fixture.db.parent / 'qc_lj_app_files/one/report.txt').read_bytes())

    def test_downloads_use_each_record_label_and_only_advertise_required_activation(self):
        fixture = download_fixture.SoftwareDownloadsTest(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        new = download_fixture.published(version='1.1.1', display_version='1.01.1', activation_required=True)
        old = download_fixture.published(version='1.0.1',
            url='https://downloads.example.org/old-macos.dmg')
        catalog = fixture.catalog({'windows-x86_64': new, 'macos-arm64': old})
        self.assertEqual(('1.1.1', '1.01.1', True), (catalog.downloads[0].release.version,
            catalog.downloads[0].release.display_version, catalog.downloads[0].release.activation_required))
        self.assertEqual(('1.0.1', False), (catalog.downloads[1].release.display_version,
                                           catalog.downloads[1].release.activation_required))
        app = fixture.app(catalog)
        markdown = [item.value for item in app.markdown]
        self.assertIn('**1.01.1 一体安装包**', markdown)
        self.assertIn('**1.0.1 一体安装包**', markdown)
        self.assertEqual(1, sum('首次打开软件时，输入收到的验证码并联网激活。' in value for value in markdown))
        self.assertFalse(any('验证码管理' in value or '管理员登录' in value for value in markdown))

    def test_invalid_display_or_activation_flags_only_disable_that_download(self):
        fixture = download_fixture.SoftwareDownloadsTest(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        for fields in (dict(display_version='<b>1.01.1</b>'), dict(display_version=123),
                       dict(activation_required='true'), dict(activation_required=1)):
            with self.subTest(fields=fields):
                catalog = fixture.catalog({'windows-x86_64': download_fixture.published(**fields),
                    'macos-arm64': download_fixture.published(url='https://downloads.example.org/mac.dmg')})
                self.assertIsNone(catalog.downloads[0].release)
                self.assertIsNotNone(catalog.downloads[1].release)


if __name__ == '__main__':
    unittest.main(verbosity=2)
