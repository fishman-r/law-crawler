"""Select immutable derived-output caches without reusing failed or stale receipts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_cache(directory, expected_receipt_fields, completed_statuses):
    """Validate complete config and all relative artifact hashes; never mutate a cache."""
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Derived cache is not a regular directory')
    receipt_path = directory / 'receipt.json'
    if receipt_path.is_symlink():
        raise ValueError('Derived cache receipt must not be a symlink')
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    if receipt.get('status') not in completed_statuses:
        raise ValueError('Derived cache is incomplete or failed')
    if any(receipt.get(key) != value for key, value in expected_receipt_fields.items()):
        raise ValueError('Derived cache input, implementation or dependency configuration differs')
    artifacts = receipt.get('artifacts')
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError('Derived cache has no artifact hash manifest')
    root = directory.resolve()
    for item in artifacts:
        if (not isinstance(item, dict) or not isinstance(item.get('path'), str)
                or not isinstance(item.get('sha256'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', item['sha256'])):
            raise ValueError('Derived cache artifact manifest is invalid')
        relative = Path(item['path'])
        target = directory / relative
        if (relative.is_absolute() or not relative.parts or '..' in relative.parts
                or target.is_symlink() or not target.resolve().is_relative_to(root)
                or not target.is_file() or file_sha256(target) != item['sha256']):
            raise ValueError('Derived cache artifact path or SHA-256 differs')
    return receipt


def select_output_directory(base, expected_receipt_fields, *, completed_statuses=('completed',), max_candidates=1000):
    """Return (directory, audit); keep incompatible/failed directories unchanged.

    Inspect base, then base-attempt-2 and later suffixes. The producer remains
    responsible for validating current inputs and atomically creating output.
    This selector does not retry production, call a model, or erase evidence.
    """
    if type(max_candidates) is not int or max_candidates < 1:
        raise ValueError('max_candidates must be a positive integer')
    if not isinstance(expected_receipt_fields, dict) or not expected_receipt_fields:
        raise ValueError('Expected derived-cache receipt fields are required')
    base = Path(base).expanduser().absolute()
    skipped = []
    for attempt in range(1, max_candidates + 1):
        candidate = base if attempt == 1 else base.with_name(base.name + '-attempt-' + str(attempt))
        if not candidate.exists() and not candidate.is_symlink():
            return candidate, {'selected_directory': str(candidate), 'reuse_eligible': False, 'skipped': skipped}
        try:
            inspect_cache(candidate, expected_receipt_fields, completed_statuses)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            # Do not emit file contents, credential-bearing model settings or arbitrary
            # JSON parser excerpts into audit reports; preserve readable classifications.
            reason = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else type(exc).__name__
            record = {'directory': str(candidate), 'reason': reason}
            receipt_path = candidate / 'receipt.json'
            if not candidate.is_symlink() and not receipt_path.is_symlink() and receipt_path.is_file():
                record['receipt_sha256'] = file_sha256(receipt_path)
            skipped.append(record)
        else:
            return candidate, {'selected_directory': str(candidate), 'reuse_eligible': True, 'skipped': skipped}
    raise ValueError('Derived cache candidate limit reached; preserved all previous outputs')
