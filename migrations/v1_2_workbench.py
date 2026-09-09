from __future__ import annotations

import sqlite3


MIGRATION_KEY = "v1_2_workbench_binding_001"


def ensure_v12_workbench_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS qc_workbench_bindings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            qc_method TEXT NOT NULL CHECK (qc_method IN ('lj', 'zscore', 'instant')),
            project_template_item_id INTEGER,
            lot_config_id INTEGER NOT NULL,
            lot_config_item_id INTEGER NOT NULL,
            runtime_project_id INTEGER NOT NULL,
            runtime_batch_id INTEGER NOT NULL,
            binding_status TEXT NOT NULL DEFAULT 'active'
                CHECK (binding_status IN ('active', 'inactive')),
            source_revision_no INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_template_item_id)
                REFERENCES qc_project_template_items (id) ON DELETE RESTRICT,
            FOREIGN KEY (lot_config_id)
                REFERENCES qc_lot_configs (id) ON DELETE RESTRICT,
            FOREIGN KEY (lot_config_item_id)
                REFERENCES qc_lot_config_items (id) ON DELETE RESTRICT,
            UNIQUE (lot_config_item_id),
            UNIQUE (qc_method, runtime_batch_id)
        )
        """
    )
    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_qc_workbench_binding_project
        ON qc_workbench_bindings (qc_method, runtime_project_id, binding_status);

        CREATE INDEX IF NOT EXISTS idx_qc_workbench_binding_config
        ON qc_workbench_bindings (lot_config_id, lot_config_item_id, binding_status);
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO schema_migrations (migration_key, app_version)
        VALUES (?, ?)
        """,
        (MIGRATION_KEY, "V1.2"),
    )
