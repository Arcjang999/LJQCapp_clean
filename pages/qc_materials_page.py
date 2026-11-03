"""Shared quality-control product directory and focused management dialogs."""
import streamlit as st


def render_qc_material_workspace(*, render_dialog: bool = True) -> None:
    from ui.product_directory import render_product_catalogue
    from ui.material_catalog import render_pending_material_dialog

    render_product_catalogue()
    if render_dialog:
        render_pending_material_dialog()


def render_qc_materials_page() -> None:
    from ui.common import render_module_header
    if st.button('返回项目工作台', key='qc_materials_home'):
        from ui.daily_navigation import return_to_project_workspace
        return_to_project_workspace()
    render_module_header('质控品名录',
        caption='维护质控品产品、原厂批号和浓度水平，查看使用它们的项目与批次。',
        tone='materials', eyebrow='资料管理')
    render_qc_material_workspace()
