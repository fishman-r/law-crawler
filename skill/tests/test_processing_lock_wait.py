"""处理锁有界等待持有、句柄清理、调度失败停止队列。"""
import errno
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from law_crawler import file_lock, staged_pipeline as staged
import test_bulk_process as fixtures


class ProcessingLockWaitTests(unittest.TestCase):
    def test_busy_retry_closes_failed_handles_and_holds_acquired_handle(self):
        streams = []
        clock = SimpleNamespace(now=0)
        def flock(stream, operation):
            if operation == 8:
                return
            streams.append(stream)
            if len(streams) <= 2:
                raise OSError(errno.EAGAIN, 'busy')
        backend = SimpleNamespace(LOCK_EX=1, LOCK_NB=2, LOCK_UN=8, flock=flock)
        def sleep(seconds):
            clock.now += seconds
        with tempfile.TemporaryDirectory() as folder, patch.object(file_lock, '_backend', return_value=('fcntl', backend)), patch.object(staged.time, 'monotonic', side_effect=lambda: clock.now), patch.object(staged.time, 'sleep', side_effect=sleep):
            with staged.processing_lock(Path(folder) / 'lock', 1) as elapsed:
                self.assertEqual(elapsed, .5)
                self.assertTrue(all(s.closed for s in streams[:2]))
                self.assertFalse(streams[2].closed)
            self.assertTrue(all(s.closed for s in streams))

    def test_timeout_is_retryable_and_never_calls_work(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            with file_lock.FileLock(root / '.processing.lock'), patch.object(staged, '_process_document') as work:
                stats = {'model_seconds': 0}
                with self.assertRaises(staged.ProcessingLockTimeout):
                    staged.process_document({}, {}, root, {}, None, stats, lock_wait_seconds=0)
                work.assert_not_called()
                self.assertEqual(stats['model_seconds'], 0)
                self.assertGreaterEqual(stats['processing_lock_wait_seconds'], 0)

    def test_permission_failure_is_not_busy_and_is_not_retried(self):
        backend = SimpleNamespace(LOCK_EX=1, LOCK_NB=2,
            flock=lambda *args: (_ for _ in ()).throw(OSError(errno.EACCES, 'denied')))
        with tempfile.TemporaryDirectory() as folder, patch.object(file_lock, '_backend', return_value=('fcntl', backend)), patch.object(staged.time, 'sleep') as sleep:
            with self.assertRaisesRegex(ValueError, '目录权限') as error:
                with staged.processing_lock(Path(folder) / 'lock', 1800):
                    self.fail('must not enter')
            self.assertNotIsInstance(error.exception, file_lock.FileLockBusy)
            sleep.assert_not_called()

    def test_visual_structure_issues_require_valid_page_and_remain_explicit(self):
        value = {'version': 1, 'pdf_sha256': 'a'*64, 'page_count': 1,
            'pages': [{'page': 1, 'text': 'BAGAN', 'method': 'ocr', 'producer': 'test', 'evidence': 'image'}],
            'visual_structure_issues': [{'page': 1, 'kind': 'organization_diagram_unresolved', 'reason': 'links lost'}]}
        self.assertEqual(staged.validate_transcription(value)['visual_structure_issues'][0]['kind'], 'organization_diagram_unresolved')
        value['visual_structure_issues'][0]['page'] = 2
        with self.assertRaisesRegex(ValueError, '视觉结构'):
            staged.validate_transcription(value)


class BulkWaitSchedulingTests(unittest.TestCase):
    setUp = fixtures.BulkProcessTests.setUp
    manifest = fixtures.BulkProcessTests.manifest
    run_pipeline = fixtures.BulkProcessTests.run_pipeline
    jobs = fixtures.BulkProcessTests.jobs

    def test_wait_timeout_stops_after_one_job_and_retry_does_not_consume_processing_attempt(self):
        from law_crawler import bulk_process as bp
        self.manifest('two')
        calls = []
        def deferred(options):
            calls.append(options)
            return {'counts': {'failed': 1}, 'retryable_scheduling': True,
                    'usage': {'model_calls': 0, 'model_seconds': 0, 'processing_lock_wait_seconds': .25}}, {}
        with patch.object(bp.staged_pipeline, 'run', side_effect=deferred):
            self.assertEqual(bp.main(self.args), 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].processing_lock_wait_seconds, 1800)
        row = bp.load(self.jobs()[0])
        self.assertEqual(row['status'], 'deferred')
        self.assertEqual(row['attempts'], 0)
        report = bp.load(self.output / 'report.json')
        self.assertEqual(report['counts']['unstarted'], 1)
        self.assertEqual(report['processing_lock_wait_seconds'], .25)
        self.assertEqual(report['model_seconds'], 0)
        with patch.object(bp.staged_pipeline, 'run', side_effect=self.run_pipeline) as run:
            self.assertEqual(bp.main(self.args), 0)
            self.assertEqual(run.call_count, 2)

    def test_old_plan_remains_readable_without_new_semantic_config(self):
        from law_crawler import bulk_process as bp
        with patch.object(bp.staged_pipeline, 'run', side_effect=self.run_pipeline):
            self.assertEqual(bp.main(self.args), 0)
        plan = bp.load(self.output / 'plan.json')
        plan.pop('processing_lock_wait_seconds')
        bp.write_json(self.output / 'plan.json', plan)
        with patch.object(bp.staged_pipeline, 'run') as run:
            self.assertEqual(bp.main(self.args + ['--status-only']), 0)
            run.assert_not_called()
            self.assertEqual(bp.main(self.args + ['--processing-lock-wait-seconds', '1800']), 2)

