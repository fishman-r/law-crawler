"""将已核验的缺原件诊断并入OSS元数据；保留no_original，不增加下载完成数。"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from .file_lock import FileLock
from .oss_sink import OSSSink


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.replace(path)


def import_diagnoses(batch, patches_path, manifest_path, manifest_sha256, sink,
                     run_id, workers=5):
    """持同一批次锁；元数据OSS回读成功后才更新本地outcome与reason。

    patches每行含target_id/source_record_sha256/metadata_patch；只接受
    no_original_reason与source_availability，拒绝覆盖文件、身份和任务状态。
    """
    batch=Path(batch)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', run_id) or not 1<=workers<=5:
        raise ValueError('Invalid diagnosis run ID or concurrency')
    manifest_raw=Path(manifest_path).read_bytes()
    if _sha(manifest_raw)!=manifest_sha256:raise ValueError('Frozen manifest SHA mismatch')
    manifest=json.loads(manifest_raw)
    jobs=manifest.get('jobs',[])
    ids=[r[0] for r in jobs if isinstance(r,list) and r]
    if (manifest.get('batch_id')!=batch.name or manifest.get('count')!=len(jobs) or len(ids)!=len(jobs)
            or len(set(ids))!=len(ids) or any(not re.fullmatch('[0-9a-f]{64}',str(k)) for k in ids)):
        raise ValueError('Frozen target manifest scope mismatch')
    allowed=set(ids);raw=Path(patches_path).read_bytes();patch_sha=_sha(raw)
    patches=[json.loads(line) for line in raw.splitlines() if line.strip()]
    seen=set()
    for patch in patches:
        key=patch.get('target_id');meta=patch.get('metadata_patch')
        if key not in allowed or key in seen:raise ValueError('Duplicate or out-of-scope diagnosis target')
        seen.add(key)
        if not re.fullmatch('[0-9a-f]{64}',str(patch.get('source_record_sha256'))):raise ValueError('Record SHA required')
        if (not isinstance(meta,dict) or set(meta)!={'no_original_reason','source_availability'}
                or not isinstance(meta['no_original_reason'],str) or not meta['no_original_reason'].strip()
                or not isinstance(meta['source_availability'],dict)):
            raise ValueError('Diagnosis patch must contain only reason and source availability')
    report_dir=batch/'recovery'/run_id
    results=[]
    with FileLock(batch/'.lock'):
        report_dir.mkdir(parents=True,exist_ok=True)
        plan={'batch_id':batch.name,'run_id':run_id,'manifest_sha256':manifest_sha256,
              'patches_sha256':patch_sha,'scope_count':len(allowed),'patch_count':len(patches),'workers':workers}
        plan_path=report_dir/'diagnosis-plan.json'
        if plan_path.exists() and json.loads(plan_path.read_text())!=plan:
            raise ValueError('Diagnosis run ID already bound to different plan')
        _atomic(plan_path,plan)
        def process(patch):
            key=patch['target_id'];db=sqlite3.connect(batch/'queue.sqlite3',timeout=30)
            try:
                row=db.execute('SELECT status,record FROM jobs WHERE id=?',(key,)).fetchone()
                if not row or row[0]!='no_original':return {'target_id':key,'status':'skipped_status_changed'}
                if _sha(row[1].encode())!=patch['source_record_sha256']:raise ValueError('Record SHA mismatch')
                path=batch/'outcomes'/(key+'.json');before=path.read_bytes();outcome=json.loads(before)
                if outcome.get('status')!='no_original':raise ValueError('Outcome status mismatch')
                if outcome.get('diagnosis_import',{}).get('patches_sha256')==patch_sha:
                    return {'target_id':key,'status':'already_applied'}
                history=report_dir/'outcomes-before'/path.name
                history.parent.mkdir(exist_ok=True)
                if not history.exists():history.write_bytes(before)
                new=deepcopy(outcome);new.pop('oss_metadata',None)
                new.setdefault('metadata',{}).update(patch['metadata_patch'])
                new['reason']=patch['metadata_patch']['no_original_reason']
                stamp=datetime.now(timezone.utc).isoformat()
                new['diagnosis_import']={'run_id':run_id,'patches_sha256':patch_sha,
                    'previous_outcome_sha256':_sha(before),'source_record_sha256':patch['source_record_sha256'],
                    'manifest_sha256':manifest_sha256,'updated_at':stamp,
                    'interpretation':'source availability diagnosis only; no original file recovered'}
                uploaded=sink.put_metadata(key,new,batch_id=batch.name)
                if uploaded.get('status') not in ('uploaded_verified','reused_verified'):
                    return {'target_id':key,'status':'metadata_upload_failed'}
                new['oss_metadata']=uploaded
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    changed=db.execute('UPDATE jobs SET reason=?,updated_at=? WHERE id=? AND status=? AND record=?',
                        (new['reason'],stamp,key,'no_original',row[1]))
                    if changed.rowcount!=1:raise ValueError('Target changed during commit')
                    _atomic(path,new)
                return {'target_id':key,'status':'diagnosis_saved','download_status':'no_original'}
            except Exception as exc:
                return {'target_id':key,'status':'failed','error_type':type(exc).__name__}
            finally:db.close()
        try:
            todo=iter(patches)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                pending=set()
                while True:
                    while len(pending)<workers and not getattr(sink,'auth_failure',None):
                        patch=next(todo,None)
                        if patch is None:break
                        pending.add(pool.submit(process,patch))
                    if not pending:break
                    done,pending=wait(pending,return_when=FIRST_COMPLETED)
                    results.extend(f.result() for f in done)
                    _atomic(report_dir/'diagnosis-results.json',results)
        finally:sink.flush_cache()
        report={**plan,'updated_at':datetime.now(timezone.utc).isoformat(),
                'counts':dict(Counter(r['status'] for r in results)),
                'not_attempted':len(patches)-len(results),'download_completed_added':0,
                'auth_failure':bool(getattr(sink,'auth_failure',None)),'results':results}
        _atomic(report_dir/'diagnosis-report.json',report)
        return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('batch','patches','manifest','manifest-sha256','credentials','run-id'):p.add_argument('--'+name,required=True)
    p.add_argument('--workers',type=int,default=5);args=p.parse_args()
    batch=Path(args.batch);plan=json.loads((batch/'plan.json').read_text())
    sink=OSSSink(credentials=args.credentials,cache_path=batch/'oss-cache.json',
        **{k:plan[k] for k in ('bucket','endpoint','region','prefix')})
    report=import_diagnoses(batch,args.patches,args.manifest,args.manifest_sha256,sink,args.run_id,args.workers)
    print(json.dumps({'counts':report['counts'],'not_attempted':report['not_attempted'],
        'download_completed_added':0},ensure_ascii=False))


if __name__=='__main__':main()
