import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

from law_crawler.raw_capture import capture


class Handler(BaseHTTPRequestHandler):
    counts = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.counts[self.path] = self.counts.get(self.path, 0) + 1
        body = b'\x00\xffraw unknown binary'
        status = 200
        if self.path == '/403':
            status, body = 403, b'<html>Forbidden</html>'
        if self.path == '/500' and self.counts[self.path] < 3:
            status, body = 500, b'temporary failure'
        self.send_response(status)
        self.send_header('Content-Type', 'application/pdf')  # Deliberately misleading.
        self.send_header('Set-Cookie', 'secret=never-persist')
        self.send_header('Content-Length', str(len(body) + (20 if self.path == '/truncated' else 0)))
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True


class RawCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'capture'
        Handler.counts.clear()

    def get(self, path, **kwargs):
        return capture(self.base + path, self.root, backoff_base=0, **kwargs)

    def test_binary_archive_and_cache_integrity(self):
        result = self.get('/binary')
        self.assertEqual(result['capture_status'], 'saved')
        self.assertTrue(result['complete'])
        self.assertEqual(Path(result['path']).suffix, '.bin')
        self.assertNotIn('set-cookie', result['headers'])
        self.assertEqual(hashlib.sha256(Path(result['path']).read_bytes()).hexdigest(), result['sha256'])
        again = self.get('/binary')
        self.assertEqual(again['capture_status'], 'reused')
        self.assertEqual(again['attempts'], 0)
        self.assertEqual(Handler.counts['/binary'], 1)
        self.assertNotEqual(result['receipts'], again['receipts'])
        Path(result['path']).write_bytes(b'corrupt')
        restored = self.get('/binary')
        self.assertEqual(restored['capture_status'], 'saved')
        self.assertEqual(Handler.counts['/binary'], 2)

    def test_identical_bytes_distinct_urls_deduplicate(self):
        first, second = self.get('/one'), self.get('/two')
        self.assertEqual(first['path'], second['path'])
        self.assertNotEqual(first['receipts'], second['receipts'])
        self.assertEqual(len(list((self.root / 'objects').iterdir())), 1)
        self.assertEqual(len(list((self.root / 'index').iterdir())), 2)

    def test_403_body_preserved_without_retry(self):
        result = self.get('/403')
        self.assertEqual(result['capture_status'], 'failed')
        self.assertEqual(result['status_code'], 403)
        self.assertTrue(result['complete'])
        self.assertEqual(result['attempts'], 1)
        self.assertEqual(Path(result['path']).read_bytes(), b'<html>Forbidden</html>')
        self.assertFalse(list((self.root / 'index').iterdir()))

    def test_500_retries_all_receipts_kept(self):
        result = self.get('/500')
        self.assertEqual(result['capture_status'], 'saved')
        self.assertEqual(result['attempts'], 3)
        records = [json.loads(Path(p).read_text()) for p in result['receipts']]
        self.assertEqual([r['status_code'] for r in records], [500, 500, 200])
        self.assertTrue(all(Path(r['path']).is_file() for r in records))

    def test_truncated_and_oversize_never_cached(self):
        truncated = self.get('/truncated')
        self.assertFalse(truncated['complete'])
        self.assertEqual(truncated['capture_status'], 'failed')
        self.assertEqual(truncated['attempts'], 3)
        self.assertTrue(Path(truncated['path']).read_bytes())
        bounded = self.get('/large', max_bytes=5)
        self.assertFalse(bounded['complete'])
        self.assertEqual(bounded['size_bytes'], 5)
        self.assertEqual(bounded['attempts'], 1)
        self.get('/large', max_bytes=5)
        self.assertEqual(Handler.counts['/large'], 2)
        self.assertFalse(list((self.root / 'index').iterdir()))

    def test_increased_limit_recovers_failed_partial_without_deleting_history(self):
        first = self.get('/recover-large', max_bytes=5)
        self.assertEqual(first['capture_status'], 'failed')
        self.assertFalse(first['complete'])
        self.assertEqual(first['size_bytes'], 5)
        recovered = self.get('/recover-large', max_bytes=512 * 1024 * 1024)
        self.assertEqual(recovered['capture_status'], 'saved')
        self.assertTrue(recovered['complete'])
        self.assertTrue(Path(first['path']).is_file())
        self.assertTrue(Path(first['receipts'][0]).is_file())
        reused = self.get('/recover-large', max_bytes=512 * 1024 * 1024)
        self.assertEqual(reused['capture_status'], 'reused')
        self.assertEqual(Handler.counts['/recover-large'], 2)

    def test_streamed_large_file_and_cache_never_read_whole_object(self):
        from unittest.mock import patch
        from law_crawler.bulk_archive import valid_receipt
        total = 12 * 1024 * 1024
        class Response:
            code = 200
            headers = {'Content-Length': str(total)}
            def __init__(self): self.position = 0
            def geturl(self): return 'https://official.test/large.pdf'
            def read(self, amount):
                if not 0 < amount <= 65536:
                    raise AssertionError('unbounded response read')
                size = min(amount, total - self.position)
                data = b'x' * size
                if self.position == 0 and size:
                    data = b'%PDF-' + data[5:]
                self.position += size
                return data
            def close(self): pass
        with patch('urllib.request.OpenerDirector.open', side_effect=lambda *a, **kw: Response()) as opened:
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('whole-object memory read')):
                first = capture('https://official.test/large.pdf', self.root, max_bytes=512 * 1024 * 1024)
                self.assertTrue(valid_receipt(first))
                second = capture('https://official.test/large.pdf', self.root, max_bytes=512 * 1024 * 1024)
            self.assertEqual(opened.call_count, 1)
        self.assertEqual(first['size_bytes'], total)
        self.assertEqual(Path(first['path']).suffix, '.pdf')
        self.assertEqual(second['capture_status'], 'reused')
        self.assertEqual(list((self.root / 'objects').glob('.capture-*')), [])

    def test_cancel_during_stream_preserves_partial_and_failed_receipt(self):
        from unittest.mock import patch
        stop = threading.Event()
        class Response:
            code = 200
            headers = {'Content-Length': '1000000'}
            def geturl(self): return 'https://official.test/stop.pdf'
            def read(self, amount):
                stop.set()
                return b'%PDF-' + b'x' * 100
            def close(self): pass
        with patch('urllib.request.OpenerDirector.open', return_value=Response()) as opened:
            with self.assertRaises(InterruptedError):
                capture('https://official.test/stop.pdf', self.root, lock_scope='url', stop_event=stop)
        self.assertEqual(opened.call_count, 1)
        records = [json.loads(p.read_text()) for p in (self.root / 'receipts').glob('*.json')]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['capture_status'], 'failed')
        self.assertFalse(records[0]['complete'])
        self.assertEqual(Path(records[0]['path']).stat().st_size, 105)
        self.assertEqual(list((self.root / 'index').glob('*.json')), [])
        self.assertEqual(list((self.root / 'objects').glob('.capture-*')), [])

    def test_refresh_preserves_receipts(self):
        first = self.get('/refresh')
        self.get('/refresh', refresh=True)
        self.assertTrue(Path(first['receipts'][0]).is_file())
        self.assertEqual(Handler.counts['/refresh'], 2)

    def test_invalid_url_and_symlink_paths_rejected(self):
        for url in ['file:///tmp/a', 'https://user:pass@example.org', 'http://@example.org']:
            with self.assertRaises(ValueError):
                capture(url, self.root)
        self.root.mkdir()
        target = self.root.parent / 'outside'
        target.mkdir()
        (self.root / 'objects').symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.get('/binary')
        self.assertEqual(list(target.iterdir()), [])

    def test_no_response_still_has_receipt(self):
        from unittest.mock import patch
        from urllib.error import URLError
        with patch('urllib.request.OpenerDirector.open', side_effect=URLError('offline')):
            result = self.get('/offline')
        self.assertEqual(result['attempts'], 3)
        self.assertEqual(result['capture_status'], 'failed')
        self.assertIsNone(result['path'])
        self.assertEqual(len(result['receipts']), 3)


if __name__ == '__main__':
    unittest.main()
