"""统一入口只转发实现；测试发现须隔离同名模块并拒绝空组。"""
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run as runner


class CommandDispatchTests(unittest.TestCase):
    def test_commands_forward_arguments_and_preserve_exit_status(self):
        modules = {
            'skill': 'law_crawler.__main__', 'web': 'observer.server',
            'claude': 'observer.launch', 'events': 'observer.events',
        }
        original = sys.argv
        for command, module_name in modules.items():
            with self.subTest(command=command):
                received = []
                def main():
                    received.append(sys.argv[1:])
                    return 7
                with patch.object(runner.importlib, 'import_module', return_value=SimpleNamespace(main=main)) as load:
                    self.assertEqual(runner.main([command, '--example', 'with spaces']), 7)
                load.assert_called_once_with(module_name)
                self.assertEqual(received, [['--example', 'with spaces']])
                self.assertIs(sys.argv, original)

    def test_top_level_help_lists_all_public_commands(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(runner.main(['--help']), 0)
        for command in ('skill', 'web', 'claude', 'events', 'test'):
            self.assertIn(command, output.getvalue())


class GroupedDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='law-crawler-runner-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        deployment = self.root / 'deploy'
        deployment.mkdir()
        self.script = deployment / 'run.py'
        shutil.copy2(runner.__file__, self.script)
        for group in runner.TEST_GROUPS:
            (self.root / group / 'tests').mkdir(parents=True)

    def write_group(self, group, success=True):
        directory = self.root / group / 'tests'
        (directory / 'helper.py').write_text(f'GROUP = {group!r}\n', encoding='utf-8')
        (directory / 'test_identical_name.py').write_text(
            'import unittest\n'
            'from tests.helper import GROUP\n'
            'class DiscoveryTests(unittest.TestCase):\n'
            '    def test_group(self):\n'
            f'        self.assertEqual(GROUP, {group!r})\n'
            f'        self.assertTrue({success!r})\n', encoding='utf-8')

    def command(self, *arguments):
        environment = dict(os.environ, PYTHONPATH='', PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run([sys.executable, str(self.script), 'test', *arguments],
                              cwd=self.tmp.name, env=environment, capture_output=True,
                              text=True, encoding='utf-8')

    def test_all_groups_discover_identically_named_modules_and_their_own_helpers(self):
        for group in runner.TEST_GROUPS:
            self.write_group(group)
        result = self.command('--group', 'all', '-q')
        self.assertEqual(result.returncode, 0, result.stderr)
        for group in runner.TEST_GROUPS:
            self.assertIn(f'[{group}] 发现 1 个测试', result.stdout)
        self.assertEqual(result.stderr.count('Ran 1 test'), 3)

    def test_selected_group_is_reported_and_verbosity_is_forwarded(self):
        self.write_group('web')
        result = self.command('--group', 'web', '-v')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('[web] 发现 1 个测试', result.stdout)
        self.assertNotIn('[skill]', result.stdout)
        self.assertNotIn('[deploy]', result.stdout)
        self.assertIn('test_group (', result.stderr)

    def test_empty_group_cannot_report_success(self):
        result = self.command('--group', 'deploy', '-q')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('[deploy] 发现 0 个测试', result.stdout)
        self.assertIn('测试组为空', result.stderr)

    def test_a_failed_group_does_not_hide_later_group_counts(self):
        self.write_group('skill', success=False)
        self.write_group('web')
        self.write_group('deploy')
        result = self.command('-q')
        self.assertNotEqual(result.returncode, 0)
        for group in runner.TEST_GROUPS:
            self.assertIn(f'[{group}] 发现 1 个测试', result.stdout)
        self.assertIn('FAILED', result.stderr)

    def test_missing_group_directory_cannot_report_success(self):
        (self.root / 'web/tests').rmdir()
        result = self.command('--group', 'web', '-q')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('测试目录不存在', result.stderr)


if __name__ == '__main__':
    unittest.main()
