"""Read-only preparation, followed by atomic registration through existing services.

This does not create a laboratory organization, project, quality confirmation,
lot verification or an active QC configuration. Existing records are referenced,
never overwritten by a preparation file.
"""
from __future__ import annotations

from collections import OrderedDict
from hashlib import sha256
import json
import sqlite3

import pandas as pd

from database import atomic_write, read_snapshot
from services import master_data_service as master
from services.export_utils import dataframes_to_xlsx_bytes, xlsx_bytes_to_dataframes
from services.settings_service import get_report_settings, save_report_settings_form


UNIT_FIELDS = OrderedDict((
    ('实验室名称', 'lab_name'), ('科室名称', 'department_name'),
    ('检验员', 'qc_owner_name'), ('复核员', 'reviewer_name'),
))
FORMAT_VERSION = 1
PREPARATION_SHEETS = OrderedDict((
    ('计量单位', ['清单编号', '已登记标识', '单位符号', '单位名称', 'UCUM编码']),
    ('方法学', ['清单编号', '已登记标识', '方法学名称', '方法学编码']),
    ('检验项目', ['清单编号', '已登记标识', '检验项目名称', '标准编码', '项目缩写', '默认单位编号或标识']),
    ('仪器', ['清单编号', '已登记标识', '仪器名称', '厂家', '型号', '仪器通用名', '所属科室', '资产编号', '序列号', '位置']),
    ('试剂产品', ['清单编号', '已登记标识', '厂家', '试剂名称', '商品名', '货号']),
    ('试剂批号', ['清单编号', '试剂产品编号或标识', '批号', '效期', '参考资料']),
    ('质控品产品', ['清单编号', '已登记标识', '厂家', '质控品名称', '商品名', '货号', '标称水平数', '浓度编号', '实际浓度']),
    ('质控材料', ['清单编号', '质控品产品编号或标识', '批号', '效期', '浓度水平', '浓度编号', '浓度说明', '水平货号']),
))
TABLES = OrderedDict((
    ('manufacturer', 'md_manufacturers'), ('instrument_model', 'md_instrument_models'),
    ('lab_instrument', 'lab_instruments'), ('unit', 'md_units'), ('method', 'md_methods'),
    ('test_item', 'md_test_items'), ('reagent', 'md_reagents'), ('reagent_lot', 'md_reagent_lots'),
    ('qc_material', 'md_qc_materials'), ('qc_lot', 'md_qc_material_lots'),
    ('qc_level', 'md_qc_levels'), ('material_spec', 'md_qc_material_specs'),
))
SHEET_ENTITIES = dict(zip(PREPARATION_SHEETS,
    ('unit', 'method', 'test_item', 'lab_instrument', 'reagent', 'reagent_lot', 'qc_material', 'control_material')))
