"""Freeze replacement candidates for previously pending targets, without clearing history."""
import copy
import json
from pathlib import Path
import sqlite3
from datetime import datetime, timezone

from .file_lock import FileLock
from .recovery_validation import digest, _sha


def verify_previous_pending(item, *, base_dir=None):
    from .recovery_workflow import verify_file, _path, _items
    receipts, values = [], {}
    for name in ('previous_review', 'previous_selection'):
        binding = item.get(name)
        if not isinstance(binding, dict) or not binding.get('path'):
            raise ValueError('Supplement requires previous pending review and selection')
        path = _path(binding['path'], base_dir)
        raw = verify_file(path, binding.get('sha256'))
        values[name] = json.loads(raw)
        receipts.append({'path': str(path), 'sha256': digest(raw)})
    review = values['previous_review']
    old = [x for x in review.get('pending', []) if x.get('target_id') == item['target_id']]
    if len(old) != 1 or any(x.get('target_id') == item['target_id'] for x in review.get('repairs', [])):
        raise ValueError('Supplement target must be pending in previous review')
    selected = [x for x in _items(values['previous_selection']) if x.get('target_id') == item['target_id']]
    if len(selected) != 1:
        raise ValueError('Previous selection must identify exactly one pending target')
    previous = selected[0]
    if old[0].get('source_id') and old[0]['source_id'] != previous.get('source_id'):
        raise ValueError('Previous review and selection source differ')
    e = old[0].get('evidence') or {}
    selection_binding = e if e.get('selection_sha256') else review
    if selection_binding.get('selection_sha256') != item['previous_selection']['sha256']:
        raise ValueError('Previous pending review does not bind supplied selection')
    for key in ('target_identity', 'target_title'):
        if previous.get(key) != item.get(key):
            raise ValueError('Supplement must preserve original target identity and title')
    if previous.get('target_record_sha256') and previous['target_record_sha256'] != item.get('target_record_sha256'):
        raise ValueError('Target record changed since previous selection')
    return receipts


def select_supplemental_candidates(*, batch, manifest, manifest_sha256, candidates, output, exclusion_manifests=()):
    from .recovery_workflow import _binding, _load_exclusions, _read_json, _items, _text_hashes, _publish
    batch, output = Path(batch).resolve(), Path(output).resolve()
    if output.name != 'selection.json' or not output.is_relative_to(batch):
        raise ValueError('Supplement selection must be selection.json within batch')
    with FileLock(batch / '.lock'):
        _, allowed = _binding(batch, manifest, manifest_sha256, 'supplement')
        excluded, invalid, rejected, exclusions = _load_exclusions(batch, exclusion_manifests)
        payload, binding = _read_json(candidates)
        items, seen, previous_receipts = [], set(), []
        with sqlite3.connect((batch/'queue.sqlite3').as_uri()+'?mode=ro', uri=True) as db:
            for candidate in _items(payload):
                item = copy.deepcopy(candidate)
                target, source, sha = (_sha(item.get(k)) for k in ('target_id', 'source_id', 'sha256'))
                if target not in allowed or target in excluded or target in seen or target == source:
                    raise ValueError('Duplicate, excluded or invalid supplement target')
                if (source, sha) in invalid or (target, source, sha) in rejected:
                    raise ValueError('Supplement candidate is excluded')
                if item.get('original_verified') is not True or not item.get('raw_key'):
                    raise ValueError('Supplement original verification and key required')
                _text_hashes(item)
                for kind, expected in (('target','no_original'), ('source','completed')):
                    row=db.execute('SELECT status,record FROM jobs WHERE id=?',(item[kind+'_id'],)).fetchone()
                    if not row or row[0] != expected:
                        raise ValueError('Supplement '+kind+' status changed')
                    item[kind+'_record_sha256'] = digest(row[1].encode())
                previous_receipts.extend(verify_previous_pending(item, base_dir=Path(candidates).resolve().parent))
                # Resolve provenance once so later freeze is independent of cwd.
                for key in ('previous_review','previous_selection'):
                    p=Path(item[key]['path'])
                    item[key]['path']=str(p if p.is_absolute() else Path(candidates).resolve().parent/p)
                items.append(item);seen.add(target)
        if not items:
            raise ValueError('Supplement candidates must not be empty')
        return _publish(output,dict(schema_version=1,kind='pending_replacement_selection',batch_id=batch.name,
            target_manifest_sha256=manifest_sha256,created_at=datetime.now(timezone.utc).isoformat(),
            candidate_binding=binding,previous_bindings=previous_receipts,exclusion_bindings=exclusions,items=items))
