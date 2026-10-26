"""Deterministic cell assignment from OCR coordinates and explicitly human-confirmed grids."""
from __future__ import annotations
import argparse
import bisect
import copy
import hashlib
import json
import time
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    with Path(path).open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def edges(values):
    if len(values) < 2 or any(not isinstance(v, (float, int)) or not 0 <= v <= 1 for v in values) or any(a >= b for a, b in zip(values, values[1:])):
        raise ValueError('Grid edges must be strictly increasing normalized coordinates')
    return values


def build_table(raw, grid):
    if raw['bbox_coordinate_system'] != 'normalized_top_left_xywh':
        raise ValueError('Requires normalized_top_left_xywh OCR coordinates')
    xs, ys = edges(grid['column_edges']), edges(grid['row_edges'])
    cells = [[[] for _ in xs[:-1]] for _ in ys[:-1]]
    assigned, ambiguities = set(), []
    for index, obs in enumerate(raw['observations']):
        x, y, w, h = obs['bbox']
        cx, cy = x + w / 2, y + h / 2
        intersects = x < xs[-1] and x + w > xs[0] and y < ys[-1] and y + h > ys[0]
        inside = xs[0] <= cx <= xs[-1] and ys[0] <= cy <= ys[-1]
        if inside:
            col = min(bisect.bisect_right(xs, cx) - 1, len(xs) - 2)
            row = min(bisect.bisect_right(ys, cy) - 1, len(ys) - 2)
            crosses = x < xs[col] or x + w > xs[col + 1] or y < ys[row] or y + h > ys[row + 1]
            cells[row][col].append({'observation_index': index, **obs, 'crosses_cell_boundary': crosses})
            assigned.add(index)
            if crosses:
                ambiguities.append({'observation_index': index, 'assigned_cell': [row, col], 'reason': 'bbox_crosses_cell_boundary'})
        elif intersects:
            ambiguities.append({'observation_index': index, 'assigned_cell': None, 'reason': 'intersects_table_center_outside_preserved_in_text'})
    rows, audit = [], []
    for row, group in enumerate(cells):
        row_text = []
        for col, observations in enumerate(group):
            observations.sort(key=lambda obs: (obs['bbox'][1], obs['bbox'][0], obs['observation_index']))
            content = '<br>'.join(obs['text'].replace('|', '\\|').replace('\n', '<br>') for obs in observations) or '[MISSING: visual review required]'
            if any(obs['crosses_cell_boundary'] for obs in observations):
                content += ' [AMBIGUOUS BBOX: visual review required]'
            row_text.append(content)
            audit.append({'row': row, 'column': col, 'bbox': [xs[col], ys[row], xs[col+1]-xs[col], ys[row+1]-ys[row]],
                          'observations': observations, 'missing': not observations, 'text': content})
        rows.append('| ' + ' | '.join(row_text) + ' |')
    table = '\n'.join([rows[0], '| ' + ' | '.join('---' for _ in xs[:-1]) + ' |', *rows[1:]])
    text, inserted = [], False
    for index, obs in enumerate(raw['observations']):
        if index in assigned:
            if not inserted:
                text.extend(['[Table reconstructed from OCR and human-confirmed grid; all cells require independent visual review]', table])
                inserted = True
        else:
            text.append(obs['text'])
    if not inserted:
        text.extend(['[Table region had no recognized observations; visual review required]', table])
    if ambiguities:
        text.append('[Table boundary ambiguities are preserved in cell-audit.json; independent visual review required.]')
    return '\n'.join(text), {'cells': audit, 'ambiguities': ambiguities, 'assigned_observation_indices': sorted(assigned),
                               'outside_observation_indices': [i for i in range(len(raw['observations'])) if i not in assigned]}


def reconstruct(sidecar, grid_path, output):
    start = time.monotonic()
    sidecar, grid_path, output = Path(sidecar).resolve(), Path(grid_path).resolve(), Path(output).resolve()
    source = json.loads(sidecar.read_text())
    config = json.loads(grid_path.read_text())
    if config.get('grid_source') != 'human_confirmed':
        raise ValueError('grid_source must explicitly be human_confirmed')
    grids = config['tables']
    if len({g['page'] for g in grids}) != len(grids):
        raise ValueError('Only one table grid per page is supported; split/review more complex layouts separately')
    sources = {'sidecar_sha256': digest(sidecar), 'grid_sha256': digest(grid_path), 'implementation_sha256': digest(__file__), 'raw_ocr': []}
    prepared = []
    for grid in grids:
        page = next((p for p in source['pages'] if p['page'] == grid['page']), None)
        if page is None:
            raise ValueError('Configured page is absent from sidecar')
        path = Path(page['raw_ocr_path'])
        if not path.is_absolute():
            path = sidecar.parent / path
        raw = json.loads(path.read_text())
        if raw.get('pdf_sha256') != source['pdf_sha256'] or raw.get('page') != grid['page']:
            raise ValueError('Raw OCR PDF/page binding mismatch')
        image_path = Path(page['image_path'])
        if not image_path.is_absolute():
            image_path = sidecar.parent / image_path
        if digest(image_path) != raw['image_sha256']:
            raise ValueError('Raw OCR image binding mismatch')
        sources['raw_ocr'].append({'page': grid['page'], 'path': str(path), 'sha256': digest(path), 'image_sha256': raw['image_sha256']})
        prepared.append((page, raw, grid))
    if output.exists():
        receipt = json.loads((output / 'receipt.json').read_text())
        if receipt['sources'] != sources:
            raise ValueError('Existing output is bound to different inputs; choose a new directory')
        for artifact in receipt['artifacts']:
            if digest(output / artifact['path']) != artifact['sha256']:
                raise ValueError('Cached artifact changed')
        return {**receipt, 'reused': True}
    result = copy.deepcopy(source)
    audits = []
    for page, raw, grid in prepared:
        text, audit = build_table(raw, grid)
        target = next(p for p in result['pages'] if p['page'] == page['page'])
        target['text'] = text
        target['evidence'] += '; deterministic cell grouping using human-confirmed grid; missing and ambiguous OCR retained; independent visual review required'
        audit.update({'page': page['page'], 'grid': grid})
        audits.append(audit)
    result['table_structure_status'] = 'human_grid_ocr_assignment_needs_independent_visual_review'
    result['table_reconstruction'] = {'sources': sources, 'duration_seconds': time.monotonic() - start}
    result['review_status'] = 'needs_independent_visual_review'
    output.mkdir(parents=True)
    save(output / 'transcription.json', result)
    save(output / 'cell-audit.json', {'sources': sources, 'tables': audits})
    save(output / 'grid.json', config)
    receipt = {'status': 'completed_needs_visual_review', 'sources': sources, 'duration_seconds': time.monotonic()-start,
               'transcription_path': str(output / 'transcription.json'), 'reused': False,
               'artifacts': [{'path': name, 'sha256': digest(output / name)} for name in ('transcription.json', 'cell-audit.json', 'grid.json')]}
    save(output / 'receipt.json', receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sidecar', required=True)
    parser.add_argument('--grid', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    print(json.dumps(reconstruct(args.sidecar, args.grid, args.output), ensure_ascii=False, indent=2))
    return 0
