"""Read the published desktop downloads without opening a database or network."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import logging
from pathlib import Path
import re
from urllib.parse import urlsplit


LOGGER = logging.getLogger(__name__)
_VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_PLATFORMS = (
    ("windows-x86_64", "Windows 10/11 64 位", "下载 Windows 安装包"),
    ("macos-arm64", "Mac Apple 芯片（M 系列）", "下载 Mac 安装包"),
)


@dataclass(frozen=True)
class PublishedDownload:
    version: str
    published_at: date
    url: str
    sha256: str
    size_bytes: int
    build_number: int | None = None
    program_build_number: int | None = None
    updater_integrated: bool = False


@dataclass(frozen=True)
class SoftwareDownloadOption:
    target: str
    label: str
    download_label: str
    release: PublishedDownload | None = None


@dataclass(frozen=True)
class SoftwareDownloadCatalog:
    downloads: tuple[SoftwareDownloadOption, ...]
    readable: bool


def _published_download(value: object, target: str) -> PublishedDownload | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Invalid release record")
    if value.get("published") is not True:
        return None
    if value.get("target", target) != target:
        raise ValueError("Release target differs from its catalog entry")

    version, stamp, url, digest = (value.get(key) for key in
                                  ("version", "published_at", "url", "sha256"))
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ValueError("Invalid release version")
    if not isinstance(stamp, str) or not _DATE.fullmatch(stamp):
        raise ValueError("Invalid publication date")
    published_at = date.fromisoformat(stamp)
    if (not isinstance(url, str) or not url or len(url) > 4096
            or "\\" in url or any(character.isspace() or ord(character) < 32 or ord(character) == 127
                                   for character in url)):
        raise ValueError("Invalid download URL")
    address = urlsplit(url)
    if (address.scheme.lower() != "https" or not address.hostname
            or address.username is not None or address.password is not None):
        raise ValueError("A public HTTPS download URL is required")
    # Accessing port also rejects malformed or out-of-range port values.
    if address.port == 0:
        raise ValueError("Invalid download port")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ValueError("Invalid package digest")
    size = value.get("size_bytes")
    if type(size) is not int or size <= 0:
        raise ValueError("Invalid package size")
    build_number = value.get("build_number")
    if build_number is not None and (type(build_number) is not int or build_number <= 0):
        raise ValueError("Invalid build number")
    program_build_number = value.get("program_build_number")
    if program_build_number is not None and (type(program_build_number) is not int
                                             or program_build_number <= 0):
        raise ValueError("Invalid program build number")
    updater_integrated = value.get("updater_integrated", False)
    if type(updater_integrated) is not bool:
        raise ValueError("Invalid integrated updater flag")
    return PublishedDownload(version, published_at, url, digest.lower(), size,
                             build_number, program_build_number, updater_integrated)


def get_software_download_catalog(path: Path | None = None) -> SoftwareDownloadCatalog:
    """Return the two supported targets; incomplete releases stay unavailable."""
    catalog_path = path if path is not None else (
        Path(__file__).resolve().parents[1] / "resources" / "software_downloads.json")
    try:
        payload = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
        if (not isinstance(payload, dict) or type(payload.get("format_version")) is not int
                or payload["format_version"] != 1 or not isinstance(payload.get("releases"), dict)):
            raise ValueError("Invalid download catalog")
        records = payload["releases"]
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        LOGGER.warning("Software download catalog could not be read: %s", type(error).__name__)
        return SoftwareDownloadCatalog(tuple(
            SoftwareDownloadOption(target, label, button) for target, label, button in _PLATFORMS), False)

    downloads = []
    for target, label, button in _PLATFORMS:
        try:
            release = _published_download(records.get(target), target)
        except (TypeError, ValueError) as error:
            LOGGER.warning("Software download release is unavailable for %s: %s", target, type(error).__name__)
            release = None
        downloads.append(SoftwareDownloadOption(target, label, button, release))
    return SoftwareDownloadCatalog(tuple(downloads), True)
