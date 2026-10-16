"""源码与独立包边界；只在临时目录测试只读检查、默认目录和CLI入口。"""
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from law_crawler import download_regulations
from law_crawler.resources import skill_directory
from tools import check_skill


ROOT = Path(__file__).resolve().parents[1]


class SingleSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.skill = Path(self.tmp.name) / 'law-crawler'
        shutil.copytree(check_skill.SKILL, self.skill, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))

    def snapshot(self):
        return {p.relative_to(self.skill): p.read_bytes() for p in self.skill.rglob('*') if p.is_file()}

    def test_check_is_read_only(self):
        before = self.snapshot()
        self.assertEqual(check_skill.validate(self.skill), [])
        self.assertEqual(self.snapshot(), before)

    def test_missing_dependency_and_invalid_source_are_reported_without_writes(self):
        (self.skill / 'scripts/law_crawler/crawl_store.py').unlink()
        (self.skill / 'scripts/rebuild_markdown.py').write_text('def invalid(\n', encoding='utf-8')
        before = self.snapshot()
        errors = check_skill.validate(self.skill)
        self.assertTrue(any('crawl_store' in error for error in errors), errors)
        self.assertTrue(any('rebuild_markdown' in error for error in errors), errors)
        self.assertEqual(self.snapshot(), before)

    def test_old_generation_manifest_is_reported_without_writes(self):
        (self.skill / '.generated-files.json').write_text('{}', encoding='utf-8')
        before = self.snapshot()
        self.assertTrue(check_skill.validate(self.skill))
        self.assertEqual(self.snapshot(), before)

    def test_repository_imports_load_only_canonical_skill_source(self):
        import law_crawler
        self.assertEqual(Path(law_crawler.__file__).resolve(), check_skill.SKILL / 'scripts/law_crawler/__init__.py')
        self.assertEqual(Path(download_regulations.__file__).resolve(), check_skill.SKILL / 'scripts/law_crawler/download_regulations.py')
        self.assertIs(sys.modules['law_crawler.download_regulations'], download_regulations)
        self.assertEqual(sorted(p.name for p in (ROOT / 'law_crawler').glob('*.py')), ['__init__.py'])


class PublicEntryPointTests(unittest.TestCase):
    def test_repository_defaults_and_resources_do_not_follow_cwd_or_package_folder(self):
        self.assertEqual(download_regulations.ROOT, ROOT)
        self.assertEqual(skill_directory(), ROOT / 'skills/law-crawler')
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.properties'
            config.write_text('downdloadDir=downloads\n', encoding='utf-8')
            self.assertEqual(download_regulations.download_dir(config), ROOT / 'downloads')
            result = subprocess.run([sys.executable, str(ROOT / 'download_regulations.py'),
                '--jurisdiction', 'UnregisteredLayoutTest', '--mode', 'metadata', '--plan-only'],
                cwd=tmp, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 1)
            self.assertIn('Canada', result.stderr)
            self.assertNotIn('无可用索引', result.stderr)
            self.assertFalse((Path(tmp) / 'downloads').exists())

    def test_public_commands_and_observer_tools_still_expose_help(self):
        commands = [[sys.executable, str(ROOT / name), '--help'] for name in
                    ('download_regulations.py', 'import_browser_download.py', 'import_body_review.py', 'rebuild_markdown.py')]
        commands += [[sys.executable, '-m', 'law_crawler', '--help'],
                     [sys.executable, '-m', 'law_crawler', 'prepare', '--help'],
                     [sys.executable, '-m', 'observer.server', '--help'],
                     [sys.executable, str(ROOT / 'tools/configure_server.py'), '--help']]
        for command in commands:
            with self.subTest(command=command):
                result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout)


if __name__ == '__main__':
    unittest.main()
