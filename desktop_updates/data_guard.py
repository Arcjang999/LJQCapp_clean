"""Capture and compare desktop data without importing its initializing services."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3

from services.schema_compatibility import inspect_database
from services.storage_paths import read_storage_config
from .protocol import UpdateError


def digest_file(path: Path) -> str:
    value = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.new')
    if path.is_symlink() or temporary.is_symlink():
        raise UpdateError('更新记录位置无效，当前程序未更改。')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    if os.name != 'nt':
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def database_path(root: Path, *, require_database: bool = True) -> Path:
    config = root / 'config/storage_config.json'
    value = read_storage_config(config, require_database=require_database)
    return Path(value['database_path']).expanduser() if value else root / 'data/qc_lj_app.db'


def connect_readonly(path: Path):
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    connection.execute('PRAGMA query_only=ON')
    return connection


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _row_digest(row) -> str:
    value = hashlib.sha256()
    for cell in row:
        if cell is None:
            encoded = b'n'
        elif isinstance(cell, bytes):
            encoded = b'b' + cell
        elif isinstance(cell, str):
            encoded = b's' + cell.encode('utf-8')
        elif isinstance(cell, int):
            encoded = b'i' + str(cell).encode('ascii')
        else:
            encoded = b'f' + float(cell).hex().encode('ascii')
        value.update(len(encoded).to_bytes(8, 'big'))
        value.update(encoded)
    return value.hexdigest()


def logical_digest(path: Path) -> str:
    """Includes every table, typed cell and BLOB; independent of SQLite pages."""
    value = hashlib.sha256()
    with closing(connect_readonly(path)) as connection:
        schema = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        value.update(json.dumps(schema, ensure_ascii=False).encode('utf-8'))
        value.update(str(connection.execute('PRAGMA user_version').fetchone()[0]).encode('ascii'))
        for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            value.update(table.encode('utf-8') + b'\0')
            for item in sorted(_row_digest(row) for row in connection.execute('SELECT * FROM ' + _quote(table))):
                value.update(item.encode('ascii'))
    return value.hexdigest()


def assert_retained(before: Path, after: Path) -> None:
    """Permit additive migrations, while retaining all original typed cells."""
    with closing(connect_readonly(before)) as original, closing(connect_readonly(after)) as candidate:
        if candidate.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise UpdateError('新版资料副本完整性检查未通过，原资料未更改。')
        if candidate.execute('PRAGMA foreign_key_check').fetchall():
            raise UpdateError('新版资料副本关联检查未通过，原资料未更改。')
        tables = {row[0] for row in candidate.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for (table,) in original.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            if table not in tables:
                raise UpdateError('此次升级会移除原资料表，已停止更新。')
            columns = [row[1] for row in original.execute('PRAGMA table_info(' + _quote(table) + ')')]
            new_columns = {row[1] for row in candidate.execute('PRAGMA table_info(' + _quote(table) + ')')}
            if not set(columns).issubset(new_columns):
                raise UpdateError('此次升级会移除原资料字段，已停止更新。')
            selection = ','.join(_quote(column) for column in columns)
            query = 'SELECT ' + selection + ' FROM ' + _quote(table)
            old_rows = Counter(_row_digest(row) for row in original.execute(query))
            new_rows = Counter(_row_digest(row) for row in candidate.execute(query))
            if old_rows - new_rows:
                raise UpdateError('新版资料核对发现原记录、报告或设置变化，已停止更新。')


def managed_files(path: Path) -> list[dict]:
    with closing(connect_readonly(path)) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qc_managed_files'").fetchone():
            return []
        rows = connection.execute('SELECT relative_path,size_bytes,sha256 FROM qc_managed_files').fetchall()
    entries = {}
    for relative, size, sha in rows:
        if (not isinstance(relative, str) or '\\' in relative or ':' in relative
                or PurePosixPath(relative).is_absolute() or any(p in ('', '.', '..') for p in relative.split('/'))
                or not isinstance(size, int) or size <= 0 or not isinstance(sha, str)
                or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha)):
            raise UpdateError('原资料的附件登记无法核对，已停止更新。')
        entry = {'path': relative, 'size_bytes': size, 'sha256': sha}
        if relative in entries and entries[relative] != entry:
            raise UpdateError('原资料的附件登记不一致，已停止更新。')
        entries[relative] = entry
    return list(entries.values())


def _attachment(root: Path, entry: dict) -> Path:
    path = root / entry['path']
    cursor = path
    while cursor != root.parent:
        if cursor.is_symlink():
            raise UpdateError('资料附件位置无效，已停止更新。')
        cursor = cursor.parent
    if not path.is_file() or path.stat().st_size != entry['size_bytes'] or digest_file(path) != entry['sha256']:
        raise UpdateError('原资料附件缺失或校验未通过，请核对后再更新。')
    return path


def assert_attachments(database: Path, expected: list[dict]) -> None:
    if sorted(managed_files(database), key=lambda item: item['path']) != sorted(expected, key=lambda item: item['path']):
        raise UpdateError('此次更新需要迁移附件文件，暂未自动安装；现有资料已保留。')
    root = database.parent / (database.stem + '_files')
    for entry in expected:
        _attachment(root, entry)


def snapshot(root: Path, destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    config = root / 'config/storage_config.json'
    original = database_path(root)
    result = {'format_version': 1, 'original_db': str(original), 'is_new': not original.exists(), 'files': [],
              'config_sha256': digest_file(config) if config.exists() else None}
    if config.exists():
        shutil.copy2(config, destination / 'storage_config.json')
    if original.exists():
        inspect_database(original, check_integrity=True)
        copy = destination / 'database.db'
        with closing(connect_readonly(original)) as source, closing(sqlite3.connect(copy)) as target:
            source.backup(target)
        result['logical_digest'] = logical_digest(copy)
        if logical_digest(original) != result['logical_digest']:
            raise UpdateError('备份期间资料发生变化，已停止更新。')
        result['database_sha256'] = digest_file(copy)
        source_files = original.parent / (original.stem + '_files')
        for entry in managed_files(copy):
            source = _attachment(source_files, entry)
            target = destination / 'files' / entry['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            _attachment(destination / 'files', entry)
            result['files'].append(entry)
    atomic_json(destination / 'snapshot.json', result)
    return result


def assert_current(root: Path, receipt: dict) -> None:
    config = root / 'config/storage_config.json'
    current_config = digest_file(config) if config.exists() else None
    if current_config != receipt['config_sha256'] or str(database_path(root)) != receipt['original_db']:
        raise UpdateError('资料保存位置已变化，已停止更新。')
    path = Path(receipt['original_db'])
    if receipt['is_new']:
        if path.exists():
            raise UpdateError('检查期间已有新资料建立，请重新检查更新。')
        return
    if logical_digest(path) != receipt['logical_digest']:
        raise UpdateError('检查期间资料已变化，请保存并关闭软件后重试。')
    files_root = path.parent / (path.stem + '_files')
    for entry in receipt['files']:
        _attachment(files_root, entry)


def prepare_candidate(snapshot_directory: Path, destination: Path, receipt: dict) -> Path:
    database = destination / 'data/qc_lj_app.db'
    database.parent.mkdir(parents=True, exist_ok=False)
    if not receipt['is_new']:
        shutil.copy2(snapshot_directory / 'database.db', database)
        for entry in receipt['files']:
            target = database.parent / (database.stem + '_files') / entry['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snapshot_directory / 'files' / entry['path'], target)
    return database
