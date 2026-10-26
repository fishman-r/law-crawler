import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from law_crawler.bulk_archive import BulkArchive, eligible, main
from law_crawler.platform_adapters.base import Document


class FakeAdapter:
    allowed_hosts = ('official.test',)
    country = 'Indonesia'
    def discover_bulk(self, fetch, max_pages=1000, checkpoint=None, on_page=None):
        fetch('https://official.test/catalog')
        state = {'version': 1, 'details': ['https://official.test/detail/1', 'https://official.test/detail/2'],
            'complete': False, 'pending_urls': ['https://official.test/catalog?page=2'], 'pages': [{'url': 'https://official.test/catalog'}]}
        on_page(state)
        return state
    def read_document(self, url, fetch):
        fetch(url)
        return Document(url, ['https://official.test/a.pdf', 'https://official.test/fail.pdf'],
            {'title': {'value': 'PERMA Nomor 1 Tahun 2026'},
             'legal_identity': {'value': {'number': '1', 'year': '2026'}},
             'document_type': {'value': 'judicial_guidance'}})


class BulkArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.batch = self.root / 'Indonesia/pipeline/bulk/test'
        self.batch.mkdir(parents=True)
        self.plan = {'country_directory': str(self.root / 'Indonesia'), 'platforms': ['official.test', 'unknown.test'],
            'retry': {}, 'delay': 0, 'max_pages': 1, 'batch_id': 'test', 'jurisdiction': 'Indonesia',
            'translation_style': {'profile_id': 'test'}}
        self.calls = []

    def capture(self, url, raw, **kwargs):
        self.calls.append(url)
        data = b'%PDF-1.4\nsource' if url.endswith('.pdf') else b'<html>catalog</html>'
        status = 403 if url.endswith('fail.pdf') else 200
        digest = hashlib.sha256(data).hexdigest()
        path = raw / 'objects' / (digest + ('.pdf' if url.endswith('.pdf') else '.html'))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {'url': url, 'path': str(path), 'sha256': digest, 'size_bytes': len(data), 'status_code': status,
            'final_url': url, 'complete': True, 'capture_status': 'saved' if status == 200 else 'failed',
            'reason': 'saved' if status == 200 else 'HTTP 403', 'attempts': 1}

    def test_original_fetch_can_skip_body_read_but_details_keep_body(self):
        queue = BulkArchive(self.batch, self.plan, self.capture)
        queue.allowed_hosts = ('official.test',)
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('whole-object memory read')):
            data, final = queue.fetch('https://official.test/a.pdf', read_body=False)
        self.assertEqual(data, b'')
        self.assertEqual(final, 'https://official.test/a.pdf')
        detail, _ = queue.fetch('https://official.test/detail/1')
        self.assertEqual(detail, b'<html>catalog</html>')

    def test_discovery_checkpoint_and_incomplete_coverage(self):
        with patch('law_crawler.bulk_archive.ADAPTERS', {'official.test': FakeAdapter()}):
            queue = BulkArchive(self.batch, self.plan, self.capture)
            queue.discover()
        state = json.loads((self.batch / 'state.json').read_text())
        self.assertFalse(state['platforms']['official.test']['complete'])
        self.assertEqual(state['platforms']['unknown.test']['status'], 'unsupported')
        self.assertEqual(len(state['platforms']['official.test']['details']), 2)

    def test_capture_dedup_failure_and_export_source_provenance(self):
        with patch('law_crawler.bulk_archive.ADAPTERS', {'official.test': FakeAdapter()}):
            queue = BulkArchive(self.batch, self.plan, self.capture)
            queue.discover()
            queue.capture_documents()
            candidates = queue.export()
        self.assertEqual(len(candidates), 1)
        for field in ('published_at', 'updated_at', 'effective_region'):
            self.assertEqual(candidates[0]['document'][field]['status'], 'not_provided')
        self.assertEqual(len(candidates[0]['document']['sources']), 2)
        self.assertEqual(candidates[0]['document']['identity_verification'], 'not_independently_reviewed')
        report = json.loads((self.batch / 'report.json').read_text())
        self.assertEqual(report['counts']['files_complete'], 2)
        self.assertEqual(report['counts']['unique_original_sha256'], 1)
        self.assertEqual(len(json.loads((self.batch / 'export-exclusions.json').read_text())), 2)
        self.assertEqual(report['national_completeness'], 'unverified')
        self.assertEqual(len(list((self.batch / 'manifests').glob('*.json'))), 1)
        self.assertTrue((self.batch / 'report.html').is_file())

    def test_corrupt_cache_and_missing_identity_cannot_enter_model(self):
        receipt = self.capture('https://official.test/a.pdf', self.root / 'raw')
        doc = {'official_hosts': ['official.test'], 'metadata': {'title': {'value': 'Unknown'}}}
        self.assertFalse(eligible(doc, receipt)[0])
        doc['metadata']['legal_identity'] = {'value': {'number': '1', 'year': '2020'}}
        self.assertTrue(eligible(doc, receipt)[0])
        Path(receipt['path']).write_bytes(b'corrupted')
        self.assertFalse(eligible(doc, receipt)[0])

    def test_challenge_html_cannot_enter_model(self):
        receipt = self.capture('https://official.test/html', self.root / 'raw')
        data = b'<html>Cloudflare verify you are human 123</html>'
        Path(receipt['path']).write_bytes(data)
        receipt.update(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
        self.assertFalse(eligible({'official_hosts': ['official.test'], 'metadata': {'title': 'PERMA', 'legal_identity': {'number': '123', 'year': '2020'}}}, receipt)[0])

    def test_resume_freezes_plan(self):
        queue = BulkArchive(self.batch, self.plan, self.capture)
        queue.save()
        changed = {**self.plan, 'max_pages': 2}
        with self.assertRaisesRegex(ValueError, '冻结计划'):
            BulkArchive(self.batch, changed, self.capture)

    def test_unregistered_jurisdiction_rejected_before_network(self):
        (self.root / 'countries/Indonesia').mkdir(parents=True)
        (self.root / 'countries/Indonesia/index.md').write_text('[Official](https://official.test/)')
        with self.assertRaisesRegex(ValueError, '未在工程索引'):
            main(['--root', str(self.root), '--jurisdiction', 'Other', '--batch-id', 'test'])

    def test_fallback_never_claims_exhaustive(self):
        class Fallback:
            allowed_hosts = ('official.test',)
            def discover(self, fetch, limit):
                return ['https://official.test/one']
        plan = {**self.plan, 'platforms': ['official.test']}
        with patch('law_crawler.bulk_archive.ADAPTERS', {'official.test': Fallback()}):
            queue = BulkArchive(self.batch, plan, self.capture)
            queue.discover()
        self.assertFalse(queue.state['platforms']['official.test']['complete'])
        self.assertIn('分页', queue.state['platforms']['official.test']['reason'])


    def test_official_host_guard_rejects_before_network_and_redirect_before_export(self):
        queue = BulkArchive(self.batch, self.plan, self.capture)
        queue.allowed_hosts = ('official.test',)
        with self.assertRaisesRegex(ValueError, '拒绝网络'):
            queue.fetch('https://evil.test/a.pdf')
        self.assertEqual(self.calls, [])
        def redirected(url, raw, **kwargs):
            row = self.capture(url, raw, **kwargs)
            row['final_url'] = 'https://evil.test/a.pdf'
            return row
        queue.capture_fn = redirected
        with self.assertRaisesRegex(ValueError, '响应跳转'):
            queue.fetch('https://official.test/a.pdf')
        receipt = list(queue.state['responses'].values())[0]
        self.assertFalse(eligible({'official_hosts': ['official.test'], 'metadata': {
            'title': 'PERMA', 'legal_identity': {'number': '1', 'year': '2020'}}}, receipt)[0])

    def test_html_with_number_is_pending_not_model_candidate(self):
        receipt = self.capture('https://official.test/html', self.root / 'raw')
        data = b'<html><nav>PERMA number 123 2020</nav></html>'
        Path(receipt['path']).write_bytes(data)
        receipt.update(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
        accepted, reason = eligible({'official_hosts': ['official.test'], 'metadata': {
            'title': 'PERMA', 'legal_identity': {'number': '123', 'year': '2020'}}}, receipt)
        self.assertFalse(accepted)
        self.assertIn('正文门禁', reason)

    def test_aliases_do_not_scan_twice_and_unsupported_report_is_accurate(self):
        plan = {**self.plan, 'platforms': ['bpk.go.id', 'peraturan.bpk.go.id', 'unknown.test']}
        adapter = FakeAdapter()
        with patch('law_crawler.bulk_archive.ADAPTERS', {'bpk.go.id': adapter}):
            queue = BulkArchive(self.batch, plan, self.capture)
            queue.discover()
            report = queue.report()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(report['unsupported'], ['unknown.test'])
        self.assertEqual(report['coverage']['peraturan.bpk.go.id']['alias_of'], 'bpk.go.id')
        self.assertTrue((self.batch / 'report.html').is_file())

    def test_platform_exception_still_writes_interim_report(self):
        class Broken:
            allowed_hosts = ('official.test',)
            def discover(self, fetch, limit):
                raise ValueError('temporary failure')
        with patch('law_crawler.bulk_archive.ADAPTERS', {'official.test': Broken()}):
            queue = BulkArchive(self.batch, self.plan, self.capture)
            queue.discover()
        report = json.loads((self.batch / 'report.json').read_text())
        self.assertEqual(report['coverage']['official.test']['status'], 'failed')


    def test_reexport_keeps_old_snapshots_but_active_index_has_one_per_raw_sha(self):
        with patch('law_crawler.bulk_archive.ADAPTERS', {'official.test': FakeAdapter()}):
            queue = BulkArchive(self.batch, self.plan, self.capture)
            queue.discover()
            queue.capture_documents()
            queue.export()
            first = json.loads((self.batch / 'active-manifests.json').read_text())
            document = next(iter(queue.state['documents'].values()))
            document['metadata']['updated_at'] = {'value': '2026-10-21', 'status': 'observed'}
            queue.export()
        active = json.loads((self.batch / 'active-manifests.json').read_text())
        self.assertEqual(len(active['manifests']), 1)
        self.assertNotEqual(first['manifests'][0]['path'], active['manifests'][0]['path'])
        self.assertEqual(len(list((self.batch / 'manifests').glob('*.json'))), 2)
        manifest = json.loads(Path(active['manifests'][0]['path']).read_text())
        self.assertEqual(len(manifest['documents'][0]['document']['sources']), 2)
        self.assertEqual(hashlib.sha256(Path(active['manifests'][0]['path']).read_bytes()).hexdigest(), active['manifests'][0]['sha256'])

    def test_administrative_document_is_archived_but_not_model_candidate(self):
        receipt = self.capture('https://official.test/a.pdf', self.root / 'raw')
        document = {'official_hosts': ['official.test'], 'metadata': {
            'title': 'Keputusan', 'legal_identity': {'number': '1', 'year': '2026'},
            'document_type': 'administrative_decision', 'processing_gate': 'pending_document_type_review'}}
        self.assertFalse(eligible(document, receipt)[0])

if __name__ == '__main__':
    unittest.main()
