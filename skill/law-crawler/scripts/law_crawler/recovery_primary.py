"""Explicit replacement primary receipts; never rewrite the archived source outcome."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from .platform_adapters.census import allowed_hosts

CAPTURE_FIELDS = ('url', 'final_url', 'sha256', 'size_bytes', 'status_code',
                  'complete', 'capture_status')


def validate_primary_schema(repair):
    if 'replacement_primary' not in repair:
        return
    from .recovery_validation import _sha
    item = repair['replacement_primary']
    if not isinstance(item, dict) or item.get('role') != 'regulation_body' or not repair.get('source_id'):
        raise ValueError('Replacement primary requires explicit source and body role')
    receipt = item.get('receipt')
    if not isinstance(receipt, dict):
        raise ValueError('Replacement primary receipt required')
    sha = _sha(receipt.get('sha256'))
    if repair.get('approved_primary_sha256s') != [sha]:
        raise ValueError('Replacement primary must be the sole approved primary')
    for key in ('metadata_sha256', 'evidence_report_sha256'):
        _sha(item.get(key))
    if not isinstance(item.get('evidence_report_path'), str) or not item['evidence_report_path']:
        raise ValueError('Replacement primary independent report required')


def verify_primary_evidence(repair, original=None, *, base_dir=None):
    """Offline review and receipt validation used by freeze and every later check."""
    validate_primary_schema(repair)
    if 'replacement_primary' not in repair:
        return []
    from .recovery_workflow import verify_file, _path
    item = repair['replacement_primary']; receipt = item['receipt']
    path = _path(item['evidence_report_path'], base_dir)
    raw = verify_file(path, item['evidence_report_sha256'])
    matches = [x for x in json.loads(raw).get('items', [])
               if x.get('target_id') == repair['target_id']
               and x.get('parent_source_id') == repair['source_id']
               and x.get('original_sha256') == receipt['sha256']]
    if len(matches) != 1:
        raise ValueError('Replacement primary report identity binding mismatch')
    review = matches[0]
    if review.get('primary_identity_approved') is not True or review.get('role') != 'regulation_body':
        raise ValueError('Replacement primary independent approval missing')
    for field in ('target_identity', 'target_title'):
        if not repair.get('evidence', {}).get(field) or review.get(field) != repair['evidence'][field]:
            raise ValueError('Replacement primary target identity differs from review')
    if any(receipt.get(k) != review.get('receipt', {}).get(k) for k in CAPTURE_FIELDS):
        raise ValueError('Replacement primary capture differs from review')
    if (receipt.get('complete') is not True or receipt.get('status_code') != 200
            or receipt.get('capture_status') not in ('saved', 'reused')
            or type(receipt.get('size_bytes')) is not int or receipt['size_bytes'] <= 0):
        raise ValueError('Replacement primary capture is incomplete')
    oss = receipt.get('oss', {})
    if (receipt['sha256'] != repair['evidence'].get('original_sha256')
            or oss.get('raw_key') != repair['evidence'].get('original_key')):
        raise ValueError('Replacement primary differs from frozen selection original')
    if (oss.get('status') not in ('uploaded_verified', 'reused_verified')
            or review.get('oss', {}).get('status') not in ('uploaded_verified', 'reused_verified')
            or any(not oss.get(k) or oss[k] != review.get('oss', {}).get(k)
                   for k in ('raw_key', 'metadata_key'))):
        raise ValueError('Replacement primary archive reference differs from review')
    if (Path(oss['raw_key']).stem != receipt['sha256'] or '/raw/' not in oss['raw_key']
            or Path(oss['metadata_key']).stem != item['metadata_sha256']):
        raise ValueError('Replacement primary archive key digest mismatch')
    if any(receipt.get(k) not in (None, oss['raw_key']) for k in ('raw_key', 'key', 'oss_raw_key')):
        raise ValueError('Replacement primary raw-key alias conflict')
    for field in ('url', 'final_url'):
        u = urlsplit(receipt.get(field, ''))
        if (u.scheme != 'https' or not u.hostname or u.username or u.password or u.port not in (None, 443)
                or (original is not None and u.hostname not in allowed_hosts(original['platform']))):
            raise ValueError('Replacement primary URL outside official source hosts')
    return [{'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}]


def verified_primary(repair, original, sink, excluded_shas):
    binding = repair.get('review_binding', {}).get('path')
    verify_primary_evidence(repair, original, base_dir=Path(binding).parent if binding else None)
    item = repair['replacement_primary']; receipt = item['receipt']; oss = receipt['oss']
    if receipt['sha256'] in excluded_shas:
        raise ValueError('Replacement primary is excluded')
    envelope = sink.read_verified_archive_metadata(oss['metadata_key'], item['metadata_sha256'])
    source = envelope.get('source', {})
    if (envelope.get('raw_key') != oss['raw_key'] or source.get('target_id') != repair['target_id']
            or source.get('parent_source_id') != repair['source_id'] or source.get('role') != 'regulation_body'
            or not original.get('detail_url') or source.get('detail_url') != original['detail_url']):
        raise ValueError('Replacement primary archive provenance mismatch')
    if any(envelope.get('capture', {}).get(k) != receipt.get(k) for k in CAPTURE_FIELDS):
        raise ValueError('Replacement primary archive capture mismatch')
    verified = sink.verify_original(dict(sha256=receipt['sha256'], size_bytes=receipt['size_bytes'],
                                        oss_raw_key=oss['raw_key']))
    if (verified.get('status') != 'reused_verified' or verified.get('raw_key') != oss['raw_key']
            or verified.get('sha256') != receipt['sha256'] or verified.get('size_bytes') != receipt['size_bytes']):
        raise ValueError('Replacement primary OSS original verification failed')
    result = dict(deepcopy(receipt), oss=verified, archive_metadata_key=oss['metadata_key'],
                  archive_metadata_sha256=item['metadata_sha256'], source_detail_key=repair['source_id'],
                  source_detail_url=original['detail_url'])
    role = dict(role='regulation_body', alternate_source=True, replacement_primary=True,
                evidence_report_sha256=item['evidence_report_sha256'])
    return result, {receipt['url']: role}
