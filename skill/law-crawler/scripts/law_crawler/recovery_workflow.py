"""Read-only queue selection and immutable, evidence-bound recovery plan preparation."""
import argparse
import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from datetime import datetime, timezone

from .file_lock import FileLock
from .recovery_validation import digest, _sha, validate_plan, validate_repair_sources


def verify_file(path, expected_sha256):
    raw = Path(path).read_bytes()
    if digest(raw) != _sha(expected_sha256):
        raise ValueError(f'Evidence SHA256 mismatch: {path}')
    return raw


def _path(value, base_dir=None):
    path = Path(value)
    return path if path.is_absolute() or base_dir is None else Path(base_dir) / path


def _items(value):
    if isinstance(value, list):
        return value
    for key in ('items', 'selection', 'candidates', 'results'):
        if isinstance(value.get(key), list):
            return value[key]
    raise ValueError('Expected an item list or items/selection/candidates/results array')


def _text_hashes(item):
    pages = item.get('page_texts') or [item.get('first_page_text')]
    if not isinstance(pages, list) or not pages or any(not isinstance(t, str) for t in pages) or not any(t.strip() for t in pages):
        raise ValueError('Complete retained page text required')
    actual = [digest(t.encode('utf-8')) for t in pages]
    declared = item.get('extracted_page_sha256')
    if declared is None:
        if len(pages) != 1:
            raise ValueError('Every retained page requires a declared SHA256')
        declared = [item.get('extracted_first_page_sha256')]
    if actual != declared:
        raise ValueError('Retained page text SHA256 mismatch')
    if item.get('first_page_text') is not None:
        if digest(item['first_page_text'].encode()) != _sha(item.get('extracted_first_page_sha256')):
            raise ValueError('First page text SHA256 mismatch')
    return actual


def verify_review_evidence(repair, *, base_dir=None):
    """Verify offline review provenance only; never inspect or mutate queue status."""
    if base_dir is None and repair.get('review_binding', {}).get('path'):
        base_dir = Path(repair['review_binding']['path']).parent
    e = repair.get('evidence') or {}
    approved = repair.get('approved_primary_sha256s')
    if not isinstance(approved, list) or not approved or len(set(approved)) != len(approved):
        raise ValueError('Explicit unique approved primary SHA256 list required')
    for sha in approved:
        _sha(sha)
    status = e.get('review_status', '')
    if not isinstance(status, str) or not status or any(word in status.lower() for word in ('pending', 'reject', 'unverified', 'not_verified', 'not_approved', 'mismatch', 'unmatched', 'not_match')) or not any(word in status.lower() for word in ('approved', 'verified', 'match')):
        raise ValueError('Approved independent identity review required')
    if e.get('original_sha256') is not None and e['original_sha256'] not in approved:
        raise ValueError('Review original SHA256 is not an approved original')
    identity = e.get('target_identity')
    if not isinstance(identity, (list, dict)) or len(identity) < 3 or not all(identity.values() if isinstance(identity, dict) else identity):
        raise ValueError('Complete type, number and year identity required')
    if not e.get('target_title') or not e.get('review_note'):
        raise ValueError('Complete subject and independent review rationale required')
    selection_path = _path(e['selection_path'], base_dir)
    raw = verify_file(selection_path, e['selection_sha256'])
    matches = [x for x in _items(json.loads(raw)) if x.get('target_id') == repair['target_id'] and x.get('source_id') == repair['source_id'] and x.get('sha256') in approved]
    if {x.get('sha256') for x in matches} != set(approved):
        raise ValueError('Selection does not bind all approved source originals')
    receipts = [{'path': str(selection_path), 'sha256': digest(raw)}]
    visual = []
    for key in ('pages', 'evidence_pages', 'additional_visual_pages'):
        for page in e.get(key, []) or []:
            if not isinstance(page, dict):
                continue
            path = page.get('path') or page.get('image_path')
            sha = page.get('sha256') or page.get('image_sha256')
            if path:
                image = verify_file(_path(path, base_dir), sha)
                visual.append(digest(image))
                receipts.append({'path': str(_path(path, base_dir)), 'sha256': digest(image)})
            elif sha:
                raise ValueError('Page SHA256 has no evidence path')
    if visual:
        viewed = e.get('viewed_image_sha256s') or []
        if not set(visual).issubset(set(viewed)):
            raise ValueError('Actual viewed image SHA256 missing')
    for x in matches:
        if 'previous_review' in x or 'previous_selection' in x:
            from .recovery_supplement import verify_previous_pending
            receipts.extend(verify_previous_pending(x, base_dir=selection_path.parent))
        if x.get('original_verified') is not True:
            raise ValueError('Selection original was not verified')
        if e.get('original_verified') is not True or e.get('original_sha256') != x['sha256'] or not e.get('original_key') or e['original_key'] != x.get('raw_key'):
            raise ValueError('Review original verification/key/SHA differs from selection')
        if x.get('target_identity') != identity or x.get('target_title') != e['target_title']:
            raise ValueError('Review identity differs from frozen selection')
        if x.get('first_page_text') or x.get('page_texts'):
            hashes = _text_hashes(x)
            if not visual and e.get('extracted_page_sha256') != hashes:
                raise ValueError('Review does not bind all retained text pages')
        elif not visual:
            raise ValueError('Neither retained text nor viewed page evidence exists')
    for check in e.get('supporting_checks', []) or []:
        if not isinstance(check, dict) or not check.get('path') or not check.get('sha256'):
            raise ValueError('Supporting evidence requires path and SHA256')
        raw = verify_file(_path(check['path'], base_dir), check['sha256'])
        receipts.append({'path': str(_path(check['path'], base_dir)), 'sha256': digest(raw)})
    from .recovery_adjudication import validate_pending_resolution
    receipts.extend(validate_pending_resolution(repair, base_dir=base_dir))
    from .recovery_primary import verify_primary_evidence
    receipts.extend(verify_primary_evidence(repair, base_dir=base_dir))
    return {'verified': True, 'files': receipts, 'approved_primary_sha256s': approved}


