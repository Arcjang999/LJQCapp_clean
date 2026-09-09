from __future__ import annotations

from collections.abc import Mapping
from html import escape as html_escape
from io import BytesIO
import math
from numbers import Real
import posixpath
import re
from string import ascii_uppercase
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

import pandas as pd


def dataframe_to_csv_bytes(dataframe: pd.DataFrame) -> bytes:
    return dataframe.to_csv(index=False).encode("utf-8-sig")


def _excel_column_name(index: int) -> str:
    result = ""
    current = index
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        result = ascii_uppercase[remainder] + result
    return result


def dataframe_to_xlsx_bytes(dataframe: pd.DataFrame) -> bytes:
    output = BytesIO()
    rows = [list(dataframe.columns)] + dataframe.fillna("").astype(object).values.tolist()
    shared_strings: list[str] = []
    shared_lookup: dict[str, int] = {}
    worksheet_rows: list[str] = []

    for row_index, row_values in enumerate(rows, start=1):
        cells: list[str] = []
        for column_index, value in enumerate(row_values, start=1):
            cell_reference = f"{_excel_column_name(column_index)}{row_index}"
            if isinstance(value, bool):
                cell_value = "1" if value else "0"
                cells.append(f'<c r="{cell_reference}" t="b"><v>{cell_value}</v></c>')
                continue

            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if math.isfinite(float(value)):
                    cells.append(f'<c r="{cell_reference}"><v>{value}</v></c>')
                    continue

            text = str(value)
            if text not in shared_lookup:
                shared_lookup[text] = len(shared_strings)
                shared_strings.append(text)
            shared_index = shared_lookup[text]
            cells.append(f'<c r="{cell_reference}" t="s"><v>{shared_index}</v></c>')

        worksheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    shared_xml_items = "".join(
        f"<si><t>{html_escape(text)}</t></si>" for text in shared_strings
    )
    worksheet_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
        <sheetData>{''.join(worksheet_rows)}</sheetData>
    </worksheet>
    """
    shared_strings_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(shared_strings)}" uniqueCount="{len(shared_strings)}">
        {shared_xml_items}
    </sst>
    """
    workbook_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <sheets>
        <sheet name="\u8d28\u63a7\u6570\u636e" sheetId="1" r:id="rId1"/>
      </sheets>
    </workbook>
    """
    workbook_rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
      <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
      <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
    </Relationships>
    """
    root_rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
      <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
      <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
    </Relationships>
    """
    content_types_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
      <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
      <Default Extension="xml" ContentType="application/xml"/>
      <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
      <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
      <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
      <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
      <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
      <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
    </Types>
    """
    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
      <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
      <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
      <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
      <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
      <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
    </styleSheet>
    """
    core_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
      xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:dcterms="http://purl.org/dc/terms/"
      xmlns:dcmitype="http://purl.org/dc/dcmitype/"
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <dc:creator>LJQCApp</dc:creator>
      <cp:lastModifiedBy>LJQCApp</cp:lastModifiedBy>
      <dcterms:created xsi:type="dcterms:W3CDTF">2026-03-24T00:00:00Z</dcterms:created>
      <dcterms:modified xsi:type="dcterms:W3CDTF">2026-03-24T00:00:00Z</dcterms:modified>
    </cp:coreProperties>
    """
    app_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
      xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
      <Application>LJQCApp</Application>
    </Properties>
    """

    with ZipFile(output, "w", ZIP_DEFLATED) as workbook:
        workbook.writestr("[Content_Types].xml", content_types_xml)
        workbook.writestr("_rels/.rels", root_rels_xml)
        workbook.writestr("docProps/core.xml", core_xml)
        workbook.writestr("docProps/app.xml", app_xml)
        workbook.writestr("xl/workbook.xml", workbook_xml)
        workbook.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        workbook.writestr("xl/styles.xml", styles_xml)
        workbook.writestr("xl/sharedStrings.xml", shared_strings_xml)
        workbook.writestr("xl/worksheets/sheet1.xml", worksheet_xml)

    return output.getvalue()


_SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_RELATIONSHIP_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_RELATIONSHIP_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CELL_REFERENCE_RE = re.compile(r"([A-Z]+)")


def _normalize_sheet_names(sheet_names: list[str]) -> list[str]:
    normalized: list[str] = []
    used: set[str] = set()
    for index, raw_name in enumerate(sheet_names, start=1):
        base = re.sub(r"[\\/*?:\[\]]", "_", str(raw_name).strip())[:31]
        base = base or f"Sheet{index}"
        candidate = base
        suffix = 2
        while candidate.casefold() in used:
            suffix_text = f"_{suffix}"
            candidate = f"{base[: 31 - len(suffix_text)]}{suffix_text}"
            suffix += 1
        used.add(candidate.casefold())
        normalized.append(candidate)
    return normalized


def _xlsx_cell_value(value: object) -> object:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat(sep=" ")
    return value


def dataframes_to_xlsx_bytes(sheets: Mapping[str, pd.DataFrame]) -> bytes:
    """Create a small styled multi-sheet XLSX without an optional Excel dependency."""
    if not sheets:
        raise ValueError("至少需要一个工作表。")

    output = BytesIO()
    raw_names = [str(name) for name in sheets]
    sheet_names = _normalize_sheet_names(raw_names)
    shared_strings: list[str] = []
    shared_lookup: dict[str, int] = {}
    worksheet_xml_by_index: dict[int, str] = {}

    def shared_index(text: str) -> int:
        if text not in shared_lookup:
            shared_lookup[text] = len(shared_strings)
            shared_strings.append(text)
        return shared_lookup[text]

    for sheet_index, ((_, dataframe), sheet_name) in enumerate(
        zip(sheets.items(), sheet_names),
        start=1,
    ):
        safe_frame = dataframe.copy()
        rows = [list(safe_frame.columns)] + safe_frame.astype(object).values.tolist()
        worksheet_rows: list[str] = []
        widths: list[float] = [max(10.0, min(42.0, len(str(column)) * 2.0 + 2.0)) for column in safe_frame.columns]
        for row_index, values in enumerate(rows, start=1):
            cells: list[str] = []
            for column_index, raw_value in enumerate(values, start=1):
                value = _xlsx_cell_value(raw_value)
                if column_index <= len(widths):
                    widths[column_index - 1] = max(
                        widths[column_index - 1],
                        min(42.0, len(str(value)) * 1.45 + 2.0),
                    )
                cell_reference = f"{_excel_column_name(column_index)}{row_index}"
                style = ' s="1"' if row_index == 1 else ""
                if isinstance(value, bool):
                    cells.append(
                        f'<c r="{cell_reference}" t="b"{style}><v>{int(value)}</v></c>'
                    )
                elif isinstance(value, Real) and math.isfinite(float(value)):
                    cells.append(
                        f'<c r="{cell_reference}"{style}><v>{value}</v></c>'
                    )
                else:
                    index = shared_index(str(value))
                    cells.append(
                        f'<c r="{cell_reference}" t="s"{style}><v>{index}</v></c>'
                    )
            worksheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

        column_xml = "".join(
            f'<col min="{index}" max="{index}" width="{width:.2f}" customWidth="1"/>'
            for index, width in enumerate(widths, start=1)
        )
        last_column = _excel_column_name(max(1, len(safe_frame.columns)))
        last_row = max(1, len(rows))
        filter_xml = (
            f'<autoFilter ref="A1:{last_column}{last_row}"/>'
            if len(safe_frame.columns) > 0
            else ""
        )
        worksheet_xml_by_index[sheet_index] = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="{_SPREADSHEET_NS}">
  <sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
  <cols>{column_xml}</cols>
  <sheetData>{''.join(worksheet_rows)}</sheetData>
  {filter_xml}
</worksheet>
"""

    shared_items = "".join(
        f'<si><t xml:space="preserve">{html_escape(text)}</t></si>'
        for text in shared_strings
    )
    shared_strings_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst xmlns="{_SPREADSHEET_NS}" count="{len(shared_strings)}" uniqueCount="{len(shared_strings)}">{shared_items}</sst>
