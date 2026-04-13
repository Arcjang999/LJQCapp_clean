from __future__ import annotations

from io import BytesIO
from typing import Any

import pandas as pd

from services.value_type_service import (
    DEFAULT_INPUT_VALUE_TYPE,
    build_level_measurement_label,
    get_measurement_label,
    normalize_input_value_type,
    parse_project_input_value,
)


LJ_BUILDING_BASE_REQUIRED_COLUMNS = ["检测时间", "检测人"]
LJ_BUILDING_REAGENT_CHANGE_COLUMN = "试剂批号变更（可选）"
LJ_BUILDING_OPTIONAL_COLUMNS = ["备注", LJ_BUILDING_REAGENT_CHANGE_COLUMN]
ZSCORE_BUILDING_BASE_COLUMNS = ["检测时间", "检测人"]
ZSCORE_BUILDING_OPTIONAL_COLUMNS = ["备注"]
REVIEW_ISSUE_DISPLAY_COLUMNS = ["行号", "字段名", "问题说明", "是否阻断"]
FILE_LEVEL_ROW_LABEL = "文件级"


def build_lj_required_columns(input_value_type: str = DEFAULT_INPUT_VALUE_TYPE) -> list[str]:
    return LJ_BUILDING_BASE_REQUIRED_COLUMNS + [get_measurement_label(input_value_type)]


def build_lj_template_columns(input_value_type: str = DEFAULT_INPUT_VALUE_TYPE) -> list[str]:
    return build_lj_required_columns(input_value_type) + LJ_BUILDING_OPTIONAL_COLUMNS


def build_lj_building_template_dataframe(
    input_value_type: str = DEFAULT_INPUT_VALUE_TYPE,
) -> pd.DataFrame:
    return pd.DataFrame(columns=build_lj_template_columns(input_value_type))


def build_zscore_level_value_columns(
    level_count: int,
    input_value_type: str = DEFAULT_INPUT_VALUE_TYPE,
) -> list[str]:
    normalized_level_count = int(level_count)
    if normalized_level_count not in {2, 3}:
        raise ValueError("Z-score 建靶期模板仅支持 2 水平或 3 水平。")

    return [
        build_level_measurement_label(f"Level {index}", input_value_type)
        for index in range(1, normalized_level_count + 1)
    ]


def build_zscore_building_template_columns(
    level_count: int,
    input_value_type: str = DEFAULT_INPUT_VALUE_TYPE,
) -> list[str]:
    return (
        ZSCORE_BUILDING_BASE_COLUMNS
        + build_zscore_level_value_columns(level_count, input_value_type)
        + ZSCORE_BUILDING_OPTIONAL_COLUMNS
    )


def build_zscore_building_template_dataframe(
    level_count: int,
    input_value_type: str = DEFAULT_INPUT_VALUE_TYPE,
) -> pd.DataFrame:
    return pd.DataFrame(columns=build_zscore_building_template_columns(level_count, input_value_type))


def build_review_issues_dataframe(issues: list[dict[str, Any]]) -> pd.DataFrame:
    if not issues:
        return pd.DataFrame(columns=REVIEW_ISSUE_DISPLAY_COLUMNS)

    sorted_issues = sorted(
        issues,
        key=lambda issue: (
            int(issue.get("_sort_index", 0)),
            0 if bool(issue.get("is_blocking")) else 1,
            str(issue.get("field_name", "")),
        ),
    )
    display_rows = [
        {
            "行号": issue["row_label"],
            "字段名": issue["field_name"],
            "问题说明": issue["message"],
            "是否阻断": "是" if bool(issue["is_blocking"]) else "否",
        }
        for issue in sorted_issues
    ]
    return pd.DataFrame(display_rows, columns=REVIEW_ISSUE_DISPLAY_COLUMNS)


