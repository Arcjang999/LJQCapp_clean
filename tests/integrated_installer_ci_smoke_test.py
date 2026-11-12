"""Inert fixtures exercise the new native CI gate without running installers."""
from __future__ import annotations

import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'packaging')]

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from build_update_patch import export_patch
import ci_verify_integrated_installer as verifier
from ci_verify_integrated_installer import accepted_source_commit, read_json, validate_inputs, verify
from desktop_updates.data_guard import atomic_json
from desktop_updates import helper_repair
from desktop_updates.protocol import BASE_RELEASE, canonical_json
from release_payload import digest_file
from tests import desktop_update_helper_repair_smoke_test as helper_fixtures


class IntegratedInstallerCITests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix='ljqc-inert-installer-ci-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.target = 'windows-x86_64'
        self.source = dict(version='1.1.1', display_version='1.01.1', build_number=2026111201,
            app_version='1.1.1', data_format_version=1, git_commit='c' * 40, source_digest='d' * 64,
            source_dirty=False, target=self.target)
        program = self.root / 'inert-program'
        program.mkdir()
        (program / 'LJQCApp.exe').write_bytes(b'NOT executable; inert source acceptance fixture')
        candidate_dir = self.root / 'candidate'
        self.manifest = export_patch(program, self.source, candidate_dir)
        self.patch_path = candidate_dir / self.manifest['artifact']['url'].rsplit('/', 1)[-1]
        self.key = Ed25519PrivateKey.generate()
        public = base64.b64encode(self.key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode('ascii')
        self.trust = dict(format_version=1, product='LJQCApp', channel='dev', base_release=BASE_RELEASE,
                          origin='https://159.75.243.120', keys={'qa': public})
        self.manifest_path = self.root / 'signed-program.json'
        self.write(self.manifest_path, dict(signed=self.manifest, key_id='qa',
            signature=base64.b64encode(self.key.sign(canonical_json(self.manifest))).decode('ascii')))
        self.installer = self.root / 'LJQCApp-1.01.1-2026111201-windows-x64-Setup.exe'
        self.installer.write_bytes(b'NOT an installer; never executed by these tests')
        self.receipt_path = self.root / 'integrated-build-receipt.json'
        self.receipt = dict(status='built_not_published', target=self.target,
            installer_release=self.source, program_release=dict(version='1.1.1', build_number=2026111201,
                source_commit=self.source['git_commit'], source_digest=self.source['source_digest'],
                sha256=digest_file(self.patch_path)),
            artifact=dict(file=self.installer.name, sha256=digest_file(self.installer),
                          size_bytes=self.installer.stat().st_size), bundle_sha256='e' * 64,
            updater_integrated=True, program_archive_rebuilt=False, helper_archive_rebuilt=False,
            business_storage_opened=False)
        self.write(self.receipt_path, self.receipt)
        self.download_path = self.root / 'download-record.json'
        self.download = dict(published=True, target=self.target, version='1.1.1', display_version='1.01.1',
            build_number=2026111201, program_build_number=2026111201,
            sha256=self.receipt['artifact']['sha256'], size_bytes=self.receipt['artifact']['size_bytes'],
            updater_integrated=True, activation_required=True)
        self.write(self.download_path, self.download)

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def validate(self):
        return validate_inputs(self.installer, self.receipt_path, self.manifest_path, self.patch_path,
                               self.target, self.source, self.trust)

    def test_matching_real_signature_archive_and_installer_bytes_are_accepted_read_only(self):
        files = {path: path.read_bytes() for path in (self.installer, self.receipt_path,
            self.manifest_path, self.patch_path, self.download_path)}
        receipt, manifest, raw = self.validate()
        self.assertEqual(self.receipt['artifact'], receipt['artifact'])
        self.assertEqual(('1.1.1', '1.01.1', 2026111201),
                         (manifest['version'], manifest['display_version'], manifest['build_number']))
        self.assertEqual(self.manifest_path.read_bytes(), raw)
        self.assertIn('_metadata_digest', manifest)
        self.assertEqual(files, {path: path.read_bytes() for path in files})

    def test_changed_setup_or_program_archive_is_rejected(self):
        for path in (self.installer, self.patch_path):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b'changed')
                    with self.assertRaises((ValueError, RuntimeError)):
                        self.validate()
                finally:
                    path.write_bytes(original)

    def test_wrong_source_public_label_or_rebuilt_helper_cannot_be_accepted(self):
        for field, value in (('source_digest', 'f' * 64), ('display_version', '1.01'),
                             ('version', '1.01.1'), ('data_format_version', True)):
            with self.subTest(field=field):
                receipt = deepcopy(self.receipt)
                receipt['installer_release'][field] = value
                self.write(self.receipt_path, receipt)
                with self.assertRaises((ValueError, RuntimeError)):
                    self.validate()
        for flag in ('program_archive_rebuilt', 'helper_archive_rebuilt', 'business_storage_opened'):
            receipt = deepcopy(self.receipt)
            receipt[flag] = True
            self.write(self.receipt_path, receipt)
            with self.assertRaises((ValueError, RuntimeError)):
                self.validate()

    def test_untrusted_signed_program_and_unlicensed_download_record_are_rejected(self):
        original = self.manifest_path.read_bytes()
        value = read_json(self.manifest_path)
        value['signed']['display_version'] = '1.01.2'
        self.write(self.manifest_path, value)
        with self.assertRaises((ValueError, RuntimeError)):
            self.validate()
        self.manifest_path.write_bytes(original)
        download = dict(self.download, activation_required=False)
        self.write(self.download_path, download)
        with self.assertRaises((ValueError, RuntimeError)):
            self.validate()

    def test_native_acceptance_refuses_this_computer_before_reading_inputs_or_running_commands(self):
        missing = self.root / 'missing'
        output = self.root / 'must-not-be-created'
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}), \
                patch('ci_verify_integrated_installer.subprocess.run', side_effect=AssertionError('native command')):
            with self.assertRaisesRegex(ValueError, 'ephemeral GitHub-hosted'):
                verify(self.target, missing, missing, missing, missing, output)
        self.assertFalse(output.exists())

    def test_selected_accepted_source_can_differ_from_dispatch_ref_but_must_be_full_commit(self):
        source, dispatch = 'a' * 40, 'b' * 40
        self.assertEqual(source, accepted_source_commit({'SOURCE_COMMIT': source, 'GITHUB_SHA': dispatch}))
        self.assertEqual(dispatch, accepted_source_commit({'GITHUB_SHA': dispatch}))
        for invalid in ('main', 'a' * 7, 'a' * 40 + '\n', None):
            with self.subTest(invalid=invalid):
                if invalid is None:
                    with self.assertRaises(ValueError):
                        accepted_source_commit({})
                else:
                    with self.assertRaises(ValueError):
                        accepted_source_commit({'SOURCE_COMMIT': invalid, 'GITHUB_SHA': dispatch})

    def test_pretty_printed_installed_metadata_keeps_original_signature_and_digest(self):
        _, manifest, raw = self.validate()
        metadata = self.root / 'installed-metadata.json'
        atomic_json(metadata, json.loads(raw))
        self.assertNotEqual(metadata.read_bytes(), raw)
        accepted = verifier.verify_installed_metadata(metadata, self.target, manifest, raw, self.trust)
        self.assertEqual(accepted['_metadata_digest'], manifest['_metadata_digest'])

    def test_installed_metadata_rejects_rebound_envelope_signature_type_or_digest(self):
        _, manifest, raw = self.validate()
        metadata = self.root / 'installed-metadata.json'
        for mutation in ('key-alias', 'signature', 'signed-value', 'digest'):
            with self.subTest(mutation=mutation):
                envelope = json.loads(raw)
                trust, selected = deepcopy(self.trust), dict(manifest)
                if mutation == 'key-alias':
                    trust['keys']['alias'] = trust['keys']['qa']
                    envelope['key_id'] = 'alias'  # Still a valid signature, but another envelope.
                elif mutation == 'signature':
                    envelope['signature'] = base64.b64encode(b'\0' * 64).decode('ascii')
                elif mutation == 'signed-value':
                    envelope['signed']['build_number'] = True
                    envelope['signature'] = base64.b64encode(self.key.sign(canonical_json(envelope['signed']))).decode('ascii')
                else:
                    selected['_metadata_digest'] = 'f' * 64
                atomic_json(metadata, envelope)
                with self.assertRaises((ValueError, RuntimeError)):
                    verifier.verify_installed_metadata(metadata, self.target, selected, raw, trust)

    def test_source_root_and_imports_cannot_be_rebound_to_the_harness(self):
        environment = dict(GITHUB_ACTIONS='true', GITHUB_WORKSPACE=str(ROOT))
        self.assertEqual(verifier.accepted_source_root(ROOT, environment=environment), ROOT)
        for wrong in (self.root, Path('relative-source')):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                verifier.accepted_source_root(wrong, environment=environment)
        foreign = self.root / 'ci_verify_license.py'
        foreign.write_text('# inert untrusted fallback\n')
        with self.assertRaises(ValueError):
            verifier.assert_accepted_module(SimpleNamespace(__name__='ci_verify_license', __file__=str(foreign)))

    def test_external_harness_imports_only_the_explicit_accepted_source(self):
        harness = self.root / 'harness' / 'packaging'
        harness.mkdir(parents=True)
        script = harness / 'ci_verify_integrated_installer.py'
        shutil.copyfile(ROOT / 'packaging/ci_verify_integrated_installer.py', script)
        (harness / 'ci_verify_update_adoption.py').write_text("raise AssertionError('harness import fallback')\n")
        probe = '''
import atexit, pathlib, runpy, sys
script, source = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]).resolve()
sys.path.insert(0, str(script.parent))
def check():
    for name in ('ci_verify_update_adoption', 'protected_runtime', 'desktop_updates.protocol', 'services.release_info'):
        assert pathlib.Path(sys.modules[name].__file__).resolve().is_relative_to(source)
    print('ACCEPTED_SOURCE_IMPORTS_CONFIRMED')
atexit.register(check)
sys.argv = [str(script), '--source-root', str(source), '--help']
runpy.run_path(str(script), run_name='__main__')
'''
        result = subprocess.run([sys.executable, '-c', probe, str(script), str(ROOT)],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ACCEPTED_SOURCE_IMPORTS_CONFIRMED', result.stdout)
        self.assertNotIn('harness import fallback', result.stderr)

    def test_harness_receipt_commit_must_match_the_workflow_checkout(self):
        expected = 'a' * 40
        def git(arguments, **kwargs):
            return SimpleNamespace(stdout=expected if arguments[-1] == 'HEAD' else str(verifier.HARNESS_ROOT))
        with patch.dict(os.environ, GITHUB_SHA=expected), patch.object(verifier.subprocess, 'run', side_effect=git):
            self.assertEqual(verifier.harness_commit(), expected)
        with patch.dict(os.environ, GITHUB_SHA='b' * 40), patch.object(verifier.subprocess, 'run', side_effect=git):
            with self.assertRaises(ValueError):
                verifier.harness_commit()

    def test_cleanup_identity_requires_original_transaction_and_native_helper_inventory(self):
        helper = self.root / 'installed-updater'
        entry = helper_fixtures.HelperRepairTests.helper(self, helper, self.target, 2026111201, version='1.1.1')
        info = helper / '_internal/build_info.json'
        value = json.loads(info.read_text())
        value.update(git_commit=self.source['git_commit'], source_digest=self.source['source_digest'])
        self.write(info, value)
        helper_repair.write_helper_manifest(helper, self.target)
        accepted_helper_raw = (helper / 'update_helper_manifest.json').read_bytes()
        data = self.root / 'installed-data'
        transaction = data / 'updates/integrated/fixture'
        transaction.mkdir(parents=True)
        raw = self.manifest_path.read_bytes()
        (transaction / 'manifest.json').write_bytes(raw)
        (transaction / 'helper-manifest.json').write_bytes(accepted_helper_raw)
        self.write(transaction / 'installation-receipt.json', dict(status='installed', automatic_updates_enabled=True,
            program_build_number=2026111201, installer_build_number=2026111201))
        manager = SimpleNamespace(get_state=lambda: dict(updater_entrypoint=str(entry.resolve())))
        with patch.object(verifier, 'UpdateManager', return_value=manager):
            verified, inventory = verifier.verified_installed_helper(data, self.target, self.source, raw, accepted_helper_raw)
            self.assertEqual(verified, helper.resolve())
            self.assertEqual(inventory.read_bytes(), accepted_helper_raw)
            (transaction / 'manifest.json').write_bytes(raw + b' ')
            with self.assertRaises(ValueError):
                verifier.verified_installed_helper(data, self.target, self.source, raw, accepted_helper_raw)
            (transaction / 'manifest.json').write_bytes(raw)
            (transaction / 'helper-manifest.json').write_bytes(accepted_helper_raw + b' ')
            with self.assertRaises(ValueError):
                verifier.verified_installed_helper(data, self.target, self.source, raw, accepted_helper_raw)
            (transaction / 'helper-manifest.json').write_bytes(accepted_helper_raw)
            entry.write_bytes(b'tampered updater; never run')
            with self.assertRaises(RuntimeError):
                verifier.verified_installed_helper(data, self.target, self.source, raw, accepted_helper_raw)


if __name__ == '__main__':
    unittest.main(verbosity=2)
