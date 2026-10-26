#!/usr/bin/env python3
"""只读检查完整skill源码及本地运行依赖，不生成或同步代码。"""
import argparse
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / 'skill/law-crawler'
# 历史独立上传入口只有这一份实现；不要求生成包内副本。
STANDALONE_ENTRYPOINTS = ('oss_archive_upload',)
ENTRYPOINTS = ('download_regulations', 'import_browser_download', 'import_body_review',
               'recovery_workflow', 'recovery_audit', 'recover_missing_original', 'attachment_recovery', 'coverage_matrix',
               'rebuild_markdown', 'prepare_workspace', 'browser_healthcheck', 'staged_pipeline', 'local_ocr', 'reconstruct_ocr_tables', 'automatic_tables', 'translation_tables', 'bulk_archive', 'bulk_download_fast', 'bulk_process', 'bulk_report')


def validate(skill=SKILL):
    skill = Path(skill).resolve()
    errors = []
    required = ['SKILL.md', 'countries/global-config.properties',
                'scripts/law_crawler/__init__.py', 'scripts/law_crawler/__main__.py',
                'scripts/law_crawler/apple_vision_ocr.swift', 'assets/translation-styles/zh-cn_en-gb.json']
    required += ['scripts/' + name + '.py' for name in ENTRYPOINTS]
    required += ['scripts/law_crawler/' + name + '.py' for name in ENTRYPOINTS]
    required += ['scripts/' + name + '.py' for name in STANDALONE_ENTRYPOINTS]
    for name in required:
        if not (skill / name).is_file():
            errors.append('缺少文件: ' + name)
    if not any((skill / 'countries').glob('*/index.md')):
        errors.append('缺少国家/法域索引: countries/*/index.md')
    if (skill / '.generated-files.json').exists():
        errors.append('存在旧生成清单；完整skill应直接维护源码')
    for path in sorted((skill / 'scripts').rglob('*.py')):
        if '__pycache__' in path.parts or path.name.startswith('._'):
            continue
        relative = path.relative_to(skill).as_posix()
        if path.parent == skill / 'scripts' and path.stem not in ENTRYPOINTS + STANDALONE_ENTRYPOINTS:
            errors.append('存在额外顶层运行副本: ' + relative)
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=relative)
        except (SyntaxError, UnicodeError, OSError) as exc:
            errors.append(f'无法解析 {relative}: {exc}')
            continue
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                modules = [node.module]
            for module in modules:
                if module == 'law_crawler' or module.startswith('law_crawler.'):
                    target = skill / 'scripts' / module.replace('.', '/')
                    if not target.with_suffix('.py').is_file() and not (target / '__init__.py').is_file():
                        errors.append(f'缺少内部依赖: {relative} -> {module}')
    if skill == SKILL.resolve():
        bridge = ROOT / 'skill/law_crawler'
        extras = [path for path in bridge.rglob('*.py')
                  if path != bridge / '__init__.py']
        errors.extend('skill兼容桥存在重复运行源码: ' + str(path.relative_to(ROOT)) for path in extras)
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skill', type=Path, default=SKILL, help='完整skill目录')
    args = parser.parse_args()
    try:
        errors = validate(args.skill)
    except OSError as exc:
        parser.exit(1, f'错误: {exc}\n')
    if errors:
        print('\n'.join(errors))
        return 1
    print('完整skill源码检查通过（只读，未生成副本）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
