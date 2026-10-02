"""Standalone connections close; shared transactions keep one atomic lifetime."""
from pathlib import Path
import sqlite3
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import database as db
from tests.project_management_v11_smoke_test import TemporaryDatabaseContext


class ConnectionLifecycleTests(unittest.TestCase):
    def count(self):
        connection = sqlite3.connect(db.DB_PATH)
        try:
            return connection.execute('SELECT COUNT(*) FROM projects').fetchone()[0]
        finally:
            connection.close()

    def assert_closed(self, connection):
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute('SELECT 1')

    def test_standalone_connection_commits_then_closes(self):
        with TemporaryDatabaseContext():
            with db.get_connection() as connection:
                connection.execute("INSERT INTO projects(name,input_value_type) VALUES('连接提交','raw')")
            self.assert_closed(connection)
            self.assertEqual(1, self.count())

    def test_standalone_connection_rolls_back_then_closes_on_failure(self):
        with TemporaryDatabaseContext():
            with self.assertRaisesRegex(RuntimeError, '模拟失败'):
                with db.get_connection() as connection:
                    connection.execute("INSERT INTO projects(name,input_value_type) VALUES('应取消','raw')")
                    raise RuntimeError('模拟失败')
            self.assert_closed(connection)
            self.assertEqual(0, self.count())

    def test_nested_writers_share_connection_and_commit_only_at_outer_boundary(self):
        with TemporaryDatabaseContext():
            with db.atomic_write() as outer:
                db.create_project('第一项', input_value_type='raw')
                with db.get_connection() as direct:
                    self.assertIs(outer, direct)
                    direct.execute('SELECT 1')
                with db.atomic_write() as nested:
                    self.assertIs(outer, nested)
                    db.create_project('第二项', input_value_type='raw')
                self.assertEqual(2, outer.execute('SELECT COUNT(*) FROM projects').fetchone()[0])
                self.assertEqual(0, self.count())
            self.assert_closed(outer)
            self.assertEqual(2, self.count())

    def test_outer_failure_rolls_back_nested_writes_and_closes(self):
        with TemporaryDatabaseContext():
            with self.assertRaisesRegex(RuntimeError, '提交前失败'):
                with db.atomic_write() as outer:
                    db.create_project('第一项', input_value_type='raw')
                    with db.atomic_write():
                        db.create_project('第二项', input_value_type='raw')
                    raise RuntimeError('提交前失败')
            self.assert_closed(outer)
            self.assertEqual(0, self.count())

    def test_read_snapshot_remains_open_across_nested_readers_and_rejects_writes(self):
        with TemporaryDatabaseContext():
            db.create_project('已有项目', input_value_type='raw')
            with db.read_snapshot() as outer:
                with db.get_connection() as direct:
                    self.assertIs(outer, direct)
                    self.assertEqual(1, direct.execute('SELECT COUNT(*) FROM projects').fetchone()[0])
                with db.read_snapshot() as nested:
                    self.assertIs(outer, nested)
                self.assertEqual(1, outer.execute('SELECT COUNT(*) FROM projects').fetchone()[0])
                with self.assertRaises(sqlite3.OperationalError):
                    outer.execute("INSERT INTO projects(name,input_value_type) VALUES('不可写','raw')")
            self.assert_closed(outer)
            self.assertEqual(1, self.count())


if __name__ == '__main__':
    unittest.main(verbosity=2)
