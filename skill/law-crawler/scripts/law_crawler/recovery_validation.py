"""冻结恢复计划校验；摘要绑定具体批次、目标记录和已核验源结果。"""
import hashlib
import json
from pathlib import Path
import re
import sqlite3


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError('Recovery SHA256 missing or invalid')
    return value


def _item(item):
    from .recovery_attachments import validate_attachment_schema
    validate_attachment_schema(item)
    from .recovery_primary import validate_primary_schema
    validate_primary_schema(item)
    _sha(item.get('target_id'))
    _sha(item.get('target_record_sha256')); _sha(item.get('target_outcome_sha256'))
    if not isinstance(item.get('evidence'), dict) or not item['evidence']:
        raise ValueError('Recovery identity evidence required')
    evidence = item['evidence']
    if 'known_missing_attachments' in evidence:
        missing = evidence['known_missing_attachments']
        if (not isinstance(missing, list) or not missing or
                any(not isinstance(value, str) or not value.strip() for value in missing) or
                evidence.get('primary_text_identity_verified') is not True):
            raise ValueError('Known missing attachments require explicit descriptions and verified primary identity')
    if item.get('source_id'):
        _sha(item['source_id']); _sha(item.get('source_record_sha256')); _sha(item.get('source_outcome_sha256'))
        if item['source_id'] == item['target_id']:
            raise ValueError('Recovery source must differ from target')
        if 'approved_primary_sha256s' in item:
            approved=item['approved_primary_sha256s']
            if not isinstance(approved,list) or not approved or len(approved)!=len(set(approved)):
                raise ValueError('Invalid approved primary SHA list')
            for value in approved:_sha(value)
    else:
        files=item.get('files')
        if not isinstance(files,list) or not files:
            raise ValueError('Direct recovery requires explicit files')
        urls=[]
        for f in files:
            if not isinstance(f,dict) or not isinstance(f.get('url'),str) or not f['url'].startswith(('https://','http://')) or not isinstance(f.get('role'),str):
                raise ValueError('Invalid direct recovery file')
            urls.append(f['url'])
        if len(urls)!=len(set(urls)):
            raise ValueError('Duplicate direct file URL')
        _sha(item['evidence'].get('original_detail_sha256'))


def validate_plan(batch, plan, manifest_path):
    batch=Path(batch); raw=Path(manifest_path).read_bytes()
    if digest(raw)!=_sha(plan.get('target_manifest_sha256')):
        raise ValueError('Frozen target manifest SHA mismatch')
    manifest=json.loads(raw)
    if plan.get('batch_id') != batch.name or manifest.get('batch_id') != batch.name:
        raise ValueError('Recovery batch binding mismatch')
    if not isinstance(plan.get('run_id'),str) or not re.fullmatch('[A-Za-z0-9_-]+',plan['run_id']):
        raise ValueError('Invalid recovery run ID')
    jobs=manifest.get('jobs')
    if not isinstance(jobs,list):raise ValueError('Frozen target jobs required')
    ids=[_sha(row[0]) for row in jobs if isinstance(row,list) and row]
    if len(ids)!=len(jobs) or len(ids)!=len(set(ids)):raise ValueError('Invalid or duplicate frozen target')
    repairs=plan.get('repairs')
    if not isinstance(repairs,list):raise ValueError('Recovery items required')
    seen=set()
    for item in repairs:
        _item(item)
        if item['target_id'] not in ids or item['target_id'] in seen:
            raise ValueError('Duplicate or out-of-manifest recovery target')
        seen.add(item['target_id'])
    return set(ids)


def validate_repair_sources(batch, item):
    """Call after skipping already completed targets, before OSS writes; caller holds batch lock."""
    _item(item);batch=Path(batch)
    # The scoped ledger may have gained a rejection after this plan was frozen.
    # Reject that relation before any upload without invalidating other targets.
    from .recovery_workflow import load_rejected_candidate_pairs
    rejected, _ = load_rejected_candidate_pairs(batch)
    from .recovery_adjudication import validate_pending_resolution
    binding = item.get('review_binding', {}).get('path')
    if item.get('replacement_primary') and not binding:
        raise ValueError('Replacement primary requires a frozen independent review binding')
    validate_pending_resolution(item, base_dir=Path(binding).parent if binding else None)
    if binding:
        # Recheck the whole evidence chain before any upload, including replacement
        # selections' previous pending review/selection, not only the new verdict.
        from .recovery_workflow import verify_file, verify_review_evidence
        reviewed = json.loads(verify_file(binding, item['review_binding'].get('sha256')))
        matches = [x for x in reviewed.get('repairs', []) if x.get('target_id') == item['target_id']]
        if len(matches) > 1:
            raise ValueError('Duplicate recovery review target')
        if len(matches) != 1 or any(matches[0].get(k) != item.get(k) for k in
                ('source_id','approved_primary_sha256s','evidence','additional_verified_attachments','replacement_primary')):
            raise ValueError('Recovery review changed or differs from frozen item')
        verify_review_evidence(item, base_dir=Path(binding).parent)
    db=sqlite3.connect('file:'+str(batch/'queue.sqlite3')+'?mode=ro',uri=True)
    try:
        for kind in ('target','source') if item.get('source_id') else ('target',):
            key=item[kind+'_id'];row=db.execute('SELECT record FROM jobs WHERE id=?',(key,)).fetchone()
            if not row or digest(row[0].encode())!=item[kind+'_record_sha256']:
                raise ValueError('Recovery '+kind+' record SHA mismatch')
            outcome_bytes = (batch/'outcomes'/(key+'.json')).read_bytes()
            if digest(outcome_bytes)!=item[kind+'_outcome_sha256']:
                raise ValueError('Recovery '+kind+' outcome SHA mismatch')
            if kind == 'target':
                old_missing = json.loads(outcome_bytes).get('metadata', {}).get('known_missing_attachments', [])
                new_missing = item['evidence'].get('known_missing_attachments', [])
                if any(value not in new_missing for value in old_missing):
                    raise ValueError('Recovery plan must preserve previously confirmed missing attachments')
            elif kind == 'source':
                approved = item.get('approved_primary_sha256s')
                if approved is None:
                    approved = [f.get('sha256') for f in json.loads(outcome_bytes).get('files', [])]
                if any((item['target_id'], item['source_id'], sha) in rejected for sha in approved):
                    raise ValueError('Rejected candidate pair cannot be executed')
    finally:db.close()
