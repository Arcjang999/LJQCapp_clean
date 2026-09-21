"""Explicit reagent dialogs preserve drafts, chosen assays and time-based history."""
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamlit.testing.v1 import AppTest
from database import get_connection
from services.lot_lifecycle_service import create_reagent_lot, record_lot_verification, switch_reagent_lots, workbench_systems
from services.reagent_lifecycle_edit_service import get_reagent_workspace_context, build_reagent_switch_preview
from services.workbench_config_service import sync_lj_workbench_bindings
from services.zscore_workbench_service import sync_zscore_workbench_bindings
from tests.project_management_v11_smoke_test import TemporaryDatabaseContext, _seed_v11_configuration_dependencies, _build_active_source_config


def fixture():
    data = _seed_v11_configuration_dependencies()
    _build_active_source_config(data)
    sync_lj_workbench_bindings(); sync_zscore_workbench_bindings(); workbench_systems()
    context = get_reagent_workspace_context()
    systems = [row for row in context['systems'] if row['available']]
    assert len(systems) == 2
    lot = create_reagent_lot(reagent_id=data['reagent_id'], lot_no='REAGENT-DIALOG-OLD', expiry_date='2100-12-31')
    return data, systems, lot


def dump():
    with get_connection() as c:
        return tuple(c.iterdump())


def make_app(lot=None):
    app = AppTest.from_string('''
import streamlit as st
from ui.reagent_lifecycle_workspace import render_reagent_lifecycle_workspace, render_pending_reagent_lifecycle_dialog
show = st.toggle('显示页面', value=True, key='show_reagents')
if show:
    render_reagent_lifecycle_workspace()
    render_pending_reagent_lifecycle_dialog()
''', default_timeout=20)
    if lot is not None:
        app.session_state['reagent_selected_lot'] = lot
    return app.run()


def prefix(app):
    return 'rgl_' + app.session_state['reagent_lifecycle_dialog']['token'] + '_'


def clean(app):
    assert not app.exception, [e.value for e in app.exception]


def test_register_missing_expiry_failure_cancel_and_save_selection():
    with TemporaryDatabaseContext():
        data, _, lot = fixture()
        app = make_app(lot)
        app.button(key='reagent_register').click().run(); clean(app)
        p = prefix(app)
        assert app.selectbox(key=p+'product').value == data['reagent_id']
        before = dump()
        app.text_input(key=p+'lot_no').set_value('REAGENT-DIALOG-NEXT').run()
        app.text_area(key=p+'source').set_value('资料第一行\n资料第二行').run()
        assert dump() == before
        app.button(key='reagent_save').click().run(); clean(app)
        assert any('效期' in e.value for e in app.error) and dump() == before
        app.button(key='reagent_cancel').click().run()
        app.button(key='reagent_continue').click().run(); clean(app)
        assert app.text_input(key=p+'lot_no').value == 'REAGENT-DIALOG-NEXT'
        assert app.text_area(key=p+'source').value == '资料第一行\n资料第二行'
        app.date_input(key=p+'expiry').set_value(date(2029,12,31))
        app.button(key='reagent_save').click().run(); clean(app)
        assert not app.error
        new = app.session_state['reagent_selected_lot']
        assert new != lot and 'reagent_lifecycle_dialog' not in app.session_state
        with get_connection() as c:
            row = c.execute('SELECT * FROM md_reagent_lots WHERE id=?',(new,)).fetchone()
            assert row['lot_no']=='REAGENT-DIALOG-NEXT' and row['expiry_date']=='2029-12-31'
            assert c.execute('SELECT COUNT(*) FROM qc_lot_change_events').fetchone()[0] == 0


def test_registration_discard_and_stale_catalog_leave_no_partial_data():
    with TemporaryDatabaseContext():
        data, _, lot = fixture()
        app = make_app(lot)
        app.button(key='reagent_register').click().run();p=prefix(app)
        app.text_input(key=p+'lot_no').set_value('放弃的批号')
        app.button(key='reagent_cancel').click().run()
        app.button(key='reagent_discard').click().run();clean(app)
        app.button(key='reagent_register').click().run()
        assert prefix(app) != p and app.text_input(key=prefix(app)+'lot_no').value == ''
        p=prefix(app)
        app.text_input(key=p+'lot_no').set_value('陈旧窗口批号')
        app.date_input(key=p+'expiry').set_value(date(2029,12,31))
        create_reagent_lot(reagent_id=data['reagent_id'],lot_no='另一窗口登记',expiry_date='2029-12-31')
        before=dump()
        app.button(key='reagent_save').click().run();clean(app)
        assert any('已修改' in e.value for e in app.error)
        assert dump()==before and app.text_input(key=p+'lot_no').value=='陈旧窗口批号'