def review_lj_building_import_csv(
    file_bytes: bytes,
    existing_results_df: pd.DataFrame,
    target_n: int,
    input_value_type: str = DEFAULT_INPUT_VALUE_TYPE,
) -> dict[str, Any]:
    return _review_lj_import_csv(
        file_bytes=file_bytes,
        existing_results_df=existing_results_df,
        target_n=target_n,
        phase_scope="building",
        target_ready=True,
        input_value_type=input_value_type,
    )


def review_lj_formal_import_csv(
    file_bytes: bytes,
    existing_results_df: pd.DataFrame,
    target_n: int,
    target_ready: bool,
    input_value_type: str = DEFAULT_INPUT_VALUE_TYPE,
) -> dict[str, Any]:
    return _review_lj_import_csv(
        file_bytes=file_bytes,
        existing_results_df=existing_results_df,
        target_n=target_n,
        phase_scope="formal",
        target_ready=target_ready,
        input_value_type=input_value_type,
    )


def review_zscore_building_import_csv(
    file_bytes: bytes,
    existing_results_df: pd.DataFrame,
    level_count: int,
    target_n: int,
    input_value_type: str = DEFAULT_INPUT_VALUE_TYPE,
) -> dict[str, Any]:
    return _review_zscore_import_csv(
        file_bytes=file_bytes,
        existing_results_df=existing_results_df,
        level_count=level_count,
        phase_scope="building",
        target_ready=True,
        existing_phase_count=len(existing_results_df),
        target_n=target_n,
        input_value_type=input_value_type,
    )


def review_zscore_formal_import_csv(
    file_bytes: bytes,
    existing_results_df: pd.DataFrame,
    level_count: int,
    target_ready: bool,
    existing_formal_count: int,
    input_value_type: str = DEFAULT_INPUT_VALUE_TYPE,
) -> dict[str, Any]:
    return _review_zscore_import_csv(
        file_bytes=file_bytes,
        existing_results_df=existing_results_df,
        level_count=level_count,
        phase_scope="formal",
        target_ready=target_ready,
        existing_phase_count=existing_formal_count,
        target_n=None,
        input_value_type=input_value_type,
    )


