"""下载运维计数：任务分区、法规身份、原件角色和墙钟历时分开统计。"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import re
import unicodedata

from law_crawler.batch_runtime import timestamp
from law_crawler.raw_capture import _url


def value(metadata, key):
    item = metadata.get(key)
    return item.get('value') if isinstance(item, dict) and 'value' in item else item


def normalize(item):
    if not isinstance(item, (str, int)) or isinstance(item, bool):
        return ''
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(item)).casefold()).strip()


def successful(row):
    return (row.get('capture_status') in (None, 'saved', 'reused') and row.get('complete')
            and type(row.get('status_code')) is int and 200 <= row['status_code'] < 300
            and isinstance(row.get('sha256'), str) and re.fullmatch('[a-fA-F0-9]{64}', row['sha256']))


def seconds_between(start, end):
    try:
        a = datetime.fromisoformat(start.replace('Z', '+00:00'))
        b = datetime.fromisoformat(end.replace('Z', '+00:00'))
        if a.tzinfo is None or b.tzinfo is None or b < a:
            return None
        return int((b-a).total_seconds())
    except (AttributeError, TypeError, ValueError):
        return None


def progress_summary(state, workers, updated_at=None, *, active_keys=(), queued_keys=(),
                     phase=None, run_started_at=None):
    updated = updated_at or timestamp()
    documents = state.get('documents', {})
    successful_hashes, reasons, file_roles = set(), Counter(), defaultdict(set)
    references = downloaded_references = 0
    legal_groups, kinds, pending_identity, other_types = defaultdict(list), {}, 0, 0
    for doc in documents.values():
        metadata = doc.get('metadata', {})
        kind = value(metadata, 'document_type')
        identity = value(metadata, 'legal_identity')
        target = kind in ('regulation', 'judicial_guidance') and metadata.get('processing_gate', 'eligible') == 'eligible'
        legal_key = None
        if target and isinstance(identity, dict):
            key = tuple(normalize(identity.get(k)) for k in ('jurisdiction', 'issuer', 'type', 'number', 'year'))
            identity_field = metadata.get('legal_identity', {})
            if all(key) and identity_field.get('status') in ('observed', 'html_context_observed', 'verified', 'independently_verified'):
                legal_key = key
                kinds[key] = kind if key not in kinds or kinds[key] == kind else 'conflict'
                legal_groups[key].append(metadata)
        if legal_key is None:
            if kind in ('administrative_decision', 'other', 'court_judgment'):
                other_types += 1
            else:
                pending_identity += 1
        roles = metadata.get('file_roles', {})
        if not isinstance(roles, dict):
            roles = {}
        for row in doc.get('files', {}).values():
            references += 1
            if successful(row):
                downloaded_references += 1
                digest = row['sha256'].lower(); successful_hashes.add(digest)
                role = roles.get(row.get('url'), {})
                role = (role.get('role') if isinstance(role, dict) and role.get('status') in
                        ('observed', 'html_context_observed', 'verified', 'independently_verified') else None)
                # 明确的源页面角色仍需对应目标文种；pending相关文件不得当附件。
                category = ('body' if role == 'regulation_body' and target else
                            'annex' if role == 'regulation_annex' and target else
                            'related' if role in ('related_document', 'supporting_document') else 'pending')
                file_roles[digest].add(category)
            else:
                reason = str(row.get('reason') or '未取得完整原文')
                if row.get('status_code') == 403 or 'HTTP 403' in reason:
                    label = 'HTTP 403：官方入口拒绝访问'
                elif any(w in reason.lower() for w in ('max_bytes', 'too large', 'size limit', 'exceeds', '超出', '超过')):
                    label = '文件超过批次体积上限'
                elif any(w in reason.lower() for w in ('tls', 'ssl', 'certificate')):
                    label = 'TLS或证书错误'
                elif 'timed out' in reason.lower() or 'timeout' in reason.lower():
                    label = '请求超时'
                else:
                    label = '其他下载错误'
                reasons[label] += 1
    # 同一身份的文种冲突不计已确认项；同法多来源仅计一次。
    conflict_keys = [key for key in legal_groups if kinds[key] == 'conflict']
    for key in conflict_keys:
        pending_identity += len(legal_groups.pop(key))
    versions, version_pending = {}, 0
    for key, rows in legal_groups.items():
        observed, unknown = set(), False
        for metadata in rows:
            evidence = metadata.get('version_id') or metadata.get('consolidation_date')
            if (isinstance(evidence, dict) and evidence.get('status') == 'observed'
                    and evidence.get('source_url') and normalize(evidence.get('value'))):
                observed.add(normalize(evidence['value']))
            else:
                unknown = True
        versions[key] = observed
        if unknown or not observed:
            version_pending += 1
    role_counts = Counter(next(iter(roles)) if len(roles) == 1 else 'pending' for roles in file_roles.values())
    # 不以“已尝试”当成功。保留失败分区，重试排队时由实时队列覆盖旧状态。
    task_keys = set()
    for platform in state.get('platforms', {}).values():
        for url in platform.get('details', []):
            try:
                task_keys.add(hashlib.sha256(_url(url).encode()).hexdigest())
            except (TypeError, ValueError):
                continue
    active, queued = set(active_keys), set(queued_keys)
    parts = Counter(total=len(task_keys), completed=0, active=0, pending=0, failed=0, needs_verification=0)
    for key in task_keys:
        doc = documents.get(key)
        if key in active:
            category = 'active'
        elif key in queued or doc is None:
            category = 'pending'
        elif doc.get('status') == 'archived' and doc.get('files') and all(successful(r) for r in doc['files'].values()):
            category = 'completed'
        elif doc.get('status') in ('failed', 'incomplete'):
            category = 'failed'
        elif doc.get('status') in ('interrupted', 'pending'):
            category = 'pending'
        else:
            category = 'needs_verification'
        parts[category] += 1
    operations = dict(parts, unit='条详情归档任务', partition_valid=True)
    legal_total = len(legal_groups)
    identity_counts = dict(regulations=sum(kinds[k] == 'regulation' for k in legal_groups),
        judicial_guidance=sum(kinds[k] == 'judicial_guidance' for k in legal_groups), total_legal_instruments=legal_total,
        identity_pending_details=pending_identity, non_target_details=other_types,
        duplicate_source_records=sum(len(v)-1 for v in legal_groups.values()),
        versions=None if version_pending else sum(map(len, versions.values())),
        multiple_version_regulations=None if version_pending else sum(len(v)>1 for v in versions.values()),
        confirmed_version_records=sum(map(len, versions.values())), version_pending_regulations=version_pending,
        basis='官方元数据五字段身份归并；文种和身份缺失或冲突列待核验；未核定法律效力')
    timing = dict(started_at=state.get('created_at'), ended_at=updated if phase in ('finished', 'stopped') else None,
                  elapsed_seconds=seconds_between(state.get('created_at'), updated), active_download_seconds=None,
                  basis='批次创建至快照的历时（含目录发现、等待和暂停）', active_time_status='pending_verification',
                  run_started_at=run_started_at, run_elapsed_seconds=seconds_between(run_started_at, updated))
    return dict(total_details=len(task_keys), attempted_details=len(documents), successful_files=len(successful_hashes),
        failed_file_references=sum(reasons.values()), failure_reasons=[{'reason':k,'count':v} for k,v in sorted(reasons.items())],
        run_workers=workers, updated_at=updated, count_basis='committed_receipts_sha256', national_completeness='unverified',
        operations=operations, identity_counts=identity_counts, timing=timing,
        file_counts=dict(total_references=references, downloaded_references=downloaded_references,
            unique_downloaded_files=len(successful_hashes), body_files=role_counts['body'], annex_files=role_counts['annex'],
            related_files=role_counts['related'], role_pending_files=role_counts['pending'], failed_references=sum(reasons.values())))
