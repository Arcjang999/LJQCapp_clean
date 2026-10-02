"""Read the two added instant scales and preview their next group without saving."""
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import database as db
from services.daily_context_service import get_daily_context
from services.daily_draft_service import new_draft
from services.daily_overview_service import list_overview_bindings
from streamlit.testing.v1 import AppTest


def run():
    db.DB_PATH = ROOT / 'data/qc_lj_app.db'
    output = ROOT / 'output/teaching-demo-audit-2026-09-28'
    def digest():
        with db.read_snapshot() as connection:
            # Normal workbench opening refreshes existing binding bookkeeping.
            # Another task may also export reports; compare all saved detection
            # values and immutable evidence, which this audit must not alter.
            saved = {table: [dict(row) for row in connection.execute('SELECT * FROM ' + table + ' ORDER BY id')]
                     for table in ('results', 'zscore_runs', 'zscore_level_results', 'instant_results',
                                   'qc_result_contexts', 'qc_result_context_levels', 'qc_result_evaluations')}
            return sha256(json.dumps(saved, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    before = digest()
    matrix = []
    with db.read_snapshot() as connection:
        for method, table, column in [('lj', 'results', 'lj_result_id'), ('zscore', 'zscore_runs', 'zscore_run_id'), ('instant', 'instant_results', 'instant_result_id')]:
            for scale in ('raw', 'ct', 'log'):
                rows = connection.execute(f'''SELECT r.id,r.batch_id FROM {table} r
                    JOIN qc_result_contexts x ON x.{column}=r.id
                    WHERE json_extract(x.config_snapshot_json,'$.input_value_type')=?''', (scale,)).fetchall()
                assert rows, (method, scale)
                matrix.append(dict(method=method, scale=scale, saved_count=len(rows), batch_ids=sorted({r['batch_id'] for r in rows})))
    bindings = [row for row in list_overview_bindings() if row['qc_method'] == 'instant' and row['input_value_type'] in ('raw', 'log')]
    assert len(bindings) == 2
    cases = []
    for binding in bindings:
        app = AppTest.from_string('''
from pages.instant_page import render_instant_page
render_instant_page()
''', default_timeout=30)
        app.session_state['instant_selected_project_id'] = binding['runtime_project_id']
        app.session_state['instant_selected_batch_id'] = binding['runtime_batch_id']
        app.run()
        assert not app.exception and not app.error, str(list(app.exception))
        assert app.session_state['instant_selected_batch_id'] == binding['runtime_batch_id']
        assert app.get('image')
        with db.read_snapshot() as connection:
            saved = [dict(row) for row in connection.execute('SELECT * FROM instant_results WHERE batch_id=? ORDER BY id', (binding['runtime_batch_id'],))]
        assert len(saved) == 3
        context = get_daily_context(**{key: binding[key] for key in ('lab_instrument_id', 'qc_material_id', 'qc_material_lot_id', 'template_id', 'lot_config_id')},
                                    test_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        assert not context['issues'] and len(context['items']) == 1
        item = context['items'][0]
        assert item['writable'] and not item['issues']
        assert item['input_value_type'] == binding['input_value_type']
        assert item['unit_symbol'] == binding['unit_symbol']
        draft = new_draft(context, operator='检验员')
        value = saved[-1]['log_value'] if binding['input_value_type'] == 'log' else saved[-1]['value']
        draft['values'] = {key: str(value) for key in draft['values']}
        daily = AppTest.from_string('''
import database as db
import streamlit as st
import ui.daily_grid as grid
grid._GRID=st.components.v2.component('daily_result_grid',html=grid.HTML,css=grid.CSS,js=grid.JS)
from ui.daily_entry import render_daily_entry
with db.read_snapshot():
    render_daily_entry()
''', default_timeout=30)
        daily.session_state['daily_entry_template_id'] = binding['template_id']
        daily.session_state['entry_lot'] = binding['qc_material_lot_id']
        daily.session_state['daily_draft'] = draft
        daily.run()
        assert not daily.exception and not daily.error, str(list(daily.exception))
        daily.button(key='entry_validate').click().run()
        assert not daily.exception and not daily.error, str(list(daily.exception))
        assert daily.session_state['daily_draft']['frozen'] is not None
        assert not daily.button(key='entry_commit').disabled
        cases.append(dict(project=binding['template_name'], method='instant', scale=binding['input_value_type'], unit=binding['unit_symbol'],
                          batch_id=binding['runtime_batch_id'], saved_count=len(saved), chart_count=len(app.get('image')),
                          writable_context=True, group_preview_passed=True, save_not_clicked=True))
    assert digest() == before
    result = dict(passed=True, source_database=str(db.DB_PATH), cases=cases, method_scale_matrix=matrix,
                  total_saved_count=sum(row['saved_count'] for row in matrix), source_and_evidence_sha256=before,
                  original_records_and_evidence_unchanged=True, group_previews_in_query_only_transactions=True)
    (output / 'formal-instant-scales.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    run()
