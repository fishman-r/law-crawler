#!/usr/bin/env python3
"""为旧批次补充轻量下载摘要；不修改历史计划、状态或原件。"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


def refresh(directory):
    from law_crawler.bulk_download_fast import progress_summary
    from law_crawler.crawl_store import write_json
    from law_crawler.raw_capture import _safe
    directory = _safe(Path(directory).absolute())
    status_path = _safe(directory / 'fast-download/status.json')
    status = json.loads(status_path.read_text())
    if status.get('phase') not in ('finished', 'stopped'):
        raise ValueError('仅为已结束或已停止的旧批次补摘要；运行批次由下载器生成')
    run_id = status.get('run_id', '')
    import re
    if not re.fullmatch(r'[a-f0-9]{32}', run_id):
        raise ValueError('运行ID不合法')
    policy = json.loads(_safe(directory / 'fast-download/runs' / (run_id + '.json')).read_text())
    state_path = _safe(directory / 'state.json')
    state = json.loads(state_path.read_text())
    updated = datetime.fromtimestamp(status_path.stat().st_mtime, timezone.utc).isoformat()
    summary = progress_summary(state, policy['workers'], updated, phase=status.get('phase'), run_started_at=policy.get('created_at'))
    if status.get('phase') == 'finished':
        counts = json.loads(_safe(directory / 'report.json').read_text())['counts']
        for field, original in [('total_details','details_discovered'), ('attempted_details','details_attempted'),
                                ('successful_files','unique_original_sha256')]:
            if summary[field] != counts[original]:
                raise ValueError('摘要与最终报告不一致: ' + field)
    summary['snapshot_recorded_at'] = datetime.now(timezone.utc).isoformat()
    write_json(directory / 'fast-download/progress.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-dir', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skill/law-crawler/scripts'))
    print(json.dumps(refresh(args.batch_dir), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