def _review_zscore_import_csv(
    file_bytes: bytes,
    existing_results_df: pd.DataFrame,
    level_count: int,
    phase_scope: str,
    target_ready: bool,
    existing_phase_count: int,
    target_n: int | None,
    input_value_type: str,
) -> dict[str, Any]:
    normalized_level_count = int(level_count)
    normalized_input_value_type = normalize_input_value_type(input_value_type)
    expected_required_columns = build_zscore_building_template_columns(
        normalized_level_count,
        normalized_input_value_type,
    )[:-1]
    expected_template_columns = build_zscore_building_template_columns(
        normalized_level_count,
        normalized_input_value_type,
    )
    level_value_columns = build_zscore_level_value_columns(
        normalized_level_count,
        normalized_input_value_type,
    )
    level_3_column = build_level_measurement_label("Level 3", normalized_input_value_type)

    issues: list[dict[str, Any]] = []
    normalized_rows: list[dict[str, Any]] = []

    if phase_scope == "formal" and not target_ready:
        issues.append(
            _make_issue(
                row_label=FILE_LEVEL_ROW_LABEL,
                field_name="建靶状态",
                message="当前批次尚未完成建靶，不能导入正式期数据。",
                is_blocking=True,
            )
        )

    source_df, read_issues = _read_csv_for_review(file_bytes)
    issues.extend(read_issues)
    if source_df is None:
        return _build_review_result(
            total_rows=0,
            normalized_rows=normalized_rows,
            issues=issues,
        )

    total_rows = len(source_df)
    normalized_df = source_df.copy()
    normalized_df.columns = [
        _normalize_zscore_column_name(
            str(column).strip(),
            normalized_level_count,
            normalized_input_value_type,
        )
        for column in normalized_df.columns
    ]
    input_columns = list(normalized_df.columns)

    missing_required_columns = [
        column for column in expected_required_columns if column not in input_columns
    ]
    unexpected_columns = [
        column for column in input_columns if column not in expected_template_columns
    ]
    missing_optional_columns = [
        column for column in ZSCORE_BUILDING_OPTIONAL_COLUMNS if column not in input_columns
    ]
    allowed_column_orders = [expected_required_columns, expected_template_columns]
    has_template_blocking_issues = False

    if normalized_level_count == 2 and level_3_column in input_columns:
        has_template_blocking_issues = True
        issues.append(
            _make_issue(
                row_label=FILE_LEVEL_ROW_LABEL,
                field_name=level_3_column,
                message=f"当前批次为 2 水平，不能导入包含 {level_3_column} 列的 3 水平模板。",
                is_blocking=True,
            )
        )
    if normalized_level_count == 3 and level_3_column not in input_columns:
        has_template_blocking_issues = True
        issues.append(
            _make_issue(
                row_label=FILE_LEVEL_ROW_LABEL,
                field_name=level_3_column,
                message=f"当前批次为 3 水平，缺少必填列 {level_3_column}，请使用 3 水平模板。",
                is_blocking=True,
            )
        )

    for column in missing_required_columns:
        if normalized_level_count == 3 and column == level_3_column:
            continue
        has_template_blocking_issues = True
        issues.append(
            _make_issue(
                row_label=FILE_LEVEL_ROW_LABEL,
                field_name=column,
                message="缺少必填列，请使用当前批次对应的标准 CSV 模板。",
                is_blocking=True,
            )
        )
    for column in unexpected_columns:
        if normalized_level_count == 2 and column == level_3_column:
            continue
        has_template_blocking_issues = True
        issues.append(
            _make_issue(
                row_label=FILE_LEVEL_ROW_LABEL,
                field_name=column,
                message="发现模板外列名，当前文件无法安全识别，请按标准 CSV 模板整理后重新上传。",
                is_blocking=True,
            )
        )
    for column in missing_optional_columns:
        issues.append(
            _make_issue(
                row_label=FILE_LEVEL_ROW_LABEL,
                field_name=column,
                message="可选列缺失，将按空备注处理。",
                is_blocking=False,
            )
        )
    if not missing_required_columns and not unexpected_columns and input_columns not in allowed_column_orders:
        has_template_blocking_issues = True
        issues.append(
            _make_issue(
                row_label=FILE_LEVEL_ROW_LABEL,
                field_name="列结构",
                message="列顺序或列名与当前批次模板不匹配，无法安全识别，请重新下载标准 CSV 模板。",
                is_blocking=True,
            )
        )

    if has_template_blocking_issues:
        return _build_review_result(
            total_rows=total_rows,
            normalized_rows=normalized_rows,
            issues=issues,
        )

    for column in missing_optional_columns:
        normalized_df[column] = ""

    existing_time_strings = _build_existing_time_string_set(existing_results_df)

    for row_index, row in normalized_df.iterrows():
        row_number = row_index + 2
        row_has_blocking_error = False

        parsed_time, time_error = _parse_test_time(row.get("检测时间", ""))
        if time_error is not None:
            issues.append(
                _make_issue(
                    row_label=row_number,
                    field_name="检测时间",
                    message=time_error,
                    is_blocking=True,
                )
            )
            row_has_blocking_error = True

        operator = str(row.get("检测人", "") or "").strip()
        if not operator:
            issues.append(
                _make_issue(
                    row_label=row_number,
                    field_name="检测人",
                    message="检测人不能为空。",
                    is_blocking=True,
                )
            )
            row_has_blocking_error = True

        normalized_level_results: list[dict[str, Any]] = []
        for level_index, level_column in enumerate(level_value_columns, start=1):
            parsed_value, log_value, value_error = parse_project_input_value(
                row.get(level_column, ""),
                normalized_input_value_type,
                field_label=level_column,
            )
            if value_error is not None:
                issues.append(
                    _make_issue(
                        row_label=row_number,
                        field_name=level_column,
                        message=value_error,
                        is_blocking=True,
                    )
                )
                row_has_blocking_error = True
                continue

            normalized_level_results.append(
                {
                    "level_id": f"Level {level_index}",
                    "raw_value": float(parsed_value),
                    "log_value": log_value,
                }
            )

        if row_has_blocking_error:
            continue

        parsed_time_string = parsed_time.strftime("%Y-%m-%d %H:%M:%S")
        if parsed_time_string in existing_time_strings:
            issues.append(
                _make_issue(
                    row_label=row_number,
                    field_name="检测时间",
                    message="与当前批次已有 run 时间重复，导入时仍会按追加处理，请确认是否重复录入。",
                    is_blocking=False,
                    summary_label="检测时间重复",
                )
            )

        manual_note = str(row.get("备注", "") or "").strip()
        if not manual_note:
            issues.append(
                _make_issue(
                    row_label=row_number,
                    field_name="备注",
                    message="备注为空，如无补充说明可继续导入。",
                    is_blocking=False,
                    summary_label="备注为空",
                )
            )

        normalized_rows.append(
            {
                "test_time": parsed_time_string,
                "operator": operator,
                "manual_note": manual_note,
                "level_results": normalized_level_results,
            }
        )

    if phase_scope == "building":
        projected_total = int(existing_phase_count) + len(normalized_rows)
        if len(normalized_rows) > 0 and projected_total > int(target_n or 0):
            issues.append(
                _make_issue(
                    row_label=FILE_LEVEL_ROW_LABEL,
                    field_name="建靶进度",
                    message=(
                        f"导入后当前批次建靶 run 将达到 {projected_total} 条，超过建靶所需次数 {int(target_n or 0)} 条；"
                        "继续导入会进入正式期，本次版本不支持正式期导入，请拆分文件后重试。"
                    ),
                    is_blocking=True,
                )
            )
        elif len(normalized_rows) > 0:
            issues.append(
                _make_issue(
                    row_label=FILE_LEVEL_ROW_LABEL,
                    field_name="建靶期序列",
                    message=(
                        f"导入后将继续追加到现有建靶期序列；"
                        f"当前批次已有 {int(existing_phase_count)} 条建靶期 run。"
                    ),
                    is_blocking=False,
                )
            )
    elif len(normalized_rows) > 0:
        issues.append(
            _make_issue(
                row_label=FILE_LEVEL_ROW_LABEL,
                field_name="正式期序列",
                message=(
                    f"导入后将继续追加到现有正式期序列；"
                    f"当前批次已有 {int(existing_phase_count)} 条正式期 run。"
                ),
                is_blocking=False,
            )
        )

    return _build_review_result(
        total_rows=total_rows,
        normalized_rows=normalized_rows,
        issues=issues,
    )


