"""按批次从本地原始响应重建Markdown，不访问网络或改变法规原件。"""
import argparse
from copy import deepcopy
import json
import sys
from pathlib import Path

from law_crawler.batch_runtime import BatchRuntime, emit_report, safe_id
from law_crawler.crawl_store import CrawlStore, now, write_json
from law_crawler.download_regulations import download_dir, country_download_dir
from law_crawler.resources import configuration_path
from law_crawler.jurisdiction_registry import resolve_jurisdiction
from law_crawler.text_documents import CONVERSION_METHOD
from law_crawler.relocation import plan_relocation


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--jurisdiction')
    parser.add_argument('--config', type=Path)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--batch-id')
    group.add_argument('--rerun-batch')
    parser.add_argument('--force', action='store_true', help='同版本也重新计算；相同输出不重复备份')
    args = parser.parse_args(argv)
    try:
        return execute(args, parser)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # 参数语法错误仍由 argparse 以状态 2 退出；运行前置条件失败返回状态 1。
        print(f'错误: {exc}', file=sys.stderr)
        return 1


def execute(args, parser):
    root = args.root.resolve()
    config = configuration_path(root, args.config)
    if not config.is_file():
        raise ValueError(f'配置文件不存在或不是文件: {config}')
    base = download_dir(config, root)
    plan_dir = base/'.batches'
    if plan_dir.is_symlink():
        parser.error('批次根目录不得为符号链接')
    if args.rerun_batch:
        batch_path = plan_dir/safe_id(args.rerun_batch)
        if batch_path.is_symlink() or (batch_path/'plan.json').is_symlink():
            parser.error('批次目录/计划不得为符号链接')
        plan_path = batch_path/'plan.json'
        if not plan_path.is_file():
            raise ValueError(f'找不到原批次计划: {plan_path}')
        plan_bytes = plan_path.read_bytes()
        plan = json.loads(plan_bytes.decode('utf-8'))
        if not isinstance(plan, dict):
            raise ValueError('原批次计划必须为 JSON 对象')
        required = ('jurisdiction', 'download_base', 'force', 'directory', 'files')
        missing = [field for field in required if field not in plan]
        if missing:
            raise ValueError('原批次计划缺少字段: ' + ', '.join(missing))
        if plan.get('mode') != 'offline_markdown_rebuild' or plan.get('conversion_method') != CONVERSION_METHOD:
            parser.error('批次类型或目标转换版本不符；请新建批次')
        selection = resolve_jurisdiction(root, args.jurisdiction or plan['jurisdiction'])
        relocation = plan_relocation(base, args.rerun_batch, plan_bytes)
        if selection.name != plan['jurisdiction'] or not relocation.matches_download_base(plan['download_base']):
            parser.error('重跑不得改变法域或下载目录')
        if args.force and not plan['force']:
            parser.error('重跑不得改变force选项')
    else:
        selection = resolve_jurisdiction(root, args.jurisdiction)
        if len(selection.countries) != 1:
            parser.error('离线重建一次须明确一个已登记国家')
        directory = country_download_dir(config, root, selection.index, selection.name)
        files = []
        for path in sorted(directory.glob('*.json')):
            if path.is_symlink():
                continue
            record = json.loads(path.read_text(encoding='utf-8'))
            if (not isinstance(record, dict) or record.get('schema_version') != 2
                    or record.get('file', {}).get('media_type') != 'text/markdown'
                    or not record.get('original_file')):
                continue
            original = record['original_file']
            conversion = record.get('conversion', {})
            files.append(dict(key=original['sha256'], metadata_file=path.name,
                original_file=deepcopy(original), conversion_metadata=deepcopy(conversion.get('metadata')),
                source_url=conversion.get('source_url'), previous_method=conversion.get('method')))
        plan = dict(schema_version=1, jurisdiction=selection.name, mode='offline_markdown_rebuild',
            created_at=now(), download_base=str(base), directory=str(directory), files=files,
            force=args.force, conversion_method=CONVERSION_METHOD,
            steps=['冻结原响应身份与转换证据', '备份旧Markdown及元数据',
                   '从已验证原字节离线重建并更新派生哈希', '校验索引、去重及幂等性并输出报告'])
    directory = country_download_dir(config, root, selection.index, selection.name)
    planned_directory = relocation.map_path(plan['directory']) if args.rerun_batch else Path(plan['directory'])
    if str(directory) != str(planned_directory):
        parser.error('重跑国家目录与原计划不一致')
    report = dict(title='法规Markdown结构重建报告', jurisdiction=selection.name,
        mode='offline_markdown_rebuild', conversion_method=CONVERSION_METHOD,
        counts={}, files=[], errors=[], network_requests=0,
        directory=str(directory), note='只改变派生Markdown；原件、来源和请求计数保留，旧输出在conversion-history中可追溯。')
    with BatchRuntime(plan_dir, plan_dict=None if args.rerun_batch else plan,
                      batch_id=args.batch_id, rerun_from=args.rerun_batch) as batch:
        with CrawlStore(directory) as store:
            report['initial_integrity_observations'] = store.migration_errors
            for job in plan['files']:
                key = job['key']
                try:
                    path = (directory/job['metadata_file']).resolve()
                    if not path.is_relative_to(directory) or path.parent != directory:
                        raise ValueError('元数据路径越出国家目录')
                    record = json.loads(path.read_text(encoding='utf-8'))
                    conversion = record.get('conversion', {})
                    if (record['original_file'] != job['original_file']
                            or conversion.get('metadata') != job['conversion_metadata']
                            or conversion.get('source_url') != job['source_url']):
                        raise ValueError('本地原件身份或冻结转换来源已变化；请新建批次')
                    batch.record(key, 'running', '验证本地原响应并重建派生结构', attempts=0)
                    updated = store.upgrade_conversion(record, force=plan['force'])
                    if not store.valid(record):
                        raise ValueError('重建结果未通过原件/派生文件双重完整性检查')
                    result = 'rebuilt' if updated else 'already_current'
                    report['counts'][result] = report['counts'].get(result, 0) + 1
                    item = dict(file=str(directory/record['file']['path']), metadata_file=str(path),
                        original_sha256=key, sha256=record['file']['sha256'], result=result,
                        previous_method=job['previous_method'], conversion_method=CONVERSION_METHOD,
                        history=record.get('conversion_history', []))
                    report['files'].append(item)
                    batch.record(key, 'success', result, attempts=0, **item)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    report['errors'].append(dict(original_sha256=key, error=str(exc)))
                    batch.record(key, 'fail', str(exc), attempts=0)
            # 有失败项时不通过全库persist副作用重建被本轮拒绝的来源；
            # 全部完成后再统一刷新清单，使失败项严格遵循冻结计划。
            report['selection_index_updated'] = not report['errors']
            if not report['errors']:
                store.update_current_selection(selection.name)
        report['result'] = 'completed_with_errors' if report['errors'] else 'completed'
        paths = batch.finish(report)
    payload = json.loads(paths['json'].read_text(encoding='utf-8'))
    emit_report(payload, directory, 'markdown-rebuild-last-run')
    print(json.dumps(dict(batch_id=batch.batch_id, attempt=batch.attempt_number,
        counts=report['counts'], errors=report['errors'], report=str(paths['html'])), ensure_ascii=False))
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
