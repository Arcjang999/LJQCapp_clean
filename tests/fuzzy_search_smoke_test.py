"""Retrieval accepts partial input without weakening clinical identity checks."""
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streamlit.testing.v1 import AppTest
from database import get_connection
from services.search_service import fuzzy_match
from services.master_data_service import create_test_item, create_alias, list_test_items
from services.quality_applicability_service import numeric_candidates
from services.report_service import filter_report_history_records
from tests.instant_v12_integration_smoke_test import IsolatedDatabase


def test_partial_multikeyword_and_safe_literal_search():
    for query, values in [
        ('免球蛋白', ['血清免疫球蛋白G']), ('Ｉｇｇ', ['IgG']),
        ('2026 罗氏', ['罗氏试剂', 'LOT-2026-09']),
        ('PCR 230', ['实时荧光 PCR', 'WS/T 230—2024']),
        ('albumni', ['Albumin']), ('SCO', ['S/CO 信号精密度']),
        ('', [None]), ('%', ['CV 上限 %']),
    ]:
        assert fuzzy_match(query, *values), (query, values)
    for query, value in [('HBV', 'HCV'), ('LOT2027', 'LOT2026'),
                         ('葡糖', '葡萄很多别的内容糖'), ('%', '任意内容'),
                         ('[.*]', '任意内容'), ('上海 缺项', '上海仪器'),
                         ('检验项目乙', '检验项目甲')]:
        assert not fuzzy_match(query, value), (query, value)


def test_alias_keywords_disabled_scope_and_exact_adoption_identity():
    with IsolatedDatabase():
        item = create_test_item(chinese_name='搜索验收免疫球蛋白', abbreviation='QAIGG', specimen_type='血清')
        alias = create_alias(entity_type='test_item', entity_id=item, alias_text='本院蛋白简称')
        assert item in list_test_items(query='本院 血清').id.tolist()
        assert item in list_test_items(query='免球蛋白 ＱＡＩＧＧ').id.tolist()
        assert item not in list_test_items(query='%').id.tolist()
        with get_connection() as c: c.execute('UPDATE md_aliases SET is_disabled=1 WHERE id=?', (alias,))
        assert item not in list_test_items(query='本院').id.tolist()
        with get_connection() as c: c.execute('UPDATE md_test_items SET is_disabled=1 WHERE id=?', (item,))
        assert item not in list_test_items(query='QAIGG').id.tolist()
        assert item in list_test_items(include_disabled=True, query='QAIGG').id.tolist()
        assert not numeric_candidates({'test_item_name':'免球蛋白'})


def test_report_filters_use_same_rules_and_preserve_exact_facets():
    row = SimpleNamespace(project_name='搜索验收免疫球蛋白G', method_label='LJ',
        batch_label='罗氏 QC-2026-09', file_name='report.pdf', report_month='2026-09')
    assert filter_report_history_records([row],project_query='Ｉｇｇ') == []
    assert filter_report_history_records([row],project_query='免球蛋白',batch_query='2026 罗氏') == [row]
    assert filter_report_history_records([row],batch_query='QC2027') == []
    assert filter_report_history_records([row],method_label='Z-score') == []


def test_master_search_ui_keeps_selection_and_explicit_save():
    with IsolatedDatabase():
        create_test_item(chinese_name='搜索验收免疫球蛋白', abbreviation='QAIGG', specimen_type='血清')
        app = AppTest.from_string("from ui.master_data_workspace import render_master_data_workspace\nrender_master_data_workspace('test_item')", default_timeout=20).run()
        app.text_input(key='md_search_test_item').set_value('免球蛋白 ＱＡＩＧＧ').run()
        assert not app.exception
        assert len(app.dataframe[0].value) == 1
        app.text_input(key='md_search_test_item').set_value('不存在的字典结果').run()
        assert not app.exception
        assert app.button(key='md_edit_test_item').disabled


if __name__ == '__main__':
    for name, test in list(globals().items()):
        if name.startswith('test_'):
            test(); print('PASS', name, flush=True)
