"""Separate SQLite authorization store with atomic device-seat accounting."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
import time

from .config import DEFAULT_ENDS_AT, DEFAULT_MAX_DEVICES, ServerConfig, parse_deadline, private_path


APPLICATION_ID = 0x4C4A4C43
SCHEMA = '''
CREATE TABLE IF NOT EXISTS licenses (
 id TEXT PRIMARY KEY, code_digest TEXT UNIQUE NOT NULL, label TEXT NOT NULL,
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), expires_at INTEGER NOT NULL,
 max_devices INTEGER NOT NULL CHECK(max_devices BETWEEN 1 AND 1000),
 generation INTEGER NOT NULL DEFAULT 1, min_generation INTEGER NOT NULL DEFAULT 1,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
 id TEXT PRIMARY KEY, license_id TEXT NOT NULL REFERENCES licenses(id),
 machine_hash TEXT NOT NULL, public_key TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)),
 generation INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL,
 first_ip TEXT NOT NULL DEFAULT '', last_ip TEXT NOT NULL DEFAULT '',
 UNIQUE(license_id,machine_hash), UNIQUE(license_id,public_key)
);
CREATE TABLE IF NOT EXISTS revoked_device_keys (
 license_id TEXT NOT NULL REFERENCES licenses(id), public_key TEXT NOT NULL,
 revoked_at INTEGER NOT NULL, PRIMARY KEY(license_id,public_key)
);
CREATE TABLE IF NOT EXISTS challenges (
 id TEXT PRIMARY KEY, nonce TEXT NOT NULL, action TEXT NOT NULL,
 request_digest TEXT NOT NULL, license_id TEXT NOT NULL REFERENCES licenses(id),
 device_public_key TEXT NOT NULL, machine_hash TEXT NOT NULL,
 activation_id TEXT, expires_at INTEGER NOT NULL, consumed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at INTEGER NOT NULL,
 action TEXT NOT NULL, license_id TEXT, device_id TEXT, detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS devices_license_active ON devices(license_id,active);
CREATE INDEX IF NOT EXISTS challenges_expiry ON challenges(expires_at);
'''

LICENSE_SELECT = ('SELECT l.id,l.label,l.enabled,l.expires_at,l.max_devices,l.generation,l.created_at,l.updated_at,'
    '(SELECT COUNT(*) FROM devices d WHERE d.license_id=l.id AND d.active=1) AS active_devices FROM licenses l')


class LicenseStore:
    def __init__(self, config: ServerConfig, *, clock=time.time):
        self.config, self.clock = config, clock
        self.path = private_path(config.database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.path.exists():
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        with self.connection() as connection:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            application_id = connection.execute('PRAGMA application_id').fetchone()[0]
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            allowed = {'licenses', 'devices', 'revoked_device_keys', 'challenges', 'audit_events'}
            if (version not in (0, 1, 2) or application_id not in (0, APPLICATION_ID)
                    or tables - allowed or (tables and application_id != APPLICATION_ID)):
                raise ValueError('此文件不是授权数据库，请保留业务数据并使用独立空文件。')
            try:
                connection.executescript('BEGIN IMMEDIATE;\n' + SCHEMA)
                columns = {row['name'] for row in connection.execute('PRAGMA table_info(devices)')}
                for name in ('first_ip', 'last_ip'):
                    if name not in columns:
                        connection.execute('ALTER TABLE devices ADD COLUMN ' + name + " TEXT NOT NULL DEFAULT ''")
                connection.execute('PRAGMA application_id=' + str(APPLICATION_ID))
                connection.execute('PRAGMA user_version=2')
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @contextmanager
    def connection(self):
        private_path(self.path, must_exist=True)
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=15000')
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def write(self):
        with self.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    def digest(self, code: str) -> str:
        if not isinstance(code, str) or not 8 <= len(code.strip()) <= 128:
            raise ValueError('验证码格式无效。')
        return hmac.new(self.config.code_pepper, code.strip().encode('utf-8'), hashlib.sha256).hexdigest()

    def event(self, connection, action, license_id=None, device_id=None, detail=''):
        connection.execute('INSERT INTO audit_events(occurred_at,action,license_id,device_id,detail) VALUES(?,?,?,?,?)',
                           (int(self.clock()), action, license_id, device_id, detail))

    def issue(self, label='', *, expires_at=DEFAULT_ENDS_AT, max_devices=DEFAULT_MAX_DEVICES):
        end = parse_deadline(expires_at) if isinstance(expires_at, str) else expires_at
        if type(end) is not int or end <= int(self.clock()):
            raise ValueError('使用期限须晚于当前时间。')
        if type(max_devices) is not int or not 1 <= max_devices <= 1000:
            raise ValueError('设备台数须为1到1000之间的整数。')
        label = str(label).strip()
        if len(label) > 160:
            raise ValueError('备注最多160个字。')
        code = 'LJQC-' + secrets.token_hex(16).upper()
        license_id, now = secrets.token_hex(16), int(self.clock())
        with self.write() as connection:
            connection.execute('INSERT INTO licenses(id,code_digest,label,enabled,expires_at,max_devices,created_at,updated_at) '
                               'VALUES(?,?,?,1,?,?,?,?)',
                               (license_id, self.digest(code), label, end, max_devices, now, now))
            self.event(connection, 'issue', license_id, detail='max_devices=' + str(max_devices))
        return dict(license_id=license_id, code=code, expires_at=end, max_devices=max_devices)

    def list_licenses(self):
        with self.connection() as connection:
            return [dict(row) for row in connection.execute(LICENSE_SELECT + ' ORDER BY l.created_at,l.id')]

    def get_license(self, license_id):
        if not isinstance(license_id, str) or not 1 <= len(license_id) <= 64:
            raise ValueError('验证码记录不存在。')
        with self.connection() as connection:
            row = connection.execute(LICENSE_SELECT + ' WHERE l.id=?', (license_id,)).fetchone()
            if row is None:
                raise ValueError('验证码记录不存在。')
            return dict(row)

    def list_devices(self, license_id):
        with self.connection() as connection:
            return [dict(row) for row in connection.execute('SELECT id,license_id,machine_hash,public_key,active,generation,'
                'created_at,last_seen_at,first_ip,last_ip FROM devices WHERE license_id=? ORDER BY created_at,id', (license_id,))]

    def set_enabled(self, license_id, enabled):
        if type(enabled) is not bool:
            raise ValueError('启停状态无效。')
        with self.write() as connection:
            row = connection.execute('SELECT * FROM licenses WHERE id=?', (license_id,)).fetchone()
            if row is None:
                raise ValueError('验证码记录不存在。')
            if bool(row['enabled']) != enabled:
                new = row['generation'] + 1
                connection.execute('UPDATE licenses SET enabled=?,generation=?,min_generation=?,updated_at=? WHERE id=?',
                                   (int(enabled), new, new, int(self.clock()), license_id))
                self.event(connection, 'enable' if enabled else 'disable', license_id)

    def extend(self, license_id, expires_at):
        end = parse_deadline(expires_at) if isinstance(expires_at, str) else expires_at
        if type(end) is not int or end <= int(self.clock()):
            raise ValueError('使用期限须晚于当前时间。')
        with self.write() as connection:
            row = connection.execute('SELECT * FROM licenses WHERE id=?', (license_id,)).fetchone()
            if row is None:
                raise ValueError('验证码记录不存在。')
            if end < row['expires_at']:
                raise ValueError('延期后的时间不能早于原截止时间。')
            if end != row['expires_at']:
                connection.execute('UPDATE licenses SET expires_at=?,generation=generation+1,updated_at=? WHERE id=?',
                                   (end, int(self.clock()), license_id))
                self.event(connection, 'extend', license_id, detail='expires_at=' + str(end))

    def unbind(self, license_id, device_id):
        with self.write() as connection:
            row = connection.execute('SELECT * FROM devices WHERE id=? AND license_id=?', (device_id, license_id)).fetchone()
            if row is None:
                raise ValueError('设备记录不存在。')
            if row['active']:
                now = int(self.clock())
                connection.execute('INSERT OR IGNORE INTO revoked_device_keys VALUES(?,?,?)',
                                   (license_id, row['public_key'], now))
                connection.execute('UPDATE devices SET active=0,generation=generation+1 WHERE id=?', (device_id,))
                connection.execute('UPDATE licenses SET generation=generation+1,updated_at=? WHERE id=?', (now, license_id))
                self.event(connection, 'unbind', license_id, device_id)
