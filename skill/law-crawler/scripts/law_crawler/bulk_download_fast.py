"""只续跑已冻结目录的原始下载：隔离detail shard，单writer合并，默认2并发。"""
import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
import json
from pathlib import Path
import threading
import time
import uuid
from urllib.parse import urlsplit, parse_qsl

from law_crawler import bulk_archive as bulk
from law_crawler.download_metrics import progress_summary
from law_crawler.batch_runtime import safe_id, timestamp
from law_crawler.crawl_store import write_json
from law_crawler.download_regulations import country_download_dir
from law_crawler.file_lock import FileLock
from law_crawler.jurisdiction_registry import resolve_jurisdiction
from law_crawler.raw_capture import capture, deferred_receipt, _safe, _url
from law_crawler.download_transport import retry_after_seconds, MAX_AUTOMATIC_WAIT, RetryDeferred
from law_crawler.resources import configuration_path


class HostPace:
    def __init__(self, delay, stop, max_bytes=None, refresh_failed_details=False):
        self.max_bytes = max_bytes
        self.refresh_failed_details = refresh_failed_details
        self.delay, self.stop, self.mutex, self.next = delay, stop, threading.Lock(), {}

    def capture(self, url, raw, **options):
        if self.max_bytes is not None:
            options['max_bytes'] = self.max_bytes
        host = urlsplit(url).hostname
        while True:
            with self.mutex:
                now = time.monotonic()
                pause = max(0, self.next.get(host, now) - now)
                if pause > MAX_AUTOMATIC_WAIT:
                    return deferred_receipt(url, raw, RetryDeferred(pause))
                if pause <= 0:
                    self.next[host] = now + self.delay
                    break
            if self.stop.wait(min(pause, .25)):
                raise InterruptedError('下载停止')
        def response_hook(record):
            if record.get('status_code') in (429, 503):
                cooldown = max(5, retry_after_seconds(record.get('headers', {}).get('retry-after', '')))
                with self.mutex:
                    self.next[host] = max(self.next.get(host, 0), time.monotonic() + cooldown)
        return capture(url, raw, lock_scope='url', stop_event=self.stop, response_hook=response_hook, **options)


class ShardArchive(bulk.BulkArchive):
    post_fetch_delay_enabled = False
    def report(self, *args, **kwargs):
        return None  # 只有parent写汇总；shard只维护一件详情的短state。


def completed(doc):
    return doc.get('status') == 'archived' and bool(doc.get('files')) and all(bulk.valid_receipt(r) for r in doc['files'].values())



def detail_tasks(state):
    seen = set()
    for platform, discovery in state['platforms'].items():
        if platform not in bulk.ADAPTERS or discovery.get('alias_of'):
            continue
        for url in discovery.get('details', []):
            key = bulk.sha(_url(url).encode())
            if key not in seen:
                seen.add(key)
                yield platform, url, key


def merge(parent, shard, seen_events):
    if shard.get('plan_sha256') != parent.state['plan_sha256']:
        raise ValueError('shard冻结计划SHA不符')
    parent.state['documents'].update(deepcopy(shard.get('documents', {})))
    for key, response in shard.get('responses', {}).items():
        old = parent.state['responses'].get(key, {})
        parent.state['responses'][key] = {**response, 'sources': list(dict.fromkeys(old.get('sources', []) + response.get('sources', [])))}
    for event in shard.get('events', []):
        key = bulk.sha(json.dumps(event, sort_keys=True).encode())
        if key not in seen_events:
            seen_events.add(key)
            parent.state['events'].append(event)
    parent.save()



def file_reference_key(url):
    """DPR详情为同一PDF生成14位时间缓存参数；不移除其他平台/签名参数。"""
    parsed = urlsplit(url)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if (parsed.hostname == 'berkas.dpr.go.id' and parsed.path.lower().endswith('.pdf')
            and (not pairs or all(k == 't' and v.isdigit() and len(v) >= 12 for k, v in pairs))):
        return parsed.scheme, parsed.netloc, parsed.path
    return url


