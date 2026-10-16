#!/usr/bin/env python3
"""工程为源码：打包技能运行文件，校验漂移，可同步到本机技能目录。"""
import argparse
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills/download-official-regulations'


def payload():
    pairs = [(ROOT / name, SKILL / 'scripts' / name)
             for name in ('download_regulations.py', 'import_browser_download.py', 'import_body_review.py', 'rebuild_markdown.py', 'crawl_store.py', 'document_formats.py', 'regulation_metadata.py', 'jurisdiction_registry.py', 'regulation_selection.py', 'regulation_pipeline.py', 'text_documents.py', 'batch_runtime.py', 'file_lock.py')]
    pairs += [(path, SKILL / 'scripts/platform_adapters' / path.name)
              for path in sorted((ROOT / 'platform_adapters').glob('*.py'))]
    pairs += [(path, SKILL / 'assets' / f'{path.parent.name}-index.md')
              for path in sorted((ROOT / 'countries').glob('*/index.md'))]
    # assets/global-config.properties是独立维护的通用模板，不能打包工作区私有下载路径。
    return pairs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='只检查工程代码与技能包是否一致')
    parser.add_argument('--install', action='store_true', help='打包后同步到 CODEX_HOME/skills')
    args = parser.parse_args()
    if args.check and args.install:
        parser.error('--check 与 --install 不能同时使用')
    mismatches = []
    for source, target in payload():
        if args.check:
            if not target.exists() or source.read_bytes() != target.read_bytes():
                mismatches.append(str(target.relative_to(ROOT)))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    if mismatches:
        print('技能包需要同步:\n' + '\n'.join(mismatches))
        return 1
    if args.install:
        dest = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'skills' / SKILL.name
        if dest.resolve() == SKILL.resolve():
            raise ValueError('安装目录不能与工程技能源码相同')
        for source in SKILL.rglob('*'):
            if source.is_file() and '__pycache__' not in source.parts and source.suffix != '.pyc':
                target = dest / source.relative_to(SKILL)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        print(f'已同步本机安装版: {dest}')
    print('技能源码与运行文件一致' if args.check else f'已打包工程技能: {SKILL}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
