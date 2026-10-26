"""批量处理门禁使用本地 fixture；不请求网络或真实模型。"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from law_crawler import bulk_process as bp


class BulkProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.country = self.root / 'Indonesia'
        self.pipeline = self.country / 'pipeline'
        self.raw = self.pipeline / 'raw/objects/one.txt'
        self.raw.parent.mkdir(parents=True)
        self.raw.write_text('official source')
        self.manifests = self.root / 'manifests'
        self.manifests.mkdir()
        self.manifest('one')
        self.output = self.root / 'processed'
        self.model = self.root / 'model.json'
        bp.write_json(self.model, {'provider': 'ollama', 'base_url': 'http://localhost:11434', 'model': 'cheap-local'})
        self.args = ['--root', str(self.root), '--jurisdiction', 'Indonesia', '--manifest-dir', str(self.manifests),
                     '--output', str(self.output), '--model-config', str(self.model), '--ocr-engine', 'none']
        self.addCleanup(patch.stopall)
        patch.object(bp, 'resolve_jurisdiction', return_value=SimpleNamespace(name='Indonesia', index=self.root/'index.md')).start()
        patch.object(bp, 'configuration_path', return_value=self.root/'config').start()
        patch.object(bp, 'country_download_dir', return_value=self.country).start()
        patch.object(bp.shutil, 'disk_usage', return_value=SimpleNamespace(free=10*1024**3)).start()

    def manifest(self, key):
        path = self.manifests / (key + '.json')
        bp.write_json(path, {'documents': [{'id': key, 'path': str(self.raw)}]})
        return path

    def run_pipeline(self, options):
        batch_id = options.batch_id or options.rerun_batch
        batch = self.pipeline / 'batches' / batch_id
        batch.mkdir(parents=True, exist_ok=True)
        bp.write_json(batch / 'plan.json', bp.staged_pipeline.make_plan(self.root, 'Indonesia', bp.load(options.manifest),
            bp.load(options.model_config), options.chunk_chars, options.max_chars, options.max_calls))
        artifact = self.pipeline / 'processed' / (batch_id + '.md')
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_text('原文 / 中文 / English')
        manifest = bp.load(options.manifest)
        request = {'batch_id': batch_id, 'failed_files': [], 'files': [{'id': manifest['documents'][0]['id'],
                    'artifacts': [{'path': str(artifact), 'sha256': bp.sha(artifact)}],
                    'quality_flags': [{'kind': 'visual_review_required'}]}]}
        bp.write_json(batch / 'review-request.json', request)
        return {'counts': {'failed': 0}, 'usage': {'model_calls': 2}}, {}

    def jobs(self):
        return sorted((self.output / 'jobs').glob('*.json'))

    def approve(self, verdict='pass', flag=True):
        row = bp.load(self.jobs()[0])
        request = bp.load(row['review_request'])
        file = dict(request['files'][0], verdict=verdict, issues=[])
        file['resolved_quality_flags'] = [{'flag': f, 'evidence': 'checked full original'} for f in file['quality_flags']] if flag else []
        bp.write_json(Path(row['review_request']).with_name('review.json'), {'batch_id': row['pipeline_batch'],
            'model': 'independent-good-model', 'reviewer': 'independent reviewer', 'imported_at': '2026-10-21',
            'cost_usd': None, 'duration_seconds': 12, 'files': [file]})

    def test_pending_replay_checks_sha_and_does_not_rebill(self):
        with patch.object(bp.staged_pipeline, 'run', side_effect=self.run_pipeline) as run:
            self.assertEqual(bp.main(self.args), 0)
            self.assertEqual(bp.main(self.args), 0)
            self.assertEqual(run.call_count, 1)
        report = bp.load(self.output/'report.json')
        self.assertEqual(report['cumulative_model_calls'], 2)
        self.assertFalse(report['review_complete'])
        self.assertEqual(report['counts']['pending_review'], 1)
        row = bp.load(self.jobs()[0])
        artifact = Path(bp.load(row['review_request'])['files'][0]['artifacts'][0]['path'])
        artifact.write_text('tampered')
        with patch.object(bp.staged_pipeline, 'run') as run:
            self.assertEqual(bp.main(self.args), 0)
            run.assert_not_called()
        self.assertEqual(bp.load(self.output/'report.json')['counts']['failed'], 1)

    def test_independent_review_and_flags_control_actual_status(self):
        with patch.object(bp.staged_pipeline, 'run', side_effect=self.run_pipeline):
            bp.main(self.args)
        self.approve(flag=False)
        report = bp.summarize(self.output, self.pipeline, time.monotonic())
        self.assertEqual(report['counts']['failed'], 1)
        self.assertFalse(report['review_complete'])
        self.approve()
        report = bp.summarize(self.output, self.pipeline, time.monotonic())
        self.assertEqual(report['counts']['approved'], 1)
        self.assertTrue(report['review_complete'])
        self.assertFalse(report['complete'])
        self.assertEqual(report['independent_review_seconds'], 12)
        self.assertEqual(bp.load(self.output/'review-queue.json')['jobs'], [])
        self.approve(verdict='needs_revision')
        self.assertEqual(bp.summarize(self.output,self.pipeline,time.monotonic())['counts']['needs_revision'], 1)

    def test_model_and_style_snapshot_prevent_read_drift(self):
        original = bp.prepare_manifest
        def prepare(*args):
            bp.write_json(self.model, {'provider':'openai-compatible','base_url':'https://remote.invalid','model':'changed'})
            return original(*args)
        observed = []
        def run(options):
            observed.append(bp.load(options.model_config))
            value = bp.load(options.manifest)
            self.assertEqual(Path(value['translation_style_path']), self.output/'translation-style.json')
            return self.run_pipeline(options)
        with patch.object(bp, 'prepare_manifest', side_effect=prepare), patch.object(bp.staged_pipeline,'run',side_effect=run):
            self.assertEqual(bp.main(self.args), 0)
        self.assertEqual(observed[0]['model'], 'cheap-local')
        with patch.object(bp.staged_pipeline,'run') as run:
            self.assertEqual(bp.main(self.args), 2)
            run.assert_not_called()

    def test_cloud_rejected_before_ocr_and_empty_inventory_not_completed(self):
        bp.write_json(self.model, {'provider':'ollama','base_url':'https://remote.invalid','model':'x'})
        with patch.object(bp,'prepare_manifest') as prepare:
            self.assertEqual(bp.main(self.args), 2)
            prepare.assert_not_called()
        bp.write_json(self.model, {'provider':'ollama','base_url':'http://localhost:11434','model':'x'})
        self.manifests.joinpath('one.json').unlink()
        self.assertEqual(bp.main(self.args), 2)

    def test_failures_are_per_file_and_retries_are_bounded(self):
        self.manifest('two')
        calls = []
        def run(options):
            key = bp.load(options.manifest)['documents'][0]['id']
            calls.append(key)
            if key == 'one':
                raise RuntimeError('model unavailable')
            return self.run_pipeline(options)
        with patch.object(bp.staged_pipeline,'run',side_effect=run):
            self.assertEqual(bp.main(self.args), 0)
            self.assertEqual(calls, ['one', 'two'])
            self.assertEqual(bp.main(self.args), 0)
            for _ in range(5):
                self.assertEqual(bp.main(self.args+['--retry-failed']), 0)
        self.assertEqual(calls.count('one'), 3)
        self.assertEqual(calls.count('two'), 1)
        rows = [bp.load(p) for p in self.jobs()]
        self.assertEqual(next(r for r in rows if r['id']=='one')['attempts'], 3)
        report = bp.load(self.output/'report.json')
        self.assertEqual(report['input_count'], 2)
        self.assertEqual(report['counts']['failed'], 1)
        self.assertFalse(report['processing_complete'])

    def test_pdf_ocr_all_pages_and_table_sidecar_are_forwarded(self):
        self.raw.write_bytes(b'%PDF-1.7 fixture')
        def ocr(raw, output, engine, languages):
            output.mkdir(parents=True)
            bp.write_json(output/'transcription.json', {'pages': [{'page':1},{'page':2}], 'page_count':2})
            return {'duration_seconds': 4, 'reused': False}
        def tables(sidecar, output):
            self.assertEqual(bp.load(sidecar)['page_count'], 2)
            output.mkdir(parents=True)
            bp.write_json(output/'transcription.json', {'table_structure_status':'needs_review'})
            return {'duration_seconds': 2, 'reused': False}
        with patch('law_crawler.local_ocr.generate',side_effect=ocr) as o, patch('law_crawler.automatic_tables.generate_compatible',side_effect=tables):
            value, rawsha, metrics = bp.prepare_manifest(self.manifests/'one.json',self.pipeline,'apple-vision')
        self.assertEqual(o.call_args.args[3], ['id-ID'])
        self.assertIn('bulk-tables-',value['documents'][0]['transcription_path'])
        self.assertEqual(metrics['ocr_seconds'], 4)
        self.assertEqual(metrics['automatic_tables_seconds'], 2)

    def test_failed_pdf_ocr_preserved_and_different_retry_directory(self):
        base = self.pipeline/'ocr/example'
        base.mkdir(parents=True)
        bp.write_json(base/'receipt.json', {'status':'failed'})
        self.assertEqual(bp.preparation_directory(base),base.with_name('example-attempt-2'))
        self.assertTrue(base.is_dir())

    def test_status_only_detects_tampered_raw_without_model_calls(self):
        with patch.object(bp.staged_pipeline,'run',side_effect=self.run_pipeline):
            bp.main(self.args)
        self.raw.write_text('changed raw')
        with patch.object(bp.staged_pipeline,'run') as run:
            self.assertEqual(bp.main(self.args+['--status-only']), 0)
            run.assert_not_called()
        self.assertEqual(bp.load(self.output/'report.json')['counts']['failed'], 1)

    def test_failed_pipeline_uses_existing_batch_and_cumulative_ledger(self):
        seen = []
        def run(options):
            seen.append((options.batch_id, options.rerun_batch))
            batch_id = options.batch_id or options.rerun_batch
            batch = self.pipeline/'batches'/batch_id
            batch.mkdir(parents=True,exist_ok=True)
            bp.write_json(batch/'plan.json', bp.staged_pipeline.make_plan(self.root, 'Indonesia', bp.load(options.manifest),
                bp.load(options.model_config), options.chunk_chars, options.max_chars, options.max_calls))
            bp.write_json(batch/'model-calls.json', {'calls':len(seen)})
            if len(seen) == 1:
                return {'counts':{'failed':1},'usage':{'model_calls':1}}, {}
            return self.run_pipeline(options)
        with patch.object(bp.staged_pipeline,'run',side_effect=run):
            bp.main(self.args)
            bp.main(self.args+['--retry-failed'])
            bp.main(self.args)
        self.assertEqual(len(seen), 2)
        self.assertIsNone(seen[0][1])
        self.assertEqual(seen[1], (None, seen[0][0]))
        self.assertEqual(bp.load(self.output/'report.json')['cumulative_model_calls'], 2)

    def test_failed_table_detection_blocks_partial_translation(self):
        self.raw.write_bytes(b'%PDF-1.7 fixture')
        def ocr(raw, output, engine, languages):
            output.mkdir(parents=True)
            bp.write_json(output/'transcription.json', {'pages':[], 'page_count':2})
            return {'duration_seconds':4, 'reused':False}
        with patch('law_crawler.local_ocr.generate',side_effect=ocr), patch('law_crawler.automatic_tables.generate_compatible',side_effect=RuntimeError('dependency missing')):
            with self.assertRaisesRegex(RuntimeError, '自动表格结构处理失败'):
                bp.prepare_manifest(self.manifests/'one.json',self.pipeline,'apple-vision')

    def test_inventory_frozen_no_silent_new_input(self):
        with patch.object(bp.staged_pipeline,'run',side_effect=self.run_pipeline):
            bp.main(self.args)
        self.manifest('extra')
        with patch.object(bp.staged_pipeline,'run') as run:
            self.assertEqual(bp.main(self.args), 2)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
