"""Recoverable program switching, outside the running application and its DB."""
from __future__ import annotations

from contextlib import contextmanager, closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from urllib.request import urlopen

from .data_guard import (atomic_json, assert_current, assert_retained, assert_attachments, database_path,
                         digest_file, logical_digest, prepare_candidate, snapshot)
from .protocol import BASE_RELEASE, UPDATER_VERSION, TARGETS, UpdateError, strict_json, verify_envelope, validate_trust
from .transport import fetch_manifest, download_artifact
from .payload import extract_verified_payload, verify_installed_payload


class _FileLock:
    def __init__(self, path: Path):
        self.path, self.handle = path, None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise UpdateError('更新锁位置无效。')
        handle = self.path.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0, 2)
                if not handle.tell():
                    handle.write(b'0')
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise UpdateError('软件或后台服务仍在运行，请保存并正常退出后再更新。') from error
        self.handle = handle
        return self

    def __exit__(self, *args):
        if self.handle:
            self.handle.close()
            self.handle = None


def _environment(root: Path) -> dict:
    env = dict(os.environ)
    env.update(LJQC_DATA_ROOT=str(root), LJQC_DEPLOYMENT_MODE='desktop', PYINSTALLER_RESET_ENVIRONMENT='1', PYTHONUTF8='1')
    for key in ('LJQC_UPDATE_PREFLIGHT', 'LJQC_SHUTDOWN_FILE', 'QT_PLUGIN_PATH', 'QML2_IMPORT_PATH'):
        env.pop(key, None)
    return env


def _spawn(command: list[str], **kwargs):
    # A frozen Windows helper must not force its Qt DLL directory on its child.
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        return subprocess.Popen(command, **kwargs)
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    buffer = ctypes.create_unicode_buffer(32768)
    kernel.GetDllDirectoryW(len(buffer), buffer)
    kernel.SetDllDirectoryW(None)
    try:
        return subprocess.Popen(command, **kwargs)
    finally:
        kernel.SetDllDirectoryW(buffer.value or None)


