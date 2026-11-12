"""GitHub-hosted acceptance of an actual integrated Setup/DMG and its license.

Reuse the already signed program archive and native helper. This verifier never
builds binaries, signs artifacts, alters embedded trust, or runs on a user's Mac.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HARNESS_ROOT = Path(__file__).resolve().parents[1]


def accepted_source_root(value=None, *, environment=None) -> Path:
    environment = os.environ if environment is None else environment
    selected = HARNESS_ROOT if value is None else Path(value)
    if not selected.is_absolute() or selected.is_symlink() or not selected.is_dir():
        raise ValueError('The accepted source must be an existing absolute checkout root')
    selected = selected.resolve(strict=True)
    if environment.get('GITHUB_ACTIONS') == 'true':
        workspace = Path(environment.get('GITHUB_WORKSPACE', ''))
        if not workspace.is_absolute() or selected != workspace.resolve(strict=True):
            raise ValueError('The accepted source must be the unchanged GitHub workspace')
    return selected


_bootstrap = argparse.ArgumentParser(add_help=False)
_bootstrap.add_argument('--source-root', type=Path)
_selected = _bootstrap.parse_known_args()[0].source_root if __name__ == '__main__' else None
ROOT = accepted_source_root(_selected)
# The independently checked-out harness is executable, not an import fallback
# for the accepted program, helper or public trust policy.
if HARNESS_ROOT != ROOT:
    sys.path[:] = [entry for entry in sys.path
                   if not Path(entry or Path.cwd()).resolve().is_relative_to(HARNESS_ROOT)]
sys.path[:0] = [str(ROOT), str(ROOT / 'packaging')]

from ci_verify_update_adoption import TemporaryKnownFolders, require_ephemeral_runner, refuse_existing_windows_installation
from desktop_updates.helper_repair import verify_helper_manifest
from desktop_updates.manager import UpdateManager
from desktop_updates.payload import verify_installed_payload
from desktop_updates.protocol import BASE_RELEASE, canonical_json, strict_json, verify_envelope
from protected_runtime import application_modules
from release_payload import PROTECTION_MANIFEST, assert_protected_payload, digest_file
from services.release_info import get_release_info


def assert_accepted_module(module) -> None:
    name, filename = getattr(module, '__name__', ''), getattr(module, '__file__', None)
    packages = {'desktop_updates.helper_repair', 'desktop_updates.manager',
                'desktop_updates.payload', 'desktop_updates.protocol', 'services.release_info'}
    helpers = {'ci_verify_update_adoption', 'protected_runtime', 'release_payload',
               'build_integrated_installer', 'ci_verify_license'}
    expected = (ROOT.joinpath(*name.split('.')).with_suffix('.py') if name in packages else
                ROOT / 'packaging' / (name + '.py') if name in helpers else None)
    if (expected is None or not isinstance(filename, (str, os.PathLike))
            or Path(filename).resolve(strict=True) != expected.resolve(strict=True)):
        raise ValueError('Acceptance imported a helper outside the accepted source')


for _name in ('ci_verify_update_adoption', 'desktop_updates.helper_repair',
              'desktop_updates.manager', 'desktop_updates.payload', 'desktop_updates.protocol',
              'protected_runtime', 'release_payload', 'services.release_info'):
    assert_accepted_module(sys.modules[_name])
if Path(sys.modules['ci_verify_update_adoption'].ROOT).resolve() != ROOT:
    raise ValueError('The native runner guard is not bound to the accepted source')


def read_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError('Acceptance metadata is missing, unbounded or linked')
    value = strict_json(path.read_bytes(), maximum=1024 * 1024)
    if not isinstance(value, dict):
        raise ValueError('Acceptance metadata is not an object')
    return value


def accepted_source_commit(environment=None) -> str:
    environment = os.environ if environment is None else environment
    selected = environment.get('SOURCE_COMMIT') or environment.get('GITHUB_SHA', '')
    if not isinstance(selected, str) or re.fullmatch('[0-9a-f]{40}', selected) is None:
        raise ValueError('The successful native source commit must be explicitly identified')
    return selected


def validate_inputs(installer: Path, receipt_path: Path, manifest_path: Path, patch: Path,
                    target: str, source: dict, trust: dict) -> tuple[dict, dict, bytes]:
    """Read-only identity and byte checks, also exercised by inert fixtures."""
    receipt = read_json(receipt_path)
    if (receipt.get('status') != 'built_not_published' or receipt.get('target') != target
            or receipt.get('updater_integrated') is not True
            or receipt.get('program_archive_rebuilt') is not False
            or receipt.get('helper_archive_rebuilt') is not False
            or receipt.get('business_storage_opened') is not False):
        raise ValueError('The integrated installer did not reuse accepted native artifacts')
    if source.get('source_dirty') is not False or source.get('target') != target:
        raise ValueError('Acceptance requires the clean matching platform source')
    installer_release, program = receipt.get('installer_release', {}), receipt.get('program_release', {})
    for field in ('version', 'display_version', 'build_number', 'data_format_version', 'git_commit', 'source_digest', 'target'):
        if (type(installer_release.get(field)) is not type(source.get(field))
                or installer_release.get(field) != source.get(field)):
            raise ValueError('Installer metadata differs from the current clean release')
    if installer_release.get('source_dirty') is not False:
        raise ValueError('The installer source was not clean')
    read_json(manifest_path)
    raw = manifest_path.read_bytes()
    envelope = strict_json(raw)
    issued = datetime.fromisoformat(envelope['signed']['issued_at'].replace('Z', '+00:00'))
    manifest = verify_envelope(raw, trust, target=target, installed_build=0, highest_sequence=0, now=issued)
    for field, identity in (('version', 'version'), ('display_version', 'display_version'),
                            ('build_number', 'build_number'), ('source_commit', 'git_commit'),
                            ('source_digest', 'source_digest')):
        if manifest.get(field) != source.get(identity):
            raise ValueError('The signed program is from another release or source')
    for field in ('version', 'build_number', 'source_commit', 'source_digest'):
        if program.get(field) != manifest.get(field):
            raise ValueError('The installer receipt selected another program archive')
    artifact = receipt.get('artifact', {})
    if (not isinstance(artifact.get('file'), str) or Path(artifact['file']).name != artifact['file']
            or installer.name != artifact['file'] or installer.is_symlink() or not installer.is_file()
            or installer.stat().st_size != artifact.get('size_bytes')
            or digest_file(installer) != artifact.get('sha256')):
        raise ValueError('The actual Setup/DMG differs from its build receipt')
    if (patch.is_symlink() or not patch.is_file()
            or patch.stat().st_size != manifest['artifact']['size_bytes']
            or digest_file(patch) != manifest['artifact']['sha256']
            or program.get('sha256') != manifest['artifact']['sha256']):
        raise ValueError('The accepted program archive bytes have changed')
    if re.fullmatch('[0-9a-f]{64}', str(receipt.get('bundle_sha256', ''))) is None:
        raise ValueError('The integrated bundle identity is missing')
    download = read_json(receipt_path.parent / 'download-record.json')
    expected = dict(target=target, version=source['version'], display_version=source['display_version'],
        build_number=source['build_number'], program_build_number=source['build_number'],
        sha256=artifact['sha256'], size_bytes=artifact['size_bytes'],
        published=True, updater_integrated=True, activation_required=True)
    if any(type(download.get(field)) is not type(value) or download.get(field) != value
           for field, value in expected.items()):
        raise ValueError('The candidate download record does not describe the accepted licensed installer')
    return receipt, manifest, raw


def verify_installed_metadata(path: Path, target: str, manifest: dict, raw: bytes, trust: dict) -> dict:
    # The update store deliberately pretty-prints JSON. Its full signed
    # envelope must remain canonically identical, including key/signature.
    stored, original = read_json(path), strict_json(raw)
    issued = datetime.fromisoformat(original['signed']['issued_at'].replace('Z', '+00:00'))
    verified = verify_envelope(canonical_json(stored), trust, target=target,
                               installed_build=0, highest_sequence=0, now=issued)
    if (canonical_json(stored) != canonical_json(original)
            or verified['_metadata_digest'] != manifest['_metadata_digest']):
        raise ValueError('The installed signed manifest differs from the accepted input')
    return verified


def verified_installed_helper(data_root: Path, target: str, source: dict, raw: bytes,
                              accepted_helper_raw: bytes) -> tuple[Path, Path]:
    manager = UpdateManager(data_root, target, ROOT / 'resources/desktop_update_trust.json')
    state = manager.get_state()
    transactions = list((data_root / 'updates/integrated').glob('*/installation-receipt.json'))
    if len(transactions) != 1:
        raise ValueError('A fresh installation must produce exactly one committed native receipt')
    transaction = transactions[0].parent
    if (transaction.is_symlink()
            or not transaction.resolve().is_relative_to((data_root / 'updates/integrated').resolve())
            or (transaction / 'manifest.json').is_symlink()
            or (transaction / 'manifest.json').read_bytes() != raw):
        raise ValueError('The native installation selected another embedded program')
    installation = read_json(transactions[0])
    if (installation.get('status') != 'installed' or installation.get('automatic_updates_enabled') is not True
            or installation.get('program_build_number') != source['build_number']
            or installation.get('installer_build_number') != source['build_number']):
        raise ValueError('The native installation was not committed with automatic updates')
    helper_entry = Path(state.get('updater_entrypoint', ''))
    if not helper_entry.is_absolute() or helper_entry.is_symlink():
        raise ValueError('The installed updater has an unexpected entry point')
    if target == 'macos-arm64':
        helper = Path('/Applications/LJQCApp.app')
        expected_entry = helper / 'Contents/MacOS/LJQCAppUpdater'
    else:
        helper, expected_entry = helper_entry.parent, helper_entry.parent / 'LJQCAppUpdater.exe'
    if helper_entry != expected_entry or helper.is_symlink():
        raise ValueError('The installed updater has an unexpected native destination')
    helper_manifest = transaction / 'helper-manifest.json'
    if helper_manifest.is_symlink() or helper_manifest.read_bytes() != accepted_helper_raw:
        raise ValueError('The installed updater inventory differs from its accepted native input')
    identity = verify_helper_manifest(helper, target, manifest_path=helper_manifest)['release']
    if any(identity.get(field) != expected for field, expected in (
            ('version', source['version']), ('build_number', source['build_number']),
            ('source_commit', source['git_commit']), ('source_digest', source['source_digest']))):
        raise ValueError('The installed updater differs from the already accepted native helper')
    return helper, helper_manifest


def installed_program(data_root: Path, target: str, manifest: dict, raw: bytes, source: dict,
                      accepted_helper_raw: bytes) -> tuple[Path, Path, Path, dict]:
    manager = UpdateManager(data_root, target, ROOT / 'resources/desktop_update_trust.json')
    state = manager.get_state()
    current = state.get('current', {})
    for field, expected in (('version', source['version']), ('display_version', source['display_version']),
                            ('build_number', source['build_number']), ('source_commit', source['git_commit']),
                            ('source_digest', source['source_digest']), ('target', target)):
        if current.get(field) != expected:
            raise ValueError('The native installed current state has another release identity')
    if state.get('base') != current or state.get('base_release') != BASE_RELEASE:
        raise ValueError('A fresh integrated installation did not register its own program')
    program = Path(current.get('path', ''))
    if (not program.is_absolute() or program.is_symlink()
            or not program.resolve().is_relative_to((data_root / 'updates/versions').resolve())):
        raise ValueError('The installed program escaped the fresh acceptance root')
    manager._entry(current)
    verify_installed_payload(program, manifest)
    native_count = assert_protected_payload(program)
    declaration = next(program.rglob(PROTECTION_MANIFEST))
    protected = read_json(declaration)
    if {row['module'] for row in protected['modules']} != set(application_modules(ROOT)):
        raise ValueError('The installed native module inventory differs from this source')
    metadata = data_root / 'updates/metadata' / (manifest['_metadata_digest'] + '.json')
    verify_installed_metadata(metadata, target, manifest, raw, manager.trust)
    helper, helper_manifest = verified_installed_helper(data_root, target, source, raw, accepted_helper_raw)
    service = program / ('_internal/app/LJQCAppService.exe' if target == 'windows-x86_64'
                         else 'LJQCApp.app/Contents/Resources/app/LJQCAppService')
    if service.is_symlink() or not service.is_file():
        raise ValueError('The installed frozen service is missing')
    return service, helper, helper_manifest, dict(native_modules=native_count,
        source_protection='passed', installed_payload='passed', signed_program_archive_sha256=manifest['artifact']['sha256'])


def harness_commit() -> str:
    commit = subprocess.run(['git', '-C', str(HARNESS_ROOT), 'rev-parse', 'HEAD'],
                            check=True, capture_output=True, text=True).stdout.strip()
    top = subprocess.run(['git', '-C', str(HARNESS_ROOT), 'rev-parse', '--show-toplevel'],
                         check=True, capture_output=True, text=True).stdout.strip()
    if (Path(top).resolve() != HARNESS_ROOT or commit != os.environ.get('GITHUB_SHA')
            or re.fullmatch('[0-9a-f]{40}', commit) is None):
        raise ValueError('The verification harness differs from its trusted workflow commit')
    return commit


def verify(target: str, installer: Path, receipt_path: Path, manifest_path: Path,
           patch: Path, output: Path, *, helper_export: Path | None = None) -> dict:
    runner_temp = require_ephemeral_runner(target)  # Must precede any install or modification.
    verification_commit = harness_commit()
    code = os.environ.get('LJQC_CI_LICENSE_CODE', '')
    if not code.strip():
        raise ValueError('The independent CI activation credential is not configured')
    if any(path.is_symlink() or not path.is_file() for path in (installer, receipt_path, manifest_path, patch)):
        raise ValueError('Acceptance requires real files, not missing files or aliases')
    source = get_release_info(ROOT, include_source_state=True).as_dict()
    source['target'] = target
    if source.get('git_commit') != accepted_source_commit():
        raise ValueError('The integrated installer checkout differs from its accepted native source commit')
    trust = read_json(ROOT / 'resources/desktop_update_trust.json')
    receipt, manifest, raw = validate_inputs(installer.resolve(), receipt_path.resolve(), manifest_path.resolve(),
                                           patch.resolve(), target, source, trust)
    if helper_export is None:
        raise ValueError('Acceptance requires the unchanged accepted native helper input')
    import build_integrated_installer
    assert_accepted_module(build_integrated_installer)
    _, original_helper_manifest, _ = build_integrated_installer._reused_helper(
        helper_export, target, manifest, source['build_number'])
    accepted_helper_raw = original_helper_manifest.read_bytes()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    qa = Path(tempfile.mkdtemp(prefix='ljqc-integrated-native-', dir=runner_temp)).resolve()
    logs, temporary, data_root = qa / 'logs', qa / 'temp', qa / 'customer-data'
    logs.mkdir()
    temporary.mkdir()
    env = {**os.environ, 'LJQC_DATA_ROOT': str(data_root), 'LJQC_DEPLOYMENT_MODE': 'desktop',
           'MPLCONFIGDIR': str(qa / 'mpl'), 'TMP': str(temporary), 'TEMP': str(temporary),
           'TMPDIR': str(temporary), 'PYTHONUTF8': '1'}
    env.pop('LJQC_CI_LICENSE_CODE', None)
    if target == 'windows-x86_64':
        env.update(LOCALAPPDATA=str(qa / 'profile/LocalAppData'), APPDATA=str(qa / 'profile/AppData'),
                   USERPROFILE=str(qa / 'profile'))
    mount = qa / 'mounted'
    mounted = False
    known_folders = helper = helper_manifest = None
    result = dict(status='failed', target=target, release=source, ephemeral_runner=True,
        harness_commit=verification_commit, accepted_source_commit=source['git_commit'],
        accepted_source_digest=source['source_digest'],
        native_installer_tested=False, native_business_gui_tested=False, production_trust_modified=False,
        production_license_verified=False, production_business_data_access=False,
        program_archive_rebuilt=False, helper_archive_rebuilt=False)

    def run(arguments, name, timeout=180):
        with (logs / (name + '.log')).open('wb') as log:
            return subprocess.run(arguments, cwd=qa, env=env, stdout=log, stderr=subprocess.STDOUT,
                                  check=True, timeout=timeout)

    try:
        if target == 'windows-x86_64':
            refuse_existing_windows_installation()
            known_folders = TemporaryKnownFolders(qa)
            known_folders.begin()
            run([str(installer.resolve()), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/NOICONS',
                 '/LOG=' + str(logs / 'setup-inno.log')], 'native-integrated-setup', timeout=600)
            result['native_installer_tested'] = True
        else:
            if Path('/Applications/LJQCApp.app').exists() or Path('/Applications/LJQCApp.app').is_symlink():
                raise ValueError('The ephemeral runner already has an LJQC application; it was not changed')
            mount.mkdir()
            run(['hdiutil', 'verify', str(installer.resolve())], 'verify-integrated-dmg')
            run(['hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', str(mount), str(installer.resolve())],
                'mount-integrated-dmg')
            mounted = True
            app = qa / 'LJQCAppInstaller.app'
            run(['ditto', str(mount / 'LJQCAppInstaller.app'), str(app)], 'copy-native-installer')
            run(['codesign', '--verify', '--deep', '--strict', str(app)], 'verify-native-installer-signature')
            bundle = app / 'Contents/Resources/integrated-bundle'
            if (digest_file(bundle / 'bundle.json') != receipt['bundle_sha256']
                    or digest_file(bundle / 'program.tar.gz') != manifest['artifact']['sha256']
                    or (bundle / 'manifest.json').read_bytes() != raw):
                raise ValueError('The mounted actual DMG contains a different integrated bundle')
            run([str(app / 'Contents/MacOS/LJQCAppInstaller'), '--install', '--no-gui', '--data-root', str(data_root)],
                'native-integrated-install', timeout=600)
            result['native_installer_tested'] = True
            run(['hdiutil', 'detach', str(mount)], 'unmount-integrated-dmg')
            mounted = False
        helper, helper_manifest = verified_installed_helper(data_root, target, source, raw, accepted_helper_raw)
        service, _, _, installed = installed_program(data_root, target, manifest, raw, source, accepted_helper_raw)
        result.update(installed)
        import ci_verify_license
        assert_accepted_module(ci_verify_license)
        license_result = ci_verify_license.verify_frozen_license(service, target, output / 'frozen-license', env=env, code=code)
        if (license_result.get('status') != 'passed' or any(license_result.get(field) is not True for field in (
                'activation_required_before_use', 'wrong_code_rejected', 'activated', 'restart_without_code'))):
            raise ValueError('The installed frozen service did not complete real activation and restart acceptance')
        # Never export credentials, grant material or a raw activation response.
        result['license'] = {key: license_result[key] for key in (
            'status', 'activation_required_before_use', 'wrong_code_rejected', 'activated', 'restart_without_code')}
        result.update(status='passed', production_license_verified=True,
            installer_sha256=receipt['artifact']['sha256'], installer_size_bytes=receipt['artifact']['size_bytes'])
    except BaseException as error:
        result['error_type'] = type(error).__name__
        raise
    finally:
        cleanup = []
        if mounted:
            try:
                run(['hdiutil', 'detach', str(mount)], 'cleanup-dmg', timeout=120)
            except (OSError, subprocess.SubprocessError):
                cleanup.append('mounted-image')
        if helper is None and target == 'macos-arm64' and Path('/Applications/LJQCApp.app').exists():
            try:
                helper, helper_manifest = verified_installed_helper(data_root, target, source, raw, accepted_helper_raw)
            except (OSError, ValueError, RuntimeError):
                pass  # Retain an unknown application; never delete by existence.
        if helper is not None and target == 'macos-arm64':
            try:
                if helper != Path('/Applications/LJQCApp.app') or helper.is_symlink() or helper_manifest is None:
                    raise ValueError('Unexpected Mac installation cleanup target')
                verify_helper_manifest(helper, target, manifest_path=helper_manifest)
                shutil.rmtree(helper)
            except (OSError, ValueError, RuntimeError):
                cleanup.append('installed-native-helper')
        elif target == 'macos-arm64' and Path('/Applications/LJQCApp.app').exists():
            cleanup.append('unverified-native-helper-retained')
        if known_folders is not None:
            try:
                known_folders.restore()
            except (OSError, ValueError):
                cleanup.append('shortcut-folders')
        result['temporary_installer_cleanup'] = 'passed' if not cleanup else 'failed'
        if cleanup:
            result.update(status='failed', cleanup_errors=cleanup)
        exported = output / 'integrated-verification-logs'
        exported.mkdir(exist_ok=True)
        for path in logs.glob('*.log'):
            raw_log = path.read_bytes()[:65536].replace(code.encode('utf-8'), b'[redacted]')
            raw_log = raw_log.replace(code.strip().encode('utf-8'), b'[redacted]')
            (exported / path.name).write_bytes(raw_log)
        (output / 'integrated-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n',
                                                          encoding='utf-8')
        if result['status'] == 'passed':
            shutil.rmtree(qa)
        if cleanup:
            raise RuntimeError('Integrated native acceptance cleanup did not finish')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', required=True, choices=('windows-x86_64', 'macos-arm64'))
    parser.add_argument('--installer', required=True, type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--patch', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source-root', type=Path, default=ROOT)
    parser.add_argument('--helper-export', required=True, type=Path)
    args = parser.parse_args()
    verify(args.target, args.installer, args.receipt, args.manifest, args.patch, args.output,
           helper_export=args.helper_export)
    print(json.dumps({'status': 'passed', 'target': args.target}))


if __name__ == '__main__':
    main()
