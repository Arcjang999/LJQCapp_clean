"""Per-level product and physical-lot choices shared by setup and replacement."""
from __future__ import annotations

from copy import deepcopy

import streamlit as st

from services.master_data_service import list_qc_materials
from services.material_workflow_service import available_materials, material_label
from services.qc_product_display_service import product_choice_label, product_display_rows
from ui.required_fields import required_field


def control_products():
    return {int(row['id']): row for row in product_display_rows(
        list_qc_materials(include_disabled=False).to_dict('records'))}


def restore_registration_inputs(key):
    """Restore parent input before its widgets are instantiated again."""
    for widget, value in st.session_state.pop(key + '_registration_restore', {}).items():
        st.session_state[widget] = deepcopy(value)
    return st.session_state.pop(key + '_registration_result', None)


def render_control_slot(*, prefix, order, selected_level=None, selected_product=None,
                        registration_key, slot, preserve_prefixes=(), disabled=False,
                        products=None, materials=None):
    """Return one product and one physical record without compressing empty slots."""
    from ui.material_registration import open_material_registration

    products = control_products() if products is None else products
    rows = (available_materials().to_dict('records') if materials is None else materials)
    records = {int(row.get('qc_level_id') or row['id']): row for row in rows}
    old = records.get(selected_level)
    if selected_product is None and old:
        selected_product = int(old['qc_material_id'])
    product_key = prefix + '_product'
    level_key = prefix + '_level'
    if product_key not in st.session_state:
        st.session_state[product_key] = selected_product if selected_product in products else None
    elif st.session_state[product_key] not in [None, *products]:
        st.session_state[product_key] = None
    with required_field(product_key, missing=st.session_state[product_key] is None,
                        message=f'请选择水平 {order} 的产品。'):
        product = st.selectbox(f'水平 {order} 的质控品产品', [None, *products], key=product_key,
            format_func=lambda value: '请选择产品' if value is None else product_choice_label(products[value]),
            filter_mode='fuzzy', disabled=disabled)
    choices = {identifier: row for identifier, row in records.items()
               if int(row['qc_material_id']) == product}
    previous_product_key = prefix + '_last_product'
    if level_key not in st.session_state:
        st.session_state[level_key] = selected_level if selected_level in choices else None
    elif (st.session_state.get(previous_product_key) not in (None, product)
          or st.session_state[level_key] not in [None, *choices]):
        st.session_state[level_key] = None
    st.session_state[previous_product_key] = product
    with required_field(level_key, missing=st.session_state[level_key] is None,
                        message=f'请选择水平 {order} 的原厂批号。'):
        level = st.selectbox(f'水平 {order} 的原厂批号与浓度', [None, *choices], key=level_key,
            format_func=lambda value: '请选择批号、浓度和效期' if value is None else material_label(choices[value]),
            disabled=disabled or product is None or not choices)
    add_product, add_lot = st.columns(2)
    if add_product.button('新增质控品产品', key=prefix + '_new_product', disabled=disabled):
        open_material_registration(None, key=registration_key, slot=slot, kind='product',
                                   preserve_prefixes=preserve_prefixes)
    if add_lot.button('登记质控品批号', key=prefix + '_new_lot', disabled=disabled or product is None):
        open_material_registration(product, key=registration_key, slot=slot,
                                   preserve_prefixes=preserve_prefixes)
    return product, level
