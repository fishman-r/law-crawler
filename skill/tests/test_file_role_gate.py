"""真实15/2026详情HTML用于角色回归；小PDF字节为合成数据，不冒充官网原件。"""
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from law_crawler import bulk_process as bp
from law_crawler.platform_adapters.trade import classify_file_roles
import test_bulk_process as fixtures

DETAIL = 'https://jdih.kemendag.go.id/peraturan/peraturan-menteri-perdagangan-nomor-15-tahun-2026-tentang-kebijakan-dan-pengaturan-ekspor-komoditas-sumber-daya-alam-strategis-batubara'
PRIMARY = 'https://jdih.kemendag.go.id/uploads/peraturan/2026/1785664446_cfb7e57b3e0d7a91.pdf'
RELATED = 'https://jdih.kemendag.go.id/pembentukan-puu/download/afc48c09-a222-4c5c-b54d-f328b7816c3a'
HTML = Path(__file__).with_name('fixtures') / 'kemendag-15-2026-file-roles.html'


class FileRoleGateTests(unittest.TestCase):
    setUp = fixtures.BulkProcessTests.setUp
    manifest = fixtures.BulkProcessTests.manifest
    run_pipeline = fixtures.BulkProcessTests.run_pipeline
    jobs = fixtures.BulkProcessTests.jobs

    def archive(self, url, content, extension):
        from law_crawler.raw_capture import _url
        raw = self.pipeline / 'raw'
        digest = hashlib.sha256(content).hexdigest()
        path = raw / 'objects' / (digest + extension)
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
        index = raw / 'index'; index.mkdir(exist_ok=True)
        bp.write_json(index / (hashlib.sha256(_url(url).encode()).hexdigest() + '.json'), {
            'url': url, 'final_url': url, 'sha256': digest, 'path': str(path), 'size_bytes': len(content),
            'status_code': 200, 'complete': True})
        return path

    def candidate(self, urls, same_bytes=False):
        self.archive(DETAIL, HTML.read_bytes(), '.html')
        contents = b'%PDF-synthetic RANCANGAN hidden-token-not-an-identity-test'
        paths = [self.archive(url, contents if same_bytes else contents+str(i).encode(), '.pdf') for i, url in enumerate(urls)]
        document = {'sources': [{'platform': 'kemendag.go.id', 'detail_url': DETAIL, 'file_url': url} for url in urls]}
        manifest = self.manifests / 'one.json'
        bp.write_json(manifest, {'documents': [{'id': 'one', 'path': str(paths[-1]), 'document': document}]})
        return manifest, paths

    def test_real_html_roles_do_not_use_hidden_pdf_keyword(self):
        self.assertEqual(hashlib.sha256(HTML.read_bytes()).hexdigest(), '17e936de1a5957b24a99d16d53b3d4bccb6be5efa4a2f159978049d2e83f35cd')
        roles = classify_file_roles(HTML.read_text(), DETAIL, [PRIMARY, RELATED])
        self.assertEqual(roles[PRIMARY]['role'], 'regulation_body')
        self.assertEqual(roles[RELATED]['role'], 'related_formation_document_pending')
        self.assertNotIn('draft_identity_confirmed', str(roles))

    def test_old_producer_related_manifest_is_pending_before_ocr(self):
        manifest, _ = self.candidate([RELATED])
        with patch('law_crawler.local_ocr.generate') as ocr, patch.object(bp.staged_pipeline, 'run') as run:
            self.assertEqual(bp.main(self.args), 0)
            ocr.assert_not_called(); run.assert_not_called()
        row = bp.load(self.jobs()[0])
        self.assertEqual(row['status'], 'pending_identity')
        self.assertEqual(row['attempts'], 0)
        self.assertFalse(bp.load(self.output / 'report.json')['processing_complete'])
        self.assertTrue(Path(bp.load(manifest)['documents'][0]['path']).exists())

    def test_primary_source_compatible_and_same_sha_primary_overrides_pending_source(self):
        for sources in ([PRIMARY], [PRIMARY, RELATED]):
            with self.subTest(sources=sources):
                manifest, _ = self.candidate(sources, same_bytes=True)
                value, _, metrics = bp.prepare_manifest(manifest, self.pipeline, 'none')
                self.assertTrue(metrics['file_role_gate']['eligible'])
                self.assertEqual(value['documents'][0]['document']['sources'][-1]['file_url'], sources[-1])
                if len(sources) == 2:
                    observed = metrics['file_role_gate']['sources']
                    self.assertTrue(observed[0]['eligible']); self.assertFalse(observed[1]['eligible'])

    def test_different_sha_or_unknown_related_source_cannot_borrow_primary_identity(self):
        manifest, _ = self.candidate([PRIMARY, RELATED], same_bytes=False)
        with self.assertRaises(bp.FileIdentityPending):
            bp.prepare_manifest(manifest, self.pipeline, 'none')
        unknown = 'https://jdih.kemendag.go.id/uploads/peraturan/history.pdf'
        manifest, _ = self.candidate([unknown])
        with self.assertRaises(bp.FileIdentityPending):
            bp.prepare_manifest(manifest, self.pipeline, 'none')

    def test_corrupt_local_detail_evidence_blocks_and_nontrade_legacy_remains_compatible(self):
        manifest, _ = self.candidate([PRIMARY])
        detail_receipt, path = bp.archived_response(DETAIL, self.pipeline)
        path.write_bytes(b'changed')
        with self.assertRaises(bp.FileIdentityPending):
            bp.prepare_manifest(manifest, self.pipeline, 'none')
        decision = bp.file_role_decision({'sources': [{'platform': 'mahkamahagung.go.id',
            'file_url': 'https://jdih.mahkamahagung.go.id/legal-product/perma-1/download/dokumen'}]}, self.pipeline, 'a'*64)
        self.assertTrue(decision['eligible'])

    def test_frozen_preparation_retry_preserves_sidecar_and_does_not_repeat_ocr(self):
        self.candidate([PRIMARY])
        with patch.object(bp.staged_pipeline, 'run', side_effect=self.run_pipeline):
            self.assertEqual(bp.main(self.args), 0)
        row = bp.load(self.jobs()[0]); row['status'] = 'failed'
        bp.write_json(self.jobs()[0], row)
        with patch.object(bp, 'prepare_manifest', side_effect=AssertionError('must reuse frozen preparation')) as prepare, patch.object(bp.staged_pipeline, 'run', side_effect=self.run_pipeline):
            self.assertEqual(bp.main(self.args + ['--retry-failed']), 0)
            prepare.assert_not_called()
        latest = bp.load(self.jobs()[0])
        self.assertTrue(latest['preparation']['reused_frozen_preparation'])
        self.assertEqual(latest['preparation']['ocr_seconds'], 0)
        self.assertEqual(latest['prepared_manifest_sha256'], row['prepared_manifest_sha256'])

    def test_previously_processed_related_candidate_cannot_bypass_reuse_gate(self):
        self.candidate([RELATED])
        with patch.object(bp, 'file_role_decision', return_value={'eligible': True}), patch.object(bp.staged_pipeline, 'run', side_effect=self.run_pipeline):
            self.assertEqual(bp.main(self.args), 0)
        with patch.object(bp.staged_pipeline, 'run') as run:
            self.assertEqual(bp.main(self.args), 0)
            run.assert_not_called()
        report = bp.load(self.output / 'report.json')
        self.assertEqual(report['counts']['pending_identity'], 1)
        self.assertFalse(report['review_complete'])


    def test_interrupted_running_row_recovers_stage_bound_prepared_without_ocr(self):
        self.candidate([PRIMARY])
        with patch.object(bp.staged_pipeline, 'run', side_effect=self.run_pipeline):
            self.assertEqual(bp.main(self.args), 0)
        row = bp.load(self.jobs()[0]); row['status'] = 'running'
        for key in ('prepared_manifest', 'prepared_manifest_sha256', 'raw_path', 'raw_sha256', 'preparation'):
            row.pop(key, None)
        bp.write_json(self.jobs()[0], row)
        def resume(options):
            persisted = bp.load(self.jobs()[0])
            self.assertTrue(persisted['prepared_manifest_sha256'])
            self.assertTrue(persisted['raw_sha256'])
            return self.run_pipeline(options)
        with patch.object(bp, 'prepare_manifest', side_effect=AssertionError('must not redo OCR')) as prepare, patch.object(bp.staged_pipeline, 'run', side_effect=resume):
            self.assertEqual(bp.main(self.args + ['--retry-failed']), 0)
            prepare.assert_not_called()
        latest = bp.load(self.jobs()[0])
        self.assertTrue(latest['preparation']['historical_preparation_metrics_unavailable'])
        self.assertTrue(latest['preparation']['reused_frozen_preparation'])