def _read_json(path):
    raw = Path(path).read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = [json.loads(line) for line in raw.splitlines() if line.strip()]
    return value, {'path': str(Path(path).resolve()), 'sha256': digest(raw)}


def _scoped_pair(node, *, base_dir=None):
    """Reject only this candidate relation, never the valid source original globally."""
    if node.get('scope') != 'candidate_pair':
        raise ValueError('Unknown exclusion scope; expected candidate_pair')
    triple = tuple(_sha(node.get(k)) for k in ('target_id', 'source_id', 'sha256'))
    if triple[0] == triple[1]:
        raise ValueError('Rejected candidate target and source must differ')
    if not isinstance(node.get('reason'), str) or not node['reason'].strip():
        raise ValueError('Rejected candidate pair requires a reason')
    if not isinstance(node.get('review_path'), str) or not node['review_path']:
        raise ValueError('Rejected candidate review path required')
    review = json.loads(verify_file(_path(node['review_path'], base_dir), node.get('review_sha256')))
    matched = []
    for entry in review.get('pending', []) + review.get('rejections', []):
        evidence = entry.get('evidence') or {}
        observed = (entry.get('target_id'), entry.get('source_id'), entry.get('sha256') or evidence.get('original_sha256'))
        decision = entry.get('decision') or evidence.get('source_classification_observation', {}).get('decision')
        if observed == triple and decision == 'reject_target_source_sha_pair_only':
            matched.append(entry)
    if len(matched) != 1 or any(entry.get('target_id') == triple[0] and entry.get('source_id') == triple[1] and triple[2] in entry.get('approved_primary_sha256s', []) for entry in review.get('repairs', [])):
        raise ValueError('Review must explicitly reject exactly this candidate pair without approving it')
    return triple


def load_rejected_candidate_pairs(batch):
    """Read the dedicated scoped ledger without interpreting it as global exclusions."""
    batch = Path(batch)
    path = batch / 'recovery' / 'rejected-candidate-pairs.json'
    if not path.exists():
        return set(), []
    value, receipt = _read_json(path)
    if (not isinstance(value, dict) or type(value.get('schema_version')) is not int or value.get('schema_version') != 1 or
            value.get('batch_id') != batch.name or not isinstance(value.get('rejections'), list)):
        raise ValueError('Rejected candidate ledger schema or batch mismatch')
    triples, receipts = set(), [receipt]
    for node in value['rejections']:
        if not isinstance(node, dict):
            raise ValueError('Rejected candidate ledger entry must be an object')
        triples.add(_scoped_pair(node, base_dir=path.parent))
        if node.get('review_path'):
            receipts.append({'path': str(_path(node['review_path'], path.parent)),
                             'sha256': node['review_sha256']})
    return triples, receipts


