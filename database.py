from __future__ import annotations

import json
import math
import os
import re
import shutil
import sqlite3
from pathlib import Path

import pandas as pd

from services.outlier_service import (
    OUTLIER_MANUAL_STATUS_NORMAL,
    OUTLIER_STATUS_NORMAL,
    normalize_outlier_manual_status,
    normalize_outlier_status,
)
from services.value_type_service import (
    DEFAULT_INPUT_VALUE_TYPE,
    get_measurement_label,
    normalize_input_value_type,
    should_show_auxiliary_log_column,
)
from migrations.v1_1_master_data import ensure_v11_schema
from migrations.v1_2_workbench import ensure_v12_workbench_schema


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DATA_DIR = BASE_DIR / "data"
PROJECT_DB_PATH = PROJECT_DATA_DIR / "qc_lj_app.db"
PROJECT_LEGACY_DB_PATH = BASE_DIR / "lj_qc.db"
MIGRATION_PROJECT_NAME = "\u5386\u53f2\u6570\u636e\u8fc1\u79fb\u9879\u76ee"
_UNSET = object()
PROJECT_METHOD_LJ = "lj"
PROJECT_METHOD_ZSCORE = "zscore"


def _get_persistent_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "LJQCApp"
    return Path.home() / ".ljqcapp"


DATA_DIR = _get_persistent_data_dir()
DEFAULT_DB_PATH = DATA_DIR / "qc_lj_app.db"
STORAGE_CONFIG_PATH = DATA_DIR / "storage_config.json"
LEGACY_DB_CANDIDATES = [
    PROJECT_DB_PATH,
    PROJECT_LEGACY_DB_PATH,
    DEFAULT_DB_PATH,
]
DB_PATH = DEFAULT_DB_PATH


def _read_storage_config_payload() -> dict[str, object]:
    if not STORAGE_CONFIG_PATH.exists():
        return {}
    try:
        payload = json.loads(STORAGE_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_configured_db_path(payload: dict[str, object] | None = None) -> Path | None:
    resolved_payload = payload if payload is not None else _read_storage_config_payload()
    raw_path = str((resolved_payload or {}).get("database_path") or "").strip()
    if not raw_path:
        return None
    return Path(raw_path).expanduser()


def _resolve_runtime_db_path() -> Path:
    configured_path = _extract_configured_db_path()
    return configured_path or DEFAULT_DB_PATH


DB_PATH = _resolve_runtime_db_path()


def get_db_path() -> Path:
    return DB_PATH


def get_default_db_path() -> Path:
    return DEFAULT_DB_PATH


def get_storage_config_path() -> Path:
    return STORAGE_CONFIG_PATH


def get_configured_db_path() -> Path | None:
    return _extract_configured_db_path()


def save_db_path_config(db_path: Path) -> Path:
    normalized_path = Path(db_path).expanduser()
    STORAGE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "database_path": str(normalized_path.resolve()),
    }
    temp_path = STORAGE_CONFIG_PATH.with_name(f"{STORAGE_CONFIG_PATH.name}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(STORAGE_CONFIG_PATH)
    return STORAGE_CONFIG_PATH


def clear_db_path_config() -> None:
    try:
        STORAGE_CONFIG_PATH.unlink()
    except FileNotFoundError:
        return


def refresh_db_path_from_config() -> Path:
    global DB_PATH
    DB_PATH = _resolve_runtime_db_path()
    return DB_PATH


def _ensure_db_parent() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _migrate_legacy_db_file() -> None:
    _ensure_db_parent()
    if DB_PATH.exists():
        return

    for legacy_db_path in LEGACY_DB_CANDIDATES:
        if not legacy_db_path.exists() or legacy_db_path.resolve() == DB_PATH.resolve():
            continue
        shutil.move(str(legacy_db_path), str(DB_PATH))
        return


def reset_database() -> None:
    for db_path in [DB_PATH, *LEGACY_DB_CANDIDATES]:
        if not db_path.exists():
            continue
        try:
            db_path.unlink()
        except PermissionError as exc:
            raise RuntimeError(
                f"\u65e0\u6cd5\u5220\u9664\u6570\u636e\u5e93\u6587\u4ef6\uff1a{db_path}\uff0c"
                "\u8bf7\u5148\u5173\u95ed\u6b63\u5728\u8fd0\u884c\u7684\u7a0b\u5e8f\u540e\u518d\u91cd\u8bd5\u3002"
            ) from exc


def get_connection() -> sqlite3.Connection:
    _migrate_legacy_db_file()
    connection = sqlite3.connect(DB_PATH, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    _migrate_legacy_db_file()
    with get_connection() as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        _ensure_projects_table(connection)
        _ensure_batches_table(connection)
        _ensure_results_table(connection)
        _ensure_instant_projects_table(connection)
        _ensure_instant_batches_table(connection)
        _ensure_instant_results_table(connection)
        _ensure_zscore_project_config_table(connection)
        _ensure_zscore_batch_config_table(connection)
        _ensure_zscore_runs_table(connection)
        _ensure_zscore_level_results_table(connection)
        _ensure_zscore_level_targets_table(connection)
        _ensure_report_exports_table(connection)
        _ensure_app_settings_table(connection)
        ensure_v11_schema(connection)
        ensure_v12_workbench_schema(connection)
        _rebind_legacy_batches_foreign_keys(connection)
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_results_batch_time
            ON results (batch_id, test_time, id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_batches_project
            ON batches (project_id, id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_instant_batches_project
            ON instant_batches (project_id, id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_instant_results_batch_time
            ON instant_results (batch_id, test_time, id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_zscore_runs_batch_time
            ON zscore_runs (batch_id, rule_template_id, test_time, id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_zscore_runs_batch_sequence
            ON zscore_runs (batch_id, test_sequence, id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_zscore_batch_config_project
            ON zscore_batch_config (project_id, level_count, batch_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_zscore_level_results_run_level
            ON zscore_level_results (run_id, level_id, id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_zscore_level_targets_batch_level
            ON zscore_level_targets (batch_id, level_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_report_exports_scope
            ON report_exports (report_type, batch_id, report_month, generated_at)
            """
        )


def _quote_identifier(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _find_tables_referencing_legacy_batches(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
          AND sql LIKE '%batches_legacy%'
        ORDER BY name ASC
        """
    ).fetchall()
    return [str(row["name"]) for row in rows]


def _rebind_legacy_batches_foreign_keys(connection: sqlite3.Connection) -> None:
    target_tables = _find_tables_referencing_legacy_batches(connection)
    if not target_tables:
        return

    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        for table_name in target_tables:
            _rebuild_table_rebinding_legacy_batches(connection, table_name)
        connection.commit()
    finally:
        connection.execute("PRAGMA foreign_keys = ON")

    remaining_tables = _find_tables_referencing_legacy_batches(connection)
    if remaining_tables:
        raise RuntimeError(
            "batches 外键修复未完成，以下表仍引用 batches_legacy："
            + ", ".join(remaining_tables)
        )

    foreign_key_issues = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_issues:
        raise RuntimeError(
            "batches 外键修复后仍存在外键检查错误："
            + "; ".join(str(tuple(issue)) for issue in foreign_key_issues[:10])
        )


def _rebuild_table_rebinding_legacy_batches(
    connection: sqlite3.Connection,
    table_name: str,
) -> None:
    table_row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    if table_row is None or not table_row["sql"]:
        return

    temp_table_name = f"__rebinding_{table_name}"
    connection.execute(f"DROP TABLE IF EXISTS {_quote_identifier(temp_table_name)}")

    table_sql = str(table_row["sql"])
    rebuilt_sql = _build_rebound_table_sql(table_sql, temp_table_name)
    preserved_indexes = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'index'
          AND tbl_name = ?
          AND sql IS NOT NULL
        ORDER BY name ASC
        """,
        (table_name,),
    ).fetchall()

    connection.execute(rebuilt_sql)
    column_rows = connection.execute(f"PRAGMA table_info({_quote_identifier(table_name)})").fetchall()
    column_names = [str(row["name"]) for row in column_rows]
    if column_names:
        quoted_columns = ", ".join(_quote_identifier(column_name) for column_name in column_names)
        connection.execute(
            f"""
            INSERT INTO {_quote_identifier(temp_table_name)} ({quoted_columns})
            SELECT {quoted_columns}
            FROM {_quote_identifier(table_name)}
            """
        )

    connection.execute(f"DROP TABLE {_quote_identifier(table_name)}")
    connection.execute(
        f"ALTER TABLE {_quote_identifier(temp_table_name)} RENAME TO {_quote_identifier(table_name)}"
    )
    for index_row in preserved_indexes:
        index_sql = index_row["sql"]
        if index_sql:
            connection.execute(str(index_sql))


def _build_rebound_table_sql(table_sql: str, temp_table_name: str) -> str:
    rebuilt_sql = re.sub(
        r'^(CREATE TABLE\s+(?:IF NOT EXISTS\s+)?)("?[^\s(]+"?)',
        lambda match: f'{match.group(1)}{_quote_identifier(temp_table_name)}',
        table_sql,
        count=1,
        flags=re.IGNORECASE,
    )
    if rebuilt_sql == table_sql:
        raise RuntimeError(f"无法重建表定义：{table_sql}")
    return rebuilt_sql.replace('"batches_legacy"', "batches").replace("batches_legacy", "batches")


def _normalize_project_method_type(
    value: str | None,
    *,
    fallback: str = PROJECT_METHOD_LJ,
) -> str:
    normalized_value = str(value or "").strip().lower()
    if normalized_value in {PROJECT_METHOD_LJ, PROJECT_METHOD_ZSCORE}:
        return normalized_value
    return fallback


def _create_projects_table(connection: sqlite3.Connection, table_name: str = "projects") -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_quote_identifier(table_name)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            method_type TEXT NOT NULL DEFAULT '{PROJECT_METHOD_LJ}'
                CHECK (method_type IN ('{PROJECT_METHOD_LJ}', '{PROJECT_METHOD_ZSCORE}')),
            input_value_type TEXT NOT NULL DEFAULT 'raw'
                CHECK (input_value_type IN ('raw', 'ct', 'log')),
            is_disabled INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (method_type, name)
        )
        """
    )


def _get_existing_zscore_project_ids(connection: sqlite3.Connection) -> set[int]:
    table_exists = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name = 'zscore_project_config'
        """
    ).fetchone()
    if table_exists is None:
        return set()
    rows = connection.execute(
        """
        SELECT project_id
        FROM zscore_project_config
        """
    ).fetchall()
    return {int(row["project_id"]) for row in rows}


def _projects_table_needs_rebuild(connection: sqlite3.Connection) -> bool:
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(projects)").fetchall()
    }
    expected_columns = {"id", "name", "method_type", "input_value_type", "is_disabled", "created_at"}
    if existing_columns != expected_columns:
        return True

    table_row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'projects'
        """
    ).fetchone()
    table_sql = str(table_row["sql"] if table_row is not None else "")
    return re.search(r"unique\s*\(\s*method_type\s*,\s*name\s*\)", table_sql, flags=re.IGNORECASE) is None


def _rebuild_projects_table(connection: sqlite3.Connection) -> None:
    legacy_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(projects)").fetchall()
    }
    rows = connection.execute("SELECT * FROM projects ORDER BY id ASC").fetchall()
    zscore_project_ids = _get_existing_zscore_project_ids(connection)

    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute(f"DROP TABLE IF EXISTS {_quote_identifier('__new_projects')}")
        _create_projects_table(connection, "__new_projects")
        for row in rows:
            default_method_type = (
                PROJECT_METHOD_ZSCORE if int(row["id"]) in zscore_project_ids else PROJECT_METHOD_LJ
            )
            method_type = _normalize_project_method_type(
                row["method_type"] if "method_type" in legacy_columns else None,
                fallback=default_method_type,
            )
            input_value_type = normalize_input_value_type(
                row["input_value_type"] if "input_value_type" in legacy_columns else None
            )
            is_disabled = int(row["is_disabled"] if "is_disabled" in legacy_columns else 0)
            created_at = row["created_at"] if "created_at" in legacy_columns else None
            connection.execute(
                """
                INSERT INTO __new_projects (
                    id, name, method_type, input_value_type, is_disabled, created_at
                )
                VALUES (?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
                """,
                (
                    int(row["id"]),
                    str(row["name"]),
                    method_type,
                    input_value_type,
                    is_disabled,
                    created_at,
                ),
            )

        connection.execute("DROP TABLE projects")
        connection.execute("ALTER TABLE __new_projects RENAME TO projects")
        connection.commit()
    finally:
        connection.execute("PRAGMA foreign_keys = ON")

    foreign_key_issues = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_issues:
        raise RuntimeError(
            "projects 表迁移后仍存在外键检查错误："
            + "; ".join(str(tuple(issue)) for issue in foreign_key_issues[:10])
        )


def _ensure_projects_table(connection: sqlite3.Connection) -> None:
    table_exists = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'projects'"
    ).fetchone()
    if table_exists is None:
        _create_projects_table(connection)
        return

    if _projects_table_needs_rebuild(connection):
        _rebuild_projects_table(connection)

    connection.execute(
        """
        UPDATE projects
        SET input_value_type = ?
        WHERE input_value_type IS NULL
           OR LOWER(TRIM(input_value_type)) NOT IN ('raw', 'ct', 'log')
        """,
        (DEFAULT_INPUT_VALUE_TYPE,),
    )
    zscore_project_ids = _get_existing_zscore_project_ids(connection)
    if zscore_project_ids:
        placeholders = ", ".join("?" for _ in zscore_project_ids)
        connection.execute(
            f"""
            UPDATE projects
            SET method_type = ?
            WHERE id IN ({placeholders})
            """,
            (PROJECT_METHOD_ZSCORE, *zscore_project_ids),
        )
    connection.execute(
        """
        UPDATE projects
        SET method_type = ?
        WHERE method_type IS NULL
           OR LOWER(TRIM(method_type)) NOT IN (?, ?)
        """,
        (PROJECT_METHOD_LJ, PROJECT_METHOD_LJ, PROJECT_METHOD_ZSCORE),
    )


def _get_or_create_migration_project(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT id FROM projects WHERE name = ? AND method_type = ?",
        (MIGRATION_PROJECT_NAME, PROJECT_METHOD_LJ),
    ).fetchone()
    if row is not None:
        return int(row["id"])

    cursor = connection.execute(
        "INSERT INTO projects (name, method_type) VALUES (?, ?)",
        (MIGRATION_PROJECT_NAME, PROJECT_METHOD_LJ),
    )
    return int(cursor.lastrowid)


def _ensure_batches_table(connection: sqlite3.Connection) -> None:
    table_exists = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'batches'"
    ).fetchone()

    if table_exists is None:
        _create_batches_table(connection)
        return

    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(batches)").fetchall()
    }
    expected_columns = {
        "id",
        "project_id",
        "instrument",
        "reagent",
        "qc_material",
        "concentration",
        "lot_no",
        "target_n",
        "cv_limit",
        "is_disabled",
        "source_method",
        "source_instant_project_id",
        "source_instant_batch_id",
        "source_transfer_time",
        "created_at",
    }
    if existing_columns == expected_columns:
        return

    migration_project_id = _get_or_create_migration_project(connection)
    connection.execute("ALTER TABLE batches RENAME TO batches_legacy")
    _create_batches_table(connection)

    legacy_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(batches_legacy)").fetchall()
    }
    rows = connection.execute("SELECT * FROM batches_legacy ORDER BY id ASC").fetchall()
    for row in rows:
        instrument = row["instrument"] if "instrument" in legacy_columns else ""
        reagent = _legacy_value(row, legacy_columns, "reagent", "qc_category", default="")
        qc_material = _legacy_value(row, legacy_columns, "qc_material", "qc_category", default="")
        concentration = row["concentration"] if "concentration" in legacy_columns else ""
        lot_no = _legacy_value(row, legacy_columns, "lot_no", "lot_number", default="")
        target_n = int(_legacy_value(row, legacy_columns, "target_n", "target_count", default=10))
        cv_limit_raw = _legacy_value(row, legacy_columns, "cv_limit", default=None)
        cv_limit = None if cv_limit_raw in (None, "") else float(cv_limit_raw)
        is_disabled = int(_legacy_value(row, legacy_columns, "is_disabled", default=0) or 0)
        source_method = str(_legacy_value(row, legacy_columns, "source_method", default="") or "")
        source_instant_project_id = _legacy_value(row, legacy_columns, "source_instant_project_id", default=None)
        source_instant_batch_id = _legacy_value(row, legacy_columns, "source_instant_batch_id", default=None)
        source_transfer_time = _legacy_value(row, legacy_columns, "source_transfer_time", default=None)
        project_id = int(_legacy_value(row, legacy_columns, "project_id", default=migration_project_id))
        created_at = row["created_at"] if "created_at" in legacy_columns else None

        connection.execute(
            """
            INSERT INTO batches (
                id, project_id, instrument, reagent,
                qc_material, concentration, lot_no, target_n, cv_limit, is_disabled,
                source_method, source_instant_project_id, source_instant_batch_id, source_transfer_time,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
            """,
            (
                row["id"],
                project_id,
                instrument,
                reagent,
                qc_material,
                concentration,
                lot_no,
                target_n,
                cv_limit,
                is_disabled,
                source_method,
                source_instant_project_id,
                source_instant_batch_id,
                source_transfer_time,
                created_at,
            ),
        )

    connection.execute("DROP TABLE batches_legacy")


def _ensure_results_table(connection: sqlite3.Connection) -> None:
    table_exists = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'results'"
    ).fetchone()

    if table_exists is None:
        _create_results_table(connection)
        return

    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(results)").fetchall()
    }
    expected_columns = {
        "id",
        "batch_id",
        "test_time",
        "operator",
        "value",
        "log_value",
        "reagent_lot_changed",
        "is_building_included",
        "is_outlier_suspect",
        "outlier_status",
        "outlier_method",
        "grubbs_statistic",
        "grubbs_threshold",
        "manual_status",
        "handled_at",
        "manual_note",
        "created_at",
    }
    if existing_columns == expected_columns:
        return

    connection.execute("ALTER TABLE results RENAME TO results_legacy")
    _create_results_table(connection)

    legacy_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(results_legacy)").fetchall()
    }
    rows = connection.execute("SELECT * FROM results_legacy ORDER BY id ASC").fetchall()
    for row in rows:
        value = float(row["value"]) if "value" in legacy_columns else 0.0
        operator = _legacy_value(row, legacy_columns, "operator", default="")
        log_value = _legacy_value(
            row,
            legacy_columns,
            "log_value",
            default=_safe_log10(value),
        )
        reagent_lot_changed = int(_legacy_value(row, legacy_columns, "reagent_lot_changed", default=0))
        is_building_included = int(
            _legacy_value(row, legacy_columns, "is_building_included", default=1) or 0
        )
        is_outlier_suspect = int(
            _legacy_value(row, legacy_columns, "is_outlier_suspect", default=0) or 0
        )
        outlier_status = normalize_outlier_status(
            _legacy_value(row, legacy_columns, "outlier_status", default=OUTLIER_STATUS_NORMAL)
        )
        outlier_method = str(_legacy_value(row, legacy_columns, "outlier_method", default="") or "")
        grubbs_statistic = _legacy_value(row, legacy_columns, "grubbs_statistic", default=None)
        grubbs_threshold = _legacy_value(row, legacy_columns, "grubbs_threshold", default=None)
        manual_status = normalize_outlier_manual_status(
            _legacy_value(row, legacy_columns, "manual_status", default=OUTLIER_MANUAL_STATUS_NORMAL)
        )
        handled_at = _legacy_value(row, legacy_columns, "handled_at", default=None)
        manual_note = str(_legacy_value(row, legacy_columns, "manual_note", default="") or "")
        created_at = row["created_at"] if "created_at" in legacy_columns else None

        connection.execute(
            """
            INSERT INTO results (
                id, batch_id, test_time, operator, value, log_value, reagent_lot_changed,
                is_building_included, is_outlier_suspect, outlier_status, outlier_method,
                grubbs_statistic, grubbs_threshold, manual_status, handled_at, manual_note, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
            """,
            (
                row["id"],
                row["batch_id"],
                row["test_time"],
                operator,
                value,
                log_value,
                reagent_lot_changed,
                is_building_included,
                is_outlier_suspect,
                outlier_status,
                outlier_method,
                grubbs_statistic,
                grubbs_threshold,
                manual_status,
                handled_at,
                manual_note,
                created_at,
            ),
        )

    connection.execute("DROP TABLE results_legacy")


