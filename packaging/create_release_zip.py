from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import time
import zipfile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    return parser.parse_args()


def remove_with_retry(path: Path) -> None:
    if not path.exists():
        return
    last_error: OSError | None = None
    for _ in range(10):
        try:
            path.unlink()
            return
        except OSError as exc:
            last_error = exc
            time.sleep(2)
    if last_error is not None:
        raise last_error


def create_zip(source: Path, output: Path, work_dir: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    work_dir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)

    temp_zip = work_dir / f"release-{int(time.time())}.zip"
    remove_with_retry(temp_zip)

    with zipfile.ZipFile(
        temp_zip,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())

    remove_with_retry(output)
    try:
        shutil.copyfile(temp_zip, output)
    finally:
        remove_with_retry(temp_zip)


def main() -> int:
    args = parse_args()
    create_zip(args.source, args.output, args.work_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