def download_shard(directory, plan, task, previous, pace, stop):
    platform, url, key = task
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / 'finished.json'
    if marker.exists():
        marker.unlink()
    prior = deepcopy(previous or {})
    cached_detail = prior.pop('_cached_detail_receipt', None)
    reusable = {r.get('url'): r for r in prior.get('files', {}).values() if r.get('url')}
    if cached_detail and not pace.refresh_failed_details:
        reusable[cached_detail.get('url')] = cached_detail
    requested = set()
    reusable_paths = {file_reference_key(u): r for u, r in reusable.items()}
    def missing_only(url, raw, **options):
        requested.add(url)
        receipt = reusable.get(url) or reusable_paths.get(file_reference_key(url))
        if receipt and bulk.valid_receipt(receipt):
            return {**deepcopy(receipt), 'capture_status': 'reused', 'attempts': 0,
                    'reason': '重试前已成功文件经SHA校验复用；未发送下载请求'}
        if pace.refresh_failed_details and url == task[1]:
            options['refresh'] = True
        return pace.capture(url, raw, **options)
    child = ShardArchive(directory, plan, missing_only)
    child.state['platforms'] = {platform: {'details': [url], 'complete': False}}
    child.state['documents'] = {key: prior} if prior else {}
    child.save()
    child.capture_documents()
    doc = child.state['documents'][key]
    # 保留旧失败收据，但更换纯缓存参数的同一文件只保留本轮引用于当前集合。
    retired = []
    for file_key, receipt in list(doc.get('files', {}).items()):
        old_url = receipt.get('url')
        replacements = [u for u in requested if u != old_url and file_reference_key(u) == file_reference_key(old_url or '')]
        if old_url not in requested and replacements and not bulk.valid_receipt(receipt):
            retired.append({**receipt, 'superseded_at': timestamp(), 'current_urls': replacements,
                            'superseded_reason': '官方详情更新同路径PDF的缓存参数；旧失败历史保留，不计本轮活动引用'})
            del doc['files'][file_key]
    if retired:
        doc.setdefault('superseded_file_failures', []).extend(retired)
        if doc['files'] and all(bulk.valid_receipt(r) for r in doc['files'].values()):
            doc.update(status='archived', reason='当前官方文件引用已完整归档；旧缓存参数失败保留历史')
        child.save()
    if stop.is_set() and not completed(doc):
        doc.update(status='interrupted', reason='停止请求，保留已归档文件及短state，续跑可恢复')
        child.save()
    write_json(marker, {'plan_sha256': child.state['plan_sha256'], 'state_sha256': bulk.sha(child.state_path.read_bytes())})
    return directory


