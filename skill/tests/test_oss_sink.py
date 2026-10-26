"""OSS sink 离线 SDK 替身；本测试在服务器执行，不连接 OSS。"""
import hashlib
import io
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from law_crawler.oss_sink import OSSSink

class ServiceError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code

class FakeBucket:
    def __init__(self):
        self.objects = {}
        self.puts = {}
        self.gets = {}
        self.denied = False
        self.calls = 0
        self.corrupt = False
        self.lock = threading.Lock()
    def head_object(self, key):
        self.calls += 1
        if self.denied:
            raise ServiceError(403, 'AccessDenied')
        if key not in self.objects:
            raise ServiceError(404, 'NoSuchKey')
        body, headers = self.objects[key]
        return SimpleNamespace(content_length=len(body), headers=headers)
    def list_objects(self, prefix, max_keys):
        return SimpleNamespace(object_list=[SimpleNamespace(key=k) for k in self.objects if k.startswith(prefix)])
    def put_object(self, key, content, headers):
        with self.lock:
            if key in self.objects:
                raise ServiceError(409, 'FileAlreadyExists')
            self.puts[key] = self.puts.get(key, 0) + 1
            self.objects[key] = (content, headers)
    def put_object_from_file(self, key, path, headers):
        self.put_object(key, Path(path).read_bytes(), headers)
    def get_object(self, key):
        self.gets[key] = self.gets.get(key, 0) + 1
        return io.BytesIO(b'corrupt' if self.corrupt else self.objects[key][0])

class SinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'file.pdf'
        self.path.write_bytes(b'%PDF-1.7 test regulation')
        self.digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.bucket = FakeBucket()
        self.sink = self.new_sink()
        self.receipt = dict(capture_status='saved', complete=True, status_code=200,
                            path=str(self.path), sha256=self.digest, size_bytes=self.path.stat().st_size)
    def new_sink(self):
        return OSSSink(credentials=None, cache_path=Path(self.tmp.name) / 'cache.json',
                       bucket_factory=lambda: self.bucket, bucket='law-crawler',
                       endpoint='https://oss-ap-southeast-1-internal.aliyuncs.com',
                       region='ap-southeast-1', prefix='ods/Indonesia')
    def test_concurrent_dedup(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: self.sink.put_original(self.receipt), range(10)))
        self.assertTrue(all(r['status'] != 'failed' for r in results))
        self.assertEqual(sum(self.bucket.puts.values()), 1)
        self.assertEqual(sum(self.bucket.gets.values()), 1)
    def test_distinct_sources_and_changed_metadata(self):
        a = self.sink.put_metadata('https://official/a', {'files': []}, batch_id='batch')
        b = self.sink.put_metadata('https://official/b', {'files': []}, batch_id='batch')
        c = self.sink.put_metadata('https://official/a', {'files': ['new']}, batch_id='batch')
        self.assertEqual(len({a['key'], b['key'], c['key']}), 3)
    def test_conflict_never_overwrites(self):
        key = 'ods/Indonesia/raw/' + self.digest + '.pdf'
        self.bucket.objects[key] = (b'wrong', {})
        result = self.sink.put_original(self.receipt)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(self.bucket.puts)
        self.assertEqual(self.bucket.objects[key][0], b'wrong')
    def test_denied_circuit_breaker(self):
        self.bucket.denied = True
        first = self.sink.put_original(self.receipt)
        count = self.bucket.calls
        second = self.sink.put_original(self.receipt)
        self.assertEqual(first['attempts'], 1)
        self.assertEqual(second['attempts'], 0)
        self.assertEqual(count, self.bucket.calls)
        self.assertIn('403', self.sink.auth_failure)
    def test_remote_corruption_fails(self):
        self.bucket.corrupt = True
        result = self.sink.put_original(self.receipt)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('SHA256', result['reason'])
    def test_restart_remote_only_receipt(self):
        first = self.sink.put_original(self.receipt)
        self.path.unlink()
        restarted = self.new_sink()
        second = restarted.verify_original(first)
        self.assertEqual(second['status'], 'reused_verified')
        self.assertEqual(self.bucket.gets[first['key']], 2)
    def test_other_extension_reused(self):
        key = 'ods/Indonesia/raw/' + self.digest + '.bin'
        self.bucket.objects[key] = (self.path.read_bytes(), {})
        result = self.sink.put_original(self.receipt)
        self.assertEqual(result['key'], key)
        self.assertFalse(self.bucket.puts)
    def test_local_hash_mismatch(self):
        self.path.write_bytes(b'changed')
        self.assertEqual(self.sink.put_original(self.receipt)['status'], 'failed')
        self.assertEqual(self.bucket.calls, 0)
    def test_wrong_remote_identity(self):
        result = self.sink.verify_original({'raw_key': 'elsewhere/file.pdf', 'sha256': self.digest, 'size_bytes': 1})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.bucket.calls, 0)

if __name__ == '__main__':
    unittest.main()
