"""Back up, add provenance schema, and verify original result values without guessing lots.

Stop Streamlit before running. Restoring the backup is a separate explicit operation.
"""
from __future__ import annotations
import argparse,hashlib,json,sqlite3,sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import database


def fingerprint(connection,table,columns):
    quoted=','.join('"'+column.replace('"','""')+'"' for column in columns)
    rows=connection.execute(f'SELECT {quoted} FROM "{table}" ORDER BY rowid').fetchall()
    data=json.dumps([list(row) for row in rows],ensure_ascii=False,default=lambda v:v.hex() if isinstance(v,bytes) else str(v))
    return {'count':len(rows),'sha256':hashlib.sha256(data.encode()).hexdigest()}


def migrate(path:Path,output:Path):
    path=path.expanduser().resolve()
    if not path.is_file():raise ValueError('数据库文件不存在；请先检查路径。')
    output.mkdir(parents=True,exist_ok=True)
    backup_dir=path.parent/'backups';backup_dir.mkdir(parents=True,exist_ok=True)
    backup=backup_dir/f'before-lot-lifecycle-{datetime.now():%Y%m%d-%H%M%S}.db'
    with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as source:
        with sqlite3.connect(backup) as target:source.backup(target)
        tables=[r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        columns={t:[r[1] for r in source.execute(f'PRAGMA table_info("{t}")')] for t in tables}
        before={t:fingerprint(source,t,columns[t]) for t in tables}
        initialization_fields={'md_sources':{'updated_at'},'md_test_items':{'updated_at'},'md_source_records':{'updated_at','last_seen_at'}}
        comparison_columns={t:[col for col in columns[t] if col not in initialization_fields.get(t,set())] for t in tables}
        before_content={t:fingerprint(source,t,comparison_columns[t]) for t in tables}
    database.DB_PATH=path;database.LEGACY_DB_CANDIDATES=[]
    database.init_db()
    from services.workbench_config_service import sync_lj_workbench_bindings
    from services.zscore_workbench_service import sync_zscore_workbench_bindings
    from services.instant_workbench_service import sync_instant_workbench_bindings
    from services.lot_lifecycle_service import backfill_result_contexts
    with database.atomic_write() as c:
        # Existing run source snapshots are frozen before per-result provenance is backfilled.
        sync_lj_workbench_bindings();zs_issues=sync_zscore_workbench_bindings();instant_issues=sync_instant_workbench_bindings()
        backfilled=backfill_result_contexts()
        integrity=c.execute('PRAGMA integrity_check').fetchone()[0]
        foreign_keys=[list(row) for row in c.execute('PRAGMA foreign_key_check')]
        after={t:fingerprint(c,t,columns[t]) for t in tables}
        protected=[t for t in tables if t in ('results','zscore_runs','zscore_level_results','instant_results','report_exports') or t.startswith('md_') or t.startswith('lab_') or t.startswith('qc_project_') or t.startswith('qc_lot_config')]
        changed=[t for t in protected if before_content[t]!=fingerprint(c,t,comparison_columns[t])]
        if integrity!='ok' or foreign_keys or changed:
            raise ValueError(f'迁移核对未通过，已保留备份：{backup}；外键 {foreign_keys}；原始资料变化 {changed}')
        provenance=[dict(row) for row in c.execute('SELECT provenance,COUNT(*) AS count FROM qc_result_contexts GROUP BY provenance')]
    report={'database':str(path),'backup':str(backup),'integrity_check':integrity,'foreign_key_check':foreign_keys,'backfilled_contexts':backfilled,
        'before':before,'after_original_columns':after,'original_result_values_unchanged':True,'protected_tables_verified':protected,
        'ignored_initialization_columns':{t:sorted(v) for t,v in initialization_fields.items()},'provenance':provenance,'configuration_issues':{'zscore':zs_issues,'instant':instant_issues}}
    (output/'migration-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:report[key] for key in ('database','backup','integrity_check','foreign_key_check','backfilled_contexts','original_result_values_unchanged','provenance')},ensure_ascii=False,indent=2))
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=database.get_db_path())
    parser.add_argument('--output',type=Path,default=ROOT/'output'/'lot-lifecycle-migration')
    args=parser.parse_args();migrate(args.database,args.output)
