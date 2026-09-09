from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.export_utils import dataframes_to_xlsx_bytes, xlsx_bytes_to_dataframes
from services.project_config_io_service import (
    PROJECT_IMPORT_COLUMNS,
    build_lot_config_xlsx,
    build_project_import_template_xlsx,
    build_project_template_xlsx,
    import_project_template_xlsx,
    preview_project_template_xlsx,
)
from services.project_config_service import (
    activate_project_template,
    create_lot_config_from_template,
    create_project_template,
    list_lot_config_items,
    list_template_items,
    validate_project_template,
)
from tests.project_management_v11_smoke_test import (
    TemporaryDatabaseContext,
    _seed_v11_configuration_dependencies,
)


def _import_workbook() -> bytes:
    rows = [
        [
            "批量项目A",
            "BATCH-A",
            "WS-DEMO-A",
            "LJ",
            "真实检测值",
            "mmol/L",
            "比色法",
            "批量导入厂家",
            "批量导入试剂",
            "A试剂",
            1,
            20,
            4.5,
            "医院自定义目标",
            "导入测试",
        ],
        [
            "批量项目B",
            "BATCH-B",
            "WS-DEMO-B",
            "Z-score",
            "Ct值",
            "Ct",
            "荧光 PCR 法",
            "批量导入厂家",
            "批量导入试剂",
            "B试剂",
            3,
            15,
            "",
            "",
            "导入测试",
        ],
    ]
    return dataframes_to_xlsx_bytes(
        {"项目配置": pd.DataFrame(rows, columns=PROJECT_IMPORT_COLUMNS)}
    )


def test_multi_sheet_xlsx_round_trip() -> None:
    payload = build_project_import_template_xlsx()
    assert payload.startswith(b"PK")
    sheets = xlsx_bytes_to_dataframes(payload)
    assert list(sheets) == ["项目配置", "填写说明"]
    assert list(sheets["项目配置"].columns) == PROJECT_IMPORT_COLUMNS


def test_project_import_export_and_lot_export_round_trip() -> None:
    with TemporaryDatabaseContext():
        seeded = _seed_v11_configuration_dependencies()
        template_id = create_project_template(
            template_name="V11 批量导入模板",
            lab_instrument_id=int(seeded["lab_instrument_id"]),
            qc_material_id=int(seeded["qc_material_id"]),
        )
        payload = _import_workbook()
        preview, errors = preview_project_template_xlsx(payload)
        assert errors == []
        assert len(preview.index) == 2

        result = import_project_template_xlsx(template_id, payload, mode="replace")
        assert result["imported_count"] == 2
        assert result["saved_count"] == 2
        assert result["created"]["检验项目"] == 2
        assert result["created"]["厂家"] == 1
        assert result["created"]["试剂"] == 2

        items = list_template_items(template_id)
        assert len(items.index) == 2
        assert set(items["qc_method"].tolist()) == {"lj", "zscore"}
        assert set(items["input_value_type"].tolist()) == {"raw", "ct"}
        assert validate_project_template(template_id) == []

        exported_template = xlsx_bytes_to_dataframes(
            build_project_template_xlsx(template_id)
        )
        assert list(exported_template) == ["模板信息", "项目配置", "填写说明"]
        assert len(exported_template["项目配置"].index) == 2

        activate_project_template(template_id)
        lot_config_id = create_lot_config_from_template(
            template_id=template_id,
            qc_material_lot_id=int(seeded["source_lot_id"]),
        )
        assert len(list_lot_config_items(lot_config_id).index) == 2
        exported_lot = xlsx_bytes_to_dataframes(build_lot_config_xlsx(lot_config_id))
        assert list(exported_lot) == ["批号信息", "项目配置", "水平靶值", "修订记录"]
        assert len(exported_lot["项目配置"].index) == 2
        assert exported_lot["水平靶值"].empty


if __name__ == "__main__":
    tests = [
        test_multi_sheet_xlsx_round_trip,
        test_project_import_export_and_lot_export_round_trip,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"All {len(tests)} V1.1 project config IO smoke tests passed.")
