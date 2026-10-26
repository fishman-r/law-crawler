#!/usr/bin/env python3
"""Publish a credential-free census snapshot without interrupting downloads."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import grp
import json
import os
from pathlib import Path
import sqlite3
import time


def category(status, reason):
    if status == 'no_original':
        if '身份冲突' in reason:
            return '原件身份冲突：待取得正确文件'
        if '财政部法律书目' in reason:
            return '法律书目：官方未提供电子全文'
        if 'official_download_filename_empty' in reason or '下载入口文件名为空' in reason:
            return '官方按钮缺少文件名'
        if 'official_download_link_absent' in reason or '未提供可识别的下载链接' in reason:
            return '官方详情缺少下载入口'
        if 'official_primary_document_unavailable' in reason or 'official_document_not_yet_available' in reason:
            return '官方明确尚未提供原件'
        if 'official_catalogue_file_fields_empty' in reason:
            return '官方目录文件字段为空'
        return '未发现原件链接'
    value = reason.lower()
    for needles, label in [
        (('403', 'forbidden'), 'HTTP 403：来源拒绝访问'),
        (('401', 'accessdenied', '授权'), '访问或OSS授权失败'),
        (('429',), 'HTTP 429：来源限流'),
        (('404',), 'HTTP 404：文件不存在'),
        (('ssl', 'tls', 'certificate'), 'TLS或证书错误'),
        (('timeout', 'timed out', '超时'), '网络请求超时'),
        (('exceed', 'too large', '超限', '超过', 'size limit'), '文件体积超限'),
        (('challenge', 'captcha', '挑战'), '来源访问验证页'),
        (('非pdf', '格式', 'content-type'), '文件格式不符'),
        (('oss',), 'OSS保存或校验失败'),
    ]:
        if any(token in value for token in needles):
            return label
    return '未发现原件链接' if status == 'no_original' else '其他下载失败或不完整（待核验）'


class Publisher:
    def __init__(self, batch, destination):
        self.batch, self.destination = Path(batch), Path(destination)
        self.cache = {}

    def snapshot(self):
        source = json.loads((self.batch / 'status.json').read_text())
        # Explicit allow-list: never publish original URLs, catalogue payloads or errors.
        keys = ('batch_id', 'status', 'updated_at', 'started_at', 'total', 'pending',
                'downloading', 'completed', 'failed', 'incomplete', 'no_original',
                'attempted', 'unique_oss_files', 'oss_bytes', 'workers',
                'elapsed_this_run_seconds', 'bucket', 'prefix')
        result = {key: source.get(key) for key in keys}
        reasons = Counter()
        with sqlite3.connect((self.batch / 'queue.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=3) as db:
            db.execute('BEGIN')
            counts = dict(db.execute('SELECT status,count(*) FROM jobs GROUP BY status'))
            latest = db.execute('SELECT max(updated_at) FROM jobs').fetchone()[0]
            files, size = db.execute('SELECT count(*),coalesce(sum(bytes),0) FROM objects').fetchone()
            rows = db.execute("SELECT id,status,reason,updated_at FROM jobs WHERE status IN ('failed','incomplete','no_original')").fetchall()
        result.update(total=sum(counts.values()), pending=counts.get('pending', 0),
                      downloading=counts.get('running', 0), completed=counts.get('completed', 0),
                      failed=counts.get('failed', 0), incomplete=counts.get('incomplete', 0),
                      no_original=counts.get('no_original', 0), unique_oss_files=files, oss_bytes=size,
                      attempted=sum(v for k, v in counts.items() if k not in ('pending', 'running')))
        if latest and (not result.get('updated_at') or datetime.fromisoformat(latest) > datetime.fromisoformat(result['updated_at'])):
            result['updated_at'] = latest
        for key, status, reason, updated in rows:
            cached = self.cache.get(key)
            if not cached or cached[0] != updated:
                parts = [] if reason == '部分下载或OSS保存失败' else [reason or '']
                try:
                    outcome = json.loads((self.batch / 'outcomes' / (key + '.json')).read_text())
                    metadata = outcome.get('metadata')
                    availability = metadata.get('source_availability') if isinstance(metadata, dict) else None
                    diagnosis = availability.get('diagnosis_code') if isinstance(availability, dict) else None
                    if diagnosis in {'official_download_filename_empty', 'official_download_link_absent',
                                     'official_primary_document_unavailable', 'official_catalogue_file_fields_empty'}:
                        parts.append(diagnosis)
                    for item in outcome.get('files', []):
                        if item.get('capture_status') == 'failed':
                            parts.append(str(item.get('reason', '')))
                        if item.get('oss', {}).get('status') == 'failed':
                            parts.append('OSS ' + str(item['oss'].get('reason', '')))
                    if outcome.get('oss_metadata', {}).get('status') == 'failed':
                        parts.append('OSS ' + str(outcome['oss_metadata'].get('reason', '')))
                except (OSError, ValueError, TypeError):
                    pass
                cached = (updated, category(status, ' '.join(parts)))
                self.cache[key] = cached
            reasons[cached[1]] += 1
        result['failure_reasons'] = [dict(reason=k, count=v) for k, v in reasons.most_common()]
        result['failure_reasons_updated_at'] = datetime.now(timezone.utc).isoformat()
        result['failure_reason_basis'] = '任务计数与原因来自同一队列快照；每个失败、不完整或未发现原件的任务只计一个主要原因'
        return result

    def publish(self):
        result = self.snapshot()
        temporary = self.destination.with_suffix('.tmp')
        with temporary.open('w') as stream:
            os.fchmod(stream.fileno(), 0o640)
            if os.geteuid() == 0:
                os.fchown(stream.fileno(), 0, grp.getgrnam('lawcrawler').gr_gid)
            json.dump(result, stream, ensure_ascii=False)
        os.replace(temporary, self.destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    publisher = Publisher(args.batch, args.output)
    while True:
        publisher.publish()
        if args.once:
            return
        time.sleep(10)


if __name__ == '__main__':
    main()