def _review_lj_import_csv(
    file_bytes: bytes,
    existing_results_df: pd.DataFrame,
    target_n: int,
    phase_scope: str,
    target_ready: bool,
    input_value_type: str,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    normalized_rows: list[dict[str, Any]] = []
    normalized_input_value_type = normalize_input_value_type(input_value_type)
    value_column = get_measurement_label(normalized_input_value_type)
    required_columns = build_lj_required_columns(normalized_input_value_type)
    template_columns = build_lj_template_columns(normalized_input_value_type)

    if phase_scope == "formal" and not target_ready:
        issues.append(
            _make_issue(
                row_label=FILE_LEVEL_ROW_LABEL,
                field_name="建靶状态",
                message="当前批次尚未完成建靶，不能导入正式期数据。",
                is_blocking=True,
            )
        )

    source_df, read_issues = _read_csv_for_review(file_bytes)
    issues.extend(read_issues)
    if source_df is None:
        return _build_review_result(
            total_rows=0,
            normalized_rows=normalized_rows,
            issues=issues,
        )

    total_rows = len(source_df)
    normalized_df = source_df.copy()
    normalized_df.columns = [
        _normalize_lj_building_column_name(
            str(column).strip(),
            normalized_input_value_type,
        )
        for column in normalized_df.columns
    ]
    input_columns = list(normalized_df.columns)

    missing_required_columns = [
        column for column in required_columns if column not in input_columns
    ]
    unexpected_columns = [
        column for column in input_columns if column not in template_columns
    ]
    missing_optional_columns = [
        column for column in LJ_BUILDING_OPTIONAL_COLUMNS if column not in input_columns
    ]

    for column in missing_required_columns:
        issues.append(
            _make_issue(
                row_label=FILE_LEVEL_ROW_LABEL,
                field_name=column,
                message="缺少必填列，请使用标准 CSV 模板。",
                is_blocking=True,
            )
        )
    for column in unexpected_columns:
        issues.append(
            _make_issue(
                row_label=FILE_LEVEL_ROW_LABEL,
                field_name=column,
                message="发现模板外列名，请按标准 CSV 模板整理后重新上传。",
                is_blocking=True,
            )
        )
    for column in missing_optional_columns:
        issues.append(
            _make_issue(
                row_label=FILE_LEVEL_ROW_LABEL,
                field_name=column,
                message="可选列缺失，将按默认值处理。",
                is_blocking=False,
            )
        )

    if missing_required_columns or unexpected_columns:
        return _build_review_result(
            total_rows=total_rows,
            normalized_rows=normalized_rows,
            issues=issues,
        )

    for column in missing_optional_columns:
        normalized_df[column] = ""

    existing_time_strings = _build_existing_time_string_set(existing_results_df)

    for row_index, row in normalized_df.iterrows():
        row_number = row_index + 2
        row_has_blocking_error = False

        parsed_time, time_error = _parse_test_time(row.get("检测时间", ""))
        if time_error is not None:
            issues.append(
                _make_issue(
                    row_label=row_number,
                    field_name="检测时间",
                    message=time_error,
                    is_blocking=True,
                )
            )
            row_has_blocking_error = True

        operator = str(row.get("检测人", "") or "").strip()
        if not operator:
            issues.append(
                _make_issue(
                    row_label=row_number,
                    field_name="检测人",
                    message="检测人不能为空。",
                    is_blocking=True,
                )
            )
            row_has_blocking_error = True

        parsed_value, log_value, value_error = parse_project_input_value(
            row.get(value_column, ""),
            normalized_input_value_type,
            field_label=value_column,
        )
        if value_error is not None:
            issues.append(
                _make_issue(
                    row_label=row_number,
                    field_name=value_column,
                    message=value_error,
                    is_blocking=True,
                )
            )
            row_has_blocking_error = True

        reagent_lot_changed, reagent_error = _parse_reagent_lot_changed(
            row.get(LJ_BUILDING_REAGENT_CHANGE_COLUMN, "")
        )
        if reagent_error is not None:
            issues.append(
                _make_issue(
                    row_label=row_number,
                    field_name=LJ_BUILDING_REAGENT_CHANGE_COLUMN,
                    message=reagent_error,
                    is_blocking=True,
                )
            )
            row_has_blocking_error = True

        if row_has_blocking_error:
            continue

        parsed_time_string = parsed_time.strftime("%Y-%m-%d %H:%M:%S")
        if parsed_time_string in existing_time_strings:
            issues.append(
                _make_issue(
                    row_label=row_number,
                    field_name="检测时间",
                    message="与当前批次已有记录时间重复，导入时仍会按追加处理，请确认是否重复录入。",
                    is_blocking=False,
                    summary_label="检测时间重复",
                )
            )

        manual_note = str(row.get("备注", "") or "").strip()
        if phase_scope == "formal" and not manual_note:
            issues.append(
                _make_issue(
                    row_label=row_number,
                    field_name="备注",
                    message="备注为空，如无补充说明可继续导入。",
                    is_blocking=False,
                    summary_label="备注为空",
                )
            )

        normalized_rows.append(
            {
                "test_time": parsed_time_string,
                "operator": operator,
                "value": float(parsed_value),
                "log_value": log_value,
                "manual_note": manual_note,
                "reagent_lot_changed": int(reagent_lot_changed),
            }
        )

    projected_total = len(existing_results_df) + len(normalized_rows)
    if phase_scope == "building" and len(normalized_rows) > 0 and projected_total > int(target_n):
        issues.append(
            _make_issue(
                row_label=FILE_LEVEL_ROW_LABEL,
                field_name="建靶进度",
                message=(
                    f"导入后当前批次记录数将达到 {projected_total} 条，"
                    f"超过建靶所需次数 {int(target_n)} 条。"
                ),
                is_blocking=False,
            )
        )
    if phase_scope == "formal" and len(normalized_rows) > 0 and target_ready:
        existing_formal_count = max(len(existing_results_df) - int(target_n), 0)
        issues.append(
            _make_issue(
                row_label=FILE_LEVEL_ROW_LABEL,
                field_name="正式期序列",
                message=(
                    f"导入后将继续追加到现有正式期序列；"
                    f"当前批次已有 {existing_formal_count} 条正式期记录。"
                ),
                is_blocking=False,
            )
        )

    return _build_review_result(
        total_rows=total_rows,
        normalized_rows=normalized_rows,
        issues=issues,
    )


def _read_csv_for_review(file_bytes: bytes) -> tuple[pd.DataFrame | None, list[dict[str, Any]]]:
    if not file_bytes:
        return None, [
            _make_issue(
                row_label=FILE_LEVEL_ROW_LABEL,
                field_name="文件",
                message="文件为空，请先下载标准 CSV 模板并填写数据。",
                is_blocking=True,
            )
        ]

    last_exception: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            dataframe = pd.read_csv(
                BytesIO(file_bytes),
                encoding=encoding,
                dtype=str,
                keep_default_na=False,
            )
            if dataframe.empty:
                return None, [
                    _make_issue(
                        row_label=FILE_LEVEL_ROW_LABEL,
                        field_name="文件",
                        message="文件为空，请至少填写一行数据后再导入。",
                        is_blocking=True,
                    )
                ]
            return dataframe, []
        except pd.errors.EmptyDataError:
            return None, [
                _make_issue(
                    row_label=FILE_LEVEL_ROW_LABEL,
                    field_name="文件",
                    message="文件为空，请至少填写一行数据后再导入。",
                    is_blocking=True,
                )
            ]
        except UnicodeDecodeError as exc:
            last_exception = exc
        except pd.errors.ParserError as exc:
            last_exception = exc
            break

    error_message = "CSV 文件无法读取，请确认使用标准 CSV 模板并按 CSV 格式保存。"
    if last_exception is not None and isinstance(last_exception, pd.errors.ParserError):
        error_message = "CSV 文件结构无法解析，请确认分隔符和列数与模板一致。"
    return None, [
        _make_issue(
            row_label=FILE_LEVEL_ROW_LABEL,
            field_name="文件",
            message=error_message,
            is_blocking=True,
        )
    ]


def _build_existing_time_string_set(existing_results_df: pd.DataFrame) -> set[str]:
    if existing_results_df.empty or "test_time" not in existing_results_df.columns:
        return set()

    parsed_times = pd.to_datetime(existing_results_df["test_time"], errors="coerce")
    valid_times = parsed_times.dropna()
    return {
        timestamp.strftime("%Y-%m-%d %H:%M:%S")
        for timestamp in valid_times.tolist()
    }


def _parse_test_time(raw_value: Any) -> tuple[pd.Timestamp | None, str | None]:
    text = str(raw_value or "").strip()
    if not text:
        return None, "检测时间不能为空。"

    parsed_time = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed_time):
        return None, "检测时间无法解析，请使用标准日期时间格式。"
    return pd.Timestamp(parsed_time), None


