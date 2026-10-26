"""显式审核的替代来源计划执行器；上传溯源元数据后才计为完成。

调用方须冻结目标清单，并在持有批次执行锁时调用，禁止与下载 runner 同时改队列。
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from .recovery_validation import validate_repair_sources
from .recovery_attachments import verified_attachments


def _write(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.replace(path)


def apply_verified_recovery(batch, repairs, sink, allowed_target_ids, run_id):
    batch = Path(batch)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', run_id):
        raise ValueError('Invalid recovery run ID')
    history = batch / 'recovery' / run_id / 'outcomes-before'
    history.mkdir(parents=True, exist_ok=True)
    allowed = set(allowed_target_ids)
    results = []
    db = sqlite3.connect(batch / 'queue.sqlite3')
    try:
        for repair in repairs:
            target, source = repair['target_id'], repair['source_id']
            if target not in allowed or target == source or not all(re.fullmatch('[0-9a-f]{64}', v) for v in (target, source)):
                raise ValueError('Recovery target outside frozen manifest')
            row = db.execute('SELECT status FROM jobs WHERE id=?', (target,)).fetchone()
            other = db.execute('SELECT status FROM jobs WHERE id=?', (source,)).fetchone()
            if row == ('completed',):
                results.append(dict(target_id=target, status='already_completed')); continue
            if row != ('no_original',) or other != ('completed',):
                results.append(dict(target_id=target, status='skipped_status_changed')); continue
            validate_repair_sources(batch, repair)
            if not repair.get('evidence'):
                raise ValueError('Independent identity match evidence required')
            source_path = batch / 'outcomes' / (source + '.json')
            source_bytes = source_path.read_bytes()
            original = json.loads(source_bytes)
            if original.get('status') != 'completed' or original.get('oss_metadata', {}).get('status') not in ('uploaded_verified', 'reused_verified'):
                results.append(dict(target_id=target, status='source_metadata_unverified')); continue
            roles = original.get('metadata', {}).get('file_roles', {})
            excluded_path=batch/'recovery'/'invalid-source-files.json'
            excluded=json.loads(excluded_path.read_text()) if excluded_path.exists() else []
            excluded_shas={entry['sha256'] for entry in excluded if entry.get('source_id')==source}
            files = []
            primary_failed = False
            if repair.get('replacement_primary'):
                from .recovery_primary import verified_primary
                try:
                    replacement, replacement_roles = verified_primary(repair, original, sink, excluded_shas)
                    files.append(replacement)
                    roles = {**roles, **replacement_roles}
                except (ValueError, KeyError, OSError, TypeError):
                    results.append(dict(target_id=target, status='replacement_primary_verification_failed')); continue
            for receipt in ([] if repair.get('replacement_primary') else original.get('files', [])):
                if repair.get('approved_primary_sha256s') and receipt.get('sha256') not in repair['approved_primary_sha256s']:
                    continue
                if roles.get(receipt.get('url'), {}).get('role') != 'regulation_body':
                    continue
                if receipt.get('sha256') in excluded_shas:
                    primary_failed=True
                    continue
                if receipt.get('oss', {}).get('status') not in ('uploaded_verified', 'reused_verified'):
                    primary_failed = True
                    continue
                verified = sink.verify_original(dict(receipt, oss_raw_key=receipt['oss']['raw_key']))
                if verified.get('status') != 'reused_verified':
                    primary_failed = True
                    continue
                files.append(dict(deepcopy(receipt), oss=verified, source_detail_key=source,
                                  source_detail_url=original.get('detail_url')))
            if not files or primary_failed or (repair.get('approved_primary_sha256s') and set(repair['approved_primary_sha256s'])!={f['sha256'] for f in files}):
                results.append(dict(target_id=target, status='no_verified_primary')); continue
            try:
                attachments, attachment_roles = verified_attachments(repair, original, files, sink, excluded_shas)
            except (ValueError, KeyError, OSError, TypeError):
                results.append(dict(target_id=target, status='attachment_verification_failed')); continue
            if any(f['url'] in {p['url'] for p in files} for f in attachments):
                results.append(dict(target_id=target, status='attachment_verification_failed')); continue
            primary_files = files[:]
            files.extend(attachments)
            target_path = batch / 'outcomes'  / (target + '.json')
            before = target_path.read_bytes()
            archive = history / target_path.name
            if not archive.exists():
                archive.write_bytes(before)
            outcome = json.loads(before)
            outcome['files'] = files
            missing = repair['evidence'].get('known_missing_attachments', [])
            outcome['status'] = 'incomplete' if missing else 'completed'
            outcome['reason'] = ('已归档并核验法规正文；已确认缺失附件：' + '；'.join(missing) if missing else
                                 '相同法规原件由其他官方来源补足并经OSS回读核验；原平台缺原件的事实仍保留记录')
            outcome['finished_at'] = datetime.now(timezone.utc).isoformat()
            outcome['recovery'] = dict(run_id=run_id, source_detail_key=source,
                source_detail_url=original.get('detail_url'), source_platform=original.get('platform'),
                source_outcome_sha256=hashlib.sha256(source_bytes).hexdigest(),
                previous_outcome_sha256=hashlib.sha256(before).hexdigest(), evidence=repair['evidence'],
                source_relation='alternative_official_source_not_original_platform_hosted_file')
            metadata = outcome.setdefault('metadata', {})
            metadata['primary_original_missing'] = False
            metadata['original_platform_file_missing'] = True
            metadata['all_attachments_verified'] = False
            if missing:
                metadata['known_missing_attachments'] = deepcopy(missing)
            metadata['file_roles'] = {f['url']: dict(roles[f['url']], alternate_source=True) for f in primary_files}
            metadata['file_roles'].update(attachment_roles)
            outcome.pop('oss_metadata', None)
            uploaded = sink.put_metadata(target, outcome, batch_id=batch.name)
            if uploaded.get('status') not in ('uploaded_verified', 'reused_verified'):
                results.append(dict(target_id=target, status='metadata_upload_failed')); continue
            outcome['oss_metadata'] = uploaded
            with db:
                db.execute('BEGIN IMMEDIATE')
                changed = db.execute('UPDATE jobs SET status=?,reason=?,files=?,bytes=?,updated_at=? WHERE id=? AND status=?',
                    (outcome['status'], outcome['reason'], len(files), sum(f['size_bytes'] for f in files), outcome['finished_at'], target, 'no_original'))
                if changed.rowcount != 1:
                    raise ValueError('Recovery target changed during commit')
                _write(target_path, outcome)
                for f in files:
                    db.execute('INSERT OR IGNORE INTO objects VALUES(?,?,?)', (f['sha256'], f['size_bytes'], f['oss']['raw_key']))
            results.append(dict(target_id=target, source_id=source, status=outcome['status'], files=len(files)))
    finally:
        db.close()
        sink.flush_cache()
    _write(history.parent / 'results.json', results)
    return results