def _create_batches_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            instrument TEXT NOT NULL,
            reagent TEXT NOT NULL,
            qc_material TEXT NOT NULL,
            concentration TEXT NOT NULL,
            lot_no TEXT NOT NULL,
            target_n INTEGER NOT NULL CHECK (target_n BETWEEN 5 AND 20),
            cv_limit REAL,
            is_disabled INTEGER NOT NULL DEFAULT 0,
            source_method TEXT NOT NULL DEFAULT '',
            source_instant_project_id INTEGER,
            source_instant_batch_id INTEGER,
            source_transfer_time TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
        )
        """
    )


def _create_results_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            test_time TEXT NOT NULL,
            operator TEXT NOT NULL DEFAULT '',
            value REAL NOT NULL,
            log_value REAL,
            reagent_lot_changed INTEGER NOT NULL DEFAULT 0,
            is_building_included INTEGER NOT NULL DEFAULT 1,
            is_outlier_suspect INTEGER NOT NULL DEFAULT 0,
            outlier_status TEXT NOT NULL DEFAULT 'normal',
            outlier_method TEXT NOT NULL DEFAULT '',
            grubbs_statistic REAL,
            grubbs_threshold REAL,
            manual_status TEXT NOT NULL DEFAULT 'normal',
            handled_at TEXT,
            manual_note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (batch_id) REFERENCES batches (id) ON DELETE CASCADE
        )
        """
    )


def _ensure_instant_projects_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS instant_projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            input_value_type TEXT NOT NULL DEFAULT 'raw' CHECK (input_value_type IN ('raw', 'ct', 'log')),
            is_disabled INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(instant_projects)").fetchall()
    }
    if "input_value_type" not in existing_columns:
        connection.execute(
            f"""
            ALTER TABLE instant_projects
            ADD COLUMN input_value_type TEXT NOT NULL DEFAULT '{DEFAULT_INPUT_VALUE_TYPE}'
            """
        )
    if "is_disabled" not in existing_columns:
        connection.execute(
            "ALTER TABLE instant_projects ADD COLUMN is_disabled INTEGER NOT NULL DEFAULT 0"
        )
    connection.execute(
        """
        UPDATE instant_projects
        SET input_value_type = ?
        WHERE input_value_type IS NULL
           OR LOWER(TRIM(input_value_type)) NOT IN ('raw', 'ct', 'log')
        """,
        (DEFAULT_INPUT_VALUE_TYPE,),
    )


def _ensure_instant_batches_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS instant_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            input_value_type TEXT NOT NULL DEFAULT 'raw' CHECK (input_value_type IN ('raw', 'ct', 'log')),
            instrument TEXT NOT NULL,
            reagent TEXT NOT NULL,
            qc_material TEXT NOT NULL,
            concentration TEXT NOT NULL,
            lot_no TEXT NOT NULL,
            is_disabled INTEGER NOT NULL DEFAULT 0,
            lj_transfer_status TEXT NOT NULL DEFAULT 'pending',
            lj_transfer_target_batch_id INTEGER,
            lj_transfer_marked_at TEXT,
            transfer_status TEXT NOT NULL DEFAULT 'not_transferred',
            transferred_to_lj_project_id INTEGER,
            transferred_to_lj_batch_id INTEGER,
            transferred_at TEXT,
            transferred_effective_count INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES instant_projects (id) ON DELETE CASCADE
        )
        """
    )
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(instant_batches)").fetchall()
    }
    missing_columns = {
        "input_value_type": f"TEXT NOT NULL DEFAULT '{DEFAULT_INPUT_VALUE_TYPE}'",
        "is_disabled": "INTEGER NOT NULL DEFAULT 0",
        "lj_transfer_status": "TEXT NOT NULL DEFAULT 'pending'",
        "lj_transfer_target_batch_id": "INTEGER",
        "lj_transfer_marked_at": "TEXT",
        "transfer_status": "TEXT NOT NULL DEFAULT 'not_transferred'",
        "transferred_to_lj_project_id": "INTEGER",
        "transferred_to_lj_batch_id": "INTEGER",
        "transferred_at": "TEXT",
        "transferred_effective_count": "INTEGER",
    }
    for column_name, column_type in missing_columns.items():
        if column_name in existing_columns:
            continue
        connection.execute(f"ALTER TABLE instant_batches ADD COLUMN {column_name} {column_type}")
    connection.execute(
        """
        UPDATE instant_batches
        SET input_value_type = ?
        WHERE input_value_type IS NULL
           OR LOWER(TRIM(input_value_type)) NOT IN ('raw', 'ct', 'log')
        """,
        (DEFAULT_INPUT_VALUE_TYPE,),
    )
    if "transfer_status" in {row["name"] for row in connection.execute("PRAGMA table_info(instant_batches)").fetchall()}:
        connection.execute(
            """
            UPDATE instant_batches
            SET transfer_status = CASE
                WHEN LOWER(TRIM(COALESCE(transfer_status, ''))) IN ('transferred', 'not_transferred')
                    THEN LOWER(TRIM(transfer_status))
                WHEN LOWER(TRIM(COALESCE(lj_transfer_status, ''))) = 'transferred'
                    THEN 'transferred'
                ELSE 'not_transferred'
            END
            """
        )
        connection.execute(
            """
            UPDATE instant_batches
            SET transferred_to_lj_batch_id = COALESCE(transferred_to_lj_batch_id, lj_transfer_target_batch_id),
                transferred_at = COALESCE(transferred_at, lj_transfer_marked_at)
            """
        )


def _create_instant_results_table(connection: sqlite3.Connection, table_name: str = "instant_results") -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_quote_identifier(table_name)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            test_time TEXT NOT NULL,
            operator TEXT NOT NULL DEFAULT '',
            value REAL NOT NULL,
            log_value REAL,
            is_effective INTEGER NOT NULL DEFAULT 1,
            is_outlier_suspect INTEGER NOT NULL DEFAULT 0,
            outlier_method TEXT NOT NULL DEFAULT '',
            grubbs_statistic REAL,
            grubbs_threshold REAL,
            manual_status TEXT NOT NULL DEFAULT 'normal'
                CHECK (manual_status IN ('normal', 'pending_review', 'keep', 'disabled', 'restored')),
            manual_note TEXT NOT NULL DEFAULT '',
            lj_transfer_status TEXT NOT NULL DEFAULT 'pending',
            lj_transfer_target_batch_id INTEGER,
            lj_transfer_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (batch_id) REFERENCES instant_batches (id) ON DELETE CASCADE,
            FOREIGN KEY (project_id) REFERENCES instant_projects (id) ON DELETE CASCADE
        )
        """
    )


