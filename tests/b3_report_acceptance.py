"""Create fresh routine monthly acceptance artifacts through public services."""
from pathlib import Path
import sys,json,subprocess,hashlib
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import database
from services import batch_monthly_report_service as svc
from services.report_service import get_report_history_record
from tests.batch_monthly_report_smoke_test import seed_mix,seed_versioned_quality
from tests.report_pdf_assertions import assert_uniform_a4_pages_without_watermark


def run(output):
    output.mkdir(parents=True,exist_ok=True)
    assert not (output/'acceptance.db').exists(), 'Use a new isolated output path.'
    database.DATA_DIR=output
    database.DEFAULT_DB_PATH=output/'acceptance.db'
    database.DB_PATH=database.DEFAULT_DB_PATH
    database.STORAGE_CONFIG_PATH=output/'storage_config.json'
    database.LEGACY_DB_CANDIDATES=[]
    database.init_db()
    seed_mix()
    version_batch=seed_versioned_quality()
    pdfdir=output/'pdf';pdfdir.mkdir()
    renders=output/'renders';renders.mkdir()
    results=[]
    for month in ('2026-04','2026-09'):
        preview=svc.preview_monthly_reports(month)
        selected=[r for r in preview['items'] if month=='2026-04' or r['batch_id']==version_batch]
        job=svc.create_monthly_report_job(preview,[r['unit_key'] for r in selected],month+'-acceptance')
        for item in svc.get_monthly_report_job(job)['items']:
            state=svc.run_monthly_report_item(job,item['id'])
            assert state['status']=='succeeded',state
            original=svc.read_monthly_report_item(job,item['id'])['pdf_bytes']
            reader=assert_uniform_a4_pages_without_watermark(original)
            summary=get_report_history_record(state['export_id']).summary_json
            path=pdfdir/f"{item['qc_method']}-{item['batch_id']}-{month}.pdf"
            path.write_bytes(original)
            for number,page in enumerate(reader.pages,1):
                assert f'第 {number}/{len(reader.pages)} 页' in page.extract_text()
            subprocess.run(['pdftoppm','-r','110','-png',str(path),str(renders/path.stem)],check=True)
            results.append(dict(path=str(path.relative_to(output)),pages=len(reader.pages),sha256=hashlib.sha256(original).hexdigest(),
                statistics=summary['statistics'],quality_summary=summary['quality_summary'],source_version=summary['source_version']))
        (output/f'{month}-reports.zip').write_bytes(svc.build_monthly_report_zip(job))
        (output/f'{month}-summary.json').write_text(json.dumps(svc.monthly_job_summary(job),ensure_ascii=False,indent=2))
    (output/'result.json').write_text(json.dumps(dict(reports=results,pages=sum(r['pages'] for r in results),
        evidence='Fresh database; real APIs and renderers; no function or calculation patches.'),ensure_ascii=False,indent=2))
    print(json.dumps(dict(reports=len(results),pages=sum(r['pages'] for r in results),output=str(output)),ensure_ascii=False))


if __name__=='__main__':
    run(Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'output/execution/B3/reports-01')
