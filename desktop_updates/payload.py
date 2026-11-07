"""Extract and verify a complete program payload into a new private directory.

Archive members are written manually. No extractall, external links, hardlinks,
mutable business data or special devices are accepted. Symlinks are created
only after every ordinary file, and their resolved targets must stay inside the
candidate. This module never imports the application or opens a database.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile

from .protocol import (HEX64, MAX_ARTIFACT_BYTES, MAX_FILES, MAX_UNPACKED_BYTES,
                       UpdateError, positive_int, strict_json)

MANIFEST_NAME = "release_payload_manifest.json"
FORBIDDEN_NAMES = frozenset(("storage_config.json", "storage_config.json.tmp", "trial-data.zip",
    "清除数据库.bat", "写入演示数据.bat", "reset_db.bat", "reset_db.py"))
FORBIDDEN_DIRECTORIES = frozenset(("backups", "test_data", "output", "outputs"))
WINDOWS_RESERVED = re.compile(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?\Z", re.I)


def file_digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _safe_name(value: str, *, directory: bool = False) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096 or "\\" in value or any(ord(c) < 32 for c in value):
        raise UpdateError("更新包包含无效文件路径。")
    value = value.rstrip("/") if directory else value
    parts = value.split("/")
    if any(part in ("", ".", "..") or ":" in part or part.endswith((" ", "."))
           or WINDOWS_RESERVED.fullmatch(part) is not None for part in parts):
        raise UpdateError("更新包包含越界或不兼容文件路径。")
    normalized = PurePosixPath(value).as_posix()
    if normalized != value or PurePosixPath(value).is_absolute():
        raise UpdateError("更新包包含越界文件路径。")
    lower_parts = [part.casefold() for part in parts]
    if any(part in FORBIDDEN_DIRECTORIES or part.endswith("_files") for part in lower_parts):
        raise UpdateError("更新包含运行资料目录，已停止更新。")
    name = lower_parts[-1]
    if (name in FORBIDDEN_NAMES or Path(name).suffix in (".db", ".sqlite", ".sqlite3")
            or name.endswith((".db-wal", ".db-shm", ".db-journal"))):
        raise UpdateError("更新包含客户资料或重置工具，已停止更新。")
    return value


class _LimitedTarInfo(tarfile.TarInfo):
    def _bound_header(self, archive):
        total = getattr(archive, "_ljqc_extended_header_bytes", 0) + self.size
        if self.size > 65536 or total > 32 * 1024**2:
            raise UpdateError("更新包的扩展文件头超过允许大小。")
        archive._ljqc_extended_header_bytes = total

    def _proc_pax(self, tarfile):
        self._bound_header(tarfile)
        return super()._proc_pax(tarfile)

    def _proc_gnulong(self, tarfile):
        self._bound_header(tarfile)
        return super()._proc_gnulong(tarfile)


def _verify_release(payload: dict, manifest: dict) -> None:
    if (payload.get("format") != "ljqc-program-payload" or type(payload.get("format_version")) is not int
            or payload["format_version"] != 1 or payload.get("mutable_data_included") is not False):
        raise UpdateError("更新包的程序清单格式无效。")
    release = payload.get("release")
    if not isinstance(release, dict):
        raise UpdateError("更新包缺少来源记录。")
    for key in ("target", "version", "build_number", "source_digest", "data_format_version"):
        if type(release.get(key)) is not type(manifest.get(key)) or release.get(key) != manifest.get(key):
            raise UpdateError("更新包与发行清单的版本或平台不一致。")
    if release.get("git_commit") != manifest.get("source_commit") or release.get("source_dirty") is not False:
        raise UpdateError("更新包的源码来源不一致或未经封存。")


def _verify_extracted(root: Path, manifest: dict, entries: dict[str, dict], *, check_modes: bool = True) -> dict:
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise UpdateError("更新包缺少完整程序清单。")
    if file_digest(manifest_path) != manifest.get("payload", {}).get("manifest_sha256"):
        raise UpdateError("更新包的程序清单校验未通过。")
    # The signed outer artifact limits the total unpacked size. The file list is
    # bounded separately to avoid loading an arbitrarily large JSON object.
    if manifest_path.stat().st_size > 32 * 1024**2:
        raise UpdateError("更新包的文件清单超过允许大小。")
    payload = strict_json(manifest_path.read_bytes(), maximum=32 * 1024**2)
    _verify_release(payload, manifest)
    records = payload.get("files")
    if (not isinstance(records, list) or len(records) > MAX_FILES
            or type(payload.get("file_count")) is not int or payload["file_count"] != len(records)):
        raise UpdateError("更新包的文件清单无效。")
    expected = {}
    for record in records:
        if not isinstance(record, dict):
            raise UpdateError("更新包的文件清单无效。")
        name = _safe_name(record.get("path"))
        if name == MANIFEST_NAME or name in expected or record.get("kind") not in ("file", "symlink"):
            raise UpdateError("更新包的文件清单重复或无效。")
        expected[name] = record
    actual = {name: info for name, info in entries.items() if info["kind"] != "directory" and name != MANIFEST_NAME}
    if set(actual) != set(expected):
        raise UpdateError("更新包的实际文件与完整清单不一致。")
    for name, record in expected.items():
        info = actual[name]
        path = root / name
        if record.get("kind") != info["kind"]:
            raise UpdateError("更新包的文件类型不一致。")
        if info["kind"] == "symlink":
            if record.get("target") != os.readlink(path):
                raise UpdateError("更新包的链接记录不一致。")
            try:
                resolved = path.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise UpdateError("更新包含损坏或循环链接。") from error
            if not resolved.is_relative_to(root):
                raise UpdateError("更新包链接越出程序目录。")
        else:
            resolved = path
        if resolved.is_file():
            if (type(record.get("size_bytes")) is not int or record["size_bytes"] != resolved.stat().st_size
                    or not isinstance(record.get("sha256"), str) or HEX64.fullmatch(record["sha256"]) is None
                    or record["sha256"] != file_digest(resolved)):
                raise UpdateError("更新包的程序文件校验未通过。")
            with resolved.open("rb") as handle:
                if handle.read(16) == b"SQLite format 3\x00":
                    raise UpdateError("更新包含数据库内容，已停止更新。")
        elif info["kind"] != "symlink" or not resolved.is_dir():
            raise UpdateError("更新包包含不支持的文件。")
        if check_modes and info["kind"] == "file" and "mode" in record:
            mode = record["mode"]
            if type(mode) is not int or mode != info["mode"]:
                raise UpdateError("更新包文件权限与清单不一致。")
    entry_name = manifest.get("payload", {}).get("entrypoint")
    if entry_name not in actual or actual[entry_name]["kind"] != "file":
        raise UpdateError("更新包缺少可执行程序入口。")
    if manifest.get("target") == "macos-arm64":
        # The authenticated manifest and tar entry retain POSIX mode even when
        # inspected on Windows. Windows stat/chmod cannot represent executable
        # bits; only a native POSIX recheck may require them on the installed file.
        signed_mode = expected[entry_name].get("mode")
        if (type(signed_mode) is not int or not signed_mode & stat.S_IXUSR
                or (check_modes and not actual[entry_name]["mode"] & stat.S_IXUSR)):
            raise UpdateError("Mac 更新包的启动权限无效。")
    return payload


def extract_verified_payload(archive: Path, destination: Path, manifest: dict) -> dict:
    """Verify artifact bytes and extract into a non-existing candidate directory.

    Production callers must verify the original signed envelope immediately
    before this call. Build QA may pass an unsigned signed-body for local checks.
    Any failure removes only the candidate directory created by this function.
    """
    artifact = manifest.get("artifact", {})
    size = positive_int(artifact.get("size_bytes"), "文件大小", maximum=MAX_ARTIFACT_BYTES)
    expected_unpacked = positive_int(artifact.get("unpacked_size_bytes"), "展开大小", maximum=MAX_UNPACKED_BYTES)
    archive = Path(archive)
    if (not archive.is_file() or archive.is_symlink() or archive.stat().st_size != size
            or file_digest(archive) != artifact.get("sha256")):
        raise UpdateError("更新文件长度或校验值不一致。")
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise UpdateError("候选程序目录已存在，未覆盖原文件。")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.parent.is_symlink():
        raise UpdateError("候选程序位置无效。")
    if shutil.disk_usage(destination.parent).free < expected_unpacked + 1024**2:
        raise UpdateError("磁盘空间不足，当前程序未更改。")
    destination.mkdir(mode=0o700)
    root = destination.resolve()
    entries = {}
    folded = set()
    symlinks = []
    unpacked = 0
    max_files = min(positive_int(manifest.get("_max_files", MAX_FILES), "文件数量"), MAX_FILES)
    try:
        with tarfile.open(archive, "r|gz", tarinfo=_LimitedTarInfo) as bundle:
            for member in bundle:
                if len(entries) >= max_files:
                    raise UpdateError("更新包文件数量超过允许范围。")
                name = _safe_name(member.name, directory=member.isdir())
                if name in entries or name.casefold() in folded:
                    raise UpdateError("更新包包含重复或大小写冲突的文件。")
                folded.add(name.casefold())
                if (not member.isfile() and member.size != 0) or member.sparse is not None:
                    raise UpdateError("更新包包含不支持的稀疏或特殊文件数据。")
                if member.mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX) or (not member.issym() and member.mode & 0o022):
                    raise UpdateError("更新包包含不安全的文件权限。")
                mode = member.mode & 0o777
                target = root / name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    entries[name] = {"kind": "directory", "mode": mode}
                elif member.isfile():
                    positive_int(member.size, "展开文件大小", minimum=0, maximum=expected_unpacked)
                    unpacked += member.size
                    if unpacked > expected_unpacked:
                        raise UpdateError("更新包展开后超过发行记录大小。")
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    source = bundle.extractfile(member)
                    if source is None:
                        raise UpdateError("更新包文件无法读取。")
                    copied = 0
                    with source, target.open("xb") as handle:
                        while True:
                            chunk = source.read(min(1024**2, member.size - copied + 1))
                            if not chunk:
                                break
                            copied += len(chunk)
                            if copied > member.size:
                                raise UpdateError("更新包文件超过声明长度。")
                            handle.write(chunk)
                    if copied != member.size:
                        raise UpdateError("更新包文件不完整。")
                    target.chmod(mode)
                    entries[name] = {"kind": "file", "mode": mode}
                elif member.issym():
                    link = member.linkname
                    if (not isinstance(link, str) or not link or len(link) > 4096 or "\\" in link
                            or PurePosixPath(link).is_absolute() or ":" in link
                            or any(ord(char) < 32 for char in link)):
                        raise UpdateError("更新包包含无效链接。")
                    # '..' is needed by framework links, but must stay in root.
                    resolved = (target.parent / link).resolve()
                    if not resolved.is_relative_to(root):
                        raise UpdateError("更新包链接越出程序目录。")
                    symlinks.append((target, link))
                    entries[name] = {"kind": "symlink", "mode": mode}
                else:
                    raise UpdateError("更新包包含硬链接、设备或不支持的文件。")
            if unpacked != expected_unpacked:
                raise UpdateError("更新包展开长度与发行记录不一致。")
        # No tar member is ever written through a symlink. Create links last.
        for target, link in symlinks:
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.symlink_to(link)
        payload = _verify_extracted(root, manifest, entries)
        for name, info in entries.items():
            if info["kind"] == "directory":
                (root / name).chmod(info["mode"])
        return payload
    except UpdateError:
        shutil.rmtree(root)
        raise
    except (OSError, ValueError, tarfile.TarError, EOFError, RuntimeError) as error:
        shutil.rmtree(root)
        raise UpdateError("更新包无法安全展开，当前程序未更改。") from error


def verify_installed_payload(root: Path, manifest: dict) -> dict:
    """Read-only revalidation of a candidate immediately before switching.

    Windows POSIX permission bits are not representable by chmod/stat; archive
    modes were checked during extraction. Mac candidates additionally retain
    exact file modes and executable bits. Links must still match the sealed list.
    """
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise UpdateError("候选程序目录无效。")
    root = root.resolve()
    entries = {}
    folded = set()
    try:
        for path in root.rglob("*"):
            name = _safe_name(path.relative_to(root).as_posix(), directory=path.is_dir() and not path.is_symlink())
            if len(entries) >= MAX_FILES or name.casefold() in folded:
                raise UpdateError("候选程序含重复或过多文件。")
            folded.add(name.casefold())
            mode = stat.S_IMODE(path.lstat().st_mode)
            if path.is_symlink():
                kind = "symlink"
            elif path.is_file():
                kind = "file"
            elif path.is_dir():
                kind = "directory"
            else:
                raise UpdateError("候选程序含不支持的文件类型。")
            if mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                raise UpdateError("候选程序的文件权限已变化。")
            entries[name] = {"kind": kind, "mode": mode}
        return _verify_extracted(root, manifest, entries, check_modes=os.name != "nt")
    except UpdateError:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise UpdateError("候选程序无法重新核验，当前程序未更改。") from error
