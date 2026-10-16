#!/usr/bin/env python3
"""从国家平台索引下载法规原文件，更新来源元数据并执行多层去重。"""
import argparse
from copy import copy
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, quote

from law_crawler.crawl_store import CrawlStore, canonical_url, now, write_json
from law_crawler.platform_adapters.base import Links
from law_crawler.resources import configuration_path, default_workspace

ROOT = default_workspace()
from law_crawler.platform_adapters import ADAPTERS



def download_dir(config, root=ROOT):
    props = {}
    for line in Path(config).read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if line and not line.startswith(('#', '!')) and '=' in line:
            key, value = line.split('=', 1)
            props[key.strip()] = value.strip()
    value = props.get('downdloadDir') or props.get('downloadDir')
    if not value:
        raise ValueError('配置缺少 downdloadDir')
    path = Path(value).expanduser()
    return (Path(root) / path).resolve() if not path.is_absolute() else path.resolve()


def index_country(index, country=None):
    """标准 countries/<Country>/index.md 可推导国家；其他索引必须显式声明。"""
    index = Path(index)
    inferred = index.parent.name if index.name == 'index.md' and index.parent.parent.name == 'countries' else None
    selected = country if country is not None else inferred
    if (not isinstance(selected, str) or not selected or selected != selected.strip()
            or selected in ('.', '..')
            or any(not (char.isalnum() or char in ('-', '_')) for char in selected)):
        raise ValueError('国家必须为单个安全目录名；非标准索引请提供 --country（如 Canada）')
    if inferred is not None and country is not None and country != inferred:
        raise ValueError('--country 必须与 countries/<Country>/index.md 的国家目录一致')
    return selected


def country_download_dir(config, root, index, country=None):
    """仅新运行使用国家目录；不迁移或删除基础目录内的历史文件。"""
    base = download_dir(config, root)
    directory = base / index_country(index, country)
    # 拒绝已存在的目录符号链接，防止跨国家合并或写入基础目录之外。
    if directory.is_symlink() or directory.resolve().parent != base:
        raise ValueError('国家下载目录不得使用符号链接或逃逸基础下载目录')
    return directory


def validate_platform_countries(platforms, country):
    """已适配平台须属于目标国家；未知适配器不从域名臆测国家。"""
    for platform in platforms:
        known_country = getattr(ADAPTERS.get(platform), 'country', None)
        if known_country is not None and known_country != country:
            raise ValueError(f'平台 {platform} 属于 {known_country}，不能保存到 {country} 国家目录')


def index_platforms(index):
    return list(dict.fromkeys(urlsplit(u).hostname.removeprefix('www.')
                             for u in re.findall(r'\]\((https?://[^\s)]+)\)', Path(index).read_text(encoding='utf-8'))))


