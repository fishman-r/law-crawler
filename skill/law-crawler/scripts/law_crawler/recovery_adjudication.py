"""Validate supplemental adjudication provenance without changing catalog metadata."""
import json
from pathlib import Path

from .recovery_validation import digest, _sha


def validate_pending_resolution(repair, *, base_dir=None):
    """Keep legacy reviews readable; validate explicit supplemental resolutions strictly."""
    evidence = repair.get('evidence') or {}
    if 'pending_resolution' not in evidence:
        return []
    resolution = evidence['pending_resolution']
    kinds = {'ocr_transcription_error', 'catalog_metadata_error', 'source_internal_anomaly',
             'missing_attachment_confirmed', 'attachment_recovered'}
    if not isinstance(resolution, dict) or resolution.get('kind') not in kinds:
        raise ValueError('Unknown pending resolution kind')
    if not isinstance(resolution.get('rationale'), str) or not resolution['rationale'].strip():
        raise ValueError('Pending resolution rationale required')
    receipts = []

    def read(binding):
        if not isinstance(binding, dict) or not isinstance(binding.get('path'), str) or not binding['path']:
            raise ValueError('Resolution evidence path required')
        path = Path(binding['path'])
        if not path.is_absolute() and base_dir is not None:
            path = Path(base_dir) / path
        raw = path.read_bytes()
        if digest(raw) != _sha(binding.get('sha256')):
            raise ValueError('Resolution evidence SHA256 mismatch')
        receipts.append({'path': str(path), 'sha256': digest(raw)})
        return raw

    previous = json.loads(read(resolution.get('previous_review')))
    pending = [x for x in previous.get('pending', []) if x.get('target_id') == repair['target_id']]
    if len(pending) != 1 or any(x.get('target_id') == repair['target_id'] for x in previous.get('repairs', [])):
        raise ValueError('Previous review must contain exactly one pending target and no approval')
    checks = resolution.get('evidence_files')
    if not isinstance(checks, list) or not checks:
        raise ValueError('New resolution evidence required')
    for binding in checks:
        read(binding)
    if all(x['sha256'] == resolution['previous_review']['sha256'] for x in checks):
        raise ValueError('Old pending review alone cannot resolve itself')
    kind = resolution['kind']
    pages = evidence.get('evidence_pages') or []
    for page in pages:
        if (page.get('original_sha256') not in repair.get('approved_primary_sha256s', []) or
                type(page.get('page_number')) is not int or page['page_number'] < 1):
            raise ValueError('Resolution page must bind approved original SHA and positive page number')
    if kind == 'ocr_transcription_error':
        viewed = evidence.get('viewed_image_sha256s') or []
        if not pages or not all(p.get('sha256') in viewed for p in pages):
            raise ValueError('OCR resolution requires actually viewed page images')
        for page in pages:
            read(page)
    elif kind == 'catalog_metadata_error':
        # URL provenance is checked here; source authority and legal meaning remain
        # an independent review obligation, never inferred from a domain suffix.
        from urllib.parse import urlsplit
        official = resolution.get('official_evidence')
        fields = resolution.get('corrected_fields')
        if not isinstance(official, list) or not official or not isinstance(fields, list) or not fields:
            raise ValueError('Catalog correction requires official snapshots and explicit field differences')
        for item in official:
            parsed = urlsplit(item.get('url', ''))
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('Official evidence URL invalid')
            read(item)
        for field in fields:
            if (not isinstance(field, dict) or not field.get('field') or
                    'recorded_value' not in field or 'verified_value' not in field or
                    field['recorded_value'] == field['verified_value']):
                raise ValueError('Catalog correction must record both differing values')
    elif kind == 'source_internal_anomaly':
        anomalies = evidence.get('source_text_anomalies')
        if not isinstance(anomalies, list) or not anomalies or evidence.get('legal_content_accuracy') != 'not_verified':
            raise ValueError('Preserve source internal anomalies explicitly')
        for anomaly in anomalies:
            if not isinstance(anomaly, dict) or not anomaly.get('location') or not anomaly.get('quote'):
                raise ValueError('Source anomaly requires location, original quote and evidence')
            read(anomaly)
    elif kind == 'missing_attachment_confirmed':
        missing = evidence.get('known_missing_attachments')
        statements = resolution.get('missing_attachment_evidence')
        if not missing or evidence.get('primary_text_identity_verified') is not True or not isinstance(statements, list):
            raise ValueError('Missing attachment resolution requires verified primary and source statements')
        described = set()
        for item in statements:
            if not isinstance(item.get('quote'), str) or not item['quote'].strip():
                raise ValueError('Missing attachment source quote required')
            described.add(item.get('description'))
            read(item)
            if Path(item['path']).suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.gif'}:
                pages = evidence.get('evidence_pages') or []
                if (item['sha256'] not in (evidence.get('viewed_image_sha256s') or []) or
                        item['sha256'] not in {p.get('sha256') for p in pages}):
                    raise ValueError('Missing attachment page image was not actually viewed')
        if set(missing) != described:
            raise ValueError('Every missing attachment must have source evidence')
    elif kind == 'attachment_recovered':
        if not repair.get('additional_verified_attachments'):
            raise ValueError('Recovered attachments require verified subordinate receipts')
        if evidence.get('known_missing_attachments'):
            raise ValueError('Recovered attachment resolution still declares missing attachments')
    return receipts
