from __future__ import annotations

import json
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
        _ensure_zscore_project_config_table(connection)
        _ensure_zscore_batch_config_table(connection)
        _ensure_zscore_runs_table(connection)
        _ensure_zscore_level_results_table(connection)
        _ensure_zscore_level_targets_table(connection)
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
            CREATE INDEX IF NOT EXISTS idx_zscore_runs_batch_time
            ON zscore_runs (batch_id, rule_template_id, test_time, id)
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
            test_time TEXT NOT NULL,
            operator TEXT NOT NULL DEFAULT '',
            level_count INTEGER NOT NULL CHECK (level_count BETWEEN 1 AND 3),
            phase TEXT NOT NULL,
            run_status TEXT NOT NULL,
            rule_template_id TEXT NOT NULL,
            rule_hits_run TEXT NOT NULL DEFAULT '[]',
            error_type_hint TEXT NOT NULL DEFAULT 'unknown',
            analysis_prompt TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (batch_id) REFERENCES batches (id) ON DELETE CASCADE,
            FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
        )
        """
    )


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
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES zscore_runs (id) ON DELETE CASCADE
        )
        """
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
    level_1_label: str | None = None,
    level_2_label: str | None = None,
    level_3_label: str | None = None,
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


def create_zscore_project(name: str, level_count: int) -> int:
    if int(level_count) not in {2, 3}:
        raise ValueError("Z-score 项目 level_count 只能是 2 或 3")

    with get_connection() as connection:
        try:
            cursor = connection.execute(
                "INSERT INTO projects (name) VALUES (?)",
                (name,),
            )
            project_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO zscore_project_config (project_id, level_count)
                VALUES (?, ?)
                """,
                (project_id, int(level_count)),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("\u9879\u76ee\u540d\u79f0\u5df2\u5b58\u5728") from exc
    return project_id


def list_zscore_projects() -> pd.DataFrame:
    with get_connection() as connection:
        dataframe = pd.read_sql_query(
            """
            SELECT
                projects.id,
                projects.name,
                projects.created_at,
                config.level_count
            FROM projects
            INNER JOIN zscore_project_config AS config
                ON config.project_id = projects.id
            ORDER BY projects.id DESC
            """,
            connection,
        )

    if not dataframe.empty:
        dataframe["level_count"] = dataframe["level_count"].astype(int)
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
            WHERE projects.id = ?
            """,
            (project_id,),
        ).fetchone()

    if row is None:
        raise ValueError(f"未找到 Z-score 项目 {project_id}")
    return row


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
) -> int:
    if project_id is None:
        raise ValueError("\u8bf7\u5148\u9009\u62e9\u9879\u76ee")

    with get_connection() as connection:
        project_row = connection.execute(
            """
            SELECT level_count
            FROM zscore_project_config
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
        if project_row is None:
            raise ValueError("所选项目不是 Z-score 项目")

        cursor = connection.execute(
            """
            INSERT INTO batches (
                project_id, instrument, reagent, qc_material, concentration, lot_no, target_n
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (project_id, instrument, reagent, qc_material, concentration, lot_no, target_n),
        )
        batch_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO zscore_batch_config (batch_id, project_id, level_count)
            VALUES (?, ?, ?)
            """,
            (batch_id, project_id, int(project_row["level_count"])),
        )
    return batch_id


def list_zscore_batches(project_id: int | None = None) -> pd.DataFrame:
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
                    batches.created_at,
                    config.level_count,
                    config.level_1_label,
                    config.level_2_label,
                    config.level_3_label
                FROM batches
                INNER JOIN zscore_batch_config AS config ON config.batch_id = batches.id
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
                    batches.created_at,
                    config.level_count,
                    config.level_1_label,
                    config.level_2_label,
                    config.level_3_label
                FROM batches
                INNER JOIN zscore_batch_config AS config ON config.batch_id = batches.id
                LEFT JOIN projects ON projects.id = batches.project_id
                WHERE batches.project_id = ?
                ORDER BY batches.id DESC
                """,
                connection,
                params=(project_id,),
            )

    if not dataframe.empty:
        dataframe["level_count"] = dataframe["level_count"].astype(int)
    return dataframe


def get_zscore_batch(batch_id: int) -> sqlite3.Row:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                batches.*,
                projects.name AS project_name,
                config.level_count,
                config.level_1_label,
                config.level_2_label,
                config.level_3_label
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


