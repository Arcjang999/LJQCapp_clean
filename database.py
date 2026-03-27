from __future__ import annotations

import math
import os
import shutil
import sqlite3
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DATA_DIR = BASE_DIR / "data"
PROJECT_DB_PATH = PROJECT_DATA_DIR / "qc_lj_app.db"
PROJECT_LEGACY_DB_PATH = BASE_DIR / "lj_qc.db"
MIGRATION_PROJECT_NAME = "\u5386\u53f2\u6570\u636e\u8fc1\u79fb\u9879\u76ee"


def _get_persistent_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "LJQCApp"
    return Path.home() / ".ljqcapp"


DATA_DIR = _get_persistent_data_dir()
DB_PATH = DATA_DIR / "qc_lj_app.db"
LEGACY_DB_CANDIDATES = [
    PROJECT_DB_PATH,
    PROJECT_LEGACY_DB_PATH,
]


def get_db_path() -> Path:
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
    return connection


def init_db() -> None:
    _migrate_legacy_db_file()
    with get_connection() as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        _ensure_projects_table(connection)
        _ensure_batches_table(connection)
        _ensure_results_table(connection)
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


def _ensure_projects_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _get_or_create_migration_project(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT id FROM projects WHERE name = ?",
        (MIGRATION_PROJECT_NAME,),
    ).fetchone()
    if row is not None:
        return int(row["id"])

    cursor = connection.execute(
        "INSERT INTO projects (name) VALUES (?)",
        (MIGRATION_PROJECT_NAME,),
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
        project_id = int(_legacy_value(row, legacy_columns, "project_id", default=migration_project_id))
        created_at = row["created_at"] if "created_at" in legacy_columns else None

        connection.execute(
            """
            INSERT INTO batches (
                id, project_id, instrument, reagent,
                qc_material, concentration, lot_no, target_n, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
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
        created_at = row["created_at"] if "created_at" in legacy_columns else None

        connection.execute(
            """
            INSERT INTO results (
                id, batch_id, test_time, operator, value, log_value, reagent_lot_changed, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
            """,
            (
                row["id"],
                row["batch_id"],
                row["test_time"],
                operator,
                value,
                log_value,
                reagent_lot_changed,
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
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (batch_id) REFERENCES batches (id) ON DELETE CASCADE
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


def create_project(name: str) -> int:
    with get_connection() as connection:
        try:
            cursor = connection.execute(
                "INSERT INTO projects (name) VALUES (?)",
                (name,),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("\u9879\u76ee\u540d\u79f0\u5df2\u5b58\u5728") from exc
        return int(cursor.lastrowid)


def update_project(project_id: int, name: str) -> None:
    with get_connection() as connection:
        try:
            connection.execute(
                "UPDATE projects SET name = ? WHERE id = ?",
                (name, project_id),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("\u9879\u76ee\u540d\u79f0\u5df2\u5b58\u5728") from exc


def list_projects() -> pd.DataFrame:
    with get_connection() as connection:
        dataframe = pd.read_sql_query(
            """
            SELECT id, name, created_at
            FROM projects
            ORDER BY id DESC
            """,
            connection,
        )
    return dataframe


def get_project(project_id: int) -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()

    if row is None:
        raise ValueError(f"\u672a\u627e\u5230\u9879\u76ee {project_id}")
    return row


def create_batch(
    instrument: str,
    reagent: str,
    qc_material: str,
    concentration: str,
    lot_no: str,
    target_n: int,
    project_id: int | None = None,
) -> int:
    if project_id is None:
        raise ValueError("\u8bf7\u5148\u9009\u62e9\u9879\u76ee")

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO batches (
                project_id, instrument, reagent, qc_material, concentration, lot_no, target_n
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (project_id, instrument, reagent, qc_material, concentration, lot_no, target_n),
        )
        return int(cursor.lastrowid)


def update_batch(batch_id: int, lot_no: str) -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE batches SET lot_no = ? WHERE id = ?",
            (lot_no, batch_id),
        )


def list_batches(project_id: int | None = None) -> pd.DataFrame:
    with get_connection() as connection:
        if project_id is None:
            dataframe = pd.read_sql_query(
                """
                SELECT
                    batches.id,
                    batches.project_id,
                    projects.name AS project_name,
                    batches.instrument,
                    batches.reagent,
                    batches.qc_material,
                    batches.concentration,
                    batches.lot_no,
                    batches.target_n,
                    batches.created_at
                FROM batches
                LEFT JOIN projects ON projects.id = batches.project_id
                ORDER BY batches.id DESC
                """,
                connection,
            )
        else:
            dataframe = pd.read_sql_query(
                """
                SELECT
                    batches.id,
                    batches.project_id,
                    projects.name AS project_name,
                    batches.instrument,
                    batches.reagent,
                    batches.qc_material,
                    batches.concentration,
                    batches.lot_no,
                    batches.target_n,
                    batches.created_at
                FROM batches
                LEFT JOIN projects ON projects.id = batches.project_id
                WHERE batches.project_id = ?
                ORDER BY batches.id DESC
                """,
                connection,
                params=(project_id,),
            )
    return dataframe


def get_batch(batch_id: int) -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                batches.*,
                projects.name AS project_name
            FROM batches
            LEFT JOIN projects ON projects.id = batches.project_id
            WHERE batches.id = ?
            """,
            (batch_id,),
        ).fetchone()

    if row is None:
        raise ValueError(f"\u672a\u627e\u5230\u6279\u6b21 {batch_id}")
    return row


def add_result(
    batch_id: int,
    test_time: str,
    value: float,
    operator: str = "",
    log_value: float | None = None,
    reagent_lot_changed: int = 0,
) -> None:
    with get_connection() as connection:
        if log_value is None:
            log_value = _safe_log10(value)
        connection.execute(
            """
            INSERT INTO results (batch_id, test_time, operator, value, log_value, reagent_lot_changed)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (batch_id, test_time, operator, value, log_value, int(reagent_lot_changed)),
        )


def update_result(
    result_id: int,
    test_time: str,
    value: float,
    operator: str = "",
    log_value: float | None = None,
    reagent_lot_changed: int = 0,
) -> None:
    with get_connection() as connection:
        if log_value is None:
            log_value = _safe_log10(value)
        cursor = connection.execute(
            """
            UPDATE results
            SET test_time = ?, operator = ?, value = ?, log_value = ?, reagent_lot_changed = ?
            WHERE id = ?
            """,
            (test_time, operator, value, log_value, int(reagent_lot_changed), result_id),
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


def get_results(batch_id: int) -> pd.DataFrame:
    with get_connection() as connection:
        dataframe = pd.read_sql_query(
            """
            SELECT
                id,
                batch_id,
                test_time,
                operator,
                value,
                log_value,
                reagent_lot_changed,
                created_at
            FROM results
            WHERE batch_id = ?
            ORDER BY datetime(test_time) ASC, id ASC
            """,
            connection,
            params=(batch_id,),
        )

    if not dataframe.empty:
        dataframe["test_time"] = pd.to_datetime(dataframe["test_time"])
        dataframe["created_at"] = pd.to_datetime(dataframe["created_at"])
        dataframe["reagent_lot_changed"] = dataframe["reagent_lot_changed"].fillna(0).astype(int)
    return dataframe


def export_batch_results(batch: sqlite3.Row, qc_df: pd.DataFrame) -> pd.DataFrame:
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
    column_mapping = {
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
        "value": "检测值",
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