"""
    workbook_sheets = "".join(
        f'<sheet name="{html_escape(name, quote=True)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheet_names, start=1)
    )
    workbook_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="{_SPREADSHEET_NS}" xmlns:r="{_RELATIONSHIP_NS}"><sheets>{workbook_sheets}</sheets></workbook>
"""
    worksheet_relationships = "".join(
        f'<Relationship Id="rId{index}" Type="{_RELATIONSHIP_NS}/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, len(sheet_names) + 1)
    )
    styles_relationship_id = len(sheet_names) + 1
    strings_relationship_id = len(sheet_names) + 2
    workbook_rels_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{_PACKAGE_RELATIONSHIP_NS}">
  {worksheet_relationships}
  <Relationship Id="rId{styles_relationship_id}" Type="{_RELATIONSHIP_NS}/styles" Target="styles.xml"/>
  <Relationship Id="rId{strings_relationship_id}" Type="{_RELATIONSHIP_NS}/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>
"""
    worksheet_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, len(sheet_names) + 1)
    )
    content_types_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  {worksheet_overrides}
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>
"""
    root_rels_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{_PACKAGE_RELATIONSHIP_NS}">
  <Relationship Id="rId1" Type="{_RELATIONSHIP_NS}/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="{_RELATIONSHIP_NS}/extended-properties" Target="docProps/app.xml"/>
