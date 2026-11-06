"""Small read-only compatibility fence before the legacy initializer can write.

This recognizes the current unversioned/format-1 baseline and supported legacy
core columns. It is not a general-purpose migration or downgrade engine.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3


SUPPORTED_DATA_FORMAT_VERSION = 1
PROJECT_COLUMNS = frozenset(('id', 'name', 'method_type', 'input_value_type', 'is_disabled', 'created_at'))
RESULT_COLUMNS = frozenset(('id', 'batch_id', 'test_time', 'operator', 'value', 'log_value',
    'reagent_lot_changed', 'is_building_included', 'is_outlier_suspect', 'outlier_status',
    'outlier_method', 'grubbs_statistic', 'grubbs_threshold', 'manual_status',
    'handled_at', 'manual_note', 'created_at'))
KNOWN_MIGRATIONS = frozenset((
    'v1_1_master_data_001', 'v1_2_template_reagent_001', 'v1_2_workbench_binding_001',
    'v1_2_zscore_snapshot_002', 'v1_2_instant_transfer_snapshot_003',
    'v1_2_lot_lifecycle_004', 'material_workflow_001', 'quality_targets_2024_001',
    'quality_applicability_review_001', 'out_of_control_001', 'product_directory_001',
    'product_attributes_001', 'qc_product_recommendations_001',
))


class SchemaCompatibilityError(RuntimeError):
    pass


@dataclass(frozen=True)
class SchemaCompatibility:
    path: Path
    data_format_version: int
    has_business_schema: bool
    is_new: bool


def assert_supported_connection(connection: sqlite3.Connection) -> tuple[int, bool]:
    version = int(connection.execute('PRAGMA user_version').fetchone()[0])
    if version not in (0, SUPPORTED_DATA_FORMAT_VERSION):
        raise SchemaCompatibilityError('此数据文件需要其他程序版本，请保留原资料并使用匹配版本打开。')
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    for table, allowed, required in (
        ('projects', PROJECT_COLUMNS, {'id', 'name'}),
        ('results', RESULT_COLUMNS, {'id', 'batch_id', 'test_time', 'value'}),
    ):
        if table not in tables:
            continue
        columns = {row[1] for row in connection.execute('PRAGMA table_info(' + table + ')')}
        if columns - allowed or not required.issubset(columns):
            raise SchemaCompatibilityError('数据结构尚未确认兼容，已停止写入；请保留资料并核对程序版本。')
    if tables and not tables.intersection({'projects', 'instant_projects', 'zscore_project_config', 'qc_project_templates'}):
        raise SchemaCompatibilityError('未识别此数据文件的业务结构，未执行初始化。')
    if 'schema_migrations' in tables:
        columns = {row[1] for row in connection.execute('PRAGMA table_info(schema_migrations)')}
        if 'migration_key' not in columns:
            raise SchemaCompatibilityError('数据结构登记无法识别，已停止写入。')
        registered = {row[0] for row in connection.execute('SELECT migration_key FROM schema_migrations')}
        if registered - KNOWN_MIGRATIONS:
            raise SchemaCompatibilityError('数据文件含当前程序尚不支持的升级记录，已停止写入。')
    return version, bool(tables)


def inspect_database(path: Path, *, check_integrity: bool = False,
                     require_business_schema: bool = False) -> SchemaCompatibility:
    path = Path(path).resolve()
    if not path.exists():
        if require_business_schema:
            raise SchemaCompatibilityError('原数据文件未找到，未建立替代空库。')
        return SchemaCompatibility(path, 0, False, True)
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
            connection.execute('PRAGMA query_only=ON')
            version, populated = assert_supported_connection(connection)
            if check_integrity and connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise SchemaCompatibilityError('数据文件检查未通过，当前资料未更改。')
            if require_business_schema:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'projects', 'batches', 'results'}.issubset(tables):
                    raise SchemaCompatibilityError('原文件不包含可接入的质控业务结构。')
    except (sqlite3.Error, OSError) as error:
        raise SchemaCompatibilityError('数据文件无法读取或结构已损坏，已停止初始化。') from error
    return SchemaCompatibility(path, version, populated, not populated)
