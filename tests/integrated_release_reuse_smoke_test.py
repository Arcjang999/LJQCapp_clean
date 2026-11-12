"""New integrated releases must reuse accepted bytes and preserve strict identity."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'packaging')]
import build_integrated_installer as builder
from ci_verify_license import _activation, _business_page, _page, verify_frozen_license
from desktop_updates import helper_repair
from desktop_updates.bootstrap import BootstrapError
from tests import desktop_update_helper_repair_smoke_test as repair_fixtures


class IntegratedReuseTests(unittest.TestCase):
    def fixture(self, root):
        package = root / 'LJQCAppUpdater'
        # Existing isolated fixtures build the same real helper inventory checks.
        repair_fixtures.HelperRepairTests.helper(self, package, 'windows-x86_64', 2026111201, version='1.1.1')
        inventory = helper_repair.write_helper_manifest(package, 'windows-x86_64')
        manifest = package / 'update_helper_manifest.json'
        release = json.loads((package / '_internal/build_info.json').read_text())
        receipt = dict(status='passed', target='windows-x86_64', release=release,
            native_production_tls_probe={'status': 'passed'}, native_ui_lifecycle_probe={'status': 'passed'},
            business_database_access=False,
            helper_integrity_manifest={'sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()})
        (root / 'helper-build-receipt.json').write_text(json.dumps(receipt))
        program = dict(version='1.1.1', build_number=2026111201,
                       source_commit=release['git_commit'], source_digest=release['source_digest'])
        return package, receipt, program

    def test_reused_helper_requires_accepted_native_identity_and_exact_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, receipt, program = self.fixture(root)
            accepted, _, _ = builder._reused_helper(root, 'windows-x86_64', program, 2026111201)
            self.assertEqual(accepted, package.resolve())
            (package / 'LJQCAppUpdater.exe').write_bytes(b'tampered')
            with self.assertRaises(BootstrapError):
                builder._reused_helper(root, 'windows-x86_64', program, 2026111201)

    def test_helper_for_another_source_or_without_native_acceptance_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, receipt, program = self.fixture(root)
            with self.assertRaisesRegex(ValueError, 'differs'):
                builder._reused_helper(root, 'windows-x86_64', {**program, 'source_commit': 'b' * 40}, 2026111201)
            receipt['native_production_tls_probe']['status'] = 'not-run-outside-github'
            (root / 'helper-build-receipt.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'acceptance'):
                builder._reused_helper(root, 'windows-x86_64', program, 2026111201)

    def test_display_version_must_not_replace_canonical_helper_version(self):
        self.assertIsNone(helper_repair._VERSION.fullmatch('1.01.1'))
        self.assertIsNotNone(helper_repair._VERSION.fullmatch('1.1.1'))

    def test_accepted_archive_preserves_native_file_modes_and_internal_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'helper.tar.gz'
            with tarfile.open(archive, 'w:gz') as bundle:
                row = tarfile.TarInfo('LJQCAppUpdater.app/Contents/MacOS/helper')
                mode = 0o444 if os.name == 'nt' else 0o555
                row.mode, row.size = mode, 6
                bundle.addfile(row, io.BytesIO(b'native'))
                if os.name != 'nt':
                    link = tarfile.TarInfo('LJQCAppUpdater.app/Contents/MacOS/current')
                    link.type, link.linkname = tarfile.SYMTYPE, 'helper'
                    bundle.addfile(link)
            output = root / 'output'
            builder.extract_accepted_helper(archive, output)
            executable = output / 'LJQCAppUpdater.app/Contents/MacOS/helper'
            self.assertEqual(executable.stat().st_mode & 0o777, mode)
            if os.name != 'nt':
                self.assertEqual((executable.parent / 'current').resolve(), executable.resolve())
            self.assertEqual(executable.read_bytes(), b'native')

    def test_accepted_archive_rejects_escape_links_and_special_permissions(self):
        for malicious in ('escape', 'special-mode'):
            with self.subTest(malicious=malicious), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = root / 'helper.tar.gz'
                with tarfile.open(archive, 'w:gz') as bundle:
                    row = tarfile.TarInfo('payload')
                    if malicious == 'escape':
                        row.type, row.linkname = tarfile.SYMTYPE, '../../outside'
                    else:
                        row.mode = 0o4755
                    bundle.addfile(row)
                with self.assertRaises((ValueError, tarfile.FilterError)):
                    builder.extract_accepted_helper(archive, root / 'output')

    def test_new_program_cannot_enter_the_legacy_default_or_change_selected_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'program.tar.gz'
            archive.write_bytes(b'accepted exact program bytes')
            signed = root / 'manifest.json'
            signed.write_text(json.dumps({'signed': {'issued_at': '2026-11-12T00:00:00+00:00'}}))
            selected = dict(version='1.1.1', build_number=2026111201, source_commit='a' * 40,
                source_digest='b' * 64, artifact=dict(size_bytes=archive.stat().st_size,
                    sha256=hashlib.sha256(archive.read_bytes()).hexdigest()))
            with patch.object(builder, 'verify_envelope', return_value=selected):
                with self.assertRaisesRegex(ValueError, 'selected accepted program'):
                    builder._verify_program(signed, archive, 'windows-x86_64')
                result, _ = builder._verify_program(signed, archive, 'windows-x86_64',
                    program_version='1.1.1', program_build=2026111201,
                    source_commit='a' * 40, source_digest='b' * 64)
                self.assertEqual(result, selected)
                with self.assertRaisesRegex(ValueError, 'selected native source'):
                    builder._verify_program(signed, archive, 'windows-x86_64',
                        program_version='1.1.1', program_build=2026111201,
                        source_commit='c' * 40, source_digest='b' * 64)

    def test_native_activation_probe_never_runs_on_the_developer_machine(self):
        with patch.dict('os.environ', {'GITHUB_ACTIONS': 'false'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'disposable native GitHub'):
                verify_frozen_license(Path('/absent/native'), 'windows-x86_64', Path('/absent/output'))

    def test_activation_parser_discards_an_early_script_when_app_reruns(self):
        from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
        messages = []
        def add(mutator):
            message = ForwardMsg()
            mutator(message)
            messages.append(message.SerializeToString())
        add(lambda m: setattr(m.new_session, 'script_run_id', 'first'))
        add(lambda m: setattr(m.delta.new_element.heading, 'body', '软件激活'))
        def code(m):
            m.delta.new_element.text_input.label = '验证码'
            m.delta.new_element.text_input.id = 'code'
        add(code)
        def button(m):
            m.delta.new_element.button.label = '验证并进入软件'
            m.delta.new_element.button.id = 'activate'
        add(button)
        add(lambda m: setattr(m, 'script_finished', ForwardMsg.FINISHED_EARLY_FOR_RERUN))
        add(lambda m: setattr(m.new_session, 'script_run_id', 'second'))
        add(lambda m: setattr(m.delta.new_element.heading, 'body', '首次使用'))
        add(lambda m: setattr(m, 'script_finished', ForwardMsg.FINISHED_SUCCESSFULLY))
        class Connection:
            def recv(self, timeout):
                return messages.pop(0)
        page = _page(Connection())
        self.assertFalse(_activation(page))
        self.assertEqual(page['headings'], ['首次使用'])

    def test_business_page_requires_visible_content_and_rejects_rendered_errors(self):
        from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
        page = dict(headings=[], text=[], inputs={}, buttons={}, errors=[])
        self.assertFalse(_business_page(page))
        for severity, allowed in ((1, False), (3, True)):
            with self.subTest(severity=severity):
                heading = ForwardMsg()
                heading.delta.new_element.heading.body = '选择质控方法'
                alert = ForwardMsg()
                alert.delta.new_element.alert.body = '业务初始化失败'
                alert.delta.new_element.alert.format = severity
                finished = ForwardMsg(script_finished=ForwardMsg.FINISHED_SUCCESSFULLY)
                messages = [row.SerializeToString() for row in (heading, alert, finished)]
                class Connection:
                    def recv(self, timeout):
                        return messages.pop(0)
                self.assertEqual(_business_page(_page(Connection())), allowed)


if __name__ == '__main__':
    unittest.main(verbosity=2)
