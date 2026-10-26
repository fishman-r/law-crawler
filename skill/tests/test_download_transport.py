"""HTTP integration fixtures; run on the deployment host, not the workstation."""
import hashlib
import errno
import io
import json
import ssl
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from law_crawler.download_transport import (stream_response, is_retryable, retry_delay,
    retry_after_seconds, wait_delay, RetryDeferred, IncompleteResponse, SizeLimitExceeded)
from law_crawler.download_regulations import fetch
from law_crawler.batch_runtime import retry_fetch
from law_crawler.raw_capture import capture


class Handler(BaseHTTPRequestHandler):
    counts={}
    def log_message(self,*a):pass
    def do_GET(self):
        n=self.counts.get(self.path,0)+1;self.counts[self.path]=n
        status=200;body=b'%PDF-1.7\nshared transport fixture\n%%EOF'
        if self.path.startswith('/retry') and n<3:status=429
        if self.path.startswith('/server') and n<3:status=500
        if self.path.startswith('/permanent'):status=403
        if self.path.startswith('/long'):status=429
        self.send_response(status);self.send_header('Content-Type','application/pdf')
        if self.path.startswith('/retry'):self.send_header('Retry-After','2')
        if self.path.startswith('/long'):self.send_header('Retry-After','601')
        self.send_header('Content-Length',str(len(body)+(5 if self.path.startswith('/short') else 0)))
        self.end_headers();self.wfile.write(body);self.close_connection=True


class SharedTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def setUp(self):
        Handler.counts.clear();self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def traditional(self,path,max_bytes=1024,events=None,delays=None):
        return retry_fetch(lambda u:fetch(u,2,max_bytes),self.base+path,base_delay=0,
            sleep=(delays if delays is not None else []).append,event_callback=(events if events is not None else []).append,jitter=lambda:0)
    def archived(self,path,max_bytes=1024,**kw):
        return capture(self.base+path,self.root/path.strip('/'),timeout=2,max_bytes=max_bytes,backoff_base=0,**kw)
    def test_matching_bytes_cache_and_no_nested_retries(self):
        data,_=self.traditional('/server-legacy');r=self.archived('/server-raw')
        self.assertEqual(hashlib.sha256(data).hexdigest(),r['sha256'])
        self.assertEqual(Handler.counts,{'/server-legacy':3,'/server-raw':3})
        again=self.archived('/server-raw');self.assertEqual(again['capture_status'],'reused');self.assertEqual(Handler.counts['/server-raw'],3)
    def test_retry_after_honored_without_stop_event(self):
        sleeps=[];events=[];self.traditional('/retry-legacy',events=events,delays=sleeps);self.assertEqual(sleeps,[2,2])
        with patch('law_crawler.raw_capture.time.sleep') as sleep:
            r=self.archived('/retry-raw')
        self.assertEqual([x.args[0] for x in sleep.call_args_list],[2,2]);self.assertEqual(r['attempts'],3)
        records=[json.loads(Path(p).read_text()) for p in r['receipts']]
        self.assertEqual([x['backoff_seconds'] for x in records],[2,2,0])
    def test_permanent_oversize_and_short_transfers_consistent(self):
        for kind,attempts,exc in [('permanent',1,HTTPError),('large',1,SizeLimitExceeded),('short',3,IncompleteResponse)]:
            with self.subTest(kind=kind):
                limit=4 if kind=='large' else 1024
                with self.assertRaises(exc):self.traditional('/'+kind+'-legacy',limit)
                r=self.archived('/'+kind+'-raw',limit)
                self.assertEqual(r['capture_status'],'failed');self.assertEqual(r['attempts'],attempts)
                self.assertEqual(Handler.counts['/'+kind+'-legacy'],attempts)
                self.assertFalse(list((self.root/(kind+'-raw')/'index').glob('*.json')))
    def test_long_retry_after_is_deferred_not_shortened(self):
        events=[]
        with self.assertRaises(RetryDeferred) as ex:self.traditional('/long-legacy',events=events)
        self.assertEqual(events[0]['status'],'deferred');self.assertEqual(events[0]['not_before'],ex.exception.not_before)
        with patch('law_crawler.raw_capture.time.sleep') as sleep:r=self.archived('/long-raw')
        sleep.assert_not_called();self.assertEqual(r['attempts'],1);self.assertTrue(r['retry_deferred']);self.assertEqual(r['retry_after_seconds'],601)
    def test_global_stop_and_response_hook_are_not_dropped(self):
        stop=threading.Event();stop.set()
        with self.assertRaises(InterruptedError):self.archived('/stopped',stop_event=stop)
        self.assertNotIn('/stopped',Handler.counts)
        seen=[];self.archived('/ordinary',response_hook=seen.append);self.assertEqual(len(seen),1)
    def test_legacy_large_reads_are_chunked_and_bounded(self):
        class Response(io.BytesIO):
            headers={'Content-Length':'150000'};url='https://official.test/file'
            def read(self,n):
                if not 0<n<=65536:raise AssertionError('unbounded read')
                return super().read(n)
        with patch('urllib.request.urlopen',return_value=Response(b'x'*150000)):
            data,_=fetch('https://official.test/file',2,200000)
        self.assertEqual(len(data),150000)
    def test_policy_error_classification_jitter_dates_and_cancel(self):
        for code in (408,425,429,500,502,503,504):self.assertTrue(is_retryable(HTTPError('u',code,'',{},None)))
        for exc in (HTTPError('u',403,'',{},None),HTTPError('u',404,'',{},None),HTTPError('u',501,'',{},None),URLError(ssl.SSLCertVerificationError('bad')),SizeLimitExceeded(),InterruptedError(),OSError(errno.ENOSPC,"full"),PermissionError("denied")):self.assertFalse(is_retryable(exc))
        self.assertEqual(retry_delay(2,1,jitter=lambda:1),2.5)
        self.assertEqual(retry_delay(1,1,{'Retry-After':'5'},jitter=lambda:1),5)
        self.assertEqual(retry_after_seconds('Thu, 01 Jan 1970 00:01:40 GMT',now=90),10)
        stop=threading.Event();stop.set()
        with self.assertRaises(InterruptedError):wait_delay(4,stop_event=stop)
        with self.assertRaises(ValueError):stream_response(io.BytesIO(),lambda b:None,False)
    def test_invalid_length_preserves_bounded_diagnostic_body(self):
        class Response(io.BytesIO):
            headers={'Content-Length':'invalid'}
        output=io.BytesIO()
        with self.assertRaises(IncompleteResponse):
            stream_response(Response(b'official response'),output.write,100)
        self.assertEqual(output.getvalue(),b'official response')
    def test_host_long_cooldown_does_not_block_worker(self):
        import time
        from law_crawler.bulk_download_fast import HostPace
        stop=threading.Event();p=HostPace(0,stop);p.next['official.test']=time.monotonic()+1000
        r=p.capture('https://official.test/file',self.root)
        self.assertEqual(r['attempts'],0);self.assertTrue(r['retry_deferred']);self.assertTrue(Path(r['receipts'][0]).is_file())
