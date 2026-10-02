"""Native folder actions select the host platform and keep failures readable."""
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.storage_service import open_folder_in_system


class FolderOpenTests(unittest.TestCase):
    def test_mac_and_linux_open_the_requested_folder_without_a_shell(self):
        with TemporaryDirectory(prefix='质控 文件夹 ') as temp:
            for platform, command in [('darwin', 'open'), ('linux', 'xdg-open')]:
                with self.subTest(platform=platform), patch('services.storage_service.sys.platform', platform), \
                        patch('services.storage_service.subprocess.run') as run:
                    open_folder_in_system(Path(temp))
                    run.assert_called_once_with([command, temp], check=True, capture_output=True)

    def test_windows_uses_native_startfile(self):
        with TemporaryDirectory() as temp, patch('services.storage_service.sys.platform', 'win32'), \
                patch('services.storage_service.os.startfile', create=True) as start, \
                patch('services.storage_service.subprocess.run') as run:
            open_folder_in_system(Path(temp))
            start.assert_called_once_with(temp)
            run.assert_not_called()

    def test_native_failure_returns_an_actionable_message(self):
        with TemporaryDirectory() as temp:
            for failure in [FileNotFoundError('missing opener'), subprocess.CalledProcessError(1, ['open'])]:
                with self.subTest(failure=type(failure).__name__), patch('services.storage_service.sys.platform', 'darwin'), \
                        patch('services.storage_service.subprocess.run', side_effect=failure):
                    with self.assertRaisesRegex(RuntimeError, '请在系统文件管理器中打开'):
                        open_folder_in_system(Path(temp))

    def test_missing_folder_is_not_opened(self):
        with TemporaryDirectory() as temp, patch('services.storage_service.subprocess.run') as run:
            with self.assertRaisesRegex(RuntimeError, '目标路径不存在'):
                open_folder_in_system(Path(temp) / '不存在')
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
