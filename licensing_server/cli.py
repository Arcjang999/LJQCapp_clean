"""Explicit local administration; no HTTP endpoint can issue or change a code."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from .config import ConfigError, DEFAULT_ENDS_AT, DEFAULT_MAX_DEVICES, ServerConfig, initialize
from .store import LicenseStore


def parser():
    root = argparse.ArgumentParser(description='管理桌面验证码，仅在服务器本机运行。')
    commands = root.add_subparsers(dest='command', required=True)
    create = commands.add_parser('init', help='生成独立私有配置及公开信任文件，不覆盖现有文件。')
    create.add_argument('--config', type=Path, required=True)
    create.add_argument('--database', type=Path, required=True)
    create.add_argument('--public-config', type=Path, required=True)
    create.add_argument('--origin', required=True)
    for name in ('issue', 'list', 'devices', 'disable', 'enable', 'extend', 'unbind'):
        item = commands.add_parser(name)
        item.add_argument('--config', type=Path, required=True)
        if name not in ('issue', 'list'):
            item.add_argument('--license-id', required=True)
        if name in ('issue', 'extend'):
            item.add_argument('--expires-at', default=DEFAULT_ENDS_AT if name == 'issue' else None,
                              required=name == 'extend')
        if name == 'issue':
            item.add_argument('--label', default='')
            item.add_argument('--max-devices', type=int, default=DEFAULT_MAX_DEVICES)
        if name == 'unbind':
            item.add_argument('--device-id', required=True)
    return root


def main(argv=None):
    arguments = parser()
    args = arguments.parse_args(argv)
    try:
        if args.command == 'init':
            initialize(args.config, args.database, args.public_config, args.origin)
            store = LicenseStore(ServerConfig.load(args.config))
            result = {'status': 'initialized', 'public_config': str(args.public_config)}
        else:
            store = LicenseStore(ServerConfig.load(args.config))
            if args.command == 'issue':
                result = store.issue(args.label, expires_at=args.expires_at, max_devices=args.max_devices)
            elif args.command == 'list':
                result = store.list_licenses()
            elif args.command == 'devices':
                result = store.list_devices(args.license_id)
            elif args.command in ('disable', 'enable'):
                store.set_enabled(args.license_id, args.command == 'enable')
                result = {'status': args.command + 'd', 'license_id': args.license_id}
            elif args.command == 'extend':
                store.extend(args.license_id, args.expires_at)
                result = {'status': 'extended', 'license_id': args.license_id}
            elif args.command == 'unbind':
                store.unbind(args.license_id, args.device_id)
                result = {'status': 'unbound', 'device_id': args.device_id}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ConfigError, ValueError, OSError, sqlite3.Error):
        # Never echo private config, a typed code, signing material or DB rows.
        arguments.exit(1, '操作未完成，请核对记录、截止时间及私有配置权限。\n')


if __name__ == '__main__':
    main()

