"""Identify the running edition without opening business data or using Git."""
from __future__ import annotations

import os
import sys
from typing import Mapping

from services.release_info import get_release_info


def get_runtime_edition(*, environ: Mapping[str, str] | None = None,
                        frozen: bool | None = None) -> str:
    environment = os.environ if environ is None else environ
    packaged = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    # A packaged desktop service remains desktop even if its parent shell has
    # a cloud setting left over from another task.
    if packaged:
        return "desktop"
    mode = str(environment.get("LJQC_DEPLOYMENT_MODE", "")).strip().lower()
    if mode == "desktop":
        return "desktop"
    if mode == "cloud":
        return "web"
    return "source"


def software_downloads_available() -> bool:
    return get_runtime_edition() == "web"


def runtime_version_label() -> str:
    labels = {"desktop": "桌面版", "web": "网页版", "source": "源码运行版"}
    release = get_release_info()
    return (f"{labels[get_runtime_edition()]} · 版本 {release.display_version or release.version}"
            f" · 构建 {release.build_number}")
