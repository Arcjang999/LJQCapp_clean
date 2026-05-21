from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.demo_data_service import (  # noqa: E402
    delete_demo_data,
    format_operation_summary,
    seed_demo_data,
    validate_demo_data,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导入、替换或验证 LJQCApp 演示数据。")
    parser.add_argument("--profile", choices=("basic", "full"), default="full")
    parser.add_argument("--replace-demo", action="store_true", help="先删除旧演示数据，再导入。")
    parser.add_argument("--reset-all", action="store_true", help="先重置整个数据库，必须配合 --yes。")
    parser.add_argument("--delete-demo", action="store_true", help="只删除【演示】前缀的数据。")
    parser.add_argument("--yes", action="store_true", help="确认执行 --reset-all。")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写入数据库。")
    parser.add_argument("--validate-only", action="store_true", help="只验证当前数据库中的演示数据。")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.reset_all and not args.yes:
        print("[拒绝执行] --reset-all 会清空整个数据库，必须增加 --yes。")
        return 2
    if args.delete_demo and (args.reset_all or args.replace_demo or args.validate_only):
        print("[拒绝执行] --delete-demo 请单独使用。")
        return 2
    try:
        if args.validate_only:
            validation = validate_demo_data(profile=args.profile)
            print(f"数据库：{validation.get('db_path')}")
            print(f"演示数据验证：{'通过' if validation.get('ok') else '失败'}")
            if validation.get("failed"):
                print("失败项：")
                for failed in validation["failed"]:
                    print(f"- {failed['name']}：{failed['detail']}")
            return 0 if validation.get("ok") else 1

        if args.delete_demo:
            result = delete_demo_data(dry_run=bool(args.dry_run))
            print(format_operation_summary(result))
            return 0

        result = seed_demo_data(
            profile=args.profile,
            replace_demo=bool(args.replace_demo),
            reset_all=bool(args.reset_all),
            dry_run=bool(args.dry_run),
        )
        print(format_operation_summary(result))
        return 0 if (result.get("validation") or {}).get("ok", True) else 1
    except Exception:
        print("[ERROR] 演示数据操作失败：")
        print(traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

