#!/usr/bin/env python3
"""离线导入已下载的官方法规原件；显式区分浏览器与CLI取得，不发起网络请求。"""
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from law_crawler.crawl_store import CrawlStore, now, write_json
from law_crawler.download_regulations import country_download_dir, download_dir, index_country, index_platforms, validate_platform_countries
from law_crawler.platform_adapters import ADAPTERS
from law_crawler.resources import configuration_path, default_workspace
from law_crawler.relocation import plan_relocation


ROOT = default_workspace()
REQUIRED_METADATA = {'title', 'published_at', 'updated_at', 'effective_region', 'document_type'}
WRAPPER_KEYS = {'value', 'raw', 'source_label', 'source_url', 'status', 'meaning'}
STATUSES = {'observed', 'not_provided', 'unparsed', 'partial'}
MA_GUIDANCE_HOSTS = {'jdih.mahkamahagung.go.id', 'kepaniteraan.mahkamahagung.go.id'}
SEMA_KIND = 'Surat Edaran Mahkamah Agung'
SEMA_MEANING = '印度尼西亚最高法院通函 SEMA，属于司法指导文件；不等同于中国法的司法解释，效力尚未核实。'


def official_url(value, label, hosts):
    """检查来源声明，不访问网络，也不把未知跳转终点当作已验证终点。"""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f'{label} 必须是非空官方 URL')
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f'{label} 含控制字符')
    try:
        parts = urlsplit(value)
        port = parts.port
        valid = (parts.scheme in ('https', 'http') and parts.hostname in hosts
                 and parts.username is None and parts.password is None
                 and not parts.netloc.endswith(':')
                 and (port is None or 1 <= port <= 65535))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f'{label} 必须使用平台白名单主机、http(s)、无凭证及有效端口')
    return value


def validate_metadata(metadata, hosts):
    """要求显式来源证据；不从文件名生成标题、日期或法规类型。"""
    if not isinstance(metadata, dict) or not REQUIRED_METADATA.issubset(metadata):
        raise ValueError('metadata 缺少 schema 2 必需字段及 document_type')
    for name, item in metadata.items():
        if name == 'legal_status':
            if item != 'not_verified':
                raise ValueError('legal_status 只能为 not_verified')
            continue
        if not isinstance(item, dict) or not WRAPPER_KEYS.issubset(item):
            raise ValueError(f'metadata.{name} 必须是完整来源字段对象')
        value, raw, status = item['value'], item['raw'], item['status']
        if status not in STATUSES or not isinstance(item['meaning'], str) or not item['meaning'].strip():
            raise ValueError(f'metadata.{name} 缺少有效 status/meaning')
        if item['source_label'] is not None and not isinstance(item['source_label'], str):
            raise ValueError(f'metadata.{name}.source_label 必须是字符串或 null')
        if raw is not None and not isinstance(raw, (str, dict)):
            raise ValueError(f'metadata.{name}.raw 必须是原文、身份对象或 null')
        if item['source_url'] is not None:
            official_url(item['source_url'], f'metadata.{name}.source_url', hosts)
        if status in ('not_provided', 'unparsed') and value is not None:
            raise ValueError(f'metadata.{name} 未知或未解析时 value 必须为 null')
        if status in ('observed', 'partial'):
            raw_present = bool(raw) and (not isinstance(raw, str) or raw.strip().lower() not in
                                        {'', '-', '—', 'null', 'n/a'})
            if (value is None or not raw_present or not (item['source_label'] or '').strip()
                    or not item['source_url']):
                raise ValueError(f'metadata.{name} 的已观察字段必须包含值及来源证据')
        if status == 'unparsed' and not raw:
            raise ValueError(f'metadata.{name} 解析失败时必须保留原文')
        if name == 'legal_identity':
            if value is not None and (not isinstance(value, dict) or
                                      any(v is not None and not isinstance(v, str) for v in value.values())):
                raise ValueError('legal_identity.value 必须为字符串/null 组成的对象或 null')
        elif name == 'text_body_confirmed':
            if value is not None and not isinstance(value, bool):
                raise ValueError('text_body_confirmed.value 必须为布尔值或 null')
        elif value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f'metadata.{name}.value 必须是非空字符串或 null')
    if (metadata['title']['status'] != 'observed' or not metadata['title']['value']
            or metadata['document_type']['status'] != 'observed'):
        raise ValueError('必须提供观察到的法规标题，且 document_type 为 regulation')
    kind = metadata['document_type']['value']
    if kind == 'judicial_guidance':
        # 仅扩展最高法院正式 SEMA，不把法院判决、其他通函或文章放宽为法规。
        identity_field = metadata.get('legal_identity', {})
        identity = identity_field.get('value')
        if (not hosts or not set(hosts).issubset(MA_GUIDANCE_HOSTS)
                or identity_field.get('status') != 'observed' or not isinstance(identity, dict)
                or identity.get('type') != SEMA_KIND or identity.get('issuer') != 'Mahkamah Agung'
                or identity.get('jurisdiction') != 'ID'
                or any(not isinstance(identity.get(key), str) or not identity[key].strip() for key in ('number', 'year'))
                or metadata['document_type']['raw'] not in (SEMA_KIND, 'SEMA')):
            raise ValueError('judicial_guidance 仅接受最高法院官方来源及完整观察身份的 SEMA')
        # 前述通用校验保证每个已观察字段均有实际来源声明；额外限制为 MA 主机。
        for name in ('title', 'document_type', 'legal_identity'):
            official_url(metadata[name]['source_url'], 'SEMA ' + name + ' 来源', MA_GUIDANCE_HOSTS)
        metadata = copy.deepcopy(metadata)
        metadata['document_type']['meaning'] = SEMA_MEANING
    elif kind != 'regulation':
        raise ValueError('必须提供观察到的法规标题，且 document_type 为 regulation 或已核实 SEMA judicial_guidance')
    return metadata