def _instant_results_table_needs_rebuild(connection: sqlite3.Connection) -> bool:
    table_row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'instant_results'
        """
    ).fetchone()
    if table_row is None:
        return False
    table_sql = str(table_row["sql"] or "")
    return "pending_review" not in table_sql or "'normal'" not in table_sql


def _rebuild_instant_results_table(connection: sqlite3.Connection) -> None:
    legacy_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(instant_results)").fetchall()
    }
    rows = connection.execute("SELECT * FROM instant_results ORDER BY id ASC").fetchall()

    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("ALTER TABLE instant_results RENAME TO instant_results_legacy")
        _create_instant_results_table(connection)
        for row in rows:
            raw_manual_status = str(_legacy_value(row, legacy_columns, "manual_status", default="normal") or "")
            normalized_manual_status = raw_manual_status.strip().lower()
            if normalized_manual_status in {"disabled", "restored"}:
                manual_status = normalized_manual_status
            elif normalized_manual_status in {"pending_review", "keep"}:
                manual_status = normalized_manual_status if normalized_manual_status == "pending_review" else "normal"
            else:
                manual_status = "normal"
            connection.execute(
                """
                INSERT INTO instant_results (
                    id,
                    batch_id,
                    project_id,
                    test_time,
                    operator,
                    value,
                    log_value,
                    is_effective,
                    is_outlier_suspect,
                    outlier_method,
                    grubbs_statistic,
                    grubbs_threshold,
                    manual_status,
                    manual_note,
                    lj_transfer_status,
                    lj_transfer_target_batch_id,
                    lj_transfer_at,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
                """,
                (
                    row["id"],
                    row["batch_id"],
                    int(_legacy_value(row, legacy_columns, "project_id", default=0) or 0),
                    row["test_time"],
                    str(_legacy_value(row, legacy_columns, "operator", default="") or ""),
                    float(row["value"]),
                    _legacy_value(row, legacy_columns, "log_value", default=None),
                    int(_legacy_value(row, legacy_columns, "is_effective", default=1) or 1),
                    int(_legacy_value(row, legacy_columns, "is_outlier_suspect", default=0) or 0),
                    str(_legacy_value(row, legacy_columns, "outlier_method", default="") or ""),
                    _legacy_value(row, legacy_columns, "grubbs_statistic", default=None),
                    _legacy_value(row, legacy_columns, "grubbs_threshold", default=None),
                    manual_status,
                    str(_legacy_value(row, legacy_columns, "manual_note", default="") or ""),
                    str(_legacy_value(row, legacy_columns, "lj_transfer_status", default="pending") or "pending"),
                    _legacy_value(row, legacy_columns, "lj_transfer_target_batch_id", default=None),
                    _legacy_value(row, legacy_columns, "lj_transfer_at", default=None),
                    _legacy_value(row, legacy_columns, "created_at", default=None),
                ),
            )
        connection.execute("DROP TABLE instant_results_legacy")
        connection.commit()
    finally:
        connection.execute("PRAGMA foreign_keys = ON")

    connection.execute(
        """
        UPDATE instant_results
        SET project_id = (
            SELECT project_id
            FROM instant_batches
            WHERE instant_batches.id = instant_results.batch_id
        )
        WHERE project_id IS NULL OR project_id = 0
        """
    )


def _ensure_instant_results_table(connection: sqlite3.Connection) -> None:
    _create_instant_results_table(connection)
    if _instant_results_table_needs_rebuild(connection):
        _rebuild_instant_results_table(connection)
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(instant_results)").fetchall()
    }
    missing_columns = {
        "project_id": "INTEGER NOT NULL DEFAULT 0",
        "operator": "TEXT NOT NULL DEFAULT ''",
        "log_value": "REAL",
        "is_effective": "INTEGER NOT NULL DEFAULT 1",
        "is_outlier_suspect": "INTEGER NOT NULL DEFAULT 0",
        "outlier_method": "TEXT NOT NULL DEFAULT ''",
        "grubbs_statistic": "REAL",
        "grubbs_threshold": "REAL",
        "manual_status": "TEXT NOT NULL DEFAULT 'normal'",
        "manual_note": "TEXT NOT NULL DEFAULT ''",
        "lj_transfer_status": "TEXT NOT NULL DEFAULT 'pending'",
        "lj_transfer_target_batch_id": "INTEGER",
        "lj_transfer_at": "TEXT",
    }
    for column_name, column_type in missing_columns.items():
        if column_name in existing_columns:
            continue
        connection.execute(f"ALTER TABLE instant_results ADD COLUMN {column_name} {column_type}")
    connection.execute(
        """
        UPDATE instant_results
        SET manual_status = CASE
            WHEN LOWER(TRIM(COALESCE(manual_status, ''))) IN ('disabled', 'restored', 'keep', 'pending_review', 'normal')
                THEN LOWER(TRIM(manual_status))
            ELSE 'normal'
        END
        """
    )
    connection.execute(
        """
        UPDATE instant_results
        SET manual_status = 'normal'
        WHERE manual_status IS NULL
           OR TRIM(manual_status) = ''
        """
    )
    connection.execute(
        """
        UPDATE instant_results
        SET project_id = (
            SELECT project_id
            FROM instant_batches
            WHERE instant_batches.id = instant_results.batch_id
        )
        WHERE project_id IS NULL OR project_id = 0
        """
    )


def _ensure_zscore_project_config_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS zscore_project_config (
            project_id INTEGER PRIMARY KEY,
            level_count INTEGER NOT NULL CHECK (level_count IN (2, 3)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
        )
        """
    )


def _ensure_zscore_batch_config_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS zscore_batch_config (
            batch_id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL,
            level_count INTEGER NOT NULL CHECK (level_count IN (2, 3)),
            level_1_label TEXT,
            level_2_label TEXT,
            level_3_label TEXT,
            effective_building_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (batch_id) REFERENCES batches (id) ON DELETE CASCADE,
            FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
        )
        """
    )
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(zscore_batch_config)").fetchall()
    }
    missing_columns = {
        "level_1_label": "TEXT",
        "level_2_label": "TEXT",
        "level_3_label": "TEXT",
        "effective_building_count": "INTEGER NOT NULL DEFAULT 0",
    }
    for column_name, column_type in missing_columns.items():
        if column_name in existing_columns:
            continue
        connection.execute(f"ALTER TABLE zscore_batch_config ADD COLUMN {column_name} {column_type}")


def _ensure_zscore_runs_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS zscore_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            test_sequence INTEGER,
            test_time TEXT NOT NULL,
            operator TEXT NOT NULL DEFAULT '',
            level_count INTEGER NOT NULL CHECK (level_count BETWEEN 1 AND 3),
            phase TEXT NOT NULL,
            run_status TEXT NOT NULL,
            rule_template_id TEXT NOT NULL,
            rule_hits_run TEXT NOT NULL DEFAULT '[]',
            error_type_hint TEXT NOT NULL DEFAULT 'unknown',
            analysis_prompt TEXT NOT NULL DEFAULT '',
            manual_note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (batch_id) REFERENCES batches (id) ON DELETE CASCADE,
            FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
        )
        """
    )
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(zscore_runs)").fetchall()
    }
    missing_columns = {
        "test_sequence": "INTEGER",
        "manual_note": "TEXT NOT NULL DEFAULT ''",
    }
    for column_name, column_type in missing_columns.items():
        if column_name in existing_columns:
            continue
        connection.execute(f"ALTER TABLE zscore_runs ADD COLUMN {column_name} {column_type}")
    _backfill_missing_zscore_test_sequences(connection)


def _backfill_missing_zscore_test_sequences(connection: sqlite3.Connection) -> None:
    batch_rows = connection.execute(
        """
        SELECT DISTINCT batch_id
        FROM zscore_runs
        ORDER BY batch_id ASC
        """
    ).fetchall()
    for batch_row in batch_rows:
        batch_id = int(batch_row["batch_id"])
        max_existing_sequence = connection.execute(
            """
            SELECT COALESCE(MAX(test_sequence), 0)
            FROM zscore_runs
            WHERE batch_id = ? AND test_sequence IS NOT NULL
            """,
            (batch_id,),
        ).fetchone()[0]
        next_sequence = int(max_existing_sequence or 0) + 1
        missing_rows = connection.execute(
            """
            SELECT id
            FROM zscore_runs
            WHERE batch_id = ? AND test_sequence IS NULL
            ORDER BY datetime(test_time) ASC, id ASC
            """,
            (batch_id,),
        ).fetchall()
        for row in missing_rows:
            connection.execute(
                """
                UPDATE zscore_runs
                SET test_sequence = ?
                WHERE id = ?
                """,
                (next_sequence, int(row["id"])),
            )
            next_sequence += 1


def _ensure_zscore_level_results_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS zscore_level_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            level_id TEXT NOT NULL,
            raw_value REAL NOT NULL,
            log_value REAL,
            zscore REAL,
            level_status TEXT NOT NULL,
            rule_hits_local TEXT NOT NULL DEFAULT '[]',
            is_in_control_for_realtime_stats INTEGER NOT NULL DEFAULT 0,
            is_building_included INTEGER NOT NULL DEFAULT 1,
            is_outlier_suspect INTEGER NOT NULL DEFAULT 0,
            outlier_status TEXT NOT NULL DEFAULT 'normal',
            outlier_method TEXT NOT NULL DEFAULT '',
            grubbs_statistic REAL,
            grubbs_threshold REAL,
            manual_status TEXT NOT NULL DEFAULT 'normal',
            handled_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES zscore_runs (id) ON DELETE CASCADE
        )
        """
    )
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(zscore_level_results)").fetchall()
    }
    missing_columns = {
        "is_building_included": "INTEGER NOT NULL DEFAULT 1",
        "is_outlier_suspect": "INTEGER NOT NULL DEFAULT 0",
        "outlier_status": "TEXT NOT NULL DEFAULT 'normal'",
        "outlier_method": "TEXT NOT NULL DEFAULT ''",
        "grubbs_statistic": "REAL",
        "grubbs_threshold": "REAL",
        "manual_status": "TEXT NOT NULL DEFAULT 'normal'",
        "handled_at": "TEXT",
    }
    for column_name, column_type in missing_columns.items():
        if column_name in existing_columns:
            continue
        connection.execute(f"ALTER TABLE zscore_level_results ADD COLUMN {column_name} {column_type}")
    connection.execute(
        """
        UPDATE zscore_level_results
        SET outlier_status = ?
        WHERE outlier_status IS NULL OR TRIM(outlier_status) = ''
        """,
        (OUTLIER_STATUS_NORMAL,),
    )
    connection.execute(
        """
        UPDATE zscore_level_results
        SET manual_status = ?
        WHERE manual_status IS NULL OR TRIM(manual_status) = ''
        """,
        (OUTLIER_MANUAL_STATUS_NORMAL,),
    )


def _ensure_zscore_level_targets_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS zscore_level_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            level_id TEXT NOT NULL,
            vendor_reference_mean REAL,
            vendor_reference_sd REAL,
            vendor_reference_cv REAL,
            vendor_reference_source_note TEXT,
            provisional_mean REAL,
            provisional_sd REAL,
            provisional_cv REAL,
            final_target_mean REAL,
            final_target_sd REAL,
            final_target_cv REAL,
            realtime_mean REAL,
            realtime_sd REAL,
            realtime_cv REAL,
            collected_n INTEGER NOT NULL DEFAULT 0,
            required_n INTEGER NOT NULL DEFAULT 5,
            is_ready INTEGER NOT NULL DEFAULT 0,
            phase TEXT NOT NULL DEFAULT 'target_building',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (batch_id, level_id),
            FOREIGN KEY (batch_id) REFERENCES batches (id) ON DELETE CASCADE
        )
        """
    )


