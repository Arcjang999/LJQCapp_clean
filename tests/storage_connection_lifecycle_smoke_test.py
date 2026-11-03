"""Storage helpers release real SQLite handles before temporary files are removed."""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services import storage_service as storage


REAL_CONNECT = sqlite3.connect


class QueryFailure(sqlite3.Connection):
    def execute(self, sql, *args, **kwargs):
        if sql.startswith("SELECT name FROM sqlite_master"):
            raise sqlite3.OperationalError("injected schema read failure")
        return super().execute(sql, *args, **kwargs)


class BackupFailure(sqlite3.Connection):
    def backup(self, *args, **kwargs):
        raise sqlite3.OperationalError("injected backup failure")


@contextmanager
def tracked_connections(*, factory=sqlite3.Connection, fail_path=None):
    # Strong references keep garbage collection from concealing a leaked handle.
    connections = []

    def connect(path, *args, **kwargs):
        if fail_path is not None and str(path) == str(fail_path):
            raise sqlite3.OperationalError("injected destination open failure")
        connection = REAL_CONNECT(path, *args, factory=factory, **kwargs)
        connections.append(connection)
        return connection

    try:
        with patch.object(storage.sqlite3, "connect", side_effect=connect):
            yield connections
    finally:
        for connection in connections:
            connection.close()


def make_database(path, *, supported=True, managed=False):
    with closing(REAL_CONNECT(path)) as connection, connection:
        connection.execute("CREATE TABLE projects(value TEXT)")
        connection.execute("INSERT INTO projects VALUES ('original contents')")
        if supported:
            connection.execute("CREATE TABLE batches(id INTEGER)")
            connection.execute("CREATE TABLE results(id INTEGER)")
        if managed:
            connection.execute("CREATE TABLE qc_managed_files(relative_path TEXT, size_bytes INTEGER, sha256 TEXT)")
            connection.execute("INSERT INTO qc_managed_files VALUES ('proof.txt', 3, ?)", ("a" * 64,))


class StorageConnectionLifecycleTests(unittest.TestCase):
    def assert_released(self, connections, *paths):
        self.assertTrue(connections, "The test must exercise a real SQLite connection")
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
        # These operations fail on Windows while an SQLite handle is still open.
        # Connections remain strongly referenced until after replacement/deletion.
        for path in paths:
            moved = path.with_suffix(".moved")
            path.replace(moved)
            moved.replace(path)
            path.unlink()
            path.write_bytes(b"replacement")
            path.unlink()

    def test_managed_file_reader_closes_on_records_early_return_and_sql_error(self):
        for branch in ("records", "no_table", "sql_error"):
            with self.subTest(branch=branch), TemporaryDirectory() as folder:
                path = Path(folder) / "snapshot.db"
                make_database(path, managed=branch == "records")
                factory = QueryFailure if branch == "sql_error" else sqlite3.Connection
                with tracked_connections(factory=factory) as connections:
                    if branch == "sql_error":
                        with self.assertRaisesRegex(sqlite3.OperationalError, "schema read failure"):
                            storage._managed_file_records(path)
                    else:
                        records = storage._managed_file_records(path)
                        self.assertEqual([{"relative_path": "proof.txt", "size_bytes": 3, "sha256": "a" * 64}]
                                         if branch == "records" else [], records)
                    self.assert_released(connections, path)

    def test_supported_database_validator_closes_on_success_rejection_and_sql_error(self):
        for branch in ("supported", "unsupported", "sql_error"):
            with self.subTest(branch=branch), TemporaryDirectory() as folder:
                path = Path(folder) / "snapshot.db"
                make_database(path, supported=branch != "unsupported")
                factory = QueryFailure if branch == "sql_error" else sqlite3.Connection
                with tracked_connections(factory=factory) as connections:
                    if branch == "unsupported":
                        with self.assertRaisesRegex(RuntimeError, "不包含可恢复"):
                            storage._validate_supported_database(path)
                    elif branch == "sql_error":
                        with self.assertRaisesRegex(sqlite3.OperationalError, "schema read failure"):
                            storage._validate_supported_database(path)
                    else:
                        storage._validate_supported_database(path)
                    self.assert_released(connections, path)

    def test_quick_check_closes_on_success_and_corrupt_database(self):
        for valid in (True, False):
            with self.subTest(valid=valid), TemporaryDirectory() as folder:
                path = Path(folder) / "snapshot.db"
                if valid:
                    make_database(path)
                else:
                    path.write_bytes(b"not an SQLite database")
                with tracked_connections() as connections:
                    self.assertEqual(valid, storage.validate_sqlite_database(path)[0])
                    self.assert_released(connections, path)

    def test_snapshot_copy_closes_handles_and_preserves_database_contents(self):
        with TemporaryDirectory() as folder:
            source, target = (Path(folder) / name for name in ("source.db", "target.db"))
            make_database(source)
            before = source.read_bytes()
            with tracked_connections() as connections:
                storage._copy_database_snapshot(source, target, overwrite=False)
                with closing(REAL_CONNECT(target)) as check:
                    self.assertEqual([("original contents",)], check.execute("SELECT value FROM projects").fetchall())
                self.assertEqual(before, source.read_bytes())
                self.assert_released(connections, source, target)

    def test_snapshot_copy_closes_source_if_destination_cannot_open(self):
        with TemporaryDirectory() as folder:
            source, target = (Path(folder) / name for name in ("source.db", "target.db"))
            make_database(source)
            with tracked_connections(fail_path=target) as connections:
                with self.assertRaisesRegex(sqlite3.OperationalError, "destination open failure"):
                    storage._copy_database_snapshot(source, target, overwrite=False)
                self.assert_released(connections, source)

    def test_snapshot_copy_closes_both_handles_after_backup_failure(self):
        with TemporaryDirectory() as folder:
            source, target = (Path(folder) / name for name in ("source.db", "target.db"))
            make_database(source)
            with tracked_connections(factory=BackupFailure) as connections:
                with self.assertRaisesRegex(sqlite3.OperationalError, "backup failure"):
                    storage._copy_database_snapshot(source, target, overwrite=False)
                self.assertEqual(2, len(connections))
                self.assert_released(connections, source, target)

    def test_snapshot_copy_closes_handles_before_reporting_validation_failure(self):
        with TemporaryDirectory() as folder:
            source, target = (Path(folder) / name for name in ("source.db", "target.db"))
            make_database(source)
            with tracked_connections() as connections, patch.object(
                    storage, "validate_sqlite_database", return_value=(False, "injected validation failure")):
                with self.assertRaisesRegex(RuntimeError, "validation failure"):
                    storage._copy_database_snapshot(source, target, overwrite=False)
                self.assert_released(connections, source, target)


if __name__ == "__main__":
    unittest.main(verbosity=2)
