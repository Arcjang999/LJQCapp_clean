"""Readonly aggregation and identity-preserving draft/file behavior on real saved fixtures."""
from pathlib import Path
import io,json,sys,tempfile,unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tests.b1_business_chain import seed
from services.daily_overview_service import get_daily_overview
from services.daily_context_service import get_daily_context
from services.daily_draft_service import new_draft,draft_rows,parse_rectangular_paste,apply_paste,build_request
from services.daily_result_io_service import export_daily_workbook,preview_daily_workbook,apply_daily_workbook
from services.export_utils import dataframes_to_xlsx_bytes,xlsx_bytes_to_dataframes
import database


def database_state():
    with database.read_snapshot() as c:
        return {r[0]:[tuple(v) for v in c.execute('SELECT * FROM "'+r[0]+'"')] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


class DailyOverviewDraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.fixture=seed(Path(cls.temp.name)/'seed')

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def draft(self):
        with database.read_snapshot() as c:
            config=dict(c.execute('SELECT * FROM qc_lot_configs ORDER BY id LIMIT 1').fetchone())
            lot=c.execute('SELECT l.qc_material_lot_id FROM qc_lot_config_item_levels a JOIN md_qc_levels l ON l.id=a.qc_level_id JOIN qc_lot_config_items i ON i.id=a.lot_config_item_id WHERE i.lot_config_id=?',(config['id'],)).fetchone()[0]
        ctx=get_daily_context(config['lab_instrument_id'],config['qc_material_id'],lot,'2026-09-28 12:00:00',lot_config_id=config['id'])
        return new_draft(ctx,operator='日常录入验收员')

    def test_counts_saved_evidence_and_readonly(self):
        before=database_state();r=get_daily_overview('2026-09-28')
        self.assertEqual(12,r['count']);self.assertEqual(3,len(r['items']))
        self.assertTrue(all(i['ever_reject'] and i['count']==4 for i in r['items']))
        self.assertTrue(all(i['latest']['conclusion']=='警告' for i in r['items']))
        self.assertEqual([4,4,4],[i['latest']['source_id'] if i['qc_method']=='lj' else i['count'] for i in r['items']])
        self.assertIsNone(r['missing_rate']);self.assertIsNone(r['expected_count'])
        tomorrow=get_daily_overview('2026-09-29');self.assertEqual(0,tomorrow['count']);self.assertEqual(4,len(tomorrow['pending']['cross_day']))
        self.assertEqual(before,database_state())
        print('overview elapsed ms',r['elapsed_ms'])

    def test_filter_and_quality_failure_not_green(self):
        with patch('services.daily_overview_service.batch_quality_summary',side_effect=RuntimeError('injected')):
            r=get_daily_overview('2026-09-28',qc_method='zscore')
        self.assertEqual(2,len(r['items']));self.assertTrue(all(i['quality'].get('error') for i in r['items']))
        self.assertEqual(0,len(get_daily_overview('2026-09-28',lab_instrument_id=999999)['items']))

    def test_paste_preserves_text_and_rejects_conflicts(self):
        d=self.draft();rows=draft_rows(d);preview=parse_rectangular_paste('1.0e1\t人工核对',rows)
        self.assertTrue(preview['valid']);apply_paste(d,preview);self.assertEqual('1.0e1',d['values'][rows[0]['key']])
        for text in ('','bad','nan','1\t备注\t多列','1\n2'):
            p=parse_rectangular_paste(text,rows);self.assertFalse(p['valid']);self.assertEqual('1.0e1',d['values'][rows[0]['key']])
        self.assertEqual('1.0e1',build_request(d)['items'][0]['levels'][0]['value'])
        self.assertEqual(d['context']['test_time'],d['test_time'])

    def test_workbook_identity_preview_only_and_error_lines(self):
        d=self.draft();k=draft_rows(d)[0]['key'];d['values'][k]='10.3'
        before=database_state();data=export_daily_workbook(d);p=preview_daily_workbook(data,d)
        self.assertTrue(p['valid'],p);self.assertEqual('10.3',p['rows'][0]['value']);self.assertEqual(before,database_state())
        workbook=xlsx_bytes_to_dataframes(data);workbook['常规质控结果'].iloc[0,3]='错误批号';out=io.BytesIO(dataframes_to_xlsx_bytes(workbook))
        p=preview_daily_workbook(out.getvalue(),d);self.assertFalse(p['valid']);self.assertIn('第2行',';'.join(p['errors']))
        with self.assertRaises(ValueError):apply_daily_workbook(d,p)
        self.assertEqual('10.3',d['values'][k]);self.assertEqual(before,database_state())


if __name__=='__main__':unittest.main(verbosity=2)
