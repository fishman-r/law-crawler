"""仅下载服务配置；使用临时目录，不运行systemd、网络、下载或模型。"""
import contextlib
import io
import json
import os
from pathlib import Path
import pwd
import tempfile
import sys
import shutil
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import configure_downloader as tool

ROOT = Path(__file__).resolve().parents[2]


class DownloaderDeployTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name).resolve()
        self.release = self.folder / 'releases' / 'pinned-v1'
        (self.release / 'skill/law-crawler/countries/Indonesia').mkdir(parents=True)
        (self.release / 'skill/law-crawler/countries/Indonesia/index.md').write_text('[Trade](https://kemendag.go.id/)')
        (self.release / 'skill/law-crawler/scripts').mkdir()
        (self.release / 'skill/law-crawler/scripts/bulk_archive.py').write_text('# canonical test entry')
        (self.release / 'deploy/tools').mkdir(parents=True)
        (self.release / 'deploy/tools/configure_downloader.py').write_text('# pinned runner')
        self.workspace = self.folder / 'workspace'
        self.downloads = self.folder / 'downloads'
        self.units = self.folder / 'units'
        self.user = pwd.getpwuid(os.getuid()).pw_name

    def build(self, **kwargs):
        kwargs.setdefault('python', sys.executable)
        return tool.build(self.release, self.workspace, self.downloads, 'Indonesia', 'server-20261021',
                          ['kemendag.go.id'], user=self.user, **kwargs)

    def install(self):
        plan, name, unit, index, config = self.build()
        tool.install(plan, name, unit, index, config, self.units)
        return plan, name, unit

    def test_dry_run_is_readonly_and_worker_has_only_download_entry(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = tool.main(['--release', str(self.release), '--workspace', str(self.workspace),
                '--download-base', str(self.downloads), '--jurisdiction', 'Indonesia', '--batch-id', 'server-20261021', '--python', sys.executable, '--dry-run'])
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertFalse(self.workspace.exists()); self.assertFalse(self.downloads.exists())
        unit = payload['unit']
        for fragment in ('Restart=no', 'KillSignal=SIGINT', 'MemoryMax=512M', 'TasksMax=32', 'NoNewPrivileges=yes'):
            self.assertIn(fragment, unit)
        for fragment in ('ollama', 'anthropic', 'observer', '/current/', 'claude'):
            self.assertNotIn(fragment, unit.lower())
        self.assertIn(str(self.release), unit)

    def test_private_snapshot_and_idempotence_do_not_copy_source(self):
        plan, name, unit = self.install()
        self.install()
        self.assertEqual((self.workspace / 'countries/global-config.properties').read_text(), 'downdloadDir=' + str(self.downloads) + '\n')
        self.assertEqual((self.workspace / 'downloader-deployment-plan.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.workspace.stat().st_mode & 0o777, 0o700)
        self.assertFalse((self.workspace / 'scripts').exists())
        command = tool.worker_command(plan)
        self.assertIn(str(self.release / 'skill/law-crawler/scripts/bulk_archive.py'), command)
        self.assertIn('--batch-id', command); self.assertIn('all', command)
        self.assertNotIn('--model-config', command)

    def test_conflicting_config_plan_or_non_download_workspace_refused(self):
        self.workspace.mkdir(); (self.workspace / 'observer.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, '非下载workspace'):
            self.install()
        (self.workspace / 'observer.json').unlink(); self.install()
        config = self.workspace / 'countries/global-config.properties'
        config.write_text('downdloadDir=/different\n')
        with self.assertRaisesRegex(ValueError, '拒绝覆写'):
            self.install()
        self.assertEqual(config.read_text(), 'downdloadDir=/different\n')

    def test_worker_rechecks_sha_source_and_disk_without_exec(self):
        plan, _, _ = self.install()
        path = self.workspace / 'downloader-deployment-plan.json'
        signature = tool.digest(path.read_bytes())
        with patch.object(tool.shutil, 'disk_usage', return_value=SimpleNamespace(free=10*1024**3)):
            command = tool.run_worker(path, signature, execute=False)
        self.assertIn('--batch-id', command)
        with patch.object(tool.shutil, 'disk_usage', return_value=SimpleNamespace(free=1)):
            with self.assertRaisesRegex(ValueError, '可用磁盘'):
                tool.run_worker(path, signature, execute=False)
        (self.release / 'skill/law-crawler/scripts/bulk_archive.py').write_text('# changed')
        with self.assertRaisesRegex(ValueError, '源码发生变化'):
            tool.run_worker(path, signature, execute=False)

    def test_existing_batch_requires_matching_server_plan_then_uses_rerun(self):
        plan, _, _ = self.install()
        path = self.downloads / 'Indonesia/pipeline/bulk/server-20261021/plan.json'
        path.parent.mkdir(parents=True)
        value = {'jurisdiction': 'Indonesia', 'batch_id': 'server-20261021', 'country_directory': str(self.downloads / 'Indonesia'),
            'index_sha256': plan['index_sha256'], 'platforms': plan['platforms'], 'max_pages': 1000, 'delay': .25,
            'retry': {'timeout': 15, 'max_bytes': 40*1024**2, 'max_attempts': 3, 'backoff_base': 1}}
        path.write_bytes(tool.encoded(value)); path.with_name('plan.sha256').write_text(tool.digest(path.read_bytes()))
        self.assertIn('--rerun-batch', tool.worker_command(plan))
        value['country_directory'] = '/Users/local/mac/downloads/Indonesia'
        path.write_bytes(tool.encoded(value))
        with self.assertRaisesRegex(ValueError, '独立新batch-id'):
            tool.worker_command(plan)

    def test_alias_symlink_overlap_unknown_platform_and_invalid_delay_refused(self):
        alias = self.folder / 'current'; alias.symlink_to(self.release)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            tool.build(alias, self.workspace, self.downloads, 'Indonesia', 'server')
        with self.assertRaisesRegex(ValueError, '重叠'):
            tool.build(self.release, self.release / 'workspace', self.downloads, 'Indonesia', 'server', python=sys.executable)
        with self.assertRaisesRegex(ValueError, '索引中登记'):
            tool.build(self.release, self.workspace, self.downloads, 'Indonesia', 'server', ['unregistered.test'], python=sys.executable)
        with self.assertRaises(ValueError):
            self.build(delay=float('nan'))



    def test_system_python_symlink_resolves_but_private_old_data_is_not_chowned(self):
        link = self.folder / 'python3'
        import sys
        link.symlink_to(Path(sys.executable).resolve())
        plan, *_ = self.build(python=link)
        self.assertEqual(plan['python'], str(Path(sys.executable).resolve()))
        self.downloads.mkdir(); hidden = self.downloads / 'root-upload.pdf'; hidden.write_text('raw')
        with patch.object(tool, 'accessible', side_effect=lambda path, owner, bits: path != hidden), patch.object(tool.os, 'chown') as chown:
            with self.assertRaisesRegex(ValueError, '不自动递归修改'):
                self.install()
            chown.assert_not_called()


    def test_unsupported_python_runtime_is_rejected_before_install(self):
        completed = SimpleNamespace(stdout='[3, 8]')
        with patch.object(tool.subprocess, 'run', return_value=completed):
            with self.assertRaisesRegex(ValueError, 'Python>=3.10'):
                self.build()
        self.assertFalse(self.workspace.exists())


    def test_untraversable_existing_workspace_parent_is_rejected_before_mutations(self):
        with patch.object(tool, 'accessible', side_effect=lambda path, owner, bits: path != self.folder), patch.object(tool.os, 'chown') as chown:
            with self.assertRaisesRegex(ValueError, '父链'):
                self.install()
            chown.assert_not_called()
        self.assertFalse(self.workspace.exists())


    def test_working_directory_is_scalar_not_quoted_token(self):
        self.workspace = self.folder / 'workspace with spaces'
        self.downloads = self.folder / 'downloads with spaces'
        _, _, unit, *_ = self.build()
        line = next(s for s in unit.splitlines() if s.startswith('WorkingDirectory='))
        self.assertEqual(line, 'WorkingDirectory=' + str(self.workspace))
        self.assertIn('ReadWritePaths="' + str(self.workspace) + '" "' + str(self.downloads) + '"', unit)
        self.assertTrue(next(s for s in unit.splitlines() if s.startswith('ExecStart=')).startswith('ExecStart="'))

    @unittest.skipUnless(sys.platform == 'linux' and shutil.which('systemd-analyze'), '需要Linux systemd离线解析器')
    def test_generated_unit_passes_real_systemd_parser_with_spaced_paths(self):
        self.workspace = self.folder / 'workspace with spaces'
        self.downloads = self.folder / 'downloads with spaces'
        _, name, unit, *_ = self.build()
        self.workspace.mkdir(); self.downloads.mkdir()
        path = self.folder / name; path.write_text(unit)
        result = subprocess.run(['systemd-analyze', 'verify', '--man=no', str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

if __name__ == '__main__':
    unittest.main()
