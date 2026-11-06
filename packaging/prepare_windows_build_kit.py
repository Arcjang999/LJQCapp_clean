"""Export a verified source-only Windows build kit; this does not create Setup.exe."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.release_info import get_release_info, release_input_files


def digest_file(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def prepare_kit(output: Path) -> dict:
    captured = get_release_info(ROOT, include_source_state=True).as_dict()
    now = datetime.now(timezone.utc)
    run = now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    stage = output.resolve() / f"{captured['version']}-{captured['build_number']}-windows-source-kit-{run}"
    stage.mkdir(parents=True, exist_ok=False)
    source = stage / "LJQCApp-source"
    source.mkdir()
    records = []
    for original in release_input_files(ROOT):
        relative = original.relative_to(ROOT)
        if not original.resolve().is_relative_to(ROOT) or original.is_symlink():
            raise ValueError("Source build input must be an ordinary in-repository file: " + relative.as_posix())
        if any(part in {".git", "output", "outputs", "test_data", "backups"}
               or part.endswith("_files") for part in relative.parts):
            raise ValueError("Mutable or snapshot path entered the source allowlist: " + relative.as_posix())
        if original.name in {"storage_config.json", "trial-data.zip"}:
            raise ValueError("A runtime file entered the source allowlist")
        with original.open("rb") as handle:
            if handle.read(16) == b"SQLite format 3\x00":
                raise ValueError("SQLite content entered the source allowlist: " + relative.as_posix())
        destination = source / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, destination)
        records.append({"path": relative.as_posix(), "sha256": digest_file(destination),
                        "size_bytes": destination.stat().st_size})
    identity = {"format": "ljqc-source-build-identity", "format_version": 1,
                "git_commit": captured["git_commit"], "source_dirty": captured["source_dirty"],
                "source_digest": captured["source_digest"], "version": captured["version"],
                "build_number": captured["build_number"], "captured_at": now.isoformat()}
    (source / "source_identity.json").write_text(json.dumps(identity, ensure_ascii=False, indent=2) + "\n",
                                                encoding="utf-8")
    verified = get_release_info(source, include_source_state=True).as_dict()
    for key in ("version", "build_number", "git_commit", "source_dirty", "source_digest"):
        if verified[key] != captured[key]:
            raise RuntimeError("Exported source identity differs from the captured source: " + key)
    current = get_release_info(ROOT, include_source_state=True).as_dict()
    if any(current[key] != captured[key] for key in ("git_commit", "source_digest")):
        raise RuntimeError("Source changed during export; this failed staging directory is retained")
    # This explicit auxiliary document is not a build input and does not alter the
    # canonical source digest. It is included separately in the transfer manifest.
    shutil.copy2(ROOT / "docs/windows-build.md", source / "WINDOWS_BUILD_README.md")
    manifest = {"format": "ljqc-windows-source-build-kit", "format_version": 1,
                "artifact_kind": "source-build-kit-not-installer", "identity": identity,
                "source_files": records, "mutable_data_included": False,
                "windows_setup_built": False, "windows_native_validation": "pending"}
    (source / "build_kit_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                                   encoding="utf-8")
    files = sorted(path for path in source.rglob("*") if path.is_file())
    expected = {"LJQCApp-source/" + path.relative_to(source).as_posix(): digest_file(path) for path in files}
    archive = stage / f"LJQCApp-{captured['version']}-{captured['build_number']}-windows-source-kit.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as handle:
        for path in files:
            handle.write(path, "LJQCApp-source/" + path.relative_to(source).as_posix())
    with zipfile.ZipFile(archive, "r") as handle:
        if set(handle.namelist()) != set(expected) or handle.testzip() is not None:
            raise RuntimeError("Build-kit archive validation failed")
        for name, digest in expected.items():
            if hashlib.sha256(handle.read(name)).hexdigest() != digest:
                raise RuntimeError("A source file changed in the archive: " + name)
    receipt = {"status": "source_kit_prepared", "artifact_kind": "source-build-kit-not-installer",
               "artifact": str(archive), "sha256": digest_file(archive), "size_bytes": archive.stat().st_size,
               "source_snapshot": str(source), "source_files": len(records), **identity,
               "copied_source_identity_verified": True, "archive_content_verified": True,
               "windows_setup_built": False, "windows_native_validation": "pending"}
    (stage / "source_kit_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                                                  encoding="utf-8")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/installers")
    args = parser.parse_args()
    print(json.dumps(prepare_kit(args.output), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
