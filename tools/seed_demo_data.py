from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.demo_data_service import (  # noqa: E402
    PROFILE_BASIC,
    PROFILE_FULL,
    build_demo_plan,
    delete_demo_data,
    seed_demo_data,
    validate_demo_data,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed LJQCApp demo data focused on two-month monthly report demonstrations.",
    )
    parser.add_argument(
        "--profile",
        choices=(PROFILE_BASIC, PROFILE_FULL),
        default=PROFILE_FULL,
        help="Demo data profile to seed.",
    )
    parser.add_argument(
        "--replace-demo",
        action="store_true",
        help="Delete existing projects whose names start with the demo prefix before seeding.",
    )
    parser.add_argument(
        "--reset-all",
        action="store_true",
        help="Reset the whole configured database before seeding. Requires --yes-reset-all.",
    )
    parser.add_argument(
        "--yes-reset-all",
        action="store_true",
        help="Confirm that --reset-all may delete all current database content.",
    )
    parser.add_argument(
        "--delete-demo",
        action="store_true",
        help="Only delete demo projects whose names start with the demo prefix.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without writing the database.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate existing demo data without seeding or deleting.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.reset_all and not args.yes_reset_all:
        print("--reset-all deletes the whole configured database. Re-run with --yes-reset-all after confirming.")
        return 2

    if args.delete_demo:
        result = delete_demo_data(dry_run=bool(args.dry_run))
        action = "would delete" if result.dry_run else "deleted"
        print(
            f"Demo cleanup {action}: {result.lj_zscore_projects} LJ/Z-score project(s), "
            f"{result.instant_projects} Instant project(s)."
        )
        return 0

    if args.validate_only:
        validation = validate_demo_data(profile=args.profile)
        print(
            f"Validation passed: profile={validation.profile}, datasets={validation.checked_datasets}, "
            f"report_packages={validation.checked_report_packages}, pdf_bytes={validation.checked_pdf_bytes}."
        )
        return 0

    if args.dry_run:
        plan = build_demo_plan(profile=args.profile)
        print(f"Dry run: profile={args.profile}, datasets={len(plan)}")
        for item in plan:
            print(f"- {item.key}: {item.project_name} | {item.method} | lot={item.lot_no}")
        return 0

    result = seed_demo_data(
        profile=args.profile,
        replace_demo=bool(args.replace_demo),
        reset_all=bool(args.reset_all),
        dry_run=False,
    )
    validation = validate_demo_data(profile=args.profile)
    print(
        f"Demo seed completed: profile={result.profile}, datasets={len(result.datasets)}, "
        f"report_snapshots={result.report_snapshots_created}."
    )
    if result.deleted is not None:
        print(
            f"Replaced demo projects: {result.deleted.lj_zscore_projects} LJ/Z-score, "
            f"{result.deleted.instant_projects} Instant."
        )
    for summary in result.datasets:
        months = ", ".join(
            f"{month}:{count}" for month, count in sorted(summary.formal_records_by_month.items())
        ) or "-"
        abnormal = ", ".join(
            f"{month}:{count}" for month, count in sorted(summary.abnormal_records_by_month.items())
        ) or "-"
        print(
            f"- {summary.key} | {summary.project_name} | building={summary.effective_building_records} | "
            f"formal={months} | abnormal={abnormal} | snapshots={summary.report_snapshots_created}"
        )
    print(
        f"Validation passed: report_packages={validation.checked_report_packages}, "
        f"pdf_bytes={validation.checked_pdf_bytes}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