def _normalize_lj_building_column_name(
    column_name: str,
    input_value_type: str = DEFAULT_INPUT_VALUE_TYPE,
) -> str:
    stripped_name = str(column_name or "").strip()
    if stripped_name == "试剂批号变更":
        return LJ_BUILDING_REAGENT_CHANGE_COLUMN
    if normalize_input_value_type(input_value_type) == "raw" and stripped_name == "检测值":
        return get_measurement_label(input_value_type)
    return stripped_name


def _normalize_zscore_column_name(
    column_name: str,
    level_count: int,
    input_value_type: str = DEFAULT_INPUT_VALUE_TYPE,
) -> str:
    stripped_name = str(column_name or "").strip()
    if normalize_input_value_type(input_value_type) != "raw":
        return stripped_name
    for level_index in range(1, int(level_count) + 1):
        legacy_column = f"Level {level_index} 值"
        if stripped_name == legacy_column:
            return build_level_measurement_label(f"Level {level_index}", input_value_type)
    return stripped_name


def _parse_reagent_lot_changed(raw_value: Any) -> tuple[int, str | None]:
    text = str(raw_value or "").strip()
    if not text:
        return 0, None

    normalized_text = text.lower()
    if normalized_text in {"1", "true", "yes", "y", "\u662f"}:
        return 1, None
    if normalized_text in {"0", "false", "no", "n", "\u5426"}:
        return 0, None
    return 0, "试剂批号变更仅支持填写 是/否、Y/N、1/0；空值按否处理。"


