"""Desktop version identity and shutdown/locking boundaries, without business DBs."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.release_info import get_release_info, release_input_files
from services.runtime_lock import ServiceLock
import run_app


class InstallerRuntimeTests(unittest.TestCase):
    def test_embedded_identity_is_available_without_git_or_database(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'release.json').write_text(json.dumps({'version': '2.1.0', 'build_number': 42, 'data_format_version': 1}))
            (root / 'build_info.json').write_text(json.dumps({'git_commit': 'a' * 40, 'source_digest': 'b' * 64, 'source_dirty': True}))
            with patch('services.release_info.subprocess.run', side_effect=AssertionError('runtime invoked git')):
                info = get_release_info(root)
            self.assertEqual(('2.1.0', 42, 'a' * 40, True), (info.version, info.build_number, info.git_commit, info.source_dirty))
            self.assertEqual('2.1.0', info.as_dict()['app_version'])
            (root / 'output').mkdir()
            (root / 'output/secret.db').write_bytes(b'not-release-data')
            (root / 'trial.db').write_bytes(b'not-release-data')
            self.assertEqual(['release.json'], [p.name for p in release_input_files(root)])

    def test_second_service_is_rejected_and_lock_releases_on_exit(self):
        with TemporaryDirectory() as folder:
            one, two = ServiceLock(Path(folder)), ServiceLock(Path(folder))
            try:
                one.acquire()
                with self.assertRaisesRegex(RuntimeError, '正在使用'):
                    two.acquire()
                one.close()
                two.acquire()
            finally:
                one.close()
                two.close()

    def test_shutdown_request_delivers_sigint_to_main_thread(self):
        with TemporaryDirectory() as folder:
            script = """
import os, signal, time
from pathlib import Path
import run_app
seen = []
signal.signal(signal.SIGINT, lambda *args: seen.append(True))
stopped, watcher, request = run_app._start_shutdown_watcher()
request.parent.mkdir(parents=True)
request.touch()
deadline = time.monotonic() + 3
while not seen and time.monotonic() < deadline:
    time.sleep(.05)
stopped.set()
watcher.join(1)
assert seen == [True], seen
request.unlink()
"""
            env = {**os.environ, 'LJQC_DATA_ROOT': folder,
                   'LJQC_SHUTDOWN_FILE': str(Path(folder) / 'runtime' / ('shutdown-' + 'a' * 32 + '.request'))}
            result = subprocess.run([sys.executable, '-c', script], cwd=ROOT, env=env, capture_output=True, text=True, timeout=8)
            self.assertEqual(0, result.returncode, result.stderr)

    def test_service_lock_outlives_main_loop_until_pending_save_thread_finishes(self):
        with TemporaryDirectory() as folder:
            script = """
import threading, time
import run_app
def service(**kwargs):
    threading.Thread(target=lambda: time.sleep(1.5), daemon=False).start()
    return 0
run_app.run_streamlit_service = service
assert run_app.main([]) == 0
print('main-returned', flush=True)
"""
            process = subprocess.Popen([sys.executable, '-c', script], cwd=ROOT,
                env={**os.environ, 'LJQC_DATA_ROOT': folder}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            lock = ServiceLock(Path(folder) / 'runtime')
            try:
                self.assertEqual('main-returned', process.stdout.readline().strip())
                with self.assertRaisesRegex(RuntimeError, '正在使用'):
                    lock.acquire()
                self.assertEqual(0, process.wait(timeout=5))
                lock.acquire()
            finally:
                lock.close()
                process.wait(timeout=5)
                process.stdout.close()
                process.stderr.close()

    def test_foreign_and_stale_shutdown_requests_are_not_consumed(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            foreign = root / 'keep.txt'
            foreign.write_text('keep')
            with patch.dict(os.environ, {'LJQC_DATA_ROOT': folder, 'LJQC_SHUTDOWN_FILE': str(foreign)}):
                with self.assertRaisesRegex(RuntimeError, '关闭请求位置无效'):
                    run_app._start_shutdown_watcher()
            self.assertEqual('keep', foreign.read_text())


if __name__ == '__main__':
    unittest.main()
