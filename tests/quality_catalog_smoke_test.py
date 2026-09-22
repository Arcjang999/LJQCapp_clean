"""Every source kind is discoverable; retrieval cannot activate a future rule."""
from pathlib import Path
from datetime import date
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streamlit.testing.v1 import AppTest
from services.quality_catalog_service import browse_quality_catalog
from tests.instant_v12_integration_smoke_test import IsolatedDatabase


def test_complete_catalog_search_and_dates():
    with IsolatedDatabase():
        rows = browse_quality_catalog(on_date=date(2026,9,22))
        assert sum(r['kind']=='numeric' for r in rows)==94
        assert {r['category'] for r in rows} >= {'生化检测','免疫检测','分子检测','血液与凝血'}
        assert [r['id'] for r in browse_quality_catalog('HBV DNA')]==['molecular-hbv-dna']
        assert [r['id'] for r in browse_quality_catalog('乙肝核酸')]==['molecular-hbv-dna']
        hcv = browse_quality_catalog('HCV RNA')[0]
        assert [r['id'] for r in hcv['related_sources']]==['cdc-hcv-2023']
        assert '外部对照' in hcv['requirements']
        assert not any(r['id'].startswith('cdc-hcv') for r in browse_quality_catalog(section='general'))
        hiv = browse_quality_catalog('HIV-1 RNA')[0]
        assert hiv['name']=='HIV-1 RNA定量检测'
        assert [r['id'] for r in hiv['related_sources']]==['cdc-hiv-iqc-2024']
        assert 'Log10' in hiv['requirements']
        assert len(browse_quality_catalog('PCR 230',category='分子检测')) >= 4
        signals = browse_quality_catalog('494 信号',section='general')
        assert 'wst494-2017-signal' in [r['id'] for r in signals]
        assert all(r['standard']=='WS/T 494—2017' for r in signals)
        assert 'wst403-2024-042' in [r['id'] for r in browse_quality_catalog('免球蛋白')]
        assert browse_quality_catalog('肯定没有的标准名称') == []
        old = browse_quality_catalog('641',section='general',on_date=date(2026,10,31))
        new = browse_quality_catalog('641',section='general',on_date=date(2026,11,1))
        assert [r['id'] for r in old]==['wst641-2018-iqc']
        assert [r['id'] for r in new]==['wst641-2026-iqc']
        for day in [date(2026,9,22),date(2026,11,1)]:
            for section in ('projects','general'):
                current=browse_quality_catalog(section=section,on_date=day)
                assert all(r['status']=='current' and r['kind']!='reference_interval' for r in current)
                assert not browse_quality_catalog(status='future',section=section,on_date=day)
                assert not browse_quality_catalog(status='withdrawn',section=section,on_date=day)
                assert not browse_quality_catalog('645',section=section,on_date=day)


def test_catalog_page_search_filters_details_and_clear():
    with IsolatedDatabase():
        app = AppTest.from_string('from ui.quality_catalog import render_quality_catalog\nrender_quality_catalog()',default_timeout=20).run()
        assert not app.exception
        assert len(app.dataframe[0].value)>94
        app.text_input(key='quality_search').set_value('ＨＢＶ ＤＮＡ').run()
        assert not app.exception
        assert len(app.dataframe[0].value)==1
        app.selectbox(key='quality_catalog_detail').set_value('molecular-hbv-dna').run()
        assert not app.exception
        assert any('阴性' in x.value for x in app.markdown)
        app.text_input(key='quality_search').set_value('HCV RNA').run()
        assert app.selectbox(key='quality_catalog_detail').value is None
        app.radio(key='quality_category_filter').set_value('血液与凝血').run()
        assert not app.dataframe
        app.text_input(key='quality_search').set_value('').run()
        assert len(app.dataframe[0].value)==12
        app.radio(key='quality_catalog_section').set_value('general').run()
        assert all('2026' not in value for value in app.dataframe[0].value['现行依据'])
        texts=' '.join(x.value for x in [*app.markdown,*app.caption,*app.info])
        assert not any(word in texts for word in ['SHA-256','自动评价','开发','尚未生效','数据迁移'])
        assert not any(s.label == '标准状态' for s in app.selectbox)
        assert not app.exception


def test_inactive_custom_requirements_hidden_in_catalog_and_current_draft_summary():
    import json
    from database import get_connection
    from services.quality_target_service import get_requirement
    with IsolatedDatabase():
        for identifier, changes in [('future',dict(effective_date='2099-01-01')),
                                    ('withdrawn',dict(withdrawn_on='2020-01-01'))]:
            spec=dict(get_requirement('wst403-2024-042'),id='custom-'+identifier,
                origin='custom',standard='隐藏的失效来源',**changes)
            with get_connection() as connection:
                connection.execute('INSERT INTO qc_quality_catalog(id,origin,payload_json) VALUES (?,?,?)',
                    (spec['id'],'custom',json.dumps(spec)))
        assert not browse_quality_catalog('隐藏的失效来源')
        review=dict(confirmed_by='核查', evidence='来源核对', candidates=[dict(source='废止来源',
            standard=dict(effective_date='2019-01-01',withdrawn_on='2020-01-01'))],
            registered_standards=[dict(standard='未来补充来源',effective_date='2099-01-01')])
        app=AppTest.from_string('import streamlit as st\nfrom ui.quality_targets import render_review_summary\nrender_review_summary(st.session_state["review"])')
        app.session_state['review']=review
        app.run()
        assert not app.exception
        assert not any(word in ' '.join(c.value for c in app.caption) for word in ('废止来源','未来补充来源'))


def test_existing_item_draft_shows_standard_preview():
    from database import get_connection
    from services.project_config_service import list_template_items
    from services.master_data_service import list_units
    from tests.instant_v12_fixtures import seed_instant_configuration
    with IsolatedDatabase():
        fixture = seed_instant_configuration(name='保存前提示')
        with get_connection() as connection:
            connection.execute('UPDATE md_test_items SET chinese_name=? WHERE id=?', ('IgG', fixture['test_item_id']))
        draft = dict(list_template_items(fixture['template_id']).iloc[0])
        draft['unit_id'] = list_units().loc[lambda x: x.symbol == 'g/L'].iloc[0]['id']
        app = AppTest.from_string('import streamlit as st\nfrom ui.quality_applicability import render_draft_standard_preview\nrender_draft_standard_preview(st.session_state["draft"])', default_timeout=20)
        app.session_state['draft'] = draft
        app.run()
        assert not app.exception
        assert any(x.label == '质量标准与待核对条件' for x in app.expander)
        assert any('免疫球蛋白G' in x.value for x in app.caption)
        # An incomplete new-item draft still gives a useful prompt.
        app.session_state['draft'] = dict(draft, method_id=None, unit_id=None)
        app.run()
        assert not app.exception
        assert any('请先选择方法学' in x.value for x in app.info)


if __name__ == '__main__':
    for name, test in list(globals().items()):
        if name.startswith('test_'):
            test(); print('PASS', name, flush=True)
