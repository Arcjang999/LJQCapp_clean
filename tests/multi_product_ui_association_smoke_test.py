"""Upstream changes keep independent inputs and invalidate dependent confirmations."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import get_connection
from services.master_data_service import (create_lab_instrument, create_reagent, create_method,
    create_qc_material, create_qc_lot, create_qc_level, list_units)
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.quick_project_setup_smoke_test import metadata_values, database_contents
from tests.quick_project_setup_ui_smoke_test import open_details, to_preview, product_prefix, quality_prefix
from tests.project_workspace_smoke_test import assert_clean, draft_key


def directory_values():
    from services import product_directory_service as directory
    values = metadata_values('zscore', 2)
    with get_connection() as c:
        maker = c.execute('SELECT manufacturer_id FROM md_qc_materials WHERE id=?',
            (values['qc_material_id'],)).fetchone()[0]
    package = directory.load_bondson_directory_package()
    package.update(source_code='ui-quality-semantic', version_label='1', excluded=[],
        records=[dict(product_code='SECOND-CATALOG', product_name='目录第二水平产品',
            concentration='200 IU/mL', concentration_code='L2', source_rows=[2])])
    preview = directory.preview_product_directory(package, maker)
    release = directory.publish_product_directory(package, maker,
        expected_preview_hash=preview['preview_hash'], published_by='目录业务核对人')
    product = int(directory.list_directory_products(release['release_id']).iloc[0]['product_id'])
    lot = create_qc_lot(qc_material_id=product, lot_no='SECOND-ACTUAL-LOT', expiry_date='2029-12-31')
    level = create_qc_level(qc_material_lot_id=lot, level_name='第二原厂水平', level_code='L2', level_order=1)
    values['level_product_ids'] = [values['qc_material_id'], product]
    values['level_ids'][1] = level
    return values, package, maker, lot


def publish(package, maker):
    from services import product_directory_service as directory
    preview = directory.preview_product_directory(package, maker)
    assert not preview['issues'], preview['issues']
    return directory.publish_product_directory(package, maker,
        expected_preview_hash=preview['preview_hash'], published_by='目录业务核对人')


def return_to_quality(app):
    app.button(key='quick_preview_details').click().run()
    confirmations = [button for button in app.button if button.key == product_prefix(app) + '_confirm']
    if confirmations:
        assert not confirmations[0].disabled
        confirmations[0].click().run()
    app.button(key='quick_details_next').click().run()
    assert_clean(app)


class MultiProductUiAssociationTests(unittest.TestCase):
    def test_selected_catalog_business_changes_require_fresh_quality_with_identical_ids(self):
        for field in ('concentration', 'concentration_code'):
            with self.subTest(field=field), IsolatedDatabase():
                values, package, maker, _ = directory_values()
                app = open_details(values)
                to_preview(app, values)
                identity = app.session_state['project_workspace_dialog']['quick_quality_identity']
                package['version_label'] = '2'
                package['records'][0][field] = '250 IU/mL' if field == 'concentration' else 'L2-UPDATED'
                publish(package, maker)
                before = database_contents()
                return_to_quality(app)
                ctx = app.session_state['project_workspace_dialog']
                self.assertNotEqual(identity, ctx['quick_quality_identity'])
                self.assertEqual(values['level_ids'], ctx['draft']['level_ids'])
                self.assertFalse(app.checkbox(key=quality_prefix(app) + '_checked').value)
                self.assertTrue(app.button(key='quick_quality_next').disabled)
                self.assertEqual(before, database_contents())

    def test_local_product_and_actual_level_business_changes_require_fresh_quality(self):
        changes = ('product_concentration', 'product_concentration_code', 'level_code', 'concentration_label', 'expiry_date')
        for field in changes:
            with self.subTest(field=field), IsolatedDatabase():
                from tests.multi_product_control_workflow_smoke_test import multi_values
                values = multi_values()
                app = open_details(values)
                to_preview(app, values)
                identity = app.session_state['project_workspace_dialog']['quick_quality_identity']
                if field == 'concentration_label':
                    app.button(key='quick_preview_back').click().run()
                with get_connection() as c:
                    if field.startswith('product_'):
                        column = field.removeprefix('product_')
                        c.execute('UPDATE md_qc_materials SET ' + column + '=? WHERE id=?',
                            ('UPDATED-250', values['level_product_ids'][1]))
                    elif field == 'expiry_date':
                        c.execute('''UPDATE md_qc_material_lots SET expiry_date='2029-11-30'
                            WHERE id=(SELECT qc_material_lot_id FROM md_qc_levels WHERE id=?)''', (values['level_ids'][1],))
                    else:
                        c.execute('UPDATE md_qc_levels SET ' + field + '=? WHERE id=?', ('新的实际水平属性', values['level_ids'][1]))
                before = database_contents()
                if field == 'concentration_label':
                    app.run()
                    assert_clean(app)
                else:
                    return_to_quality(app)
                ctx = app.session_state['project_workspace_dialog']
                self.assertNotEqual(identity, ctx['quick_quality_identity'])
                self.assertEqual(values['level_ids'], ctx['draft']['level_ids'])
                self.assertFalse(app.checkbox(key=quality_prefix(app) + '_checked').value)
                self.assertEqual(before, database_contents())

    def test_same_business_release_evidence_and_unselected_records_keep_quality_draft(self):
        for change in ('same_content_release', 'evidence_only', 'unselected_product_and_lot', 'timestamps_only'):
            with self.subTest(change=change), IsolatedDatabase():
                values, package, maker, _ = directory_values()
                app = open_details(values)
                to_preview(app, values)
                ctx = app.session_state['project_workspace_dialog']
                identity = ctx['quick_quality_identity']
                original_quality = deepcopy(ctx['draft']['quality'])
                if change == 'same_content_release':
                    package['version_label'] = '2'
                    publish(package, maker)
                elif change == 'unselected_product_and_lot':
                    create_qc_material(manufacturer_id=maker, generic_name='未选中的产品', catalog_no='UNSELECTED')
                    create_qc_lot(qc_material_id=values['level_product_ids'][1], lot_no='UNSELECTED-LOT', expiry_date='2029-12-31')
                elif change == 'timestamps_only':
                    with get_connection() as c:
                        c.execute("UPDATE md_qc_levels SET updated_at='2099-01-01' WHERE id=?", (values['level_ids'][1],))
                before = database_contents()
                if change == 'evidence_only':
                    app.button(key='quick_preview_details').click().run()
                    prefix = product_prefix(app) + '_' + str(values['level_product_ids'][1])
                    app.button(key=prefix + '_edit').click().run()
                    app.text_area(key=prefix + '_evidence').set_value('只补充所选产品的适用依据文字').run()
                    app.button(key=product_prefix(app) + '_confirm').click().run()
                    app.button(key='quick_details_next').click().run()
                else:
                    return_to_quality(app)
                assert_clean(app)
                ctx = app.session_state['project_workspace_dialog']
                self.assertEqual(identity, ctx['quick_quality_identity'])
                self.assertTrue(app.checkbox(key=quality_prefix(app) + '_checked').value)
                self.assertEqual(original_quality, ctx['draft']['quality'])
                self.assertFalse(app.button(key='quick_quality_next').disabled)
                self.assertEqual(values['level_ids'], ctx['draft']['level_ids'])
                self.assertEqual(before, database_contents())

    def test_scope_changes_require_fresh_quality_confirmation_without_losing_materials(self):
        for field in ('lab_instrument_id', 'reagent_id', 'method_id', 'unit_id'):
            with self.subTest(field=field), IsolatedDatabase():
                values = metadata_values('zscore', 2)
                with get_connection() as c:
                    model = c.execute('SELECT instrument_model_id FROM lab_instruments WHERE id=?',
                        (values['lab_instrument_id'],)).fetchone()[0]
                    maker = c.execute('SELECT manufacturer_id FROM md_qc_materials WHERE id=?',
                        (values['qc_material_id'],)).fetchone()[0]
                options = dict(lab_instrument_id=create_lab_instrument(instrument_model_id=model, display_name='另一实际仪器'),
                    reagent_id=create_reagent(manufacturer_id=maker, generic_name='另一实际试剂'),
                    method_id=create_method(method_name='另一实际检测法'),
                    unit_id=next(int(row['id']) for row in list_units().to_dict('records') if row['id'] != values['unit_id']))
                app = open_details(values)
                to_preview(app, values)
                ctx = app.session_state['project_workspace_dialog']
                identity = ctx['quick_quality_identity']
                original_levels = list(ctx['draft']['level_ids'])
                before = database_contents()
                app.button(key='quick_preview_details').click().run()
                app.selectbox(key=draft_key(app, field)).set_value(options[field]).run()
                assert_clean(app)
                self.assertEqual(original_levels, app.session_state['project_workspace_dialog']['draft']['level_ids'])
                prefix = product_prefix(app)
                confirmations = [button for button in app.button if button.key == prefix + '_confirm']
                if field != 'unit_id':
                    self.assertTrue(app.button(key='quick_details_next').disabled)
                    self.assertEqual(1, len(confirmations))
                if confirmations:
                    confirmations[0].click().run()
                app.button(key='quick_details_next').click().run()
                assert_clean(app)
                ctx = app.session_state['project_workspace_dialog']
                self.assertNotEqual(identity, ctx['quick_quality_identity'])
                self.assertFalse(app.checkbox(key=quality_prefix(app) + '_checked').value)
                self.assertTrue(app.button(key='quick_quality_next').disabled)
                self.assertEqual(original_levels, ctx['draft']['level_ids'])
                self.assertEqual(before, database_contents())

    def test_product_change_clears_only_its_lot_and_needs_new_product_and_quality_confirmation(self):
        from tests.multi_product_control_workflow_smoke_test import multi_values
        with IsolatedDatabase():
            values = multi_values()
            with get_connection() as c:
                maker = c.execute('SELECT manufacturer_id FROM md_qc_materials WHERE id=?',
                    (values['level_product_ids'][1],)).fetchone()[0]
            third = create_qc_material(manufacturer_id=maker, generic_name='更换后的第二水平产品', catalog_no='REPLACED-SKU')
            lot = create_qc_lot(qc_material_id=third, lot_no='REPLACED-FACTORY-LOT', expiry_date='2029-12-31')
            level = create_qc_level(qc_material_lot_id=lot, level_name='替换的高水平', level_order=1)
            app = open_details(values)
            to_preview(app, values)
            ctx = app.session_state['project_workspace_dialog']
            identity = ctx['quick_quality_identity']
            original_review = deepcopy(ctx['draft']['product_choice_reviews'][str(values['level_product_ids'][0])])
            before = database_contents()
            app.button(key='quick_preview_details').click().run()
            token = app.session_state['project_workspace_dialog']['token']
            slot = 'quick_control_' + token + '_1'
            app.selectbox(key=slot + '_product').set_value(third).run()
            assert_clean(app)
            draft = app.session_state['project_workspace_dialog']['draft']
            self.assertEqual([values['level_ids'][0], None], draft['level_ids'])
            self.assertEqual(original_review, draft['product_choice_reviews'][str(values['level_product_ids'][0])])
            self.assertNotIn(str(values['level_product_ids'][1]), draft['product_choice_reviews'])
            self.assertTrue(app.button(key='quick_details_next').disabled)
            app.selectbox(key=slot + '_level').set_value(level).run()
            prefix = product_prefix(app) + '_' + str(third)
            app.text_input(key=prefix + '_person').set_value('替换产品核对人')
            app.text_area(key=prefix + '_evidence').set_value('已重新核对替换货号在当前系统的适用性')
            app.run()
            app.button(key=product_prefix(app) + '_confirm').click().run()
            app.button(key='quick_details_next').click().run()
            assert_clean(app)
            ctx = app.session_state['project_workspace_dialog']
            self.assertNotEqual(identity, ctx['quick_quality_identity'])
            self.assertFalse(app.checkbox(key=quality_prefix(app) + '_checked').value)
            self.assertEqual([values['level_ids'][0], level], ctx['draft']['level_ids'])
            self.assertEqual(before, database_contents())

    def test_single_and_group_round_trip_keeps_first_product_but_resets_level_and_quality_choices(self):
        with IsolatedDatabase():
            values = metadata_values('zscore', 3)
            app = open_details(values)
            to_preview(app, values)
            before = database_contents()
            app.button(key='quick_preview_details').click().run()
            app.button(key='quick_use_group').click().run()
            app.button(key='setup_mode_change').click().run()
            app.button(key='group_use_quick').click().run()
            app.button(key='setup_mode_change').click().run()
            assert_clean(app)
            ctx = app.session_state['project_workspace_dialog']
            draft = ctx['draft']
            slot = 'quick_control_' + ctx['token'] + '_0'
            self.assertEqual(values['qc_material_id'], app.selectbox(key=slot + '_product').value)
            self.assertEqual(values['qc_material_id'], draft['qc_material_id'])
            self.assertEqual([values['qc_material_id'], None, None], draft['level_product_ids'])
            self.assertEqual([None, None, None], draft['level_ids'])
            self.assertEqual({}, draft['quality'])
            self.assertTrue(app.button(key='quick_details_next').disabled)
            self.assertEqual(before, database_contents())


if __name__ == '__main__':
    unittest.main(verbosity=2)
