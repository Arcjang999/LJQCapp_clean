"""Run release checks in child processes confined to fresh SQLite roots."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import traceback
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SUITES = (
    'installer_runtime_smoke_test', 'desktop_distribution_smoke_test',
    'storage_paths_adoption_smoke_test', 'storage_connection_lifecycle_smoke_test',
    'onboarding_service_smoke_test', 'onboarding_ui_smoke_test',
    'workbench_tutorial_smoke_test', 'software_downloads_smoke_test',
    'user_guide_smoke_test', 'app_entry_smoke_test',
    'desktop_external_links_smoke_test',
    'date_time_inputs_smoke_test', 'registered_standard_dates_ui_smoke_test',
    'project_setup_draft_smoke_test', 'project_setup_draft_ui_smoke_test',
    'desktop_update_core_smoke_test', 'desktop_update_bootstrap_smoke_test',
    'desktop_update_manager_smoke_test', 'desktop_update_packaging_smoke_test',
    'desktop_update_catalog_smoke_test',
    'desktop_update_redirect_smoke_test', 'desktop_update_receiver_setup_smoke_test',
    'desktop_update_receiver_smoke_test', 'desktop_update_tls_probe_smoke_test',
    'desktop_update_helper_repair_smoke_test',
    'desktop_update_resend_smoke_test', 'desktop_update_delivery_reference_smoke_test',
)


def run_child(name: str, sandbox: Path, result_file: Path) -> int:
    sandbox = sandbox.resolve()
    sandbox.mkdir(parents=True, exist_ok=False)
    os.environ.update(LJQC_DATA_ROOT=str(sandbox / 'root'), TMPDIR=str(sandbox),
                      TMP=str(sandbox), TEMP=str(sandbox), MPLCONFIGDIR=str(sandbox / 'mpl'))
    import tempfile
    tempfile.tempdir = None
    denied, runs, opened = [], [], set()
    def guard(event, arguments):
        if event != 'sqlite3.connect':
            return
        target = str(arguments[0])
        if target == ':memory:':
            return
        if target.startswith('file:'):
            parsed = urlsplit(target)
            if parsed.path == ':memory:' or 'mode=memory' in parsed.query:
                return
            target = unquote(parsed.path)
            if os.name == 'nt' and re.match(r'^/[A-Za-z]:', target):
                target = target[1:]
        path = Path(target).resolve()
        if not path.is_relative_to(sandbox):
            denied.append(str(path))
            raise PermissionError('CI SQLite access must stay in its fresh test directory')
        opened.add(str(path))
    sys.addaudithook(guard)
    result = {'status': 'failed', 'suite': name, 'sandbox': str(sandbox)}
    exit_code = 1
    try:
        sys.path.insert(0, str(ROOT))
        import database as db
        db.DATA_DIR = sandbox / 'data'
        db.DEFAULT_DB_PATH = db.DB_PATH = sandbox / 'base.db'
        db.PROJECT_DB_PATH = sandbox / 'old-project.db'
        db.PROJECT_LEGACY_DB_PATH = sandbox / 'old.db'
        db.STORAGE_CONFIG_PATH = sandbox / 'storage_config.json'
        db.LEGACY_DB_CANDIDATES = []
        import unittest
        original = unittest.TextTestRunner.run
        def capture(self, suite):
            value = original(self, suite)
            runs.append({'tests': value.testsRun, 'failures': len(value.failures),
                         'errors': len(value.errors), 'skipped': len(value.skipped),
                         'expected_failures': len(value.expectedFailures),
                         'unexpected_successes': len(value.unexpectedSuccesses)})
            return value
        unittest.TextTestRunner.run = capture
        suite = ROOT / 'tests' / (name + '.py')
        sys.argv = [str(suite)]
        try:
            runpy.run_path(str(suite), run_name='__main__')
            exit_code = 0
        except SystemExit as error:
            exit_code = 0 if error.code is None else int(error.code)
        if denied or not runs or any(row[key] for row in runs for key in (
                'failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes')):
            raise AssertionError('Every release check must pass; skips and guard denials are failures')
        result['status'] = 'passed' if exit_code == 0 else 'failed'
    except BaseException as error:
        exit_code = 1
        result['error'] = f'{type(error).__name__}: {error}'
        traceback.print_exc()
    finally:
        result.update(exit_code=exit_code, runs=runs, guard_denials=denied,
                      opened_sqlite_count=len(opened))
        result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--suite', choices=SUITES, action='append')
    parser.add_argument('--child', choices=SUITES)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.child:
        return run_child(args.child, output / 'sandbox', output / 'result.json')
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for name in args.suite or SUITES:
        directory = output / name
        directory.mkdir()
        with (directory / 'run.log').open('w', encoding='utf-8') as log:
            process = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                '--output', str(directory), '--child', name], cwd=ROOT,
                stdout=log, stderr=subprocess.STDOUT, timeout=300)
        path = directory / 'result.json'
        record = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'status': 'failed', 'suite': name}
        record['process_exit_code'] = process.returncode
        records.append(record)
        print(f"{name}: {record['status']}", flush=True)
        if process.returncode:
            print((directory / 'run.log').read_text(encoding='utf-8', errors='replace')[-6000:])
    passed = all(row['status'] == 'passed' and row['process_exit_code'] == 0 for row in records)
    summary = {'status': 'passed' if passed else 'failed', 'suites': records,
               'tests': sum(run['tests'] for row in records for run in row.get('runs', []))}
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