class UpdateManager:
    def __init__(self, root: Path, target: str, trust_path: Path | None = None):
        if target not in TARGETS or not Path(root).is_absolute():
            raise UpdateError('更新平台或本机资料位置无效。')
        self.root = Path(root)
        self.target = target
        self.directory = self.root / 'updates'
        self.state_path = self.directory / 'state.json'
        self.journal_path = self.directory / 'transaction.json'
        self._reservation = None
        if trust_path is None:
            from .bootstrap import resource_path
            trust_path = resource_path('resources/desktop_update_trust.json')
        self.trust = validate_trust(strict_json(Path(trust_path).read_bytes()))
        for path in (self.root, self.directory):
            if path.is_symlink():
                raise UpdateError('更新资料位置不能是符号链接。')

    @contextmanager
    def maintenance(self):
        with _FileLock(self.root / 'runtime/updater.lock'):
            with _FileLock(self.root / 'runtime/desktop.lock'):
                with _FileLock(self.root / 'runtime/service.lock'):
                    # Recovery may find an external DB between two atomic renames.
                    # This relaxes only existence checking; no replacement DB is created.
                    path = database_path(self.root, require_database=not self.journal_path.exists())
                    if path.exists():
                        try:
                            self._reservation = sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True, timeout=1)
                            self._reservation.execute('BEGIN IMMEDIATE')
                        except sqlite3.Error as error:
                            self._release_reservation()
                            raise UpdateError('资料文件仍有其他写入，请关闭使用它的程序后重试。') from error
                    try:
                        yield
                    finally:
                        self._release_reservation()

    def _release_reservation(self):
        if self._reservation is not None:
            self._reservation.rollback()
            self._reservation.close()
            self._reservation = None

    def get_state(self) -> dict:
        if not self.state_path.exists():
            return {}
        if self.state_path.is_symlink():
            raise UpdateError('更新记录位置无效，请保留现场并重新接入。')
        state = strict_json(self.state_path.read_bytes(), maximum=1024 * 1024)
        if (state.get('format_version') != 1 or state.get('base_release') != BASE_RELEASE
                or state.get('target') != self.target or not isinstance(state.get('current'), dict)):
            raise UpdateError('本机更新记录无法识别，请保留原资料并重新接入。')
        return state

    def register_base(self, payload_root: Path, entrypoint: str, *, target: str,
                      source_digest: str, updater_entrypoint: str | None = None,
                      already_locked: bool = False) -> dict:
        if not already_locked:
            with self.maintenance():
                return self.register_base(payload_root, entrypoint, target=target, source_digest=source_digest,
                                          updater_entrypoint=updater_entrypoint, already_locked=True)
        from .bootstrap import BASE_DIGEST, BASE_COMMIT
        if target != self.target or source_digest != BASE_DIGEST:
            raise UpdateError('接入对象不是指定的 1.00 基础发行。')
        current = {'path': str(Path(payload_root).resolve()), 'entrypoint': entrypoint, 'version': '1.0.0',
                   'build_number': 2026110606, 'target': target, 'source_digest': source_digest,
                   'source_commit': BASE_COMMIT}
        self._entry(current)
        old = self.get_state()
        if old:
            if old['current'] == current:
                return old
            raise UpdateError('此电脑已经接入更新，请从现有更新入口继续。')
        state = {'format_version': 1, 'base_release': BASE_RELEASE, 'target': target,
                 'updater_version': UPDATER_VERSION, 'current': current, 'base': current,
                 'highest_sequence': 0, 'highest_metadata_digest': '',
                 'updater_entrypoint': str(updater_entrypoint or '')}
        atomic_json(self.state_path, state)
        return state

    def _verify(self, raw: bytes, state: dict) -> dict:
        manifest = verify_envelope(raw, self.trust, target=self.target,
            installed_build=state['current']['build_number'], highest_sequence=state.get('highest_sequence', 0))
        if (manifest['sequence'] == state.get('highest_sequence', 0)
                and state.get('highest_metadata_digest') != manifest['_metadata_digest']):
            raise UpdateError('同一发布序号的更新内容已变化，已保留当前程序。')
        current_version = tuple(map(int, state['current']['version'].split('.')))
        if tuple(map(int, manifest['version'].split('.'))) < current_version:
            raise UpdateError('服务器返回较早的功能版本，已保留当前程序。')
        return manifest

    def check(self) -> dict | None:
        if self.journal_path.exists():
            with self.maintenance():
                self._recover()
        raw = fetch_manifest(self.trust, self.target)
        with _FileLock(self.root / 'runtime/updater.lock'):
            state = self.get_state()
            if not state:
                raise UpdateError('请先完成一次性更新接入。')
            manifest = self._verify(raw, state)
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            metadata_path = self.directory / 'metadata' / (manifest['_metadata_digest'] + '.json')
            atomic_json(metadata_path, strict_json(raw))
            state.update(highest_sequence=manifest['sequence'], highest_metadata_digest=manifest['_metadata_digest'])
            atomic_json(self.state_path, state)
            return manifest if manifest['build_number'] > state['current']['build_number'] else None

    def _accepted(self, manifest: dict) -> dict:
        digest = manifest.get('_metadata_digest', '')
        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise UpdateError('请重新检查更新后再下载。')
        path = self.directory / 'metadata' / (digest + '.json')
        if path.is_symlink() or not path.is_file():
            raise UpdateError('更新的签名记录缺失，请重新检查。')
        verified = self._verify(path.read_bytes(), self.get_state())
        if verified['_metadata_digest'] != digest:
            raise UpdateError('更新签名记录不一致。')
        return verified

    def download(self, manifest: dict, progress=None) -> Path:
        verified = self._accepted(manifest)
        artifact = verified['artifact']
        destination = self.directory / 'downloads' / (artifact['sha256'] + '.tar.gz')
        return download_artifact(verified, destination, progress=progress)

    def _entry(self, current: dict) -> Path:
        name = current.get('entrypoint', '')
        relative = PurePosixPath(name)
        if not name or '\\' in name or ':' in name or relative.is_absolute() or any(p in ('', '.', '..') for p in name.split('/')):
            raise UpdateError('程序启动记录无效。')
        root = Path(current['path'])
        entry = root.joinpath(*relative.parts)
        if not root.is_absolute() or entry.is_symlink() or not entry.is_file() or not entry.resolve().is_relative_to(root.resolve()):
            raise UpdateError('当前程序文件未找到，请保留资料并重新接入。')
        return entry

    def launch_current(self, wait=False):
        if self.journal_path.exists():
            with self.maintenance():
                self._recover()
        with _FileLock(self.root / 'runtime/updater.lock'):
            state = self.get_state()
            if not state:
                raise UpdateError('请先接入已安装的 1.00 程序。')
            entry = self._entry(state['current'])
            env = _environment(self.root)
            env['LJQC_LAUNCHED_BY_UPDATER'] = '1'
            if state.get('updater_entrypoint'):
                env['LJQC_UPDATE_HELPER'] = state['updater_entrypoint']
            process = _spawn([str(entry)], cwd=str(entry.parent), env=env,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            # Wait until the launcher owns its normal instance lock before a
            # competing update may obtain updater.lock and inspect the data.
            # Keep the handoff lock until the trusted child owns desktop.lock
            # or exits. A slow launcher must not become a late old-version start.
            while process.poll() is None:
                try:
                    with _FileLock(self.root / 'runtime/desktop.lock'):
                        pass
                except UpdateError:
                    break
                time.sleep(.05)
            else:
                raise UpdateError('软件启动未完成，当前资料已保留。')
        return process.wait() if wait else process

    def _service(self, candidate: Path) -> Path:
        name = 'LJQCAppService.exe' if self.target == 'windows-x86_64' else 'LJQCAppService'
        roots = [candidate / '_internal/app', candidate / '_internal/service', candidate / 'app',
                 candidate / 'service', candidate, candidate / 'LJQCApp.app/Contents/Resources/app',
                 candidate / 'LJQCApp.app/Contents/Frameworks/app']
        for root in roots:
            path = root / name
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(candidate.resolve()):
                return path
        raise UpdateError('补丁缺少本机服务，当前程序未更改。')

    def _preflight(self, candidate: Path, manifest: dict, directory: Path, backup: Path, receipt: dict) -> Path:
        data_root = directory / 'candidate-data'
        candidate_db = prepare_candidate(backup, data_root, receipt)
        atomic_json(data_root / 'update-candidate.json', {'format': 'ljqc-update-candidate', 'base_release': BASE_RELEASE})
        service = self._service(candidate)
        result_file = data_root / 'preflight.json'
        env = _environment(data_root)
        env['LJQC_UPDATE_PREFLIGHT'] = '1'
        with (directory / 'preflight.log').open('wb') as log:
            process = _spawn([str(service), '--update-preflight', '--update-result', str(result_file)],
                             cwd=str(service.parent), env=env, stdout=log, stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                code = process.wait(timeout=150)
            except subprocess.TimeoutExpired as error:
                # This process has only a disposable copy. Never kill an active app.
                process.terminate()
                process.wait(timeout=15)
                raise UpdateError('新版隔离检查超时，当前程序和资料未更改。') from error
        if code or not result_file.is_file():
            raise UpdateError('新版隔离检查未通过，当前程序和资料未更改。')
        result = strict_json(result_file.read_bytes())
        release = result.get('release', {})
        if (result.get('status') != 'passed' or release.get('build_number') != manifest['build_number']
                or release.get('version') != manifest['version'] or release.get('source_digest') != manifest['source_digest']
                or release.get('git_commit') != manifest['source_commit'] or release.get('source_dirty') is not False):
            raise UpdateError('新版程序与签名发行记录不一致，已停止更新。')
        self._health_check(service, data_root, directory)
        if not receipt['is_new']:
            assert_retained(backup / 'database.db', candidate_db)
            assert_attachments(candidate_db, receipt['files'])
        return candidate_db

    def _health_check(self, service: Path, data_root: Path, directory: Path):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        request = data_root / 'runtime' / ('shutdown-' + uuid.uuid4().hex + '.request')
        request.parent.mkdir(parents=True, exist_ok=True)
        env = _environment(data_root)
        env['LJQC_SHUTDOWN_FILE'] = str(request)
        with (directory / 'health.log').open('wb') as log:
            process = _spawn([str(service), '--port', str(port), '--address', '127.0.0.1'], cwd=str(service.parent),
                env=env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            healthy = False
            try:
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline and process.poll() is None:
                    try:
                        with urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1) as response:
                            healthy = response.status == 200 and response.read(32).strip() == b'ok'
                    except OSError:
                        pass
                    if healthy:
                        break
                    time.sleep(.2)
            finally:
                request.write_text('stop\n', encoding='ascii')
                try:
                    process.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=15)
                    healthy = False
                request.unlink(missing_ok=True)
            if not healthy or process.returncode:
                raise UpdateError('新版本机服务检查未通过，当前程序和资料未更改。')

    def apply(self, manifest: dict, archive: Path) -> dict:
        with self.maintenance():
            self._recover()
            verified = self._accepted(manifest)
            old_state = self.get_state()
            if verified['build_number'] <= old_state['current']['build_number']:
                return old_state
            identifier = str(verified['build_number']) + '-' + verified['source_digest'][:12]
            destination = self.directory / 'versions' / identifier
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if destination.exists():
                verify_installed_payload(destination, verified)
            else:
                extract_verified_payload(Path(archive), destination, verified)
            if self.target == 'macos-arm64':
                result = subprocess.run(['codesign', '--verify', '--deep', '--strict', str(destination / 'LJQCApp.app')], capture_output=True)
                if result.returncode:
                    raise UpdateError('Mac 程序签名检查未通过，已保留当前程序。')
            run = self.directory / 'transactions' / uuid.uuid4().hex
            run.mkdir(parents=True, mode=0o700)
            backup = run / 'backup'
            receipt = snapshot(self.root, backup)
            candidate_db = self._preflight(destination, verified, run, backup, receipt)
            assert_current(self.root, receipt)
            verify_installed_payload(destination, verified)
            current = {'path': str(destination), 'entrypoint': verified['payload']['entrypoint'],
                       'version': verified['version'], 'build_number': verified['build_number'], 'target': self.target,
                       'source_digest': verified['source_digest'], 'source_commit': verified['source_commit']}
            new_state = {**old_state, 'current': current, 'previous': old_state['current'],
                         'last_update_at': datetime.now(timezone.utc).isoformat()}
            journal = {'format_version': 1, 'phase': 'prepared', 'old_state': old_state, 'new_state': new_state,
                       'backup': str(backup), 'receipt': receipt, 'metadata_digest': verified['_metadata_digest'],
                       'candidate_db': str(candidate_db), 'expected_data_digest': logical_digest(candidate_db)}
            atomic_json(self.journal_path, journal)
            if not receipt['is_new'] and journal['expected_data_digest'] != receipt['logical_digest']:
                self._switch_data(journal)
            atomic_json(self.state_path, new_state)
            journal['phase'] = 'committed'
            atomic_json(self.journal_path, journal)
            # Committed means a new business launch may follow. Never restore data after it.
            atomic_json(run / 'receipt.json', {'status': 'passed', 'build_number': verified['build_number'],
                'source_commit': verified['source_commit'], 'source_digest': verified['source_digest'],
                'original_data_retained': True, 'backup': str(backup), 'data_migrated': journal.get('data_migrated', False)})
            self.journal_path.unlink()
            return new_state

    def _switch_data(self, journal: dict):
        receipt = journal['receipt']
        original = Path(receipt['original_db'])
        candidate = Path(journal['candidate_db'])
        identifier = uuid.uuid4().hex
        prepared = original.with_name('.' + original.name + '.update-' + identifier)
        previous = original.with_name('.' + original.name + '.before-update-' + identifier)
        shutil.copy2(candidate, prepared)
        shutil.copystat(original, prepared, follow_symlinks=False)
        # Windows FlushFileBuffers requires a handle opened for writing.
        with prepared.open('r+b') as handle:
            os.fsync(handle.fileno())
        journal.update(phase='data-switching', prepared_db=str(prepared), previous_db=str(previous), data_migrated=True)
        atomic_json(self.journal_path, journal)
        self._release_reservation()
        with closing(sqlite3.connect(original.resolve().as_uri() + '?mode=rw', uri=True, timeout=1)) as connection:
            if connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
                raise UpdateError('数据文件仍被占用，已暂停更新。')
        assert_current(self.root, receipt)
        os.replace(original, previous)
        os.replace(prepared, original)
        journal['phase'] = 'data-switched'
        atomic_json(self.journal_path, journal)

    def _recover(self):
        if not self.journal_path.exists():
            return
        journal = strict_json(self.journal_path.read_bytes(), maximum=2 * 1024 * 1024)
        phase = journal.get('phase')
        if phase == 'committed':
            # New records may already exist. The live database always wins.
            self.journal_path.unlink()
            return
        if phase == 'prepared':
            self.journal_path.unlink()
            return
        if phase not in ('data-switching', 'data-switched'):
            raise UpdateError('上次更新记录需要核对，当前资料未回退。')
        self._verify_recovery(journal)
        original = Path(journal['receipt']['original_db'])
        previous = Path(journal['previous_db'])
        prepared = Path(journal['prepared_db'])
        config = self.root / 'config/storage_config.json'
        actual_config = digest_file(config) if config.exists() else None
        if (actual_config != journal['receipt']['config_sha256']
                or database_path(self.root, require_database=False) != original):
            raise UpdateError('保存位置在更新中断后已变化，未自动切换程序或资料。')
        for path, prefix in ((previous, '.' + original.name + '.before-update-'),
                             (prepared, '.' + original.name + '.update-')):
            suffix = path.name.removeprefix(prefix)
            if (path.parent != original.parent or not path.name.startswith(prefix) or len(suffix) != 32
                    or any(c not in '0123456789abcdef' for c in suffix) or path.is_symlink()):
                raise UpdateError('上次更新的资料位置记录无效，原资料未回退。')
        if not original.exists() and previous.is_file():
            # No program could have written to a missing DB; finish the prepared switch.
            if not prepared.is_file() or logical_digest(prepared) != journal['expected_data_digest']:
                raise UpdateError('上次更新的候选资料不完整，请保留现场核对。')
            os.replace(prepared, original)
        if original.is_file() and logical_digest(original) == journal['expected_data_digest']:
            assert_attachments(original, journal['receipt']['files'])
            # Continue forward; never replace a database with an old snapshot.
            self._entry(journal['new_state']['current'])
            atomic_json(self.state_path, journal['new_state'])
            self.journal_path.unlink()
            return
        if original.is_file() and logical_digest(original) == journal['receipt'].get('logical_digest'):
            atomic_json(self.state_path, journal['old_state'])
            self.journal_path.unlink()
            return
        raise UpdateError('更新中断后资料已有变化，请保留当前资料核对；未自动恢复旧备份。')

    def _verify_recovery(self, journal: dict):
        digest = journal.get('metadata_digest', '')
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise UpdateError('上次更新的签名记录无效，资料未回退。')
        metadata = self.directory / 'metadata' / (digest + '.json')
        if metadata.is_symlink() or not metadata.is_file():
            raise UpdateError('上次更新的签名记录缺失，资料未回退。')
        manifest = self._verify(metadata.read_bytes(), self.get_state())
        current = journal.get('new_state', {}).get('current', {})
        expected = self.directory / 'versions' / (str(manifest['build_number']) + '-' + manifest['source_digest'][:12])
        if (manifest['_metadata_digest'] != digest or current.get('path') != str(expected)
                or current.get('entrypoint') != manifest['payload']['entrypoint']
                or current.get('build_number') != manifest['build_number'] or current.get('version') != manifest['version']
                or current.get('source_digest') != manifest['source_digest'] or current.get('source_commit') != manifest['source_commit']):
            raise UpdateError('上次更新的程序记录与发行不一致，资料未回退。')
        verify_installed_payload(expected, manifest)
        if self.target == 'macos-arm64':
            result = subprocess.run(['codesign', '--verify', '--deep', '--strict', str(expected / 'LJQCApp.app')], capture_output=True)
            if result.returncode:
                raise UpdateError('上次更新的Mac程序校验未通过，资料未回退。')
