"""Enabled dictionary/template/lot configuration fixtures for the Instant workbench."""
from database import get_connection
from services.master_data_service import (
    create_manufacturer, create_instrument_model, create_lab_instrument, create_reagent,
    create_qc_material, create_qc_lot, create_qc_level, create_method, create_test_item, list_units,
)
from tests.quality_review_fixtures import confirm_fixture_lot, confirm_fixture_project
from services.project_config_service import (
    create_project_template, save_template_items, activate_project_template,
    create_lot_config_from_template, list_lot_config_items, save_lot_item_levels, activate_lot_config,
)
from services.instant_workbench_service import sync_instant_workbench_bindings


def seed_instant_configuration(name="V12 即时法", input_value_type="raw",
                               instrument="V12 仪器", reagent="V12 试剂", qc_material="V12 质控品",
                               concentration="常规浓度", lot_no="V12-INSTANT-LOT", cv_limit=5.0,
                               target_source="building"):
    manufacturer = create_manufacturer(display_name=name + " 厂家")
    model = create_instrument_model(manufacturer_id=manufacturer, generic_name="分析仪", model=name)
    instrument_id = create_lab_instrument(instrument_model_id=model, display_name=instrument)
    reagent_id = create_reagent(manufacturer_id=manufacturer, generic_name=reagent)
    material_id = create_qc_material(manufacturer_id=manufacturer, generic_name=qc_material, nominal_level_count=1)
    lot_id = create_qc_lot(qc_material_id=material_id, lot_no=lot_no, expiry_date="2028-12-31")
    level_id = create_qc_level(qc_material_lot_id=lot_id, level_name="常规水平", level_order=1, concentration_label=concentration)
    unit_id = int(list_units().loc[lambda df: df.symbol == "mg/L", "id"].iloc[0])
    method_id = create_method(method_name=name + " 检测法")
    test_id = create_test_item(chinese_name=name, default_unit_id=unit_id)
    template_id = create_project_template(template_name=name + " 模板", lab_instrument_id=instrument_id,
                                          qc_material_id=material_id, default_reagent_id=reagent_id)
    save_template_items(template_id, [{"test_item_id": test_id, "qc_method": "instant", "input_value_type": input_value_type,
        "unit_id": unit_id, "method_id": method_id, "reagent_id": reagent_id, "level_count": 1,
        "target_n": 20, "cv_limit": cv_limit}])
    confirm_fixture_project(template_id)
    activate_project_template(template_id)
    config_id = create_lot_config_from_template(template_id=template_id, qc_material_lot_id=lot_id, config_name=name + " 批号配置")
    item_id = int(list_lot_config_items(config_id).iloc[0]["id"])
    assignments = [{"qc_level_id": level_id, "target_source": target_source,
                    "target_mean": 100, "target_sd": 2, "target_confirmed": True}]
    save_lot_item_levels(item_id, assignments)
    confirm_fixture_lot(config_id)
    activate_lot_config(config_id)
    sync_instant_workbench_bindings()
    with get_connection() as connection:
        binding = connection.execute("SELECT * FROM qc_workbench_bindings WHERE lot_config_item_id = ?", (item_id,)).fetchone()
    return {"project_id": binding["runtime_project_id"] if binding else None,
            "batch_id": binding["runtime_batch_id"] if binding else None,
            "config_id": config_id, "item_id": item_id, "template_id": template_id,
            "level_id": level_id, "test_item_id": test_id, "assignments": assignments,
            "instrument_id": instrument_id, "reagent_id": reagent_id, "material_id": material_id,
            "lot_id": lot_id, "unit_id": unit_id, "method_id": method_id}


def create_configured_instant_batch(**kwargs):
    fixture = seed_instant_configuration(**kwargs)
    return fixture["project_id"], fixture["batch_id"]