def prepare_item(item, platforms, manifest_dir, max_bytes):
    if not isinstance(item, dict):
        raise ValueError('manifest 条目必须是对象')
    platform = item.get('platform')
    if not isinstance(platform, str) or platform not in platforms or platform not in ADAPTERS:
        raise ValueError('platform 必须在国家索引和适配器白名单中')
    hosts = ADAPTERS[platform].allowed_hosts
    download_url = official_url(item.get('download_url'), 'download_url', hosts)
    detail_url = item.get('detail_url')
    if detail_url is not None:
        official_url(detail_url, 'detail_url', hosts)
    final_url = item.get('final_url')
    if final_url is None:
        if item.get('final_url_verified') not in (None, False):
            raise ValueError('未提供 final_url 时不能声称 final_url_verified=true')
        final_url, final_verified = download_url, False
    else:
        official_url(final_url, 'final_url', hosts)
        if item.get('final_url_verified') is not True:
            raise ValueError('显式 final_url 必须同时提供 final_url_verified=true')
        final_verified = True
    metadata = validate_metadata(item.get('metadata'), hosts)
    if metadata['document_type']['value'] == 'judicial_guidance' and platform != 'mahkamahagung.go.id':
        raise ValueError('SEMA 司法指导文件仅允许最高法院平台导入')
    local = item.get('local_file')
    if not isinstance(local, str) or not local.strip():
        raise ValueError('local_file 必须是本地文件路径')
    local = Path(local).expanduser()
    local = (manifest_dir / local).resolve() if not local.is_absolute() else local.resolve()
    if not local.is_file():
        raise ValueError('local_file 不是可读取的普通文件')
    if local.stat().st_size > max_bytes:
        raise ValueError('本地文件超过 --max-mib 上限；如已核实大文件可显式提高上限')
    with local.open('rb') as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError('本地文件读取时超过 --max-mib 上限；未截断导入')
    return dict(platform=platform, download_url=download_url, detail_url=detail_url,
                final_url=final_url, final_url_verified=final_verified,
                declared_final_url=item.get('final_url'),
                metadata=metadata, local_file=str(local), data=data)


def retain_existing_evidence(store, prepared):
    """仅同一内容、同一来源用已有观察补足本次未知；不跨版本或来源借用证据。"""
    metadata = copy.deepcopy(prepared['metadata'])
    final_url, final_verified = prepared['final_url'], prepared['final_url_verified']
    preserved_fields, preserved_final = [], False
    previous = store.by_hash(hashlib.sha256(prepared['data']).hexdigest())
    key = (prepared['platform'], prepared['detail_url'], prepared['download_url'])
    source = next((s for s in previous['sources']
                   if (s.get('platform'), s.get('detail_url'), s.get('download_url')) == key), None) if previous else None
    if source:
        for name, old in source.get('metadata', {}).items():
            incoming = metadata.get(name)
            if (isinstance(old, dict) and old.get('status') == 'observed'
                    and (incoming is None or isinstance(incoming, dict)
                         and incoming.get('status') != 'observed')):
                metadata[name] = copy.deepcopy(old)
                preserved_fields.append(name)
        if not final_verified and source.get('final_url_verified') is True:
            final_url = official_url(source['final_url'], '已有 final_url',
                                     ADAPTERS[prepared['platform']].allowed_hosts)
            final_verified, preserved_final = True, True
    return metadata, final_url, final_verified, preserved_fields, preserved_final


