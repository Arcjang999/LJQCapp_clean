"""Bounded HTTPS transport. Partial downloads are restarted, never appended."""
from __future__ import annotations

import hashlib
from http.client import HTTPException
import os
from pathlib import Path
import shutil
import ssl
import time
import threading
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .protocol import (DEFAULT_ORIGIN, MAX_ARTIFACT_BYTES, MAX_MANIFEST_BYTES,
                       TARGETS, UpdateError, positive_int, validate_https_url, validate_trust)

CHUNK_SIZE = 1024 * 1024
TIMEOUT_SECONDS = 20
MANIFEST_TIMEOUT_SECONDS = 5
_METADATA_GATE = threading.BoundedSemaphore(1)


class _OriginRedirects(HTTPRedirectHandler):
    def __init__(self, origin: str):
        self.origin = origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_https_url(newurl, self.origin)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_https(url: str, origin: str, *, timeout: float = TIMEOUT_SECONDS):
    validate_https_url(url, origin)
    opener = build_opener(_OriginRedirects(origin), HTTPSHandler(context=ssl.create_default_context()))
    request = Request(url, headers={"User-Agent": "LJQCApp-Updater/1", "Accept-Encoding": "identity"})
    response = opener.open(request, timeout=timeout)
    try:
        validate_https_url(response.geturl(), origin)
        if response.status != 200:
            raise UpdateError("服务器尚未提供完整更新文件，请稍后重试。")
        if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
            raise UpdateError("服务器更新文件的传输格式无效。")
    except Exception:
        response.close()
        raise
    return response


def fetch_manifest(trust: dict, target: str) -> bytes:
    """Fetch untrusted bytes at the fixed target path; caller then verifies."""
    if target not in TARGETS:
        raise UpdateError("无法识别当前更新平台。")
    validate_trust(trust)
    origin = trust["origin"].rstrip("/")
    return fetch_metadata_url(trust, origin + "/updates/desktop/dev/" + target + "/latest.json")


def fetch_metadata_url(trust: dict, url: str) -> bytes:
    """Bounded same-origin update metadata for release tooling and clients.

    Returned bytes are untrusted until their expected format and signatures are
    verified. This helper deliberately cannot read arbitrary server URLs.
    """
    validate_trust(trust)
    origin = trust["origin"].rstrip("/")
    validate_https_url(url, origin)
    from urllib.parse import urlsplit
    if not urlsplit(url).path.startswith("/updates/desktop/dev/"):
        raise UpdateError("更新元数据不在指定发布目录。")
    maximum = min(positive_int(trust.get("max_manifest_bytes", MAX_MANIFEST_BYTES), "清单大小"), MAX_MANIFEST_BYTES)
    deadline = time.monotonic() + MANIFEST_TIMEOUT_SECONDS
    # urllib's socket timeout is not a wall-clock budget: a peer can trickle
    # status/header bytes forever. A read-only daemon bounds the caller even
    # during headers. At most one such request may remain in flight; it owns no
    # business or update-cache handles, and cannot install anything after timeout.
    if not _METADATA_GATE.acquire(blocking=False):
        raise UpdateError("上次版本检查尚未结束，可继续使用当前版本并稍后重试。")
    completed = threading.Event()
    outcome = {}
    def network():
        try:
            outcome["value"] = _read_metadata(url, origin, maximum, deadline)
        except BaseException as error:
            outcome["error"] = error
        finally:
            _METADATA_GATE.release()
            completed.set()
    try:
        threading.Thread(target=network, name="LJQCMetadataRead", daemon=True).start()
    except RuntimeError as error:
        _METADATA_GATE.release()
        raise UpdateError("暂时无法检查更新，可继续使用当前版本。") from error
    if not completed.wait(MANIFEST_TIMEOUT_SECONDS):
        raise UpdateError("检查更新超时，可继续使用当前版本。")
    if "error" in outcome:
        error = outcome["error"]
        if isinstance(error, UpdateError):
            raise error
        raise UpdateError("暂时无法检查更新，可继续使用当前版本。") from error
    return outcome["value"]


