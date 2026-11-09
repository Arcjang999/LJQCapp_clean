"""Actual-SKU project entry, complete three-method saves and overview filters."""
from pathlib import Path
from datetime import datetime
import sys
import unittest

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import get_connection
from services.daily_overview_service import get_daily_overview, list_overview_bindings
from services.master_data_service import create_test_item
from services.material_workflow_service import create_material_config
from services.project_config_service import (create_project_template, save_template_items, list_template_items,
    list_lot_config_items, activate_project_template, activate_lot_config)
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.multi_product_control_workflow_smoke_test import multi_values
from tests.quality_review_fixtures import confirm_fixture_project, confirm_fixture_lot, confirm_fixture_product_choice
from tests.quick_project_setup_smoke_test import database_contents


def seed(*, only_second=False):
    from services.workbench_config_service import sync_lj_workbench_bindings
    from services.zscore_workbench_service import sync_zscore_workbench_bindings
    from services.instant_workbench_service import sync_instant_workbench_bindings
    from services.lot_lifecycle_service import (workbench_systems, create_reagent_lot,
        record_lot_verification, switch_reagent_lots)
    values = multi_values()
    project = create_project_template(template_name='逐产品三方法录入',
        lab_instrument_id=values['lab_instrument_id'], qc_material_id=values['qc_material_id'],
        default_reagent_id=values['reagent_id'])
    methods = ('lj', 'instant') if only_second else ('zscore', 'lj', 'instant')
    save_template_items(project, [dict(test_item_id=create_test_item(chinese_name='实际SKU录入-' + method,
        default_unit_id=values['unit_id']), qc_method=method, input_value_type='raw',
        unit_id=values['unit_id'], method_id=values['method_id'], reagent_id=values['reagent_id'],
        level_count=2 if method == 'zscore' else 1, target_n=20) for method in methods])
    confirm_fixture_project(project)
    activate_project_template(project)
    items = list_template_items(project).to_dict('records')
    config = create_material_config(template_id=project, selections={row['id']:
        values['level_ids'] if row['qc_method'] == 'zscore' else values['level_ids'][1:] for row in items},
        config_name='内部旧组合名不可作为实际产品显示')
    for item in list_lot_config_items(config).to_dict('records'):
        confirm_fixture_product_choice('lot', item['id'])
    confirm_fixture_lot(config)
    activate_lot_config(config)
    sync_lj_workbench_bindings(); sync_zscore_workbench_bindings(); sync_instant_workbench_bindings()
    reagent = create_reagent_lot(reagent_id=values['reagent_id'], lot_no='SKU-UI-REAGENT', expiry_date='2029-12-31')
    checks = []
    for system in workbench_systems():
        verification = record_lot_verification(template_item_id=system['template_item_id'], system_id=system['id'],
            reagent_lot_id=reagent, conclusion='pass', evidence='三方法实际SKU录入夹具',
            confirmed_by='实际试剂核对人', confirmed_at='2026-09-01')
        checks.append(dict(template_item_id=system['template_item_id'], system_id=system['id'],
            reagent_lot_id=reagent, verification_id=verification, expected_revision=0))
    switch_reagent_lots(selections=checks, effective_at='2026-09-01', operator='实际试剂核对人', reason='三方法录入夹具')
    with get_connection() as c:
        lots = [c.execute('SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?', (level,)).fetchone()[0]
                for level in values['level_ids']]
    return dict(values=values, template_id=project, config_id=config, lots=lots, reagent=reagent)


def entry_page():
    import streamlit as st
    import ui.daily_grid as grid
    from ui.daily_navigation import prepare_project_daily_entry
    from ui.daily_entry import render_daily_entry
    grid._GRID = st.components.v2.component('daily_result_grid', html=grid.HTML, css=grid.CSS, js=grid.JS)
    if st.button('打开项目整组录入', key='open_actual_product_project'):
        prepare_project_daily_entry(st.session_state['actual_product_template'])
    render_daily_entry()


def overview_page():
    from ui.daily_overview import render_daily_overview
    render_daily_overview()


def healthy(app):
    assert not list(app.exception), [error.message for error in app.exception]


def click(app, label):
    next(button for button in app.button if button.label == label).click().run()
    healthy(app)


def open_project(fixture):
    app = AppTest.from_function(entry_page, default_timeout=25)
    app.session_state['actual_product_template'] = fixture['template_id']
    app.run()
    app.button(key='open_actual_product_project').click().run()
    healthy(app)
    return app


