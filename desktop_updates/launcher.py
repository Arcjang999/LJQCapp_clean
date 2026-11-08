"""Stable update entry point, including side-effect-free build probes."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop_updates.bootstrap import (BASE_RELEASE, UPDATER_VERSION, BootstrapError,
                                      default_data_root, helper_source, install_bootstrap,
                                      native_target, read_baseline_manifest, recover_bootstrap,
                                      resource_path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LJQCApp 更新组件")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--ui-self-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--tls-self-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--probe-result", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--base", type=Path)
    parser.add_argument("--helper", type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--target", choices=("windows-x86_64", "macos-arm64"))
    parser.add_argument("--no-gui", action="store_true")
    args = parser.parse_args(argv)
    if args.ui_self_test and args.tls_self_test:
        parser.error("一次只能运行一个验收检查。")
    if args.probe_result is not None and not (args.ui_self_test or args.tls_self_test):
        parser.error("--probe-result 仅用于独立验收检查。")
    return args


def version_info() -> dict:
    return {"product": "LJQCAppUpdater", "updater_version": UPDATER_VERSION,
            "base_release": BASE_RELEASE, "automatic_update_client": True}


def self_test(target: str | None = None) -> dict:
    """Read packaged public configuration only; never create root or state."""
    from desktop_updates.protocol import validate_trust
    targets = (target,) if target else ("windows-x86_64", "macos-arm64")
    receipts = {name: read_baseline_manifest(name)["file_count"] for name in targets}
    trust = json.loads(resource_path("resources/desktop_update_trust.json").read_text(encoding="utf-8"))
    validate_trust(trust)
    keys = trust.get("keys", {})
    if not isinstance(keys, dict) or not keys:
        raise BootstrapError("更新组件缺少发行校验资料。")
    # Exercise the frozen native crypto backend, not merely JSON/base64 parsing.
    # This ephemeral diagnostic key is never saved or used as release trust.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    probe = Ed25519PrivateKey.generate()
    message = b'LJQCApp isolated cryptography backend probe'
    probe.public_key().verify(probe.sign(message), message)
    return {**version_info(), "status": "passed", "baseline_file_counts": receipts,
            "trust_keys": len(keys), "cryptography_backend": "passed", "business_storage_opened": False}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.version:
        print(json.dumps(version_info(), ensure_ascii=False))
        return 0
    if args.self_test:
        print(json.dumps(self_test(args.target), ensure_ascii=False))
        return 0
    if args.tls_self_test:
        # No Qt, UpdateManager or storage-path resolution on this branch, even
        # when the server is unavailable or TLS verification fails.
        from desktop_updates.tls_probe import run_tls_self_test
        try:
            trust = json.loads(resource_path("resources/desktop_update_trust.json").read_text(encoding="utf-8"))
            result = run_tls_self_test(trust)
        except Exception as error:
            chain = []
            cause = error
            while cause is not None and len(chain) < 5:
                chain.append(type(cause).__name__)
                cause = cause.__cause__
            result = {"format_version": 1, "product": "LJQCAppUpdater", "status": "failed",
                      "reason": "tls-probe-" + type(error).__name__,
                      "error_types": chain,
                      "business_storage_opened": False}
        if args.probe_result is not None:
            _write_probe_result(args.probe_result, result)
        if sys.stdout is not None:
            print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") == "passed" else 1
    if args.ui_self_test:
        # This branch deliberately precedes native storage-path resolution and
        # UpdateManager import. Its manager is entirely in memory.
        from desktop_updates.qt_ui import run_ui_self_test
        try:
            result = run_ui_self_test()
        except Exception as error:
            result = {"format_version": 1, "product": "LJQCAppUpdater", "status": "failed",
                      "reason": "ui-probe-" + type(error).__name__,
                      "business_storage_opened": False}
        if args.probe_result is not None:
            _write_probe_result(args.probe_result, result)
        if sys.stdout is not None:
            print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") == "passed" else 1
    target = args.target or native_target()
    root = args.data_root.expanduser().absolute() if args.data_root else default_data_root(target)
    if not root.is_absolute():
        raise BootstrapError("资料位置必须使用完整路径。")
    from desktop_updates.manager import UpdateManager
    manager = UpdateManager(root=root, target=target)
    recover_bootstrap(manager)
    from desktop_updates.helper_repair import (needs_helper_repair, recover_helper_repair,
                                               repair_registered_helper)
    recover_helper_repair(manager)
    source_helper = args.helper or (helper_source() if getattr(sys, "frozen", False) else None)
    registered = bool(manager.get_state().get("current"))
    repair_mode = bool(source_helper is not None and registered
                       and (args.install or needs_helper_repair(manager, source_helper)))
    if args.no_gui:
        if args.install:
            if registered:
                result = repair_registered_helper(manager, source_helper or helper_source())
            elif not args.base:
                raise BootstrapError("请指定已安装的 1.00 程序位置。")
            else:
                result = install_bootstrap(manager, args.base, source_helper or helper_source())
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.check:
            result = manager.check()
            print(json.dumps(result, ensure_ascii=False))
            return 0
        raise BootstrapError("无窗口模式仅支持版本检查或明确指定的更新接入。")
    from desktop_updates.qt_ui import run_update_ui
    return run_update_ui(manager, install=args.install, base=args.base,
                         helper=source_helper, manual_check=args.check, repair_mode=repair_mode)


def _write_probe_result(path: Path, result: dict) -> None:
    output = path.expanduser()
    if not output.is_absolute():
        raise BootstrapError("验收结果必须使用完整路径。")
    if output.exists() or output.is_symlink():
        raise BootstrapError("验收结果文件已经存在。")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        # Do not expose a Python traceback in a windowed customer entry point.
        logging.getLogger("ljqc.updater").exception("Updater entry failed")
        if any(flag in sys.argv[1:] for flag in ("--no-gui", "--tls-self-test", "--ui-self-test", "--self-test", "--version")):
            # Native CI/maintenance failures must exit rather than leave an
            # invisible modal dialog. Diagnostics already emit bounded receipts.
            if sys.stderr is not None:
                print("更新组件验收未通过，现有程序及资料已保留。", file=sys.stderr)
            raise SystemExit(1)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance() or QApplication(sys.argv[:1])
            QMessageBox.critical(None, "LJQCApp", "更新组件未能启动，请保留现有程序及资料，重新运行更新接入补丁。")
        except Exception:
            print("更新组件未能启动，请保留现有程序及资料并重新运行更新接入补丁。", file=sys.stderr)
        raise SystemExit(1)
