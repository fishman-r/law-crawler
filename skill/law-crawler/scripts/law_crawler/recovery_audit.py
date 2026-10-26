"""恢复执行后独立只读审计；流式回读 OSS，不上传、不修改队列或缓存。"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import html
import os
import tempfile
import json
from pathlib import Path
import sqlite3


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


class AuditFailure(ValueError):
    pass


def _require(ok, code):
    if not ok:
        raise AuditFailure(code)


def _read_object(sink, key, expected_sha, expected_size, *, metadata=False):
    """Only GET: never trust HEAD/receipt verification flags or persist sink cache."""
    _require(isinstance(key, str) and key.startswith(sink.prefix + '/'), 'oss_key_scope')
    _require(type(expected_size) is int and expected_size >= 0, 'oss_size_missing')
    limit = 16 * 1024 * 1024 if metadata else expected_size
    _require(not metadata or expected_size <= limit, 'metadata_too_large')
    stream = sink._bucket().get_object(key)
    h = hashlib.sha256(); size = 0; chunks = []
    try:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            _require(size <= limit, 'oss_size_exceeded')
            h.update(chunk)
            if metadata:
                chunks.append(chunk)
    finally:
        close = getattr(stream, 'close', None)
        if close:
            close()
    _require(size == expected_size and h.hexdigest() == expected_sha, 'oss_content_mismatch')
    return b''.join(chunks) if metadata else None


def _expected_files(item):
    if item.get('source_id'):
        primary = item.get('approved_primary_sha256s')
        _require(isinstance(primary, list) and bool(primary), 'explicit_primary_set_required')
    else:
        primary = [f.get('sha256') for f in item.get('files', [])]
        _require(primary and all(primary), 'direct_original_sha_required')
    extra = [a['receipt']['sha256'] for a in item.get('additional_verified_attachments', [])]
    expected = primary + extra
    _require(len(expected) == len(set(expected)), 'duplicate_approved_original')
    return set(expected)


class _ReadOnlyAttachmentSink:
    def __init__(self, sink):
        self.sink = sink

    def verify_original(self, receipt):
        key = receipt['oss_raw_key']
        _read_object(self.sink, key, receipt['sha256'], receipt['size_bytes'])
        return dict(status='reused_verified', raw_key=key, sha256=receipt['sha256'], size_bytes=receipt['size_bytes'])

    def read_verified_archive_metadata(self, key, digest):
        # Read bounded metadata directly; no HEAD or mutable verification cache.
        stream = self.sink._bucket().get_object(key)
        try:
            raw = stream.read(2 * 1024 * 1024 + 1)
        finally:
            close = getattr(stream, 'close', None)
            if close: close()
        _require(len(raw) <= 2 * 1024 * 1024 and _digest(raw) == digest, 'attachment_metadata_digest')
        _require(key.startswith(self.sink.prefix + '/metadata/') and Path(key).stem == digest, 'attachment_metadata_key')
        envelope = json.loads(raw)
        _require(envelope.get('bucket') == self.sink.bucket_name, 'attachment_metadata_bucket')
        return envelope


def _publish(path, text):
    """Publish complete bytes without replacing an earlier report."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text); handle.flush(); os.fsync(handle.fileno())
        os.link(temporary, path)  # atomic and fails if destination exists
    finally:
        os.unlink(temporary)