def fetch(url, timeout, max_bytes=40 * 1024 * 1024):
    encoded = quote(url, safe=":/?#[]@!$&'()*+,;=%")
    request = urllib.request.Request(encoded, headers={'User-Agent': 'law-crawler/0.3 (official regulation downloader)'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError('响应超过大小上限')
        return data, response.url


def official_fetch(url, timeout, hosts, max_bytes=40 * 1024 * 1024):
    hosts = (hosts, 'www.' + hosts) if isinstance(hosts, str) else tuple(hosts)
    if urlsplit(url).hostname not in hosts:
        raise ValueError(f'请求主机未在平台白名单中: {url}')
    data, final = fetch(url, timeout) if max_bytes == 40 * 1024 * 1024 else fetch(url, timeout, max_bytes)
    if urlsplit(final).hostname not in hosts:
        raise ValueError(f'响应跳转到未适配主机: {final}')
    return data, final


def save_pdf(directory, url, detail, platform, timeout, metadata=None, refresh=False):
    """兼容单文件调用；批量运行共享一个 CrawlStore。"""
    with CrawlStore(directory) as store:
        return store.save(url, detail, platform, lambda u: fetch(u, timeout), metadata, refresh)


def record_result(report, record, result):
    sha = record['file']['sha256']
    row = dict(sha256=sha, file=str(Path(report['download_dir']) / record['file']['path']),
               size_bytes=record['file']['size_bytes'], media_type=record['file']['media_type'], source_count=len(record['sources']), result=result)
    previous = next((r for r in report['files'] if r['sha256'] == sha), None)
    if previous:
        previous['source_count'] = row['source_count']
    else:
        report['files'].append(row)
    report['counts'][result] = report['counts'].get(result, 0) + 1
    print(f'{result}: {row["file"]}', flush=True)


def refresh_metadata(args, store, report, platforms):
    """使用各平台自身的详情/API解析器；不请求 PDF。"""
    documents = {}
    updated_by_platform = {}
    for record in store.records():
        if len(report['files']) >= args.limit:
            break
        changed = False
        for source in record['sources']:
            platform, detail = source.get('platform'), source.get('detail_url')
            if platform not in platforms or platform not in ADAPTERS or not detail:
                continue
            platform_hashes = updated_by_platform.setdefault(platform, set())
            per_limit = getattr(args, 'per_platform_limit', None) or args.limit
            sha = record['file']['sha256']
            if len(platform_hashes) >= per_limit and sha not in platform_hashes:
                continue
            adapter = ADAPTERS[platform]
            key = (platform, canonical_url(detail))
            try:
                if key not in documents:
                    time.sleep(args.delay)
                    try:
                        documents[key] = adapter.read_document(
                            detail, lambda u: official_fetch(u, args.timeout, adapter.allowed_hosts))
                    except Exception as exc:
                        documents[key] = exc
                if isinstance(documents[key], Exception):
                    raise documents[key]
                document = documents[key]
                current_links = {canonical_url(u) for u in document.file_urls}
                prior_links = {canonical_url(source['download_url']), canonical_url(source['final_url'])}
                latest_hashes = [row[0] for link in prior_links
                                 if (row := store.db.execute(
                                     "SELECT sha256 FROM requests WHERE canonical_url=? AND status='succeeded'",
                                     (link,)).fetchone())]
                if not current_links.intersection(prior_links) or any(value != sha for value in latest_hashes):
                    report['errors'].append(dict(platform=platform, url=detail, code='stale_source',
                                                 error='当前详情链接或已知内容版本已变化，保留旧文件的历史元数据'))
                    continue
                store.add_source(record, source['download_url'], detail, platform,
                                 source['final_url'], document.metadata)
                platform_hashes.add(sha)
                changed = True
            except Exception as exc:
                report['errors'].append(dict(platform=platform, url=detail, error=str(exc)))
        if changed:
            store.persist(record)
            record_result(report, record, 'metadata_updated')


def download_platform(args, store, report, platform, seen_pdfs):
    adapter = ADAPTERS[platform]
    if getattr(args, 'experimental_sources', False) and hasattr(adapter, 'with_experimental_sources'):
        adapter = adapter.with_experimental_sources()
    state = dict(platform=platform, entries=list(adapter.entry_urls), status='scanning',
                 detail_pages=0, files=[], errors=[], checked_at=now())
    if getattr(adapter, 'experimental_sources', False):
        state['experimental_entry'] = 'https://' + adapter.experimental_host + '/'
    report['platforms'].append(state)
    per_limit = getattr(args, 'per_platform_limit', None) or args.limit
    seen_details, platform_hashes = set(), set()
    error_start = len(report['errors'])
    def fetch_page(url):
        return official_fetch(url, args.timeout, adapter.allowed_hosts)
    try:
        details = adapter.discover(fetch_page, args.max_details)
        for detail in details:
            if len(report['files']) >= args.limit or len(platform_hashes) >= per_limit or state['detail_pages'] >= args.max_details:
                break
            key = canonical_url(detail)
            if key in seen_details:
                continue
            seen_details.add(key)
            time.sleep(args.delay)
            state['detail_pages'] += 1
            try:
                document = adapter.read_document(detail, fetch_page)
                route = document.metadata.get('source_route', {}).get('value')
                if route:
                    state['source_route'] = route
                if not document.file_urls:
                    raise ValueError('详情未发现支持格式的官方原件链接')
                for pdf in document.file_urls:
                    if len(report['files']) >= args.limit or len(platform_hashes) >= per_limit:
                        break
                    pdf_key = canonical_url(pdf)
                    if seen_pdfs.get(pdf_key) == 'failed':
                        continue
                    try:
                        result, record = store.save(
                            pdf, document.detail_url, platform, fetch_page, document.metadata,
                            refresh=args.refresh and pdf_key not in seen_pdfs)
                        seen_pdfs[pdf_key] = 'succeeded'
                        record_result(report, record, result)
                        sha = record['file']['sha256']
                        if sha not in platform_hashes:
                            state['files'].append(dict(sha256=sha, file=record['file']['path'],
                                                       detail_url=document.detail_url, download_url=pdf, result=result))
                        platform_hashes.add(sha)
                    except Exception as exc:
                        seen_pdfs[pdf_key] = 'failed'
                        report['errors'].append(dict(platform=platform, url=pdf, error=str(exc)))
            except Exception as exc:
                report['errors'].append(dict(platform=platform, url=detail, error=str(exc)))
        state['status'] = 'success' if state['files'] else ('no_downloads' if state['detail_pages'] else 'no_details')
    except Exception as exc:
        state['status'] = 'failed'
        report['errors'].append(dict(platform=platform, url=adapter.entry_urls[0], error=str(exc)))
        print(f'失败: {platform}: {exc}', file=sys.stderr, flush=True)
    state['errors'] = report['errors'][error_start:]
    if state['files'] and state['errors']:
        state['status'] = 'partial_success'
    production = getattr(adapter, 'production_discovery', None)
    if production is not None:
        state['production_discovery'] = production
    if state.get('source_route') == 'experimental_official_fallback' and state['files']:
        state['status'] = 'experimental_partial_success' if state['errors'] else 'experimental_success'
    print(f'平台 {platform}: {state["status"]}，可用文件 {len(state["files"])}', flush=True)


EU_PLATFORMS = {
    'eur-lex.europa.eu': ('EU', 'navigation_only'),
    'gesetze-im-internet.de': ('Germany', None),
    'rechtsprechung-im-internet.de': ('Germany', 'out_of_scope_case_law'),
    'legifrance.gouv.fr': ('France', None),
    'wetten.overheid.nl': ('Netherlands', None),
    'uitspraken.rechtspraak.nl': ('Netherlands', 'out_of_scope_case_law'),
    'normattiva.it': ('Italy', None),
}


def run_eu(args, platforms):
    """EU 是跨国索引：原件按成员国隔离，EU 目录仅放本轮汇总。"""
    directory = country_download_dir(args.config, args.root, args.index, 'EU')
    report = dict(schema_version=2, country='EU', index=str(args.index.resolve()),
                  download_dir=str(directory), started_at=now(),
                  mode='refresh_metadata' if args.refresh_metadata else 'download',
                  experimental_sources=getattr(args, 'experimental_sources', False), files=[], counts={},
                  platforms=[], errors=[], migration_errors=[])
    # 在发起网络请求前验证所有国家目录，拒绝符号链接与逃逸。
    for platform in platforms:
        country, scope = EU_PLATFORMS.get(platform, ('EU', 'unsupported'))
        if scope is None:
            country_download_dir(args.config, args.root, Path('index.md'), country)
            validate_platform_countries([platform], country)
    with CrawlStore(directory):
        for platform in platforms:
            country, scope = EU_PLATFORMS.get(platform, ('EU', 'unsupported'))
            if scope:
                report['platforms'].append(dict(platform=platform, country=country, status=scope))
                continue
            remaining = args.limit - len(report['files'])
            if remaining <= 0:
                report['platforms'].append(dict(platform=platform, country=country, status='not_scanned_limit'))
                continue
            child = copy(args)
            child.limit = min(remaining, getattr(args, 'per_platform_limit', None) or remaining)
            result = run_single(child, country, [platform], return_report=True)
            report['files'].extend(result['files'])
            report['errors'].extend(result['errors'])
            report['migration_errors'].extend(result['migration_errors'])
            report['platforms'].extend(dict(state, country=country) for state in result['platforms'])
            for key, value in result['counts'].items():
                report['counts'][key] = report['counts'].get(key, 0) + value
        report['finished_at'] = now()
        report['unique_files'] = len(report['files'])
        write_json(directory/'last-run.json', report)
        status_path = directory/'platform-status.json'
        status = json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else {}
        status.update({p['platform']: p for p in report['platforms'] if p['status'] != 'not_scanned_limit'})
        write_json(status_path, status)
    print(f'EU 汇总: {directory / "last-run.json"}；文件 {len(report["files"])}')
    return 0 if report['files'] else 1


def run(args):
    country = index_country(args.index, getattr(args, 'country', None))
    platforms = index_platforms(args.index)
    if args.platform != 'all':
        if args.platform not in platforms:
            raise ValueError('指定平台不在 index.md 中')
        platforms = [args.platform]
    if country == 'EU':
        return run_eu(args, platforms)
    return run_single(args, country, platforms)


def run_single(args, country, platforms, return_report=False):
    directory = country_download_dir(args.config, args.root, Path('index.md'), country)
    validate_platform_countries(platforms, country)
    report = dict(schema_version=2, country=country, index=str(args.index.resolve()), download_dir=str(directory),
                  experimental_sources=getattr(args, 'experimental_sources', False),
                  mode='refresh_metadata' if args.refresh_metadata else 'download',
                  started_at=now(), files=[], counts={}, platforms=[], errors=[], migration_errors=[])
    with CrawlStore(directory) as store:
        report['migration_errors'] = store.migration_errors
        if args.refresh_metadata:
            refresh_metadata(args, store, report, platforms)
        else:
            seen_pdfs = {}
            for platform in platforms:
                if platform not in ADAPTERS:
                    report['platforms'].append(dict(platform=platform, status='unsupported'))
                elif len(report['files']) >= args.limit:
                    report['platforms'].append(dict(platform=platform, status='not_scanned_limit'))
                else:
                    download_platform(args, store, report, platform, seen_pdfs)
            status_path = directory / 'platform-status.json'
            status = json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else {}
            for state in report['platforms']:
                if state['status'] != 'not_scanned_limit':
                    status[state['platform']] = state
            write_json(status_path, status)
        report['finished_at'] = now()
        report['unique_files'] = len(report['files'])
        write_json(directory / 'last-run.json', report)
    print(f'本次不同内容文件: {len(report["files"])}；错误: {len(report["errors"])}；报告: {directory / "last-run.json"}')
    return report if return_report else (0 if report['files'] else 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT, help='工作区根目录，决定相对配置路径')
    parser.add_argument('--index', type=Path, help='可选；须与显式法域在工程中的索引一致')
    parser.add_argument('--jurisdiction', '--country', dest='jurisdiction', help='必须明确适用国家/法域；仅接受工程索引登记项')
    parser.add_argument('--mode', choices=('metadata', 'content'), help='仅目录元数据，或下载法规原文')
    parser.add_argument('--batch-id', help='新批次的可选安全标识')
    parser.add_argument('--rerun-batch', help='按已有批次计划重跑失败或未完成项')
    parser.add_argument('--rerun-all', action='store_true', help='重跑批次全部候选，包括已成功项')
    parser.add_argument('--plan-only', action='store_true', help='仅保存执行计划，不请求网络')
    parser.add_argument('--max-attempts', type=int, default=3, help='每个请求最多尝试次数，包含首次，默认3')
    parser.add_argument('--backoff-base', type=float, default=1, help='指数退避初始秒数，默认1')
    parser.add_argument('--config', type=Path, help='配置路径；默认读取工作区或源码工程skill的countries配置')
    parser.add_argument('--experimental-sources', action='store_true', help='启用已明确标注的官方实验/开发部署备用来源；不代表正式站恢复')
    parser.add_argument('--platform', default='all', help='索引中的域名，默认 all')
    parser.add_argument('--limit', type=int, default=3, help='本次不同内容文件数上限，含已下载文件')
    parser.add_argument('--per-platform-limit', type=int, help='每个平台的不同内容文件数上限；逐平台验证可设为 1')
    parser.add_argument('--max-details', type=int, default=20, help='每个平台最多访问的详情页数')
    parser.add_argument('--timeout', type=float, default=20)
    parser.add_argument('--delay', type=float, default=1)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--refresh', action='store_true', help='重新请求已成功 URL，检测并保留新内容版本')
    modes.add_argument('--refresh-metadata', action='store_true', help='只更新已有文件的详情元数据，不下载原件')
    args = parser.parse_args()
    args.root = args.root.expanduser().resolve()
    if args.index is not None:
        args.index = args.root / args.index
    args.config = configuration_path(args.root, args.config)
    if args.max_attempts <= 0 or args.backoff_base < 0:
        parser.error("max-attempts必须大于0，backoff-base不能为负数")
    if min(args.limit, args.max_details, args.timeout) <= 0 or args.delay < 0 or (args.per_platform_limit is not None and args.per_platform_limit <= 0):
        parser.error('数量和 timeout 必须大于 0，delay 不能为负数')
    try:
        from law_crawler.jurisdiction_registry import resolve_jurisdiction
        from law_crawler.regulation_pipeline import execute
        if not args.rerun_batch:
            resolve_jurisdiction(args.root, args.jurisdiction, args.index)
            if args.mode is None:
                if sys.stdin.isatty():
                    answer = input('是否先只下载元数据（目录）？[Y/n] ').strip().lower()
                    if answer in ('', 'y', 'yes', '是'):
                        args.mode = 'metadata'
                    elif answer in ('n', 'no', '否'):
                        args.mode = 'content'
                    else:
                        raise ValueError('请选择 --mode metadata 或 --mode content')
                else:
                    raise ValueError('非交互执行须先确认范围并显式传 --mode metadata|content')
        if args.refresh_metadata and args.mode == 'content':
            raise ValueError('--refresh-metadata 仅适用于元数据模式')
        return execute(args)
    except (OSError, ValueError) as exc:
        parser.exit(1, f'错误: {exc}\n')


if __name__ == '__main__':
    sys.exit(main())
