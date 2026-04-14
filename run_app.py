from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path


DEFAULT_SERVER_ADDRESS = "0.0.0.0"
DEFAULT_SERVER_PORT = int(os.environ.get("LJQCAPP_PORT", "8501"))


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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run_streamlit_service(port=args.port, address=args.address)


if __name__ == "__main__":
    raise SystemExit(main())