def _exclusions(value, *, base_dir=None):
    targets, pairs, rejected = set(), set(), set()
    def walk(node):
        if isinstance(node, list):
            for x in node: walk(x)
        elif isinstance(node, dict):
            if 'scope' in node:
                rejected.add(_scoped_pair(node, base_dir=base_dir))
                return  # Its target/source fields must not widen the rejection.
            if node.get('target_id'): targets.add(node['target_id'])
            targets.update(node.get('target_ids') or [])
            if node.get('source_id') and node.get('sha256'):
                pairs.add((node['source_id'], node['sha256']))
            for child in node.values():
                if isinstance(child, (dict, list)): walk(child)
    walk(value)
    return targets, pairs, rejected


def _load_exclusions(batch, paths=(), dirs=(), selections=False):
    files = {Path(p).resolve() for p in paths}
    ledger = batch / 'recovery' / 'invalid-source-files.json'
    if ledger.exists(): files.add(ledger.resolve())
    selection_files = set()
    if selections:
        for directory in (batch, *map(Path, dirs)):
            if not directory.is_dir(): raise ValueError(f'Exclusion directory missing: {directory}')
            for root, child_dirs, names in os.walk(directory):
                child_dirs[:] = [name for name in child_dirs if name not in {'staging', 'outcomes', '.git', '__pycache__'}]
                if 'selection.json' in names:
                    selection_files.add((Path(root)/'selection.json').resolve())
    files.update(selection_files)
    rejected, receipts = load_rejected_candidate_pairs(batch)
    pair_ledger = (batch / 'recovery' / 'rejected-candidate-pairs.json').resolve()
    targets, pairs = set(), set()
    for path in sorted(files):
        if path == pair_ledger:
            continue  # Already read with strict schema/batch validation above.
        value, receipt = _read_json(path)
        if path in selection_files or path.name == 'selection.json':
            # Prior selections reserve target work only. A source may legally
            # provide the same original for many distinct catalogue targets.
            targets.update(x['target_id'] for x in _items(value) if x.get('target_id'))
        else:
            ids, sources, scoped = _exclusions(value, base_dir=path.parent)
            if path == ledger.resolve():
                if scoped:
                    raise ValueError('Candidate-pair rejection belongs in its dedicated ledger')
            else:
                targets.update(ids)
            pairs.update(sources); rejected.update(scoped)
        receipts.append(receipt)
    return targets, pairs, rejected, receipts


def _publish(output, value):
    output = Path(output)
    sidecar = output.with_suffix('.sha256')
    if output == sidecar or output.exists() or sidecar.exists():
        raise ValueError(f'Immutable output already exists or invalid suffix: {output}')
    output.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
    # Exclusive creation never replaces an existing artifact. A sidecar failure leaves
    # an explicitly unusable JSON artifact rather than silently overwriting evidence.
    temporary = []
    try:
        for destination, data in ((output, raw), (sidecar, (digest(raw)+'\n').encode())):
            with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as stream:
                temporary.append(Path(stream.name))
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.link(temporary[-1], destination)  # Atomic create, never replace.
    finally:
        for path in temporary: path.unlink(missing_ok=True)
    return {'path': str(output), 'sha256': digest(raw), 'count': len(value.get('repairs', value.get('items', [])))}


def _binding(batch, manifest, manifest_sha256, run_id):
    plan = dict(batch_id=batch.name, run_id=run_id, target_manifest_sha256=_sha(manifest_sha256), repairs=[])
    allowed = validate_plan(batch, plan, manifest)
    return plan, allowed


