"""User navigation and readable historical evidence, using an isolated database."""
import json
import sys
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import add_result, get_connection, get_results
from pages.main_page import METHOD_ENTRY_OPTIONS
from qc_logic import calculate_qc_results
from services.instant_service import save_instant_result
from services.lot_lifecycle_service import create_target_profile, list_lot_events
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.instant_v12_fixtures import seed_instant_configuration
from tests.lot_lifecycle_smoke_test import lj, verified, switch
from tests.zscore_v12_fixtures import seed_zscore_configuration
from ui.traceability import evaluation_tables, event_history_table, target_history_table
from zscore_logic import create_zscore_run


def test_saved_evidence_is_readable_and_unchanged():
    with IsolatedDatabase():
        batch = lj()
        source, lot, verification = verified('lj', batch, 'REAGENT-001')
        switch(source, lot, verification)
        create_target_profile(method='lj', batch_id=batch,
            levels=[{'level_id': 'Level 1', 'mean': 100, 'sd': 2}],
            source='manual', evidence='实验室确认依据', confirmed_by='确认人', effective_at='2026-09-02')
        value = 101.123456789
        add_result(batch, '2026-09-03', value, operator='检测人', lot_selection={'reagent_lot_id': lot})
        calculate_qc_results(get_results(batch), 5)
        zscore = seed_zscore_configuration(target_source='manual')
        create_target_profile(method='zscore', batch_id=zscore['batch_id'],
            levels=[{'level_id': 'Level 1', 'mean': 100, 'sd': 2}, {'level_id': 'Level 2', 'mean': 200, 'sd': 2}],
            source='manual', evidence='双水平确认', confirmed_by='确认人', effective_at='2026-09-02')
        create_zscore_run(batch_id=zscore['batch_id'], test_time='2026-09-03', operator='检测人',
            level_results=[{'level_id': 'Level 1', 'raw_value': 101}, {'level_id': 'Level 2', 'raw_value': 201}],
            template_id='2_level_classic', required_n=5)
        instant = seed_instant_configuration(instrument="即时法专用仪器")
        for i in range(3):
            save_instant_result(batch_id=instant['batch_id'], test_time=f'2026-09-03 08:0{i}:00',
                value=100+i, log_value=None, operator='检测人')
        with get_connection() as connection:
            before = connection.execute('SELECT evaluation_json FROM qc_result_evaluations ORDER BY id').fetchall()
            summaries, level_tables = [], []
            for row in before:
                payload = json.loads(row[0])
                original = json.dumps(payload, sort_keys=True)
                summary, levels = evaluation_tables(payload, 'raw', {'Level 1': '低值', 'Level 2': '高值'})
                assert not summary.empty
                if payload.get('phase') == 'formal_qc':
                    assert '参与参数建立' not in summary['项目'].tolist()
                    assert set(levels['参与参数建立']) == {'不适用（正式期）'}
                assert json.dumps(payload, sort_keys=True) == original
                summaries.append(summary)
                if not levels.empty: level_tables.append(levels)
            assert str(value) in pd.concat(summaries)['记录内容'].tolist()
            assert any(table['质控水平'].tolist() == ['低值', '高值'] for table in level_tables)
            assert any('上侧 SI' in table['项目'].tolist() for table in summaries)
            events = event_history_table(connection, list_lot_events(source['system_id']))
            assert 'REAGENT-001' in events['调整后'].tolist()
            targets = target_history_table(pd.read_sql_query('SELECT * FROM qc_target_profiles', connection))
            assert '实验室确认依据' in targets['确认依据'].tolist()
            assert before == connection.execute('SELECT evaluation_json FROM qc_result_evaluations ORDER BY id').fetchall()


def test_home_and_global_navigation_on_empty_database():
    with IsolatedDatabase():
        app = AppTest.from_file(str(ROOT/'app.py'), default_timeout=15).run()
        assert not list(app.exception)
        assert list(app.radio(key='top_level_method_selector').options) == METHOD_ENTRY_OPTIONS
        for target, close in [('open_master_data_page', 'close_master_data_page'),
                              ('open_project_management_page', 'close_project_management_page'),
                              ('open_report_history_page', 'close_report_history_page')]:
            app.button(key=target).click().run()
            assert not list(app.exception)
            app.button(key=close).click().run()
            assert not list(app.exception)
        for card in ('open_main_lj_card', 'open_main_zscore_card', 'open_main_instant_card'):
            app.button(key=card).click().run()
            assert not list(app.exception)
            app.radio(key='top_level_method_selector').set_value(METHOD_ENTRY_OPTIONS[0]).run()
            assert not list(app.exception)


if __name__ == '__main__':
    test_saved_evidence_is_readable_and_unchanged()
    test_home_and_global_navigation_on_empty_database()
    print('user_interface_smoke_test passed')