def add_zscore_run(
    batch_id: int,
    project_id: int,
    test_time: str,
    operator: str,
    level_count: int,
    phase: str,
    run_status: str,
    rule_template_id: str,
    rule_hits_run,
    error_type_hint: str,
    analysis_prompt: str,
) -> int:
    serialized_rule_hits = json.dumps(rule_hits_run or [], ensure_ascii=False)
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO zscore_runs (
                batch_id,
                project_id,
                test_time,
                operator,
                level_count,
                phase,
                run_status,
                rule_template_id,
                rule_hits_run,
                error_type_hint,
                analysis_prompt
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                project_id,
                test_time,
                operator,
                int(level_count),
                phase,
                run_status,
                rule_template_id,
                serialized_rule_hits,
                error_type_hint,
                analysis_prompt,
            ),
        )
        return int(cursor.lastrowid)


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
                is_in_control_for_realtime_stats
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def get_zscore_runs_df(batch_id: int, rule_template_id: str | None = None) -> pd.DataFrame:
    with get_connection() as connection:
        if rule_template_id:
            dataframe = pd.read_sql_query(
                """
                SELECT
                    runs.*,
                    projects.name AS project_name
                FROM zscore_runs AS runs
                LEFT JOIN projects ON projects.id = runs.project_id
                WHERE runs.batch_id = ? AND runs.rule_template_id = ?
                ORDER BY datetime(runs.test_time) ASC, runs.id ASC
                """,
                connection,
                params=(batch_id, rule_template_id),
            )
        else:
            dataframe = pd.read_sql_query(
                """
                SELECT
                    runs.*,
                    projects.name AS project_name
                FROM zscore_runs AS runs
                LEFT JOIN projects ON projects.id = runs.project_id
                WHERE runs.batch_id = ?
                ORDER BY datetime(runs.test_time) ASC, runs.id ASC
                """,
                connection,
                params=(batch_id,),
            )

    if not dataframe.empty:
        dataframe["test_time"] = pd.to_datetime(dataframe["test_time"])
        dataframe["created_at"] = pd.to_datetime(dataframe["created_at"])
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


def create_zscore_project(name: str, level_count: int) -> int:
    cleaned_name = str(name or "").strip()
    normalized_level_count = int(level_count)
    if normalized_level_count not in {2, 3}:
        raise ValueError("Z-score 项目 level_count 只能是 2 或 3")
    if not cleaned_name:
        raise ValueError("项目名称不能为空")

    with get_connection() as connection:
        try:
            cursor = connection.execute(
                "INSERT INTO projects (name) VALUES (?)",
                (cleaned_name,),
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
) -> int:
    if project_id is None:
        raise ValueError("请先选择项目")

    project_level_count = get_zscore_project_level_count(project_id)
    cleaned_level_labels = [
        str(level_1_label or "").strip() or None,
        str(level_2_label or "").strip() or None,
        str(level_3_label or "").strip() or None,
    ]
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
        batch_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO zscore_batch_config (
                batch_id,
                project_id,
                level_count,
                level_1_label,
                level_2_label,
                level_3_label
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (batch_id, project_id, project_level_count, *cleaned_level_labels),
        )
    return batch_id


def add_zscore_run(
    batch_id: int,
    project_id: int,
    test_time: str,
    operator: str,
    level_count: int,
    phase: str,
    run_status: str,
    rule_template_id: str,
    rule_hits_run,
    error_type_hint: str,
    analysis_prompt: str,
) -> int:
    normalized_level_count = int(level_count)
    if normalized_level_count not in {2, 3}:
        raise ValueError("Z-score run 的 level_count 只能是 2 或 3")

    serialized_rule_hits = json.dumps(rule_hits_run or [], ensure_ascii=False)
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO zscore_runs (
                batch_id,
                project_id,
                test_time,
                operator,
                level_count,
                phase,
                run_status,
                rule_template_id,
                rule_hits_run,
                error_type_hint,
                analysis_prompt
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                project_id,
                test_time,
                operator,
                normalized_level_count,
                phase,
                run_status,
                rule_template_id,
                serialized_rule_hits,
                error_type_hint,
                analysis_prompt,
            ),
        )
        return int(cursor.lastrowid)