def _read_metadata(url: str, origin: str, maximum: int, deadline: float) -> bytes:
    try:
        with _open_https(url, origin, timeout=MANIFEST_TIMEOUT_SECONDS) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None and (not declared.isdecimal() or int(declared) > maximum):
                raise UpdateError("更新清单超过允许大小。")
            raw = bytearray()
            # HTTPResponse.read(n) may internally wait for n bytes while a slow
            # peer keeps resetting the socket timeout. read1 yields after one
            # available read so the wall-clock budget is checked between chunks.
            reader = getattr(response, "read1", response.read)
            while True:
                if time.monotonic() >= deadline:
                    raise UpdateError("检查更新超时，可继续使用当前版本。")
                chunk = reader(min(8192, maximum - len(raw) + 1))
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > maximum:
                    break
            if len(raw) > maximum:
                raise UpdateError("更新清单超过允许大小。")
            if declared is not None and len(raw) != int(declared):
                raise UpdateError("更新清单下载不完整。")
            return bytes(raw)
    except UpdateError:
        raise
    except (OSError, URLError, HTTPException, ValueError) as error:
        raise UpdateError("暂时无法检查更新，可继续使用当前版本。") from error


def _hash_file(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            result.update(chunk)
    return result.hexdigest()


def download_artifact(manifest: dict, destination: Path, *, progress=None) -> Path:
    """Download exactly the authenticated artifact to a caller-owned cache path.

    At most two complete attempts are made for interrupted transport. A .part
    from an earlier attempt is restarted; byte-range resume is not claimed.
    """
    artifact = manifest.get("artifact", {})
    origin = manifest.get("_trusted_origin", DEFAULT_ORIGIN)
    url = validate_https_url(artifact.get("url"), origin)
    expected_size = positive_int(artifact.get("size_bytes"), "文件大小", maximum=MAX_ARTIFACT_BYTES)
    expected_hash = artifact.get("sha256")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64 or any(char not in "0123456789abcdef" for char in expected_hash):
        raise UpdateError("更新文件缺少有效校验值。")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.parent.is_symlink() or destination.is_symlink():
        raise UpdateError("更新缓存位置无效。")
    if destination.exists():
        if (destination.is_file() and destination.stat().st_size == expected_size
                and _hash_file(destination) == expected_hash):
            return destination
        raise UpdateError("更新缓存中已有其他文件，请保留现场并重试。")
    temporary = destination.with_name(destination.name + ".part")
    if temporary.is_symlink() or (temporary.exists() and not temporary.is_file()):
        raise UpdateError("更新临时文件位置无效。")
    if shutil.disk_usage(destination.parent).free < expected_size + 1024**2:
        raise UpdateError("磁盘空间不足，当前程序未更改。")
    for attempt in range(2):
        if temporary.exists():
            temporary.unlink()
        digest = hashlib.sha256()
        count = 0
        try:
            with _open_https(url, origin) as response, temporary.open("xb") as handle:
                declared = response.headers.get("Content-Length")
                if declared is not None and (not declared.isdecimal() or int(declared) != expected_size):
                    raise UpdateError("更新文件长度与发行记录不一致。")
                while True:
                    chunk = response.read(min(CHUNK_SIZE, expected_size - count + 1))
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > expected_size:
                        raise UpdateError("更新文件超过发行记录中的长度。")
                    digest.update(chunk)
                    handle.write(chunk)
                    if progress:
                        progress(count, expected_size)
                handle.flush()
                os.fsync(handle.fileno())
            if count != expected_size:
                if attempt == 0:
                    continue
                raise UpdateError("更新文件下载不完整，请重试。")
            if digest.hexdigest() != expected_hash:
                raise UpdateError("更新文件校验未通过，当前程序未更改。")
            # A verified candidate must never replace an existing other candidate.
            if destination.exists():
                raise UpdateError("更新缓存已变化，请重新检查更新。")
            temporary.replace(destination)
            return destination
        except UpdateError:
            raise
        except (OSError, URLError, HTTPException, ValueError) as error:
            if attempt == 1:
                raise UpdateError("更新下载中断，可稍后重试；当前程序未更改。") from error
    raise UpdateError("更新文件下载不完整，请重试。")
