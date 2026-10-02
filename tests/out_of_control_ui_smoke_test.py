"""Isolated AppTest acceptance for saved handling pages (not clinical evidence).

Native browser upload is covered separately. Here genuine staged attachments
are injected into the page draft to verify its unsaved/saved presentation.
"""
from copy import deepcopy
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
import database
from services import out_of_control_service as service
from services import out_of_control_attachment_service as attachments
from tests.out_of_control_service_smoke_test import seed_engineering_sources


APP = """
from pages.out_of_control_page import render_out_of_control_page
render_out_of_control_page()
"""


class HandlingPageTest(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        names = ('DB_PATH', 'DEFAULT_DB_PATH', 'STORAGE_CONFIG_PATH', 'LEGACY_DB_CANDIDATES')
        self.original = {name: getattr(database, name) for name in names}
        database.DB_PATH = database.DEFAULT_DB_PATH = self.root / 'isolated.db'
        database.STORAGE_CONFIG_PATH = self.root / 'config.json'
        database.LEGACY_DB_CANDIDATES = []
        database.init_db()
        self.sources = seed_engineering_sources()
        self.event = service.open_event(*self.sources['lj'], 'ui-open-lj', '登记员')
        self.other = service.open_event(*self.sources['z3'], 'ui-open-z3', '登记员')
        self.initial_source_rows = self.source_rows()

    def tearDown(self):
        self.assertEqual(self.initial_source_rows, self.source_rows())
        for name, value in self.original.items():
            setattr(database, name, value)
        self.temporary.cleanup()

    def source_rows(self):
        with database.get_connection() as connection:
            return {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY id')]
                    for table in ('results', 'zscore_runs', 'zscore_level_results',
                                  'qc_result_contexts', 'qc_result_context_levels', 'qc_result_evaluations')}

    def open_page(self, event=None):
        app = AppTest.from_string(APP, default_timeout=20)
        if event is not None:
            self.select(app, event)
        else:
            app.run()
        self.assert_healthy(app)
        return app

    def select(self, app, event):
        app.session_state['ooc_selection'] = dict(event_id=event['event_id'],
            source_type=event['source_type'], source_id=event['source_id'], from_list=True)
        app.run()
        self.assert_healthy(app)

    def assert_healthy(self, app):
        self.assertFalse(list(app.exception), str(list(app.exception)))

    def widget(self, app, kind, label):
        matches = [item for item in getattr(app, kind) if item.label == label]
        self.assertEqual(len(matches), 1, f'{kind}: {label}; found {len(matches)}')
        return matches[0]

    def fill(self, app, kind, label, value):
        self.widget(app, kind, label).set_value(value).run()
        self.assert_healthy(app)

    def click(self, app, label):
        self.widget(app, 'button', label).click().run()
        self.assert_healthy(app)

    def fill_required(self, app):
        self.fill(app, 'selectbox', '原因分类', '其他')
        for label, value in [('原因分析', '核对后记录原因'), ('纠正措施', '完成纠正措施'),
                             ('处理效果说明', '人工核对效果'), ('效果依据', '独立原始记录')]:
            self.fill(app, 'text_area', label, value)
        self.fill(app, 'text_input', '处理人', '处理员')

    def current(self, event=None):
        return service.get_event((event or self.event)['event_id'])

    def test_unchanged_initial_open_returns_without_discard_prompt(self):
        app = self.open_page(self.event)
        self.click(app, '返回处理列表')
        self.assertFalse(any('尚未保存' in item.value for item in app.warning))
        self.assertTrue(any(item.key == 'ooc_list_back' for item in app.button))
        self.assertEqual(self.current()['revision_no'], 1)

    def test_pending_submit_explains_save_first_and_enables_after_save(self):
        app = self.open_page(self.event)
        submit = self.widget(app, 'button', '提交效果确认')
        self.assertTrue(submit.disabled)
        self.assertIn('请先保存处理草稿', submit.proto.help)
        self.fill_required(app)
        self.assertTrue(self.widget(app, 'button', '提交效果确认').disabled)
        self.click(app, '保存处理草稿')
        self.assertFalse(self.widget(app, 'button', '提交效果确认').disabled)
        self.click(app, '提交效果确认')
        self.assertEqual(self.current()['status'], 'pending_confirmation')

    def test_saved_retest_removal_reason_survives_reruns_and_saves_history(self):
        kind, source_id = self.sources['later']
        content = {**self.event['content'], 'handler_text': '处理员',
                   'retest_refs': [{'source_type': kind, 'source_id': source_id,
                                    'difference_reason': '已人工核对旧资料未记录的参数和试剂批号'}]}
        saved = service.save_handling(self.event['event_id'], 1, 'ui-link-retest',
                                      'save_draft', content, '处理员')
        app = self.open_page(saved)
        self.assertEqual(self.widget(app, 'multiselect', '选择后续复测').value, [(kind, source_id)])
        self.fill(app, 'multiselect', '选择后续复测', [])
        self.fill(app, 'text_input', '取消复测关联的原因', '关联错误，保留说明后取消')
        app.run()
        self.assert_healthy(app)
        self.assertEqual(self.widget(app, 'text_input', '取消复测关联的原因').value, '关联错误，保留说明后取消')
        self.fill(app, 'text_area', '补充说明', '填写其他字段也不能丢失取消原因')
        self.assertEqual(self.widget(app, 'text_input', '取消复测关联的原因').value, '关联错误，保留说明后取消')
        self.click(app, '保存处理草稿')
        self.assertFalse(list(app.error))
        self.assertEqual(self.current()['revision_no'], 3)
        self.assertEqual(self.current()['retest_refs'], [])
        self.assertEqual(self.current()['change_reason'], '关联错误，保留说明后取消')
        self.assertEqual(service.get_event(self.event['event_id'], 2)['retest_refs'][0]['source_id'], source_id)

    def test_log_snapshot_displays_recorded_log_value(self):
        # Rendering-only boundary: distinct raw/log fields make a wrong column visible.
        snapshot = deepcopy(self.other['origin_snapshot'])
        snapshot['input_value_type'] = 'log'
        for level, raw, logarithm in zip(snapshot['levels'], [1000, 2000, 3000], [3.0, 3.30103, 3.47712]):
            level.update(value=raw, log_value=logarithm)
        app = AppTest.from_string("import streamlit as st\nfrom ui.out_of_control import render_snapshot\nrender_snapshot(st.session_state['snapshot'])", default_timeout=20)
        app.session_state['snapshot'] = snapshot
        app.run()
        self.assert_healthy(app)
        levels = [element.value for element in app.dataframe if '检测值' in element.value.columns]
        self.assertEqual(len(levels), 1)
        self.assertEqual(levels[0]['检测值'].tolist(), ['3.0', '3.30103', '3.47712'])

    def test_invalid_time_retains_inputs_and_explicit_save_survives_reopen(self):
        app = self.open_page(self.event)
        self.fill(app, 'text_input', '处理人', '处理员')
        self.fill(app, 'text_area', '原因分析', '尚未保存的分析')
        label = '影响开始时间（可选，年-月-日 时:分:秒）'
        self.fill(app, 'text_input', label, '不是时间')
        self.click(app, '保存处理草稿')
        self.assertTrue(list(app.error))
        self.assertEqual(self.current()['revision_no'], 1)
        self.assertEqual(self.widget(app, 'text_input', label).value, '不是时间')
        self.assertEqual(self.widget(app, 'text_area', '原因分析').value, '尚未保存的分析')
        self.fill(app, 'text_input', label, '2026-09-01 09:00:00')
        self.click(app, '保存处理草稿')
        self.assertEqual(self.current()['revision_no'], 2)
        self.assertEqual(self.current()['status'], 'in_progress')
        reopened = self.open_page(self.current())
        self.assertEqual(self.widget(reopened, 'text_area', '原因分析').value, '尚未保存的分析')
        self.assertEqual(self.widget(reopened, 'text_input', label).value, '2026-09-01 09:00:00')
        self.assertEqual(len(self.current()['history']), 2)

    def test_navigation_drafts_isolate_events_and_discard_does_not_write(self):
        app = self.open_page(self.event)
        self.fill(app, 'text_area', '原因分析', '第一事件草稿')
        self.click(app, '返回处理列表')
        self.assertTrue(any('尚未保存' in item.value for item in app.warning))
        self.click(app, '继续填写')
        self.assertEqual(self.widget(app, 'text_area', '原因分析').value, '第一事件草稿')
        self.click(app, '返回处理列表')
        self.click(app, '保留本次草稿并返回')
        self.assertTrue(any(item.key == 'ooc_list_back' for item in app.button))
        self.select(app, self.other)
        self.assertEqual(self.widget(app, 'text_area', '原因分析').value, '')
        self.fill(app, 'text_area', '原因分析', '第二事件草稿')
        self.click(app, '返回处理列表')
        self.click(app, '放弃本次修改')
        self.select(app, self.event)
        self.assertEqual(self.widget(app, 'text_area', '原因分析').value, '第一事件草稿')
        self.assertEqual(self.current()['revision_no'], 1)
        self.assertEqual(self.current(self.other)['revision_no'], 1)
        self.select(app, self.other)
        self.assertEqual(self.widget(app, 'text_area', '原因分析').value, '')

    def test_confirmation_required_return_and_new_revision_history(self):
        app = self.open_page(self.event)
        self.fill_required(app)
        self.click(app, '保存处理草稿')
        self.click(app, '提交效果确认')
        self.assertEqual(self.current()['status'], 'pending_confirmation')
        self.assertTrue(self.widget(app, 'text_area', '原因分析').disabled)
        self.click(app, '确认完成')
        self.assertTrue(list(app.error))
        self.assertEqual(self.current()['revision_no'], 3)
        self.fill(app, 'text_input', '确认人', '确认员')
        self.fill(app, 'text_input', '确认时间（年-月-日 时:分:秒）', service._now())
        self.click(app, '确认完成')
        self.assertTrue(any('明确确认' in item.value for item in app.error))
        self.assertEqual(self.current()['revision_no'], 3)
        self.click(app, '退回继续处理')
        self.assertTrue(any('操作人' in item.value for item in app.error))
        self.fill(app, 'text_input', '本次退回人', '复核退回员')
        self.click(app, '退回继续处理')
        self.assertTrue(any('原因' in item.value for item in app.error))
        self.fill(app, 'text_input', '退回原因', '补充依据')
        self.click(app, '退回继续处理')
        self.assertEqual(self.current()['revision_no'], 4)
        self.assertEqual(self.current()['status'], 'in_progress')
        self.assertEqual(self.current()['saved_by'], '复核退回员')
        self.assertEqual(self.current()['content']['handler_text'], '处理员')
        self.fill(app, 'text_area', '效果依据', '补充后的独立记录')
        self.click(app, '提交效果确认')
        self.fill(app, 'text_input', '确认人', '确认员')
        self.fill(app, 'text_input', '确认时间（年-月-日 时:分:秒）', service._now())
        self.fill(app, 'checkbox', '已核对处理效果及相关依据', True)
        self.click(app, '确认完成')
        self.assertEqual(self.current()['status'], 'completed')
        self.assertEqual(self.current()['revision_no'], 6)
        self.click(app, '新增处理修订')
        self.assertTrue(any('操作人' in item.value for item in app.error))
        self.fill(app, 'text_input', '本次修订人', '后续修订员')
        self.click(app, '新增处理修订')
        self.assertTrue(any('原因' in item.value for item in app.error))
        self.fill(app, 'text_input', '补充或更正原因', '追加调查资料')
        self.click(app, '新增处理修订')
        self.assertEqual(self.current()['status'], 'in_progress')
        self.assertEqual(self.current()['revision_no'], 7)
        self.assertEqual(self.current()['saved_by'], '后续修订员')
        self.assertEqual(next(row for row in self.current()['history'] if row['revision_no'] == 4)['saved_by'], '复核退回员')
        self.assertEqual(next(row for row in self.current()['history'] if row['revision_no'] == 7)['saved_by'], '后续修订员')
        self.assertEqual(service.get_event(self.event['event_id'], 6)['status'], 'completed')
        self.assertEqual([row['revision_no'] for row in self.current()['history']], list(range(1, 8)))

    def test_attachment_staged_and_saved_lists_and_history(self):
        item = attachments.stage_attachment(self.event['event_id'], '复测数据.csv', b'value\n1\n',
                                             uploaded_by='处理员', description='上传原始记录')
        app = self.open_page(self.event)
        self.fill(app, 'text_input', '处理人', '处理员')
        drafts = deepcopy(app.session_state['ooc_drafts'])
        drafts[str(self.event['event_id'])]['content']['attachment_ids'] = [item['id']]
        app.session_state['ooc_drafts'] = drafts
        app.run()
        self.assert_healthy(app)
        self.assertTrue(any('复测数据.csv（本次草稿）' in element.value for element in app.markdown))
        self.assertFalse(list(app.get('download_button')))
        self.assertEqual(attachments.list_attachments(self.event['event_id']), [])
        self.click(app, '保存处理草稿')
        labels = [element.proto.label for element in app.get('download_button')]
        self.assertIn('复测数据.csv', labels)
        self.assertIn('历史附件：复测数据.csv', labels)
        self.assertEqual(attachments.list_staged_attachments(self.event['event_id']), [])
        self.click(app, '移出本次')
        self.assertEqual(attachments.read_attachment(self.event['event_id'], item['id']), b'value\n1\n')
        self.click(app, '保存处理草稿')
        self.assertEqual(self.current()['attachment_ids'], [])
        self.assertEqual(attachments.read_attachment(self.event['event_id'], item['id'], 2), b'value\n1\n')
        self.select(app, self.other)
        self.assertFalse(list(app.get('download_button')))


SOURCE_APP = """
import streamlit as st
from ui.out_of_control import render_event_detail
from pages.lj_sections import render_lj_records_section, render_lj_abnormal_note_quick_entry
from pages.zscore_sections import render_zscore_rule_records_overview_section, render_zscore_abnormal_note_quick_entry
if st.session_state.get('ooc_selection'):
    render_event_detail(st.session_state['ooc_selection'])
else:
    context = st.session_state['source_context']
    mode = st.session_state['source_mode']
    if mode == 'lj_records':
        render_lj_records_section(context['qc_df'], context['input_value_type'])
    elif mode == 'lj_latest':
        render_lj_abnormal_note_quick_entry(context['qc_df'].loc[lambda frame: frame.status == '失控'].iloc[0])
    elif mode == 'zscore_records':
        render_zscore_rule_records_overview_section(context)
    else:
        render_zscore_abnormal_note_quick_entry(next(row for row in context['history_runs'] if row['run_status'] == 'reject'))
"""


class BusinessEntryPageTest(unittest.TestCase):
    """Real configured materials and algorithm-generated rows through entry UI."""
    assert_healthy = HandlingPageTest.assert_healthy
    widget = HandlingPageTest.widget
    fill = HandlingPageTest.fill
    click = HandlingPageTest.click
    source_rows = HandlingPageTest.source_rows

    def test_instant_abnormal_chart_and_records_have_no_formal_event_entry(self):
        from tests.instant_v12_fixtures import seed_instant_configuration
        from tests.instant_v12_integration_smoke_test import entry
        from services.instant_service import build_instant_workbench_context
        original = {name: getattr(database, name) for name in
                    ('DB_PATH', 'DEFAULT_DB_PATH', 'STORAGE_CONFIG_PATH', 'LEGACY_DB_CANDIDATES')}
        try:
            with TemporaryDirectory() as temporary:
                root = Path(temporary)
                database.DB_PATH = database.DEFAULT_DB_PATH = root / 'instant.db'
                database.STORAGE_CONFIG_PATH = root / 'config.json'
                database.LEGACY_DB_CANDIDATES = []
                database.init_db()
                fixture = seed_instant_configuration()
                for index, value in enumerate([100, 100.1, 99.9, 100.2, 130]):
                    entry(fixture, index, value)
                context = build_instant_workbench_context(fixture['batch_id'])
                self.assertTrue(context['summary']['si_ready'])
                self.assertEqual(context['summary']['si_status'], 'reject')
                self.assertIn('疑似离群', context['analysis_df']['status'].tolist())
                app = AppTest.from_string("""
import streamlit as st
from pages.instant_page import _render_instant_chart_analysis_section, _render_instant_records_section
_render_instant_chart_analysis_section(st.session_state['context'])
_render_instant_records_section(st.session_state['context'])
""", default_timeout=30)
                app.session_state['context'] = context
                app.run()
                self.assert_healthy(app)
                self.assertFalse([button for button in app.button if '处理' in button.label])
                self.assertEqual(len(app.dataframe), 1)
                with database.get_connection() as connection:
                    self.assertEqual(connection.execute('SELECT COUNT(*) FROM qc_ooc_events').fetchone()[0], 0)
        finally:
            for name, value in original.items():
                setattr(database, name, value)

    def test_lj_and_multilevel_entry_sources_reopen_same_event(self):
        from tests.b1_business_chain import seed
        from pages.lj_sections import build_lj_workbench_context
        from pages.zscore_sections import build_zscore_workbench_context
        original = {name: getattr(database, name) for name in
                    ('DB_PATH', 'DEFAULT_DB_PATH', 'STORAGE_CONFIG_PATH', 'LEGACY_DB_CANDIDATES')}
        try:
            with TemporaryDirectory() as temporary:
                with redirect_stdout(StringIO()):
                    fixture = seed(Path(temporary))
                contexts = [build_lj_workbench_context(row['runtime_batch_id']) if row['qc_method'] == 'lj'
                            else build_zscore_workbench_context(row['runtime_batch_id']) for row in fixture['bindings']]
                before = self.source_rows()
                events = []
                for binding, context in zip(fixture['bindings'], contexts):
                    method = binding['qc_method']
                    source = next(row for row in fixture['sources'] if row['method'] == method
                                  and row['level_count'] == binding['fixture_level_count'] and row['position'] == 0)
                    event_ids = []
                    for mode in (method + '_records', method + '_latest'):
                        app = AppTest.from_string(SOURCE_APP, default_timeout=30)
                        app.session_state['source_context'] = context
                        app.session_state['source_mode'] = mode
                        app.run()
                        self.assert_healthy(app)
                        if mode.endswith('_records'):
                            self.fill(app, 'selectbox', '选择需要处理的异常记录', source['source_id'])
                        self.click(app, '打开失控处理')
                        selection = app.session_state['ooc_selection']
                        self.assertEqual(selection['source_type'], source['source_type'])
                        self.assertEqual(selection['source_id'], source['source_id'])
                        self.fill(app, 'text_input', '登记人', '来源页面验收人员')
                        self.click(app, '登记并打开处理记录')
                        event_id = app.session_state['ooc_selection']['event_id']
                        event = service.get_event(event_id)
                        self.assertEqual(len(event['origin_snapshot']['levels']), source['level_count'])
                        self.assertEqual(event['revision_no'], 1)
                        event_ids.append(event_id)
                    self.assertEqual(event_ids[0], event_ids[1])
                    events.append(event_ids[0])
                self.assertEqual(len(set(events)), 3)
                with database.get_connection() as connection:
                    self.assertEqual(connection.execute('SELECT COUNT(*) FROM qc_ooc_events').fetchone()[0], 3)
                self.assertEqual(before, self.source_rows())
        finally:
            for name, value in original.items():
                setattr(database, name, value)


if __name__ == '__main__':
    unittest.main(verbosity=2)