def select_candidates(*, batch, manifest, candidates, output, limit, exclude_review_dirs=(), exclusion_manifests=(), manifest_sha256):
    batch = Path(batch).resolve()
    if limit < 1: raise ValueError('Selection limit must be positive')
    output = Path(output).resolve()
    roots = (batch, *[Path(p).resolve() for p in exclude_review_dirs])
    if output.name != 'selection.json' or not any(output.is_relative_to(root) for root in roots):
        raise ValueError('Selection output must be selection.json inside batch or an explicit review directory')
    with FileLock(batch / '.lock'):
        plan, allowed = _binding(batch, manifest, manifest_sha256, 'selection')
        excluded, pairs, rejected, bindings = _load_exclusions(batch, exclusion_manifests, exclude_review_dirs, True)
        registry = batch / 'recovery' / 'selection-registry'
        if registry.exists():
            for entry in sorted(registry.glob('*.json')):
                reservation = json.loads(entry.read_bytes())
                if reservation.get('state') != 'published':
                    raise ValueError(f'Unfinished selection reservation requires explicit inspection: {entry}')
                verify_file(reservation['path'], reservation['sha256'])
                excluded.update(reservation['target_ids'])
        pool, candidate_binding = _read_json(candidates)
        chosen, seen = [], set()
        with sqlite3.connect((batch/'queue.sqlite3').as_uri()+'?mode=ro', uri=True) as db:
            for x in _items(pool):
                target = x.get('target_id')
                if (target not in allowed or target in excluded or target in seen or
                        (x.get('source_id'), x.get('sha256')) in pairs or
                        (target, x.get('source_id'), x.get('sha256')) in rejected): continue
                row = db.execute('SELECT status,record FROM jobs WHERE id=?', (target,)).fetchone()
                if not row or row[0] != 'no_original': continue
                if x.get('original_verified') is not True: raise ValueError(f'Unverified original for {target}')
                _sha(x.get('sha256')); _sha(x.get('source_id')); _text_hashes(x)
                selected = copy.deepcopy(x)
                selected['target_record_sha256'] = digest(row[1].encode())
                source = db.execute('SELECT status,record FROM jobs WHERE id=?',(x['source_id'],)).fetchone()
                if not source or source[0] != 'completed': raise ValueError('Candidate source is not completed')
                selected['source_record_sha256'] = digest(source[1].encode())
                chosen.append(selected); seen.add(target)
                if len(chosen) == limit: break
        value = dict(batch_id=batch.name, target_manifest_sha256=manifest_sha256,
            created_at=datetime.now(timezone.utc).isoformat(), candidate_binding=candidate_binding,
            exclusion_bindings=bindings, requested_limit=limit, items=chosen)
        if output.exists() or output.with_suffix('.sha256').exists():
            raise ValueError(f'Immutable selection already exists: {output}')
        registry.mkdir(parents=True, exist_ok=True)
        entry = registry / (digest(str(output).encode()) + '.json')
        reservation = dict(state='reserved', path=str(output), target_ids=[x['target_id'] for x in chosen])
        with entry.open('x') as stream:
            json.dump(reservation, stream); stream.flush(); os.fsync(stream.fileno())
        result = _publish(output, value)
        reservation.update(state='published', sha256=result['sha256'])
        with tempfile.NamedTemporaryFile(dir=registry, mode='w', delete=False) as stream:
            json.dump(reservation, stream); stream.flush(); os.fsync(stream.fileno()); temporary = Path(stream.name)
        os.replace(temporary, entry)
        return result