</Relationships>
"""
    styles_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="{_SPREADSHEET_NS}">
  <fonts count="2"><font><sz val="11"/><name val="Microsoft YaHei"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Microsoft YaHei"/></font></fonts>
  <fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF184D8D"/><bgColor indexed="64"/></patternFill></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>
"""
    core_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:creator>LJQCApp</dc:creator><cp:lastModifiedBy>LJQCApp</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">2026-09-02T00:00:00Z</dcterms:created></cp:coreProperties>
"""
    titles = "".join(f"<vt:lpstr>{html_escape(name)}</vt:lpstr>" for name in sheet_names)
    app_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>LJQCApp</Application><TitlesOfParts><vt:vector size="{len(sheet_names)}" baseType="lpstr">{titles}</vt:vector></TitlesOfParts></Properties>
"""

    with ZipFile(output, "w", ZIP_DEFLATED) as workbook:
        workbook.writestr("[Content_Types].xml", content_types_xml)
        workbook.writestr("_rels/.rels", root_rels_xml)
        workbook.writestr("docProps/core.xml", core_xml)
        workbook.writestr("docProps/app.xml", app_xml)
        workbook.writestr("xl/workbook.xml", workbook_xml)
        workbook.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        workbook.writestr("xl/styles.xml", styles_xml)
        workbook.writestr("xl/sharedStrings.xml", shared_strings_xml)
        for index, worksheet_xml in worksheet_xml_by_index.items():
            workbook.writestr(f"xl/worksheets/sheet{index}.xml", worksheet_xml)
    return output.getvalue()


def _column_index_from_reference(reference: str) -> int:
    match = _CELL_REFERENCE_RE.match(reference.upper())
    if match is None:
        return 0
    result = 0
    for character in match.group(1):
        result = result * 26 + (ord(character) - ord("A") + 1)
    return result - 1