def test_verification_requires_explicit_conclusion_and_keeps_selected_lot():
    with TemporaryDatabaseContext():
        _, systems, lot = fixture()
        app=make_app(lot);app.button(key='reagent_verify').click().run();p=prefix(app)
        assert app.selectbox(key=p+'conclusion').value is None
        app.selectbox(key=p+'system').set_value(systems[0]['id'])
        app.text_area(key=p+'evidence').set_value('逐检测项验证依据')
        app.text_input(key=p+'person').set_value('测试确认人')
        before=dump();app.button(key='reagent_save').click().run();clean(app)
        assert app.error and dump()==before
        app.selectbox(key=p+'conclusion').set_value('fail')
        app.button(key='reagent_cancel').click().run()
        app.button(key='reagent_continue').click().run();clean(app)
        assert app.selectbox(key=p+'system').value==systems[0]['id']
        assert app.selectbox(key=p+'conclusion').value=='fail'
        app.button(key='reagent_save').click().run();clean(app)
        assert not app.error and app.session_state['reagent_selected_lot']==lot
        with get_connection() as c:
            rows=c.execute('SELECT * FROM qc_lot_verifications').fetchall()
            assert len(rows)==1 and rows[0]['conclusion']=='fail' and rows[0]['system_id']==systems[0]['id']
            assert not c.execute('SELECT * FROM qc_reagent_lot_usage').fetchall()


def verify(system, lot, conclusion='pass', when='2026-09-01'):
    return record_lot_verification(template_item_id=system['template_item_id'],system_id=system['id'],
        reagent_lot_id=lot,conclusion=conclusion,evidence='有效验证',confirmed_by='验证人',confirmed_at=when)


def test_switch_only_chosen_assay_after_confirmation_preserves_other_system():
    with TemporaryDatabaseContext():
        _,systems,lot=fixture()
        for system in systems:verify(system,lot)
        app=make_app(lot);app.button(key='reagent_switch').click().run();p=prefix(app)
        assert app.multiselect(key=p+'systems').value==[]
        app.multiselect(key=p+'systems').set_value([systems[1]['id']]).run()
        app.text_input(key=p+'person').set_value('换批操作者')
        app.text_area(key=p+'reason').set_value('已验证原均值和标准差仍适用')
        before=dump();app.button(key='reagent_save').click().run();clean(app)
        assert app.error and dump()==before
        app.button(key='reagent_cancel').click().run()
        app.button(key='reagent_continue').click().run()
        assert app.multiselect(key=p+'systems').value==[systems[1]['id']]
        app.checkbox(key=p+'confirmed').check()
        app.button(key='reagent_save').click().run();clean(app)
        assert not app.error and 'reagent_lifecycle_dialog' not in app.session_state
        assert app.session_state['reagent_selected_lot']==lot
        with get_connection() as c:
            rows=c.execute('SELECT * FROM qc_reagent_lot_usage').fetchall()
            assert len(rows)==1 and rows[0]['system_id']==systems[1]['id']


def test_latest_failed_verification_shown_and_switch_rejected():
    with TemporaryDatabaseContext():
        _,systems,lot=fixture()
        verify(systems[0],lot);verify(systems[0],lot,'fail','2026-09-02')
        app=make_app(lot);app.button(key='reagent_switch').click().run();p=prefix(app)
        app.multiselect(key=p+'systems').set_value([systems[0]['id']]).run()
        preview=next(frame.value for frame in app.dataframe if '原试剂批号' in frame.value.columns)
        assert preview.iloc[0]['验证结论']=='未通过'
        app.text_input(key=p+'person').set_value('测试人')
        app.text_area(key=p+'reason').set_value('失败验证不可用')
        app.checkbox(key=p+'confirmed').check()
        before=dump();app.button(key='reagent_save').click().run();clean(app)
        assert app.error and dump()==before


