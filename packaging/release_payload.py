"""Inspect an assembled program payload without importing the application."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

MANIFEST_NAME = "release_payload_manifest.json"
FORBIDDEN_NAMES = {
    "storage_config.json", "storage_config.json.tmp", "trial-data.zip",
    "清除数据库.bat", "写入演示数据.bat", "reset_db.bat", "reset_db.py",
}
FORBIDDEN_DIRECTORIES = {"backups", "test_data", "output", "outputs"}


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_payload(root: Path) -> list[dict[str, object]]:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("Program payload directory does not exist")
    records = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.name == MANIFEST_NAME and path.parent == root:
            continue
        if any(part.casefold() in FORBIDDEN_DIRECTORIES or part.casefold().endswith("_files")
               for part in relative.parts):
            raise ValueError("Mutable or trial directory in program payload: " + relative.as_posix())
        if path.name.casefold() in FORBIDDEN_NAMES:
            raise ValueError("Mutable or destructive file in program payload: " + relative.as_posix())
        if path.suffix.casefold() in {".db", ".sqlite", ".sqlite3"} or path.name.casefold().endswith(
                (".db-wal", ".db-shm", ".db-journal")):
            raise ValueError("Database file in program payload: " + relative.as_posix())
        record: dict[str, object] = {"path": relative.as_posix()}
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise ValueError("Payload symlink escapes program directory: " + relative.as_posix())
            record.update(kind="symlink", target=path.readlink().as_posix())
            if resolved.is_file():
                record.update(size_bytes=resolved.stat().st_size, sha256=digest_file(resolved))
            records.append(record)
            continue
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Unsupported entry in program payload: " + relative.as_posix())
        with path.open("rb") as handle:
            if handle.read(16) == b"SQLite format 3\x00":
                raise ValueError("SQLite content in program payload: " + relative.as_posix())
        record.update(kind="file", size_bytes=path.stat().st_size, sha256=digest_file(path))
        records.append(record)
    return records


def write_payload_manifest(root: Path, metadata: dict[str, object]) -> dict[str, object]:
    files = validate_payload(root)
    manifest = {"format": "ljqc-program-payload", "format_version": 1,
                "release": metadata, "files": files, "file_count": len(files),
                "mutable_data_included": False}
    (root / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    manifest = write_payload_manifest(args.payload, metadata)
    print(json.dumps({"status": "passed", "file_count": manifest["file_count"],
                      "manifest": str(args.payload / MANIFEST_NAME)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
