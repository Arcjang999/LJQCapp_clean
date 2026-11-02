"""Direct quality inputs preserve catalog, manual and pending-draft intent."""
from datetime import date
from pathlib import Path
import csv
import io
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
from services.quality_target_service import IMPORT_COLUMNS, import_custom_csv, list_catalog, item_context, decode
from tests.quality_applicability_smoke_test import IsolatedDatabase, context, draft
from tests.quality_simple_ui_smoke_test import screen, set_conditions, confirm
from tests.quick_project_setup_smoke_test import database_contents


def custom_limit(item_id):
    item = item_context('project', item_id)
    values = dict.fromkeys(IMPORT_COLUMNS, '')
    values.update({'检验项目': item['test_item_name'], '来源名称': '直接选用的实验室限值',
        '版本': 'FORM-V2', '实施日期': date.today().isoformat(), '单位': item['unit_symbol'],
        '允许不精密度类型': 'CV', '上限': '3.5', '比较符': '<=',
        '来源链接或依据': '已核对的实验室文件', '确认人': '隔离确认人'})
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=IMPORT_COLUMNS)
    writer.writeheader()
    writer.writerow(values)
    import_custom_csv(output.getvalue().encode('utf-8'))
    return next(row['id'] for row in list_catalog() if row['standard'] == values['来源名称'])


def ready_form():
    _, item = draft('直接填写质量要求隔离项目')
    prefix = f'quality_project_{item}'
    identifier = custom_limit(item)
    app = screen('project', item)
    set_conditions(app, prefix, context())
    return app, item, prefix, identifier


def standard_lot(identifier,unit,level_values):
    from database import get_connection
    from services.quality_target_service import get_requirement
    from tests.quality_targets_smoke_test import new_lot,apply
    item,config,_=new_lot('zscore',2)
    with get_connection() as connection:
        unit_id=connection.execute('SELECT id FROM md_units WHERE symbol=?',(unit,)).fetchone()[0]
        connection.execute('UPDATE qc_lot_config_items SET unit_id=? WHERE id=?',(unit_id,item))
        existing=connection.execute('SELECT id FROM md_test_items WHERE chinese_name=? AND is_disabled=0',
                                    (get_requirement(identifier)['name'],)).fetchone()
        if existing:
            connection.execute('UPDATE qc_lot_config_items SET test_item_id=? WHERE id=?',(existing[0],item))
        connection.execute('UPDATE qc_lot_configs SET material_selection_mode=1 WHERE id=?',(config,))
    goal=apply(item,identifier,2,levels=[dict(level_order=order,**value)
                                      for order,value in enumerate(level_values,1)])
    return item,config,goal


