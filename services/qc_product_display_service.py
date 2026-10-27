"""Display confirmed catalogue concentration text without assigning project levels."""
from string import punctuation

from services.product_directory_service import list_catalog_product_choices


def _text(value):
    return '' if value is None else str(value).strip()


def product_display_rows(rows, *, product_id_field='id', connection=None):
    if not rows:
        return []
    catalogue = {int(row['product_id']): row for row in
                 list_catalog_product_choices(connection=connection, include_disabled=True).to_dict('records')}
    return [{**row, **catalogue.get(int(row[product_id_field]), {})} for row in rows]


def product_choice_label(row):
    row = dict(row)
    code = _text(row.get('concentration_code'))
    concentration = _text(row.get('concentration'))
    level = code if code not in ('', '/') else concentration if concentration != '/' else ''
    parts = (row.get('manufacturer_name'), row.get('product_code') or row.get('catalog_no'),
             level, row.get('product_name') or row.get('generic_name') or row.get('qc_material_name'))
    return '｜'.join(_text(part) for part in parts if _text(part))


def product_concentration_summary(row):
    row = dict(row)
    return '｜'.join(label + '：' + _text(row.get(field)) for field, label in
                    (('concentration', '浓度'), ('concentration_code', '浓度编号'))
                    if _text(row.get(field)) not in ('', '/'))


def product_concentration_caption(row):
    """Keep source punctuation literal when Streamlit renders the caption."""
    return product_concentration_summary(row).translate(str.maketrans(
        {character: '\\' + character for character in punctuation}))
