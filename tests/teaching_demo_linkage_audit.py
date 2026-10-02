"""Read a prepared application's saved identities in one read-only transaction."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
import argparse
import json
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(database_path, output):
    import database as db
    from services.daily_overview_service import get_daily_overview
    from services.batch_monthly_report_service import preview_monthly_reports
    source = Path(database_path).resolve()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    db.DB_PATH = source
    db.LEGACY_DB_CANDIDATES = []
    def digest(connection):
        return sha256('\n'.join(connection.iterdump()).encode()).hexdigest()
    with db.read_snapshot() as connection:
        before = digest(connection)
        assert connection.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        assert not connection.execute('PRAGMA foreign_key_check').fetchall()
        contexts = [dict(r) for r in connection.execute('SELECT * FROM qc_result_contexts ORDER BY id')]
        method_counts, scale_counts, level_counts = Counter(), Counter(), Counter()
        references = set()
        for context in contexts:
            snapshot = json.loads(context['config_snapshot_json'])
            refs = [(method, table, context[column]) for method, table, column in (
                ('lj', 'results', 'lj_result_id'), ('zscore', 'zscore_runs', 'zscore_run_id'),
                ('instant', 'instant_results', 'instant_result_id')) if context[column] is not None]
            assert len(refs) == 1, context['id']
            method, table, result_id = refs[0]
            references.add((method, result_id))
            result = dict(connection.execute(f'SELECT * FROM {table} WHERE id=?', (result_id,)).fetchone())
            identity = snapshot['identity']
            if context['provenance'] == 'instant_transfer':
                original = connection.execute('SELECT config_snapshot_json FROM qc_result_contexts WHERE id=?', (context['source_context_id'],)).fetchone()
                assert original and original['config_snapshot_json'] == context['config_snapshot_json']
                assert method == 'lj' and snapshot['runtime_method'] == 'instant'
            else:
                assert snapshot['runtime_method'] == method
                assert snapshot['runtime_batch_id'] == result['batch_id']
            test_item = connection.execute('SELECT chinese_name FROM md_test_items WHERE id=?', (snapshot.get('test_item_id', identity[3]),)).fetchone()
            assert test_item[0] == snapshot['test_item_name']
            reagent = connection.execute('SELECT reagent_id,lot_no FROM md_reagent_lots WHERE id=?', (context['reagent_lot_id'],)).fetchone()
            assert reagent and reagent['reagent_id'] == snapshot.get('reagent_id', identity[7]) and reagent['lot_no'] == context['reagent_lot_no']
            levels = [dict(r) for r in connection.execute('SELECT * FROM qc_result_context_levels WHERE context_id=? ORDER BY level_order', (context['id'],))]
            assert len(levels) == len(snapshot['levels'])
            for frozen, configured in zip(levels, snapshot['levels']):
                assert frozen['qc_level_id'] == configured['qc_level_id']
                assert frozen['lot_no'] == configured['lot_no']
                actual = connection.execute('''SELECT l.qc_material_lot_id,q.qc_material_id,q.lot_no
                    FROM md_qc_levels l JOIN md_qc_material_lots q ON q.id=l.qc_material_lot_id WHERE l.id=?''',
                    (frozen['qc_level_id'],)).fetchone()
                assert actual['qc_material_lot_id'] == frozen['qc_lot_id']
                assert actual['qc_material_id'] == snapshot.get('qc_material_id', identity[1])
                assert actual['lot_no'] == frozen['lot_no']
            if method == 'zscore':
                assert connection.execute('SELECT COUNT(*) FROM zscore_level_results WHERE run_id=?', (result_id,)).fetchone()[0] == len(levels)
            assert connection.execute('SELECT 1 FROM qc_result_evaluations WHERE context_id=?', (context['id'],)).fetchone()
            method_counts[method] += 1
            scale_counts[snapshot['input_value_type']] += 1
            level_counts[f'{method}:{len(levels)}'] += 1
        for method, table in [('lj', 'results'), ('zscore', 'zscore_runs'), ('instant', 'instant_results')]:
            assert {(method, r['id']) for r in connection.execute('SELECT id FROM ' + table)} <= references
        templates = [dict(r) for r in connection.execute('SELECT id,template_name FROM qc_project_templates ORDER BY id')]
        assert all(not any(text in r['template_name'] for text in ('工程', 'V11', 'DAILY')) for r in templates)
        overview = get_daily_overview('2026-09-28')
        raw_day_count = sum(connection.execute(f"SELECT COUNT(*) FROM {table} WHERE substr(test_time,1,10)='2026-09-28'",).fetchone()[0]
                            for table in ('results', 'zscore_runs', 'instant_results'))
        # A transferred instant batch remains a historical source. Other active bindings
        # are required to agree with the saved source records shown in the overview.
        overview_refs = {(r['qc_method'], r['runtime_batch_id']): r for r in overview['items']}
        for (method, batch), item in overview_refs.items():
            if batch is None:
                continue
            table = {'lj': 'results', 'zscore': 'zscore_runs', 'instant': 'instant_results'}[method]
            column = {'lj': 'lj_result_id', 'zscore': 'zscore_run_id', 'instant': 'instant_result_id'}[method]
            expected = connection.execute(f'''SELECT COUNT(*) FROM {table} r
                JOIN qc_result_contexts x ON x.{column}=r.id
                WHERE r.batch_id=? AND substr(r.test_time,1,10)='2026-09-28'
                AND x.provenance<>'instant_transfer' ''', (batch,)).fetchone()[0]
            assert expected == item['count']
            binding = connection.execute('''SELECT d.chinese_name,t.template_name FROM qc_lot_config_items i
                JOIN md_test_items d ON d.id=i.test_item_id JOIN qc_lot_configs c ON c.id=i.lot_config_id
                JOIN qc_project_templates t ON t.id=c.template_id WHERE i.id=?''', (item['lot_config_item_id'],)).fetchone()
            assert item['test_item_name'] == binding['chinese_name']
            assert item['template_name'] == binding['template_name']
        preview = preview_monthly_reports('2026-09')
        for report in preview['items']:
            key = (report['qc_method'], report['batch_id'])
            if key in overview_refs:
                assert report['template_name'] == overview_refs[key]['template_name']
                assert report['instrument_name'] == overview_refs[key]['instrument_name']
        assert digest(connection) == before, 'Read-only aggregation must not alter saved evidence.'
        result = dict(passed=True, source_database=str(source), inspection_mode='read-only transaction; no database copy',
                      project_count=len(templates), projects=templates, result_count=len(contexts),
                      methods=dict(method_counts), scales=dict(scale_counts), complete_level_counts=dict(level_counts),
                      overview_day_count=overview['count'], all_saved_day_count=raw_day_count,
                      overview_items=len(overview['items']), monthly_eligible_count=len(preview['items']),
                      monthly_exclusions=[dict(qc_method=r['qc_method'], project_name=r['project_name'], reason=r['reason']) for r in preview['exclusions']],
                      source_and_material_identity_exact=True, all_saved_records_have_context_and_evaluation=True,
                      overview_and_report_names_match=True, read_only_sha256=before)
        (output / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    run(args.database, args.output)
