"""Export an immutable cumulative program archive, never a replacement installer."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "packaging")]
from release_payload import MANIFEST_NAME, digest_file, validate_payload

BASE_RELEASE = "1.0.0-2026110606"
TARGETS = {"windows-x86_64": ("LJQCApp.exe", "10.0.17763"),
           "macos-arm64": ("LJQCApp.app/Contents/MacOS/LJQCApp", "13.0")}
ORIGIN = "https://159.75.243.120"


def canonical_json(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def export_patch(payload: Path, metadata: dict, output: Path, *, now: datetime | None = None) -> dict:
    target = metadata.get("target")
    if target not in TARGETS:
        raise ValueError("Unsupported patch target")
    if metadata.get("source_dirty") is not False:
        raise ValueError("A publishable patch requires a clean, captured source identity")
    import re
    if (re.fullmatch(r"[0-9a-f]{40}", str(metadata.get("git_commit", ""))) is None
            or re.fullmatch(r"[0-9a-f]{64}", str(metadata.get("source_digest", ""))) is None
            or re.fullmatch(r"\d+\.\d+\.\d+", str(metadata.get("version", ""))) is None
            or type(metadata.get("build_number")) is not int
            or metadata["build_number"] <= 2026110606
            or metadata.get("data_format_version") != 1):
        raise ValueError("Incomplete or incompatible patch identity")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    name = f"LJQCApp-{metadata['version']}-{metadata['build_number']}-{target}-patch.tar.gz"
    artifact = output / name
    candidate_path = output / "candidate-update.json"
    if artifact.exists() or candidate_path.exists():
        raise ValueError("Immutable patch output already exists")
    payload = payload.resolve(strict=True)
    entrypoint = TARGETS[target][0]
    if not (payload / entrypoint).is_file():
        raise ValueError("Native program entry point is missing")
    with tempfile.TemporaryDirectory(prefix="ljqc-patch-stage-") as temporary:
        stage = Path(temporary) / "program"
        if target == "macos-arm64":
            stage.mkdir()
            shutil.copytree(payload / "LJQCApp.app", stage / "LJQCApp.app", symlinks=True)
        else:
            shutil.copytree(payload, stage, symlinks=True)
        (stage / MANIFEST_NAME).unlink(missing_ok=True)
        records = validate_payload(stage)
        for record in records:
            if record["kind"] == "file":
                original_mode = stat.S_IMODE((stage / record["path"]).stat().st_mode)
                record["mode"] = (0o755 if Path(record["path"]).suffix.lower() in (".exe", ".dll", ".pyd") else 0o644) if target == "windows-x86_64" else original_mode & 0o755
        manifest = {"format": "ljqc-program-payload", "format_version": 1,
                    "release": metadata, "files": records, "file_count": len(records),
                    "mutable_data_included": False}
        (stage / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        file_modes = {record["path"]: record["mode"] for record in records if record["kind"] == "file"}
        manifest_sha = digest_file(stage / MANIFEST_NAME)
        unpacked_size = sum(path.stat().st_size for path in stage.rglob("*") if path.is_file() and not path.is_symlink())
        temporary_output = output / ("." + name + ".part")
        try:
            with temporary_output.open("xb") as raw:
                with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
                    with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT, dereference=False) as archive:
                        for path in sorted(stage.rglob("*"), key=lambda item: item.relative_to(stage).as_posix()):
                            relative = path.relative_to(stage).as_posix()
                            info = archive.gettarinfo(str(path), arcname=relative)
                            info.uid = info.gid = 0
                            info.uname = info.gname = ""
                            info.mtime = 0
                            if info.isfile():
                                info.mode = file_modes.get(relative, 0o644)
                                with path.open("rb") as content:
                                    archive.addfile(info, content)
                            else:
                                info.mode = 0o777 if info.issym() else 0o755
                                archive.addfile(info)
                raw.flush()
                os.fsync(raw.fileno())
            temporary_output.replace(artifact)
        finally:
            temporary_output.unlink(missing_ok=True)
    issued = now or datetime.now(timezone.utc)
    candidate = {"format": "ljqc-desktop-update", "format_version": 1,
        "product": "LJQCApp", "edition": "desktop", "channel": "dev", "target": target,
        "base_release": BASE_RELEASE, "version": metadata["version"],
        "build_number": metadata["build_number"], "sequence": metadata["build_number"],
        "issued_at": issued.isoformat(), "expires_at": (issued + timedelta(days=14)).isoformat(),
        "min_updater_version": 1, "min_os_version": TARGETS[target][1],
        "data_format_version": 1, "source_commit": metadata["git_commit"],
        "source_digest": metadata["source_digest"],
        "artifact": {"url": f"{ORIGIN}/downloads/patches/{BASE_RELEASE}/{metadata['version']}/{metadata['build_number']}/{target}/{name}",
                     "size_bytes": artifact.stat().st_size, "sha256": digest_file(artifact),
                     "kind": "tar.gz", "unpacked_size_bytes": unpacked_size},
        "payload": {"entrypoint": entrypoint, "manifest_sha256": manifest_sha}}
    candidate_path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    receipt = {"status": "candidate_built_not_published", "base_release": BASE_RELEASE,
        "release": metadata, "artifact": {"file": name, **candidate["artifact"]},
        "payload_file_count": len(records), "mutable_data_included": False,
        "base_installer_rebuilt": False, "signed": False}
    (output / "patch-build-receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return candidate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8-sig"))
    result = export_patch(args.payload, metadata, args.output)
    print(json.dumps({"status": "candidate_built_not_published", "target": result["target"],
                      "build_number": result["build_number"], "sha256": result["artifact"]["sha256"]}))


if __name__ == "__main__":
    main()