def _make_issue(
    row_label: str | int,
    field_name: str,
    message: str,
    is_blocking: bool,
    summary_label: str | None = None,
) -> dict[str, Any]:
    if row_label == FILE_LEVEL_ROW_LABEL:
        sort_index = 0
        row_key = FILE_LEVEL_ROW_LABEL
    else:
        sort_index = int(row_label)
        row_key = int(row_label)
    return {
        "row_label": row_label,
        "field_name": field_name,
        "message": message,
        "is_blocking": bool(is_blocking),
        "summary_label": str(summary_label or field_name),
        "_sort_index": sort_index,
        "_row_key": row_key,
    }


def _build_review_result(
    total_rows: int,
    normalized_rows: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    row_blocking_keys = {
        issue["_row_key"]
        for issue in issues
        if bool(issue["is_blocking"]) and issue["_row_key"] != FILE_LEVEL_ROW_LABEL
    }
    file_blocking_issues = [
        issue
        for issue in issues
        if bool(issue["is_blocking"]) and issue["_row_key"] == FILE_LEVEL_ROW_LABEL
    ]
    row_warning_keys = {
        issue["_row_key"]
        for issue in issues
        if not bool(issue["is_blocking"]) and issue["_row_key"] != FILE_LEVEL_ROW_LABEL
    }
    file_warning_issues = [
        issue
        for issue in issues
        if not bool(issue["is_blocking"]) and issue["_row_key"] == FILE_LEVEL_ROW_LABEL
    ]
    row_warning_groups: dict[str, set[int]] = {}
    for issue in issues:
        if bool(issue["is_blocking"]) or issue["_row_key"] == FILE_LEVEL_ROW_LABEL:
            continue
        label = str(issue.get("summary_label") or issue["field_name"])
        row_warning_groups.setdefault(label, set()).add(int(issue["_row_key"]))

    return {
        "summary": {
            "total_rows": int(total_rows),
            "importable_rows": int(len(normalized_rows)),
            "error_rows": int(len(row_blocking_keys)),
            "warning_rows": int(len(row_warning_keys)),
            "file_error_count": int(len(file_blocking_issues)),
            "file_warning_count": int(len(file_warning_issues)),
            "row_warning_groups": [
                {
                    "label": label,
                    "row_count": len(row_keys),
                }
                for label, row_keys in sorted(
                    row_warning_groups.items(),
                    key=lambda item: (-len(item[1]), item[0]),
                )
            ],
            "has_blocking_errors": bool(row_blocking_keys or file_blocking_issues),
        },
        "issues": issues,
        "normalized_rows": normalized_rows,
    }
