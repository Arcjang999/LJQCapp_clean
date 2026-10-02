"""Regression checks for the two defects found during R0-01 acceptance."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
from tests.project_management_v11_smoke_test import TemporaryDatabaseContext
from tests.material_workflow_smoke_test import mixed_fixture
from tests.project_workspace_smoke_test import select_table_row, assert_clean
from services.material_workflow_service import copy_material_config
from services.project_config_service import activate_lot_config
from tests.quality_review_fixtures import confirm_fixture_lot


def test_filter_and_batch_survive_real_route_cleanup():
    with TemporaryDatabaseContext():
        f = mixed_fixture()
        source = f['bindings']['lj']['lot_config_item_id']
        other = copy_material_config(source_config_id=f['config_id'],
            selections={source: [f['data']['target_levels'][0]]})
        confirm_fixture_lot(other)
        activate_lot_config(other)
        app = AppTest.from_file(str(ROOT/'app.py'), default_timeout=30).run()
        app.text_input(key='home_project_filter_search').set_value('混合 检测').run()
        app.selectbox(key='home_project_filter_group').set_value('血筛').run()
        app.selectbox(key='home_project_filter_instrument').set_value(f['data']['lab_instrument_id']).run()
        select_table_row(app,0)
        app.run()
        names=app.dataframe[0].value['检验项目'].tolist()
        select_table_row(app,names.index('V11 单水平项目'))
        key=f"workspace_batch_{f['bindings']['lj']['project_template_item_id']}"
        app.selectbox(key=key).set_value(source).run()
        app.button(key='home_open_batch').click().run()
        assert_clean(app)
        assert app.session_state['selected_batch_id']==f['bindings']['lj']['runtime_batch_id']
        app.button(key='workspace_switch_context').click().run()
        assert_clean(app)
        assert app.selectbox(key=key).value==source
        app.button(key='workspace_back').click().run()
        assert app.text_input(key='home_project_filter_search').value=='混合 检测'
        assert app.selectbox(key='home_project_filter_group').value=='血筛'
        assert app.selectbox(key='home_project_filter_instrument').value==f['data']['lab_instrument_id']


def test_three_material_lots_fit_report_cells():
    import matplotlib.pyplot as plt
    from services.report_pdf_layout import _build_zscore_summary_page, _build_pdf_rc_params
    from services.report_service import build_zscore_monthly_report_package, _resolve_pdf_font_name
    from tests.zscore_v12_fixtures import seed_zscore_configuration
    from tests.zscore_v12_integration_smoke_test import add_run
    from dataclasses import replace
    with TemporaryDatabaseContext():
        f=seed_zscore_configuration(level_count=3)
        for i in range(6): add_run(f,i,3)
        report=build_zscore_monthly_report_package(f['batch_id'],'2026-09').report
        lot='R0-MATERIAL-1 / R0-MATERIAL-2 / R0-MATERIAL-3'
        concentration='低值（编号 1） / 中值（编号 2） / 高值（编号 3）'
        report=replace(report,basic_info=replace(report.basic_info,lot_no=lot,concentration=concentration))
        with plt.rc_context(_build_pdf_rc_params(_resolve_pdf_font_name())):
            fig=_build_zscore_summary_page(report)
            try:
                fig.canvas.draw()
                renderer=fig.canvas.get_renderer()
                for ax in fig.axes:
                    for text in ax.texts:
                        assert text.get_window_extent(renderer).y0 > fig.bbox.height * .10
                table=fig.axes[0].tables[0]
                for position,source in [((3,3),lot),((5,3),concentration)]:
                    cell=table[position]
                    text=cell.get_text()
                    assert ''.join(text.get_text().split())==''.join(source.split())
                    box=text.get_window_extent(renderer)
                    boundary=cell.get_window_extent(renderer)
                    assert box.x0>=boundary.x0 and box.x1<=boundary.x1
                    assert box.y0>=boundary.y0 and box.y1<=boundary.y1
            finally: plt.close(fig)


if __name__=='__main__':
    test_filter_and_batch_survive_real_route_cleanup()
    test_three_material_lots_fit_report_cells()
    print('r0_01_regression_smoke_test passed')