def run(args):
    method = getattr(args, 'acquisition_method', None) or 'browser_download'
    import_mode = 'browser_import' if method == 'browser_download' else 'official_file_import'
    declaration_key = 'browser_import' if method == 'browser_download' else 'official_file_import'
    report_name = 'browser-import-last-run' if method == 'browser_download' else 'official-file-import-last-run'
    manifest = args.manifest.resolve()
    payload = getattr(args, 'manifest_payload', None)
    if payload is None:
        payload = json.loads(manifest.read_text(encoding='utf-8-sig'))
    items = payload if isinstance(payload, list) else [payload]
    if not items:
        raise ValueError('manifest 不能为空')
    platforms = getattr(args, 'selected_platforms', None) or index_platforms(args.index)
    country = args.country if getattr(args, 'verified_jurisdiction', False) else index_country(args.index, getattr(args, 'country', None))
    directory = getattr(args, 'selected_directory', None) or country_download_dir(args.config, args.root, args.index, country)
    validate_platform_countries(platforms, country)
    # 报告不是 原件 sidecar，不使用后者的 schema_version=2，以免被存储重建误认。
    report = dict(report_schema_version=1, country=country, mode=import_mode, acquisition_method=method, manifest=str(manifest),
                  download_dir=str(directory), started_at=now(), max_mib=args.max_mib,
                  network_requests=0, items=[], files=[], counts={}, errors=[])
    files = {}
    with CrawlStore(directory) as store:
        report['migration_errors'] = store.migration_errors
        batch = getattr(args, 'batch', None)
        prior = getattr(args, 'prior_states', {})
        for index, item in enumerate(items):
            key = str(index)
            try:
                previous = prior.get(key, {})
                cached = store.by_hash(previous.get('original_sha256', '')) if previous.get('status') == 'success' else None
                if not getattr(args, 'rerun_all', False) and cached and store.valid(cached):
                    batch.record(key, 'success', '沿用本批次此前成功导入，缓存校验通过', attempts=0, original_sha256=previous['original_sha256'], result='existing')
                    report['counts']['existing'] = report['counts'].get('existing', 0) + 1
                    files[cached['file']['sha256']] = dict(sha256=cached['file']['sha256'], file=str(directory / cached['file']['path']), media_type=cached['file']['media_type'], size_bytes=cached['file']['size_bytes'])
                    report['items'].append(dict(index=index, result='existing', reason='此前成功，本地缓存有效'))
                    continue
                if batch:
                    batch.record(key, 'running', '读取已下载的本地文件', attempts=0)
                prepared = prepare_item(item, platforms, manifest.parent, args.max_mib * 1024 * 1024)
                metadata, final_url, final_verified, kept_fields, kept_final = retain_existing_evidence(store, prepared)
                # refresh=True 确保本次实际下载字节接受 SHA 判断；不使用 URL 成功缓存跳过读取。
                result, record = store.save(
                    prepared['download_url'], prepared['detail_url'], prepared['platform'],
                    lambda _url: (prepared['data'], final_url),
                    metadata, refresh=True, convert_text=getattr(args, 'convert_text', False))
                source = next(s for s in record['sources']
                              if (s['platform'], s['detail_url'], s['download_url']) ==
                              (prepared['platform'], prepared['detail_url'], prepared['download_url']))
                source.update(discovery_method=method,
                              final_url_verified=final_verified)
                # 保留本次声明，不把此前验证终点/日期误写成本次浏览器重新观察到的内容。
                source[declaration_key] = dict(
                    local_file=prepared['local_file'], imported_at=now(),
                    metadata=copy.deepcopy(prepared['metadata']),
                    final_url=prepared['declared_final_url'],
                    final_url_verified=prepared['final_url_verified'],
                    preserved_metadata_fields=kept_fields, preserved_final_url=kept_final)
                store.persist(record)
                sha = record['file']['sha256']
                files[sha] = dict(sha256=sha, file=str(directory / record['file']['path']),
                                  size_bytes=record['file']['size_bytes'], media_type=record['file']['media_type'], source_count=len(record['sources']))
                report['counts'][result] = report['counts'].get(result, 0) + 1
                report['items'].append(dict(index=index, platform=prepared['platform'], result=result,
                                            sha256=sha, local_file=prepared['local_file'],
                                            download_url=prepared['download_url'], final_url=prepared['final_url'],
                                            final_url_verified=prepared['final_url_verified'],
                                            stored_final_url=final_url, stored_final_url_verified=final_verified,
                                            **{declaration_key:copy.deepcopy(source[declaration_key])},
                                            discovery_method=method))
                if batch:
                    batch.record(key, 'success', '离线文件校验及导入成功', attempts=0, result=result, original_sha256=hashlib.sha256(prepared['data']).hexdigest(), file=files[sha]['file'])
                print(f'{result}: {files[sha]["file"]}', flush=True)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                report['errors'].append(dict(index=index, error=str(exc)))
                if batch:
                    batch.record(key, 'failed', str(exc), attempts=0, error_type=type(exc).__name__)
        store.update_current_selection(country)
        report.update(files=list(files.values()), unique_files=len(files), finished_at=now())
        if batch:
            report.update(jurisdiction=country, requested_mode='content', batch_id=batch.batch_id, states=batch.states)
            batch.finish(report)
            from law_crawler.batch_runtime import emit_report
            emit_report(report, directory, report_name)
        else:
            write_json(directory / (report_name+'.json'), report)
    print(f'官方文件导入（{method}）: {len(files)} 份不同内容；错误: {len(report["errors"])}；报告: {directory / (report_name+".json")}')
    return 1 if report['errors'] or not files else 0


