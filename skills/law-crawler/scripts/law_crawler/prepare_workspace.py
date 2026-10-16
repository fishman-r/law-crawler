#!/usr/bin/env python3
"""检查独立skill工作区；显式--initialize时仅补齐首次使用的索引/配置模板。"""
import sys

if sys.version_info < (3, 10):
    print('错误: 需要完整的 Python 3.10+（含 sqlite3、ssl）；请选择正确的 Python 解释器。', file=sys.stderr)
    sys.exit(2)

import argparse
import json
from pathlib import Path

try:
    import sqlite3
    import ssl
    from law_crawler.jurisdiction_registry import resolve_jurisdiction
    from law_crawler.download_regulations import download_dir
    from law_crawler.platform_adapters import ADAPTERS
except ImportError as exc:
    print(f'错误: Python运行环境或skill包不完整（{exc}）；需要Python 3.10+及sqlite3、ssl和完整scripts目录。', file=sys.stderr)
    sys.exit(2)


try:
    from law_crawler.resources import skill_directory, country_directory, configuration_path
    COUNTRIES = skill_directory() / 'countries'
except (ImportError, ValueError) as exc:
    print(f'错误: {exc}', file=sys.stderr)
    sys.exit(2)


def bundled_index(jurisdiction):
    """直接解析skill内的规范目录，不生成第二套扁平索引。"""
    selection = resolve_jurisdiction(COUNTRIES.parent, jurisdiction)
    return selection.index.parent.name, selection.index.read_bytes()


def prepare(root, jurisdiction, initialize=False, config=None):
    root = Path(root).expanduser().resolve()
    countries = country_directory(root)
    if countries.is_symlink():
        raise ValueError('工作区countries目录不得为符号链接')
    config = configuration_path(root, config)
    if config.is_symlink():
        raise ValueError('配置文件不得为符号链接')
    # 已有配置先验证；坏配置不能借初始化静默覆盖。
    if config.exists():
        base = download_dir(config, root)
    elif not initialize:
        raise ValueError('配置缺失；确认工作区后用--initialize创建索引/配置模板')
    else:
        source = COUNTRIES / 'global-config.properties'
        if source.is_symlink() or not source.is_file():
            raise ValueError('skill配置模板缺失；重新取得完整skill包')
        base = download_dir(source, root)
    existing_indexes = list(countries.glob('*/index.md'))
    new_index = None
    if existing_indexes:
        # 不以打包模板补写已有索引未登记的法域。
        selection = resolve_jurisdiction(root, jurisdiction)
    elif initialize:
        name, data = bundled_index(jurisdiction)
        target = countries / name / 'index.md'
        if target.is_symlink() or target.parent.is_symlink() or target.exists():
            raise ValueError('索引目标已存在或使用符号链接；保留现有内容')
        new_index = (target, data)
    else:
        raise ValueError('工作区没有索引；确认法域后用--initialize初始化已打包的索引')
    initialized = []
    if new_index:
        target, data = new_index
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(data)
        initialized.append(str(target))
        selection = resolve_jurisdiction(root, jurisdiction)
    if not config.exists():
        if any(parent.is_symlink() for parent in config.parents):
            raise ValueError('配置父目录不得使用符号链接')
        config.parent.mkdir(parents=True, exist_ok=True)
        with config.open('xb') as stream:
            stream.write((COUNTRIES / 'global-config.properties').read_bytes())
        initialized.append(str(config))
    return dict(result='ready', jurisdiction=selection.name, work_root=str(root),
                index=str(selection.index), config=str(config), download_base=str(base),
                country_directories=[str(base / name) for name in selection.countries],
                platforms=[dict(host=host, adapter_supported=host in ADAPTERS)
                           for host in selection.platforms],
                initialized=initialized, network_requests=0,
                note='仅检查/初始化工作区；登记或适配不代表网络可达。未创建采集批次，未下载法规。')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path, help='用户选择的工作区；不使用skill安装目录')
    parser.add_argument('--jurisdiction', required=True, help='用户明确指定的已登记国家/法域')
    parser.add_argument('--config', type=Path, help='配置路径；相对工作区，默认countries/global-config.properties')
    parser.add_argument('--initialize', action='store_true', help='只在尚无索引时复制匹配模板，补齐缺失配置；不覆盖已有文件')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(prepare(args.root, args.jurisdiction, args.initialize, args.config), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, ImportError) as exc:
        print(f'错误: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