def freeze_reviews(*, batch, manifest, reviews, output, run_id, exclusion_manifests=(), manifest_sha256):
    batch = Path(batch).resolve()
    with FileLock(batch / '.lock'):
        plan, allowed = _binding(batch, manifest, manifest_sha256, run_id)
        excluded, pairs, rejected, bindings = _load_exclusions(batch, exclusion_manifests)
        plan.update(review_bindings=[], exclusion_bindings=bindings, skipped=[])
        seen = set()
        loaded_reviews = []
        pending_targets = set()
        for path, sha in reviews:
            path = Path(path).resolve()
            review = json.loads(verify_file(path, sha))
            pending_targets.update(x.get('target_id') for x in review.get('pending', []))
            loaded_reviews.append((path, sha, review))
        with sqlite3.connect((batch/'queue.sqlite3').as_uri()+'?mode=ro', uri=True) as db:
            for path, sha, review in loaded_reviews:
                binding = {'path': str(path), 'sha256': sha}
                plan['review_bindings'].append(binding)
                repairs = review.get('repairs')
                if not isinstance(repairs, list): raise ValueError('Review repairs array required')
                pending = {x.get('target_id') for x in review.get('pending', [])}
                for original in repairs:
                    item = copy.deepcopy(original); target = item['target_id']
                    if target in seen or target in pending or target in pending_targets: raise ValueError(f'Duplicate/conflicting review target: {target}')
                    seen.add(target)
                    if target not in allowed or target in excluded: raise ValueError(f'Excluded or out-of-manifest target: {target}')
                    if any((target, item.get('source_id'), sha) in rejected for sha in item.get('approved_primary_sha256s', [])):
                        raise ValueError('Rejected candidate pair cannot enter a recovery plan')
                    verify_review_evidence(item, base_dir=path.parent)
                    selection = json.loads(verify_file(_path(item['evidence']['selection_path'],path.parent),item['evidence']['selection_sha256']))
                    selected = [x for x in _items(selection) if x.get('target_id') == target and x.get('source_id') == item['source_id']]
                    item['review_binding'] = binding
                    row = db.execute('SELECT status,record FROM jobs WHERE id=?', (target,)).fetchone()
                    if row and row[0] == 'completed':
                        plan['skipped'].append({'target_id': target, 'reason': 'already_completed', 'review_binding': binding}); continue
                    if not row or row[0] != 'no_original': raise ValueError(f'Target is not current no_original: {target}')
                    for kind, expected in (('target', 'no_original'), ('source', 'completed')):
                        key = item[kind+'_id']
                        row = db.execute('SELECT status,record FROM jobs WHERE id=?', (key,)).fetchone()
                        raw = (batch/'outcomes'/(key+'.json')).read_bytes(); outcome = json.loads(raw)
                        if not row or row[0] != expected or outcome.get('status') != expected: raise ValueError(f'{kind} status mismatch: {key}')
                        item[kind+'_record_sha256'] = digest(row[1].encode())
                        for snapshot in selected:
                            old_sha = snapshot.get(kind+'_record_sha256')
                            if old_sha is not None and old_sha != item[kind+'_record_sha256']:
                                raise ValueError('Record changed since selection; independent review must be renewed')
                        item[kind+'_outcome_sha256'] = digest(raw)
                        if kind == 'source':
                            if outcome.get('oss_metadata', {}).get('status') not in ('uploaded_verified', 'reused_verified'): raise ValueError('Source metadata not verified')
                            roles = outcome.get('metadata', {}).get('file_roles', {})
                            if item.get('replacement_primary'):
                                from .recovery_primary import verify_primary_evidence
                                verify_primary_evidence(item, outcome, base_dir=path.parent)
                            for sha in item['approved_primary_sha256s']:
                                if (key, sha) in pairs: raise ValueError('Approved original is in exclusion ledger')
                                if item.get('replacement_primary'):
                                    continue  # Explicit independently archived body; source stays frozen unchanged.
                                files = [f for f in outcome.get('files', []) if f.get('sha256') == sha and roles.get(f.get('url'), {}).get('role') == 'regulation_body' and f.get('oss', {}).get('status') in ('uploaded_verified', 'reused_verified')]
                                if not files: raise ValueError('Approved original missing verified body receipt')
                                if len(files) != 1 or not isinstance(files[0].get('size_bytes'), int) or files[0]['size_bytes'] <= 0 or files[0].get('complete') is not True or files[0]['oss'].get('raw_key') != item['evidence']['original_key']:
                                    raise ValueError('Source body receipt ambiguous, incomplete, or key/size mismatch')
                    validate_repair_sources(batch, item)
                    plan['repairs'].append(item)
        validate_plan(batch, plan, manifest)
        return _publish(output, plan)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('select', 'supplement', 'freeze'):
        sub = commands.add_parser(name)
        for flag in ('batch', 'manifest', 'manifest-sha256', 'output'):
            sub.add_argument('--'+flag, required=True)
        sub.add_argument('--exclusion-manifest', action='append', default=[], help='JSON/JSONL target or source+SHA exclusions; digest recorded')
        if name == 'select':
            sub.add_argument('--candidates', required=True)
            sub.add_argument('--limit', type=int, required=True)
            sub.add_argument('--exclude-review-dir', action='append', default=[])
        elif name == 'supplement':
            sub.add_argument('--candidates', required=True)
        else:
            sub.add_argument('--run-id', required=True)
            sub.add_argument('--review', action='append', required=True, help='Immutable review PATH=SHA256')
    args = vars(parser.parse_args(argv)); command = args.pop('command')
    args['exclusion_manifests'] = args.pop('exclusion_manifest')
    try:
        if command == 'select':
            args['exclude_review_dirs'] = args.pop('exclude_review_dir')
            result = select_candidates(**args)
        elif command == 'supplement':
            from .recovery_supplement import select_supplemental_candidates
            result = select_supplemental_candidates(**args)
        else:
            args['reviews'] = [tuple(value.rsplit('=', 1)) for value in args.pop('review')]
            if any(len(pair) != 2 for pair in args['reviews']): raise ValueError('--review requires PATH=SHA256')
            result = freeze_reviews(**args)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        parser.exit(2, f'Recovery workflow refused: {exc}\n')


if __name__ == '__main__':
    main()
