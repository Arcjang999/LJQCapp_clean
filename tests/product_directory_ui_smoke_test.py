"""New isolated AppTest coverage for manufacturer roles and directory UI."""
from pathlib import Path
from copy import deepcopy
import json
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from streamlit.testing.v1 import AppTest
from tests import product_directory_smoke_test as directory_fixture
from tests.master_data_dialogs_smoke_test import make_app,open_dialog,field_key
from tests.project_workspace_smoke_test import select_table_row
from services import master_data_service as master
from services.master_data_edit_service import get_master_record_context,save_master_record
from services import product_directory_service as directory


class ProductDirectoryUITest(unittest.TestCase):
    setUp=directory_fixture.DirectoryTest.setUp
    tearDown=directory_fixture.DirectoryTest.tearDown
    dump=directory_fixture.DirectoryTest.dump

    def healthy(self,app):
        self.assertFalse(list(app.exception),str(list(app.exception)))

    def widget(self,app,kind,label):
        matches=[value for value in getattr(app,kind) if value.label==label]
        self.assertEqual(len(matches),1,(kind,label))
        return matches[0]

    def fill(self,app,kind,label,value):
        self.widget(app,kind,label).set_value(value).run();self.healthy(app)

    def click(self,app,label):
        self.widget(app,'button',label).click().run();self.healthy(app)

    def material_page(self):
        app=AppTest.from_string('from ui.material_catalog import render_material_catalog\nrender_material_catalog(include_directory=True)',default_timeout=30).run()
        self.healthy(app)
        return app

    def open_picker(self,app):
        self.click(app,'选择检验项目')
        state=app.session_state['material_dialog']
        self.assertEqual(state['kind'],'coverage_picker')
        return f'coverage_picker_{state["nonce"]}'

    def picker_table(self,app):
        prefix=f'coverage_picker_{app.session_state["material_dialog"]["nonce"]}_table_'
        matches=[table for table in app.dataframe if prefix in table.proto.id]
        self.assertEqual(len(matches),1)
        return matches[0]

    def search_picker(self,app,query):
        prefix=f'coverage_picker_{app.session_state["material_dialog"]["nonce"]}'
        app.text_input(key=prefix+'_search').set_value(query).run()
        self.healthy(app)

    def select_picker_rows(self,app,names):
        table=self.picker_table(app)
        visible=table.value['检验项目'].tolist()
        rows=[visible.index(name) for name in names]
        states=app._tree.get_widget_states()
        widget=states.widgets.add()
        widget.id=table.proto.id
        widget.string_value=json.dumps({'selection':{'rows':rows,'columns':[],'cells':[]}})
        app._run(states)
        self.healthy(app)

    def assert_preselected(self,app,names):
        table=self.picker_table(app)
        rows=json.loads(table.proto.selection_default)['selection']['rows']
        self.assertEqual({table.value.iloc[index]['检验项目'] for index in rows},set(names))

    def test_three_candidate_paths_categories_cancel_and_stale_draft(self):
        instrument=master.create_manufacturer(display_name='仅仪器厂家',categories=['instrument'])
        reagent=master.create_manufacturer(display_name='仅试剂厂家',categories=['reagent'])
        mixed=master.create_manufacturer(display_name='仪器和质控厂家',categories=['instrument','qc_material'])
        for entity,expected in [('instrument_model',{'仅仪器厂家','仪器和质控厂家'}),('reagent',{'仅试剂厂家'})]:
            app=open_dialog(make_app({'record':dict(entity_type=entity)}))
            self.assertEqual(set(app.selectbox(key=field_key(app,'manufacturer_id')).options)-{'请选择'},expected)
        app=self.material_page();self.click(app,'新增质控品')
        self.assertEqual(set(self.widget(app,'selectbox','厂家 *').options)-{'请选择厂家'},{'邦德盛','仪器和质控厂家'})
        app=open_dialog(make_app({'record':dict(entity_type='manufacturer',entity_id=mixed)}))
        self.fill(app,'multiselect','厂家业务类别',['instrument'])
        self.click(app,'取消');self.click(app,'放弃修改')
        self.assertEqual(master.get_manufacturer_categories(mixed),['instrument','qc_material'])
        open_dialog(app)
        self.fill(app,'multiselect','厂家业务类别',['instrument','qc_material','reagent'])
        context=get_master_record_context('manufacturer',mixed)
        save_master_record('manufacturer',{'notes':'另一处修改'},entity_id=mixed,expected_fingerprint=context['fingerprint'])
        before=self.dump();self.click(app,'保存')
        self.assertTrue(list(app.error));self.assertEqual(before,self.dump())
        self.assertEqual(set(self.widget(app,'multiselect','厂家业务类别').value),{'instrument','qc_material','reagent'})

    def test_real_directory_preview_cancel_publish_duplicate_and_export(self):
        app=self.material_page()
        self.fill(app,'selectbox','目录所属质控品厂家',self.manufacturer)
        before=self.dump();self.click(app,'预览目录');self.assertEqual(before,self.dump())
        preview=app.session_state['directory_preview_draft']['preview']
        self.assertEqual((preview['included_count'],preview['excluded_count']),(1054,252))
        self.click(app,'取消目录预览');self.assertEqual(before,self.dump())
        self.click(app,'预览目录')
        self.fill(app,'text_input','本次目录核对人','实际目录核对员')
        self.assertEqual(before,self.dump())
        self.click(app,'确认发布目录')
        self.assertEqual(len(master.list_qc_materials()),1054)
        self.assertEqual(len(directory.list_product_directory_releases()),1)
        self.assertTrue(any(element.proto.label=='导出当前目录清单' for element in app.get('download_button')))
        self.click(app,'预览目录');self.click(app,'确认发布目录')
        self.assertEqual(len(master.list_qc_materials()),1054)
        self.assertEqual(len(directory.list_product_directory_releases()),1)
        self.fill(app,'checkbox','包含排除记录',True)
        self.assertTrue(any('排除原因' in frame.value.columns and len(frame.value)==1306 for frame in app.dataframe))
        # Exercise the actual enclosing page: a directory product must have
        # exactly one product-status action, just like a local product.
        full=AppTest.from_string('from pages.master_data_page import render_master_data_page\nrender_master_data_page()',default_timeout=30).run()
        self.healthy(full)
        self.assertEqual(len([button for button in full.button if button.label=='停用质控品']),1)
        self.assertTrue(full.button(key='material_catalog_status_product').disabled)
        self.assertIsNone(full.session_state['product_catalogue_selected_id'])
        self.assertFalse(any(widget.key=='material_catalog_product_id' for widget in full.selectbox))
        table_index=next(index for index,table in enumerate(full.dataframe)
            if 'product_catalogue_table_' in table.proto.id)
        selected_code=full.dataframe[table_index].value.iloc[-1]['产品编号']
        expected_id=int(directory.list_catalog_product_choices().query('product_code == @selected_code').iloc[0]['product_id'])
        select_table_row(full,len(full.dataframe[table_index].value)-1,index=table_index)
        self.healthy(full)
        self.assertEqual(full.session_state['product_catalogue_selected_id'],expected_id)
        self.assertNotIn('material_dialog',full.session_state)
        self.assertEqual(len([button for button in full.button if button.label=='停用质控品']),1)
        self.assertFalse(full.button(key='material_catalog_status_product').disabled)
        self.assertFalse(full.button(key='product_catalogue_relationships').disabled)
        self.assertFalse(full.button(key='product_catalogue_batches').disabled)

    def test_coverage_cancel_save_stale_failure_retains_selected_ids(self):
        product=master.create_qc_material(manufacturer_id=self.manufacturer,generic_name='人工核对产品')
        first=master.create_test_item(chinese_name='覆盖项目甲',standard_code='COVER-13579')
        second=master.create_test_item(chinese_name='覆盖项目乙',standard_code='COVER-24680')
        master.create_alias(entity_type='test_item',entity_id=first,alias_text='适用别称甲')
        master.create_alias(entity_type='test_item',entity_id=second,alias_text='适用别称乙')
        app=self.material_page()
        self.assertFalse([item for item in app.multiselect if item.label=='选择检验项目'])
        self.assertFalse([item for item in app.text_input if item.label=='查找检验项目'])
        self.fill(app,'text_input','核对人','核对员')
        self.fill(app,'text_area','参考资料','说明书另行逐项核对')
        outer=deepcopy(app.session_state['product_coverage_drafts'][str(product)])
        before=self.dump()
        prefix=self.open_picker(app)
        self.search_picker(app,'覆盖 甲')
        self.assertEqual(self.picker_table(app).value['检验项目'].tolist(),['覆盖项目甲'])
        self.select_picker_rows(app,['覆盖项目甲'])
        self.search_picker(app,'COVER-24680')
        self.assertEqual(self.picker_table(app).value['检验项目'].tolist(),['覆盖项目乙'])
        self.select_picker_rows(app,['覆盖项目乙'])
        self.assertEqual(set(app.session_state['material_dialog']['selected']),{first,second})
        self.search_picker(app,'别称甲')
        self.assert_preselected(app,['覆盖项目甲'])
        self.select_picker_rows(app,[])
        self.assertEqual(app.session_state['material_dialog']['selected'],[second])
        self.search_picker(app,'COVER-13579')
        self.assert_preselected(app,[])
        self.select_picker_rows(app,['覆盖项目甲'])
        self.assertEqual(app.session_state['product_coverage_drafts'][str(product)],outer)
        self.assertEqual(before,self.dump())
        app.button(key=prefix+'_confirm').click().run();self.healthy(app)
        self.assertEqual(set(app.session_state['product_coverage_drafts'][str(product)]['selected']),{first,second})
        self.assertEqual(app.session_state['product_coverage_drafts'][str(product)]['version'],outer['version'])
        self.assertEqual(before,self.dump())
        self.assertEqual(self.widget(app,'text_input','核对人').value,'核对员')
        self.assertEqual(self.widget(app,'text_area','参考资料').value,'说明书另行逐项核对')
        self.click(app,'取消修改');self.click(app,'放弃修改')
        self.assertEqual(directory.get_product_relationships(product)['coverage'],[])
        self.assertEqual(app.session_state['product_coverage_drafts'][str(product)]['selected'],[])
        self.assertEqual(before,self.dump())
        prefix=self.open_picker(app)
        self.search_picker(app,'COVER-13579')
        self.select_picker_rows(app,['覆盖项目甲'])
        app.button(key=prefix+'_confirm').click().run();self.healthy(app)
        self.fill(app,'text_input','核对人','核对员')
        self.fill(app,'text_area','参考资料','说明书明确适用')
        self.click(app,'保存适用项目')
        self.assertEqual(directory.get_product_relationships(product)['coverage'][0]['test_item_id'],first)
        self.fill(app,'text_input','核对人','另一操作人')
        self.fill(app,'text_area','参考资料','待保存草稿')
        outer=deepcopy(app.session_state['product_coverage_drafts'][str(product)])
        before=self.dump()
        prefix=self.open_picker(app)
        self.search_picker(app,'COVER-13579');self.assert_preselected(app,['覆盖项目甲'])
        self.search_picker(app,'别称乙');self.select_picker_rows(app,['覆盖项目乙'])
        app.button(key=prefix+'_cancel').click().run();self.healthy(app)
        self.assertEqual(app.session_state['product_coverage_drafts'][str(product)],outer)
        self.assertEqual(self.widget(app,'text_input','核对人').value,'另一操作人')
        self.assertEqual(self.widget(app,'text_area','参考资料').value,'待保存草稿')
        self.assertEqual(before,self.dump())
        prefix=self.open_picker(app)
        self.search_picker(app,'COVER-13579');self.assert_preselected(app,['覆盖项目甲'])
        self.search_picker(app,'COVER-24680');self.select_picker_rows(app,['覆盖项目乙'])
        app.button(key=prefix+'_confirm').click().run();self.healthy(app)
        current=directory.get_product_relationships(product)
        directory.save_product_coverage(product,[],expected_version=current['edit_version'],confirmed_by='外部核对员',evidence='移除旧关联')
        before=self.dump();self.click(app,'保存适用项目')
        self.assertTrue(list(app.error));self.assertEqual(before,self.dump())
        self.assertEqual(set(app.session_state['product_coverage_drafts'][str(product)]['selected']),{first,second})
        self.assertEqual(self.widget(app,'text_input','核对人').value,'另一操作人')
        self.assertEqual(self.widget(app,'text_area','参考资料').value,'待保存草稿')

    def test_coverage_picker_isolates_products_and_retains_method_constraints(self):
        first_product=master.create_qc_material(manufacturer_id=self.manufacturer,generic_name='适用项目产品甲')
        second_product=master.create_qc_material(manufacturer_id=self.manufacturer,generic_name='适用项目产品乙')
        first=master.create_test_item(chinese_name='指定方法项目甲',standard_code='PICK-13579')
        second=master.create_test_item(chinese_name='指定方法项目乙',standard_code='PICK-24680')
        method=master.create_method(method_name='覆盖核对专用方法学')
        current=directory.get_product_relationships(second_product)
        directory.save_product_coverage(second_product,[{'test_item_id':first,'method_id':method}],
            expected_version=current['edit_version'],confirmed_by='原核对员',evidence='原始适用条件')
        app=self.material_page()
        app.selectbox(key='material_catalog_product_id').set_value(first_product).run();self.healthy(app)
        prefix=self.open_picker(app)
        self.search_picker(app,'PICK-24680');self.select_picker_rows(app,['指定方法项目乙'])
        before=self.dump()
        app.button(key=prefix+'_confirm').click().run();self.healthy(app)
        self.assertEqual(before,self.dump())
        app.selectbox(key='material_catalog_product_id').set_value(second_product).run();self.healthy(app)
        self.assertEqual(app.session_state['product_coverage_drafts'][str(first_product)]['selected'],[second])
        self.assertEqual(app.session_state['product_coverage_drafts'][str(second_product)]['selected'],[first])
        prefix=self.open_picker(app)
        self.search_picker(app,'PICK-13579');self.assert_preselected(app,['指定方法项目甲'])
        app.button(key=prefix+f'_remove_{first}').click().run();self.healthy(app)
        self.search_picker(app,'项目甲');self.assert_preselected(app,[])
        app.button(key=prefix+'_cancel').click().run();self.healthy(app)
        self.assertEqual(app.session_state['product_coverage_drafts'][str(second_product)]['selected'],[first])
        prefix=self.open_picker(app)
        self.search_picker(app,'PICK-24680');self.select_picker_rows(app,['指定方法项目乙'])
        app.button(key=prefix+'_confirm').click().run();self.healthy(app)
        self.fill(app,'text_input','核对人','新增项目核对员')
        self.fill(app,'text_area','参考资料','新增项目适用条件')
        self.click(app,'保存适用项目')
        coverage={row['test_item_id']:row for row in directory.get_product_relationships(second_product)['coverage'] if not row['is_disabled']}
        self.assertEqual(set(coverage),{first,second})
        self.assertEqual(coverage[first]['method_id'],method)
        self.assertEqual(coverage[first]['evidence'],'原始适用条件')
        self.assertIsNone(coverage[second]['method_id'])
        self.assertEqual(directory.get_product_relationships(first_product)['coverage'],[])
        self.assertEqual(app.session_state['product_coverage_drafts'][str(first_product)]['selected'],[second])
        master.set_master_entity_disabled('test_item',first,is_disabled=True,reason='不再使用')
        app.run();self.healthy(app)
        before=self.dump()
        prefix=self.open_picker(app)
        self.assertTrue(any('已停用' in item.value for item in app.warning))
        app.button(key=prefix+'_confirm').click().run();self.healthy(app)
        self.assertTrue(any('请先移除' in item.value for item in app.error))
        self.assertEqual(before,self.dump())
        app.button(key=prefix+f'_remove_{first}').click().run();self.healthy(app)
        self.search_picker(app,'PICK-24680');self.assert_preselected(app,['指定方法项目乙'])
        app.button(key=prefix+'_confirm').click().run();self.healthy(app)
        self.assertEqual(app.session_state['product_coverage_drafts'][str(second_product)]['selected'],[second])
        self.assertEqual(before,self.dump())

    def test_directory_preview_invalidated_by_manufacturer_source_and_content(self):
        app = AppTest.from_string('''
import io
from unittest.mock import patch
import streamlit as st
from ui.material_catalog import render_material_catalog
content = st.session_state.get('test_upload_content')
uploaded = None
if content is not None:
    uploaded = io.BytesIO(content)
    uploaded.name = st.session_state.get('test_upload_name', '目录.json')
with patch.object(st, 'file_uploader', return_value=uploaded):
    render_material_catalog(include_directory=True)
''', default_timeout=30).run()
        self.healthy(app)
        other = master.create_manufacturer(display_name='另一质控厂家', categories=['qc_material'])
        app.run()
        self.fill(app, 'selectbox', '目录所属质控品厂家', self.manufacturer)
        before = self.dump()
        self.click(app, '预览目录')
        self.assertFalse(self.widget(app, 'button', '确认发布目录').disabled)
        self.fill(app, 'selectbox', '目录所属质控品厂家', other)
        self.assertTrue(self.widget(app, 'button', '确认发布目录').disabled)
        self.assertTrue(any('重新预览' in item.value for item in app.warning))
        # Returning to an earlier choice must not silently revalidate a preview.
        self.fill(app, 'selectbox', '目录所属质控品厂家', self.manufacturer)
        self.assertTrue(self.widget(app, 'button', '确认发布目录').disabled)
        self.click(app, '预览目录')
        self.assertFalse(self.widget(app, 'button', '确认发布目录').disabled)
        package = directory.load_bondson_directory_package()
        app.session_state['test_upload_content'] = json.dumps(package, ensure_ascii=False).encode('utf-8')
        app.run(); self.healthy(app)
        # The same package selected from a different source still needs review.
        self.assertTrue(self.widget(app, 'button', '确认发布目录').disabled)
        self.click(app, '预览目录')
        self.assertFalse(self.widget(app, 'button', '确认发布目录').disabled)
        package['version_label'] = '重新核对版本'
        app.session_state['test_upload_content'] = json.dumps(package, ensure_ascii=False).encode('utf-8')
        app.run(); self.healthy(app)
        # A replacement with the same filename is identified by its bytes.
        self.assertTrue(self.widget(app, 'button', '确认发布目录').disabled)
        self.assertEqual(before, self.dump())
        self.click(app, '预览目录')
        self.fill(app, 'text_input', '本次目录核对人', '重新核对员')
        self.click(app, '确认发布目录')
        self.assertEqual(len(master.list_qc_materials()), 1054)
        self.assertEqual(directory.list_product_directory_releases().iloc[0]['version_label'], '重新核对版本')


if __name__=='__main__':unittest.main(verbosity=2)