LABELS = {'manufacturer': '厂家', 'instrument_model': '仪器型号', 'lab_instrument': '仪器',
          'unit': '计量单位', 'method': '方法学', 'test_item': '检验项目', 'reagent': '试剂产品',
          'reagent_lot': '试剂批号', 'qc_material': '质控品产品', 'control_material': '质控材料'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def _hash(value):
    return sha256(_json(value).encode()).hexdigest()


def _text(value):
    if value is None or pd.isna(value):
        return ''
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _norm(value):
    return _text(value).casefold()


def _snapshot(connection):
    records = {entity: [dict(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY id')]
               for entity, table in TABLES.items()}
    # Published product text remains the source for imported products. Reading
    # these fields never rewrites a directory product or its material levels.
    from services.product_directory_service import list_catalog_product_choices
    products = {int(row['product_id']): row for row in list_catalog_product_choices(
        connection=connection, include_disabled=True).to_dict('records')}
    for product in records['qc_material']:
        source = products.get(product['id'])
        if source:
            product.update(concentration_code=source['concentration_code'],
                           concentration=source['concentration'])
    categories = [dict(row) for row in connection.execute(
        'SELECT manufacturer_id,category FROM md_manufacturer_categories ORDER BY manufacturer_id,category')]
    settings = [dict(row) for row in connection.execute('SELECT key,value FROM app_settings ORDER BY key')]
    source = {entity: [{key: value for key, value in row.items() if key not in ('created_at', 'updated_at')}
                      for row in rows] for entity, rows in records.items()}
    return records, categories, _hash(dict(records=source, categories=categories, settings=settings))


def build_lab_preparation_template():
    sheets = OrderedDict()
    sheets['填写说明'] = pd.DataFrame([
        ['清单版本', str(FORMAT_VERSION)],
        ['用途', '填写并保存单位信息、仪器、试剂和质控品资料。保存后，在软件中继续新建项目、设置质量目标和均值与标准差，再登记试剂批号验证。'],
        ['单位信息', '填写已核对的实验室、科室及报告默认人员；空白保留原设置。未准备项会在审查中提示。'],
        ['清单编号', '每个填写的资料行需要本表内唯一的清单编号；在批号、材料或检验项目表中用它引用产品或单位。'],
        ['引用已有资料', '下载已登记资料清单，把对应资料的标识复制到“已登记标识”；留空时按各列填写的内容查找相同资料。'],
        ['新资料', '新增单位填单位符号；新增方法填名称；新增检验项目填名称。新增仪器填名称、厂家、型号、通用名；新增产品填厂家和名称，质控品的浓度编号和实际浓度照产品资料填写。'],
        ['实际批号', '试剂批号填写产品编号或已有标识、批号和效期。质控材料还填写浓度水平，可补浓度编号和说明；每行是一份实际批号水平。'],
        ['已有资料', '本文件只引用已有资料，不覆盖名称、资产编号、效期或材料关系。有冲突时请先到相应资料管理入口核对。'],
        ['填写格式', '标识、批号、编号使用文本格式，保留前导零；效期填YYYY-MM-DD。不要填写未核实的人名、批号或参数。'],
        ['保存', '上传后先审查并核对新增/引用及单位设置变化。确认后全部一起保存；取消不会登记资料。'],
    ], columns=['事项', '说明'])
    sheets['单位信息'] = pd.DataFrame([[label, ''] for label in UNIT_FIELDS], columns=['资料项目', '填写内容'])
    for name, columns in PREPARATION_SHEETS.items():
        sheets[name] = pd.DataFrame(columns=columns)
    return dataframes_to_xlsx_bytes(sheets, text_columns={
        '质控品产品': ['浓度编号'], '质控材料': ['浓度编号'],
    })


def build_lab_preparation_reference_xlsx():
    with read_snapshot() as connection:
        records, _, _ = _snapshot(connection)
    rows = []
    for entity in ('unit', 'method', 'test_item', 'lab_instrument', 'reagent', 'qc_material'):
        for row in records[entity]:
            if row['is_disabled']:
                continue
            manufacturer = next((item['display_name'] for item in records['manufacturer']
                                 if item['id'] == row.get('manufacturer_id')), '')
            if entity == 'lab_instrument':
                model = next(item for item in records['instrument_model'] if item['id'] == row['instrument_model_id'])
                manufacturer = next((item['display_name'] for item in records['manufacturer']
                                     if item['id'] == model['manufacturer_id']), '')
            rows.append({'资料类型': LABELS[entity], '已登记标识': row['uid'],
                '名称': next((row[key] for key in ('symbol', 'method_name', 'chinese_name', 'display_name', 'generic_name') if key in row), ''),
                '厂家': manufacturer, '商品名': row.get('trade_name', ''), '货号': row.get('catalog_no', ''),
                '浓度编号': row.get('concentration_code', ''), '实际浓度': row.get('concentration', '')})
    return dataframes_to_xlsx_bytes({'已登记资料': pd.DataFrame(rows,
        columns=['资料类型', '已登记标识', '名称', '厂家', '商品名', '货号', '浓度编号', '实际浓度'])},
        text_columns={'已登记资料': ['浓度编号']})


class _Planner:
    def __init__(self, connection):
        self.records, categories, self.fingerprint = _snapshot(connection)
        self.categories = {row['id']: {item['category'] for item in categories if item['manufacturer_id'] == row['id']}
                           for row in self.records['manufacturer']}
        self.plan, self.preview, self.issues = [], [], []
        self.inputs = {name: {} for name in PREPARATION_SHEETS}
        self.current_sheet, self.current_row = '', 0

    def issue(self, message, *, blocking=True, field=''):
        self.issues.append(dict(sheet=self.current_sheet, row=self.current_row, field=field,
                                message=message, blocking=blocking))

    def _check(self, entity, record):
        if record.get('is_disabled'):
            raise ValueError(f"{LABELS.get(entity, '资料')}已停用，请先在资料管理中核对，不能重复登记。")
        if entity == 'lab_instrument':
            parent = self._record('instrument_model', record['instrument_model_id'])
            self._check('instrument_model', parent)
        if entity == 'test_item' and record.get('default_unit_id'):
            self._check('unit', self._record('unit', record['default_unit_id']))
        if entity in ('instrument_model', 'reagent', 'qc_material') and record.get('manufacturer_id'):
            parent = self._record('manufacturer', record['manufacturer_id'])
            self._check('manufacturer', parent)
            category = {'instrument_model': 'instrument', 'reagent': 'reagent', 'qc_material': 'qc_material'}[entity]
            if category not in self.categories[parent['id']]:
                raise ValueError('已有厂家未登记此业务类别，请先到厂家管理核对后重新审查。')
        elif entity in ('instrument_model', 'reagent', 'qc_material'):
            self.issue(LABELS[entity] + '尚未登记厂家，可在对应资料管理中补充核对。', blocking=False)

    def _record(self, entity, identifier):
        return next(row for row in self.records[entity] if row['id'] == identifier)

    def description(self, entity, identifier):
        if isinstance(identifier, str):
            record = next(node['payload'] for node in self.plan if node['ref'] == identifier)
        elif entity == 'control_material':
            record = self._record('qc_level', identifier)
            lot = self._record('qc_lot', record['qc_material_lot_id'])
            product = self._record('qc_material', lot['qc_material_id'])
            return [product['generic_name'], lot['lot_no'], lot['expiry_date'], record['level_name'], record['level_code']]
        else:
            record = self._record(entity, identifier)
        values = [record.get(key) for key in ('symbol', 'method_name', 'chinese_name', 'display_name',
                                             'generic_name', 'trade_name', 'catalog_no', 'concentration_code',
                                             'concentration', 'lot_no', 'level_name', 'level_code')]
        if entity == 'lab_instrument' and isinstance(record['instrument_model_id'], int):
            model = self._record('instrument_model', record['instrument_model_id'])
            values.append(model['model'])
            manufacturer = model['manufacturer_id']
        else:
            manufacturer = record.get('manufacturer_id')
        if isinstance(manufacturer, int):
            values.append(self._record('manufacturer', manufacturer)['display_name'])
        return [_text(value) for value in values if value not in ('', None)]

    def uid(self, entity, uid):
        matches = [row for row in self.records[entity] if row.get('uid') == uid]
        if len(matches) != 1:
            raise ValueError(f"未找到唯一的{LABELS[entity]}标识，请从已登记资料清单核对。")
        self._check(entity, matches[0])
        return matches[0]

    def match(self, entity, payload, keys, required):
        def equal(row):
            return all(_norm(row.get(key)) == _norm(payload.get(key)) for key in keys)
        matches = [row for row in self.records[entity] if equal(row)]
        active = [row for row in matches if not row.get('is_disabled')]
        if len(active) > 1:
            raise ValueError('已有资料不唯一，请填写已登记标识明确引用。')
        if active:
            record = active[0]
            self._check(entity, record)
            self.check_values(record, payload)
            return record['id'], False
        if matches:
            raise ValueError('相同资料已登记但已停用。请到对应管理页面查看，确认后恢复使用。')
        for node in self.plan:
            if node['entity'] == entity and equal(node['payload']):
                self.check_values(node['payload'], payload)
                return node['ref'], True
        for key, label in required.items():
            if payload.get(key) in (None, ''):
                raise ValueError('新增资料请填写' + label + '。')
        ref = f"new:{entity}:{len(self.plan)}"
        self.plan.append(dict(entity=entity, ref=ref, payload=payload))
        return ref, True

    @staticmethod
    def check_values(record, payload):
        for field, value in payload.items():
            if value not in ('', None) and _norm(record.get(field)) != _norm(value):
                raise ValueError('填写内容与已有资料不同；本文件不覆盖已有资料，请先到相应管理入口核对。')

    def manufacturer(self, name, category):
        if not name:
            raise ValueError('新增产品或仪器请填写厂家。')
        matches = [row for row in self.records['manufacturer'] if _norm(row['display_name']) == _norm(name)]
        active = [row for row in matches if not row['is_disabled']]
        if len(active) == 1:
            if category not in self.categories[active[0]['id']]:
                raise ValueError('已有厂家未登记此业务类别，请先到厂家管理核对后重新审查。')
            return active[0]['id']
        if matches:
            raise ValueError('厂家已停用或不唯一，请先核对厂家资料。')
        for node in self.plan:
            if node['entity'] == 'manufacturer' and _norm(node['payload']['display_name']) == _norm(name):
                node['payload']['categories'] = sorted(set(node['payload']['categories']) | {category})
                return node['ref']
        ref = f'new:manufacturer:{len(self.plan)}'
        self.plan.append(dict(entity='manufacturer', ref=ref,
                              payload=dict(display_name=name, categories=[category])))
        return ref

    def reference(self, sheet, entity, value):
        if value in self.inputs[sheet]:
            return self.inputs[sheet][value]
        return self.uid(entity, value)['id']

    def date(self, value):
        if not value:
            raise ValueError('请填写实际批号的效期。')
        try:
            time = pd.Timestamp(value)
            if pd.isna(time):
                raise ValueError()
            return time.strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            raise ValueError('效期无效，请填写YYYY-MM-DD日期。') from None

    def _payload(self, row, mappings):
        return {field: row.get(label, '') for label, field in mappings.items()}

    def _simple(self, entity, row, mappings, keys, required, foreign=None):
        payload = self._payload(row, mappings)
        if foreign:
            label, field, sheet, parent = foreign
            if row.get(label):
                payload[field] = self.reference(sheet, parent, row[label])
        if row.get('已登记标识'):
            record = self.uid(entity, row['已登记标识'])
            self.check_values(record, payload)
            return record['id'], False
        return self.match(entity, payload, keys, required)

    def row(self, sheet, row):
        if sheet == '计量单位':
            return self._simple('unit', row, {'单位符号': 'symbol', '单位名称': 'unit_name', 'UCUM编码': 'ucum_code'},
                ('symbol',), {'symbol': '单位符号'})
        if sheet == '方法学':
            return self._simple('method', row, {'方法学名称': 'method_name', '方法学编码': 'method_code'},
                ('method_name',), {'method_name': '方法学名称'})
        if sheet == '检验项目':
            return self._simple('test_item', row,
                {'检验项目名称': 'chinese_name', '标准编码': 'standard_code', '项目缩写': 'abbreviation'},
                ('chinese_name',), {'chinese_name': '检验项目名称'},
                foreign=('默认单位编号或标识', 'default_unit_id', '计量单位', 'unit'))
        if sheet == '仪器':
            payload = self._payload(row, {'仪器名称': 'display_name', '所属科室': 'department_name',
                                         '资产编号': 'asset_code', '序列号': 'serial_number', '位置': 'location'})
            if row.get('已登记标识'):
                record = self.uid('lab_instrument', row['已登记标识'])
                model = self._record('instrument_model', record['instrument_model_id'])
                self.check_values(record, payload)
                self.check_values(model, self._payload(row, {'型号': 'model', '仪器通用名': 'generic_name'}))
                if row['厂家']:
                    manufacturer = self._record('manufacturer', model['manufacturer_id'])
                    self.check_values(manufacturer, {'display_name': row['厂家']})
                return record['id'], False
            manufacturer = self.manufacturer(row['厂家'], 'instrument')
            model, _ = self.match('instrument_model', dict(manufacturer_id=manufacturer, model=row['型号'],
                generic_name=row['仪器通用名']), ('manufacturer_id', 'model'), {'model': '仪器型号', 'generic_name': '仪器通用名'})
            return self.match('lab_instrument', dict(payload, instrument_model_id=model), ('display_name',),
                              {'display_name': '仪器名称'})
        if sheet in ('试剂产品', '质控品产品'):
            entity = 'reagent' if sheet == '试剂产品' else 'qc_material'
            name = '试剂名称' if entity == 'reagent' else '质控品名称'
            payload = self._payload(row, {name: 'generic_name', '商品名': 'trade_name', '货号': 'catalog_no'})
            if entity == 'qc_material':
                payload.update(self._payload(row, {'浓度编号': 'concentration_code', '实际浓度': 'concentration'}))
            if entity == 'qc_material' and row['标称水平数']:
                try:
                    number = float(row['标称水平数'])
                    if not number.is_integer() or not 1 <= number <= 9:
                        raise ValueError()
                    payload['nominal_level_count'] = int(number)
                except ValueError:
                    raise ValueError('标称水平数应为1至9的整数，尚未核实时留空。') from None
            if row.get('已登记标识'):
                record = self.uid(entity, row['已登记标识'])
                self.check_values(record, payload)
                if row['厂家']:
                    parent = self._record('manufacturer', record['manufacturer_id'])
                    self.check_values(parent, {'display_name': row['厂家']})
                return record['id'], False
            payload['manufacturer_id'] = self.manufacturer(row['厂家'], entity)
            # A blank product number cannot distinguish equal-name catalogue
            # products. Reuse a unique exact match or require an explicit UID.
            keys = ('manufacturer_id', 'generic_name', 'trade_name') + (
                ('catalog_no',) if entity == 'qc_material' and payload['catalog_no'] else ())
            return self.match(entity, payload, keys, {'generic_name': name})
        if sheet == '试剂批号':
            product = self.reference('试剂产品', 'reagent', row['试剂产品编号或标识'])
            payload = dict(reagent_id=product, lot_no=row['批号'], expiry_date=self.date(row['效期']), source_text=row['参考资料'])
            return self.match('reagent_lot', payload, ('reagent_id', 'lot_no'), {'lot_no': '试剂批号'})
        product = self.reference('质控品产品', 'qc_material', row['质控品产品编号或标识'])
        payload = dict(material_id=product, lot_no=row['批号'], expiry_date=self.date(row['效期']),
                       level_name=row['浓度水平'], level_code=row['浓度编号'],
                       concentration_note=row['浓度说明'], catalog_no=row['水平货号'])
        if not payload['lot_no'] or not payload['level_name']:
            raise ValueError('质控材料请填写实际批号和浓度水平名称。')
        specs = [spec for spec in self.records['material_spec'] if spec['qc_material_id'] == product
                 and (spec['level_name'], spec['level_code']) == (payload['level_name'], payload['level_code'])]
        if specs and payload['catalog_no'] and specs[0]['catalog_no'] != payload['catalog_no']:
            raise ValueError('浓度水平和编号已登记不同货号，请核对原资料。')
        lots = [lot for lot in self.records['qc_lot'] if lot['qc_material_id'] == product and _norm(lot['lot_no']) == _norm(payload['lot_no'])]
        if lots:
            if len(lots) != 1 or lots[0]['is_disabled']:
                raise ValueError('此质控品批号已停用或不唯一，请先核对原资料。')
            if lots[0]['expiry_date'] != payload['expiry_date']:
                raise ValueError('同一质控品批号已登记不同效期，请先核对。')
            for level in self.records['qc_level']:
                if level['qc_material_lot_id'] == lots[0]['id'] and (
                    _norm(level['level_name']), _norm(level['level_code'])) == (_norm(payload['level_name']), _norm(payload['level_code'])):
                    if level['is_disabled']:
                        raise ValueError('此批号水平已停用，请先核对原记录。')
                    if payload['concentration_note'] and level['concentration_label'] != payload['concentration_note']:
                        raise ValueError('浓度说明与已有水平不同，请先核对。')
                    if payload['catalog_no']:
                        spec = next((item for item in self.records['material_spec'] if item['id'] == level.get('specification_id')), None)
                        if not spec or spec['catalog_no'] != payload['catalog_no']:
                            raise ValueError('水平货号与已有资料不同，请先核对。')
                    return level['id'], False
        for node in self.plan:
            if node['entity'] == 'control_material' and node['payload']['material_id'] == product and _norm(node['payload']['lot_no']) == _norm(payload['lot_no']):
                if node['payload']['expiry_date'] != payload['expiry_date']:
                    raise ValueError('文件内同一质控品批号的效期不一致。')
                if (_norm(node['payload']['level_name']), _norm(node['payload']['level_code'])) == (_norm(payload['level_name']), _norm(payload['level_code'])):
                    self.check_values(node['payload'], payload)
                    return node['ref'], True
        existing_count = len([level for level in self.records['qc_level'] if lots and
                              level['qc_material_lot_id'] == lots[0]['id'] and not level['is_disabled']])
        planned_count = len([node for node in self.plan if node['entity'] == 'control_material'
                             and node['payload']['material_id'] == product and _norm(node['payload']['lot_no']) == _norm(payload['lot_no'])])
        if existing_count + planned_count >= 9:
            raise ValueError('此质控品批号最多登记9个水平。')
        ref = f'new:control_material:{len(self.plan)}'
        self.plan.append(dict(entity='control_material', ref=ref, payload=payload))
        return ref, True


def prepare_lab_preparation(data):
    if not isinstance(data, (bytes, bytearray)) or not data or len(data) > 10 * 1024 * 1024:
        raise ValueError('请上传不超过10MB的单位资料XLSX文件。')
    sheets = xlsx_bytes_to_dataframes(data)
    with read_snapshot() as connection:
        planner = _Planner(connection)
        before_settings = get_report_settings().to_payload()
        settings = {}
        instructions = sheets.get('填写说明')
        if instructions is not None and list(instructions.columns) == ['事项', '说明']:
            versions = [_text(row['说明']) for row in instructions.to_dict('records') if _text(row['事项']) == '清单版本']
            if versions and versions != [str(FORMAT_VERSION)]:
                planner.current_sheet = '填写说明'
                planner.issue('此资料清单版本不受支持，请重新下载空白清单后填写。')
        planner.current_sheet = '单位信息'
        unit = sheets.get('单位信息')
        if unit is None or list(unit.columns) != ['资料项目', '填写内容']:
            planner.issue('缺少单位信息工作表或列名不符，请使用下载的单位资料模板。')
        else:
            seen = set()
            for number, row in enumerate(unit.to_dict('records'), 2):
                label, value = _text(row['资料项目']), _text(row['填写内容'])
                planner.current_row = number
                if not label and not value:
                    continue
                if label not in UNIT_FIELDS or label in seen:
                    planner.issue('单位信息项目重复或不受支持，请保留模板中的四项。')
                    continue
                seen.add(label)
                if value:
                    settings[UNIT_FIELDS[label]] = value
            for label, key in UNIT_FIELDS.items():
                if not settings.get(key) and not before_settings[key]:
                    planner.current_row = 0
                    planner.issue(label + '尚未准备，可在系统设置补充。', blocking=False, field=label)
        for sheet, frame in sheets.items():
            if sheet not in (*PREPARATION_SHEETS, '单位信息', '填写说明', '已登记资料') and not frame.empty:
                planner.current_sheet, planner.current_row = sheet, 0
                planner.issue('此工作表不属于单位资料模板，请核对文件。')
        row_count = 0
        for sheet, columns in PREPARATION_SHEETS.items():
            planner.current_sheet = sheet
            frame = sheets.get(sheet)
            if frame is None:
                continue
            supplied_columns = list(frame.columns)
            legacy_columns = columns[:-2] if sheet == '质控品产品' else columns
            if supplied_columns not in (columns, legacy_columns):
                planner.current_row = 0
                planner.issue('列名或顺序与模板不符，请使用本工作表模板。')
                continue
            for number, values in enumerate(frame.to_dict('records'), 2):
                row = {label: _text(values.get(label)) for label in columns}
                if not any(row.values()):
                    continue
                row_count += 1
                planner.current_row = number
                if row_count > 5000:
                    planner.issue('一次最多准备5000行资料，请分批核对。')
                    break
                key = row['清单编号']
                if not key or key in planner.inputs[sheet]:
                    planner.issue('清单编号不能为空或在本工作表重复。', field='清单编号')
                    continue
                try:
                    identifier, created = planner.row(sheet, row)
                except (ValueError, StopIteration) as exc:
                    planner.issue(str(exc) or '引用的资料不完整，请核对已登记标识。')
                    continue
                planner.inputs[sheet][key] = identifier
                description = planner.description(SHEET_ENTITIES[sheet], identifier)
                description.extend(value for label, value in row.items()
                                   if label not in ('清单编号', '已登记标识') and value)
                planner.preview.append(dict(工作表=sheet, 清单编号=key, 处理='新增' if created else '引用已有',
                    内容=' · '.join(dict.fromkeys(description)),
                    entity=SHEET_ENTITIES[sheet], reference=identifier))
        settings_changes = {key: value for key, value in settings.items() if value != before_settings[key]}
        if not planner.preview and not settings_changes:
            planner.current_sheet, planner.current_row = '', 0
            planner.issue('没有可保存的资料或单位设置变化，请填写模板后再审查。')
        file_hash = sha256(data).hexdigest()
        review = dict(format_version=FORMAT_VERSION, file_sha256=file_hash, source_fingerprint=planner.fingerprint,
            plan=planner.plan, settings=settings_changes, before_settings=before_settings,
            preview=planner.preview, issues=planner.issues,
            summary=dict(rows=len(planner.preview), create=sum(row['处理'] == '新增' for row in planner.preview),
                reuse=sum(row['处理'] == '引用已有' for row in planner.preview), settings=len(settings_changes),
                has_blocking_errors=any(issue['blocking'] for issue in planner.issues)))
        review['preview_hash'] = _hash(review)
        return review


def _receipt(row, *, reused):
    return dict(json.loads(row['receipt_json']), receipt_id=int(row['id']), reused=reused)


def get_lab_preparation_receipt(receipt_id):
    with read_snapshot() as connection:
        row = connection.execute('SELECT * FROM qc_lab_preparation_receipts WHERE id=?', (int(receipt_id),)).fetchone()
    if row is None:
        raise ValueError('未找到单位资料保存记录。')
    return _receipt(row, reused=False)


def save_lab_preparation(data, *, expected_preview_hash, request_key):
    key = str(request_key or '').strip()
    if not key or not expected_preview_hash:
        raise ValueError('请重新审查单位资料后确认保存。')
    if not isinstance(data, (bytes, bytearray)) or not data or len(data) > 10 * 1024 * 1024:
        raise ValueError('请上传不超过10MB的单位资料XLSX文件。')
    file_hash = sha256(data).hexdigest()
    try:
        with atomic_write() as connection:
            previous = connection.execute('SELECT * FROM qc_lab_preparation_receipts WHERE request_key=?', (key,)).fetchone()
            if previous:
                if previous['file_sha256'] != file_hash:
                    raise ValueError('本次文件已变化，请重新审查并确认。')
                return _receipt(previous, reused=True)
            previous = connection.execute('SELECT * FROM qc_lab_preparation_receipts WHERE file_sha256=?', (file_hash,)).fetchone()
            if previous:
                return _receipt(previous, reused=True)
            review = prepare_lab_preparation(data)
            if review['preview_hash'] != expected_preview_hash:
                raise ValueError('文件或已登记资料已变化，请重新审查；本次文件仍保留。')
            if review['summary']['has_blocking_errors']:
                raise ValueError('单位资料审查未通过，请修正问题后再保存。')
            references = {}
            for node in review['plan']:
                foreign = {'manufacturer_id', 'instrument_model_id', 'default_unit_id', 'reagent_id', 'material_id'}
                payload = {field: references.get(value, value) if field in foreign else value
                           for field, value in node['payload'].items()}
                entity = node['entity']
                if entity == 'control_material':
                    from services.material_workflow_service import register_control_material
                    identifier = register_control_material(**payload)
                elif entity == 'reagent_lot':
                    from services.lot_lifecycle_service import create_reagent_lot
                    identifier = create_reagent_lot(**payload)
                else:
                    identifier = getattr(master, 'create_' + entity)(**payload)
                references[node['ref']] = int(identifier)
            settings = dict(review['before_settings'], **review['settings'])
            if review['settings']:
                save_report_settings_form(settings)
            rows = [dict(row, record_id=references.get(row['reference'], row['reference'])) for row in review['preview']]
            result = dict(format_version=FORMAT_VERSION, file_sha256=file_hash, saved_rows=rows, summary=review['summary'],
                          settings_changes=review['settings'])
            cursor = connection.execute('''INSERT INTO qc_lab_preparation_receipts
                (request_key,file_sha256,preview_hash,before_settings_json,after_settings_json,receipt_json)
                VALUES(?,?,?,?,?,?)''', (key, file_hash, expected_preview_hash,
                _json(review['before_settings']), _json(settings), _json(result)))
            return dict(result, receipt_id=int(cursor.lastrowid), reused=False)
    except sqlite3.Error as exc:
        raise ValueError('单位资料未保存，原资料保持不变。请保留文件，重新审查后重试。') from exc
