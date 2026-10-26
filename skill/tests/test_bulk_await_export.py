"""通过模拟生产者导出事件和时钟验证等待衔接，不运行真实OCR或模型。"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from law_crawler import bulk_process as bp
import test_bulk_process as fixtures


class AwaitExportTests(unittest.TestCase):
    setUp = fixtures.BulkProcessTests.setUp
    manifest = fixtures.BulkProcessTests.manifest
    run_pipeline = fixtures.BulkProcessTests.run_pipeline
    jobs = fixtures.BulkProcessTests.jobs

    def producer(self):
        self.producer_plan = self.manifests.parent / 'plan.json'
        bp.write_json(self.producer_plan, {'batch_id': self.manifests.parent.name, 'jurisdiction': 'Indonesia'})
        self.producer_plan.with_name('plan.sha256').write_text(bp.sha(self.producer_plan))
        self.clock = SimpleNamespace(now=0)

    def export(self, empty=False):
        rows = [] if empty else [{'path': str(self.manifests / 'one.json'),
             'sha256': bp.sha(self.manifests / 'one.json'), 'raw_sha256': bp.sha(self.raw)}]
        bp.write_json(self.manifests.parent / 'active-manifests.json', {'version': 1, 'manifests': rows})

    def test_export_event_continues_automatically_after_freezing_wait_plan(self):
        self.producer()
        sleeps = []
        def sleep(seconds):
            self.assertLessEqual(seconds, 10)
            self.assertTrue((self.output / 'await-export-plan.json').is_file())
            self.assertFalse((self.output / 'plan.json').exists())
            self.assertEqual(bp.load(self.output / 'await-export-state.json')['status'], 'pending_export')
            self.clock.now += seconds
            sleeps.append(seconds)
            self.export()
        with patch.object(bp.time, 'monotonic', side_effect=lambda: self.clock.now), patch.object(bp.time, 'sleep', side_effect=sleep), patch.object(bp.staged_pipeline, 'run', side_effect=self.run_pipeline) as run:
            self.assertEqual(bp.main(self.args + ['--await-export-seconds', '30']), 0)
        run.assert_called_once()
        self.assertEqual(sleeps, [5])
        self.assertEqual(bp.load(self.output / 'await-export-state.json')['status'], 'ready')
        self.assertEqual(len(bp.load(self.output / 'plan.json')['inputs']), 1)
        frozen = bp.load(self.output / 'await-export-plan.json')
        self.assertEqual(frozen['producer_plan']['sha256'], bp.sha(self.producer_plan))
        self.assertEqual(frozen['timeout_seconds'], 30)
        with patch.object(bp.staged_pipeline, 'run') as rerun:
            self.assertEqual(bp.main(self.args + ['--status-only']), 0)
            rerun.assert_not_called()

    def test_timeout_never_freezes_empty_processing_plan_or_calls_ocr_model(self):
        self.producer()
        def sleep(seconds):
            self.clock.now += seconds
        with patch.object(bp.time, 'monotonic', side_effect=lambda: self.clock.now), patch.object(bp.time, 'sleep', side_effect=sleep), patch.object(bp, 'prepare_manifest') as prepare, patch.object(bp.staged_pipeline, 'run') as run:
            self.assertEqual(bp.main(self.args + ['--await-export-seconds', '7']), 2)
            prepare.assert_not_called(); run.assert_not_called()
        state = bp.load(self.output / 'await-export-state.json')
        self.assertTrue(state['timed_out'])
        self.assertEqual(state['elapsed_seconds'], 7)
        self.assertFalse((self.output / 'plan.json').exists())

    def test_ready_index_skips_sleep_and_empty_export_is_failure(self):
        self.producer(); self.export()
        with patch.object(bp.time, 'sleep') as sleep, patch.object(bp.staged_pipeline, 'run', side_effect=self.run_pipeline):
            self.assertEqual(bp.main(self.args + ['--await-export-seconds', '20']), 0)
            sleep.assert_not_called()
        self.output = self.root / 'empty-output'
        args = self.args.copy(); args[args.index('--output') + 1] = str(self.output)
        self.export(empty=True)
        with patch.object(bp.staged_pipeline, 'run') as run:
            self.assertEqual(bp.main(args + ['--await-export-seconds', '20']), 2)
            run.assert_not_called()
        self.assertIn('空活跃清单', bp.load(self.output / 'await-export-state.json')['reason'])

    def test_model_and_producer_drift_during_wait_fail_without_model(self):
        for drift in ('model', 'producer', 'style'):
            with self.subTest(drift=drift):
                self.output = self.root / ('out-' + drift)
                args = self.args.copy(); args[args.index('--output') + 1] = str(self.output)
                bp.write_json(self.model, {'provider':'ollama','base_url':'http://localhost:11434','model':'cheap-local'})
                self.producer()
                def sleep(seconds):
                    self.clock.now += seconds
                    target = self.model if drift == 'model' else (self.producer_plan if drift == 'producer' else self.output / 'translation-style.json')
                    target.write_text(target.read_text() + '\n')
                with patch.object(bp.time, 'monotonic', side_effect=lambda: self.clock.now), patch.object(bp.time, 'sleep', side_effect=sleep), patch.object(bp.staged_pipeline, 'run') as run:
                    self.assertEqual(bp.main(args + ['--await-export-seconds', '20']), 2)
                    run.assert_not_called()
                self.assertIn('冻结输入发生变化', bp.load(self.output / 'await-export-state.json')['reason'])

    def test_rerun_checks_producer_signature_and_cloud_is_rejected_before_wait(self):
        self.producer()
        def sleep(seconds):
            self.clock.now += seconds
        with patch.object(bp.time, 'monotonic', side_effect=lambda: self.clock.now), patch.object(bp.time, 'sleep', side_effect=sleep):
            self.assertEqual(bp.main(self.args + ['--await-export-seconds', '1']), 2)
        self.producer_plan.write_text(self.producer_plan.read_text() + '\n')
        with patch.object(bp.time, 'sleep') as sleep:
            self.assertEqual(bp.main(self.args + ['--await-export-seconds', '1']), 2)
            sleep.assert_not_called()
        bp.write_json(self.model, {'provider':'ollama','base_url':'https://remote.invalid','model':'x'})
        with patch.object(bp, 'await_export') as wait:
            self.assertEqual(bp.main(self.args + ['--await-export-seconds', '1']), 2)
            wait.assert_not_called()

    def test_output_directory_lock_is_held_while_waiting(self):
        from law_crawler.file_lock import FileLock, FileLockBusy
        self.producer()
        def sleep(seconds):
            with self.assertRaises(FileLockBusy):
                FileLock(self.output / '.process.lock')
            self.clock.now += seconds
            self.export()
        with patch.object(bp.time, 'monotonic', side_effect=lambda: self.clock.now), patch.object(bp.time, 'sleep', side_effect=sleep), patch.object(bp.staged_pipeline, 'run', side_effect=self.run_pipeline):
            self.assertEqual(bp.main(self.args + ['--await-export-seconds', '20']), 0)
