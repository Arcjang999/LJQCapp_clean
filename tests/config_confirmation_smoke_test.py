"""Batch status confirmation remains reversible and checks the captured revision."""
from __future__ import annotations

from pathlib import Path
import sys

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import get_connection
from services.project_config_service import get_lot_config, list_config_snapshots
from tests.project_management_v11_smoke_test import TemporaryDatabaseContext, _seed_v11_configuration_dependencies, _build_active_source_config


def status_page(config_id, disabled, expected_revision):
    import streamlit as st
    from ui.config_confirmation import confirm_batch_status, render_pending_batch_confirmation
    if st.button('打开确认', key='test_open_status'):
        confirm_batch_status(config_id, disabled=disabled, expected_revision=expected_revision)
    render_pending_batch_confirmation()


def management_page():
    from pages.project_management_page import _render_lot_configs_tab
    _render_lot_configs_tab()


def assert_clean(app):
    assert not list(app.exception), [str(e) for e in app.exception]


def open_status(config_id, disabled, revision):
    app = AppTest.from_function(status_page, args=(config_id, disabled, revision), default_timeout=15).run()
    app.button(key='test_open_status').click().run()
    assert_clean(app)
    return app


def test_cancel_reason_gate_disable_and_management_restore():
    with TemporaryDatabaseContext():
        _, config_id = _build_active_source_config(_seed_v11_configuration_dependencies())
        before = dict(get_lot_config(config_id))
        snapshots = list_config_snapshots(config_id).to_json(orient='records')
        app = open_status(config_id, True, before['revision_no'])
        app.button(key='batch_status_cancel').click().run()
        assert_clean(app)
        assert dict(get_lot_config(config_id)) == before
        assert list_config_snapshots(config_id).to_json(orient='records') == snapshots
        app = open_status(config_id, True, before['revision_no'])
        app.button(key='batch_status_confirm').click().run()
        assert_clean(app)
        assert not get_lot_config(config_id)['is_disabled']
        assert any('停用原因' in message.value for message in app.error)
        app.text_input(key=f'batch_status_reason_{config_id}_True').set_value('暂停使用并核对材料')
        app.button(key='batch_status_confirm').click().run()
        assert_clean(app)
        disabled = get_lot_config(config_id)
        assert disabled['is_disabled'] == 1 and disabled['status'] == 'disabled'
        assert disabled['disabled_reason'] == '暂停使用并核对材料'
        assert disabled['revision_no'] == before['revision_no'] + 1
        app = AppTest.from_function(management_page, default_timeout=15).run()
        assert_clean(app)
        app.selectbox(key='v11_restore_lot_config_selector').select_index(1).run()
        app.button(key='v11_restore_lot_config_button').click().run()
        assert_clean(app)
        assert get_lot_config(config_id)['is_disabled'] == 1
        app.button(key='batch_status_cancel').click().run()
        assert_clean(app)
        assert get_lot_config(config_id)['is_disabled'] == 1
        app.button(key='v11_restore_lot_config_button').click().run()
        app.button(key='batch_status_confirm').click().run()
        assert_clean(app)
        restored = get_lot_config(config_id)
        assert restored['is_disabled'] == 0 and restored['status'] == 'draft'
        assert restored['revision_no'] == before['revision_no'] + 2
        assert app.session_state['v11_selected_lot_config_id'] == config_id
        actions = list_config_snapshots(config_id)['action_type'].tolist()
        assert actions.count('disable') == 1 and actions.count('reactivate') == 1


def test_stale_revision_cannot_disable():
    with TemporaryDatabaseContext():
        _, config_id = _build_active_source_config(_seed_v11_configuration_dependencies())
        current = get_lot_config(config_id)
        app = open_status(config_id, True, current['revision_no'])
        with get_connection() as c:
            c.execute('UPDATE qc_lot_configs SET revision_no=revision_no+1 WHERE id=?', (config_id,))
        changed = dict(get_lot_config(config_id))
        snapshot_count = len(list_config_snapshots(config_id))
        app.text_input(key=f'batch_status_reason_{config_id}_True').set_value('陈旧表单不应成功')
        app.button(key='batch_status_confirm').click().run()
        assert_clean(app)
        assert any('批次设置已修改' in message.value for message in app.error)
        assert dict(get_lot_config(config_id)) == changed
        assert len(list_config_snapshots(config_id)) == snapshot_count


if __name__ == '__main__':
    test_cancel_reason_gate_disable_and_management_restore()
    test_stale_revision_cannot_disable()
    print('config_confirmation_smoke_test passed')
