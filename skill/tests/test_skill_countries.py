"""skill 内 countries 的资源定位及迁移兼容；只使用临时目录且禁止网络。"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from law_crawler.batch_runtime import BatchRuntime
from law_crawler.download_regulations import download_dir
from law_crawler.jurisdiction_registry import resolve_jurisdiction
from law_crawler.prepare_workspace import prepare
from law_crawler.regulation_pipeline import OPTIONS, execute
from law_crawler.resources import configuration_path, country_directory


SKILL = Path(__file__).resolve().parents[1] / 'law-crawler'
INDEX = '# Canada\n[Official legislation](https://laws-lois.justice.gc.ca/eng/)\n'


def project_fixture(root):
    """模拟源码工程，不引用真实下载目录，也不复制历史批次。"""
    skill = root / 'skill/law-crawler'
    index = skill / 'countries/Canada/index.md'
    index.parent.mkdir(parents=True)
    index.write_text(INDEX, encoding='utf-8')
    (skill / 'SKILL.md').write_text('---\nname: law-crawler\n---\n', encoding='utf-8')
    (skill / 'countries/global-config.properties').write_text('downdloadDir=./downloads/\n', encoding='utf-8')
    return skill


class CountryResourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='skill-countries-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def test_project_uses_skill_countries_without_recreating_root_countries(self):
        skill = project_fixture(self.root)
        self.assertEqual(country_directory(self.root), skill / 'countries')
        self.assertEqual(configuration_path(self.root), skill / 'countries/global-config.properties')
        self.assertEqual(resolve_jurisdiction(self.root, '加拿大').index,
                         skill / 'countries/Canada/index.md')
        with patch('socket.create_connection', side_effect=AssertionError('unexpected network request')):
            result = prepare(self.root, 'Canada', initialize=True)
        self.assertEqual(result['initialized'], [])
        self.assertEqual(Path(result['download_base']), self.root / 'downloads')
        self.assertEqual(result['network_requests'], 0)
        self.assertFalse((self.root / 'countries').exists())
        self.assertFalse((self.root / 'downloads').exists())

    def test_existing_workspace_countries_overrides_project_resources(self):
        project_fixture(self.root)
        local = self.root / 'countries/Canada'
        local.mkdir(parents=True)
        local_index = local / 'index.md'
        local_index.write_text('# Canada\n[Local official](https://official.example/acts)\n', encoding='utf-8')
        config = local.parent / 'global-config.properties'
        config.write_text('downloadDir=./我的归档/\n', encoding='utf-8')
        result = prepare(self.root, 'Canada')
        self.assertEqual(country_directory(self.root), local.parent)
        self.assertEqual(Path(result['index']), local_index)
        self.assertEqual(Path(result['config']), config)
        self.assertEqual(Path(result['download_base']), self.root / '我的归档')
        self.assertEqual(result['platforms'], [{'host': 'official.example', 'adapter_supported': False}])

    def test_empty_or_partial_local_countries_does_not_borrow_project_indexes(self):
        project_fixture(self.root)
        local = self.root / 'countries'
        local.mkdir()
        self.assertEqual(country_directory(self.root), local)
        self.assertEqual(configuration_path(self.root), local / 'global-config.properties')
        with self.assertRaisesRegex(ValueError, '未在工程索引中登记'):
            resolve_jurisdiction(self.root, 'Canada')
        custom = local / 'CustomLand'
        custom.mkdir()
        (custom / 'index.md').write_text('[Official](https://custom.example/laws)\n', encoding='utf-8')
        self.assertEqual(resolve_jurisdiction(self.root, 'CustomLand').index, custom / 'index.md')
        with self.assertRaisesRegex(ValueError, '未在工程索引中登记'):
            resolve_jurisdiction(self.root, 'Canada')

    def test_empty_unrelated_workspace_never_borrows_installed_skill(self):
        workspace = self.root / 'empty'
        self.assertEqual(country_directory(workspace), workspace / 'countries')
        with self.assertRaisesRegex(ValueError, '未在工程索引中登记'):
            resolve_jurisdiction(workspace, 'Canada')
        with self.assertRaisesRegex(ValueError, '配置缺失'):
            prepare(workspace, 'Canada')
        self.assertFalse(workspace.exists())

    def test_project_fallback_requires_skill_entrypoint_and_countries_together(self):
        countries = self.root / 'skill/law-crawler/countries/Canada'
        countries.mkdir(parents=True)
        (countries / 'index.md').write_text(INDEX, encoding='utf-8')
        self.assertEqual(country_directory(self.root), self.root / 'countries')
        (countries.parent.parent / 'SKILL.md').write_text('skill entry', encoding='utf-8')
        self.assertEqual(country_directory(self.root), countries.parent)

    def test_explicit_configuration_is_not_replaced_by_bundled_default(self):
        project_fixture(self.root)
        requested = Path('自定义设置/config.properties')
        expected = self.root / requested
        self.assertEqual(configuration_path(self.root, requested), expected)
        self.assertEqual(configuration_path(self.root, expected), expected)
        with self.assertRaises(FileNotFoundError):
            download_dir(configuration_path(self.root, requested), self.root)
        self.assertFalse(expected.exists())
        expected.parent.mkdir()
        expected.write_text('downdloadDir=./custom-output/\n', encoding='utf-8')
        self.assertEqual(download_dir(configuration_path(self.root, requested), self.root),
                         self.root / 'custom-output')

    def test_complete_skill_initializes_in_unicode_path_with_no_repository(self):
        copied = self.root / '独立技能 包目录/law-crawler'
        shutil.copytree(SKILL, copied, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        workspace = self.root / '中文 工作区'
        guard = self.root / 'offline'
        guard.mkdir()
        (guard / 'sitecustomize.py').write_text(
            'import socket\n'
            'def denied(*args, **kwargs): raise AssertionError("unexpected network request")\n'
            'socket.create_connection = denied\n'
            'socket.socket.connect = denied\n', encoding='utf-8')
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', str(copied / 'scripts/prepare_workspace.py'),
             '--root', str(workspace), '--jurisdiction', 'Canada', '--initialize'],
            cwd=self.root, env=dict(os.environ, PYTHONPATH=str(guard)),
            input='', text=True, capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(Path(report['index']), workspace / 'countries/Canada/index.md')
        self.assertEqual((workspace / 'countries/Canada/index.md').read_bytes(),
                         (copied / 'countries/Canada/index.md').read_bytes())
        self.assertEqual((workspace / 'countries/global-config.properties').read_bytes(),
                         (copied / 'countries/global-config.properties').read_bytes())
        self.assertEqual(Path(report['download_base']), workspace / 'downloads')
        self.assertEqual(report['network_requests'], 0)
        self.assertFalse((workspace / 'downloads').exists())
        self.assertFalse((copied / 'assets/global-config.properties').exists())

    def test_legacy_relative_and_absolute_index_plans_replay_without_changes(self):
        skill = project_fixture(self.root)
        options = dict(limit=3, per_platform_limit=None, max_details=20, timeout=1,
                       delay=0, refresh=False, max_attempts=3, backoff_base=0,
                       experimental_sources=False)
        self.assertEqual(set(options), set(OPTIONS))
        batches = self.root / 'downloads/.batches'
        for name, old_index in (
                ('legacy-relative', 'countries/Canada/index.md'),
                ('legacy-absolute', str(self.root / 'countries/Canada/index.md'))):
            with self.subTest(name=name):
                plan = dict(schema_version=1, jurisdiction='Canada', mode='metadata',
                            index=old_index, index_sha256='frozen-legacy-index-digest',
                            platforms=['laws-lois.justice.gc.ca'], countries=['Canada'],
                            options=options, created_at='2026-10-01T00:00:00Z',
                            download_base=str(self.root / 'downloads'),
                            selection_policy='latest_official_published_at_with_unknowns_flagged')
                with BatchRuntime(batches, plan_dict=plan, batch_id=name):
                    pass
                batch = batches / name
                preserved = {filename: (batch / filename).read_bytes()
                             for filename in ('plan.json', 'plan.md')}
                args = argparse.Namespace(
                    root=self.root, config=configuration_path(self.root), index=None,
                    jurisdiction=None, mode=None, batch_id=None, rerun_batch=name,
                    rerun_all=False, plan_only=True, platform='all',
                    refresh_metadata=False, **options)
                with patch('law_crawler.download_regulations.fetch',
                           side_effect=AssertionError('unexpected network request')) as fetch:
                    self.assertEqual(execute(args), 0)
                    fetch.assert_not_called()
                self.assertEqual(args.index, skill / 'countries/Canada/index.md')
                self.assertEqual(download_dir(args.config, args.root), self.root / 'downloads')
                for filename, original in preserved.items():
                    self.assertEqual((batch / filename).read_bytes(), original)
                report = json.loads((batch / 'attempts/0002/report.json').read_text(encoding='utf-8'))
                self.assertEqual(report['result'], 'planned_only')
                self.assertEqual(report['files'], [])
                self.assertEqual(report['errors'], [])
                for extension in ('json', 'md', 'html'):
                    self.assertTrue((batch / f'attempts/0002/report.{extension}').is_file())
                self.assertFalse((self.root / 'countries').exists())
                # plan-only 仍按既有契约生成国家级报告，不能生成原文或存储库。
                self.assertEqual(sorted(path.name for path in (self.root / 'downloads/Canada').iterdir()),
                                 ['last-run.html', 'last-run.json', 'last-run.md'])


if __name__ == '__main__':
    unittest.main()