def main(argv=None):
    from law_crawler.batch_runtime import BatchRuntime, safe_id, emit_report
    from law_crawler.jurisdiction_registry import resolve_jurisdiction
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--config', type=Path, help='配置路径；默认读取工作区或源码工程skill的countries配置')
    parser.add_argument('--index', type=Path)
    parser.add_argument('--jurisdiction', '--country', dest='jurisdiction', help='工程已登记的适用国家/法域')
    parser.add_argument('--mode', choices=('content',), help='浏览器文件导入必须显式选择 content')
    parser.add_argument('--manifest', type=Path, help='新批次必填；条目 local_file 相对 manifest 目录')
    parser.add_argument('--max-mib', type=int, default=40)
    parser.add_argument('--acquisition-method', choices=('browser_download','official_cli_download'),
                        help='新批次原件取得方式；默认browser_download，CLI真实下载须明确声明')
    parser.add_argument('--batch-id')
    parser.add_argument('--rerun-batch')
    parser.add_argument('--rerun-all', action='store_true')
    args = parser.parse_args(argv)
    args.root = args.root.expanduser().resolve()
    if args.max_mib <= 0:
        parser.error('--max-mib 必须大于 0')
    args.config = configuration_path(args.root, args.config)
    batch = None
    try:
        base = download_dir(args.config, args.root)
        if args.rerun_batch:
            safe_id(args.rerun_batch)
            batch_dir = base / '.batches' / args.rerun_batch
            if batch_dir.is_symlink() or batch_dir.resolve().parent != (base / '.batches').resolve():
                raise ValueError('原批次目录无效')
            plan_path = batch_dir / 'plan.json'
            if plan_path.is_symlink():
                raise ValueError('原批次计划不能为符号链接')
            plan_bytes = plan_path.read_bytes()
            plan = json.loads(plan_bytes.decode('utf-8'))
            if plan.get('kind') not in ('browser_import','official_file_import') or plan.get('mode') != 'content':
                raise ValueError('批次不是官方原文文件导入任务')
            original_method=plan.get('acquisition_method','browser_download')
            if args.acquisition_method not in (None,original_method):
                raise ValueError('重跑不能改变原件取得方式')
            args.acquisition_method=original_method
            selection = resolve_jurisdiction(args.root, args.jurisdiction or plan['jurisdiction'], args.index)
            if selection.name == 'EU':
                raise ValueError('浏览器单国导入请明确选择EU索引中的成员国')
            if selection.name != plan['jurisdiction'] or args.mode not in (None, plan['mode']):
                raise ValueError('重跑不能改变法域或模式')
            if args.manifest is not None:
                raise ValueError('重跑使用原计划 manifest 快照，不能另传 --manifest')
            relocation = plan_relocation(base, args.rerun_batch, plan_bytes)
            args.manifest = relocation.map_path(plan['manifest'])
            args.max_mib = plan['max_mib']
            if not relocation.matches_download_base(plan['download_base']):
                raise ValueError('重跑不能改变配置下载基础目录')
            attempts = sorted((batch_dir / 'attempts').glob('*/states.json'))
            args.prior_states = json.loads(attempts[-1].read_text(encoding='utf-8'))['states'] if attempts else {}
            batch = BatchRuntime(base / '.batches', rerun_from=args.rerun_batch, batch_id=args.batch_id)
        else:
            if args.rerun_all:
                raise ValueError('--rerun-all 须同时提供 --rerun-batch')
            selection = resolve_jurisdiction(args.root, args.jurisdiction, args.index)
            if selection.name == 'EU':
                raise ValueError('浏览器单国导入请明确选择EU索引中的成员国')
            if args.mode != 'content' or args.manifest is None:
                raise ValueError('新浏览器导入任务必须显式提供 --mode content 和 --manifest')
            args.manifest = args.manifest.expanduser()
            if not args.manifest.is_absolute():
                args.manifest = args.root / args.manifest
            args.manifest = args.manifest.resolve()
            # 保存原始 manifest 文本后才解析条目；错误 JSON 也有完整批次失败报告。
            raw = args.manifest.read_text(encoding='utf-8-sig')
            args.acquisition_method=args.acquisition_method or 'browser_download'
            plan = dict(kind='browser_import' if args.acquisition_method=='browser_download' else 'official_file_import',
                        acquisition_method=args.acquisition_method, jurisdiction=selection.name, mode='content',
                        index=str(selection.index), manifest=str(args.manifest),
                        manifest_raw=raw, manifest_directory=str(args.manifest.parent),
                        manifest_snapshot='manifest-snapshot.json', download_base=str(base),
                        max_mib=args.max_mib, network_requests=0)
            batch = BatchRuntime(base / '.batches', plan, batch_id=args.batch_id)
        args.index, args.country = selection.index, selection.name
        args.selected_platforms = selection.platforms
        args.verified_jurisdiction = True
        args.selected_directory = base / selection.name
        if args.selected_directory.is_symlink() or args.selected_directory.resolve().parent != base:
            raise ValueError('国家目录不能为符号链接或逃逸基础目录')
        validate_platform_countries(args.selected_platforms, selection.name)
        args.batch, args.convert_text = batch, True
        payload = json.loads(batch.plan['manifest_raw'])
        items = payload if isinstance(payload, list) else [payload]
        if not items:
            raise ValueError('manifest 不能为空')
        snapshot = copy.deepcopy(items)
        for item in snapshot:
            if isinstance(item, dict) and isinstance(item.get('local_file'), str):
                local = Path(item['local_file']).expanduser()
                item['local_file'] = str((Path(batch.plan['manifest_directory']) / local).resolve() if not local.is_absolute() else local.resolve())
        snapshot_path = batch.batch_dir / 'manifest-snapshot.json'
        if args.rerun_batch:
            if snapshot_path.is_symlink():
                raise ValueError('manifest 快照不能为符号链接')
            if snapshot_path.exists() and json.loads(snapshot_path.read_text(encoding='utf-8')) != snapshot:
                raise ValueError('原批次 manifest 快照已变化')
        else:
            write_json(snapshot_path, snapshot)
        # 先用原路径核对冻结快照，再为本轮读取建立迁移视图；原快照字节不变。
        runtime_snapshot = copy.deepcopy(snapshot)
        if args.rerun_batch:
            for item in runtime_snapshot:
                if isinstance(item, dict) and isinstance(item.get('local_file'), str):
                    item['local_file'] = str(relocation.map_path(item['local_file']))
        args.manifest_payload = runtime_snapshot
        for index in range(len(snapshot)):
            batch.record(str(index), 'pending', '等待离线导入', attempts=0)
        return run(args)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if batch and not batch.finished:
            batch.record('manifest', 'failed', str(exc), attempts=0)
            method=batch.plan.get('acquisition_method','browser_download')
            report = dict(country=batch.plan.get('jurisdiction'), mode='browser_import' if method=='browser_download' else 'official_file_import',
                          acquisition_method=method,
                          requested_mode='content', network_requests=0, counts={'failed': 1},
                          errors=[dict(error=str(exc))], states=batch.states)
            batch.finish(report)
            directory = base / batch.plan['jurisdiction']
            if not directory.is_symlink() and directory.resolve().parent == base:
                emit_report(report, directory, 'browser-import-last-run' if method=='browser_download' else 'official-file-import-last-run')
        parser.exit(1, f'错误: {exc}\n')


if __name__ == '__main__':
    sys.exit(main())
