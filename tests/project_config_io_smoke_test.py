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
        from tests.quality_review_fixtures import confirm_fixture_project
        confirm_fixture_project(template_id)
        assert validate_project_template(template_id) == []

        exported_template = xlsx_bytes_to_dataframes(
            build_project_template_xlsx(template_id)
        )
        assert list(exported_template) == ["项目信息", "项目配置", "填写说明", "质量目标（供查阅）", "标准适用情况（供查阅）"]
        assert len(exported_template["项目配置"].index) == 2
        assert "项目名称" in exported_template["项目信息"]["字段"].tolist()
        for info_sheet in ("项目信息", "模板信息"):
            # The descriptive sheet name changed; existing import files remain usable.
            workbook = dict(exported_template)
            workbook[info_sheet] = workbook.pop("项目信息")
            preview, errors = preview_project_template_xlsx(dataframes_to_xlsx_bytes(workbook))
            assert not errors and len(preview) == 2

        activate_project_template(template_id)
        lot_config_id = create_lot_config_from_template(
            template_id=template_id,
            qc_material_lot_id=int(seeded["source_lot_id"]),
        )
        assert len(list_lot_config_items(lot_config_id).index) == 2
        exported_lot = xlsx_bytes_to_dataframes(build_lot_config_xlsx(lot_config_id))
        assert list(exported_lot) == ["批次信息", "项目配置", "水平均值和标准差", "修订记录", "质量目标（供查阅）", "标准适用情况（供查阅）"]
        assert len(exported_lot["项目配置"].index) == 2
        assert exported_lot["水平均值和标准差"].empty


def test_quality_review_export_is_readable_but_cannot_confirm_an_import() -> None:
    from tests.quality_review_smoke_test import draft_fixture, record
    from services.quality_target_service import decode, item_context
    from services.quality_review_service import validate_project_quality
    with TemporaryDatabaseContext():
        fixture, item_id = draft_fixture('ct', candidate=True)
        reason = '本方法输出Ct值，不能采用目录浓度尺度的CRP不精密度要求。'
        record('project', item_id, source_name='PCR 检测 SOP', source_version='QC-PCR-2026-09',
               requirement_text='按试剂说明书建立 Ct 尺度控制参数；保留人工确认记录。',
               exclusions={'wst403-2024-047': reason})
        original = decode(item_context('project', item_id)['quality_review_json'])
        payload = build_project_template_xlsx(fixture['template_id'])
        sheets = xlsx_bytes_to_dataframes(payload)
        assert list(sheets['项目配置'].columns) == PROJECT_IMPORT_COLUMNS
        source = sheets['质量目标（供查阅）'].iloc[0]
        assert source['来源名称'] == 'PCR 检测 SOP'
        assert source['来源版本或编号'] == 'QC-PCR-2026-09'
        assert source['要求内容'] == original['recorded']['requirement_text']
        assert source['确认人'] == original['confirmed_by']
        assert source['适用依据'] == original['evidence']
        assert source['来源类型'] == '实验室自定要求（不自动评价）'
        candidate = sheets['标准适用情况（供查阅）'].iloc[0]
        assert candidate['适用情况'] == '不适用' and candidate['说明'] == reason
        assert '403' in candidate['标准名称及版本'] and '2024' in candidate['标准名称及版本']
        original_preview, errors = preview_project_template_xlsx(payload)
        assert not errors

        # Descriptive sheets are untrusted input, even if edited to say approved.
        sheets['质量目标（供查阅）'].loc[0, ['来源名称', '确认人', '核对状态']] = ['伪造来源', '外部确认人', '已确认']
        sheets['标准适用情况（供查阅）'].loc[0, '适用情况'] = '已采用'
        imported = import_project_template_xlsx(fixture['template_id'], dataframes_to_xlsx_bytes(sheets))
        assert imported['imported_count'] == 1
        current = decode(item_context('project', item_id)['quality_review_json'])
        assert current['status'] == 'pending'
        assert current['recorded'] == original['recorded'] and current['candidates'] == original['candidates']
        assert validate_project_quality(item_id)
        exported_again = build_project_template_xlsx(fixture['template_id'])
        roundtrip_preview, errors = preview_project_template_xlsx(exported_again)
        assert not errors and original_preview.to_dict('records') == roundtrip_preview.to_dict('records')

        # Pre-review workbooks and old column names remain accepted.
        old = sheets['项目配置'].rename(columns={'参数建立点数*': '建靶点数*', '允许不精密度(CV%)': 'CV要求(%)'})
        old_payload = dataframes_to_xlsx_bytes({'项目配置': old})
        old_preview, errors = preview_project_template_xlsx(old_payload)
        assert not errors and old_preview.to_dict('records') == original_preview.to_dict('records')
        import_project_template_xlsx(fixture['template_id'], old_payload, mode='replace')
        assert validate_project_quality(item_id)


