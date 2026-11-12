"""One-click installation entry for the fixed integrated desktop base."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop_updates.bootstrap import default_data_root, native_target, resource_path
from services.release_info import get_release_info


def main(argv=None) -> int:
    release = get_release_info()
    display_version = getattr(release, 'display_version', '') or release.version
    parser = argparse.ArgumentParser(description="安装 LJQCApp " + display_version)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--no-gui", action="store_true")
    parser.add_argument("--bundle", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--data-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--target", choices=("windows-x86_64", "macos-arm64"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    target = args.target or native_target()
    root = args.data_root.expanduser().absolute() if args.data_root else default_data_root(target)
    root.joinpath("updates").mkdir(parents=True, exist_ok=True, mode=0o700)
    logging.basicConfig(filename=str(root / "updates/integrated-installer.log"), encoding="utf-8",
                        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("ljqc.integrated-installer")
    application = dialog = None
    try:
        if not args.no_gui:
            from PySide6.QtCore import Qt
            from PySide6.QtWidgets import QApplication, QProgressDialog
            application = QApplication.instance() or QApplication(sys.argv[:1])
            dialog = QProgressDialog("正在准备安装…", "", 0, 0)
            dialog.setWindowTitle("安装 LJQCApp " + display_version)
            dialog.setCancelButton(None)
            dialog.setWindowModality(Qt.ApplicationModal)
            dialog.setMinimumDuration(0)
            dialog.show()
            application.processEvents()
        def progress(message):
            log.info("%s", message)
            if dialog is not None:
                dialog.setLabelText(message)
                application.processEvents()
        bundle = args.bundle if args.bundle is not None else resource_path("integrated-bundle/bundle.json").parent
        from desktop_updates.manager import UpdateManager
        from desktop_updates.integrated_install import install_integrated
        result = install_integrated(UpdateManager(root=root, target=target), bundle, progress=progress)
        log.info("Installation completed: %s", json.dumps(result, ensure_ascii=False))
        if sys.stdout is not None:
            print(json.dumps(result, ensure_ascii=False))
        if dialog is not None:
            dialog.close()
            from PySide6.QtWidgets import QMessageBox
            location = "开始菜单" if target == "windows-x86_64" else "应用程序"
            QMessageBox.information(None, "LJQCApp", "安装完成，自动更新已启用。\n从“" + location + "”中的 LJQCApp 打开软件。")
        return 0
    except Exception as error:
        log.exception("Integrated installation failed")
        if dialog is not None:
            dialog.close()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(None, "LJQCApp", str(error))
        elif sys.stderr is not None:
            print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
