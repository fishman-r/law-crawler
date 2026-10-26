"""本地真实HTTP并发：不同URL重叠、相同URL复用、单writer状态合并与失败保留。"""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from law_crawler import bulk_archive as bulk
from law_crawler import bulk_download_fast as fast
from law_crawler.platform_adapters.base import Document
from law_crawler.raw_capture import capture


class FastDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.hits, self.active, self.peak, self.guard = {}, 0, 0, threading.Lock()
        self.barrier = None
        self.stop_on_path = None
        self.stop_signal = None
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                with owner.guard:
                    owner.hits[self.path] = owner.hits.get(self.path, 0) + 1
                    owner.active += 1; owner.peak = max(owner.peak, owner.active)
                try:
                    if self.path == '/redirect/out':
                        self.send_response(302); self.send_header('Location', 'http://localhost:' + str(owner.server.server_port) + '/files/redirected.pdf'); self.end_headers()
                        return
                    if self.path.startswith('/barrier/'):
                        owner.barrier.wait(timeout=3)
                    else:
                        time.sleep(.04)
                    data = b'%PDF-fixture identical bytes' if '/detail/' not in self.path else b'<html>official detail</html>'
                    code = 403 if 'forbidden' in self.path else 200
                    self.send_response(code); self.send_header('Content-Length', str(len(data) + (10 if 'incomplete' in self.path else 0))); self.end_headers()
                    self.wfile.write(data)
                    if self.path == owner.stop_on_path:
                        owner.stop_signal.set()
                finally:
                    with owner.guard: owner.active -= 1
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.country = self.root / 'Indonesia'; self.directory = self.country / 'pipeline/bulk/test'
        self.directory.mkdir(parents=True)
        self.plan = {'version': bulk.VERSION, 'batch_id': 'test', 'country_directory': str(self.country),
            'jurisdiction': 'Indonesia', 'platforms': ['fixture.test'], 'delay': 0, 'max_pages': 1,
            'retry': {'timeout': 3, 'max_bytes': 1024, 'max_attempts': 3, 'backoff_base': 0}, 'translation_style': {}}
        bulk.write_json(self.directory / 'plan.json', self.plan)
        (self.directory / 'plan.sha256').write_text(bulk.sha((self.directory / 'plan.json').read_bytes()))

    def adapter(self):
        base = self.base
        class Adapter:
            allowed_hosts = ('127.0.0.1',)
            def read_document(self, url, fetch):
                fetch(url)
                file = '/files/forbidden.pdf' if url.endswith('/fail') else '/files/shared.pdf'
                return Document(url, [base+file], {'title': {'value': 'Law'},
                    'legal_identity': {'value': {'number': '1', 'year': '2026'}}})
        return Adapter()

    def queue(self, keys=('one', 'two', 'fail')):
        parent = bulk.BulkArchive(self.directory, self.plan)
        parent.state['platforms'] = {'fixture.test': {'details': [self.base+'/detail/'+key for key in keys]}}
        parent.save()
        return parent

    def test_different_urls_overlap_and_identical_objects_remain_intact(self):
        self.barrier = threading.Barrier(2)
        raw = self.country / 'pipeline/raw'
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(lambda name: capture(self.base+'/barrier/'+name, raw,
                lock_scope='url', timeout=4, max_attempts=1), ['a', 'b']))
        self.assertGreaterEqual(self.peak, 2)
        self.assertEqual(rows[0]['sha256'], rows[1]['sha256'])
        self.assertTrue(all(bulk.valid_receipt(row) for row in rows))
        self.assertEqual(len(list((raw/'objects').glob('*'))), 1)
        self.assertEqual(len(list((raw/'index').glob('*.json'))), 2)

    def test_same_url_concurrent_requests_are_fetched_once(self):
        raw = self.country / 'pipeline/raw'; url = self.base+'/files/shared.pdf'
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(lambda _: capture(url, raw, lock_scope='url', timeout=3), range(2)))
        self.assertEqual(self.hits['/files/shared.pdf'], 1)
        self.assertEqual(sorted(row['capture_status'] for row in rows), ['reused', 'saved'])
        self.assertTrue(all(bulk.valid_receipt(row) for row in rows))

    def test_unexpected_worker_failure_never_marks_queue_finished(self):
        self.queue(('one',))
        with patch.dict(bulk.ADAPTERS, {'fixture.test': self.adapter()}), patch.object(fast, 'download_shard', side_effect=RuntimeError('fixture worker failure')):
            with self.assertRaises(RuntimeError):
                fast.execute(self.directory, self.plan, workers=5)
        row = bulk.load(self.directory/'fast-download/status.json')
        self.assertEqual(row['phase'], 'error')
        self.assertFalse(row['download_complete'])
        self.assertEqual(row['summary']['operations']['active'], 0)

    def test_progress_summary_counts_receipts_and_preserves_unknown_coverage(self):
        good = {'complete': True, 'capture_status': 'saved', 'status_code': 200, 'sha256': 'a'*64}
        state = {'platforms': {'a': {'details': ['http://fixture.test/one', 'http://fixture.test/two']}}, 'documents': {
            'one': {'files': {'a': good, 'b': dict(good)}},
            'two': {'files': {'bad': dict(good, capture_status='failed', reason='HTTP 403', status_code=403),
                              'big': dict(good, capture_status='failed', reason='响应超过 max_bytes')}}}}
        row = fast.progress_summary(state, 5, '2026-10-22T03:09:35+00:00')
        self.assertEqual(row['total_details'], 2)
        self.assertEqual(row['attempted_details'], 2)
        self.assertEqual(row['successful_files'], 1)
        self.assertEqual(row['failed_file_references'], 2)
        self.assertEqual(row['run_workers'], 5)
        self.assertEqual(row['national_completeness'], 'unverified')
        self.assertEqual(sum(r['count'] for r in row['failure_reasons']), 2)

    def test_parent_merge_has_all_sources_failure_is_not_success_and_plan_unchanged(self):
        self.queue(); before = (self.directory/'plan.json').read_bytes()
        with patch.dict(bulk.ADAPTERS, {'fixture.test': self.adapter()}):
            counts = fast.execute(self.directory, self.plan, workers=5, max_bytes=4096)
        state = bulk.load(self.directory/'state.json')
        self.assertEqual(len(state['documents']), 3)
        self.assertEqual(counts['downloaded_complete'], 2)
        self.assertEqual(counts['attempted_incomplete'], 1)
        self.assertEqual(self.hits['/files/shared.pdf'], 1)
        self.assertEqual(self.hits['/files/forbidden.pdf'], 1)
        self.assertTrue(any(row['capture_status']=='failed' for row in state['responses'].values()))
        self.assertEqual(before, (self.directory/'plan.json').read_bytes())
        self.assertEqual(len(list((self.directory/'fast-download/shards').glob('*/merged.json'))), 3)
        self.assertFalse((self.directory/'manifest.json').exists())
        self.assertFalse((self.country/'pipeline/processed').exists())

    def test_completed_skips_hash_verified_and_failures_only_retry_when_explicit(self):
        self.queue(('one', 'fail'))
        with patch.dict(bulk.ADAPTERS, {'fixture.test': self.adapter()}):
            fast.execute(self.directory, self.plan)
            previous = dict(self.hits)
            skipped = fast.execute(self.directory, self.plan)
            self.assertEqual(skipped['skipped_complete'], 1)
            self.assertEqual(skipped['skipped_failed'], 1)
            self.assertEqual(previous, self.hits)
            fast.execute(self.directory, self.plan, retry_failed=True)
        self.assertEqual(self.hits['/files/forbidden.pdf'], 2)
        self.assertEqual(self.hits['/files/shared.pdf'], 1)

    def test_successful_file_reused_even_when_raw_url_index_is_missing(self):
        self.queue(('one', 'fail'))
        adapter = self.adapter()
        original = adapter.read_document
        def read_document(url, fetch):
            doc = original(url, fetch)
            if url.endswith('/fail'):
                doc.pdf_urls.insert(0, self.base+'/files/shared.pdf')
            return doc
        adapter.read_document = read_document
        with patch.dict(bulk.ADAPTERS, {'fixture.test': adapter}):
            fast.execute(self.directory, self.plan)
            state = bulk.load(self.directory/'state.json')
            archived = next(d for d in state['documents'].values() if d['status']=='archived')
            receipt = next(iter(archived['files'].values()))
            index=self.country/'pipeline/raw/index'/(bulk.sha(receipt['url'].encode())+'.json')
            self.assertTrue(index.exists()); index.unlink()
            before=dict(self.hits)
            fast.execute(self.directory, self.plan, workers=5, retry_failed=True, max_bytes=4096, refresh_failed_details=True)
        self.assertEqual(self.hits['/files/shared.pdf'],before['/files/shared.pdf'])
        self.assertGreater(self.hits['/detail/fail'],before['/detail/fail'])
        policy_files=list((self.directory/'fast-download/runs').glob('*.json'))
        self.assertTrue(any(bulk.load(p).get('max_bytes_override')==4096 for p in policy_files))

    def test_corrupt_previously_completed_body_is_recovered_not_skipped(self):
        self.queue(('one',))
        with patch.dict(bulk.ADAPTERS, {'fixture.test': self.adapter()}):
            fast.execute(self.directory, self.plan)
            state = bulk.load(self.directory/'state.json')
            doc = next(iter(state['documents'].values())); receipt = next(iter(doc['files'].values()))
            Path(receipt['path']).write_bytes(b'corrupted')
            result = fast.execute(self.directory, self.plan)
        self.assertEqual(result['skipped_complete'], 0)
        self.assertEqual(result['downloaded_complete'], 1)
        self.assertEqual(self.hits['/files/shared.pdf'], 2)

    def test_unmerged_finished_shard_recovers_without_network(self):
        parent = self.queue(('one',))
        stop = threading.Event(); pace = fast.HostPace(0, stop)
        with patch.dict(bulk.ADAPTERS, {'fixture.test': self.adapter()}):
            task = next(fast.detail_tasks(parent.state))
            shard = self.directory/'fast-download/shards'/task[2]
            fast.download_shard(shard, self.plan, task, None, pace, stop)
            previous = dict(self.hits)
            result = fast.execute(self.directory, self.plan)
        self.assertEqual(result['recovered_shards'], 1)
        self.assertEqual(result['skipped_complete'], 1)
        self.assertEqual(previous, self.hits)

    def test_nonofficial_attachment_and_redirect_are_not_counted_complete(self):
        for redirected in (False, True):
            with self.subTest(redirected=redirected):
                base = self.base
                class Adapter:
                    allowed_hosts = ('127.0.0.1',)
                    def read_document(self, url, fetch):
                        fetch(url)
                        file = base+'/redirect/out' if redirected else 'http://localhost:'+str(self_port)+'/outside.pdf'
                        return Document(url, [file], {})
                self_port = self.server.server_port
                self.queue(('one',))
                with patch.dict(bulk.ADAPTERS, {'fixture.test': Adapter()}):
                    result = fast.execute(self.directory, self.plan, retry_failed=True)
                self.assertEqual(result['downloaded_complete'], 0)
                self.assertEqual(result['attempted_incomplete'], 1)
        self.assertNotIn('/outside.pdf', self.hits)
        state = bulk.load(self.directory/'state.json')
        self.assertTrue(any(r.get('final_url','').startswith('http://localhost:') for r in state['responses'].values()))
        self.assertTrue(all(d['status'] != 'archived' for d in state['documents'].values()))

    def test_stopped_partial_shard_resumes_without_repeat_of_completed_file(self):
        parent = self.queue(('one',)); base = self.base
        class Adapter:
            allowed_hosts = ('127.0.0.1',)
            def read_document(self, url, fetch):
                fetch(url)
                return Document(url, [base+'/files/shared.pdf', base+'/files/next.pdf'], {})
        stop = threading.Event(); self.stop_signal = stop; self.stop_on_path = '/files/shared.pdf'
        with patch.dict(bulk.ADAPTERS, {'fixture.test': Adapter()}):
            task = next(fast.detail_tasks(parent.state))
            directory = self.directory/'fast-download/shards'/task[2]
            fast.download_shard(directory, self.plan, task, None, fast.HostPace(0, stop), stop)
            self.assertEqual(bulk.load(directory/'state.json')['documents'][task[2]]['status'], 'interrupted')
            self.stop_on_path = None
            result = fast.execute(self.directory, self.plan)
        self.assertEqual(result['downloaded_complete'], 1)
        self.assertEqual(self.hits['/files/shared.pdf'], 1)
        self.assertEqual(self.hits['/files/next.pdf'], 1)
        self.assertTrue((self.directory/'report.md').exists())
        self.assertTrue((self.directory/'report.html').exists())

    def test_incomplete_content_length_never_counts_success_and_retries_are_bounded(self):
        self.queue(('one',)); base = self.base
        class Adapter:
            allowed_hosts = ('127.0.0.1',)
            def read_document(self, url, fetch):
                fetch(url)
                return Document(url, [base+'/files/incomplete.pdf'], {})
        with patch.dict(bulk.ADAPTERS, {'fixture.test': Adapter()}):
            result = fast.execute(self.directory, self.plan)
        self.assertEqual(result['downloaded_complete'], 0)
        self.assertEqual(result['attempted_incomplete'], 1)
        self.assertEqual(self.hits['/files/incomplete.pdf'], 3)
        doc = next(iter(bulk.load(self.directory/'state.json')['documents'].values()))
        self.assertEqual(doc['status'], 'incomplete')
        self.assertTrue(all(not receipt.get('complete') for receipt in doc['files'].values()))

    def test_keyboard_interrupt_stops_bounded_queue_and_leaves_recoverable_shards(self):
        self.queue(('one', 'two', 'fail'))
        with patch.dict(bulk.ADAPTERS, {'fixture.test': self.adapter()}), patch.object(fast, 'wait', side_effect=KeyboardInterrupt):
            result = fast.execute(self.directory, self.plan)
        self.assertEqual(result['interrupted'], 1)
        status = bulk.load(self.directory/'fast-download/status.json')
        self.assertTrue(status['stop_requested'])
        self.assertEqual(status['phase'], 'stopped')
        self.assertLessEqual(len(list((self.directory/'fast-download/shards').iterdir())), 2)
        self.assertTrue((self.directory/'report.html').exists())
