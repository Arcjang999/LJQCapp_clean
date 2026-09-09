from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from uuid import uuid4

import pandas as pd

from database import get_connection


MASTER_ENTITY_TABLES = {
    "manufacturer": "md_manufacturers",
    "unit": "md_units",
    "method": "md_methods",
    "test_item": "md_test_items",
    "instrument_model": "md_instrument_models",
    "lab_instrument": "lab_instruments",
    "reagent": "md_reagents",
    "qc_material": "md_qc_materials",
    "qc_lot": "md_qc_material_lots",
    "qc_level": "md_qc_levels",
    "alias": "md_aliases",
}


def _new_uid() -> str:
    return str(uuid4())


def _clean_required(value: object, label: str) -> str:
    cleaned = " ".join(str(value or "").split()).strip()
    if not cleaned:
        raise ValueError(f"{label}不能为空。")
    return cleaned


def _clean_optional(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_alias(value: object) -> str:
    cleaned = _clean_required(value, "别名")
    return re.sub(r"[\s_\-（）()]+", "", cleaned).casefold()


def _date_text(value: object | None) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    cleaned = str(value).strip()
    try:
        return date.fromisoformat(cleaned).isoformat()
    except ValueError as exc:
        raise ValueError("日期必须采用 YYYY-MM-DD 格式。") from exc


def _execute_insert(sql: str, params: tuple[object, ...], duplicate_message: str) -> int:
    with get_connection() as connection:
        try:
            cursor = connection.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            raise ValueError(duplicate_message) from exc
        return int(cursor.lastrowid)


def _read_dataframe(sql: str, params: tuple[object, ...] = ()) -> pd.DataFrame:
    with get_connection() as connection:
        return pd.read_sql_query(sql, connection, params=params)


def _enabled_clause(include_disabled: bool, prefix: str = "") -> str:
    return "" if include_disabled else f" WHERE {prefix}is_disabled = 0"


def create_manufacturer(
    *,
    display_name: str,
    legal_name: str = "",
    country_or_region: str = "",
    registration_holder_name: str = "",
    notes: str = "",
) -> int:
    cleaned_display_name = _clean_required(display_name, "厂家名称")
    cleaned_legal_name = _clean_optional(legal_name) or cleaned_display_name
    return _execute_insert(
        """
        INSERT INTO md_manufacturers (
            uid, origin_type, legal_name, display_name,
            country_or_region, registration_holder_name, notes
        )
        VALUES (?, 'hospital', ?, ?, ?, ?, ?)
        """,
        (
            _new_uid(),
            cleaned_legal_name,
            cleaned_display_name,
            _clean_optional(country_or_region),
            _clean_optional(registration_holder_name),
            _clean_optional(notes),
        ),
        "已存在同名的启用厂家。",
    )


def list_manufacturers(include_disabled: bool = False) -> pd.DataFrame:
    return _read_dataframe(
        f"""
        SELECT id, uid, display_name, legal_name, country_or_region,
               origin_type, is_disabled, created_at
        FROM md_manufacturers
        {_enabled_clause(include_disabled)}
        ORDER BY is_disabled ASC, display_name COLLATE NOCASE ASC, id ASC
        """
    )


def create_unit(
    *,
    symbol: str,
    unit_name: str = "",
    ucum_code: str = "",
    quantity_kind: str = "",
    notes: str = "",
) -> int:
    cleaned_symbol = _clean_required(symbol, "单位符号")
    return _execute_insert(
        """
        INSERT INTO md_units (
            uid, origin_type, symbol, unit_name, ucum_code, quantity_kind, notes
        )
        VALUES (?, 'hospital', ?, ?, ?, ?, ?)
        """,
        (
            _new_uid(),
            cleaned_symbol,
            _clean_optional(unit_name),
            _clean_optional(ucum_code),
            _clean_optional(quantity_kind),
            _clean_optional(notes),
        ),
        "已存在同符号的启用单位。",
    )


def list_units(include_disabled: bool = False) -> pd.DataFrame:
    return _read_dataframe(
        f"""
        SELECT id, uid, symbol, unit_name, ucum_code, quantity_kind,
               origin_type, is_disabled, created_at
        FROM md_units
        {_enabled_clause(include_disabled)}
        ORDER BY is_disabled ASC, sort_order ASC, symbol COLLATE NOCASE ASC, id ASC
        """
    )


def create_method(
    *,
    method_name: str,
    method_code: str = "",
    method_category: str = "",
    principle: str = "",
    notes: str = "",
) -> int:
    return _execute_insert(
        """
        INSERT INTO md_methods (
            uid, origin_type, method_code, method_name, method_category, principle, notes
        )
        VALUES (?, 'hospital', ?, ?, ?, ?, ?)
        """,
        (
            _new_uid(),
            _clean_optional(method_code),
            _clean_required(method_name, "方法学名称"),
            _clean_optional(method_category),
            _clean_optional(principle),
            _clean_optional(notes),
        ),
        "已存在同名的启用方法学。",
    )


def list_methods(include_disabled: bool = False) -> pd.DataFrame:
    return _read_dataframe(
        f"""
        SELECT id, uid, method_name, method_code, method_category, principle,
               origin_type, is_disabled, created_at
        FROM md_methods
        {_enabled_clause(include_disabled)}
        ORDER BY is_disabled ASC, method_name COLLATE NOCASE ASC, id ASC
        """
    )


def create_test_item(
    *,
    chinese_name: str,
    standard_code: str = "",
    english_name: str = "",
    abbreviation: str = "",
    category_name: str = "",
    specimen_type: str = "",
    default_unit_id: int | None = None,
    notes: str = "",
) -> int:
    return _execute_insert(
        """
        INSERT INTO md_test_items (
            uid, origin_type, standard_code, chinese_name, english_name,
            abbreviation, category_name, specimen_type, result_type,
            default_unit_id, notes
        )
        VALUES (?, 'hospital', ?, ?, ?, ?, ?, ?, 'quantitative', ?, ?)
        """,
        (
            _new_uid(),
            _clean_optional(standard_code),
            _clean_required(chinese_name, "检验项目名称"),
            _clean_optional(english_name),
            _clean_optional(abbreviation),
            _clean_optional(category_name),
            _clean_optional(specimen_type),
            int(default_unit_id) if default_unit_id is not None else None,
            _clean_optional(notes),
        ),
        "已存在同名的启用检验项目。",
    )


def list_test_items(include_disabled: bool = False, query: str = "") -> pd.DataFrame:
    clauses: list[str] = []
    params: list[object] = []
    if not include_disabled:
        clauses.append("items.is_disabled = 0")
    cleaned_query = _clean_optional(query)
    if cleaned_query:
        clauses.append(
            """
            (
                items.chinese_name LIKE ?
                OR items.english_name LIKE ?
                OR items.abbreviation LIKE ?
                OR items.standard_code LIKE ?
                OR EXISTS (
                    SELECT 1
                    FROM md_aliases AS aliases
                    WHERE aliases.entity_type = 'test_item'
                      AND aliases.entity_id = items.id
                      AND aliases.is_disabled = 0
                      AND aliases.alias_text LIKE ?
                )
            )
            """
        )
        like_value = f"%{cleaned_query}%"
        params.extend([like_value] * 5)
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return _read_dataframe(
        f"""
        SELECT
            items.id,
            items.uid,
            items.standard_code,
            items.chinese_name,
            items.english_name,
            items.abbreviation,
            items.category_name,
            items.specimen_type,
            units.symbol AS default_unit,
            items.origin_type,
            items.is_disabled,
            items.created_at
        FROM md_test_items AS items
        LEFT JOIN md_units AS units ON units.id = items.default_unit_id
        {where_clause}
        ORDER BY items.is_disabled ASC, items.chinese_name COLLATE NOCASE ASC, items.id ASC
        """,
        tuple(params),
    )


def create_instrument_model(
    *,
    generic_name: str,
    model: str,
    manufacturer_id: int | None = None,
    brand_name: str = "",
    registration_no: str = "",
    device_category_code: str = "",
    catalog_no: str = "",
    notes: str = "",
) -> int:
    return _execute_insert(
        """
        INSERT INTO md_instrument_models (
            uid, origin_type, manufacturer_id, generic_name, brand_name,
            model, registration_no, device_category_code, catalog_no, notes
        )
        VALUES (?, 'hospital', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _new_uid(),
            int(manufacturer_id) if manufacturer_id is not None else None,
            _clean_required(generic_name, "仪器通用名称"),
            _clean_optional(brand_name),
            _clean_required(model, "仪器型号"),
            _clean_optional(registration_no),
            _clean_optional(device_category_code),
            _clean_optional(catalog_no),
            _clean_optional(notes),
        ),
        "当前厂家下已存在同型号的启用仪器。",
    )


def list_instrument_models(include_disabled: bool = False) -> pd.DataFrame:
    where_clause = "" if include_disabled else "WHERE models.is_disabled = 0"
    return _read_dataframe(
        f"""
        SELECT
            models.id,
            models.uid,
            manufacturers.display_name AS manufacturer_name,
            models.generic_name,
            models.brand_name,
            models.model,
            models.registration_no,
            models.origin_type,
            models.is_disabled,
            models.created_at
        FROM md_instrument_models AS models
        LEFT JOIN md_manufacturers AS manufacturers ON manufacturers.id = models.manufacturer_id
        {where_clause}
        ORDER BY models.is_disabled ASC, manufacturer_name COLLATE NOCASE ASC,
                 models.model COLLATE NOCASE ASC, models.id ASC
        """
    )


def create_lab_instrument(
    *,
    instrument_model_id: int,
    display_name: str,
    asset_code: str = "",
    serial_number: str = "",
    department_name: str = "",
    instrument_group: str = "",
    location: str = "",
    notes: str = "",
) -> int:
    return _execute_insert(
        """
        INSERT INTO lab_instruments (
            uid, origin_type, instrument_model_id, display_name,
            asset_code, serial_number, department_name,
            instrument_group, location, notes
        )
        VALUES (?, 'hospital', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _new_uid(),
            int(instrument_model_id),
            _clean_required(display_name, "本地仪器名称"),
            _clean_optional(asset_code),
            _clean_optional(serial_number),
            _clean_optional(department_name),
            _clean_optional(instrument_group),
            _clean_optional(location),
            _clean_optional(notes),
        ),
        "已存在同名的启用本地仪器。",
    )


def list_lab_instruments(include_disabled: bool = False) -> pd.DataFrame:
    where_clause = "" if include_disabled else "WHERE local.is_disabled = 0"
    return _read_dataframe(
        f"""
        SELECT
            local.id,
            local.uid,
            local.display_name,
            manufacturers.display_name AS manufacturer_name,
            models.generic_name,
            models.brand_name,
            models.model,
            local.asset_code,
            local.serial_number,
            local.department_name,
            local.instrument_group,
            local.location,
            local.is_disabled,
            local.created_at
        FROM lab_instruments AS local
        INNER JOIN md_instrument_models AS models ON models.id = local.instrument_model_id
        LEFT JOIN md_manufacturers AS manufacturers ON manufacturers.id = models.manufacturer_id
        {where_clause}
        ORDER BY local.is_disabled ASC, local.display_name COLLATE NOCASE ASC, local.id ASC
        """
    )


def create_reagent(
    *,
    generic_name: str,
    manufacturer_id: int | None = None,
    trade_name: str = "",
    specification: str = "",
    registration_no: str = "",
    catalog_no: str = "",
    applicable_instrument_text: str = "",
    notes: str = "",
) -> int:
    return _execute_insert(
        """
        INSERT INTO md_reagents (
            uid, origin_type, manufacturer_id, generic_name, trade_name,
            specification, registration_no, catalog_no,
            applicable_instrument_text, notes
        )
        VALUES (?, 'hospital', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _new_uid(),
            int(manufacturer_id) if manufacturer_id is not None else None,
            _clean_required(generic_name, "试剂通用名称"),
            _clean_optional(trade_name),
            _clean_optional(specification),
            _clean_optional(registration_no),
            _clean_optional(catalog_no),
            _clean_optional(applicable_instrument_text),
            _clean_optional(notes),
        ),
        "当前厂家下已存在同名的启用试剂。",
    )


def list_reagents(include_disabled: bool = False) -> pd.DataFrame:
    where_clause = "" if include_disabled else "WHERE reagents.is_disabled = 0"
    return _read_dataframe(
        f"""
        SELECT
            reagents.id,
            reagents.uid,
            manufacturers.display_name AS manufacturer_name,
            reagents.generic_name,
            reagents.trade_name,
            reagents.specification,
            reagents.registration_no,
            reagents.catalog_no,
            reagents.origin_type,
            reagents.is_disabled,
            reagents.created_at
        FROM md_reagents AS reagents
        LEFT JOIN md_manufacturers AS manufacturers ON manufacturers.id = reagents.manufacturer_id
        {where_clause}
        ORDER BY reagents.is_disabled ASC, reagents.generic_name COLLATE NOCASE ASC, reagents.id ASC
        """
    )


def create_qc_material(
    *,
    generic_name: str,
    manufacturer_id: int | None = None,
    trade_name: str = "",
    matrix: str = "",
    physical_form: str = "",
    catalog_no: str = "",
    registration_no: str = "",
    nominal_level_count: int | None = None,
    notes: str = "",
) -> int:
    normalized_level_count = None
    if nominal_level_count not in (None, ""):
        normalized_level_count = int(nominal_level_count)
        if not 1 <= normalized_level_count <= 9:
            raise ValueError("质控品水平数必须在 1 至 9 之间。")
    return _execute_insert(
        """
        INSERT INTO md_qc_materials (
            uid, origin_type, manufacturer_id, generic_name, trade_name,
            matrix, physical_form, catalog_no, registration_no,
            nominal_level_count, notes
        )
        VALUES (?, 'hospital', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _new_uid(),
            int(manufacturer_id) if manufacturer_id is not None else None,
            _clean_required(generic_name, "质控品名称"),
            _clean_optional(trade_name),
            _clean_optional(matrix),
            _clean_optional(physical_form),
            _clean_optional(catalog_no),
            _clean_optional(registration_no),
            normalized_level_count,
            _clean_optional(notes),
        ),
        "当前厂家下已存在同名的启用质控品。",
    )


def list_qc_materials(include_disabled: bool = False) -> pd.DataFrame:
    where_clause = "" if include_disabled else "WHERE materials.is_disabled = 0"
    return _read_dataframe(
        f"""
        SELECT
            materials.id,
            materials.uid,
            manufacturers.display_name AS manufacturer_name,
            materials.generic_name,
            materials.trade_name,
            materials.matrix,
            materials.physical_form,
            materials.catalog_no,
            materials.registration_no,
            materials.nominal_level_count,
            materials.origin_type,
            materials.is_disabled,
            materials.created_at
        FROM md_qc_materials AS materials
        LEFT JOIN md_manufacturers AS manufacturers ON manufacturers.id = materials.manufacturer_id
        {where_clause}
        ORDER BY materials.is_disabled ASC, materials.generic_name COLLATE NOCASE ASC, materials.id ASC
        """
    )


def create_qc_lot(
    *,
    qc_material_id: int,
    lot_no: str,
    expiry_date: object | None = None,
    manufacture_date: object | None = None,
    received_date: object | None = None,
    opened_date: object | None = None,
    notes: str = "",
) -> int:
    return _execute_insert(
        """
        INSERT INTO md_qc_material_lots (
            uid, origin_type, qc_material_id, lot_no, manufacture_date,
            expiry_date, received_date, opened_date, notes
        )
        VALUES (?, 'hospital', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _new_uid(),
            int(qc_material_id),
            _clean_required(lot_no, "质控品批号"),
            _date_text(manufacture_date),
            _date_text(expiry_date),
            _date_text(received_date),
            _date_text(opened_date),
            _clean_optional(notes),
        ),
        "当前质控品下已存在同批号的启用记录。",
    )


def list_qc_lots(
    qc_material_id: int | None = None,
    include_disabled: bool = False,
) -> pd.DataFrame:
    clauses: list[str] = []
    params: list[object] = []
    if not include_disabled:
        clauses.append("lots.is_disabled = 0")
    if qc_material_id is not None:
        clauses.append("lots.qc_material_id = ?")
        params.append(int(qc_material_id))
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return _read_dataframe(
        f"""
        SELECT
            lots.id,
            lots.uid,
            lots.qc_material_id,
            manufacturers.display_name AS manufacturer_name,
            materials.generic_name AS qc_material_name,
            materials.trade_name AS qc_material_trade_name,
            lots.lot_no,
            lots.manufacture_date,
            lots.expiry_date,
            lots.received_date,
            lots.opened_date,
            lots.is_disabled,
            lots.created_at
        FROM md_qc_material_lots AS lots
        INNER JOIN md_qc_materials AS materials ON materials.id = lots.qc_material_id
        LEFT JOIN md_manufacturers AS manufacturers ON manufacturers.id = materials.manufacturer_id
        {where_clause}
        ORDER BY lots.is_disabled ASC, lots.expiry_date DESC, lots.id DESC
        """,
        tuple(params),
    )


def create_qc_level(
    *,
    qc_material_lot_id: int,
    level_name: str,
    level_order: int,
    level_code: str = "",
    concentration_label: str = "",
    notes: str = "",
) -> int:
    normalized_order = int(level_order)
    if not 1 <= normalized_order <= 9:
        raise ValueError("水平顺序必须在 1 至 9 之间。")
    return _execute_insert(
        """
        INSERT INTO md_qc_levels (
            uid, origin_type, qc_material_lot_id, level_code,
            level_name, level_order, concentration_label, notes
        )
        VALUES (?, 'hospital', ?, ?, ?, ?, ?, ?)
        """,
        (
            _new_uid(),
            int(qc_material_lot_id),
            _clean_optional(level_code),
            _clean_required(level_name, "水平名称"),
            normalized_order,
            _clean_optional(concentration_label),
            _clean_optional(notes),
        ),
        "当前批号下已存在同顺序或同名称的启用水平。",
    )


def list_qc_levels(
    qc_material_lot_id: int | None = None,
    include_disabled: bool = False,
) -> pd.DataFrame:
    clauses: list[str] = []
    params: list[object] = []
    if not include_disabled:
        clauses.append("levels.is_disabled = 0")
    if qc_material_lot_id is not None:
        clauses.append("levels.qc_material_lot_id = ?")
        params.append(int(qc_material_lot_id))
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return _read_dataframe(
        f"""
        SELECT
            levels.id,
            levels.uid,
            levels.qc_material_lot_id,
            materials.generic_name AS qc_material_name,
            lots.lot_no,
            levels.level_code,
            levels.level_name,
            levels.level_order,
            levels.concentration_label,
            levels.is_disabled,
            levels.created_at
        FROM md_qc_levels AS levels
        INNER JOIN md_qc_material_lots AS lots ON lots.id = levels.qc_material_lot_id
        INNER JOIN md_qc_materials AS materials ON materials.id = lots.qc_material_id
        {where_clause}
        ORDER BY levels.is_disabled ASC, levels.qc_material_lot_id DESC,
                 levels.level_order ASC, levels.id ASC
        """,
        tuple(params),
    )


def create_alias(
    *,
    entity_type: str,
    entity_id: int,
    alias_text: str,
    alias_type: str = "custom",
) -> int:
    normalized_entity_type = str(entity_type or "").strip()
    if normalized_entity_type not in {
        "manufacturer",
        "test_item",
        "instrument_model",
        "reagent",
        "qc_material",
        "method",
        "unit",
    }:
        raise ValueError("不支持的别名实体类型。")
    normalized_alias_type = str(alias_type or "").strip()
    if normalized_alias_type not in {
        "short_name",
        "english",
        "brand",
        "lis_code",
        "historical",
        "vendor_text",
        "custom",
    }:
        raise ValueError("不支持的别名类型。")
    cleaned_alias = _clean_required(alias_text, "别名")
    return _execute_insert(
        """
        INSERT INTO md_aliases (
            uid, origin_type, entity_type, entity_id,
            alias_text, normalized_alias, alias_type
        )
        VALUES (?, 'hospital', ?, ?, ?, ?, ?)
        """,
        (
            _new_uid(),
            normalized_entity_type,
            int(entity_id),
            cleaned_alias,
            _normalize_alias(cleaned_alias),
            normalized_alias_type,
        ),
        "该实体已存在相同类型的同名别名。",
    )


def list_aliases(
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    include_disabled: bool = False,
) -> pd.DataFrame:
    clauses: list[str] = []
    params: list[object] = []
    if not include_disabled:
        clauses.append("is_disabled = 0")
    if entity_type:
        clauses.append("entity_type = ?")
        params.append(str(entity_type))
    if entity_id is not None:
        clauses.append("entity_id = ?")
        params.append(int(entity_id))
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return _read_dataframe(
        f"""
        SELECT id, uid, entity_type, entity_id, alias_text, alias_type,
               origin_type, is_disabled, created_at
        FROM md_aliases
        {where_clause}
        ORDER BY entity_type ASC, entity_id ASC, alias_type ASC, alias_text ASC
        """,
        tuple(params),
    )


def list_sources() -> pd.DataFrame:
    return _read_dataframe(
        """
        SELECT
            sources.id,
            sources.source_code,
            sources.source_name,
            sources.source_kind,
            sources.publisher,
            sources.version_label,
            sources.effective_date,
            sources.source_url,
            sources.imported_at,
            sources.is_disabled,
            COUNT(
                CASE WHEN records.is_disabled = 0 THEN 1 END
            ) AS active_record_count
        FROM md_sources AS sources
        LEFT JOIN md_source_records AS records ON records.source_id = sources.id
        GROUP BY sources.id
        ORDER BY sources.is_disabled ASC, sources.source_code ASC
        """
    )


def set_master_entity_disabled(
    entity_type: str,
    entity_id: int,
    *,
    is_disabled: bool,
    reason: str = "",
) -> None:
    table_name = MASTER_ENTITY_TABLES.get(str(entity_type or "").strip())
    if table_name is None:
        raise ValueError("不支持的基础资料类型。")
    cleaned_reason = _clean_optional(reason)
    try:
        with get_connection() as connection:
            row = connection.execute(
                f"SELECT id FROM {table_name} WHERE id = ?",
                (int(entity_id),),
            ).fetchone()
            if row is None:
                raise ValueError("未找到要维护的基础资料。")
            connection.execute(
                f"""
                UPDATE {table_name}
                SET is_disabled = ?,
                    disabled_at = CASE WHEN ? = 1 THEN CURRENT_TIMESTAMP ELSE NULL END,
                    disabled_reason = CASE WHEN ? = 1 THEN ? ELSE '' END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    int(bool(is_disabled)),
                    int(bool(is_disabled)),
                    int(bool(is_disabled)),
                    cleaned_reason,
                    int(entity_id),
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError("存在同名或同编码的启用记录，当前基础资料不能恢复。") from exc
