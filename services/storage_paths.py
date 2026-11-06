"""Resolve mutable desktop storage without opening a database or creating files."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Mapping


class StoragePathError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoragePaths:
    root: Path
    config_dir: Path
    data_dir: Path
    storage_config_path: Path
    default_db_path: Path
    uses_stable_root: bool
    mode: str


def get_storage_paths(*, base_dir: Path | None = None,
                      environ: Mapping[str, str] | None = None,
                      frozen: bool | None = None, platform: str | None = None,
                      home: Path | None = None) -> StoragePaths:
    """Explicit QA/install roots win; cloud and ordinary source paths stay put."""
    env = os.environ if environ is None else environ
    base = Path(base_dir) if base_dir is not None else Path(__file__).resolve().parents[1]
    is_frozen = bool(getattr(sys, 'frozen', False)) if frozen is None else frozen
    system = sys.platform if platform is None else platform
    user_home = Path.home() if home is None else Path(home)
    explicit = str(env.get('LJQC_DATA_ROOT', '')).strip()
    cloud = str(env.get('LJQC_DEPLOYMENT_MODE', '')).strip().lower() == 'cloud'
    if explicit:
        root = Path(explicit).expanduser()
        if not root.is_absolute():
            raise StoragePathError('资料根目录必须是绝对路径，请核对启动设置。')
        mode = 'explicit'
    elif cloud or not is_frozen:
        data = base / 'data'
        return StoragePaths(base, data, data, data / 'storage_config.json',
                            data / 'qc_lj_app.db', False, 'cloud' if cloud else 'source')
    elif system == 'win32':
        local = str(env.get('LOCALAPPDATA', '')).strip()
        if not local:
            raise StoragePathError('无法确定本机资料目录，请核对用户环境后重试。')
        root = Path(local) / 'LJQCApp'
        mode = 'desktop'
    elif system == 'darwin':
        root = user_home / 'Library' / 'Application Support' / 'LJQCApp'
        mode = 'desktop'
    else:
        root = user_home / '.local' / 'share' / 'LJQCApp'
        mode = 'desktop'
    return StoragePaths(root, root / 'config', root / 'data',
                        root / 'config' / 'storage_config.json',
                        root / 'data' / 'qc_lj_app.db', True, mode)


def read_storage_config(path: Path, *, require_database: bool = True) -> dict:
    """A bad or dangling saved choice must never fall back to a new empty DB."""
    path = Path(path)
    if (path.parent / '.legacy-adoption.lock').exists():
        raise StoragePathError('上次资料接入尚未完成，请先核对接入记录；未建立替代空库。')
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as error:
        raise StoragePathError('保存位置配置无法读取，请选择原软件资料文件夹或核对保存位置。') from error
    if not isinstance(payload, dict):
        raise StoragePathError('保存位置配置无效，请核对原资料；当前资料未更改。')
    value = payload.get('database_path')
    if not isinstance(value, str) or not value.strip():
        raise StoragePathError('保存位置配置未包含有效的数据文件位置，请核对原资料。')
    database = Path(value.strip()).expanduser()
    if not database.is_absolute():
        raise StoragePathError('保存的数据文件位置不是绝对路径，请核对原资料。')
    if require_database and not database.is_file():
        raise StoragePathError('原数据文件未找到，请连接原存储位置或重新核对；未建立替代空库。')
    return payload
