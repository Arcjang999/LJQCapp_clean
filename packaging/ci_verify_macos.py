"""Verify a macOS CI build without GUI sessions or customer databases.

The exported files are the DMG and small release receipts, never the installed
application, the source snapshot, or the isolated runtime directory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'packaging')]
from release_payload import assert_protected_payload, digest_file, validate_payload
from services.release_info import get_release_info
from services.runtime_lock import ServiceLock


IDENTITY_FIELDS = ('version', 'build_number', 'data_format_version', 'git_commit',
                   'source_digest', 'source_dirty', 'target')


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object: ' + str(path))
    return value


def check_identity(actual: dict, expected: dict) -> None:
    if actual.get('target') != 'macos-arm64':
        raise ValueError('The artifact is not a macos-arm64 build')
    if not isinstance(actual.get('source_dirty'), bool):
        raise ValueError('Source cleanliness is not recorded')
    if (not isinstance(actual.get('build_number'), int)
            or isinstance(actual['build_number'], bool) or actual['build_number'] <= 0):
        raise ValueError('The build number is invalid')
    for field, pattern in (('git_commit', r'[0-9a-f]{40}'), ('source_digest', r'[0-9a-f]{64}')):
        if re.fullmatch(pattern, str(actual.get(field, ''))) is None:
            raise ValueError('Invalid release identity: ' + field)
    for field in IDENTITY_FIELDS:
        if actual.get(field) != expected.get(field):
            raise ValueError('Release identity differs from the checked-out source: ' + field)


def expected_identity(override: Path | None) -> tuple[dict, str]:
    in_actions = os.environ.get('GITHUB_ACTIONS', '').lower() == 'true'
    if override is not None:
        if in_actions:
            raise ValueError('Metadata overrides are not permitted in GitHub Actions')
        return read_object(override), 'explicit-local-validation-override'
    expected = get_release_info(ROOT, include_source_state=True).as_dict()
    expected['target'] = 'macos-arm64'
    if in_actions:
        commit = os.environ.get('GITHUB_SHA', '')
        if re.fullmatch(r'[0-9a-f]{40}', commit) is None or expected['git_commit'] != commit:
            raise ValueError('The checkout does not match GITHUB_SHA')
        if expected['source_dirty']:
            raise ValueError('CI must build a clean checkout')
    return expected, 'checked-out-source'


def find_stage(build_root: Path) -> tuple[Path, dict]:
    candidates = sorted(build_root.resolve().glob('*/artifact.json'))
    if len(candidates) != 1:
        raise ValueError('Expected exactly one build stage containing artifact.json')
    stage = candidates[0].parent.resolve()
    info = read_object(candidates[0])
    artifact = Path(str(info.get('artifact', '')))
    if not artifact.is_absolute() or not artifact.is_file() or artifact.is_symlink():
        raise ValueError('The final DMG is not an ordinary absolute file')
    if not artifact.resolve().is_relative_to(stage) or artifact.suffix.lower() != '.dmg':
        raise ValueError('The final DMG must be inside its build stage')
    return stage, info


def check_bundle_manifest(app: Path, manifest: dict) -> int:
    assert_protected_payload(app)
    if manifest.get('format') != 'ljqc-program-payload' or manifest.get('mutable_data_included') is not False:
        raise ValueError('The program payload manifest is not recognized')
    expected = {}
    for entry in manifest.get('files', []):
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
            raise ValueError('Invalid payload manifest record')
        if entry['path'].startswith('LJQCApp.app/'):
            record = dict(entry)
            record['path'] = record['path'][len('LJQCApp.app/'):]
            if record['path'] in expected:
                raise ValueError('Duplicate application manifest record')
            expected[record['path']] = record
    if not expected:
        raise ValueError('The manifest does not contain the application')
    actual = {entry['path']: entry for entry in validate_payload(app)}
    if actual != expected:
        raise ValueError('Installed application files differ from the release payload manifest')
    return len(actual)


def verify(build_root: Path, export_dir: Path, *, metadata_override: Path | None = None) -> dict:
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        raise ValueError('macOS arm64 verification requires an Apple Silicon Mac')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    logs = ROOT / 'output/ci-verification/macos' / run_id
    logs.mkdir(parents=True, exist_ok=False)
    qa = Path(tempfile.mkdtemp(prefix='ljqcapp-ci-macos-', dir='/private/tmp'))
    mount = qa / 'mounted'
    mount.mkdir()
    receipt = {'status': 'running', 'platform': 'macos-arm64',
        'checked_at': datetime.now(timezone.utc).isoformat(), 'qa_root': str(qa),
        'logs': str(logs), 'production_database_access': False, 'gui_tested': False,
        'business_initialization_tested': False, 'notarization_tested': False,
        'verification_script_sha256': digest_file(Path(__file__).resolve()), 'checks': []}
    receipt_path = logs / 'verification.json'
    process = None
    request = None
    attached = False

    def save() -> None:
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def command(arguments: list[str], name: str, *, env: dict | None = None, timeout: int = 90) -> subprocess.CompletedProcess:
        started = time.monotonic()
        log_path = logs / (name + '.log')
        try:
            result = subprocess.run(arguments, capture_output=True, text=True, env=env, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            def decoded(value) -> str:
                return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else (value or '')
            log_path.write_text(decoded(error.stdout) + '\n' + decoded(error.stderr) + '\nCommand timed out.\n', encoding='utf-8')
            receipt['checks'].append({'name': name, 'command': arguments, 'exit': None,
                'seconds': round(time.monotonic() - started, 2), 'log': log_path.name, 'timed_out': True})
            save()
            raise
        log_path.write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
        receipt['checks'].append({'name': name, 'command': arguments, 'exit': result.returncode,
            'seconds': round(time.monotonic() - started, 2), 'log': log_path.name})
        save()
        if result.returncode:
            raise RuntimeError(name + ' failed; see ' + str(log_path))
        return result

    def stop_service() -> None:
        if process is None or process.poll() is not None:
            return
        assert request is not None
        request.write_text('stop\n', encoding='ascii')
        process.wait(timeout=20)

    try:
        save()
        expected, expectation_mode = expected_identity(metadata_override)
        receipt['expectation_mode'] = expectation_mode
        stage, info = find_stage(build_root)
        check_identity(info, expected)
        metadata_path = stage / 'build_info.json'
        metadata = read_object(metadata_path)
        check_identity(metadata, expected)
        if any(info.get(key) != value for key, value in metadata.items()):
            raise ValueError('The stage metadata differs from the artifact receipt')
        artifact = Path(info['artifact'])
        if artifact.stat().st_size != info.get('size_bytes') or digest_file(artifact) != info.get('sha256'):
            raise ValueError('The DMG size or SHA-256 is incorrect')
        receipt.update(release=metadata, artifact_name=artifact.name, size_bytes=artifact.stat().st_size,
                       sha256=info['sha256'], source_identity_verified=True)
        command(['hdiutil', 'verify', str(artifact)], 'dmg-verify')
        command(['hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', str(mount), str(artifact)], 'dmg-attach')
        attached = True
        app = qa / 'Applications/LJQCApp.app'
        app.parent.mkdir()
        command(['ditto', str(mount / 'LJQCApp.app'), str(app)], 'install-copy')
        command(['codesign', '--verify', '--deep', '--strict', str(app)], 'signature-verify')
        signature = command(['codesign', '--display', '--verbose=4', str(app)], 'signature-kind')
        if 'Signature=adhoc' not in signature.stdout + signature.stderr:
            raise ValueError('This pending-signing build did not record the expected ad-hoc signature')
        for name, relative in (('desktop', 'Contents/MacOS/LJQCApp'), ('service', 'Contents/Resources/app/LJQCAppService')):
            result = command(['lipo', '-archs', str(app / relative)], 'architecture-' + name)
            if result.stdout.strip() != 'arm64':
                raise ValueError('The ' + name + ' executable is not arm64-only')
        manifest_path = stage / 'release_payload_manifest.json'
        manifest = read_object(manifest_path)
        check_identity(manifest.get('release', {}), expected)
        receipt['installed_payload_files_verified'] = check_bundle_manifest(app, manifest)
        bundled_paths = ('Contents/Resources/build_info.json',
                         'Contents/Resources/app/_internal/build_info.json')
        for relative in bundled_paths:
            bundled = read_object(app / relative)
            if bundled != metadata:
                raise ValueError('A bundled build_info.json differs from the stage metadata')
        command(['hdiutil', 'detach', str(mount)], 'dmg-detach')
        attached = False

        data = qa / 'runtime-data'
        runtime = data / 'runtime'
        runtime.mkdir(parents=True, mode=0o700)
        env = {**os.environ, 'LJQC_DATA_ROOT': str(data), 'LJQC_DEPLOYMENT_MODE': 'desktop',
               'LJQC_SHUTDOWN_FILE': ''}
        version = command([str(app / 'Contents/MacOS/LJQCApp'), '--version'], 'frozen-version', env=env, timeout=30)
        if not all(str(metadata[field]) in version.stdout for field in ('version', 'build_time', 'git_commit')):
            raise ValueError('The frozen version command does not match the captured metadata')
        if list(data.rglob('*.db')):
            raise ValueError('The version command unexpectedly created a database')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        request = runtime / ('shutdown-' + uuid.uuid4().hex + '.request')
        service_env = {**env, 'LJQC_SHUTDOWN_FILE': str(request)}
        service = app / 'Contents/Resources/app/LJQCAppService'
        with (logs / 'frozen-service.log').open('w', encoding='utf-8') as service_log:
            process = subprocess.Popen([str(service), '--port', str(port), '--address', '127.0.0.1'],
                cwd=service.parent, env=service_env, stdout=service_log, stderr=subprocess.STDOUT,
                start_new_session=True)
        deadline = time.monotonic() + 45
        healthy = False
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('The frozen service exited before becoming healthy')
            try:
                with urlopen('http://127.0.0.1:' + str(port) + '/_stcore/health', timeout=2) as response:
                    healthy = response.status == 200
            except (OSError, URLError):
                pass
            if healthy:
                break
            time.sleep(0.2)
        if not healthy:
            raise RuntimeError('The frozen service did not become healthy')
        receipt['frozen_service_healthy'] = True
        stop_service()
        if process.returncode != 0:
            raise RuntimeError('The frozen service did not exit cleanly')
        service_log_text = (logs / 'frozen-service.log').read_text(encoding='utf-8', errors='replace')
        if 'Traceback' in service_log_text or 'Unhandled Streamlit exception' in service_log_text:
            raise RuntimeError('The frozen service reported an unhandled exception')
        with socket.socket() as probe:
            probe.settimeout(2)
            if probe.connect_ex(('127.0.0.1', port)) == 0:
                raise RuntimeError('The frozen service port is still open after exit')
        lock = ServiceLock(runtime)
        try:
            lock.acquire()
        finally:
            lock.close()
        if list(runtime.glob('shutdown-*.request')):
            raise RuntimeError('A graceful shutdown request was not cleaned up')
        # A health endpoint does not start an app session. Do not misreport this
        # as onboarding, empty-business-schema or report generation acceptance.
        if list(data.rglob('*.db')):
            raise ValueError('A headless health check unexpectedly initialized a business database')
        environment = command([sys.executable, '-m', 'pip', 'freeze'], 'build-environment', timeout=30)
        receipt.update(status='passed', service_exit_code=process.returncode,
            service_port_closed=True, service_lock_released=True, shutdown_requests_cleaned=True,
            no_business_database_created=True, signing_status='ad-hoc-pending-developer-id-and-notarization',
            verification_scope='DMG installation, payload integrity, metadata, CLI and headless service lifecycle')
        save()

        export_dir = export_dir.resolve()
        export_dir.mkdir(parents=True, exist_ok=False)
        for path in (artifact, metadata_path, manifest_path):
            destination = export_dir / path.name
            shutil.copyfile(path, destination)
            if digest_file(destination) != digest_file(path):
                raise ValueError('An exported artifact changed during copy')
        (export_dir / 'macos-build-environment.txt').write_text(environment.stdout, encoding='utf-8')
        exported_logs = export_dir / 'verification-logs'
        exported_logs.mkdir()
        for path in sorted(logs.glob('*.log')):
            shutil.copyfile(path, exported_logs / path.name)
        # Publish the passing receipt only after every selected export exists.
        shutil.copyfile(receipt_path, export_dir / 'verification.json')
        return receipt
    except BaseException as error:
        receipt.update(status='failed', error=type(error).__name__ + ': ' + str(error))
        save()
        raise
    finally:
        if process is not None and process.poll() is None:
            try:
                stop_service()
            except (OSError, subprocess.TimeoutExpired) as error:
                receipt.update(status='failed', service_cleanup_error=str(error), service_pid=process.pid)
                save()
        if attached or os.path.ismount(mount):
            try:
                command(['hdiutil', 'detach', str(mount)], 'dmg-detach-after-failure')
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                receipt.update(status='failed', volume_cleanup_error=str(error))
                save()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-root', type=Path, required=True)
    parser.add_argument('--export-dir', type=Path, required=True)
    parser.add_argument('--expected-metadata', type=Path,
        help='Local-only verification of a preserved older build; forbidden in GitHub Actions')
    args = parser.parse_args()
    result = verify(args.build_root, args.export_dir, metadata_override=args.expected_metadata)
    print(json.dumps({'status': result['status'], 'export_dir': str(args.export_dir.resolve()),
        'version': result['release']['version'], 'build_number': result['release']['build_number'],
        'source_digest': result['release']['source_digest']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
