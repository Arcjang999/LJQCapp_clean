from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path


DEFAULT_SERVER_ADDRESS = "0.0.0.0"
DEFAULT_SERVER_PORT = int(os.environ.get("LJQCAPP_PORT", "8506"))


def _get_log_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        log_dir = Path(local_app_data) / "LJQCApp"
    else:
        log_dir = Path.home() / ".ljqcapp"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "launcher.log"


def _write_log(message: str) -> None:
    log_path = _get_log_path()
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(message.rstrip() + "\n")


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        extracted_dir = getattr(sys, "_MEIPASS", "")
        if extracted_dir:
            return Path(str(extracted_dir)).resolve()
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resolve_app_path(base_dir: Path) -> Path:
    candidate_paths = [
        base_dir / "app.py",
        base_dir / "_internal" / "app.py",
    ]
    for candidate_path in candidate_paths:
        if candidate_path.exists():
            return candidate_path.resolve()
    return candidate_paths[0].resolve()


def _configure_import_paths(base_dir: Path, app_path: Path) -> None:
    import_roots = [app_path.parent, base_dir]
    for import_root in import_roots:
        import_root_str = str(import_root)
        if import_root_str not in sys.path:
            sys.path.insert(0, import_root_str)


def _build_streamlit_argv(app_path: Path, *, port: int, address: str) -> list[str]:
    return [
        "streamlit",
        "run",
        str(app_path),
        "--global.developmentMode=false",
        "--browser.gatherUsageStats=false",
        "--client.showSidebarNavigation=false",
        "--server.fileWatcherType=none",
        "--server.headless=true",
        f"--server.address={address}",
        f"--server.port={port}",
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the packaged LJQCApp Streamlit service.")
    parser.add_argument("--port", type=int, default=DEFAULT_SERVER_PORT)
    parser.add_argument("--address", default=DEFAULT_SERVER_ADDRESS)
    parser.add_argument("--seed-demo", action="store_true", help="导入演示数据。")
    parser.add_argument("--delete-demo", action="store_true", help="只删除【演示】前缀的演示数据。")
    parser.add_argument("--reset-db", action="store_true", help="重置整个数据库，必须配合 --yes。")
    parser.add_argument("--reset-and-seed-demo", action="store_true", help="重置整个数据库后导入演示数据，必须配合 --yes。")
    parser.add_argument("--profile", choices=("basic", "full"), default="full", help="演示数据规模，默认 full。")
    parser.add_argument("--replace-demo", action="store_true", help="导入前先删除旧演示数据，不影响真实数据。")
    parser.add_argument("--yes", action="store_true", help="确认执行重置数据库等破坏性操作。")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写入数据库。")
    return parser.parse_args(argv)


def run_streamlit_service(*, port: int, address: str) -> int:
    base_dir = _get_base_dir()
    app_path = _resolve_app_path(base_dir)
    log_path = _get_log_path()

    _write_log("=" * 80)
    _write_log(f"sys.executable: {sys.executable}")
    _write_log(f"cwd: {Path.cwd()}")
    _write_log(f"base_dir: {base_dir}")
    _write_log(f"resolved app.py path: {app_path}")
    _write_log(f"server.address: {address}")
    _write_log(f"server.port: {port}")

    if not app_path.exists():
        message = (
            f"ERROR: app.py not found.\n"
            f"Expected path: {app_path}\n"
            f"Log file: {log_path}"
        )
        print(message)
        _write_log(message)
        return 1

    os.chdir(base_dir)
    _configure_import_paths(base_dir, app_path)
    sys.argv = _build_streamlit_argv(app_path, port=port, address=address)
    try:
        from streamlit.web.cli import main as streamlit_main

        return streamlit_main()
    except Exception:
        error_trace = traceback.format_exc()
        print("ERROR: launcher failed. See launcher.log for details.")
        _write_log("Unhandled exception:")
        _write_log(error_trace)
        return 1


def _has_demo_operation(args: argparse.Namespace) -> bool:
    return any(
        [
            bool(args.seed_demo),
            bool(args.delete_demo),
            bool(args.reset_db),
            bool(args.reset_and_seed_demo),
            bool(args.replace_demo),
        ]
    )


def _validate_operation_args(args: argparse.Namespace) -> str | None:
    selected = [
        name
        for name, enabled in [
            ("--seed-demo", args.seed_demo),
            ("--delete-demo", args.delete_demo),
            ("--reset-db", args.reset_db),
            ("--reset-and-seed-demo", args.reset_and_seed_demo),
        ]
        if enabled
    ]
    if len(selected) > 1:
        return "一次只能执行一个演示/运维操作：" + ", ".join(selected)
    if args.replace_demo and (args.delete_demo or args.reset_db or args.reset_and_seed_demo):
        return "--replace-demo 只能与 --seed-demo 一起使用，或单独作为“替换演示数据”使用。"
    if args.reset_db and not args.yes:
        return "--reset-db 会清空整个数据库，必须增加 --yes 才会执行。"
    if args.reset_and_seed_demo and not args.yes:
        return "--reset-and-seed-demo 会清空整个数据库，必须增加 --yes 才会执行。"
    return None


def run_demo_cli(args: argparse.Namespace) -> int:
    from database import get_db_path, init_db, reset_database
    from services.demo_data_service import (
        delete_demo_data,
        format_operation_summary,
        seed_demo_data,
        validate_demo_data,
    )

    validation_error = _validate_operation_args(args)
    if validation_error:
        print(f"[拒绝执行] {validation_error}")
        print("如需重置数据库，请确认已备份重要数据后重新运行并添加 --yes。")
        return 2

    try:
        if args.delete_demo:
            result = delete_demo_data(dry_run=bool(args.dry_run))
            print(format_operation_summary(result))
            return 0

        if args.reset_db:
            db_path = get_db_path()
            if args.dry_run:
                print("操作：reset-db")
                print(f"数据库：{db_path}")
                print("dry-run：True")
                print("将重置整个数据库；本次未执行任何写入。")
                print("创建项目数：0")
                print("创建批次数：0")
                print("创建记录数：0")
                print("规则验证：未执行")
                return 0
            reset_database()
            init_db()
            print("操作：reset-db")
            print(f"数据库：{db_path}")
            print("已重置整个数据库并重新初始化表结构。")
            print("创建项目数：0")
            print("创建批次数：0")
            print("创建记录数：0")
            print("规则验证：未执行")
            return 0

        if args.reset_and_seed_demo:
            result = seed_demo_data(
                profile=args.profile,
                replace_demo=False,
                reset_all=True,
                dry_run=bool(args.dry_run),
            )
            print(format_operation_summary(result))
            return 0 if (result.get("validation") or {}).get("ok", True) else 1

        if args.seed_demo or args.replace_demo:
            result = seed_demo_data(
                profile=args.profile,
                replace_demo=bool(args.replace_demo),
                reset_all=False,
                dry_run=bool(args.dry_run),
            )
            print(format_operation_summary(result))
            return 0 if (result.get("validation") or {}).get("ok", True) else 1

        validation = validate_demo_data(profile=args.profile)
        print(f"数据库：{validation.get('db_path', get_db_path())}")
        print(f"演示数据验证：{'通过' if validation.get('ok') else '失败'}")
        if validation.get("failed"):
            print("失败项：")
            for failed in validation["failed"]:
                print(f"- {failed['name']}：{failed['detail']}")
        return 0 if validation.get("ok") else 1
    except Exception:
        error_trace = traceback.format_exc()
        print("[ERROR] 演示/运维操作失败：")
        print(error_trace)
        _write_log("Demo CLI failed:")
        _write_log(error_trace)
        return 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if _has_demo_operation(args):
        return run_demo_cli(args)
    return run_streamlit_service(port=args.port, address=args.address)


if __name__ == "__main__":
    raise SystemExit(main())
