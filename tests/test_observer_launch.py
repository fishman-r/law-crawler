from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import observer.launch as launch
from observer.launch import run


class ObserverLaunchTests(unittest.TestCase):
    def test_process_result_persisted_without_arguments_or_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code = 'import os,sys;sys.exit(7 if os.environ.get("LAW_CRAWLER_RUN_ID") else 8)'
            result = run(Path(sys.executable),root,root/'runtime',['-c',code,'sk-private-test-value'])
            self.assertEqual(result,7)
            files = list((root/'runtime/runs').glob('*.json'))
            self.assertEqual(len(files),1)
            raw = files[0].read_text()
            value = json.loads(raw)
            self.assertEqual(value['exit_code'],7)
            self.assertIsNotNone(value['finished_at'])
            self.assertNotIn('sk-private',raw)
            self.assertNotIn(code,raw)
            if os.name == 'posix':
                self.assertEqual(files[0].stat().st_mode & 0o777,0o600)

    def assert_record_failure_reaps_child(self, fail_on_save, ignore_terminate=False):
        """启动真实长运行进程；模拟存储故障后必须已结束并被 wait 回收。"""
        real_popen = subprocess.Popen
        real_save = launch.save
        processes = []
        save_calls = 0
        original_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

        def start(*arguments, **options):
            process = real_popen(*arguments, **options, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
            processes.append(process)
            if ignore_terminate:
                # 等到子进程确实安装了 SIGTERM 忽略 handler，避免竞争导致假通过。
                self.assertEqual(process.stdout.readline(), 'ready\n')
            return process

        def save_or_fail(path, value):
            nonlocal save_calls
            save_calls += 1
            if save_calls == fail_on_save:
                raise OSError('simulated storage failure with sk-private-error-value')
            return real_save(path, value)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code = 'import time;time.sleep(60)'
            if ignore_terminate:
                code = ('import signal,sys,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);'
                        'print("ready",flush=True);time.sleep(60)')
            try:
                with patch('observer.launch.subprocess.Popen', side_effect=start), \
                     patch('observer.launch.save', side_effect=save_or_fail), \
                     patch('observer.launch.TERMINATE_TIMEOUT', 0.1):
                    with self.assertRaises(OSError):
                        run(Path(sys.executable), root, root/'runtime', ['-c', code, 'sk-private-argument'])
                self.assertEqual(len(processes), 1)
                process = processes[0]
                self.assertIsNotNone(process.returncode, '子进程必须已经 wait 回收')
                self.assertIsNotNone(process.poll(), '存储失败后不能保留运行中的子进程')
                if ignore_terminate and os.name == 'posix':
                    self.assertEqual(process.returncode, -signal.SIGKILL)
                if fail_on_save == 2:
                    self.assertEqual(len(list((root/'runtime/runs').glob('*.json'))), 1)
                for sig, handler in original_handlers.items():
                    self.assertIs(signal.getsignal(sig), handler)
            finally:
                # 测试失败也不在开发机器留下进程或管道。
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)
                    process.stdout.close()
                    process.stderr.close()

    def test_initial_record_failure_reaps_child_and_restores_handlers(self):
        self.assert_record_failure_reaps_child(1)

    def test_heartbeat_failure_reaps_child_and_restores_handlers(self):
        self.assert_record_failure_reaps_child(2)

    @unittest.skipUnless(os.name == 'posix', 'SIGTERM/SIGKILL escalation is a POSIX deployment behavior')
    def test_terminate_timeout_kills_and_reaps_unresponsive_child(self):
        self.assert_record_failure_reaps_child(2, ignore_terminate=True)

    def test_cli_error_does_not_print_parameters_or_exception_contents(self):
        output, errors = io.StringIO(), io.StringIO()
        arguments = ['launch', '--binary', sys.executable, '--workspace', '/workspace',
                     '--runtime-dir', '/runtime', '--', 'sk-private-argument']
        with patch.object(sys, 'argv', arguments), \
             patch('observer.launch.run', side_effect=OSError('sk-private-exception')), \
             redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(launch.main(), 1)
        self.assertEqual(output.getvalue(), '')
        self.assertIn('OSError', errors.getvalue())
        self.assertNotIn('sk-private', errors.getvalue())


if __name__ == '__main__':
    unittest.main()
