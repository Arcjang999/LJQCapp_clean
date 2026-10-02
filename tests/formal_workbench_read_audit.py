"""Open saved workbenches without submitting any form or changing original records."""
from collections import Counter
from datetime import date
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(database_path, output):
    import database as db
    from services.daily_overview_service import list_overview_bindings, get_daily_overview
    from streamlit.testing.v1 import AppTest
    db.DB_PATH = Path(database_path).resolve()
    db.LEGACY_DB_CANDIDATES = []
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    tables = ('results', 'zscore_runs', 'zscore_level_results', 'instant_results',
              'qc_result_contexts', 'qc_result_context_levels', 'qc_result_evaluations')

    def saved_rows():
        with db.read_snapshot() as connection:
            return {table: {r['id']: dict(r) for r in connection.execute('SELECT * FROM ' + table)}
                    for table in tables}

    before = saved_rows()
    bindings = [row for row in list_overview_bindings() if row['runtime_batch_id'] is not None]
    assert len({r['overview_key'] for r in bindings}) == len(bindings)
    result = {'source_database': str(db.DB_PATH), 'inspection_mode': 'Direct workbench reads; no submitted form and no database copy',
              'bindings': [], 'overview': {}}
    for binding in bindings:
        method, batch = binding['qc_method'], binding['runtime_batch_id']
        app = AppTest.from_string(f'from pages.{method}_page import render_{method}_page\nrender_{method}_page()', default_timeout=60)
        prefix = {'lj': '', 'zscore': 'zscore_', 'instant': 'instant_'}[method]
        app.session_state[prefix + 'selected_project_id'] = binding['runtime_project_id']
        app.session_state[prefix + 'selected_batch_id'] = batch
        app.run()
        errors = [str(e.value) for e in app.error]
        exceptions = [str(e.message) for e in app.exception]
        assert not exceptions, (binding['overview_key'], exceptions)
        assert not errors, (binding['overview_key'], errors)
        assert app.session_state[prefix + 'selected_batch_id'] == batch
        assert app.session_state[prefix + 'selected_project_id'] == binding['runtime_project_id']
        charts = len(app.get('image'))
        assert charts >= 1, binding['overview_key']
        result['bindings'].append({key: binding[key] for key in
            ('overview_key', 'template_name', 'test_item_name', 'config_name', 'qc_method', 'runtime_batch_id', 'instrument_name', 'material_name')})
        result['bindings'][-1].update(charts=charts, errors=errors, exceptions=exceptions,
                                     warnings=[str(e.value) for e in app.warning])
        print(json.dumps(result['bindings'][-1], ensure_ascii=False), flush=True)

    # Verify the real transferred NG target and its source have distinct choices,
    # and that the ordinary navigation button resolves to the target LJ batch.
    overview = get_daily_overview('2026-09-28')
    target = next(row for row in overview['items'] if row.get('transfer_source_batch_id'))
    app = AppTest.from_string('''
import streamlit as st
from pages.main_page import LJ_ENTRY_LABEL
if st.session_state.get('pending_top_level_method') == LJ_ENTRY_LABEL:
    from pages.lj_page import render_lj_page
    render_lj_page()
else:
    from ui.daily_overview import render_daily_overview
    render_daily_overview()
''', default_timeout=60)
    app.session_state['daily_day'] = date(2026, 9, 28)
    app.session_state['daily_selected'] = target['overview_key']
    app.run()
    assert not app.exception and not app.error
    assert len(app.selectbox(key='daily_selected').options) == len(overview['items']) + 1
    next(button for button in app.button if button.label == '查看质控图与单份月报').click().run()
    assert not app.exception and not app.error
    assert app.session_state['selected_batch_id'] == target['runtime_batch_id']
    assert app.get('image')
    result['overview'] = {'unique_choice_count': len(overview['items']), 'day_count': overview['count'],
                          'transferred_target': target['overview_key'], 'target_chart_route_passed': True}
    after = saved_rows()
    for table in tables:
        assert all(after[table].get(identifier) == row for identifier, row in before[table].items()), table
    result.update(passed=True, inspected_workbenches=len(bindings), methods=dict(Counter(r['qc_method'] for r in bindings)),
                  existing_source_and_evidence_rows_unchanged=True,
                  counts_before={table: len(rows) for table, rows in before.items()},
                  counts_after={table: len(rows) for table, rows in after.items()})
    (output / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'bindings'}, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    run(args.database, args.output)
