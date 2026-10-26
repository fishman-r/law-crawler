#!/usr/bin/env python3
"""把工程skill包安装到指定agent的原生技能目录；不修改agent权限配置。"""
import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / 'skill/law-crawler'


def skills_root(agent, scope='user', project=None):
    if scope == 'project':
        project = Path(project or ROOT).expanduser().resolve()
        return project / {'codex': '.agents/skills', 'claude': '.claude/skills', 'qoder': '.qoder/skills', 'deepseek-harness': '.dsh/skills', 'opencode': '.opencode/skills', 'workbuddy': '.workbuddy/skills'}[agent]
    if agent == 'qoder':
        return Path.home() / '.qoder/skills'
    if agent == 'deepseek-harness':
        raise ValueError('DeepSeek Harness用户目录取决于dshHome配置；用--dest指定实际skills目录，或选择--scope project')
    if agent == 'codex':
        return Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'skills'
    if agent == 'claude':
        return Path.home() / '.claude/skills'
    if agent == 'opencode':
        return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'opencode/skills'
    if agent == 'workbuddy':
        return Path.home() / '.workbuddy/skills'
    raise ValueError(f'未知agent: {agent}；使用--dest指定其他工具的技能集合目录')


def install(agent, scope='user', project=None, destination=None, source=SKILL):
    """destination指技能集合目录，返回安装后的具体skill目录。"""
    source = Path(source).resolve()
    if not (source / 'SKILL.md').is_file():
        raise ValueError('skill源码缺少SKILL.md；先取得完整skill包，再选择安装目录')
    directory = Path(destination).expanduser() if destination else skills_root(agent, scope, project)
    target = directory.resolve() / source.name
    if target == source or source in target.parents or target in source.parents:
        raise ValueError('安装目录不能与工程skill源码重叠')
    if target.is_symlink():
        if target.resolve() == source:
            return target
        raise ValueError('安装目标已链接其他源码；请先核对，不自动替换')
    if target.exists():
        raise ValueError('安装目标已存在；请核对旧副本，不自动覆盖或删除')
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.symlink_to(source, target_is_directory=True)
    except OSError as exc:
        raise ValueError('无法建立目录链接；请配置agent自定义技能目录或显式加载源码SKILL.md，不自动生成副本') from exc
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent', choices=('codex', 'claude', 'qoder', 'deepseek-harness', 'opencode', 'workbuddy'), required=True)
    parser.add_argument('--scope', choices=('user', 'project'), default='user')
    parser.add_argument('--project', help='项目级安装的工程路径')
    parser.add_argument('--dest', help='自定义技能集合目录（其下创建law-crawler）')
    parser.add_argument('--source', type=Path, default=SKILL, help='完整skill包路径，默认使用当前工程skill源码')
    args = parser.parse_args()
    try:
        print(f'已安装 {args.agent} skill: {install(args.agent, args.scope, args.project, args.dest, args.source)}')
    except (OSError, ValueError) as exc:
        parser.exit(1, f'错误: {exc}\n')


if __name__ == '__main__':
    main()