def _render_reports(output, report):
    title = '恢复执行后只读审计'
    status = '通过' if report['ok'] else '失败'
    summary = [('审计结果', status), ('全队列任务', report.get('queue_total', '未知')),
               ('原始冻结范围', report.get('manifest_count', '未知')), ('本次恢复计划', report.get('plan_count', '未知')),
               ('核验为完成', report.get('audited_completed_count', 0)), ('核验为不完整', report.get('audited_incomplete_count', 0)),
               ('审计错误', len(report['errors'])), ('最近更新时间', report.get('finished_at', ''))]
    states = sorted(set(report.get('queue_counts', {})) | set(report.get('manifest_status_counts', {})))
    state_rows = [(k, report.get('queue_counts', {}).get(k, 0), report.get('manifest_status_counts', {}).get(k, 0)) for k in states]
    target_rows = [(r['target_id'], '通过' if r['ok'] else '失败', ', '.join(r['checks'])) for r in report['targets']]
    error_rows = [(e.get('target_id') or '全局', e['check'], e['error_type']) for e in report['errors']]
    def md_cell(value):
        return str(value).replace('|', r'\|').replace('\n', ' ')
    def md_table(headers, rows):
        return ['| ' + ' | '.join(map(md_cell, headers)) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |'] + ['| ' + ' | '.join(map(md_cell, r)) + ' |' for r in rows]
    lines = [f'# {title}', '', '仅核验本恢复计划；通过不代表原始全量法规全部下载完成。', '']
    for name, headers, rows in [('摘要', ['指标','值'], summary), ('范围与状态', ['状态','全队列','原始冻结范围'], state_rows), ('逐项核验', ['目标ID','结论','通过检查'], target_rows), ('错误', ['目标ID','检查','错误类型'], error_rows)]:
        lines += [f'## {name}', ''] + md_table(headers, rows) + ['']
    _publish(output.with_suffix('.md'), '\n'.join(lines))
    def table(headers, rows):
        esc = lambda v: html.escape(str(v), quote=True)
        return '<table><thead><tr>' + ''.join('<th>'+esc(h)+'</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in row)+'</tr>' for row in rows) + '</tbody></table>'
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>'+title+'</title><style>body{max-width:1200px;margin:32px auto;padding:24px;font:15px/1.6 system-ui;color:#17324d;background:#f6f8fb}table{width:100%;border-collapse:collapse;background:white;margin:16px 0}th,td{padding:10px;border:1px solid #d7e0ea;text-align:left;overflow-wrap:anywhere}th{background:#e6eef7}pre{white-space:pre-wrap;overflow-wrap:anywhere}h1{border-bottom:3px solid #246}td:first-child{max-width:440px}</style>'
    page += '<h1>'+title+'：'+status+'</h1><p>仅核验本恢复计划；通过不代表原始全量法规全部下载完成。</p>'
    for name, headers, rows in [('摘要', ['指标','值'], summary), ('范围与状态', ['状态','全队列','原始冻结范围'], state_rows), ('逐项核验', ['目标ID','结论','通过检查'], target_rows), ('错误', ['目标ID','检查','错误类型'], error_rows)]:
        page += '<h2>'+name+'</h2>'+table(headers, rows)
    page += '<details><summary>完整审计证据 JSON</summary><pre>'+html.escape(json.dumps(report, ensure_ascii=False, indent=2))+'</pre></details></html>'
    _publish(output.with_suffix('.html'), page)


def _verify_legacy_attachment_review(item, reviewed):
    """Validate the historical compound review without rewriting its pending stage.

    Later frozen approval binds the complete primary review, explicit identity
    adjudication, and separately recovered attachment review. Only four derived
    evidence fields may have been added by the historical planner.
    """
    e = item['evidence']; original = reviewed['evidence']; receipts = []
    _require(item.get('additional_verified_attachments') and original.get('primary_text_identity_verified') is True, 'legacy_compound_required')
    def read(path, sha):
        raw = Path(path).read_bytes()
        _require(_digest(raw) == sha, 'legacy_supporting_sha')
        receipts.append({'path': str(path), 'sha256': sha})
        return raw
    proposal = json.loads(read(original['proposal_path'], original['proposal_sha256']))
    candidates = [x for x in proposal['candidates'] if x.get('target_id') == item['target_id'] and x.get('source_id') == item['source_id']]
    _require(len(candidates) == 1, 'legacy_proposal_target')
    candidate = candidates[0]; identity = original['identity_review']
    _require(candidate.get('subject_review') == identity and identity.get('decision') == 'approved_identity' and bool(identity.get('reason')), 'legacy_identity_adjudication')
    _require(candidate.get('approved_primary_sha256s') == item['approved_primary_sha256s'], 'legacy_primary_set')
    prior = json.loads(read(original['full_primary_review_path'], original['full_primary_review_sha256']))
    matches = [x for key in ('repairs','pending') for x in prior.get(key, []) if x.get('target_id') == item['target_id'] and x.get('source_id') == item['source_id']]
    _require(len(matches) == 1, 'legacy_primary_review_target')
    primary = matches[0]
    _require(primary.get('category') in ('attachment_pending','attachment_and_typo_pending') and primary.get('full_retained_text_read') is True and primary.get('original_verified') is True, 'legacy_primary_review_scope')
    _require(primary['original_sha256'] in item['approved_primary_sha256s'] and len(item['approved_primary_sha256s']) == 1, 'legacy_primary_review_sha')
    _require(identity['target_title'] == primary['target_title'] and identity['observed_header'] == primary['observed_header'], 'legacy_identity_titles')
    raw = read(primary['results_path'], primary['results_sha256'])
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    x = rows[primary['results_line'] - 1]
    _require(x.get('target_id') == item['target_id'] and x.get('source_id') == item['source_id'] and x.get('sha256') == primary['original_sha256'] and x.get('raw_key') == primary['raw_key'] and x.get('original_verified') is True, 'legacy_primary_text_identity')
    _require(x['target_identity'] == primary['target_identity'] == x['body']['identity'] and x['target_title'] == primary['target_title'], 'legacy_primary_formal_identity')
    hashes = [_digest(t.encode()) for t in x['page_texts']]
    _require(hashes == x['extracted_page_sha256'] == primary['page_sha256'] and [len(t) for t in x['page_texts']] == primary['full_text_lengths'], 'legacy_primary_text_sha')
    _require(_digest(x['first_page_text'].encode()) == x['extracted_first_page_sha256'], 'legacy_primary_first_text_sha')
    derived = {'original_verified': True, 'original_key': primary['raw_key'], 'original_sha256': primary['original_sha256'], 'extracted_page_sha256': hashes}
    _require(all(e.get(k) == v for k,v in derived.items()), 'legacy_derived_evidence')
    report = json.loads(read(original['attachment_review_path'], original['attachment_review_sha256']))
    for attachment in item['additional_verified_attachments']:
        _require(attachment['evidence_report_path'] == original['attachment_review_path'] and attachment['evidence_report_sha256'] == original['attachment_review_sha256'], 'legacy_attachment_report_binding')
        matches = [a for a in report['items'] if a.get('target_id') == item['target_id'] and a.get('parent_source_id') == item['source_id'] and a.get('original_sha256') == attachment['receipt']['sha256']]
        _require(len(matches) == 1, 'legacy_attachment_target')
        a = matches[0]
        _require(a.get('attachment_identity_approved') is True and a.get('parent_original_sha256') == primary['original_sha256'] and a.get('full_text_sha256') == attachment['full_text_sha256'], 'legacy_attachment_identity')
        read(a['full_text_path'], a['full_text_sha256'])
    comparable = deepcopy(e)
    for key in (*derived, 'review_document_path', 'review_document_sha256'):
        comparable.pop(key, None)
    _require(comparable == original, 'legacy_compound_evidence_equality')
    return {'verified': True, 'files': receipts}


def audit_recovery(*, batch, manifest, plan, results, credentials=None, output,
                   sink=None, evidence_verifier=None):
    """Return immutable report with ok flag. Inject GET-only fake sink in tests.

    Report errors contain controlled codes and exception classes, never exception
    messages, credentials, object contents, or signed URLs. An existing output
    is refused. All plan targets are audited, including failed result entries.
    """
    batch = Path(batch); output = Path(output)
    if output.suffix != '.json':
        raise ValueError('Audit output must end in .json')
    if any(output.with_suffix(ext).exists() for ext in ('.json', '.md', '.html')) or output.exists():
        raise FileExistsError('Audit report already exists')
    report = {'schema_version': 1, 'kind': 'recovery_post_execution_audit',
              'started_at': datetime.now(timezone.utc).isoformat(), 'ok': False,
              'checks': [], 'errors': [], 'targets': [], 'input_sha256': {},
              'scope': 'Only this frozen repair plan; not completion of the entire corpus',
              'all_manifest_targets_completed': False, 'read_only': True}
    db = None
    def error(code, exc, target=None):
        report['errors'].append({'check': code, 'target_id': target,
                                 'error_type': type(exc).__name__})
    try:
        inputs = {}
        for name, path in [('manifest', manifest), ('plan', plan), ('results', results)]:
            raw = Path(path).read_bytes(); inputs[name] = json.loads(raw)
            report['input_sha256'][name] = _digest(raw)
        _require(Path(plan).with_suffix('.sha256').read_text().strip() == report['input_sha256']['plan'], 'plan_sidecar_sha')
        frozen, repair_plan, result = inputs['manifest'], inputs['plan'], inputs['results']
        from .recovery_validation import validate_plan
        ids = validate_plan(batch, repair_plan, manifest)
        _require(frozen.get('count') == len(frozen['jobs']), 'manifest_count')
        if isinstance(result, dict):
            _require(result.get('plan_sha256') == report['input_sha256']['plan'], 'result_plan_sha')
            if result.get('kind') == 'recovery_execution_results':
                _require(result.get('schema_version') == 1 and result.get('batch_id') == batch.name and result.get('run_id') == repair_plan['run_id'] and isinstance(result.get('attempt_id'), str) and len(result['attempt_id']) == 32, 'result_envelope_binding')
            result = result['results']
        _require(isinstance(result, list), 'results_list')
        target_ids = [r['target_id'] for r in repair_plan['repairs']]
        result_ids = [r['target_id'] for r in result]
        _require(len(result_ids) == len(set(result_ids)) and set(result_ids) == set(target_ids), 'exact_result_targets')
        result_map = {r['target_id']: r for r in result}
        report['manifest_count'] = len(ids); report['plan_count'] = len(target_ids)
        report['checks'].append('manifest_batch_and_exact_results')
        db = sqlite3.connect((batch / 'queue.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
        db.execute('PRAGMA query_only=ON')
        data_version = db.execute('PRAGMA data_version').fetchone()[0]
        db.execute('BEGIN')
        watch = [Path(manifest), Path(plan), Path(results), Path(plan).with_suffix('.sha256')]
        watch += [batch/'outcomes'/f"{i[k]}.json" for i in repair_plan['repairs'] for k in ('target_id','source_id') if i.get(k)]
        watch += [Path(b['path']) for b in repair_plan.get('review_bindings', [])]
        watch += [Path(i['evidence']['review_document_path']) for i in repair_plan['repairs'] if i['evidence'].get('review_document_path')]
        ledger_file = batch/'recovery'/'invalid-source-files.json'
        pair_ledger_file = batch/'recovery'/'rejected-candidate-pairs.json'
        before_watch = {str(p): _digest(p.read_bytes()) for p in watch}
        ledger_before = _digest(ledger_file.read_bytes()) if ledger_file.exists() else None
        pair_ledger_before = _digest(pair_ledger_file.read_bytes()) if pair_ledger_file.exists() else None
        from .recovery_workflow import load_rejected_candidate_pairs
        rejected_pairs, rejection_receipts = load_rejected_candidate_pairs(batch)
        for proof in rejection_receipts:
            proof_path = Path(proof['path'])
            _require(_digest(proof_path.read_bytes()) == proof['sha256'], 'rejected_pair_evidence_sha')
            watch.append(proof_path); before_watch[str(proof_path)] = proof['sha256']
        report['queue_counts'] = dict(db.execute('SELECT status, count(*) FROM jobs GROUP BY status'))
        report['queue_total'] = sum(report['queue_counts'].values())
        manifest_status = {}
        missing_ids = []
        for target_id in sorted(ids):
            status_row = db.execute('SELECT status FROM jobs WHERE id=?', (target_id,)).fetchone()
            if not status_row:
                missing_ids.append(target_id)
            else:
                manifest_status[status_row[0]] = manifest_status.get(status_row[0], 0) + 1
        report['manifest_status_counts'] = manifest_status
        report['manifest_missing_target_ids'] = missing_ids
        _require(not missing_ids, 'manifest_targets_missing_from_queue')
        if sink is None:
            from .oss_sink import OSSSink
            # Constructor only reads cache; this guaranteed absent path is never flushed.
            configuration = json.loads((batch / 'plan.json').read_text())
            sink = OSSSink(credentials=credentials, cache_path=output.parent / (output.name + '.unused-cache'), **{k: configuration[k] for k in ('bucket', 'endpoint', 'region', 'prefix')})
        if evidence_verifier is None:
            from .recovery_workflow import verify_review_evidence
            evidence_verifier = verify_review_evidence
        for item in repair_plan['repairs']:
            target = item['target_id']; entry = {'target_id': target, 'ok': False, 'checks': []}
            report['targets'].append(entry)
            try:
                _require(not any((target, item.get('source_id'), sha) in rejected_pairs
                                 for sha in item.get('approved_primary_sha256s', [])),
                         'rejected_candidate_pair')
                binding = item.get('review_binding')
                legacy = binding is None
                if legacy:
                    ev = item['evidence']
                    binding = {'path': ev.get('review_document_path'), 'sha256': ev.get('review_document_sha256')}
                    _require(binding.get('path') and binding.get('sha256'), 'legacy_review_binding_missing')
                else:
                    _require(isinstance(binding, dict) and binding in repair_plan.get('review_bindings', []), 'review_binding_missing')
                review_raw = Path(binding['path']).read_bytes()
                _require(_digest(review_raw) == binding['sha256'], 'review_sha')
                reviewed = [r for r in json.loads(review_raw).get('repairs', []) if r.get('target_id') == target]
                compare_item = deepcopy(item)
                if legacy:
                    compare_item['evidence'].pop('review_document_path', None)
                    compare_item['evidence'].pop('review_document_sha256', None)
                _require(len(reviewed) == 1 and all(reviewed[0].get(k) == compare_item.get(k) for k in ('source_id','approved_primary_sha256s','additional_verified_attachments','replacement_primary')), 'review_item_binding')
                if legacy and item.get('additional_verified_attachments') and reviewed[0]['evidence'].get('full_primary_review_path'):
                    evidence_receipt = _verify_legacy_attachment_review(item, reviewed[0])
                else:
                    _require(reviewed[0]['evidence'] == compare_item['evidence'], 'review_item_binding')
                    evidence_receipt = evidence_verifier(item)
                for proof in evidence_receipt.get('files', []):
                    proof_path = Path(proof['path'])
                    _require(_digest(proof_path.read_bytes()) == proof['sha256'], 'evidence_receipt_sha')
                    watch.append(proof_path); before_watch[str(proof_path)] = proof['sha256']
                entry['checks'].append('review_selection_text_and_images')
                row = db.execute('SELECT record,status,files,bytes FROM jobs WHERE id=?', (target,)).fetchone()
                _require(row and _digest(row[0].encode()) == item['target_record_sha256'], 'target_record_sha')
                outcome = json.loads((batch/'outcomes'/f'{target}.json').read_bytes())
                expected_status = 'incomplete' if item['evidence'].get('known_missing_attachments') else 'completed'
                _require(outcome.get('status') == row[1] == expected_status, 'target_queue_status')
                _require(result_map[target].get('status') in (expected_status, 'already_completed'), 'execution_result_status')
                recovery = outcome.get('recovery', {})
                run_id = repair_plan['run_id'] + '-' + target[:16]
                _require(recovery.get('run_id') == run_id and recovery.get('evidence') == item['evidence'], 'recovery_evidence_binding')
                old = batch/'recovery'/run_id/'outcomes-before'/f'{target}.json'
                _require(_digest(old.read_bytes()) == item['target_outcome_sha256'], 'previous_outcome_sha')
                if item.get('source_id'):
                    source = item['source_id']
                    source_raw = (batch/'outcomes'/f'{source}.json').read_bytes()
                    _require(_digest(source_raw) == item['source_outcome_sha256'], 'source_outcome_changed')
                    source_row = db.execute('SELECT record,status FROM jobs WHERE id=?', (source,)).fetchone()
                    _require(source_row and _digest(source_row[0].encode()) == item['source_record_sha256'] and source_row[1] == 'completed', 'source_record_status')
                    _require(recovery.get('source_detail_key') == source and recovery.get('source_outcome_sha256') == item['source_outcome_sha256'], 'source_recovery_binding')
                ledger_path = batch/'recovery'/'invalid-source-files.json'
                ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else []
                _require(not any(e.get('source_id') == item.get('source_id') and e.get('sha256') in _expected_files(item) for e in ledger), 'excluded_source_original')
                expected = _expected_files(item); files = outcome.get('files', [])
                from .census_to_oss import valid as valid_capture
                if item.get('source_id'):
                    source_outcome = json.loads(source_raw)
                    if item.get('replacement_primary'):
                        from .recovery_primary import verified_primary
                        checked_primary, checked_primary_roles = verified_primary(item, source_outcome, _ReadOnlyAttachmentSink(sink), set())
                    for approved_sha in ([] if item.get('replacement_primary') else item['approved_primary_sha256s']):
                        source_files = [f for f in source_outcome.get('files', []) if f.get('sha256') == approved_sha]
                        _require(len(source_files) == 1 and valid_capture(source_files[0]) and source_files[0].get('size_bytes', 0) > 0, 'source_capture_incomplete')
                        source_role = source_outcome.get('metadata', {}).get('file_roles', {}).get(source_files[0].get('url'), {}).get('role')
                        _require(source_role == 'regulation_body', 'source_primary_role')
                actual = [f['sha256'] for f in files]
                _require(len(actual) == len(set(actual)) and set(actual) == expected, 'exact_original_set')
                _require(row[2] == len(files) and row[3] == sum(f['size_bytes'] for f in files), 'queue_file_counters')
                if item.get('additional_verified_attachments'):
                    from .recovery_attachments import verified_attachments
                    checked_attachments, checked_roles = verified_attachments(item, json.loads(source_raw), [f for f in files if f['sha256'] in item['approved_primary_sha256s']], _ReadOnlyAttachmentSink(sink), set())
                roles = outcome.get('metadata', {}).get('file_roles', {})
                if item.get('replacement_primary'):
                    matches = [f for f in files if f.get('sha256') == checked_primary['sha256']]
                    _require(len(matches) == 1 and all(matches[0].get(k) == checked_primary.get(k) for k in
                        ('archive_metadata_key','archive_metadata_sha256','source_detail_key','source_detail_url',
                         'url','final_url','sha256','size_bytes','complete','status_code','capture_status')), 'replacement_primary_outcome_binding')
                    _require(roles.get(checked_primary['url']) == checked_primary_roles[checked_primary['url']], 'replacement_primary_role_binding')
                if item.get('additional_verified_attachments'):
                    for attachment in checked_attachments:
                        matches = [f for f in files if f.get('sha256') == attachment['sha256']]
                        _require(len(matches) == 1 and all(matches[0].get(k) == attachment.get(k) for k in ('archive_metadata_key','archive_metadata_sha256','source_detail_key','url')), 'attachment_outcome_binding')
                        _require(roles.get(attachment['url']) == checked_roles[attachment['url']], 'attachment_role_parent_binding')
                extras = {a['receipt']['sha256'] for a in item.get('additional_verified_attachments', [])}
                for f in files:
                    _require(valid_capture(f) and f.get('size_bytes', 0) > 0, 'target_capture_incomplete')
                    _require(roles.get(f.get('url'), {}).get('role') == ('attachment' if f['sha256'] in extras else ('regulation_body' if item.get('source_id') else next(v['role'] for v in item['files'] if v.get('sha256') == f['sha256']))), 'file_role')
                    oss = f['oss']; key = oss.get('raw_key') or oss.get('key')
                    _require(all(oss.get(k) in (None,key) for k in ('raw_key','key')) and all(f.get(k) in (None,key) for k in ('raw_key','key','oss_raw_key')), 'original_alias_conflict')
                    _require(not oss.get('bucket') or oss['bucket'] == sink.bucket_name, 'original_bucket')
                    _require(key.startswith(sink.prefix + '/raw/') and key.count('/') == (sink.prefix + '/raw/x').count('/') and Path(key).stem == f['sha256'], 'original_key_digest')
                    _read_object(sink, key, f['sha256'], f['size_bytes'])
                entry['checks'].append('originals_stream_sha_size_exact_set')
                receipt = outcome['oss_metadata']; key = receipt.get('metadata_key') or receipt.get('key')
                _require(all(receipt.get(k) in (None,key) for k in ('metadata_key','key')), 'metadata_alias_conflict')
                _require(not receipt.get('bucket') or receipt['bucket'] == sink.bucket_name, 'metadata_bucket')
                _require(key == f"{sink.prefix}/metadata/{batch.name}/{_digest(target.encode())}/{receipt['sha256']}.json", 'metadata_key_binding')
                body = _read_object(sink, key, receipt['sha256'], receipt['size_bytes'], metadata=True)
                envelope = json.loads(body); document = deepcopy(outcome); document.pop('oss_metadata', None)
                _require(envelope.get('batch_id') == batch.name and envelope.get('detail_key') == target and envelope.get('document') == document, 'metadata_document_equality')
                entry['checks'].append('metadata_stream_sha_size_document_equality')
                entry['ok'] = True
            except Exception as exc:
                error(str(exc) if isinstance(exc, AuditFailure) else 'target_audit', exc, target)
        _require(all(p.exists() and _digest(p.read_bytes()) == before_watch[str(p)] for p in watch), 'audit_inputs_changed_during_read')
        _require((_digest(ledger_file.read_bytes()) if ledger_file.exists() else None) == ledger_before, 'ledger_changed_during_read')
        _require((_digest(pair_ledger_file.read_bytes()) if pair_ledger_file.exists() else None) == pair_ledger_before, 'rejected_pair_ledger_changed_during_read')
        db.rollback()
        _require(db.execute('PRAGMA data_version').fetchone()[0] == data_version, 'queue_changed_during_read')
        report['audited_completed_count'] = sum(t['ok'] and not i['evidence'].get('known_missing_attachments') for t,i in zip(report['targets'],repair_plan['repairs']))
        report['audited_incomplete_count'] = sum(t['ok'] and bool(i['evidence'].get('known_missing_attachments')) for t,i in zip(report['targets'],repair_plan['repairs']))
        report['audited_success_count'] = sum(t['ok'] for t in report['targets'])
        report['ok'] = not report['errors'] and report['audited_success_count'] == len(target_ids)
    except Exception as exc:
        error(str(exc) if isinstance(exc, AuditFailure) else 'audit_setup', exc)
    finally:
        if db is not None:
            db.close()
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        _publish(output, json.dumps(report, ensure_ascii=False, indent=2))
        _render_reports(output, report)
    return report


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('batch', 'manifest', 'plan', 'results', 'credentials', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    return 0 if audit_recovery(**vars(args))['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
