from __future__ import annotations

from html import escape as html_escape
from io import BytesIO
import math
from numbers import Number
import re
from string import ascii_uppercase
from zipfile import ZIP_DEFLATED, ZipFile

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
    return dataframes_to_xlsx_bytes({"质控数据": dataframe})


def _sanitize_excel_sheet_name(raw_name: object, used_names: set[str]) -> str:
    cleaned_name = re.sub(r"[\\/*?:\[\]]", "_", str(raw_name or "工作表")).strip() or "工作表"
    base_name = cleaned_name[:31]
    candidate = base_name
    suffix_index = 2
    while candidate in used_names:
        suffix = f"_{suffix_index}"
        candidate = f"{base_name[: 31 - len(suffix)]}{suffix}"
        suffix_index += 1
    used_names.add(candidate)
    return candidate


def _excel_text_width(value: object) -> int:
    text = str(value or "")
    return sum(2 if ord(character) > 127 else 1 for character in text)


def dataframes_to_xlsx_bytes(dataframes: dict[str, pd.DataFrame]) -> bytes:
    normalized_dataframes = dataframes or {"质控数据": pd.DataFrame()}
    used_sheet_names: set[str] = set()
    sheets = [
        (_sanitize_excel_sheet_name(sheet_name, used_sheet_names), dataframe.copy())
        for sheet_name, dataframe in normalized_dataframes.items()
    ]
    output = BytesIO()
    shared_strings: list[str] = []
    shared_lookup: dict[str, int] = {}
    shared_reference_count = 0
    worksheet_payloads: list[tuple[str, str]] = []

    for sheet_name, dataframe in sheets:
        rows = [list(dataframe.columns)] + dataframe.fillna("").astype(object).values.tolist()
        worksheet_rows: list[str] = []
        column_widths = [max(10, _excel_text_width(column) + 2) for column in dataframe.columns]

        for row_index, row_values in enumerate(rows, start=1):
            cells: list[str] = []
            for column_index, value in enumerate(row_values, start=1):
                cell_reference = f"{_excel_column_name(column_index)}{row_index}"
                style_attribute = ' s="1"' if row_index == 1 else ""
                if isinstance(value, bool):
                    cell_value = "1" if value else "0"
                    cells.append(
                        f'<c r="{cell_reference}" t="b"{style_attribute}><v>{cell_value}</v></c>'
                    )
                    continue

                if isinstance(value, Number) and not isinstance(value, bool):
                    if math.isfinite(float(value)):
                        cells.append(f'<c r="{cell_reference}"{style_attribute}><v>{value}</v></c>')
                        continue

                text = str(value)
                if text not in shared_lookup:
                    shared_lookup[text] = len(shared_strings)
                    shared_strings.append(text)
                shared_index = shared_lookup[text]
                shared_reference_count += 1
                cells.append(
                    f'<c r="{cell_reference}" t="s"{style_attribute}><v>{shared_index}</v></c>'
                )
                if column_index <= len(column_widths):
                    column_widths[column_index - 1] = min(
                        42,
                        max(column_widths[column_index - 1], _excel_text_width(text) + 2),
                    )

            worksheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

        column_definitions = "".join(
            f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
            for index, width in enumerate(column_widths, start=1)
        )
        last_column = _excel_column_name(max(1, len(dataframe.columns)))
        last_row = max(1, len(rows))
        worksheet_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
            <sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
            <cols>{column_definitions}</cols>
            <sheetData>{''.join(worksheet_rows)}</sheetData>
            <autoFilter ref="A1:{last_column}{last_row}"/>
        </worksheet>
        """
        worksheet_payloads.append((sheet_name, worksheet_xml))

    shared_xml_items = "".join(
        f"<si><t>{html_escape(text)}</t></si>" for text in shared_strings
    )
    shared_strings_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{shared_reference_count}" uniqueCount="{len(shared_strings)}">
        {shared_xml_items}
    </sst>
    """
    workbook_sheet_entries = "".join(
        f'<sheet name="{html_escape(sheet_name, quote=True)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, (sheet_name, _) in enumerate(worksheet_payloads, start=1)
    )
    workbook_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <sheets>{workbook_sheet_entries}</sheets>
    </workbook>
    """
    worksheet_relationships = "".join(
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, len(worksheet_payloads) + 1)
    )
    styles_relationship_id = len(worksheet_payloads) + 1
    shared_strings_relationship_id = len(worksheet_payloads) + 2
    workbook_rels_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      {worksheet_relationships}
      <Relationship Id="rId{styles_relationship_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
      <Relationship Id="rId{shared_strings_relationship_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
    </Relationships>
    """
    root_rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
      <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
      <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
    </Relationships>
    """
    worksheet_content_types = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, len(worksheet_payloads) + 1)
    )
    content_types_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
      <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
      <Default Extension="xml" ContentType="application/xml"/>
      <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
      {worksheet_content_types}
      <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
      <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
      <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
      <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
    </Types>
    """
    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Microsoft YaHei"/></font></fonts>
      <fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFDDEBF7"/><bgColor indexed="64"/></patternFill></fill></fills>
      <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
      <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
      <cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>
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
        for index, (_, worksheet_xml) in enumerate(worksheet_payloads, start=1):
            workbook.writestr(f"xl/worksheets/sheet{index}.xml", worksheet_xml)

    return output.getvalue()