def test_future_usage_search_and_return_do_not_mislabel_current_lot():
    with TemporaryDatabaseContext():
        data,systems,lot=fixture()
        future=create_reagent_lot(reagent_id=data['reagent_id'],lot_no='FUTURE-REAGENT',expiry_date='2100-12-31')
        verify(systems[0],lot);verify(systems[0],future)
        for identifier,when in [(lot,'2026-09-02'),(future,'2099-01-01')]:
            context=get_reagent_workspace_context()
            selection=build_reagent_switch_preview(context,reagent_lot_id=identifier,system_ids=[systems[0]['id']],effective_at=when)
            switch_reagent_lots(selections=selection,effective_at=when,operator='测试人',reason='生效时间测试')
        app=make_app(lot)
        frame=next(frame.value for frame in app.dataframe if '当前默认批号' in frame.value.columns)
        row=frame[frame['检验项目及仪器']==systems[0]['label']].iloc[0]
        assert row['当前默认批号']=='REAGENT-DIALOG-OLD' and 'FUTURE-REAGENT' in row['尚未生效的安排']
        # A correction at the same future time replaces the earlier assignment.
        context=get_reagent_workspace_context()
        selection=build_reagent_switch_preview(context,reagent_lot_id=lot,system_ids=[systems[0]['id']],effective_at='2099-01-01')
        switch_reagent_lots(selections=selection,effective_at='2099-01-01',operator='测试人',reason='更正未来安排')
        app.run();clean(app)
        frame=next(frame.value for frame in app.dataframe if '当前默认批号' in frame.value.columns)
        row=frame[frame['检验项目及仪器']==systems[0]['label']].iloc[0]
        assert row['当前默认批号']=='REAGENT-DIALOG-OLD'
        assert 'FUTURE-REAGENT' not in row['尚未生效的安排'] and 'REAGENT-DIALOG-OLD' in row['尚未生效的安排']
        app.text_input(key='reagent_search').set_value('REAGENT-DIALOG-OLD').run()
        app.selectbox(key='reagent_product_filter').set_value(data['reagent_id']).run()
        app.toggle(key='show_reagents').set_value(False).run()
        app.toggle(key='show_reagents').set_value(True).run();clean(app)
        assert app.text_input(key='reagent_search').value=='REAGENT-DIALOG-OLD'
        assert app.session_state['reagent_selected_lot']==lot
        app.text_input(key='reagent_search').set_value('无此批号').run()
        assert app.session_state['reagent_selected_lot'] is None
        assert 'reagent_switch' not in [button.key for button in app.button]


def test_empty_time_rejects_save_and_preserves_cancelled_draft():
    with TemporaryDatabaseContext():
        _,systems,lot=fixture()
        verify(systems[0],lot)
        for action in ['reagent_switch','reagent_verify']:
            app=make_app(lot);app.button(key=action).click().run();p=prefix(app)
            if action=='reagent_switch':
                app.multiselect(key=p+'systems').set_value([systems[0]['id']])
                app.text_area(key=p+'reason').set_value('空时间不应当默认为现在')
                app.checkbox(key=p+'confirmed').check()
            else:
                app.selectbox(key=p+'system').set_value(systems[0]['id'])
                app.selectbox(key=p+'conclusion').set_value('pass')
                app.text_area(key=p+'evidence').set_value('空时间不应当默认为现在')
            app.text_input(key=p+'person').set_value('测试人')
            app.get('date_time_input')[0].set_value(None).run();clean(app)
            assert app.session_state['reagent_lifecycle_dialog']['draft']['when'] is None
            before=dump();app.button(key='reagent_save').click().run();clean(app)
            assert app.error and dump()==before
            app.button(key='reagent_cancel').click().run()
            app.button(key='reagent_continue').click().run();clean(app)
            assert app.session_state['reagent_lifecycle_dialog']['draft']['when'] is None
            assert app.text_input(key=p+'person').value=='测试人'
            app.button(key='reagent_cancel').click().run()
            app.button(key='reagent_discard').click().run();clean(app)
            assert 'reagent_lifecycle_dialog' not in app.session_state


if __name__=='__main__':
    for name,function in list(globals().items()):
        if name.startswith('test_'):
            function();print('PASS',name,flush=True)