def execute(directory, plan, workers=2, retry_failed=False, stop=None, *, max_bytes=None, refresh_failed_details=False):
    if type(workers) is not int or not 1 <= workers <= 5:
        raise ValueError('workers须为1..5；默认2')
    if max_bytes is not None and (type(max_bytes) is not int or not 1 <= max_bytes <= 1024 * 1024 * 1024):
        raise ValueError('max_bytes运行上限须为1..1GiB')
    stop = stop or threading.Event()
    parent = bulk.BulkArchive(directory, plan)
    runtime = _safe(directory / 'fast-download')
    shards = runtime / 'shards'
    shards.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    policy = {'version': 1, 'mode': 'capture-only', 'workers': workers, 'retry_failed': retry_failed,
        'max_bytes_override': max_bytes, 'refresh_failed_details': refresh_failed_details,
        'created_at': timestamp(), 'original_plan_sha256': bulk.sha((directory / 'plan.json').read_bytes()),
        'source_sha256': {name: bulk.sha(Path(path).read_bytes()) for name, path in
            (('runner', __file__), ('bulk_archive', bulk.__file__), ('raw_capture', __import__('law_crawler.raw_capture', fromlist=['']).__file__))}}
    runs = runtime / 'runs'; runs.mkdir(exist_ok=True)
    write_json(runs / (run_id + '.json'), policy)
    events = {bulk.sha(json.dumps(event, sort_keys=True).encode()) for event in parent.state['events']}
    recovered = 0
    for marker in shards.glob('*/finished.json'):
        record = bulk.load(marker)
        state_path = marker.parent / 'state.json'
        merged = marker.with_name('merged.json')
        if merged.exists() and bulk.load(merged).get('state_sha256') == record['state_sha256']:
            continue
        if record['state_sha256'] != bulk.sha(state_path.read_bytes()):
            continue  # 未完成的shard不会宣称成功；后续从短state及原始缓存继续。
        merge(parent, bulk.load(state_path), events)
        write_json(merged, record)
        recovered += 1
    counts = {'skipped_complete': 0, 'skipped_failed': 0, 'downloaded_complete': 0,
              'attempted_incomplete': 0, 'recovered_shards': recovered, 'interrupted': 0}
    tasks = []
    for task in detail_tasks(parent.state):
        prior = parent.state['documents'].get(task[2], {})
        if completed(prior):
            counts['skipped_complete'] += 1
        elif prior.get('status') in ('failed', 'incomplete') and not retry_failed:
            counts['skipped_failed'] += 1
        else:
            tasks.append(task)
    queued_keys = {task[2] for task in tasks}
    iterator, pending = iter(tasks), {}
    def status(phase='capture'):
        write_json(runtime / 'status.json', {'phase': phase, 'run_id': run_id, 'mode': 'capture-only', 'counts': counts,
            'summary': progress_summary(parent.state, workers, active_keys=[t[2] for t in pending.values()],
                queued_keys=queued_keys, phase=phase, run_started_at=policy['created_at']),
            'remaining': len(tasks) - counts['downloaded_complete'] - counts['attempted_incomplete'],
            'stop_requested': stop.is_set(), 'national_completeness': 'unverified',
            'download_complete': not stop.is_set() and not counts['skipped_failed'] and not counts['attempted_incomplete']
                and counts['downloaded_complete'] == len(tasks)})
    status()
    pace = HostPace(plan['delay'], stop, max_bytes=max_bytes, refresh_failed_details=refresh_failed_details)
    pool = ThreadPoolExecutor(max_workers=workers)
    execution_error = False
    try:
        while not stop.is_set():
            while len(pending) < workers:
                task = next(iterator, None)
                if task is None:
                    break
                prior = deepcopy(parent.state['documents'].get(task[2]) or {})
                prior['_cached_detail_receipt'] = deepcopy(parent.state['responses'].get(task[2]))
                future = pool.submit(download_shard, shards / task[2], plan, task, prior, pace, stop)
                pending[future] = task
                queued_keys.discard(task[2])
                status()
            if not pending:
                break
            done, _ = wait(pending, timeout=.5, return_when=FIRST_COMPLETED)
            for future in done:
                task = pending.pop(future)
                shard_dir = future.result()
                merge(parent, bulk.load(shard_dir / 'state.json'), events)
                write_json(shard_dir / 'merged.json', bulk.load(shard_dir / 'finished.json'))
                doc = parent.state['documents'][task[2]]
                counts['downloaded_complete' if completed(doc) else 'attempted_incomplete'] += 1
                print(json.dumps({'detail': task[1], 'status': doc['status'], 'counts': counts}, ensure_ascii=False), flush=True)
                status()
    except KeyboardInterrupt:
        stop.set()
        counts['interrupted'] += 1
    except Exception:
        execution_error = True
        stop.set()
        raise
    finally:
        if stop.is_set():
            for future in pending:
                future.cancel()
        pool.shutdown(wait=True, cancel_futures=stop.is_set())
        # 已完成shard即便在停止期间返回也合并；原始归档与小state均已落盘。
        for future, task in pending.items():
            if future.cancelled():
                queued_keys.add(task[2])
            if future.done() and not future.cancelled() and future.exception() is None:
                shard_dir = future.result()
                merge(parent, bulk.load(shard_dir / 'state.json'), events)
                write_json(shard_dir / 'merged.json', bulk.load(shard_dir / 'finished.json'))
                doc = parent.state['documents'][task[2]]
                counts['downloaded_complete' if completed(doc) else 'attempted_incomplete'] += 1
        pending.clear()
        status('final_report')
        parent.report()
        status('error' if execution_error else 'stopped' if stop.is_set() else 'finished')
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--jurisdiction', required=True)
    parser.add_argument('--config')
    parser.add_argument('--rerun-batch', required=True)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--retry-failed', action='store_true')
    parser.add_argument('--max-file-mib', type=int, help='本轮原件体积上限覆盖，1..1024MiB；不修改冻结计划')
    parser.add_argument('--refresh-failed-details', action='store_true', help='刷新本轮重试详情；成功原件仍经SHA复用')
    args = parser.parse_args(argv)
    try:
        if args.max_file_mib is not None and not 1 <= args.max_file_mib <= 1024:
            raise ValueError('--max-file-mib须为1..1024')
        root = Path(args.root).resolve()
        selected = resolve_jurisdiction(root, args.jurisdiction)
        country = country_download_dir(configuration_path(root, args.config), root, selected.index, selected.name)
        directory = _safe(country / 'pipeline/bulk' / safe_id(args.rerun_batch))
        plan_path = directory / 'plan.json'
        if (directory / 'plan.sha256').read_text().strip() != bulk.sha(plan_path.read_bytes()):
            raise ValueError('原始冻结计划SHA校验失败')
        plan = bulk.load(plan_path)
        if not (directory / 'state.json').is_file() or plan.get('batch_id') != args.rerun_batch or plan.get('version') != bulk.VERSION:
            raise ValueError('必须使用已有目录断点的原冻结下载批次')
        if plan.get('index_sha256') != bulk.sha(selected.index.read_bytes()):
            raise ValueError('工作区索引与原冻结计划SHA不匹配')
        if plan.get('jurisdiction') != selected.name or plan.get('country_directory') != str(country):
            raise ValueError('原冻结计划国家或下载目录不匹配')
        with FileLock(directory / '.lock'):
            counts = execute(directory, plan, args.workers, args.retry_failed,
                max_bytes=args.max_file_mib * 1024 * 1024 if args.max_file_mib is not None else None,
                refresh_failed_details=args.refresh_failed_details)
        return 130 if counts['interrupted'] else (1 if counts['attempted_incomplete'] or counts['skipped_failed'] else 0)
    except (OSError, ValueError, KeyError) as exc:
        print(str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