def _ensure_report_exports_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS report_exports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_type TEXT NOT NULL,
            project_id INTEGER NOT NULL,
            batch_id INTEGER NOT NULL,
            report_month TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            input_value_type TEXT NOT NULL,
            method_label TEXT NOT NULL,
            summary_json TEXT NOT NULL DEFAULT '{}',
            monthly_mean REAL,
            monthly_sd REAL,
            monthly_cv REAL,
            target_mean REAL,
            target_sd REAL,
            formal_count INTEGER NOT NULL DEFAULT 0,
            in_control_count INTEGER NOT NULL DEFAULT 0,
            warning_count INTEGER NOT NULL DEFAULT 0,
            out_of_control_count INTEGER NOT NULL DEFAULT 0,
            file_name TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE,
            FOREIGN KEY (batch_id) REFERENCES batches (id) ON DELETE CASCADE
        )
        """
    )


def _ensure_app_settings_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _legacy_value(
    row: sqlite3.Row,
    legacy_columns: set[str],
    primary: str,
    fallback: str | None = None,
    default=None,
):
    if primary in legacy_columns:
        return row[primary]
    if fallback is not None and fallback in legacy_columns:
        return row[fallback]
    return default


def _safe_log10(value: float) -> float | None:
    if value <= 0:
        return None
    return math.log10(value)


def _format_optional_numeric(value, digits: int):
    if pd.isna(value) or value == "":
        return ""
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return value


def create_project(name: str, input_value_type: str = DEFAULT_INPUT_VALUE_TYPE) -> int:
    normalized_input_value_type = normalize_input_value_type(input_value_type)
    with get_connection() as connection:
        try:
            cursor = connection.execute(
                """
                INSERT INTO projects (name, method_type, input_value_type)
                VALUES (?, ?, ?)
                """,
                (name, PROJECT_METHOD_LJ, normalized_input_value_type),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("\u9879\u76ee\u540d\u79f0\u5df2\u5b58\u5728") from exc
        return int(cursor.lastrowid)


def update_project(project_id: int, name: str, input_value_type=_UNSET) -> None:
    with get_connection() as connection:
        assignments = ["name = ?"]
        values: list[object] = [name]
        if input_value_type is not _UNSET:
            assignments.append("input_value_type = ?")
            values.append(normalize_input_value_type(input_value_type))
        values.append(project_id)
        try:
            connection.execute(
                f"UPDATE projects SET {', '.join(assignments)} WHERE id = ?",
                tuple(values),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("\u9879\u76ee\u540d\u79f0\u5df2\u5b58\u5728") from exc


def set_project_disabled(project_id: int, is_disabled: int) -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE projects SET is_disabled = ? WHERE id = ?",
            (int(is_disabled), project_id),
        )


def list_projects(include_management_fields: bool = False) -> pd.DataFrame:
    select_columns = """
                projects.id,
                projects.name,
                projects.input_value_type,
                projects.created_at,
                CASE
                    WHEN EXISTS (
                        SELECT 1
                        FROM batches AS source_batches
                        WHERE source_batches.project_id = projects.id
                          AND LOWER(TRIM(source_batches.source_method)) = 'instant'
                    ) THEN 1
                    ELSE 0
                END AS is_from_instant
    """
    if include_management_fields:
        select_columns += ",\n                projects.is_disabled"
    with get_connection() as connection:
        dataframe = pd.read_sql_query(
            """
            SELECT {}
            FROM projects
            WHERE method_type = ?
            ORDER BY id DESC
            """.format(select_columns),
            connection,
            params=(PROJECT_METHOD_LJ,),
        )
    if include_management_fields and not dataframe.empty:
        dataframe["is_disabled"] = dataframe["is_disabled"].fillna(0).astype(int)
    return dataframe


def get_project(project_id: int) -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                projects.*,
                CASE
                    WHEN EXISTS (
                        SELECT 1
                        FROM batches AS source_batches
                        WHERE source_batches.project_id = projects.id
                          AND LOWER(TRIM(source_batches.source_method)) = 'instant'
                    ) THEN 1
                    ELSE 0
                END AS is_from_instant
            FROM projects
            WHERE id = ? AND method_type = ?
            """,
            (project_id, PROJECT_METHOD_LJ),
        ).fetchone()

    if row is None:
        raise ValueError(f"\u672a\u627e\u5230\u9879\u76ee {project_id}")
    return row


def count_project_batches(project_id: int) -> int:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(1) AS batch_count
            FROM batches
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
    return int(row["batch_count"] if row is not None else 0)


def _batch_lot_exists(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    project_id: int,
    lot_no: str,
    exclude_batch_id: int | None = None,
) -> bool:
    sql = f"""
        SELECT id
        FROM {_quote_identifier(table_name)}
        WHERE project_id = ?
          AND LOWER(TRIM(lot_no)) = LOWER(TRIM(?))
    """
    params: list[object] = [int(project_id), str(lot_no or "").strip()]
    if exclude_batch_id is not None:
        sql += " AND id <> ?"
        params.append(int(exclude_batch_id))
    sql += " LIMIT 1"
    return connection.execute(sql, tuple(params)).fetchone() is not None


def create_batch(
    instrument: str,
    reagent: str,
    qc_material: str,
    concentration: str,
    lot_no: str,
    target_n: int,
    project_id: int | None = None,
    level_1_label: str | None = None,
    level_2_label: str | None = None,
    level_3_label: str | None = None,
    cv_limit: float | None = None,
) -> int:
    if project_id is None:
        raise ValueError("\u8bf7\u5148\u9009\u62e9\u9879\u76ee")

    normalized_cv_limit = None if cv_limit in (None, "") else float(cv_limit)
    with get_connection() as connection:
        project_row = connection.execute(
            """
            SELECT method_type
            FROM projects
            WHERE id = ?
            """,
            (project_id,),
        ).fetchone()
        if project_row is None:
            raise ValueError("请先选择项目")
        if _normalize_project_method_type(project_row["method_type"]) != PROJECT_METHOD_LJ:
            raise ValueError("所选项目不是 LJ 项目")
        if _batch_lot_exists(connection, table_name="batches", project_id=project_id, lot_no=lot_no):
            raise ValueError("当前项目下已存在相同的质控品批号。")
        cursor = connection.execute(
            """
            INSERT INTO batches (
                project_id, instrument, reagent, qc_material, concentration, lot_no, target_n, cv_limit
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                instrument,
                reagent,
                qc_material,
                concentration,
                lot_no,
                target_n,
                normalized_cv_limit,
            ),
        )
        return int(cursor.lastrowid)


def update_batch(batch_id: int, lot_no: str, cv_limit=_UNSET) -> None:
    with get_connection() as connection:
        batch_row = connection.execute(
            """
            SELECT project_id
            FROM batches
            WHERE id = ?
            """,
            (batch_id,),
        ).fetchone()
        if batch_row is None:
            raise ValueError(f"未找到批次 {batch_id}")
        if _batch_lot_exists(
            connection,
            table_name="batches",
            project_id=int(batch_row["project_id"]),
            lot_no=lot_no,
            exclude_batch_id=batch_id,
        ):
            raise ValueError("当前项目下已存在相同的质控品批号。")
        assignments = ["lot_no = ?"]
        values: list[object] = [lot_no]
        if cv_limit is not _UNSET:
            normalized_cv_limit = None if cv_limit in (None, "") else float(cv_limit)
            assignments.append("cv_limit = ?")
            values.append(normalized_cv_limit)
        values.append(batch_id)
        connection.execute(
            f"UPDATE batches SET {', '.join(assignments)} WHERE id = ?",
            tuple(values),
        )


def set_batch_disabled(batch_id: int, is_disabled: int) -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE batches SET is_disabled = ? WHERE id = ?",
            (int(is_disabled), batch_id),
        )


def list_batches(
    project_id: int | None = None,
    include_management_fields: bool = False,
) -> pd.DataFrame:
    select_columns = """
                    batches.id,
                    batches.project_id,
                    projects.name AS project_name,
                    projects.input_value_type AS input_value_type,
                    batches.instrument,
                    batches.reagent,
                    batches.qc_material,
                    batches.concentration,
                    batches.lot_no,
                    batches.target_n,
                    batches.created_at,
                    batches.source_method,
                    batches.source_transfer_time
    """
    if include_management_fields:
        select_columns += ",\n                    batches.cv_limit,\n                    batches.is_disabled"
    with get_connection() as connection:
        if project_id is None:
            dataframe = pd.read_sql_query(
                """
                SELECT
                    {}
                FROM batches
                LEFT JOIN projects ON projects.id = batches.project_id
                ORDER BY batches.id DESC
                """.format(select_columns),
                connection,
            )
        else:
            dataframe = pd.read_sql_query(
                """
                SELECT
                    {}
                FROM batches
                LEFT JOIN projects ON projects.id = batches.project_id
                WHERE batches.project_id = ?
                ORDER BY batches.id DESC
                """.format(select_columns),
                connection,
                params=(project_id,),
            )
    if include_management_fields and not dataframe.empty:
        dataframe["is_disabled"] = dataframe["is_disabled"].fillna(0).astype(int)
    return dataframe


def get_batch(batch_id: int) -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                batches.*,
                COALESCE(v11_tests.chinese_name, projects.name) AS project_name,
                projects.input_value_type AS input_value_type,
                source_projects.name AS source_instant_project_name,
                source_batches.lot_no AS source_instant_batch_lot_no,
                v11_bindings.lot_config_id AS v11_lot_config_id,
                v11_bindings.lot_config_item_id AS v11_lot_config_item_id,
                v11_configs.config_name AS v11_config_name,
                v11_lots.expiry_date AS v11_expiry_date,
                v11_units.symbol AS unit_symbol,
                v11_methods.method_name AS method_name,
                v11_levels.target_source AS v11_target_source,
                v11_items.quality_target_source_text AS quality_target_source_text
            FROM batches
            LEFT JOIN projects ON projects.id = batches.project_id
            LEFT JOIN instant_projects AS source_projects
                ON source_projects.id = batches.source_instant_project_id
            LEFT JOIN instant_batches AS source_batches
                ON source_batches.id = batches.source_instant_batch_id
            LEFT JOIN qc_workbench_bindings AS v11_bindings
                ON v11_bindings.runtime_batch_id = batches.id
               AND v11_bindings.qc_method = 'lj'
            LEFT JOIN qc_lot_configs AS v11_configs
                ON v11_configs.id = v11_bindings.lot_config_id
            LEFT JOIN qc_lot_config_items AS v11_items
                ON v11_items.id = v11_bindings.lot_config_item_id
            LEFT JOIN md_test_items AS v11_tests ON v11_tests.id = v11_items.test_item_id
            LEFT JOIN md_qc_material_lots AS v11_lots
                ON v11_lots.id = v11_configs.qc_material_lot_id
            LEFT JOIN md_units AS v11_units ON v11_units.id = v11_items.unit_id
            LEFT JOIN md_methods AS v11_methods ON v11_methods.id = v11_items.method_id
            LEFT JOIN qc_lot_config_item_levels AS v11_levels
                ON v11_levels.lot_config_item_id = v11_items.id
               AND v11_levels.is_disabled = 0
            WHERE batches.id = ?
            """,
            (batch_id,),
        ).fetchone()

    if row is None:
        raise ValueError(f"\u672a\u627e\u5230\u6279\u6b21 {batch_id}")
    return row


def create_report_export_snapshot(
    *,
    report_type: str,
    project_id: int,
    batch_id: int,
    report_month: str,
    generated_at: str,
    input_value_type: str,
    method_label: str,
    summary: dict[str, object],
    file_name: str,
) -> int:
    summary_payload = dict(summary or {})
    statistics = summary_payload.get("statistics")
    if not isinstance(statistics, dict):
        statistics = {}
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO report_exports (
                report_type,
                project_id,
                batch_id,
                report_month,
                generated_at,
                input_value_type,
                method_label,
                summary_json,
                monthly_mean,
                monthly_sd,
                monthly_cv,
                target_mean,
                target_sd,
                formal_count,
                in_control_count,
                warning_count,
                out_of_control_count,
                file_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(report_type or "").strip(),
                int(project_id),
                int(batch_id),
                str(report_month or "").strip(),
                str(generated_at or "").strip(),
                normalize_input_value_type(input_value_type),
                str(method_label or "").strip(),
                json.dumps(summary_payload, ensure_ascii=False),
                statistics.get("monthly_mean"),
                statistics.get("monthly_sd"),
                statistics.get("monthly_cv"),
                statistics.get("target_mean"),
                statistics.get("target_sd"),
                int(statistics.get("formal_count", 0) or 0),
                int(statistics.get("in_control_count", 0) or 0),
                int(statistics.get("warning_count", 0) or 0),
                int(statistics.get("out_of_control_count", 0) or 0),
                str(file_name or "").strip(),
            ),
        )
        return int(cursor.lastrowid)


def list_report_exports(
    *,
    report_type: str | None = None,
    batch_id: int | None = None,
) -> pd.DataFrame:
    clauses: list[str] = []
    params: list[object] = []
    if report_type is not None:
        clauses.append("report_type = ?")
        params.append(str(report_type))
    if batch_id is not None:
        clauses.append("batch_id = ?")
        params.append(int(batch_id))
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as connection:
        dataframe = pd.read_sql_query(
            f"""
            SELECT
                id,
                report_type,
                project_id,
                batch_id,
                report_month,
                generated_at,
                input_value_type,
                method_label,
                summary_json,
                monthly_mean,
                monthly_sd,
                monthly_cv,
                target_mean,
                target_sd,
                formal_count,
                in_control_count,
                warning_count,
                out_of_control_count,
                file_name,
                created_at
            FROM report_exports
            {where_clause}
            ORDER BY datetime(generated_at) DESC, id DESC
            """,
            connection,
            params=tuple(params),
        )
    if dataframe.empty:
        return dataframe
    dataframe["generated_at"] = pd.to_datetime(dataframe["generated_at"], errors="coerce")
    dataframe["created_at"] = pd.to_datetime(dataframe["created_at"], errors="coerce")
    dataframe["summary_json"] = dataframe["summary_json"].map(
        lambda value: json.loads(value) if str(value or "").strip() else {}
    )
    return dataframe


def get_app_settings(keys: list[str] | tuple[str, ...] | None = None) -> dict[str, str]:
    query = "SELECT key, value FROM app_settings"
    params: list[object] = []
    if keys:
        placeholders = ", ".join("?" for _ in keys)
        query += f" WHERE key IN ({placeholders})"
        params.extend(str(key) for key in keys)

    with get_connection() as connection:
        rows = connection.execute(query, params).fetchall()
    return {str(row["key"]): str(row["value"] or "") for row in rows}


def save_app_settings(settings: dict[str, object]) -> None:
    normalized_items = [
        (str(key).strip(), str(value or "").strip())
        for key, value in dict(settings or {}).items()
        if str(key).strip()
    ]
    if not normalized_items:
        return

    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            normalized_items,
        )


