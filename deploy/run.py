#!/usr/bin/env python3
"""工程入口：转发 skill、观测台、Claude 启动器、事件命令及分组测试。"""
import argparse
from contextlib import contextmanager
import importlib
from importlib.machinery import ModuleSpec
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMMANDS = {
    'skill': 'law_crawler.__main__',
    'web': 'observer.server',
    'claude': 'observer.launch',
    'events': 'observer.events',
}
TEST_GROUPS = ('skill', 'web', 'deploy')


def bootstrap():
    """只加入工程模块路径；独立 skill 不依赖此入口。"""
    paths = [str(ROOT / name) for name in TEST_GROUPS]
    sys.path[:] = paths + [path for path in sys.path if path not in paths]


def dispatch(command, arguments):
    """保持各模块自己的参数解析与退出码。"""
    module = importlib.import_module(COMMANDS[command])
    original = sys.argv
    try:
        sys.argv = [f'{original[0]} {command}', *arguments]
        return module.main()
    finally:
        sys.argv = original


@contextmanager
def test_package(directory):
    """固定辅助导入 tests.* 的归属，避免同名目录或已安装包抢占。"""
    previous = {name: value for name, value in sys.modules.items()
                if name == 'tests' or name.startswith('tests.')}
    for name in previous:
        del sys.modules[name]
    package = ModuleType('tests')
    package.__package__ = 'tests'
    package.__path__ = [str(directory)]
    package.__spec__ = ModuleSpec('tests', loader=None, is_package=True)
    package.__spec__.submodule_search_locations = package.__path__
    sys.modules['tests'] = package
    try:
        yield
    finally:
        for name in list(sys.modules):
            if name == 'tests' or name.startswith('tests.'):
                del sys.modules[name]
        sys.modules.update(previous)


def run_test_group(group, verbosity):
    """每个子进程只发现一个目录，top-level 明确指向当前测试目录。"""
    directory = ROOT / group / 'tests'
    if not directory.is_dir():
        print(f'[{group}] 错误：测试目录不存在：{directory}', file=sys.stderr, flush=True)
        return 2
    with test_package(directory):
        loader = unittest.TestLoader()
        suite = loader.discover(str(directory), pattern='test_*.py', top_level_dir=str(directory))
        count = suite.countTestCases()
        print(f'[{group}] 发现 {count} 个测试', flush=True)
        if not count:
            print(f'[{group}] 错误：测试组为空，拒绝报告成功', file=sys.stderr, flush=True)
            return 2
        result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return 0 if result.wasSuccessful() else 1


def run_tests(groups, verbosity):
    """各组使用独立解释器，隔离 unittest 模块缓存和 discovery 状态。"""
    status = 0
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    for group in groups:
        command = [sys.executable, str(Path(__file__).resolve()), 'test',
                   '--group', group, '--_worker']
        if verbosity == 0:
            command.append('-q')
        elif verbosity > 1:
            command.append('-v')
        result = subprocess.run(command, cwd=ROOT, env=environment)
        if result.returncode:
            status = 1
    return status


def test_main(arguments):
    parser = argparse.ArgumentParser(prog=f'{sys.argv[0]} test', description='发现并运行三处测试目录；空组视为失败。')
    parser.add_argument('--group', choices=('all', *TEST_GROUPS), default='all')
    output = parser.add_mutually_exclusive_group()
    output.add_argument('-q', '--quiet', dest='verbosity', action='store_const', const=0, default=1)
    output.add_argument('-v', '--verbose', dest='verbosity', action='store_const', const=2)
    parser.add_argument('--_worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(arguments)
    if args._worker:
        if args.group == 'all':
            parser.error('内部测试进程必须指定单个测试组')
        return run_test_group(args.group, args.verbosity)
    groups = TEST_GROUPS if args.group == 'all' else (args.group,)
    return run_tests(groups, args.verbosity)


def main(argv=None):
    bootstrap()
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=(*COMMANDS, 'test'))
    if not arguments or arguments[0] in ('-h', '--help'):
        parser.print_help()
        return 0 if arguments else 2
    command = parser.parse_args(arguments[:1]).command
    if command == 'test':
        return test_main(arguments[1:])
    return dispatch(command, arguments[1:])


if __name__ == '__main__':
    raise SystemExit(main())
