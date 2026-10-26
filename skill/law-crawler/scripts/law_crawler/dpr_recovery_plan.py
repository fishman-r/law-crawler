"""读取冻结队列生成 DPR 替代官方来源计划，不写生产队列、不重复下载原件。"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sqlite3

from .platform_adapters.census_dpr import extract_dpr_evidence, identity, strict_match


def build_plan(batch):
    batch = Path(batch)
    conn = sqlite3.connect((batch / 'queue.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
    candidates = defaultdict(list)
    targets = []
    for key, platform, status, raw in conn.execute('SELECT id,platform,status,record FROM jobs'):
        record = json.loads(raw)
        if platform == 'dpr.go.id' and status == 'no_original':
            targets.append((key, record))
        elif platform != 'dpr.go.id' and identity(record):
            candidates[identity(record)].append((key, platform, status, record))
    conn.close()
    counts = Counter()
    decisions = []
    for key, record in targets:
        htmls = sorted((batch / 'staging' / key / 'objects').glob('*.html'))
        if not htmls:
            counts['missing_archived_detail'] += 1
            decisions.append(dict(detail_key=key, reason='missing_archived_detail', alternatives=[]))
            continue
        # URL receipt identifies the archived detail, never use an arbitrary linked HTML file.
        index = batch / 'staging' / key / 'index' / (key + '.json')
        receipt = json.loads(index.read_text()) if index.exists() else {}
        sha = receipt.get('sha256')
        htmls = [p for p in htmls if p.stem == sha]
        if len(htmls) != 1:
            counts['ambiguous_archived_detail'] += 1
            decisions.append(dict(detail_key=key, reason='ambiguous_archived_detail', alternatives=[]))
            continue
        evidence = extract_dpr_evidence(record, htmls[0].read_text(errors='replace'))
        evidence['archived_detail_sha256'] = sha
        evidence['archived_detail_url'] = record['url']
        alternatives = []
        for other_key, platform, status, other in candidates.get(evidence['identity'], []):
            if not strict_match(record, evidence, other):
                continue
            outcome_path = batch / 'outcomes' / (other_key + '.json')
            outcome = json.loads(outcome_path.read_text()) if outcome_path.exists() else {}
            roles = outcome.get('metadata', {}).get('file_roles', {})
            verified = []
            for f in outcome.get('files', []):
                oss = f.get('oss') or {}
                if oss.get('status') not in ('uploaded_verified', 'reused_verified'):
                    continue
                # Only an explicitly identified primary text may satisfy a missing original.
                if roles.get(f.get('url'), {}).get('role') != 'regulation_body':
                    continue
                verified.append(f)
            alternatives.append(dict(detail_key=other_key, platform=platform, detail_url=other['url'],
                status=status, catalogue_record=other, verified_primary_files=verified,
                match_basis='same_national_type_number_year_and_normalized_subject_title',
                source_relation='alternative_official_source_not_dpr_hosted_file'))
        counts[evidence['reason']] += 1
        counts['strict_matched' if alternatives else 'no_strict_match'] += 1
        if any(a['verified_primary_files'] for a in alternatives):
            counts['with_verified_oss_primary'] += 1
        decisions.append(dict(detail_key=key, detail_url=record['url'], evidence=evidence,
                              reason=evidence['reason'], alternatives=alternatives))
    return dict(schema_version=1, scope='DPR no_original records only; alternate sources retain provenance',
                total=len(targets), counts=dict(counts), decisions=decisions)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch', required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    plan = build_plan(args.batch)
    Path(args.output).write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    print(json.dumps(dict(total=plan['total'], counts=plan['counts']), ensure_ascii=False))


if __name__ == '__main__':
    main()