def xlsx_bytes_to_dataframes(data: bytes) -> dict[str, pd.DataFrame]:
    """Read values from ordinary XLSX worksheets used by the V1.1 import flow."""
    if not data:
        raise ValueError("上传的 XLSX 文件为空。")
    try:
        archive = ZipFile(BytesIO(data))
    except BadZipFile as exc:
        raise ValueError("文件不是有效的 XLSX 工作簿。") from exc
    with archive:
        if sum(info.file_size for info in archive.infolist()) > 50 * 1024 * 1024:
            raise ValueError("XLSX 解压后超过 50 MB，无法导入。")
        try:
            workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
            rels_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        except (KeyError, ET.ParseError) as exc:
            raise ValueError("XLSX 缺少工作簿结构或结构已损坏。") from exc

        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            try:
                shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            except ET.ParseError as exc:
                raise ValueError("XLSX 的共享字符串表已损坏。") from exc
            for item in shared_root.findall(f"{{{_SPREADSHEET_NS}}}si"):
                shared_strings.append(
                    "".join(
                        text.text or ""
                        for text in item.iter(f"{{{_SPREADSHEET_NS}}}t")
                    )
                )

        relationships = {
            element.attrib.get("Id", ""): element.attrib.get("Target", "")
            for element in rels_root.findall(f"{{{_PACKAGE_RELATIONSHIP_NS}}}Relationship")
        }
        result: dict[str, pd.DataFrame] = {}
        sheets_root = workbook_root.find(f"{{{_SPREADSHEET_NS}}}sheets")
        if sheets_root is None:
            raise ValueError("XLSX 中没有工作表。")
        for sheet in sheets_root.findall(f"{{{_SPREADSHEET_NS}}}sheet"):
            sheet_name = sheet.attrib.get("name", "Sheet")
            relationship_id = sheet.attrib.get(f"{{{_RELATIONSHIP_NS}}}id", "")
            target = relationships.get(relationship_id, "")
            if not target:
                continue
            worksheet_path = (
                target.lstrip("/")
                if target.startswith("/")
                else posixpath.normpath(posixpath.join("xl", target))
            )
            try:
                worksheet_root = ET.fromstring(archive.read(worksheet_path))
            except (KeyError, ET.ParseError) as exc:
                raise ValueError(f"工作表“{sheet_name}”结构已损坏。") from exc
            matrix: list[list[object]] = []
            for row in worksheet_root.findall(
                f".//{{{_SPREADSHEET_NS}}}sheetData/{{{_SPREADSHEET_NS}}}row"
            ):
                row_values: list[object] = []
                for cell in row.findall(f"{{{_SPREADSHEET_NS}}}c"):
                    column_index = _column_index_from_reference(cell.attrib.get("r", "A1"))
                    while len(row_values) <= column_index:
                        row_values.append("")
                    cell_type = cell.attrib.get("t", "")
                    value_node = cell.find(f"{{{_SPREADSHEET_NS}}}v")
                    raw_value = value_node.text if value_node is not None else ""
                    if cell_type == "s":
                        try:
                            value: object = shared_strings[int(raw_value)]
                        except (ValueError, IndexError):
                            value = ""
                    elif cell_type == "inlineStr":
                        value = "".join(
                            text.text or ""
                            for text in cell.iter(f"{{{_SPREADSHEET_NS}}}t")
                        )
                    elif cell_type == "b":
                        value = raw_value == "1"
                    elif raw_value == "":
                        value = ""
                    else:
                        try:
                            number = float(raw_value)
                            value = int(number) if number.is_integer() else number
                        except ValueError:
                            value = raw_value
                    row_values[column_index] = value
                matrix.append(row_values)

            if not matrix:
                result[sheet_name] = pd.DataFrame()
                continue
            width = max(len(row) for row in matrix)
            matrix = [row + [""] * (width - len(row)) for row in matrix]
            headers: list[str] = []
            used_headers: dict[str, int] = {}
            for index, raw_header in enumerate(matrix[0], start=1):
                base = str(raw_header).strip() or f"未命名列{index}"
                count = used_headers.get(base, 0) + 1
                used_headers[base] = count
                headers.append(base if count == 1 else f"{base}.{count}")
            result[sheet_name] = pd.DataFrame(matrix[1:], columns=headers)
        if not result:
            raise ValueError("XLSX 中没有可读取的工作表。")
        return result
