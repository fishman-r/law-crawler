"""显式导入人工正文身份审计；保留原文件，冲突文件退出当前清单。"""
import argparse
import hashlib
import json
from pathlib import Path

from law_crawler.crawl_store import CrawlStore, now


def import_body_reviews(store, audit, audit_path, root, reviewed_at=None):
    """先校验整个输入再登记；audit 的状态必须由审核者提供，不自动判定正文。"""
    if not isinstance(audit, dict) or not isinstance(audit.get('files'), list):
        raise ValueError('审核 JSON 必须包含 files 列表')
    if not isinstance(audit.get('method'), str) or not audit['method'].strip():
        raise ValueError('审核 JSON 必须包含明确 method')
    root = Path(root).resolve()
    timestamp = reviewed_at or now()
    prepared = []
    seen = set()
    allowed = {'title_type_number_year_match', 'unknown_no_extractable_text_first_two_pages',
               'catalogue_body_type_conflict', 'not_reviewed'}
    for item in audit['files']:
        if not isinstance(item, dict) or item.get('text_review') not in allowed:
            raise ValueError('每份审核须给出受支持的显式 text_review 状态')
        if not isinstance(item.get('file'), str) or not isinstance(item.get('reason'), str) or not item['reason'].strip():
            raise ValueError('每份审核须给出 file 与 reason')
        path = Path(item['file'])
        path = (path if path.is_absolute() else root / path).resolve()
        if not path.is_relative_to(store.directory):
            raise ValueError('审核文件越出指定下载目录')
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        record = store.by_hash(sha)
        if not record or not store.valid(record):
            raise ValueError('审核文件原字节 SHA 未登记或本地文件无效')
        original_path = store._path({'file':record.get('original_file', record['file'])})
        if path != original_path:
            raise ValueError('审核路径不是此记录的原始文件')
        if sha in seen:
            raise ValueError('审核输入重复引用同一原字节 SHA')
        seen.add(sha)
        evidence = dict(audit_file=str(Path(audit_path).resolve()), method=audit['method'],
            original_sha256=sha, original_file=str(path), assessment=item,
            time_basis='explicit_review_time' if reviewed_at else 'review_registration_time')
        prepared.append((sha, dict(status=item['text_review'], reason=item['reason'],
                                  evidence=evidence, reviewed_at=timestamp)))
    # 在任何审核写入前确认时间格式，避免后项输入错误导致部分批次登记。
    from datetime import datetime
    try:
        parsed = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('reviewed_at 缺少时区')
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError('reviewed_at 必须是带时区的 ISO 时间') from exc
    return [store.register_body_review(sha, review) for sha, review in prepared]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path, help='国家下载目录')
    parser.add_argument('--audit', required=True, type=Path, help='人工审计 JSON')
    parser.add_argument('--root', required=True, type=Path, help='审计文件相对路径的工作区根目录')
    parser.add_argument('--country', required=True, help='重建当前清单的国家名称')
    parser.add_argument('--reviewed-at', help='可选：人工审核的带时区 ISO 时间；默认登记时间并注明依据')
    args = parser.parse_args(argv)
    audit = json.loads(args.audit.read_text(encoding='utf-8'))
    with CrawlStore(args.directory) as store:
        records = import_body_reviews(store, audit, args.audit, args.root, args.reviewed_at)
        current = store.update_current_selection(args.country)
    counts = {}
    for record in records:
        status = record['body_review']['status']
        counts[status] = counts.get(status, 0) + 1
    print(json.dumps(dict(reviewed_files=len(records), statuses=counts,
                          current_candidate_count=len(current)), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