class MultiProductDailyUiTests(unittest.TestCase):
    def test_second_sku_stays_selected_and_saves_all_three_complete_methods(self):
        with IsolatedDatabase():
            fixture = seed()
            first, second = fixture['values']['level_product_ids']
            app = open_project(fixture)
            self.assertEqual(first, app.selectbox(key='entry_product').value)
            self.assertFalse(app.selectbox(key='entry_product').disabled)
            self.assertTrue(any('SKU-002' in label and '另一厂家' in label for label in app.selectbox(key='entry_product').options))
            app.selectbox(key='entry_product').set_value(second).run()
            app.selectbox(key='entry_lot').set_value(fixture['lots'][1]).run()
            self.assertEqual(second, app.selectbox(key='entry_product').value)
            app.button(key='entry_start').click().run()
            healthy(app)
            draft = app.session_state['daily_draft']
            self.assertEqual({'lj', 'zscore', 'instant'}, {item['qc_method'] for item in draft['context']['items']})
            self.assertTrue(all(item['writable'] for item in draft['context']['items']), draft['context']['items'])
            self.assertEqual([2, 1, 1], [len(item['levels']) for item in draft['context']['items']])
            token = draft['draft_id']
            app.text_input(key='daily_operator_' + token).set_value('三方法录入人').run()
            next(widget for widget in app.get('date_time_input') if widget.key=='daily_test_time_' + token).set_value(datetime(2026,10,1,8)).run()
            app.text_area(key='entry_paste_' + token).set_value('100\n200\n200\n200').run()
            click(app, '预览粘贴内容'); click(app, '确认带入本组')
            before = database_contents()
            click(app, '检查本组结果')
            self.assertEqual(before, database_contents())
            self.assertFalse(app.error, [error.value for error in app.error])
            click(app, '确认保存整组')
            receipt = app.session_state['daily_draft']['receipt']
            self.assertEqual(3, receipt['count'])
            self.assertEqual({'lj', 'zscore', 'instant'}, {item['qc_method'] for item in receipt['items']})
            from pages.lj_config_section import _actual_material_label
            with get_connection() as c:
                for binding in c.execute('SELECT qc_method,runtime_batch_id FROM qc_workbench_bindings WHERE lot_config_id=?',
                                         (fixture['config_id'],)):
                    label = _actual_material_label(dict(id=binding['runtime_batch_id']), binding['qc_method'])
                    self.assertIn('SKU-002', label)
                    self.assertEqual(binding['qc_method'] == 'zscore', 'SKU-001' in label)
            with get_connection() as c:
                rows = c.execute('''SELECT CASE WHEN x.lj_result_id IS NOT NULL THEN 'lj'
                    WHEN x.zscore_run_id IS NOT NULL THEN 'zscore' ELSE 'instant' END AS qc_method,q.qc_material_id FROM qc_result_contexts x
                    JOIN qc_result_context_levels a ON a.context_id=x.id JOIN md_qc_levels l ON l.id=a.qc_level_id
                    JOIN md_qc_material_lots q ON q.id=l.qc_material_lot_id ORDER BY x.id,a.level_order''').fetchall()
            self.assertEqual({first, second}, {row['qc_material_id'] for row in rows if row['qc_method'] == 'zscore'})
            self.assertEqual({second}, {row['qc_material_id'] for row in rows if row['qc_method'] in ('lj', 'instant')})
            # Opening the same project restores the actual product from its saved draft.
            app.button(key='open_actual_product_project').click().run()
            healthy(app)
            self.assertEqual(second, app.selectbox(key='entry_product').value)
            self.assertEqual(fixture['lots'][1], app.selectbox(key='entry_lot').value)
            before = database_contents()
            bindings = list_overview_bindings()
            self.assertEqual({second}, set(next(row for row in bindings if row['qc_method'] == 'lj')['qc_material_ids']))
            self.assertEqual(3, get_daily_overview('2026-10-01', qc_material_id=second)['count'])
            self.assertEqual(1, get_daily_overview('2026-10-01', qc_material_id=first)['count'])
            overview = AppTest.from_function(overview_page, default_timeout=20).run()
            overview.date_input(key='daily_day').set_value(__import__('datetime').date(2026, 10, 1)).run()
            overview.selectbox(key='daily_material').set_value(second).run()
            healthy(overview)
            frame = next(table.value for table in overview.dataframe if '质控品与原厂批号' in table.value.columns)
            self.assertEqual(3, len(frame))
            self.assertTrue(all('SKU-002' in text for text in frame['质控品与原厂批号']))
            self.assertNotIn('内部旧组合名', frame.to_string())
            self.assertEqual(before, database_contents())
            from services.lot_lifecycle_service import create_target_profile
            from zscore_logic import create_zscore_run, get_template_id_for_level_count
            with get_connection() as c:
                zscore_batch = c.execute("SELECT runtime_batch_id FROM qc_workbench_bindings WHERE lot_config_id=? AND qc_method='zscore'",
                    (fixture['config_id'],)).fetchone()[0]
            create_target_profile(method='zscore', batch_id=zscore_batch,
                levels=[dict(level_id='Level 1', mean=100, sd=10), dict(level_id='Level 2', mean=200, sd=20)],
                source='manual', evidence='两个实际SKU的控制参数', confirmed_by='参数核对人', effective_at='2026-10-02')
            create_zscore_run(zscore_batch, '2026-10-03 08:00:00', '异常筛选检查',
                [dict(level_id='Level 1', raw_value=180), dict(level_id='Level 2', raw_value=360)],
                template_id=get_template_id_for_level_count(2), required_n=20,
                lot_selection={'reagent_lot_id': fixture['reagent']})
            before = database_contents()
            first_pending = get_daily_overview('2026-10-03', qc_material_id=first)['pending']
            second_pending = get_daily_overview('2026-10-03', qc_material_id=second)['pending']
            self.assertGreater(first_pending['count'], 0)
            self.assertEqual(first_pending['count'], second_pending['count'])
            self.assertEqual(before, database_contents())

    def test_header_product_absent_from_actual_levels_defaults_to_the_actual_sku(self):
        with IsolatedDatabase():
            fixture = seed(only_second=True)
            first, second = fixture['values']['level_product_ids']
            app = open_project(fixture)
            self.assertEqual(second, app.selectbox(key='entry_product').value)
            self.assertEqual(2, len(app.selectbox(key='entry_product').options))
            self.assertFalse(any('SKU-001' in label for label in app.selectbox(key='entry_product').options))
            app.selectbox(key='entry_lot').set_value(fixture['lots'][1]).run()
            app.button(key='entry_start').click().run()
            healthy(app)
            draft = app.session_state['daily_draft']
            self.assertEqual(second, draft['context']['selection']['qc_material_id'])
            self.assertEqual({'lj', 'instant'}, {item['qc_method'] for item in draft['context']['items']})
            self.assertEqual([], get_daily_overview('2026-10-01', qc_material_id=first)['items'])
            self.assertEqual(2, len(get_daily_overview('2026-10-01', qc_material_id=second)['items']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
