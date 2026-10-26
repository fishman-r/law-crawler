"""只复用独立审核并绑定 OSS 元数据的附件；不下载、不修改源结果。"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
from .platform_adapters.census import allowed_hosts


def validate_attachment_schema(repair):
    if 'additional_verified_attachments' not in repair:
        return
    entries = repair['additional_verified_attachments']
    if not repair.get('source_id') or not repair.get('approved_primary_sha256s') or not isinstance(entries, list) or not entries:
        raise ValueError('Attachments require explicit verified source and primary SHA set')
    seen = set()
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError('Invalid attachment entry')
        if item.get('role') != 'attachment' or item.get('attachment_kind') not in ('annex', 'official_explanation'):
            raise ValueError('Invalid subordinate attachment role')
        if item.get('parent_primary_sha256') not in repair['approved_primary_sha256s']:
            raise ValueError('Attachment parent is not approved primary')
        for field in ('evidence_report_sha256', 'full_text_sha256', 'metadata_sha256'):
            value = item.get(field)
            if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('Attachment evidence SHA missing')
        receipt = item.get('receipt', {})
        digest, url = receipt.get('sha256'), receipt.get('url')
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Attachment original SHA invalid')
        if not isinstance(url, str) or digest in seen or url in seen or digest in repair['approved_primary_sha256s']:
            raise ValueError('Duplicate attachment or primary masquerading as attachment')
        seen.update((digest, url))
        if not isinstance(item.get('evidence_report_path'), str):
            raise ValueError('Attachment evidence path missing')


def verified_attachments(repair, original, primary_files, sink, excluded_shas):
    validate_attachment_schema(repair)
    files, roles = [], {}
    for item in repair.get('additional_verified_attachments', []):
        raw = Path(item['evidence_report_path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != item['evidence_report_sha256']:
            raise ValueError('Attachment evidence report changed')
        receipt = item['receipt']; digest = receipt['sha256']
        if digest in excluded_shas or item['parent_primary_sha256'] not in {f['sha256'] for f in primary_files}:
            raise ValueError('Attachment excluded or primary not verified')
        matches = [x for x in json.loads(raw).get('items', []) if x.get('target_id') == repair['target_id'] and x.get('parent_source_id') == repair['source_id'] and x.get('original_sha256') == digest]
        if len(matches) != 1:
            raise ValueError('Attachment report identity binding mismatch')
        evidence = matches[0]
        if (evidence.get('attachment_identity_approved') is not True or evidence.get('role') != 'attachment'
                or evidence.get('parent_original_sha256') != item['parent_primary_sha256']
                or evidence.get('attachment_kind') != item['attachment_kind']
                or evidence.get('full_text_sha256') != item['full_text_sha256']):
            raise ValueError('Attachment review binding mismatch')
        for field in ('url', 'final_url', 'sha256', 'size_bytes', 'status_code', 'complete', 'capture_status'):
            if receipt.get(field) != evidence['receipt'].get(field):
                raise ValueError('Attachment receipt differs from review')
        if receipt.get('complete') is not True or receipt.get('capture_status') not in ('saved', 'reused') or receipt.get('status_code') != 200 or type(receipt.get('size_bytes')) is not int or receipt['size_bytes'] <= 0:
            raise ValueError('Attachment capture is not complete')
        for field in ('url', 'final_url'):
            u = urlsplit(receipt[field])
            if u.scheme != 'https' or u.username or u.password or u.port not in (None, 443) or u.hostname not in allowed_hosts(original['platform']):
                raise ValueError('Attachment URL outside official source hosts')
        oss = receipt.get('oss', {})
        if evidence['oss'].get('status') not in ('uploaded_verified', 'reused_verified'):
            raise ValueError('Attachment archive was not verified')
        if any(oss.get(k) != evidence['oss'].get(k) for k in ('raw_key', 'metadata_key')):
            raise ValueError('Attachment OSS reference differs from review')
        envelope = sink.read_verified_archive_metadata(oss['metadata_key'], item['metadata_sha256'])
        if envelope.get('raw_key') != oss['raw_key'] or envelope.get('source', {}).get('target_id') != repair['target_id'] or envelope['source'].get('parent_source_id') != repair['source_id'] or envelope['source'].get('parent_original_sha256') != item['parent_primary_sha256'] or envelope['source'].get('role') != 'attachment' or envelope['source'].get('attachment_kind') != item['attachment_kind']:
            raise ValueError('Attachment OSS metadata provenance mismatch')
        if any(envelope.get('capture', {}).get(k) != receipt.get(k) for k in ('url', 'final_url', 'sha256', 'size_bytes', 'complete', 'status_code')):
            raise ValueError('Attachment OSS metadata capture mismatch')
        if any(receipt.get(k) not in (None, oss['raw_key']) for k in ('raw_key', 'key', 'oss_raw_key')):
            raise ValueError('Attachment raw-key alias conflict')
        verified = sink.verify_original(dict(sha256=digest, size_bytes=receipt['size_bytes'], oss_raw_key=oss['raw_key']))
        if (verified.get('status') != 'reused_verified' or verified.get('raw_key') != oss['raw_key']
                or verified.get('sha256') != digest or verified.get('size_bytes') != receipt['size_bytes']):
            raise ValueError('Attachment OSS original verification failed')
        files.append(dict(deepcopy(receipt), oss=verified, archive_metadata_key=oss['metadata_key'], archive_metadata_sha256=item['metadata_sha256'], source_detail_key=repair['source_id'], source_detail_url=original.get('detail_url')))
        roles[receipt['url']] = dict(role='attachment', attachment_kind=item['attachment_kind'], parent_primary_sha256=item['parent_primary_sha256'], alternate_source=True, evidence_report_sha256=item['evidence_report_sha256'])
    return files, roles