def create_instant_project(name: str, input_value_type: str = DEFAULT_INPUT_VALUE_TYPE) -> int:
    normalized_input_value_type = normalize_input_value_type(input_value_type)
    with get_connection() as connection:
        try:
            cursor = connection.execute(
                """
                INSERT INTO instant_projects (name, input_value_type)
                VALUES (?, ?)
                """,
                (name, normalized_input_value_type),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("项目名称已存在") from exc
        return int(cursor.lastrowid)


def count_instant_project_batches(project_id: int) -> int:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(1) AS batch_count
            FROM instant_batches
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
    return int(row["batch_count"] if row is not None else 0)


def update_instant_project(
    project_id: int,
    name: str,
    input_value_type=_UNSET,
) -> None:
    with get_connection() as connection:
        assignments = ["name = ?"]
        values: list[object] = [name]
        if input_value_type is not _UNSET:
            assignments.append("input_value_type = ?")
            values.append(normalize_input_value_type(input_value_type))
        values.append(project_id)
        try:
            connection.execute(
                f"UPDATE instant_projects SET {', '.join(assignments)} WHERE id = ?",
                tuple(values),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("项目名称已存在") from exc


def list_instant_projects(include_management_fields: bool = False) -> pd.DataFrame:
    select_columns = "id, name, input_value_type, created_at"
    if include_management_fields:
        select_columns += ", is_disabled"
    with get_connection() as connection:
        dataframe = pd.read_sql_query(
            """
            SELECT {}
            FROM instant_projects
            ORDER BY id DESC
            """.format(select_columns),
            connection,
        )
    if include_management_fields and not dataframe.empty:
        dataframe["is_disabled"] = dataframe["is_disabled"].fillna(0).astype(int)
    return dataframe


def get_instant_project(project_id: int) -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM instant_projects WHERE id = ?",
            (project_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"未找到即时法项目 {project_id}")
    return row


def create_instant_batch(
    *,
    project_id: int,
    instrument: str,
    reagent: str,
    qc_material: str,
    concentration: str,
    lot_no: str,
) -> int:
    with get_connection() as connection:
        project_row = connection.execute(
            """
            SELECT input_value_type
            FROM instant_projects
            WHERE id = ?
            """,
            (project_id,),
        ).fetchone()
        if project_row is None:
            raise ValueError("请先选择项目")
        normalized_input_value_type = normalize_input_value_type(project_row["input_value_type"])
        if _batch_lot_exists(connection, table_name="instant_batches", project_id=project_id, lot_no=lot_no):
            raise ValueError("当前项目下已存在相同的质控品批号。")
        cursor = connection.execute(
            """
            INSERT INTO instant_batches (
                project_id, input_value_type, instrument, reagent, qc_material, concentration, lot_no
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                normalized_input_value_type,
                instrument,
                reagent,
                qc_material,
                concentration,
                lot_no,
            ),
        )
        return int(cursor.lastrowid)


def update_instant_batch(batch_id: int, lot_no: str) -> None:
    with get_connection() as connection:
        batch_row = connection.execute(
            """
            SELECT project_id
            FROM instant_batches
            WHERE id = ?
            """,
            (batch_id,),
        ).fetchone()
        if batch_row is None:
            raise ValueError(f"未找到即时法批次 {batch_id}")
        if _batch_lot_exists(
            connection,
            table_name="instant_batches",
            project_id=int(batch_row["project_id"]),
            lot_no=lot_no,
            exclude_batch_id=batch_id,
        ):
            raise ValueError("当前项目下已存在相同的质控品批号。")
        connection.execute(
            """
            UPDATE instant_batches
            SET lot_no = ?
            WHERE id = ?
            """,
            (lot_no, batch_id),
        )


def list_instant_batches(
    project_id: int | None = None,
    include_management_fields: bool = False,
) -> pd.DataFrame:
    select_columns = """
                    instant_batches.id,
                    instant_batches.project_id,
                    instant_projects.name AS project_name,
                    instant_batches.input_value_type AS input_value_type,
                    instant_batches.instrument,
                    instant_batches.reagent,
                    instant_batches.qc_material,
                    instant_batches.concentration,
                    instant_batches.lot_no,
                    instant_batches.created_at
    """
    if include_management_fields:
        select_columns += ",\n                    instant_batches.is_disabled"
    with get_connection() as connection:
        if project_id is None:
            dataframe = pd.read_sql_query(
                """
                SELECT
                    {}
                FROM instant_batches
                LEFT JOIN instant_projects ON instant_projects.id = instant_batches.project_id
                ORDER BY instant_batches.id DESC
                """.format(select_columns),
                connection,
            )
        else:
            dataframe = pd.read_sql_query(
                """
                SELECT
                    {}
                FROM instant_batches
                LEFT JOIN instant_projects ON instant_projects.id = instant_batches.project_id
                WHERE instant_batches.project_id = ?
                ORDER BY instant_batches.id DESC
                """.format(select_columns),
                connection,
                params=(project_id,),
            )
    if include_management_fields and not dataframe.empty:
        dataframe["is_disabled"] = dataframe["is_disabled"].fillna(0).astype(int)
    return dataframe


def get_instant_batch(batch_id: int) -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                instant_batches.*,
                instant_projects.name AS project_name,
                target_projects.name AS transferred_to_lj_project_name,
                target_batches.lot_no AS transferred_to_lj_batch_lot_no
            FROM instant_batches
            LEFT JOIN instant_projects ON instant_projects.id = instant_batches.project_id
            LEFT JOIN projects AS target_projects
                ON target_projects.id = instant_batches.transferred_to_lj_project_id
            LEFT JOIN batches AS target_batches
                ON target_batches.id = instant_batches.transferred_to_lj_batch_id
            WHERE instant_batches.id = ?
            """,
            (batch_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"未找到即时法批次 {batch_id}")
    return row


def list_zscore_projects(include_management_fields: bool = False) -> pd.DataFrame:
    select_columns = """
                projects.id,
                projects.name,
                projects.input_value_type,
                projects.created_at,
                config.level_count
    """
    if include_management_fields:
        select_columns += ",\n                projects.is_disabled"
    with get_connection() as connection:
        dataframe = pd.read_sql_query(
            """
            SELECT
                {}
            FROM projects
            INNER JOIN zscore_project_config AS config
                ON config.project_id = projects.id
            WHERE projects.method_type = ?
            ORDER BY projects.id DESC
            """.format(select_columns),
            connection,
            params=(PROJECT_METHOD_ZSCORE,),
        )

    if not dataframe.empty:
        dataframe["level_count"] = dataframe["level_count"].astype(int)
        if include_management_fields:
            dataframe["is_disabled"] = dataframe["is_disabled"].fillna(0).astype(int)
    return dataframe


def get_zscore_project(project_id: int) -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                projects.*,
                config.level_count
            FROM projects
            INNER JOIN zscore_project_config AS config
                ON config.project_id = projects.id
            WHERE projects.id = ? AND projects.method_type = ?
            """,
            (project_id, PROJECT_METHOD_ZSCORE),
        ).fetchone()

    if row is None:
        raise ValueError(f"未找到 Z-score 项目 {project_id}")
    return row


def list_zscore_batches(
    project_id: int | None = None,
    include_management_fields: bool = False,
) -> pd.DataFrame:
    select_columns = """
                    batches.id,
                    batches.project_id,
                    projects.name AS project_name,
                    projects.input_value_type AS input_value_type,
                    batches.instrument,
                    batches.reagent,
                    batches.qc_material,
                    batches.concentration,
                    batches.lot_no,
                    batches.target_n,
                    batches.created_at,
                    config.level_count,
                    config.level_1_label,
                    config.level_2_label,
                    config.level_3_label
    """
    if include_management_fields:
        select_columns += ",\n                    batches.cv_limit,\n                    batches.is_disabled"
    with get_connection() as connection:
        if project_id is None:
            dataframe = pd.read_sql_query(
                """
                SELECT
                    {}
                FROM batches
                INNER JOIN zscore_batch_config AS config ON config.batch_id = batches.id
                LEFT JOIN projects ON projects.id = batches.project_id
                ORDER BY batches.id DESC
                """.format(select_columns),
                connection,
            )
        else:
            dataframe = pd.read_sql_query(
                """
                SELECT
                    {}
                FROM batches
                INNER JOIN zscore_batch_config AS config ON config.batch_id = batches.id
                LEFT JOIN projects ON projects.id = batches.project_id
                WHERE batches.project_id = ?
                ORDER BY batches.id DESC
                """.format(select_columns),
                connection,
                params=(project_id,),
            )

    if not dataframe.empty:
        dataframe["level_count"] = dataframe["level_count"].astype(int)
        if include_management_fields:
            dataframe["is_disabled"] = dataframe["is_disabled"].fillna(0).astype(int)
    return dataframe


def get_zscore_batch(batch_id: int) -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                batches.*,
                projects.name AS project_name,
                projects.input_value_type AS input_value_type,
                config.level_count,
                config.level_1_label,
                config.level_2_label,
                config.level_3_label,
                config.effective_building_count
            FROM batches
            INNER JOIN zscore_batch_config AS config ON config.batch_id = batches.id
            LEFT JOIN projects ON projects.id = batches.project_id
            WHERE batches.id = ?
            """,
            (batch_id,),
        ).fetchone()

    if row is None:
        raise ValueError(f"未找到 Z-score 批次 {batch_id}")
    return row


def add_result(
    batch_id: int,
    test_time: str,
    value: float,
    operator: str = "",
    log_value=_UNSET,
    reagent_lot_changed: int = 0,
    manual_note: str = "",
) -> None:
    with get_connection() as connection:
        if log_value is _UNSET:
            log_value = _safe_log10(value)
        connection.execute(
            """
            INSERT INTO results (
                batch_id, test_time, operator, value, log_value, reagent_lot_changed, manual_note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                test_time,
                operator,
                value,
                log_value,
                int(reagent_lot_changed),
                str(manual_note or ""),
            ),
        )


def update_result(
    result_id: int,
    test_time: str,
    value: float,
    operator: str = "",
    log_value=_UNSET,
    reagent_lot_changed: int = 0,
    manual_note: str | None = None,
) -> None:
    with get_connection() as connection:
        if log_value is _UNSET:
            log_value = _safe_log10(value)
        assignments = [
            "test_time = ?",
            "operator = ?",
            "value = ?",
            "log_value = ?",
            "reagent_lot_changed = ?",
        ]
        values: list[object] = [
            test_time,
            operator,
            value,
            log_value,
            int(reagent_lot_changed),
        ]
        if manual_note is not None:
            assignments.append("manual_note = ?")
            values.append(str(manual_note or ""))
        values.append(result_id)
        cursor = connection.execute(
            f"""
            UPDATE results
            SET {", ".join(assignments)}
            WHERE id = ?
            """,
            tuple(values),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"未找到检测记录 {result_id}")


def delete_result(result_id: int) -> None:
    with get_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM results WHERE id = ?",
            (result_id,),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"未找到检测记录 {result_id}")


def get_result(result_id: int) -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM results
            WHERE id = ?
            """,
            (result_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"未找到检测记录 {result_id}")
    return row


def get_results(batch_id: int, include_manual_note: bool = False) -> pd.DataFrame:
    select_columns = """
                id,
                batch_id,
                test_time,
                operator,
                value,
                log_value,
                reagent_lot_changed,
                is_building_included,
                is_outlier_suspect,
                outlier_status,
                outlier_method,
                grubbs_statistic,
                grubbs_threshold,
                manual_status,
                handled_at,
                created_at
    """
    if include_manual_note:
        select_columns += ",\n                manual_note"
    with get_connection() as connection:
        dataframe = pd.read_sql_query(
            """
            SELECT
                {}
            FROM results
            WHERE batch_id = ?
            ORDER BY datetime(test_time) ASC, id ASC
            """.format(select_columns),
            connection,
            params=(batch_id,),
        )

    if not dataframe.empty:
        dataframe["test_time"] = pd.to_datetime(dataframe["test_time"])
        dataframe["created_at"] = pd.to_datetime(dataframe["created_at"])
        dataframe["reagent_lot_changed"] = dataframe["reagent_lot_changed"].fillna(0).astype(int)
        dataframe["is_building_included"] = dataframe["is_building_included"].fillna(1).astype(int)
        dataframe["is_outlier_suspect"] = dataframe["is_outlier_suspect"].fillna(0).astype(int)
        dataframe["outlier_status"] = dataframe["outlier_status"].map(
            lambda value: normalize_outlier_status(value, fallback=OUTLIER_STATUS_NORMAL)
        )
        dataframe["outlier_method"] = dataframe["outlier_method"].fillna("")
        dataframe["manual_status"] = dataframe["manual_status"].map(
            lambda value: normalize_outlier_manual_status(value, fallback=OUTLIER_MANUAL_STATUS_NORMAL)
        )
        dataframe["handled_at"] = pd.to_datetime(dataframe["handled_at"], errors="coerce")
        if include_manual_note:
            dataframe["manual_note"] = dataframe["manual_note"].fillna("")
    return dataframe


def set_result_building_inclusion_state(
    result_id: int,
    *,
    is_building_included: int,
    manual_status: str,
    handled_at: str | None = None,
) -> None:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE results
            SET is_building_included = ?,
                manual_status = ?,
                handled_at = ?
            WHERE id = ?
            """,
            (
                int(is_building_included),
                normalize_outlier_manual_status(manual_status),
                handled_at,
                int(result_id),
            ),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"未找到检测记录 {result_id}")


def _normalize_snapshot_float(value) -> float | None:
    if value is None:
        return None
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    return numeric_value if math.isfinite(numeric_value) else None


def _snapshot_float_equal(left, right) -> bool:
    left_value = _normalize_snapshot_float(left)
    right_value = _normalize_snapshot_float(right)
    if left_value is None or right_value is None:
        return left_value is None and right_value is None
    return math.isclose(left_value, right_value, rel_tol=1e-12, abs_tol=1e-12)


def save_result_outlier_snapshot(batch_id: int, analysis_rows: list[dict[str, object]]) -> None:
    if not analysis_rows:
        return

    with get_connection() as connection:
        current_rows = connection.execute(
            """
            SELECT id,
                   is_outlier_suspect,
                   outlier_status,
                   outlier_method,
                   grubbs_statistic,
                   grubbs_threshold
            FROM results
            WHERE batch_id = ?
            """,
            (int(batch_id),),
        ).fetchall()
        current_by_id = {int(row["id"]): row for row in current_rows}

        update_rows = []
        for analysis_row in analysis_rows:
            result_id = int(analysis_row["id"])
            current_row = current_by_id.get(result_id)
            if current_row is None:
                continue

            next_is_suspect = int(analysis_row.get("is_outlier_suspect", 0) or 0)
            next_status = normalize_outlier_status(analysis_row.get("outlier_status"))
            next_method = str(analysis_row.get("outlier_method", "") or "")
            next_statistic = _normalize_snapshot_float(analysis_row.get("grubbs_statistic"))
            next_threshold = _normalize_snapshot_float(analysis_row.get("grubbs_threshold"))

            if (
                int(current_row["is_outlier_suspect"] or 0) == next_is_suspect
                and normalize_outlier_status(current_row["outlier_status"]) == next_status
                and str(current_row["outlier_method"] or "") == next_method
                and _snapshot_float_equal(current_row["grubbs_statistic"], next_statistic)
                and _snapshot_float_equal(current_row["grubbs_threshold"], next_threshold)
            ):
                continue

            update_rows.append(
                (
                    next_is_suspect,
                    next_status,
                    next_method,
                    next_statistic,
                    next_threshold,
                    result_id,
                    int(batch_id),
                )
            )

        if not update_rows:
            return

        connection.executemany(
            """
            UPDATE results
            SET is_outlier_suspect = ?,
                outlier_status = ?,
                outlier_method = ?,
                grubbs_statistic = ?,
                grubbs_threshold = ?
            WHERE id = ? AND batch_id = ?
            """,
            update_rows,
        )


def save_zscore_level_outlier_snapshot(batch_id: int, analysis_rows: list[dict[str, object]]) -> None:
    if not analysis_rows:
        return

    with get_connection() as connection:
        current_rows = connection.execute(
            """
            SELECT level_results.id,
                   level_results.is_outlier_suspect,
                   level_results.outlier_status,
                   level_results.outlier_method,
                   level_results.grubbs_statistic,
                   level_results.grubbs_threshold
            FROM zscore_level_results AS level_results
            INNER JOIN zscore_runs AS runs ON runs.id = level_results.run_id
            WHERE runs.batch_id = ?
            """,
            (int(batch_id),),
        ).fetchall()
        current_by_id = {int(row["id"]): row for row in current_rows}

        update_rows = []
        for analysis_row in analysis_rows:
            level_result_id = int(analysis_row["id"])
            current_row = current_by_id.get(level_result_id)
            if current_row is None:
                continue

            next_is_suspect = int(analysis_row.get("is_outlier_suspect", 0) or 0)
            next_status = normalize_outlier_status(analysis_row.get("outlier_status"))
            next_method = str(analysis_row.get("outlier_method", "") or "")
            next_statistic = _normalize_snapshot_float(analysis_row.get("grubbs_statistic"))
            next_threshold = _normalize_snapshot_float(analysis_row.get("grubbs_threshold"))

            if (
                int(current_row["is_outlier_suspect"] or 0) == next_is_suspect
                and normalize_outlier_status(current_row["outlier_status"]) == next_status
                and str(current_row["outlier_method"] or "") == next_method
                and _snapshot_float_equal(current_row["grubbs_statistic"], next_statistic)
                and _snapshot_float_equal(current_row["grubbs_threshold"], next_threshold)
            ):
                continue

            update_rows.append(
                (
                    next_is_suspect,
                    next_status,
                    next_method,
                    next_statistic,
                    next_threshold,
                    level_result_id,
                )
            )

        if not update_rows:
            return

        connection.executemany(
            """
            UPDATE zscore_level_results
            SET is_outlier_suspect = ?,
                outlier_status = ?,
                outlier_method = ?,
                grubbs_statistic = ?,
                grubbs_threshold = ?
            WHERE id = ?
            """,
            update_rows,
        )


def add_instant_result(
    *,
    batch_id: int,
    test_time: str,
    value: float,
    operator: str = "",
    log_value=_UNSET,
    manual_status: str = "normal",
    manual_note: str = "",
) -> int:
    with get_connection() as connection:
        batch_row = connection.execute(
            """
            SELECT project_id,
                   transfer_status
            FROM instant_batches
            WHERE id = ?
            """,
            (batch_id,),
        ).fetchone()
        if batch_row is None:
            raise ValueError(f"未找到即时法批次 {batch_id}")
        if str(batch_row["transfer_status"] or "not_transferred").strip().lower() == "transferred":
            raise ValueError("该即时法批次已转入 LJ 法，当前批次已冻结为只读。")
        if log_value is _UNSET:
            log_value = _safe_log10(value)
        cursor = connection.execute(
            """
            INSERT INTO instant_results (
                batch_id, project_id, test_time, operator, value, log_value, manual_status, manual_note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                int(batch_row["project_id"]),
                test_time,
                operator,
                value,
                log_value,
                str(manual_status or "normal"),
                str(manual_note or ""),
            ),
        )
        return int(cursor.lastrowid)


def get_instant_result(result_id: int) -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM instant_results
            WHERE id = ?
            """,
            (result_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"未找到即时法检测记录 {result_id}")
    return row


def set_instant_result_effective_state(
    result_id: int,
    *,
    is_effective: int,
    manual_status: str,
) -> None:
    with get_connection() as connection:
        result_row = connection.execute(
            """
            SELECT instant_results.batch_id,
                   instant_batches.transfer_status
            FROM instant_results
            INNER JOIN instant_batches ON instant_batches.id = instant_results.batch_id
            WHERE instant_results.id = ?
            """,
            (result_id,),
        ).fetchone()
        if result_row is None:
            raise ValueError(f"未找到即时法检测记录 {result_id}")
        if str(result_row["transfer_status"] or "not_transferred").strip().lower() == "transferred":
            raise ValueError("该即时法批次已转入 LJ 法，当前批次已冻结为只读。")
        cursor = connection.execute(
            """
            UPDATE instant_results
            SET is_effective = ?,
                manual_status = ?
            WHERE id = ?
            """,
            (int(is_effective), str(manual_status or "normal"), result_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"未找到即时法检测记录 {result_id}")


def save_instant_result_analysis_snapshot(
    batch_id: int,
    analysis_rows: list[dict[str, object]],
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE instant_results
            SET is_outlier_suspect = 0,
                outlier_method = '',
                grubbs_statistic = NULL,
                grubbs_threshold = NULL,
                manual_status = CASE
                    WHEN manual_status = 'pending_review' THEN 'normal'
                    ELSE manual_status
                END
            WHERE batch_id = ? AND is_effective = 1
            """,
            (batch_id,),
        )
        for analysis_row in analysis_rows:
            connection.execute(
                """
                UPDATE instant_results
                SET is_outlier_suspect = ?,
                    outlier_method = ?,
                    grubbs_statistic = ?,
                    grubbs_threshold = ?,
                    manual_status = CASE
                        WHEN ? = 1 AND manual_status = 'normal' THEN 'pending_review'
                        WHEN ? = 0 AND manual_status = 'pending_review' THEN 'normal'
                        ELSE manual_status
                    END
                WHERE id = ?
                """,
                (
                    int(analysis_row.get("is_outlier_suspect", 0) or 0),
                    str(analysis_row.get("outlier_method", "") or ""),
                    analysis_row.get("grubbs_statistic"),
                    analysis_row.get("grubbs_threshold"),
                    int(analysis_row.get("is_outlier_suspect", 0) or 0),
                    int(analysis_row.get("is_outlier_suspect", 0) or 0),
                    int(analysis_row["id"]),
                ),
            )


def get_instant_results(batch_id: int, include_manual_note: bool = True) -> pd.DataFrame:
    select_columns = """
                id,
                batch_id,
                project_id,
                test_time,
                operator,
                value,
                log_value,
                is_effective,
                is_outlier_suspect,
                outlier_method,
                grubbs_statistic,
                grubbs_threshold,
                manual_status,
                created_at,
                lj_transfer_status,
                lj_transfer_target_batch_id,
                lj_transfer_at
    """
    if include_manual_note:
        select_columns += ",\n                manual_note"
    with get_connection() as connection:
        dataframe = pd.read_sql_query(
            """
            SELECT
                {}
            FROM instant_results
            WHERE batch_id = ?
            ORDER BY datetime(test_time) ASC, id ASC
            """.format(select_columns),
            connection,
            params=(batch_id,),
        )
    if not dataframe.empty:
        dataframe["test_time"] = pd.to_datetime(dataframe["test_time"])
        dataframe["created_at"] = pd.to_datetime(dataframe["created_at"])
        dataframe["is_effective"] = dataframe["is_effective"].fillna(1).astype(int)
        dataframe["is_outlier_suspect"] = dataframe["is_outlier_suspect"].fillna(0).astype(int)
        dataframe["manual_status"] = dataframe["manual_status"].fillna("normal")
        if include_manual_note:
            dataframe["manual_note"] = dataframe["manual_note"].fillna("")
    return dataframe


LJ_BUILDING_PHASE_LABEL = "建靶数据"
LJ_FORMAL_PHASE_LABEL = "正式数据"
LJ_EXPORT_METADATA_COLUMNS = [
    "batch_id",
    "project_id",
    "project_name",
    "instrument",
    "reagent",
    "qc_material",
    "concentration",
    "lot_no",
    "target_n",
]
LJ_BUILDING_EXPORT_COLUMNS = LJ_EXPORT_METADATA_COLUMNS + [
    "sequence",
    "test_time",
    "operator",
    "value",
    "log_value",
    "reagent_lot_changed",
    "manual_note",
    "phase",
]
LJ_FORMAL_EXPORT_COLUMNS = LJ_EXPORT_METADATA_COLUMNS + [
    "sequence",
    "test_time",
    "operator",
    "value",
    "log_value",
    "z",
    "status",
    "rule_hits",
    "error_type",
    "analysis_prompt",
    "manual_note",
    "phase",
]


def export_batch_results(
    batch: sqlite3.Row,
    qc_df: pd.DataFrame,
    included_columns: list[str] | None = None,
) -> pd.DataFrame:
    export_df = qc_df.copy()
    if export_df.empty:
        export_df = pd.DataFrame(
            columns=[
                "test_time",
                "operator",
                "value",
                "log_value",
                "reagent_lot_changed",
                "sequence",
                "phase",
                "z",
                "status",
                "rule_hits",
                "error_type",
                "analysis_prompt",
                "manual_note",
            ]
        )

    export_df["test_time"] = pd.to_datetime(export_df["test_time"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    if "z" in export_df.columns:
        export_df["z"] = export_df["z"].map(lambda value: _format_optional_numeric(value, 4))
    if "log_value" in export_df.columns:
        export_df["log_value"] = export_df["log_value"].map(lambda value: _format_optional_numeric(value, 6))

    metadata_columns = {
        "batch_id": batch["id"],
        "project_id": batch["project_id"],
        "project_name": batch["project_name"],
        "instrument": batch["instrument"],
        "reagent": batch["reagent"],
        "qc_material": batch["qc_material"],
        "concentration": batch["concentration"],
        "lot_no": batch["lot_no"],
        "target_n": batch["target_n"],
    }
    for column_name, column_value in metadata_columns.items():
        export_df[column_name] = column_value

    ordered_prefix = list(metadata_columns.keys())
    remaining_columns = [column for column in export_df.columns if column not in ordered_prefix]
    ordered_df = export_df[ordered_prefix + remaining_columns]
    if included_columns is not None:
        selected_columns = [column for column in included_columns if column in ordered_df.columns]
        ordered_df = ordered_df[selected_columns]
    input_value_type = normalize_input_value_type(batch["input_value_type"] if "input_value_type" in batch.keys() else None)
    measurement_label = get_measurement_label(input_value_type)
    if not should_show_auxiliary_log_column(input_value_type) and "log_value" in ordered_df.columns:
        ordered_df = ordered_df.drop(columns=["log_value"])
    column_mapping = {
        "manual_note": "备注",
        "project_id": "项目ID",
        "project_name": "项目名称",
        "batch_id": "批次ID",
        "instrument": "仪器",
        "reagent": "试剂",
        "qc_material": "质控品",
        "concentration": "浓度",
        "lot_no": "质控品批号",
        "target_n": "建靶次数",
        "test_time": "检测时间",
        "operator": "检测人",
        "value": measurement_label,
        "log_value": "log值",
        "reagent_lot_changed": "试剂批号变更",
        "sequence": "检测序号",
        "phase": "阶段",
        "z": "Z值",
        "status": "判定结果",
        "rule_hits": "触发规则",
        "error_type": "误差类型",
        "analysis_prompt": "分析提示",
        "created_at": "创建时间",
        "id": "记录ID",
    }
    return ordered_df.rename(columns=column_mapping)


def export_batch_results_for_phase(
    batch: sqlite3.Row,
    qc_df: pd.DataFrame,
    phase_scope: str,
) -> pd.DataFrame:
    phase_config = {
        "building": (LJ_BUILDING_PHASE_LABEL, LJ_BUILDING_EXPORT_COLUMNS),
        "formal": (LJ_FORMAL_PHASE_LABEL, LJ_FORMAL_EXPORT_COLUMNS),
    }
    if phase_scope not in phase_config:
        raise ValueError(f"不支持的 LJ 导出阶段：{phase_scope}")

    phase_label, included_columns = phase_config[phase_scope]
    if qc_df.empty or "phase" not in qc_df.columns:
        phase_df = pd.DataFrame()
    else:
        phase_df = qc_df[qc_df["phase"] == phase_label].copy()
    return export_batch_results(batch, phase_df, included_columns=included_columns)


def update_zscore_run(
    run_id: int,
    test_sequence: int | None = None,
    test_time: str | None = None,
    operator: str | None = None,
    level_count: int | None = None,
    phase: str | None = None,
    run_status: str | None = None,
    rule_template_id: str | None = None,
    rule_hits_run=None,
    error_type_hint: str | None = None,
    analysis_prompt: str | None = None,
    manual_note: str | None = None,
) -> None:
    assignments: list[str] = []
    values: list[object] = []

    if test_sequence is not None:
        assignments.append("test_sequence = ?")
        values.append(int(test_sequence))
    if test_time is not None:
        assignments.append("test_time = ?")
        values.append(test_time)
    if operator is not None:
        assignments.append("operator = ?")
        values.append(str(operator))
    if level_count is not None:
        normalized_level_count = int(level_count)
        if normalized_level_count not in {2, 3}:
            raise ValueError("Z-score 检测记录的水平数只能是 2 或 3。")
        assignments.append("level_count = ?")
        values.append(normalized_level_count)
    if phase is not None:
        assignments.append("phase = ?")
        values.append(str(phase))
    if run_status is not None:
        assignments.append("run_status = ?")
        values.append(str(run_status))
    if rule_template_id is not None:
        assignments.append("rule_template_id = ?")
        values.append(str(rule_template_id))
    if rule_hits_run is not None:
        assignments.append("rule_hits_run = ?")
        values.append(json.dumps(rule_hits_run or [], ensure_ascii=False))
    if error_type_hint is not None:
        assignments.append("error_type_hint = ?")
        values.append(str(error_type_hint))
    if analysis_prompt is not None:
        assignments.append("analysis_prompt = ?")
        values.append(str(analysis_prompt))
    if manual_note is not None:
        assignments.append("manual_note = ?")
        values.append(str(manual_note or ""))

    if not assignments:
        return

    values.append(int(run_id))
    with get_connection() as connection:
        cursor = connection.execute(
            f"""
            UPDATE zscore_runs
            SET {", ".join(assignments)}
            WHERE id = ?
            """,
            tuple(values),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"未找到 Z-score run {run_id}")


def update_zscore_level_results(run_id: int, level_results: list[dict]) -> None:
    if not level_results:
        return

    with get_connection() as connection:
        run_row = connection.execute(
            "SELECT id FROM zscore_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if run_row is None:
            raise ValueError(f"未找到 Z-score run {run_id}")

        existing_rows = connection.execute(
            """
            SELECT id, level_id
            FROM zscore_level_results
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchall()
        existing_by_level = {str(row["level_id"]): int(row["id"]) for row in existing_rows}
        provided_level_ids = {str(level_result.get("level_id") or "").strip() for level_result in level_results}
        extra_level_ids = {
            level_id
            for level_id in existing_by_level
            if level_id and level_id not in provided_level_ids
        }
        if extra_level_ids:
            placeholders = ", ".join("?" for _ in extra_level_ids)
            connection.execute(
                f"""
                DELETE FROM zscore_level_results
                WHERE run_id = ? AND level_id IN ({placeholders})
                """,
                (run_id, *sorted(extra_level_ids)),
            )
            existing_by_level = {
                level_id: result_id
                for level_id, result_id in existing_by_level.items()
                if level_id not in extra_level_ids
            }

        for level_result in level_results:
            level_id = str(level_result.get("level_id") or "").strip()
            if not level_id:
                raise ValueError("多水平结果缺少水平标识。")

            raw_value_provided = "raw_value" in level_result
            raw_value = level_result.get("raw_value")
            if raw_value_provided and raw_value is None:
                raise ValueError(f"{level_id} 的输入值不能为空。")

            log_value_provided = "log_value" in level_result
            log_value = level_result.get("log_value")
            if raw_value_provided and not log_value_provided:
                log_value = _safe_log10(float(raw_value))
                log_value_provided = True

            zscore_provided = "zscore" in level_result
            zscore = level_result.get("zscore")
            status_provided = "level_status" in level_result or "status" in level_result
            status_value = level_result.get("level_status", level_result.get("status"))
            rule_hits_provided = "rule_hits_local" in level_result
            rule_hits_local = level_result.get("rule_hits_local")
            in_control_provided = "is_in_control_for_realtime_stats" in level_result
            in_control_value = level_result.get("is_in_control_for_realtime_stats")
            is_building_included_provided = "is_building_included" in level_result
            is_building_included = level_result.get("is_building_included")
            is_outlier_suspect_provided = "is_outlier_suspect" in level_result
            is_outlier_suspect = level_result.get("is_outlier_suspect")
            outlier_status_provided = "outlier_status" in level_result
            outlier_status = level_result.get("outlier_status")
            outlier_method_provided = "outlier_method" in level_result
            outlier_method = level_result.get("outlier_method")
            grubbs_statistic_provided = "grubbs_statistic" in level_result
            grubbs_statistic = level_result.get("grubbs_statistic")
            grubbs_threshold_provided = "grubbs_threshold" in level_result
            grubbs_threshold = level_result.get("grubbs_threshold")
            manual_status_provided = "manual_status" in level_result
            manual_status = level_result.get("manual_status")
            handled_at_provided = "handled_at" in level_result
            handled_at = level_result.get("handled_at")

            existing_result_id = existing_by_level.get(level_id)
            if existing_result_id is not None:
                assignments: list[str] = []
                values: list[object] = []
                if raw_value_provided:
                    assignments.append("raw_value = ?")
                    values.append(float(raw_value))
                if log_value_provided:
                    assignments.append("log_value = ?")
                    values.append(None if log_value is None else float(log_value))
                if zscore_provided:
                    assignments.append("zscore = ?")
                    values.append(None if zscore is None else float(zscore))
                if status_provided:
                    assignments.append("level_status = ?")
                    values.append(str(status_value or "pending"))
                if rule_hits_provided:
                    assignments.append("rule_hits_local = ?")
                    values.append(json.dumps(rule_hits_local or [], ensure_ascii=False))
                if in_control_provided:
                    assignments.append("is_in_control_for_realtime_stats = ?")
                    values.append(int(bool(in_control_value)))
                if is_building_included_provided:
                    assignments.append("is_building_included = ?")
                    values.append(int(bool(is_building_included)))
                if is_outlier_suspect_provided:
                    assignments.append("is_outlier_suspect = ?")
                    values.append(int(bool(is_outlier_suspect)))
                if outlier_status_provided:
                    assignments.append("outlier_status = ?")
                    values.append(normalize_outlier_status(outlier_status))
                if outlier_method_provided:
                    assignments.append("outlier_method = ?")
                    values.append(str(outlier_method or ""))
                if grubbs_statistic_provided:
                    assignments.append("grubbs_statistic = ?")
                    values.append(None if grubbs_statistic is None else float(grubbs_statistic))
                if grubbs_threshold_provided:
                    assignments.append("grubbs_threshold = ?")
                    values.append(None if grubbs_threshold is None else float(grubbs_threshold))
                if manual_status_provided:
                    assignments.append("manual_status = ?")
                    values.append(normalize_outlier_manual_status(manual_status))
                if handled_at_provided:
                    assignments.append("handled_at = ?")
                    values.append(handled_at)

                if assignments:
                    values.append(existing_result_id)
                    connection.execute(
                        f"""
                        UPDATE zscore_level_results
                        SET {", ".join(assignments)}
                        WHERE id = ?
                        """,
                        tuple(values),
                    )
                continue

            if raw_value is None:
                raise ValueError(f"{level_id} 缺少输入值，无法创建该水平结果。")

            connection.execute(
                """
                INSERT INTO zscore_level_results (
                    run_id,
                    level_id,
                    raw_value,
                    log_value,
                    zscore,
                    level_status,
                    rule_hits_local,
                    is_in_control_for_realtime_stats,
                    is_building_included,
                    is_outlier_suspect,
                    outlier_status,
                    outlier_method,
                    grubbs_statistic,
                    grubbs_threshold,
                    manual_status,
                    handled_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(run_id),
                    level_id,
                    float(raw_value),
                    None if log_value is None else float(log_value),
                    None if zscore is None else float(zscore),
                    str(status_value or "pending"),
                    json.dumps(rule_hits_local or [], ensure_ascii=False),
                    int(bool(in_control_value)),
                    int(bool(is_building_included)) if is_building_included_provided else 1,
                    int(bool(is_outlier_suspect)) if is_outlier_suspect_provided else 0,
                    normalize_outlier_status(outlier_status) if outlier_status_provided else OUTLIER_STATUS_NORMAL,
                    str(outlier_method or "") if outlier_method_provided else "",
                    None if grubbs_statistic is None else float(grubbs_statistic),
                    None if grubbs_threshold is None else float(grubbs_threshold),
                    normalize_outlier_manual_status(manual_status)
                    if manual_status_provided
                    else OUTLIER_MANUAL_STATUS_NORMAL,
                    handled_at if handled_at_provided else None,
                ),
            )


def delete_zscore_run(run_id: int) -> None:
    with get_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM zscore_runs WHERE id = ?",
            (run_id,),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"未找到 Z-score run {run_id}")


def delete_zscore_level_results_by_run(run_id: int) -> None:
    with get_connection() as connection:
        connection.execute(
            "DELETE FROM zscore_level_results WHERE run_id = ?",
            (run_id,),
        )


def delete_zscore_targets_by_batch(batch_id: int) -> None:
    with get_connection() as connection:
        connection.execute(
            "DELETE FROM zscore_level_targets WHERE batch_id = ?",
            (batch_id,),
        )


def _deserialize_json_list(raw_value) -> list:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return raw_value
    text = str(raw_value).strip()
    if not text:
        return []
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return []
    return decoded if isinstance(decoded, list) else []


def _build_zscore_level_result_record(row: sqlite3.Row) -> dict:
    status = str(row["level_status"])
    return {
        "id": int(row["id"]),
        "run_id": int(row["run_id"]),
        "level_id": str(row["level_id"]),
        "raw_value": float(row["raw_value"]),
        "log_value": None if row["log_value"] is None else float(row["log_value"]),
        "zscore": None if row["zscore"] is None else float(row["zscore"]),
        "level_status": status,
        "status": status,
        "rule_hits_local": _deserialize_json_list(row["rule_hits_local"]),
        "is_in_control_for_realtime_stats": bool(row["is_in_control_for_realtime_stats"]),
        "is_building_included": int(row["is_building_included"]) if row["is_building_included"] is not None else 1,
        "is_outlier_suspect": int(row["is_outlier_suspect"]) if row["is_outlier_suspect"] is not None else 0,
        "outlier_status": normalize_outlier_status(row["outlier_status"], fallback=OUTLIER_STATUS_NORMAL),
        "outlier_method": str(row["outlier_method"] or ""),
        "grubbs_statistic": None if row["grubbs_statistic"] is None else float(row["grubbs_statistic"]),
        "grubbs_threshold": None if row["grubbs_threshold"] is None else float(row["grubbs_threshold"]),
        "manual_status": normalize_outlier_manual_status(
            row["manual_status"],
            fallback=OUTLIER_MANUAL_STATUS_NORMAL,
        ),
        "handled_at": row["handled_at"],
        "created_at": row["created_at"],
    }


def _build_zscore_run_record(row: sqlite3.Row) -> dict:
    return {
        "id": int(row["id"]),
        "run_id": int(row["id"]),
        "test_sequence": int(row["test_sequence"]) if row["test_sequence"] is not None else None,
        "batch_id": int(row["batch_id"]),
        "project_id": int(row["project_id"]),
        "test_time": row["test_time"],
        "operator": str(row["operator"] or ""),
        "level_count": int(row["level_count"]),
        "phase": str(row["phase"]),
        "run_status": str(row["run_status"]),
        "rule_template_id": str(row["rule_template_id"]),
        "rule_hits_run": _deserialize_json_list(row["rule_hits_run"]),
        "error_type_hint": str(row["error_type_hint"] or "unknown"),
        "analysis_prompt": str(row["analysis_prompt"] or ""),
        "manual_note": str(row["manual_note"] or ""),
        "created_at": row["created_at"],
        "level_results": [],
    }


def get_zscore_run_with_levels(run_id: int) -> dict:
    with get_connection() as connection:
        run_row = connection.execute(
            """
            SELECT *
            FROM zscore_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if run_row is None:
            raise ValueError(f"未找到 Z-score run {run_id}")

        level_rows = connection.execute(
            """
            SELECT *
            FROM zscore_level_results
            WHERE run_id = ?
            ORDER BY level_id ASC, id ASC
            """,
            (run_id,),
        ).fetchall()

    run_record = _build_zscore_run_record(run_row)
    run_record["level_results"] = [_build_zscore_level_result_record(row) for row in level_rows]
    return run_record


def get_zscore_level_result(level_result_id: int) -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT level_results.*, runs.batch_id, runs.phase, runs.rule_template_id, runs.test_time
            FROM zscore_level_results AS level_results
            INNER JOIN zscore_runs AS runs ON runs.id = level_results.run_id
            WHERE level_results.id = ?
            """,
            (level_result_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"未找到 Z-score level result {level_result_id}")
    return row


def set_zscore_level_result_building_state(
    level_result_id: int,
    *,
    is_building_included: int,
    manual_status: str,
    handled_at: str | None = None,
) -> None:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE zscore_level_results
            SET is_building_included = ?,
                manual_status = ?,
                handled_at = ?
            WHERE id = ?
            """,
            (
                int(is_building_included),
                normalize_outlier_manual_status(manual_status),
                handled_at,
                int(level_result_id),
            ),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"未找到 Z-score level result {level_result_id}")


def get_zscore_runs_with_levels_for_batch(batch_id: int) -> list[dict]:
    with get_connection() as connection:
        run_rows = connection.execute(
            """
            SELECT *
            FROM zscore_runs
            WHERE batch_id = ?
            ORDER BY datetime(test_time) ASC, id ASC
            """,
            (batch_id,),
        ).fetchall()
        level_rows = connection.execute(
            """
            SELECT level_results.*, runs.test_time
            FROM zscore_level_results AS level_results
            INNER JOIN zscore_runs AS runs ON runs.id = level_results.run_id
            WHERE runs.batch_id = ?
            ORDER BY datetime(runs.test_time) ASC, runs.id ASC, level_results.level_id ASC, level_results.id ASC
            """,
            (batch_id,),
        ).fetchall()

    run_records = [_build_zscore_run_record(row) for row in run_rows]
    run_map = {int(run["id"]): run for run in run_records}
    for level_row in level_rows:
        run_map[int(level_row["run_id"])]["level_results"].append(_build_zscore_level_result_record(level_row))
    return run_records


def add_zscore_level_results(run_id: int, level_results: list[dict]) -> None:
    if not level_results:
        return

    rows = []
    for level_result in level_results:
        rows.append(
            (
                run_id,
                str(level_result["level_id"]),
                float(level_result["raw_value"]),
                level_result.get("log_value"),
                level_result.get("zscore"),
                str(level_result.get("status", "pending")),
                json.dumps(level_result.get("rule_hits_local") or [], ensure_ascii=False),
                int(bool(level_result.get("is_in_control_for_realtime_stats", False))),
                int(bool(level_result.get("is_building_included", 1))),
                int(bool(level_result.get("is_outlier_suspect", 0))),
                normalize_outlier_status(level_result.get("outlier_status"), fallback=OUTLIER_STATUS_NORMAL),
                str(level_result.get("outlier_method", "") or ""),
                level_result.get("grubbs_statistic"),
                level_result.get("grubbs_threshold"),
                normalize_outlier_manual_status(
                    level_result.get("manual_status"),
                    fallback=OUTLIER_MANUAL_STATUS_NORMAL,
                ),
                level_result.get("handled_at"),
            )
        )

    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO zscore_level_results (
                run_id,
                level_id,
                raw_value,
                log_value,
                zscore,
                level_status,
                rule_hits_local,
                is_in_control_for_realtime_stats,
                is_building_included,
                is_outlier_suspect,
                outlier_status,
                outlier_method,
                grubbs_statistic,
                grubbs_threshold,
                manual_status,
                handled_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def get_zscore_runs_df(
    batch_id: int,
    rule_template_id: str | None = None,
    include_manual_note: bool = False,
) -> pd.DataFrame:
    select_columns = """
                    runs.id,
                    runs.batch_id,
                    runs.project_id,
                    runs.test_sequence,
                    runs.test_time,
                    runs.operator,
                    runs.level_count,
                    runs.phase,
                    runs.run_status,
                    runs.rule_template_id,
                    runs.rule_hits_run,
                    runs.error_type_hint,
                    runs.analysis_prompt,
                    runs.created_at,
                    projects.name AS project_name
    """
    if include_manual_note:
        select_columns += ",\n                    runs.manual_note"
    with get_connection() as connection:
        if rule_template_id:
            dataframe = pd.read_sql_query(
                """
                SELECT
                    {}
                FROM zscore_runs AS runs
                LEFT JOIN projects ON projects.id = runs.project_id
                WHERE runs.batch_id = ? AND runs.rule_template_id = ?
                ORDER BY datetime(runs.test_time) ASC, runs.id ASC
                """.format(select_columns),
                connection,
                params=(batch_id, rule_template_id),
            )
        else:
            dataframe = pd.read_sql_query(
                """
                SELECT
                    {}
                FROM zscore_runs AS runs
                LEFT JOIN projects ON projects.id = runs.project_id
                WHERE runs.batch_id = ?
                ORDER BY datetime(runs.test_time) ASC, runs.id ASC
                """.format(select_columns),
                connection,
                params=(batch_id,),
            )

    if not dataframe.empty:
        dataframe["test_time"] = pd.to_datetime(dataframe["test_time"])
        dataframe["created_at"] = pd.to_datetime(dataframe["created_at"])
        if include_manual_note:
            dataframe["manual_note"] = dataframe["manual_note"].fillna("")
    return dataframe


def get_zscore_level_results_df(
    run_id: int | None = None,
    batch_id: int | None = None,
    rule_template_id: str | None = None,
) -> pd.DataFrame:
    if run_id is None and batch_id is None:
        raise ValueError("run_id 和 batch_id 至少需要提供一个")

    with get_connection() as connection:
        if run_id is not None:
            dataframe = pd.read_sql_query(
                """
                SELECT
                    level_results.*,
                    runs.batch_id,
                    runs.project_id,
                    runs.phase,
                    runs.run_status,
                    runs.rule_template_id,
                    runs.test_time
                FROM zscore_level_results AS level_results
                INNER JOIN zscore_runs AS runs ON runs.id = level_results.run_id
                WHERE level_results.run_id = ?
                ORDER BY level_results.id ASC
                """,
                connection,
                params=(run_id,),
            )
        elif rule_template_id:
            dataframe = pd.read_sql_query(
                """
                SELECT
                    level_results.*,
                    runs.batch_id,
                    runs.project_id,
                    runs.phase,
                    runs.run_status,
                    runs.rule_template_id,
                    runs.test_time
                FROM zscore_level_results AS level_results
                INNER JOIN zscore_runs AS runs ON runs.id = level_results.run_id
                WHERE runs.batch_id = ? AND runs.rule_template_id = ?
                ORDER BY datetime(runs.test_time) ASC, level_results.id ASC
                """,
                connection,
                params=(batch_id, rule_template_id),
            )
        else:
            dataframe = pd.read_sql_query(
                """
                SELECT
                    level_results.*,
                    runs.batch_id,
                    runs.project_id,
                    runs.phase,
                    runs.run_status,
                    runs.rule_template_id,
                    runs.test_time
                FROM zscore_level_results AS level_results
                INNER JOIN zscore_runs AS runs ON runs.id = level_results.run_id
                WHERE runs.batch_id = ?
                ORDER BY datetime(runs.test_time) ASC, level_results.id ASC
                """,
                connection,
                params=(batch_id,),
            )

    if not dataframe.empty:
        dataframe["test_time"] = pd.to_datetime(dataframe["test_time"])
        dataframe["created_at"] = pd.to_datetime(dataframe["created_at"])
        dataframe["is_in_control_for_realtime_stats"] = (
            dataframe["is_in_control_for_realtime_stats"].fillna(0).astype(int)
        )
        dataframe["is_building_included"] = dataframe["is_building_included"].fillna(1).astype(int)
        dataframe["is_outlier_suspect"] = dataframe["is_outlier_suspect"].fillna(0).astype(int)
        dataframe["outlier_status"] = dataframe["outlier_status"].map(
            lambda value: normalize_outlier_status(value, fallback=OUTLIER_STATUS_NORMAL)
        )
        dataframe["outlier_method"] = dataframe["outlier_method"].fillna("")
        dataframe["manual_status"] = dataframe["manual_status"].map(
            lambda value: normalize_outlier_manual_status(value, fallback=OUTLIER_MANUAL_STATUS_NORMAL)
        )
        dataframe["handled_at"] = pd.to_datetime(dataframe["handled_at"], errors="coerce")
    return dataframe


def get_zscore_level_targets_df(batch_id: int) -> pd.DataFrame:
    with get_connection() as connection:
        dataframe = pd.read_sql_query(
            """
            SELECT *
            FROM zscore_level_targets
            WHERE batch_id = ?
            ORDER BY level_id ASC, id ASC
            """,
            connection,
            params=(batch_id,),
        )

    if not dataframe.empty:
        dataframe["updated_at"] = pd.to_datetime(dataframe["updated_at"])
        dataframe["is_ready"] = dataframe["is_ready"].fillna(0).astype(int)
        dataframe["collected_n"] = dataframe["collected_n"].fillna(0).astype(int)
        dataframe["required_n"] = dataframe["required_n"].fillna(0).astype(int)
    return dataframe


def upsert_zscore_level_target(batch_id: int, level_id: str, **fields) -> None:
    allowed_fields = {
        "vendor_reference_mean",
        "vendor_reference_sd",
        "vendor_reference_cv",
        "vendor_reference_source_note",
        "provisional_mean",
        "provisional_sd",
        "provisional_cv",
        "final_target_mean",
        "final_target_sd",
        "final_target_cv",
        "realtime_mean",
        "realtime_sd",
        "realtime_cv",
        "collected_n",
        "required_n",
        "is_ready",
        "phase",
    }
    payload = {key: fields[key] for key in fields if key in allowed_fields}
    if not payload:
        return

    insert_columns = ["batch_id", "level_id", *payload.keys()]
    insert_values = [batch_id, level_id, *payload.values()]
    placeholders = ", ".join(["?"] * len(insert_columns))
    update_clause = ", ".join(f"{column} = excluded.{column}" for column in payload.keys())

    with get_connection() as connection:
        connection.execute(
            f"""
            INSERT INTO zscore_level_targets ({", ".join(insert_columns)})
            VALUES ({placeholders})
            ON CONFLICT(batch_id, level_id) DO UPDATE SET
                {update_clause},
                updated_at = CURRENT_TIMESTAMP
            """,
            insert_values,
        )


def get_zscore_project_level_count(project_id: int) -> int:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT level_count
            FROM zscore_project_config
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()

    if row is None:
        raise ValueError("所选项目不是 Z-score 项目")
    return int(row["level_count"])


def create_zscore_project(
    name: str,
    level_count: int,
    input_value_type: str = DEFAULT_INPUT_VALUE_TYPE,
) -> int:
    cleaned_name = str(name or "").strip()
    normalized_level_count = int(level_count)
    normalized_input_value_type = normalize_input_value_type(input_value_type)
    if normalized_level_count not in {2, 3}:
        raise ValueError("Z-score 项目水平数只能是 2 或 3。")
    if not cleaned_name:
        raise ValueError("项目名称不能为空")

    with get_connection() as connection:
        try:
            cursor = connection.execute(
                """
                INSERT INTO projects (name, method_type, input_value_type)
                VALUES (?, ?, ?)
                """,
                (cleaned_name, PROJECT_METHOD_ZSCORE, normalized_input_value_type),
            )
            project_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO zscore_project_config (project_id, level_count)
                VALUES (?, ?)
                """,
                (project_id, normalized_level_count),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("项目名称已存在") from exc
    return project_id


def create_zscore_batch(
    instrument: str,
    reagent: str,
    qc_material: str,
    concentration: str,
    lot_no: str,
    target_n: int,
    project_id: int | None = None,
    level_1_label: str | None = None,
    level_2_label: str | None = None,
    level_3_label: str | None = None,
    cv_limit: float | None = None,
) -> int:
    if project_id is None:
        raise ValueError("请先选择项目")

    project_level_count = get_zscore_project_level_count(project_id)
    cleaned_level_labels = [
        str(level_1_label or "").strip() or None,
        str(level_2_label or "").strip() or None,
        str(level_3_label or "").strip() or None,
    ]
    normalized_cv_limit = None if cv_limit in (None, "") else float(cv_limit)
    with get_connection() as connection:
        if _batch_lot_exists(connection, table_name="batches", project_id=project_id, lot_no=lot_no):
            raise ValueError("当前项目下已存在相同的质控品批号。")
        cursor = connection.execute(
            """
            INSERT INTO batches (
                project_id, instrument, reagent, qc_material, concentration, lot_no, target_n, cv_limit
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                instrument,
                reagent,
                qc_material,
                concentration,
                lot_no,
                target_n,
                normalized_cv_limit,
            ),
        )
        batch_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO zscore_batch_config (
                batch_id,
                project_id,
                level_count,
                level_1_label,
                level_2_label,
                level_3_label,
                effective_building_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (batch_id, project_id, project_level_count, *cleaned_level_labels, 0),
        )
    return batch_id


def update_zscore_batch_effective_building_count(batch_id: int, effective_building_count: int) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE zscore_batch_config
            SET effective_building_count = ?
            WHERE batch_id = ?
            """,
            (int(effective_building_count), batch_id),
        )


def add_zscore_run(
    batch_id: int,
    project_id: int,
    test_sequence: int,
    test_time: str,
    operator: str,
    level_count: int,
    phase: str,
    run_status: str,
    rule_template_id: str,
    rule_hits_run,
    error_type_hint: str,
    analysis_prompt: str,
    manual_note: str = "",
) -> int:
    normalized_level_count = int(level_count)
    if normalized_level_count not in {2, 3}:
        raise ValueError("Z-score 检测记录的水平数只能是 2 或 3。")

    serialized_rule_hits = json.dumps(rule_hits_run or [], ensure_ascii=False)
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO zscore_runs (
                batch_id,
                project_id,
                test_sequence,
                test_time,
                operator,
                level_count,
                phase,
                run_status,
                rule_template_id,
                rule_hits_run,
                error_type_hint,
                analysis_prompt,
                manual_note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                project_id,
                int(test_sequence),
                test_time,
                operator,
                normalized_level_count,
                phase,
                run_status,
                rule_template_id,
                serialized_rule_hits,
                error_type_hint,
                analysis_prompt,
                str(manual_note or ""),
            ),
        )
        return int(cursor.lastrowid)