import json
import test_staged_pipeline as stage_fixtures


class StagedWaitCacheTests(unittest.TestCase):
    setUp = stage_fixtures.StagedPipelineTests.setUp
    model = stage_fixtures.StagedPipelineTests.model
    setup_transcription = stage_fixtures.StagedPipelineTests.setup_transcription
    pdf_reader = stage_fixtures.StagedPipelineTests.pdf_reader
    review_file = stage_fixtures.StagedPipelineTests.review_file
    rerun = stage_fixtures.StagedPipelineTests.rerun

    def test_new_batch_wait_policy_reuses_identical_semantic_cache(self):
        with patch.object(staged, 'call_json', side_effect=self.model) as model:
            staged.run(self.args)
            self.args.batch_id = 'new-wait-policy'
            self.args.processing_lock_wait_seconds = 1800
            result, _ = staged.run(self.args)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(result['usage']['reused_chunks'], 1)
        self.assertEqual(staged.load(self.base / 'batches/new-wait-policy/execution-policy.json')['processing_lock_wait_seconds'], 1800)

    def test_timeout_stops_staged_documents_without_marking_whole_list_failed(self):
        manifest = staged.load(self.manifest)
        manifest['documents'].append({**manifest['documents'][0], 'id': 'doc2'})
        self.manifest.write_text(json.dumps(manifest))
        with patch.object(staged, 'process_document', side_effect=staged.ProcessingLockTimeout('busy')) as process:
            result, _ = staged.run(self.args)
        self.assertEqual(process.call_count, 1)
        self.assertEqual(result['counts']['failed'], 1)
        self.assertTrue(result['retryable_scheduling'])

    def test_visual_issue_survives_extraction_and_requires_explicit_resolution(self):
        self.setup_transcription()
        issue = {'page': 1, 'kind': 'organization_diagram_unresolved', 'reason': 'links not transcribed',
                 'image_path': '/original/page-1.png', 'raw_ocr_path': '/original/raw-1.json'}
        self.transcription['visual_structure_issues'] = [issue]
        self.sidecar.write_text(json.dumps(self.transcription))
        with patch.dict('sys.modules', {'pypdf': self.pdf_reader()}), patch.object(staged, 'call_json', side_effect=self.model):
            result, _ = staged.run(self.args)
        row = result['files'][0]
        self.assertEqual(row['extraction']['visual_structure_issues'], [issue])
        flag = next(f for f in row['quality_flags'] if f['kind'] == issue['kind'])
        self.assertTrue(flag['visual_structure_unresolved'])
        review = self.review_file()
        for item in review['files']:
            item['resolved_quality_flags'] = [{'flag': f, 'evidence': 'checked original'} for f in row['quality_flags'] if f != flag]
        Path(self.args.review).write_text(json.dumps(review))
        with self.assertRaisesRegex(ValueError, 'quality_flag'):
            staged.run(self.args)
