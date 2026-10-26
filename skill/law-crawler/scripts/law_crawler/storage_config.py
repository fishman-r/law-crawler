"""Explicit OSS destinations; never supply a production bucket or jurisdiction."""
import json
import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit


def validate_storage_config(config):
    result = {}
    for key in ('bucket', 'endpoint', 'region', 'prefix'):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError('OSS configuration requires explicit ' + key)
        result[key] = value
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', result['bucket']):
        raise ValueError('Invalid OSS bucket name')
    parsed = urlsplit(result['endpoint'])
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('Invalid OSS endpoint')
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', result['region']):
        raise ValueError('Invalid OSS region')
    prefix = result['prefix'].strip('/')
    if not prefix or any(part in ('', '.', '..') for part in prefix.split('/')) or '\\' in prefix or any(ord(c) < 32 for c in prefix):
        raise ValueError('Invalid OSS prefix')
    result['prefix'] = prefix
    return result


def storage_from_batch(batch):
    """Read an explicit destination; verify an existing frozen-plan signature."""
    root=Path(batch);raw=(root/'plan.json').read_bytes();signature=root/'plan.sha256'
    if signature.exists() and hashlib.sha256(raw).hexdigest()!=signature.read_text().strip():
        raise ValueError('Frozen OSS plan SHA mismatch')
    return validate_storage_config(json.loads(raw))
