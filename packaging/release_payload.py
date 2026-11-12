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
FORBIDDEN_DIRECTORIES = {"backups", "test_data", "output", "outputs", "licensing_server", ".private"}
PROTECTION_MANIFEST = "protected_runtime_manifest.json"
PROTECTED_ROOT_MODULES = frozenset(("app_main", "chart_layout", "database", "import_review", "plotting",
                                  "qc_logic", "zscore_logic", "zscore_plotting", "run_app"))
PROTECTED_PACKAGES = frozenset(("services", "ui", "pages", "migrations", "desktop_licensing"))
LICENSE_MODULES = frozenset(('desktop_licensing.client', 'desktop_licensing.protocol',
    'desktop_licensing.device', 'desktop_licensing.runtime', 'desktop_licensing.trust_anchor'))
THIN_APP_ENTRY = b"from app_main import main\n\nmain()\n"


def assert_protected_payload(root: Path) -> int:
    """Reject readable business code even if a valid extension is also present.

    The final Mac signature can change native extension bytes; ordinary payload
    hashes authenticate those bytes separately.  This check enforces the native
    module inventory and the intentionally tiny Streamlit entry script.
    """
    declarations = list(root.rglob(PROTECTION_MANIFEST))
    if len(declarations) != 1 or declarations[0].is_symlink():
        raise ValueError("Expected one protected application runtime declaration")
    declaration = declarations[0]
    value = json.loads(declaration.read_text(encoding="utf-8"))
    if (value.get("format") != "ljqc-protected-runtime" or value.get("format_version") != 1
            or value.get("readable_business_source_included") is not False):
        raise ValueError("Unrecognized protected application runtime")
    base, names = declaration.parent, set()
    for entry in value.get("modules", []):
        name, relative = entry.get("module", ""), Path(entry.get("file", ""))
        if (not name or name in names or relative.is_absolute() or ".." in relative.parts
                or relative.suffix not in {".so", ".pyd"}):
            raise ValueError("Invalid protected native module declaration")
        names.add(name)
        path = base / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError("Protected native business module is missing: " + name)
    if not (PROTECTED_ROOT_MODULES | LICENSE_MODULES).issubset(names):
        raise ValueError("Protected runtime omits required native business modules")
    trust_file = base / 'resources/desktop_license_trust.json'
    if (not trust_file.is_file() or digest_file(trust_file) != value.get('license_trust_sha256')):
        raise ValueError('Protected license trust is missing or differs from the compiled runtime')
    if not (base / "app.py").is_file() or (base / "app.py").read_bytes() != THIN_APP_ENTRY:
        raise ValueError("Desktop Streamlit entry contains unexpected application source")
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if path.suffix.lower() not in {".py", ".pyc", ".pyo", ".pyx", ".c", ".cpp", ".h"}:
            continue
        # Both desktop components are inspected.  A stray source copy beside
        # the outer launcher is a leak even when the service itself is native.
        business = (any(part in PROTECTED_PACKAGES for part in relative.parts[:-1]) or
                    path.name.split(".", 1)[0] in PROTECTED_ROOT_MODULES)
        if path.name == "app.py":
            if path.read_bytes() != THIN_APP_ENTRY:
                raise ValueError("Desktop entry contains unexpected application source: " + relative.as_posix())
            continue
        if business:
            if path.name == "__init__.py" and path.read_bytes() == b"":
                continue
            raise ValueError("Readable business source in program payload: " + relative.as_posix())
    return len(names)


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
    if list(root.rglob(PROTECTION_MANIFEST)):
        assert_protected_payload(root)
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
