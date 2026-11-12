"""One release identity for source, desktop launchers and release artifacts."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    build_number: int
    data_format_version: int = 1
    git_commit: str = ""
    source_dirty: bool = False
    source_digest: str = ""
    build_time: str = ""
    target: str = ""
    display_version: str = ""

    def as_dict(self) -> dict:
        return {**asdict(self), "app_version": self.version}


def display_version_label(value: dict) -> str:
    """Public dotted-number labels never participate in release comparisons."""
    version = str(value.get("version") or value.get("app_version") or "")
    label = value.get("display_version")
    if isinstance(label, str) and len(label) <= 32 and re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", label):
        return label
    return version


def _read_json(path: Path) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def release_input_files(root: Path) -> list[Path]:
    """Explicit allowlist: never traverse runtime databases, output or snapshots."""
    files = set(root.glob("*.py"))
    for name in ("migrations", "pages", "services", "ui", "packaging", "desktop_updates", "desktop_licensing"):
        files.update(p for p in (root / name).rglob("*") if p.suffix in {".py", ".spec", ".ps1", ".iss", ".sh", ".plist"})
    for name in ("assets", "resources", "data/dictionaries", "data/catalogs"):
        files.update(p for p in (root / name).rglob("*") if p.is_file())
    for name in ("release.json", ".gitattributes", ".github/workflows/desktop-build.yml",
                 "requirements.txt", "requirements-dev.txt", "requirements-cloud.txt", "requirements-build.txt",
                 "docs/OPERATION_GUIDE.md", "docs/current_user_guide.md"):
        files.add(root / name)
    files.update((root / "docs/images/first-use").glob("*"))
    files.update((root / ".github/workflows").glob("*.yml"))
    return sorted((p for p in files if p.is_file() and "__pycache__" not in p.parts
                   and p.suffix.lower() not in {".db", ".sqlite", ".sqlite3", ".pyc", ".pyo"}),
                  key=lambda p: p.relative_to(root).as_posix())


def get_release_info(path: Path | None = None, *, include_source_state: bool = False) -> ReleaseInfo:
    root = Path(path).resolve() if path is not None else Path(__file__).resolve().parents[1]
    if root.is_file():
        root = root.parent
    candidates = [root]
    if getattr(sys, "frozen", False):
        candidates = [Path(getattr(sys, "_MEIPASS", root)), Path(sys.executable).resolve().parent,
                      Path(sys.executable).resolve().parent.parent / "Resources", root]
    release = next((value for base in candidates if (value := _read_json(base / "release.json"))), {})
    metadata = next((value for base in candidates if (value := _read_json(base / "build_info.json"))), {})
    values = {**release, **metadata}
    metadata_version = metadata.get("version") or metadata.get("app_version")
    release_version = str(release.get("version") or release.get("app_version") or "")
    version = str(metadata_version or release_version or "1.0.0")
    # An older build_info must not inherit the checked-out release's new label.
    display = metadata.get("display_version")
    if not display and version == release_version:
        display = release.get("display_version")
    display = display_version_label({"version": version, "display_version": display})
    if include_source_state:
        def git(*arguments: str) -> str:
            process = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, text=True, check=True)
            return process.stdout.strip()
        digest = hashlib.sha256()
        for item in release_input_files(root):
            digest.update(item.relative_to(root).as_posix().encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(item.read_bytes()).digest())
        values["source_digest"] = digest.hexdigest()
        try:
            if Path(git("rev-parse", "--show-toplevel")).resolve() != root:
                raise ValueError("Source snapshot is not the Git checkout root")
            values["git_commit"] = git("rev-parse", "HEAD")
            values["source_dirty"] = bool(git("status", "--porcelain", "--untracked-files=normal"))
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
            # Offline build kits carry the captured identity, never runtime data.
            # Any edit after export invalidates it and requires a fresh export.
            import re
            identity = _read_json(root / "source_identity.json")
            if (identity.get("source_digest") != values["source_digest"]
                    or re.fullmatch(r"[0-9a-f]{40}", str(identity.get("git_commit", ""))) is None
                    or not isinstance(identity.get("source_dirty"), bool)):
                raise RuntimeError("源码快照与版本摘要不匹配，请重新导出构建资料。")
            values["git_commit"] = identity["git_commit"]
            values["source_dirty"] = identity["source_dirty"]
    return ReleaseInfo(version=version, build_number=int(values.get("build_number", 2026110601)),
        data_format_version=int(values.get("data_format_version", 1)),
        git_commit=str(values.get("git_commit", "")), source_dirty=bool(values.get("source_dirty", False)),
        source_digest=str(values.get("source_digest", "")), build_time=str(values.get("build_time", "")),
        target=str(values.get("target", "")), display_version=display)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", choices=("windows-x86_64", "macos-arm64"), required=True)
    args = parser.parse_args()
    payload = get_release_info(include_source_state=True).as_dict()
    payload.update(build_time=datetime.now(timezone.utc).isoformat(), target=args.target)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
