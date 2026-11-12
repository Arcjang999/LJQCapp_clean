"""Provision and run the independent administrator site without password arguments."""
from __future__ import annotations

import argparse
import getpass
from pathlib import Path
import sqlite3

from .admin_auth import AdminConfig, initialize_admin
from .admin_httpd import AdminHTTPServer
from .config import ConfigError, ServerConfig
from .store import LicenseStore


def parser():
    root = argparse.ArgumentParser(description='管理独立的验证码管理页面。')
    commands = root.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='设置管理员账号与密码，不覆盖原文件。')
    init.add_argument('--config', type=Path, required=True)
    init.add_argument('--license-config', type=Path, required=True)
    init.add_argument('--origin', required=True)
    init.add_argument('--username', default='admin')
    init.add_argument('--allow-local-http', action='store_true')
    init.add_argument('--trust-proxy-ip', action='store_true')
    serve = commands.add_parser('serve', help='启动管理页，仅监听本机。')
    serve.add_argument('--config', type=Path, required=True)
    serve.add_argument('--port', type=int, default=8519)
    return root


def main(argv=None):
    arguments = parser()
    args = arguments.parse_args(argv)
    try:
        if args.command == 'init':
            password = getpass.getpass('管理员密码（至少12个字符）：')
            repeated = getpass.getpass('再次输入管理员密码：')
            if password != repeated:
                raise ConfigError('两次密码不一致。')
            initialize_admin(args.config, args.license_config, args.origin, args.username, password,
                             allow_local_http=args.allow_local_http, trust_proxy_ip=args.trust_proxy_ip)
            print('管理员配置已创建，请从指定管理网址登录。')
            return
        if not 1024 <= args.port <= 65535:
            raise ConfigError('端口无效。')
        config = AdminConfig.load(args.config)
        store = LicenseStore(ServerConfig.load(config.license_config))
        with AdminHTTPServer(('127.0.0.1', args.port), config, store) as server:
            server.serve_forever()
    except KeyboardInterrupt:
        return
    except (ConfigError, OSError, ValueError, sqlite3.Error):
        arguments.exit(1, '管理服务操作未完成，请核对账号配置、文件权限和独立网址。\n')


if __name__ == '__main__':
    main()