class QualityFormV2Tests(unittest.TestCase):
    def healthy(self, app):
        self.assertFalse(list(app.exception), [row.message for row in app.exception])

    def fill_manual(self, app, prefix):
        app.number_input(key=prefix+'_cv_limit').set_value(4.25)
        app.text_input(key=prefix+'_source_name').set_value('手填依据文件')
        app.text_input(key=prefix+'_source_version').set_value('LAB-2')
        app.text_area(key=prefix+'_recorded_text').set_value('本实验室实际质控要求')
        app.text_area(key=prefix+'_evidence').set_value('用于本次方法和浓度尺度')
        app.run()
        self.healthy(app)

    def assert_manual(self, app, prefix):
        self.assertEqual('sd_cv', app.session_state[prefix+'_mode'])
        self.assertEqual(4.25, app.number_input(key=prefix+'_cv_limit').value)
        self.assertEqual('手填依据文件', app.text_input(key=prefix+'_source_name').value)
        self.assertEqual('LAB-2', app.text_input(key=prefix+'_source_version').value)
        self.assertEqual('本实验室实际质控要求', app.text_area(key=prefix+'_recorded_text').value)
        self.assertEqual('用于本次方法和浓度尺度', app.text_area(key=prefix+'_evidence').value)

    def test_complete_conditions_collapse_and_ct_has_no_cv_or_mode_choice(self):
        with IsolatedDatabase():
            _, item = draft('HCV RNA', mode='ct', unit='Ct', method='实时荧光PCR法')
            prefix = f'quality_project_{item}'
            app = screen('project', item)
            self.assertTrue(next(row for row in app.expander if row.label == '修改检测条件').proto.expanded)
            set_conditions(app, prefix, context(technique='realtime_pcr', result_scale='ct', purpose='research'))
            self.assertFalse(next(row for row in app.expander if row.label == '修改检测条件').proto.expanded)
            self.assertFalse(any(row.key == prefix+'_mode' for row in app.radio))
            self.assertFalse(any(row.key == prefix+'_cv_limit' for row in app.number_input))
            self.assertFalse(any(row.key == prefix+'_requirement' for row in app.selectbox))
            self.assertEqual(1, sum(row.label == '要求与文件依据' for row in app.expander))
            app.text_input(key=prefix+'_context_specimen').set_value('').run()
            self.assertTrue(next(row for row in app.expander if row.label == '修改检测条件').proto.expanded)
            self.assertTrue(any('必填 · 请填写标本或基质。' == row.value for row in app.caption))

    def test_catalog_selection_and_cancel_preserve_unsaved_manual_values(self):
        with IsolatedDatabase():
            app, item, prefix, identifier = ready_form()
            before = database_contents()
            self.fill_manual(app, prefix)
            app.selectbox(key=prefix+'_requirement').set_value(identifier).run()
            self.healthy(app)
            self.assertEqual('source', app.session_state[prefix+'_mode'])
            self.assertFalse(any(row.key == prefix+'_cv_limit' for row in app.number_input))
            app.selectbox(key=prefix+'_requirement').set_value(None).run()
            self.healthy(app)
            self.assert_manual(app, prefix)
            self.assertEqual(before, database_contents())

    def test_pending_catalog_reopens_and_cancel_restores_manual_draft(self):
        with IsolatedDatabase():
            app, item, prefix, identifier = ready_form()
            self.fill_manual(app, prefix)
            app.selectbox(key=prefix+'_requirement').set_value(identifier).run()
            app.button(key=prefix+'_save_pending').click().run()
            self.healthy(app)
            saved = decode(item_context('project', item)['quality_review_json'])
            self.assertEqual('pending', saved['status'])
            self.assertEqual('source', saved['draft_recorded']['mode'])
            self.assertEqual(identifier, saved['draft_recorded']['requirement_id'])
            self.assertEqual(4.25, saved['draft_recorded']['cv_limit'])
            self.assertEqual('手填依据文件', saved['draft_recorded']['source_name'])
            app = screen('project', item)
            self.healthy(app)
            self.assertEqual(identifier, app.selectbox(key=prefix+'_requirement').value)
            self.assertEqual('source', app.session_state[prefix+'_mode'])
            app.selectbox(key=prefix+'_requirement').set_value(None).run()
            self.assert_manual(app, prefix)
            confirm(app, prefix)
            self.assertFalse(list(app.error))
            self.assertEqual(4.25, item_context('project', item)['cv_limit'])

    def test_old_recorded_text_can_add_cv_without_selecting_a_mode(self):
        with IsolatedDatabase():
            from services.quality_review_service import save_recorded_requirement
            from tests.quality_applicability_smoke_test import search
            _, item = draft('旧文字要求隔离项目')
            save_recorded_requirement('project', item, source_name='原文件', source_version='R1',
                requirement_text='原实验室要求', confirmed_by='原确认人', evidence='原文件用于本项目',
                context=context(), search_record=search())
            prefix = f'quality_project_{item}'
            app = screen('project', item)
            self.assertFalse(any(row.key == prefix+'_mode' for row in app.radio))
            self.assertEqual('recorded', app.session_state[prefix+'_mode'])
            app.number_input(key=prefix+'_cv_limit').set_value(4.0).run()
            self.assertEqual('sd_cv', app.session_state[prefix+'_mode'])
            self.assertEqual('原文件', app.text_input(key=prefix+'_source_name').value)
            self.assertEqual('原实验室要求', app.text_area(key=prefix+'_recorded_text').value)
            confirm(app, prefix)
            self.assertFalse(list(app.error))
            self.assertEqual(4.0, item_context('project', item)['cv_limit'])

    def test_saved_standard_reopens_concentration_and_category_by_actual_level(self):
        cases=[('wst403-2024-082','mmol/L','concentration',[5.0,6.0]),
               ('wst406-2024-wbc','10^9/L','category',['低浓度','中/高浓度'])]
        for identifier,unit,field,values in cases:
            with self.subTest(standard=identifier),IsolatedDatabase():
                item,_,goal=standard_lot(identifier,unit,[{field:value} for value in values])
                prefix=f'quality_lot_{item}'
                app=screen('lot',item);self.healthy(app)
                widgets=app.number_input if field=='concentration' else app.selectbox
                self.assertEqual(values,[widgets(key=f'{prefix}_{identifier}_{order}_{field}').value
                                         for order in (1,2)])
                app.button(key=prefix+'_save_pending').click().run();self.healthy(app)
                pending=decode(item_context('lot',item)['quality_review_json'])['draft_recorded']['levels']
                self.assertEqual([row['qc_level_id'] for row in goal['levels']],
                                 [row['qc_level_id'] for row in pending])
                app=screen('lot',item);self.healthy(app)
                widgets=app.number_input if field=='concentration' else app.selectbox
                self.assertEqual(values,[widgets(key=f'{prefix}_{identifier}_{order}_{field}').value
                                         for order in (1,2)])

    def test_material_change_drops_old_standard_values_and_cache_for_replaced_level(self):
        self._check_material_change_source_values(save_pending=True)

    def test_confirmed_source_direct_material_change_preserves_only_unchanged_actual_level(self):
        self._check_material_change_source_values(save_pending=False)

    def _check_material_change_source_values(self,*,save_pending):
        from database import get_connection
        from services.master_data_service import create_manufacturer,create_qc_material
        from services.material_workflow_service import register_control_material
        from services.project_config_service import save_lot_item_levels,activate_lot_config
        from services.quality_review_service import validate_quality_review
        cases=[('wst403-2024-082','mmol/L','concentration',[5.0,6.0],None),
               ('wst406-2024-wbc','10^9/L','category',['低浓度','中/高浓度'],'')]
        for identifier,unit,field,values,empty in cases:
            with self.subTest(standard=identifier),IsolatedDatabase():
                item,config,goal=standard_lot(identifier,unit,[{field:value} for value in values])
                prefix=f'quality_lot_{item}'
                app=screen('lot',item);self.healthy(app)
                if save_pending:
                    app.button(key=prefix+'_save_pending').click().run();self.healthy(app)
                maker=create_manufacturer(display_name='换产品核查厂家',categories=['qc_material'])
                product=create_qc_material(manufacturer_id=maker,generic_name='新货号质控品',catalog_no='NEW-SKU')
                replacement=register_control_material(material_id=product,lot_no='NEW-FACTORY-LOT',
                    expiry_date='2029-12-31',level_name='原厂首水平',level_code='N1')
                save_lot_item_levels(item,[dict(qc_level_id=level,target_source='building')
                    for level in (goal['levels'][0]['qc_level_id'],replacement)])
                self.assertTrue(decode(item_context('lot',item)['quality_goal_json'])['pending'])
                if not save_pending:
                    review=decode(item_context('lot',item)['quality_review_json'])
                    self.assertEqual('pending',review['status'])
                    self.assertEqual([goal['levels'][0]['qc_level_id']],
                        [row['qc_level_id'] for row in review['draft_recorded']['levels']])
                self.assertTrue(validate_quality_review('lot',item))
                with self.assertRaises(ValueError):
                    activate_lot_config(config)
                app.run();self.healthy(app)
                widgets=app.number_input if field=='concentration' else app.selectbox
                self.assertEqual([values[0],empty],[widgets(key=f'{prefix}_{identifier}_{order}_{field}').value
                                                   for order in (1,2)])
                app=screen('lot',item);self.healthy(app)
                widgets=app.number_input if field=='concentration' else app.selectbox
                self.assertEqual([values[0],empty],[widgets(key=f'{prefix}_{identifier}_{order}_{field}').value
                                                   for order in (1,2)])
                confirm(app,prefix)
                self.assertTrue(list(app.error))
                self.assertTrue(decode(item_context('lot',item)['quality_goal_json'])['pending'])
                with get_connection() as connection:
                    self.assertIsNone(connection.execute('SELECT activated_at FROM qc_lot_configs WHERE id=?',
                                                         (config,)).fetchone()[0])

    def test_changed_unit_or_method_cannot_seed_an_unchanged_material_from_old_source(self):
        from database import get_connection
        from services.master_data_service import create_method
        from services.material_workflow_service import register_control_material
        from services.project_config_service import save_lot_item_levels,activate_lot_config
        from services.quality_review_service import validate_quality_review
        for field in ('unit_id','method_id'):
            with self.subTest(changed=field),IsolatedDatabase():
                item,config,goal=standard_lot('wst403-2024-082','mmol/L',
                    [dict(concentration=5.0),dict(concentration=6.0)])
                with get_connection() as connection:
                    product=connection.execute('''SELECT q.qc_material_id FROM md_qc_levels l
                        JOIN md_qc_material_lots q ON q.id=l.qc_material_lot_id WHERE l.id=?''',
                        (goal['levels'][0]['qc_level_id'],)).fetchone()[0]
                    changed=(connection.execute("SELECT id FROM md_units WHERE symbol='g/L'").fetchone()[0]
                             if field=='unit_id' else create_method(method_name='改后未核对的方法'))
                    connection.execute(f'UPDATE qc_lot_config_items SET {field}=? WHERE id=?',(changed,item))
                replacement=register_control_material(material_id=product,lot_no='CHANGED-CONTEXT-LOT',
                    expiry_date='2029-12-31',level_name='原厂新水平',level_code='C1')
                save_lot_item_levels(item,[dict(qc_level_id=level,target_source='building')
                    for level in (goal['levels'][0]['qc_level_id'],replacement)])
                review=decode(item_context('lot',item)['quality_review_json'])
                self.assertFalse(review.get('draft_recorded',{}).get('levels'))
                self.assertTrue(validate_quality_review('lot',item))
                with self.assertRaises(ValueError):
                    activate_lot_config(config)

    def test_two_products_with_physical_order_one_keep_separate_standard_limits_and_save(self):
        from database import get_connection
        from services.quality_target_service import get_requirement
        from tests.multi_product_control_workflow_smoke_test import multi_values
        from tests.quick_project_setup_ui_smoke_test import open_details, quality_prefix
        with IsolatedDatabase():
            values=multi_values(2)
            identifier='wst403-2024-082'
            spec=get_requirement(identifier)
            values['template_name']='两货号各自首水平的分浓度标准'
            with get_connection() as connection:
                existing=connection.execute('SELECT id FROM md_test_items WHERE chinese_name=? AND is_disabled=0',
                                            (spec['name'],)).fetchone()
                if existing:
                    values['test_item_id']=existing[0]
                else:
                    connection.execute('UPDATE md_test_items SET chinese_name=?,abbreviation=? WHERE id=?',
                                       (spec['name'],'',values['test_item_id']))
                connection.execute("UPDATE md_methods SET method_name='便携式血糖仪法',method_code='',principle='' WHERE id=?",
                                   (values['method_id'],))
                values['unit_id']=connection.execute("SELECT id FROM md_units WHERE symbol='mmol/L'").fetchone()[0]
                self.assertEqual([1,1],[connection.execute('SELECT level_order FROM md_qc_levels WHERE id=?',
                    (level,)).fetchone()[0] for level in values['level_ids']])
            app=open_details(values)
            app.button(key='quick_details_next').click().run()
            self.healthy(app)
            prefix=quality_prefix(app)
            set_conditions(app,prefix,context(technique='clinical_chemistry',specimen='全血'))
            self.assertEqual(identifier,app.session_state[prefix+'_requirement'])
            keys=[f'{prefix}_{identifier}_{order}_concentration' for order in (1,2)]
            self.assertEqual(set(keys),{row.key for row in app.number_input
                                       if (row.key or '').endswith('_concentration')})
            for key,concentration in zip(keys,(5.0,6.0)):
                app.number_input(key=key).set_value(concentration)
            app.run();self.healthy(app)
            limits=next(row.value for row in app.dataframe if list(row.value.columns)==['水平','采用限值'])
            self.assertEqual([1,2],limits['水平'].tolist())
            self.assertEqual(['< 0.42mmol/L（SD）','< 7.5%（CV）'],limits['采用限值'].tolist())
            app.text_input(key=prefix+'_person').set_value('两产品标准核对人')
            app.text_area(key=prefix+'_evidence').set_value('按两个实际产品的浓度分别核对标准限值。')
            app.checkbox(key=prefix+'_checked').check().run()
            self.assertFalse(app.button(key='quick_quality_next').disabled)
            app.button(key='quick_quality_next').click().run();self.healthy(app)
            self.assertEqual('preview',app.session_state['project_workspace_dialog']['quick_step'])
            app.button(key='quick_preview_back').click().run();self.healthy(app)
            self.assertEqual([5.0,6.0],[app.number_input(key=key).value for key in keys])
            app.button(key='quick_quality_next').click().run();self.healthy(app)
            app.button(key='quick_save').click().run();self.healthy(app)
            self.assertFalse(list(app.error))
            with get_connection() as connection:
                saved=connection.execute('''SELECT i.id,i.quality_goal_json FROM qc_lot_config_items i
                    JOIN qc_lot_configs c ON c.id=i.lot_config_id
                    JOIN qc_project_templates p ON p.id=c.template_id WHERE p.template_name=?''',
                    (values['template_name'],)).fetchone()
                self.assertIsNotNone(saved)
                assignments=connection.execute('''SELECT a.level_order,a.qc_level_id,l.level_order AS physical_order
                    FROM qc_lot_config_item_levels a JOIN md_qc_levels l ON l.id=a.qc_level_id
                    WHERE a.lot_config_item_id=? ORDER BY a.level_order''',(saved['id'],)).fetchall()
            self.assertEqual([1,2],[row['level_order'] for row in assignments])
            self.assertEqual(values['level_ids'],[row['qc_level_id'] for row in assignments])
            self.assertEqual([1,1],[row['physical_order'] for row in assignments])
            goal=decode(saved['quality_goal_json'])
            self.assertEqual(identifier,goal['spec']['id'])
            self.assertEqual([1,2],[row['level_order'] for row in goal['levels']])
            self.assertEqual(values['level_ids'],[row['qc_level_id'] for row in goal['levels']])
            self.assertEqual([5.0,6.0],[row['concentration'] for row in goal['levels']])
            self.assertEqual(['sd','cv'],[row['rule']['kind'] for row in goal['levels']])


if __name__ == '__main__':
    unittest.main(verbosity=2)