def test_lot_export_keeps_frozen_sources_when_project_changes() -> None:
    from tests.instant_v12_fixtures import seed_instant_configuration
    from services.quality_target_service import decode, item_context
    from services.quality_review_service import save_recorded_requirement
    with TemporaryDatabaseContext():
        fixture = seed_instant_configuration(input_value_type='ct')
        before = xlsx_bytes_to_dataframes(build_lot_config_xlsx(fixture['config_id']))
        item_id = int(list_template_items(fixture['template_id']).iloc[0]['id'])
        save_recorded_requirement('project', item_id, source_name='后续项目SOP', source_version='2',
            requirement_text='后续新批次用新版要求。', confirmed_by='项目修改人', evidence='新版本仅供后续批次核对。')
        after = xlsx_bytes_to_dataframes(build_lot_config_xlsx(fixture['config_id']))
        assert before['质量目标（供查阅）'].to_dict('records') == after['质量目标（供查阅）'].to_dict('records')
        assert before['项目配置'].to_dict('records') == after['项目配置'].to_dict('records')
        assert before['水平均值和标准差'].to_dict('records') == after['水平均值和标准差'].to_dict('records')
        row = after['质量目标（供查阅）'].iloc[0]
        frozen = decode(item_context('lot', fixture['item_id'])['quality_review_json'])
        assert row['来源版本或编号'] == frozen['recorded']['source_version'] == 'TEST-QC-001'


def test_builtin_source_export_includes_original_version_and_disposition() -> None:
    from tests.quality_review_smoke_test import draft_fixture
    from services.quality_target_service import adopt_requirement
    with TemporaryDatabaseContext():
        fixture, item_id = draft_fixture(candidate=True)
        goal = adopt_requirement('project', item_id, 'wst403-2024-047',
            confirmed_by='标准验收人', evidence='CRP 原始浓度检测，核对方法、单位及适用范围。')
        exported = xlsx_bytes_to_dataframes(build_project_template_xlsx(fixture['template_id']))
        source = exported['质量目标（供查阅）'].iloc[0]
        assert source['来源名称'] == goal['spec']['standard']
        assert source['来源版本或编号'] == goal['spec']['version']
        assert goal['spec']['imprecision_text'] in source['要求内容']
        assert source['来源条款'] == goal['spec']['source_clause']
        assert exported['标准适用情况（供查阅）'].iloc[0]['适用情况'] == '已采用'


def test_renamed_qc_labels_keep_old_imports_and_editor_rows_compatible() -> None:
    from pages.project_management_page import (_build_editor_lookup_options,
        _template_item_editor_rows, _save_editor_rows)
    with TemporaryDatabaseContext():
        seeded = _seed_v11_configuration_dependencies()
        template_id = create_project_template(template_name='质控名称兼容验证',
            lab_instrument_id=int(seeded['lab_instrument_id']), qc_material_id=int(seeded['qc_material_id']))
        for labels in [('单水平（LJ法）', '多水平（Z-score法）'),
                       ('单水平 LJ', '多水平 Z-score'), ('单水平（LJ）', '多水平法')]:
            sheets = xlsx_bytes_to_dataframes(_import_workbook())
            sheets['项目配置']['质控方法*'] = list(labels)
            payload = dataframes_to_xlsx_bytes(sheets)
            preview, errors = preview_project_template_xlsx(payload)
            assert not errors and preview['质控方法'].tolist() == ['单水平（LJ）', '多水平法']
            import_project_template_xlsx(template_id, payload, mode='replace')
            assert list_template_items(template_id)['qc_method'].tolist() == ['lj', 'zscore']
        lookups = _build_editor_lookup_options()
        editor = _template_item_editor_rows(list_template_items(template_id), lookups)
        editor['质控方法'] = ['单水平（LJ法）', '多水平（Z-score法）']
        _save_editor_rows(template_id, editor, lookups)
        assert list_template_items(template_id)['qc_method'].tolist() == ['lj', 'zscore']


if __name__ == "__main__":
    tests = [
        test_multi_sheet_xlsx_round_trip,
        test_project_import_export_and_lot_export_round_trip,
        test_quality_review_export_is_readable_but_cannot_confirm_an_import,
        test_lot_export_keeps_frozen_sources_when_project_changes,
        test_builtin_source_export_includes_original_version_and_disposition,
        test_renamed_qc_labels_keep_old_imports_and_editor_rows_compatible,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"All {len(tests)} V1.1 project config IO smoke tests passed.")
