"""独立技能包、空工作区及用户级/项目级安装；仅临时目录，禁用网络。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tools.install_skill import install, skills_root


SKILL = Path(__file__).resolve().parents[1] / 'skills/law-crawler'


class WorkspacePreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='skill-independent-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.skill = self.root / '包目录 with spaces' / SKILL.name
        shutil.copytree(SKILL, self.skill, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        self.workspace = self.root / '新工作区 with spaces'
        self.guard = self.root / 'offline'
        self.guard.mkdir()
        (self.guard / 'sitecustomize.py').write_text(
            'import socket\n'
            'def denied(*a, **kw): raise AssertionError("unexpected network request")\n'
            'socket.create_connection=denied\n'
            'socket.socket.connect=denied\n', encoding='utf-8')

    def run_script(self, name, *args):
        env = dict(os.environ, PYTHONPATH=str(self.guard))
        return subprocess.run([sys.executable, '-X', 'utf8', str(self.skill / 'scripts' / name), *map(str, args)],
                              cwd=self.root, env=env, input='', capture_output=True, text=True, encoding='utf-8')

    def prepare(self, jurisdiction='Canada', initialize=True):
        args = ['--root', self.workspace, '--jurisdiction', jurisdiction]
        if initialize:
            args.append('--initialize')
        return self.run_script('prepare_workspace.py', *args)

    def test_standalone_package_initializes_only_selected_index_and_config(self):
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['jurisdiction'], 'Canada')
        self.assertEqual(report['network_requests'], 0)
        self.assertEqual(len(report['initialized']), 2)
        self.assertEqual(list((self.workspace / 'countries').glob('*/index.md')),
                         [self.workspace / 'countries/Canada/index.md'])
        self.assertTrue((self.workspace / 'countries/global-config.properties').is_file())
        self.assertFalse((self.workspace / 'downloads').exists())
        self.assertEqual(Path(report['download_base']), self.workspace / 'downloads')
        again = self.prepare(initialize=False)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(json.loads(again.stdout)['initialized'], [])

    def test_existing_config_and_index_are_not_overwritten(self):
        self.assertEqual(self.prepare().returncode, 0)
        config = self.workspace / 'countries/global-config.properties'
        config.write_text('downloadDir=./我的归档/\n', encoding='utf-8')
        index = self.workspace / 'countries/Canada/index.md'
        original = index.read_bytes() + '\n<!-- user-maintained -->\n'.encode()
        index.write_bytes(original)
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(config.read_text(encoding='utf-8'), 'downloadDir=./我的归档/\n')
        self.assertEqual(index.read_bytes(), original)
        self.assertEqual(json.loads(result.stdout)['download_base'], str(self.workspace / '我的归档'))
        self.assertFalse((self.workspace / '我的归档').exists())

    def test_existing_indexes_do_not_gain_an_unregistered_country_from_templates(self):
        self.assertEqual(self.prepare().returncode, 0)
        before = {str(p.relative_to(self.workspace)): p.read_bytes()
                  for p in self.workspace.rglob('*') if p.is_file()}
        result = self.prepare('France')
        self.assertEqual(result.returncode, 1)
        self.assertIn('未在工程索引中登记', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(before, {str(p.relative_to(self.workspace)): p.read_bytes()
                                  for p in self.workspace.rglob('*') if p.is_file()})

    def test_unknown_jurisdiction_and_missing_config_create_nothing(self):
        result = self.prepare('UnregisteredExample')
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.workspace.exists())
        result = self.prepare(initialize=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn('配置缺失', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(self.workspace.exists())

    def test_eu_member_uses_actual_index_and_reports_adapter_scope(self):
        result = self.prepare('France')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['jurisdiction'], 'France')
        self.assertEqual(Path(report['index']), self.workspace / 'countries/EU/index.md')
        self.assertEqual(report['country_directories'], [str(self.workspace / 'downloads/France')])
        # Légifrance有受限入口适配器，不能将已适配误写成已下载；未适配平台也须报告false。
        self.assertTrue(any(p['host'] == 'legifrance.gouv.fr' for p in report['platforms']))
        self.assertTrue(all(isinstance(p['adapter_supported'], bool) for p in report['platforms']))
        self.assertFalse((self.workspace / 'downloads').exists())

    def test_invalid_existing_config_is_readable_and_does_not_seed_an_index(self):
        countries = self.workspace / 'countries'
        countries.mkdir(parents=True)
        config = countries / 'global-config.properties'
        config.write_text('# user configuration without a target\n', encoding='utf-8')
        result = self.prepare()
        self.assertEqual(result.returncode, 1)
        self.assertIn('配置缺少', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(list(countries.glob('*/index.md')), [])
        self.assertEqual(config.read_text(encoding='utf-8'), '# user configuration without a target\n')

    def test_registered_custom_workspace_is_not_replaced_by_bundled_defaults(self):
        country = self.workspace / 'countries/CustomLand'
        country.mkdir(parents=True)
        index = country / 'index.md'
        index.write_text('# CustomLand\n[Official](https://official.example/legislation)\n', encoding='utf-8')
        (country.parent / 'global-config.properties').write_text('downdloadDir=./out/\n', encoding='utf-8')
        result = self.prepare('CustomLand')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['platforms'],
                         [{'host': 'official.example', 'adapter_supported': False}])
        self.assertFalse((self.workspace / 'out').exists())

    def test_installed_package_plans_and_replays_without_repository_or_network(self):
        self.assertEqual(self.prepare().returncode, 0)
        args = ['--root', self.workspace, '--jurisdiction', 'Canada', '--mode', 'metadata',
                '--plan-only', '--batch-id', 'independent-canada']
        result = self.run_script('download_regulations.py', *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        batch = self.workspace / 'downloads/.batches/independent-canada'
        plan = json.loads((batch / 'plan.json').read_text(encoding='utf-8'))
        self.assertEqual(plan['mode'], 'metadata')
        self.assertEqual(plan['jurisdiction'], 'Canada')
        replay = self.run_script('download_regulations.py', '--root', self.workspace,
                                 '--rerun-batch', 'independent-canada', '--plan-only')
        self.assertEqual(replay.returncode, 0, replay.stderr)
        for n in ('0001', '0002'):
            for suffix in ('json', 'md', 'html'):
                self.assertTrue((batch / 'attempts' / n / ('report.' + suffix)).is_file())
        self.assertFalse(list((self.workspace / 'downloads').rglob('*.xml')))


class InstallerCompatibilityTests(unittest.TestCase):
    def test_workbuddy_project_scope_and_installed_package_are_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp).resolve() / '项目 with spaces'
            self.assertEqual(skills_root('workbuddy', 'project', project), project / '.workbuddy/skills')
            target = install('workbuddy', 'project', project, source=SKILL)
            self.assertTrue((target / 'scripts/prepare_workspace.py').is_file())
            self.assertTrue((target / 'scripts/law_crawler/file_lock.py').is_file())
            self.assertTrue((target / 'countries/global-config.properties').is_file())

    def test_missing_source_cannot_claim_success_or_create_a_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, '缺少SKILL.md'):
                install('codex', destination=root / 'destination', source=root / 'missing')
            self.assertFalse((root / 'destination').exists())


if __name__ == '__main__':
    unittest.main()
