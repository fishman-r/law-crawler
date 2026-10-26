"""Frozen, append-only recovery of explicitly missing attachments on incomplete jobs.

No original download is performed here. Official attachment receipts must already
be archived and independently reviewed. Execute verifies OSS bytes before commit.
"""
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid
from .file_lock import FileLock
from .recovery_attachments import validate_attachment_schema, verified_attachments
from .recovery_audit import _ReadOnlyAttachmentSink, _read_object


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode()


def now():
    return datetime.now(timezone.utc).isoformat()


def write_new(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as f:
            f.write(raw); f.flush(); os.fsync(f.fileno())
        os.link(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic(path, raw):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    write_new(temporary, raw)
    os.replace(temporary, path)


def checked(path, digest):
    raw = Path(path).read_bytes()
    if sha(raw) != digest:
        raise ValueError('Frozen evidence changed: ' + str(path))
    return raw


def ident(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('Invalid target/source SHA identity')
    return value


def outcome_path(batch, target):
    return batch / 'outcomes' / (ident(target) + '.json')


def primary(before):
    metadata = before.get('metadata', {})
    if before.get('status') != 'incomplete' or before.get('recovery', {}).get('evidence', {}).get('primary_text_identity_verified') is not True:
        raise ValueError('Only incomplete jobs with explicit verified primary identity are eligible')
    missing = metadata.get('known_missing_attachments')
    if not isinstance(missing, list) or not missing or any(not isinstance(s, str) or not s.strip() for s in missing) or len(set(missing)) != len(missing):
        raise ValueError('Explicit unique known missing attachment descriptions required')
    files = before.get('files', [])
    if not files or len({f['sha256'] for f in files}) != len(files):
        raise ValueError('Existing file list empty or ambiguous')
    roles = metadata.get('file_roles', {})
    bodies = [f for f in files if roles.get(f.get('url'), {}).get('role') == 'regulation_body']
    if not bodies:
        raise ValueError('Verified primary role missing')
    return bodies, missing


def validate_review_item(item, before):
    target = ident(item['target_id']); source = ident(item['source_id'])
    if target == source:
        raise ValueError('Attachment source must be a separately archived official source')
    bodies, missing = primary(before)
    if set(item.get('approved_primary_sha256s', [])) != {f['sha256'] for f in bodies}:
        raise ValueError('Approved primary differs from retained primary files')
    validate_attachment_schema(item)
    additions = item.get('additional_verified_attachments', [])
    if not additions:
        raise ValueError('No approved attachments')
    old_shas = {f['sha256'] for f in before['files']}
    old_urls = {f['url'] for f in before['files']}
    new_shas = {a['receipt']['sha256'] for a in additions}
    old_attachment_shas={f['sha256'] for f in before['files'] if before['metadata']['file_roles'].get(f['url'],{}).get('role')=='attachment'}
    if old_shas & new_shas or any(a['receipt']['url'] in old_urls for a in additions):
        raise ValueError('Existing successful file cannot be appended again')
    resolutions = item.get('resolved_missing_attachments')
    if not isinstance(resolutions, list) or not resolutions:
        raise ValueError('Explicit missing-description to attachment mapping required')
    descriptions = []
    used = set()
    for resolution in resolutions:
        description = resolution.get('description')
        hashes = resolution.get('attachment_sha256s')
        if description not in missing or description in descriptions or not isinstance(hashes, list) or not hashes or len(set(hashes)) != len(hashes) or not set(hashes) <= (new_shas | old_attachment_shas) or not set(hashes) & new_shas:
            raise ValueError('Invalid resolved missing attachment mapping')
        if type(resolution.get('fully_resolved')) is not bool or not isinstance(resolution.get('coverage_note'),str) or not resolution['coverage_note'].strip():
            raise ValueError('Explicit complete/partial coverage and evidence note required')
        descriptions.append(description); used.update(hashes)
    if used & new_shas != new_shas:
        raise ValueError('Every appended attachment must resolve an explicit known gap')
    # Independent attachment reports explicitly approve each gap, not merely the file role.
    for a in additions:
        report = json.loads(checked(a['evidence_report_path'], a['evidence_report_sha256']))
        matching = [x for x in report.get('items', []) if x.get('target_id') == target and x.get('parent_source_id') == source and x.get('original_sha256') == a['receipt']['sha256']]
        required = [{k:r[k] for k in ('description','fully_resolved','coverage_note','attachment_sha256s')} for r in resolutions if a['receipt']['sha256'] in r['attachment_sha256s']]
        if len(matching) != 1 or matching[0].get('missing_attachment_coverage') != required:
            raise ValueError('Independent evidence does not approve the missing-description mapping')
    resolved={r['description'] for r in resolutions if r['fully_resolved']}
    return bodies, [m for m in missing if m not in resolved]


def freeze(batch, review_path, review_sha256, output, run_id):
    batch = Path(batch).resolve(); output = Path(output).resolve()
    if not re.fullmatch('[A-Za-z0-9_-]+', run_id):
        raise ValueError('Invalid run ID')
    if output.exists():
        raise ValueError('Plan output already exists')
    review_path = Path(review_path).resolve()
    review = json.loads(checked(review_path, review_sha256))
    if not review.get('reviewer') or not review.get('review_note') or not isinstance(review.get('repairs'), list) or not review['repairs']:
        raise ValueError('Independent reviewer, note and repairs required')
    plan = dict(schema_version=1, mode='incomplete_attachment_recovery', batch=str(batch), batch_id=batch.name,
                run_id=run_id, created_at=now(), review_path=str(review_path), review_sha256=review_sha256, repairs=[])
    seen = set()
    with FileLock(batch / '.lock'), sqlite3.connect(f'file:{batch / "queue.sqlite3"}?mode=ro', uri=True) as db:
        for item in review['repairs']:
            t, s = ident(item['target_id']), ident(item['source_id'])
            if t in seen:
                raise ValueError('Duplicate target')
            seen.add(t)
            target_raw = outcome_path(batch,t).read_bytes(); source_raw = outcome_path(batch,s).read_bytes()
            target, source = json.loads(target_raw), json.loads(source_raw)
            validate_review_item(item, target)
            tr = db.execute('SELECT status,record FROM jobs WHERE id=?',(t,)).fetchone()
            sr = db.execute('SELECT status,record FROM jobs WHERE id=?',(s,)).fetchone()
            if not tr or tr[0] != 'incomplete' or not sr or sr[0] != 'completed' or source.get('status') != 'completed':
                raise ValueError('Target/source queue status not eligible')
            plan['repairs'].append(dict(item=deepcopy(item), target_before=target, target_before_bytes=target_raw.decode(),
                target_outcome_sha256=sha(target_raw), source_outcome_sha256=sha(source_raw),
                target_record_sha256=sha(tr[1].encode()), source_record_sha256=sha(sr[1].encode())))
        raw = encoded(plan); write_new(output, raw); write_new(str(output)+'.sha256', (sha(raw)+'\n').encode())
    return plan


def load_plan(batch, path, digest):
    plan = json.loads(checked(path,digest))
    if plan.get('mode') != 'incomplete_attachment_recovery' or plan['batch'] != str(Path(batch).resolve()):
        raise ValueError('Plan mode or batch mismatch')
    if not re.fullmatch('[A-Za-z0-9_-]+',plan.get('run_id','')):
        raise ValueError('Invalid run ID')
    review = json.loads(checked(plan['review_path'],plan['review_sha256']))
    if [p['item'] for p in plan['repairs']] != review['repairs']:
        raise ValueError('Plan differs from independent review')
    for entry in plan['repairs']:
        if sha(entry['target_before_bytes'].encode()) != entry['target_outcome_sha256'] or json.loads(entry['target_before_bytes']) != entry['target_before']:
            raise ValueError('Original snapshot mismatch')
        validate_review_item(entry['item'],entry['target_before'])
    return plan


def verify_files(files, sink):
    for f in files:
        if f.get('oss',{}).get('status') not in ('uploaded_verified','reused_verified'):
            raise ValueError('Unverified existing file receipt')
        key=f['oss']['raw_key']
        v=sink.verify_original(dict(sha256=f['sha256'],size_bytes=f['size_bytes'],oss_raw_key=key))
        if v.get('status') != 'reused_verified' or v.get('raw_key') != key or v.get('sha256') != f['sha256'] or v.get('size_bytes') != f['size_bytes']:
            raise ValueError('OSS original verification failed')


def evidence(entry, batch, db, sink):
    item=entry['item']; t,s=item['target_id'],item['source_id']
    for target, expected in ((t,entry['target_record_sha256']),(s,entry['source_record_sha256'])):
        row=db.execute('SELECT record FROM jobs WHERE id=?',(target,)).fetchone()
        if not row or sha(row[0].encode()) != expected:
            raise ValueError('Queue record changed')
    source=json.loads(checked(outcome_path(batch,s),entry['source_outcome_sha256']))
    if db.execute('SELECT status FROM jobs WHERE id=?',(s,)).fetchone() != ('completed',):
        raise ValueError('Source status changed')
    before=entry['target_before']; bodies,remaining=validate_review_item(item,before)
    reader=_ReadOnlyAttachmentSink(sink)
    verify_files(before['files'],reader)
    excluded_path=batch/'recovery'/'invalid-source-files.json'
    excluded=json.loads(excluded_path.read_text()) if excluded_path.exists() else []
    bad={x['sha256'] for x in excluded if x.get('source_id') in (s,t)}
    if bad & {x['sha256'] for x in before['files']}:
        raise ValueError('Existing file has been excluded')
    additions,roles=verified_attachments(item,source,bodies,reader,bad)
    for f in before['files']+additions:
        row=db.execute('SELECT bytes,key FROM objects WHERE sha=?',(f['sha256'],)).fetchone()
        if row is not None and row!=(f['size_bytes'],f['oss']['raw_key']):
            raise ValueError('Existing object registry conflicts with verified original')
    return additions,roles,remaining


def build_after(entry, plan, additions, roles, remaining, timestamp):
    after=deepcopy(entry['target_before']); after.pop('oss_metadata',None)
    after['files'].extend(additions)
    after['metadata']['file_roles'].update(roles)
    after['metadata']['known_missing_attachments']=remaining
    # Resolving a known list does not certify every possible annex was discovered.
    after['metadata']['known_attachment_gaps_resolved']=not remaining
    after['status']='incomplete' if remaining else 'completed'
    after['reason']='附件补收入库；仍缺：'+'；'.join(remaining) if remaining else '已补齐已确认缺失附件；正文保持不变，附件范围以审核证据为限'
    after['finished_at']=timestamp
    after.setdefault('attachment_recovery_history',[]).append(dict(run_id=plan['run_id'],
        previous_outcome_sha256=entry['target_outcome_sha256'],review_sha256=plan['review_sha256'],
        resolved_missing_attachments=deepcopy(entry['item']['resolved_missing_attachments']),
        remaining_missing_attachments=remaining,appended_sha256s=[a['sha256'] for a in additions],at=timestamp))
    return after


def verify_detail(outcome, target, batch_id, sink):
    receipt=outcome.get('oss_metadata',{})
    if receipt.get('status') not in ('uploaded_verified','reused_verified'):
        raise ValueError('Detail metadata upload unverified')
    key=receipt['metadata_key']
    if not key.startswith(sink.prefix+'/metadata/') or '..' in key.split('/') or Path(key).stem!=receipt['sha256']:
        raise ValueError('Invalid detail metadata key')
    remote=json.loads(_read_object(sink,key,receipt['sha256'],receipt['size_bytes'],metadata=True))
    expected=deepcopy(outcome);expected.pop('oss_metadata',None)
    if remote.get('document') != expected or remote.get('detail_key') != target or remote.get('batch_id') != batch_id:
        raise ValueError('Actual OSS detail metadata differs from expected outcome')


def failure_reason(exc):
    # SDK exceptions can contain signed URLs or request credentials; never echo them.
    if isinstance(exc, (ValueError, KeyError, TypeError, OSError)):
        return str(exc)
    return type(exc).__name__ + ': external operation failed; inspect protected server diagnostics'


def execute(batch, plan_path, plan_sha256, sink, results_output):
    batch=Path(batch).resolve(); results_output=Path(results_output)
    if results_output.exists():raise ValueError('Use a new attempt results output')
    results=[]
    with FileLock(batch/'.lock'), sqlite3.connect(batch/'queue.sqlite3') as db:
        plan=load_plan(batch,plan_path,plan_sha256)
        work=batch/'recovery'/'attachments'/plan['run_id']
        work.mkdir(parents=True,exist_ok=True)
        binding=work/'plan.sha256'
        if binding.exists():checked(binding,sha((plan_sha256+'\n').encode()))
        else:write_new(binding,(plan_sha256+'\n').encode())
        for entry in plan['repairs']:
            t=entry['item']['target_id']; journal=work/(t+'.prepared.json')
            try:
                additions,roles,remaining=evidence(entry,batch,db,sink)
                p=outcome_path(batch,t); current=p.read_bytes()
                if journal.exists():
                    prepared=json.loads(journal.read_bytes()); timestamp=prepared['finished_at']
                    expected=build_after(entry,plan,additions,roles,remaining,timestamp)
                    without=deepcopy(prepared);without.pop('oss_metadata',None)
                    if expected != without:raise ValueError('Prepared outcome changed')
                    verify_detail(prepared,t,batch.name,sink)
                else:
                    if sha(current) != entry['target_outcome_sha256'] or db.execute('SELECT status FROM jobs WHERE id=?',(t,)).fetchone()!=('incomplete',):
                        raise ValueError('Target changed before execution')
                    prepared=build_after(entry,plan,additions,roles,remaining,now())
                    uploaded=sink.put_metadata(t,prepared,batch_id=batch.name)
                    prepared['oss_metadata']=uploaded
                    verify_detail(prepared,t,batch.name,sink)
                    write_new(journal,encoded(prepared))
                if current not in (entry['target_before_bytes'].encode(),encoded(prepared)):
                    raise ValueError('Target changed since frozen plan')
                row=db.execute('SELECT status FROM jobs WHERE id=?',(t,)).fetchone()
                if row not in [('incomplete',),(prepared['status'],)]:raise ValueError('Target queue status changed')
                # Durable prepared journal recovers file-write/DB-commit interruption.
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    db.execute('UPDATE jobs SET status=?,reason=?,files=?,bytes=?,updated_at=? WHERE id=?',
                        (prepared['status'],prepared['reason'],len(prepared['files']),sum(f['size_bytes'] for f in prepared['files']),prepared['finished_at'],t))
                    for f in prepared['files']:
                        db.execute('INSERT OR IGNORE INTO objects VALUES(?,?,?)',(f['sha256'],f['size_bytes'],f['oss']['raw_key']))
                    atomic(p,encoded(prepared))
                results.append(dict(target_id=t,status=prepared['status'],remaining=remaining,appended=len(additions)))
            except Exception as exc:
                results.append(dict(target_id=t,status='failed',reason=failure_reason(exc)))
        sink.flush_cache()
        report=dict(plan_sha256=plan_sha256,finished_at=now(),items=results)
        write_new(results_output,encoded(report))
    return report


def audit(batch,plan_path,plan_sha256,sink,output):
    batch=Path(batch).resolve(); findings=[]
    # Same lock guarantees a consistent snapshot while this read-only audit runs.
    with FileLock(batch/'.lock'),sqlite3.connect(f'file:{batch / "queue.sqlite3"}?mode=ro',uri=True) as db:
        plan=load_plan(batch,plan_path,plan_sha256)
        for entry in plan['repairs']:
            t=entry['item']['target_id']
            try:
                additions,roles,remaining=evidence(entry,batch,db,sink)
                actual=json.loads(outcome_path(batch,t).read_bytes())
                expected=build_after(entry,plan,additions,roles,remaining,actual['finished_at'])
                bare=deepcopy(actual);bare.pop('oss_metadata',None)
                if bare!=expected:raise ValueError('Outcome differs from reviewed append-only result')
                verify_detail(actual,t,batch.name,sink)
                row=db.execute('SELECT status,reason,files,bytes,updated_at FROM jobs WHERE id=?',(t,)).fetchone()
                if row!=(actual['status'],actual['reason'],len(actual['files']),sum(f['size_bytes'] for f in actual['files']),actual['finished_at']):
                    raise ValueError('Queue outcome/count mismatch')
                for f in actual['files']:
                    if db.execute('SELECT bytes,key FROM objects WHERE sha=?',(f['sha256'],)).fetchone()!=(f['size_bytes'],f['oss']['raw_key']):
                        raise ValueError('Object registry mismatch')
                findings.append(dict(target_id=t,ok=True,status=actual['status'],remaining=remaining))
            except Exception as exc:
                findings.append(dict(target_id=t,ok=False,reason=failure_reason(exc)))
    result=dict(ok=bool(findings) and all(x['ok'] for x in findings),scope='current_state_of_this_plan; superseded outcomes require the successor plan audit',plan_sha256=plan_sha256,checked_at=now(),items=findings)
    output=Path(output);write_new(output,encoded(result))
    lines=['# 附件补收审计','',f"通过：{result['ok']}",'','| 目标 | 通过 | 状态/原因 |','| --- | --- | --- |']
    for x in findings:lines.append('| '+x['target_id']+' | '+str(x['ok'])+' | '+str(x.get('reason',x.get('status'))).replace('|','\\|')+' |')
    write_new(output.with_suffix('.md'),'\n'.join(lines).encode())
    rows=''.join('<tr><td>'+html.escape(x['target_id'])+'</td><td>'+str(x['ok'])+'</td><td>'+html.escape(str(x.get('reason',x.get('status'))))+'</td></tr>' for x in findings)
    write_new(output.with_suffix('.html'),('<!doctype html><meta charset="utf-8"><title>附件补收审计</title><style>body{font:16px system-ui;margin:40px}table{border-collapse:collapse}td,th{padding:12px;border:1px solid #ddd;overflow-wrap:anywhere}</style><h1>附件补收审计</h1><p>通过：'+str(result['ok'])+'</p><table><tr><th>目标</th><th>通过</th><th>状态/原因</th></tr>'+rows+'</table>').encode())
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    f=sub.add_parser('freeze');f.add_argument('--batch',required=True);f.add_argument('--review',required=True);f.add_argument('--review-sha256',required=True);f.add_argument('--output',required=True);f.add_argument('--run-id',required=True)
    for command in ('execute','audit'):
        p=sub.add_parser(command);p.add_argument('--batch',required=True);p.add_argument('--plan',required=True);p.add_argument('--plan-sha256',required=True);p.add_argument('--credentials',required=True);p.add_argument('--output',required=True)
    a=parser.parse_args(argv)
    if a.command=='freeze':freeze(a.batch,a.review,a.review_sha256,a.output,a.run_id);return 0
    from .oss_sink import OSSSink
    cfg=json.loads((Path(a.batch)/'plan.json').read_text())
    sink=OSSSink(credentials=a.credentials,cache_path=Path(a.batch)/'attachment-oss-cache.json',bucket=cfg['bucket'],endpoint=cfg['endpoint'],region=cfg['region'],prefix=cfg['prefix'])
    result=(execute if a.command=='execute' else audit)(a.batch,a.plan,a.plan_sha256,sink,a.output)
    print(json.dumps(result,ensure_ascii=False))
    return 0 if (result['ok'] if a.command=='audit' else all(x['status']!='failed' for x in result['items'])) else 1


if __name__=='__main__':
    raise SystemExit(main())
