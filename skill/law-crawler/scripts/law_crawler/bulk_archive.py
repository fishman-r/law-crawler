"""可恢复的登记来源原始归档队列；目录穷尽不等于全国法律全集。"""
import argparse
from copy import deepcopy
import hashlib
import html
import json
import math
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from law_crawler.batch_runtime import safe_id, timestamp
from law_crawler.crawl_store import write_json
from law_crawler.download_regulations import country_download_dir
from law_crawler.file_lock import FileLock
from law_crawler.jurisdiction_registry import resolve_jurisdiction
from law_crawler.platform_adapters import ADAPTERS
from law_crawler.raw_capture import capture, _safe, _url, file_sha256
from law_crawler.resources import configuration_path, skill_directory

VERSION = 'bulk-archive-v1'
PLATFORM_ALIASES = {'peraturan.bpk.go.id': 'bpk.go.id', 'jdih.mahkamahagung.go.id': 'mahkamahagung.go.id'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    return json.loads(_safe(path).read_text(encoding='utf-8'))


def value(metadata, key):
    item = metadata.get(key)
    return item.get('value') if isinstance(item, dict) and 'value' in item else item


def metadata_fields(metadata):
    result = deepcopy(metadata)
    for key in ('published_at', 'updated_at', 'effective_region'):
        result.setdefault(key, {'value': None, 'raw': None, 'status': 'not_provided',
            'source_url': None, 'meaning': '官方详情未提供；不得从抓取时间或法域推断'})
    return result


def valid_receipt(row):
    try:
        path = _safe(row['path'])
        return (row.get('capture_status') in (None, 'saved', 'reused') and row.get('complete') and 200 <= row['status_code'] < 300
                and path.stat().st_size == row['size_bytes'] and file_sha256(path) == row['sha256'])
    except (KeyError, TypeError, OSError, ValueError):
        return False


def eligible(document, receipt):
    """仅向处理队列送入完整且具官方详情身份线索的可识别原件。"""
    if not valid_receipt(receipt):
        return False, '原件传输失败或哈希校验失败'
    hosts = document.get('official_hosts') or getattr(ADAPTERS.get(document.get('platform')), 'allowed_hosts', ())
    if not hosts or any(urlsplit(receipt.get(k) or '').hostname not in hosts for k in ('url', 'final_url')):
        return False, '请求或最终响应主机未经当前官方适配器授权'
    metadata = document.get('metadata', {})
    if metadata.get('processing_gate', 'eligible') != 'eligible':
        return False, '官方文献类别尚待核验；已归档但禁止自动当法规处理'
    if value(metadata, 'document_type') not in (None, 'regulation', 'judicial_guidance'):
        return False, '非已确认法规或司法指导文种，等待类别复核'
    identity = value(metadata, 'legal_identity')
    title = value(metadata, 'title')
    if not title or not isinstance(identity, dict) or not identity.get('number') or not identity.get('year'):
        return False, '官方详情未提供可核验法规编号和年份；身份待独立复核'
    path = Path(receipt['path'])
    data = path.read_bytes()
    if data.startswith(b'%PDF-'):
        return True, 'PDF 魔数已确认；身份及正文待独立复核'
    if path.suffix not in ('.html', '.xml', '.json', '.txt'):
        return False, '原格式已归档，处理器尚不支持此格式'
    text = data.decode('utf-8', errors='replace')
    if re.search(r'captcha|access denied|cloudflare|403 forbidden|verify you are human|just a moment|page not found', text[:20000], re.I):
        return False, '疑似错误页面或访问挑战；禁止进入模型队列'
    # 目录、导航、登录页也可能包含法规编号，禁止仅按关键词作为正文门禁。
    if path.suffix == '.html':
        return False, 'HTML 正文门禁尚未独立核验；保留归档并等待正文核验，禁止进入模型'
    return True, '文本格式已识别；身份及正文待独立复核'


class BulkArchive:
    def __init__(self, directory, plan, capture_fn=capture):
        self.directory, self.plan, self.capture_fn = _safe(directory), plan, capture_fn
        self.raw = _safe(Path(plan['country_directory']) / 'pipeline/raw')
        self.state_path = self.directory / 'state.json'
        self.state = load(self.state_path) if self.state_path.exists() else {
            'version': VERSION, 'platforms': {}, 'documents': {}, 'responses': {}, 'events': [],
            'created_at': timestamp(), 'cost': {'model_api_cost': 0, 'total_cost': None}}
        if self.state.get('plan_sha256') not in (None, sha(json.dumps(plan, sort_keys=True).encode())):
            raise ValueError('断点与冻结计划不匹配')
        self.state['plan_sha256'] = sha(json.dumps(plan, sort_keys=True).encode())
        self.allowed_hosts = ()

    def save(self):
        write_json(self.state_path, self.state)

    def fetch(self, url, *, read_body=True):
        canonical = _url(url)
        if not self.allowed_hosts or urlsplit(canonical).hostname not in self.allowed_hosts:
            raise ValueError('非当前适配器官方主机，拒绝网络请求: ' + canonical)
        started = time.monotonic()
        receipt = self.capture_fn(canonical, self.raw, **self.plan['retry'])
        key = sha(canonical.encode())
        previous = self.state['responses'].get(key, {})
        self.state['responses'][key] = {**receipt, 'url': canonical,
            'sources': list(dict.fromkeys(previous.get('sources', []) + [url]))}
        self.state['events'].append({'url': canonical, 'at': timestamp(),
            'status': receipt['capture_status'], 'reason': receipt['reason'],
            'duration_seconds': round(time.monotonic() - started, 6)})
        self.save()
        print(f"归档 {receipt['capture_status']}: {canonical}", flush=True)
        if self.plan['delay'] and getattr(self, 'post_fetch_delay_enabled', True):
            time.sleep(self.plan['delay'])
        if urlsplit(receipt.get('final_url') or canonical).hostname not in self.allowed_hosts:
            raise ValueError('响应跳转到非当前适配器官方主机；归档仅供排错，不得进入模型')
        if not valid_receipt(receipt):
            raise ValueError(receipt['reason'])
        return (Path(receipt['path']).read_bytes() if read_body else b''), receipt.get('final_url') or canonical

    def discover(self):
        scanned = set()
        for requested in self.plan['platforms']:
            platform = PLATFORM_ALIASES.get(requested, requested)
            if requested != platform:
                self.state['platforms'][requested] = {'alias_of': platform, 'details': [],
                    'complete': False, 'status': 'alias', 'reason': '登记证据域；由同一官方适配器合并扫描，不重复枚举'}
            if platform in scanned:
                self.save(); self.report()
                continue
            scanned.add(platform)
            adapter = ADAPTERS.get(platform)
            self.allowed_hosts = getattr(adapter, 'allowed_hosts', ())
            state = self.state['platforms'].setdefault(platform, {})
            if adapter is None:
                state.update(complete=False, status='unsupported', reason='未实现平台适配器', details=[])
                self.save()
                self.report()
                continue
            try:
                if hasattr(adapter, 'discover_bulk'):
                    def checkpoint(snapshot):
                        self.state['platforms'][platform] = snapshot
                        self.save()
                        self.report()
                        print(f"目录断点 {platform}: {len(snapshot.get('pages', []))} 页 / {len(snapshot.get('details', []))} 详情", flush=True)
                    result = adapter.discover_bulk(self.fetch, max_pages=self.plan['max_pages'],
                        checkpoint=state if state.get('version') else None, on_page=checkpoint)
                    self.state['platforms'][platform] = result
                elif not state.get('details'):
                    details = adapter.discover(self.fetch, limit=1000000)
                    state.update(details=list(dict.fromkeys(details)), complete=False,
                        status='incomplete', reason='仅有限目录发现；此适配器尚未实现可证明穷尽的分页')
            except Exception as exc:
                state = self.state['platforms'].setdefault(platform, state)
                state.update(complete=False, status='failed', reason=f'{type(exc).__name__}: {exc}')
            self.save()
            self.report()

    def capture_documents(self):
        for platform, discovery in list(self.state['platforms'].items()):
            adapter = ADAPTERS.get(platform)
            if adapter is None or discovery.get('alias_of'):
                continue
            self.allowed_hosts = getattr(adapter, 'allowed_hosts', ())
            for detail_url in discovery.get('details', []):
                key = sha(_url(detail_url).encode())
                doc = self.state['documents'].setdefault(key, {'detail_url': detail_url, 'platform': platform,
                    'metadata': {}, 'files': {}, 'identity_verification': 'not_independently_reviewed'})
                doc['official_hosts'] = list(self.allowed_hosts)
                try:
                    parsed = getattr(adapter, 'read_document_bulk', adapter.read_document)(detail_url, self.fetch)
                    doc.update(metadata=metadata_fields(parsed.metadata), official_detail_url=parsed.detail_url,
                        status='metadata_saved', reason='详情身份由适配器提取，尚未独立核验')
                    for url in dict.fromkeys(parsed.file_urls):
                        canonical = _url(url)
                        file_key = sha(canonical.encode())
                        try:
                            self.fetch(canonical, read_body=False)
                            receipt = self.state['responses'][file_key]
                            doc['files'][file_key] = deepcopy(receipt)
                        except Exception as exc:
                            receipt = self.state['responses'].get(file_key, {})
                            doc['files'][file_key] = {**receipt, 'url': canonical,
                                'capture_status': 'failed', 'reason': f'{type(exc).__name__}: {exc}'}
                        self.save()
                    doc.update(status='archived' if parsed.file_urls and all(valid_receipt(x) for x in doc['files'].values()) else 'incomplete',
                        reason='全部已发现附件完整归档' if parsed.file_urls and all(valid_receipt(x) for x in doc['files'].values()) else '缺失附件或部分附件失败')
                except Exception as exc:
                    doc.update(status='failed', reason=f'{type(exc).__name__}: {exc}')
                self.save()
                print(f"详情 {doc['status']}: {detail_url}", flush=True)
            self.report()

    def export(self):
        manifest_dir = self.directory / 'manifests'
        manifest_dir.mkdir(exist_ok=True)
        style = self.directory / 'translation-style.json'
        if not style.exists():
            write_json(style, self.plan['translation_style'])
        elif load(style) != self.plan['translation_style']:
            raise ValueError('冻结翻译文体文件被修改')
        candidates, by_sha, excluded = [], {}, []
        for key, doc in self.state['documents'].items():
            for file_key, receipt in doc['files'].items():
                accepted, reason = eligible(doc, receipt)
                record = {'document_id': key, 'url': receipt.get('url'), 'reason': reason}
                if not accepted:
                    excluded.append(record)
                    continue
                digest = receipt['sha256']
                if digest not in by_sha:
                    item = {'id': 'doc-' + digest[:24], 'path': receipt['path'],
                        'title': value(doc['metadata'], 'title'), 'source_language': 'id',
                        'document': {**metadata_fields(doc['metadata']), 'identity_verification': 'not_independently_reviewed',
                            'archive_source_sha256': digest, 'sources': []}}
                    by_sha[digest] = item
                    candidates.append(item)
                source = {'detail_url': doc.get('official_detail_url', doc['detail_url']),
                    'file_url': receipt['url'], 'platform': doc['platform'], 'metadata': doc['metadata'],
                    'file_role': doc['metadata'].get('file_roles', {}).get(receipt['url']),
                    'raw_sha256': digest}
                if source not in by_sha[digest]['document']['sources']:
                    by_sha[digest]['document']['sources'].append(source)
        active = []
        for item in candidates:
            manifest = {'translation_style_path': str(style), 'documents': [item]}
            # Export snapshots are content-addressed; preserve earlier snapshots on resumed discovery.
            manifest_key = sha(json.dumps(manifest, sort_keys=True).encode())
            target = manifest_dir / (item['id'] + '-' + manifest_key[:12] + '.json')
            if not target.exists():
                write_json(target, manifest)
            active.append({'path': str(target), 'sha256': sha(target.read_bytes()),
                           'raw_sha256': item['document']['archive_source_sha256']})
        write_json(self.directory / 'active-manifests.json', {'version': 1, 'manifests': active})
        write_json(self.directory / 'manifest.json', {'translation_style_path': str(style), 'documents': candidates})
        write_json(self.directory / 'export-exclusions.json', excluded)
        self.report(len(candidates), len(excluded))
        return candidates

    def report(self, export_count=None, excluded_count=None):
        if export_count is None and (self.directory / 'manifest.json').exists():
            export_count = len(load(self.directory / 'manifest.json').get('documents', []))
        if excluded_count is None and (self.directory / 'export-exclusions.json').exists():
            excluded_count = len(load(self.directory / 'export-exclusions.json'))
        platforms = self.state['platforms']
        # 旧批次可能把索引中的官方子域作为独立未适配平台；保留计划并在报告注明别名。
        for alias in self.plan['platforms']:
            canonical_name = PLATFORM_ALIASES.get(alias)
            if canonical_name:
                platforms.setdefault(alias, {}).update(alias_of=canonical_name, status='alias',
                    reason='同一官方平台的已登记子域；覆盖状态读取主平台')
        for state in platforms.values():
            if state.get('alias_of'):
                canonical = platforms.get(state['alias_of'], {})
                state['complete'] = canonical.get('complete', False)
                state['canonical_status'] = canonical.get('status')
        docs = self.state['documents']
        files = [r for d in docs.values() for r in d['files'].values()]
        counts = {'platforms': len(self.plan['platforms']), 'catalogs_exhausted': sum(bool(p.get('complete')) for p in platforms.values() if not p.get('alias_of')),
            'details_discovered': sum(len(p.get('details', [])) for p in platforms.values()),
            'details_attempted': len(docs), 'files_discovered': len(files),
            'files_complete': sum(bool(valid_receipt(f)) for f in files),
            'unique_original_sha256': len({f['sha256'] for f in files if valid_receipt(f)}),
            'processing_candidates': export_count, 'processing_excluded': excluded_count}
        report = {'batch_id': self.plan['batch_id'], 'jurisdiction': self.plan['jurisdiction'], 'counts': counts,
            'national_completeness': 'unverified', 'coverage': platforms,
            'unsupported': [p for p in self.plan['platforms'] if PLATFORM_ALIASES.get(p, p) not in ADAPTERS],
            'boundary': '仅归档索引登记且适配器发现的官方响应；目录穷尽不证明全国法规全集；未独立验收身份、最新版本或效力。',
            'cost': self.state['cost'], 'duration_seconds': sum(e['duration_seconds'] for e in self.state['events']),
            'generated_at': timestamp()}
        write_json(self.directory / 'metadata.json', {'plan': self.plan, 'documents': list(docs.values()), 'responses': self.state['responses']})
        write_json(self.directory / 'report.json', report)
        lines = [f"# 原始归档批次 {self.plan['batch_id']}", '', report['boundary'], '', '| 指标 | 数量 |', '|---|---:|']
        lines += [f'| {name} | {number} |' for name, number in counts.items()]
        lines += ['', '## 平台覆盖', '', '| 平台 | 目录穷尽 | 说明 |', '|---|---|---|']
        lines += [f"| {p} | {s.get('complete', False)} | {s.get('reason') or s.get('status') or '查看 JSON 分页断点与停止原因'} |" for p, s in platforms.items()]
        lines += ['', '模型 API 费用：0；总成本：未知。耗时为实际归档调用耗时，不含模型处理及独立复核。', '']
        (self.directory / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
        body = '<h1>原始归档批次 ' + html.escape(self.plan['batch_id']) + '</h1><p>' + html.escape(report['boundary']) + '</p>'
        body += '<table><tr><th>指标</th><th>数量</th></tr>' + ''.join(f'<tr><td>{html.escape(k)}</td><td>{v}</td></tr>' for k, v in counts.items()) + '</table>'
        body += '<h2>平台覆盖</h2><table><tr><th>平台</th><th>目录穷尽</th><th>说明</th></tr>' + ''.join('<tr><td>'+html.escape(p)+'</td><td>'+str(s.get('complete',False))+'</td><td>'+html.escape(str(s.get('reason') or s.get('status') or s.get('streams',{})))+'</td></tr>' for p,s in platforms.items()) + '</table><p>模型 API 费用：0；总成本：未知。身份、正文与翻译尚待独立复核。</p>'
        (self.directory / 'report.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>原始归档报告</title><style>body{font:16px/1.7 system-ui;max-width:1100px;margin:40px auto;padding:20px;color:#182332}table{border-collapse:collapse;width:100%;margin:24px 0}td,th{border:1px solid #ccd5df;padding:10px;text-align:left;overflow-wrap:anywhere}th{background:#edf2f7}p{background:#f3f6fa;padding:18px}</style>'+body+'</html>', encoding='utf-8')
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--jurisdiction', required=True)
    parser.add_argument('--config')
    parser.add_argument('--platform', action='append')
    parser.add_argument('--batch-id')
    parser.add_argument('--rerun-batch')
    parser.add_argument('--phase', choices=('discover', 'capture', 'export', 'status', 'all'), default='all')
    parser.add_argument('--max-pages', type=int, default=1000)
    parser.add_argument('--timeout', type=float, default=15)
    parser.add_argument('--max-bytes', type=int, default=40 * 1024 * 1024)
    parser.add_argument('--max-attempts', type=int, default=3)
    parser.add_argument('--backoff-base', type=float, default=1)
    parser.add_argument('--delay', type=float, default=.25)
    args = parser.parse_args(argv)
    root = _safe(Path(args.root).expanduser())
    selected = resolve_jurisdiction(root, args.jurisdiction)
    if bool(args.batch_id) == bool(args.rerun_batch):
        parser.error('必须且只能提供 --batch-id / --rerun-batch 之一')
    batch_id = safe_id(args.batch_id or args.rerun_batch)
    country = country_download_dir(configuration_path(root, args.config), root, selected.index, selected.name)
    directory = _safe(country / 'pipeline/bulk' / batch_id)
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(directory / '.lock'):
        plan_path = directory / 'plan.json'
        if args.rerun_batch:
            plan = load(plan_path)
            frozen_hash = directory / 'plan.sha256'
            if not frozen_hash.is_file() or frozen_hash.read_text().strip() != sha(plan_path.read_bytes()):
                raise ValueError('冻结计划 SHA-256 校验失败')
            if plan.get('jurisdiction') != selected.name or plan.get('batch_id') != batch_id:
                raise ValueError('批次与法域不匹配')
            if args.platform and list(dict.fromkeys(args.platform)) != plan['platforms']:
                raise ValueError('重跑不能修改冻结平台')
        else:
            if plan_path.exists():
                raise ValueError('批次已存在，使用 --rerun-batch')
            platforms = list(dict.fromkeys(args.platform or selected.platforms))
            if set(platforms) - set(selected.platforms):
                raise ValueError('平台未在所选国家/法域索引登记')
            if not all(math.isfinite(x) for x in (args.timeout, args.backoff_base, args.delay)):
                raise ValueError('超时、退避和延时必须为有限数值')
            if args.max_pages < 1 or args.max_attempts < 1 or args.timeout <= 0 or args.max_bytes < 1 or args.delay < 0 or args.backoff_base < 0:
                raise ValueError('分页、超时、大小、重试次数须为正；退避和延时不得为负')
            for p in platforms:
                canonical = PLATFORM_ALIASES.get(p, p)
                if canonical in ADAPTERS and ADAPTERS[canonical].country != selected.name:
                    raise ValueError('适配器国家与所选法域不一致')
            plan = {'version': VERSION, 'batch_id': batch_id, 'jurisdiction': selected.name,
                'country_directory': str(country), 'index_path': str(selected.index),
                'index_sha256': sha(selected.index.read_bytes()), 'platforms': platforms,
                'created_at': timestamp(), 'max_pages': args.max_pages, 'delay': args.delay,
                'retry': {'timeout': args.timeout, 'max_bytes': args.max_bytes,
                    'max_attempts': args.max_attempts, 'backoff_base': args.backoff_base},
                'translation_style': load(skill_directory() / 'assets/translation-styles/zh-cn_en-gb.json')}
            write_json(plan_path, plan)
            (directory / 'plan.sha256').write_text(sha(plan_path.read_bytes()) + '\n')
        queue = BulkArchive(directory, plan)
        queue.save()
        queue.report()
        if args.phase in ('discover', 'all'):
            queue.discover()
        if args.phase in ('capture', 'all'):
            queue.capture_documents()
        if args.phase in ('export', 'all'):
            queue.export()
        else:
            queue.report()
        print(json.dumps({'batch_directory': str(directory), 'phase': args.phase}, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
