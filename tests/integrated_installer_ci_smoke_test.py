"""Inert fixtures exercise the new native CI gate without running installers."""
from __future__ import annotations

import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'packaging')]

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from build_update_patch import export_patch
from ci_verify_integrated_installer import accepted_source_commit, read_json, validate_inputs, verify
from desktop_updates.protocol import BASE_RELEASE, canonical_json
from release_payload import digest_file


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


if __name__ == '__main__':
    unittest.main(verbosity=2)
