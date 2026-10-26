"""Run on deployment host: catalogue transport and retry invariants."""
import io
import json
import ssl
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from law_crawler.catalog_census_common import Census

URL='https://official.example/catalog'

class Response(io.BytesIO):
    def __init__(self,body=b'catalog',length=None,url=URL):
        super().__init__(body);self.url=url;self.status=200;self.read_sizes=[]
        self.headers=Message();self.headers['Content-Type']='text/html'
        self.headers['Content-Length']=str(len(body) if length is None else length)
    def read(self,size=-1):
        self.read_sizes.append(size)
        return super().read(size)

class Opener:
    def __init__(self,results):self.results=list(results);self.requests=[]
    def open(self,request,timeout):
        self.requests.append(request)
        result=self.results.pop(0)
        if isinstance(result,Exception):raise result
        return result

def http_error(code,hint=None):
    h=Message()
    if hint is not None:h['Retry-After']=str(hint)
    return HTTPError(URL,code,'test',h,io.BytesIO(b'error'))

class CensusTransportTests(unittest.TestCase):
    def fixture(self,results):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        census=Census(Path(temp.name),'test',{'official.example'},cutoff='2025-06-30')
        self.addCleanup(census.db.close)
        opener=Opener(results);census.opener=opener
        return census,opener
    def attempts(self,census):
        return [json.loads(p.read_text()) for p in (census.root/'responses').glob('*.*.*.json')]
    def test_post_headers_streaming_and_cache_reuse(self):
        response=Response(b'x'*150000);c,o=self.fixture([response])
        with patch('law_crawler.catalog_census_common.time.sleep'):
            body,final=c.fetch(URL,data=b'page=2',headers={'Referer':'https://official.example/','Accept-Encoding':'gzip','Cookie':'private-session'})
            self.assertEqual(c.fetch(URL,data=b'page=2'),(body,final))
        self.assertEqual(len(o.requests),1);self.assertEqual(o.requests[0].data,b'page=2')
        self.assertEqual(o.requests[0].get_header('Accept-encoding'),'identity')
        self.assertEqual(len(body),150000);self.assertTrue(all(0<n<=65536 for n in response.read_sizes))
        self.assertEqual(c.requests,1);self.assertEqual(len(self.attempts(c)),1)
        for p in (c.root/'responses').glob('*.json'):
            self.assertNotIn('private-session',p.read_text());self.assertNotIn('Cookie',p.read_text())
    def test_retry_after_and_three_total_attempts(self):
        c,o=self.fixture([http_error(429,7),http_error(503,7),Response()]);delays=[]
        with patch('law_crawler.catalog_census_common.time.sleep',side_effect=delays.append):c.fetch(URL)
        self.assertEqual(len(o.requests),3);self.assertEqual(delays,[7,7,.25])
        self.assertEqual(sorted(r['attempt'] for r in self.attempts(c)),[1,2,3])
        self.assertEqual(sorted(r['attempt_status'] for r in self.attempts(c)),['retrying','retrying','succeeded'])
        self.assertFalse(c.failures)
    def test_permanent_errors_single_attempt(self):
        for error in (http_error(403),http_error(404),URLError(ssl.SSLCertVerificationError('test certificate'))):
            with self.subTest(error=type(error).__name__):
                c,o=self.fixture([error])
                with patch('law_crawler.catalog_census_common.time.sleep') as sleep:
                    with self.assertRaises(Exception):c.fetch(URL)
                    sleep.assert_not_called()
                self.assertEqual(len(o.requests),1);self.assertEqual(len(c.failures),1)
                self.assertFalse(list((c.root/'responses').glob('*.body')))
    def test_truncated_and_empty_retry_no_success_cache(self):
        for body,length in ((b'x',4),(b'',0)):
            with self.subTest(body=body):
                c,o=self.fixture([Response(body,length) for _ in range(3)])
                with patch('law_crawler.catalog_census_common.time.sleep'):
                    with self.assertRaises(Exception):c.fetch(URL)
                self.assertEqual(len(o.requests),3);self.assertEqual(len(self.attempts(c)),3)
                self.assertFalse(any(r['ok'] for r in self.attempts(c)))
                self.assertFalse(list((c.root/'responses').glob('*.body')))
    def test_oversize_and_redirect_fail_without_retry(self):
        for response in (Response(b'x'*(12*1024*1024+1)),Response(url='https://other.example/')):
            c,o=self.fixture([response])
            with patch('law_crawler.catalog_census_common.time.sleep'):
                with self.assertRaises(ValueError):c.fetch(URL)
            self.assertEqual(len(o.requests),1)
    def test_long_retry_after_deferred_and_history_not_overwritten(self):
        c,o=self.fixture([http_error(429,600),http_error(403)])
        with patch('law_crawler.catalog_census_common.time.sleep') as sleep:
            with self.assertRaises(Exception):c.fetch(URL)
            first={p.name:p.read_bytes() for p in (c.root/'responses').glob('*.*.*.json')}
            with self.assertRaises(Exception):c.fetch(URL)
            sleep.assert_not_called()
        self.assertEqual(len(o.requests),2);self.assertEqual(len(self.attempts(c)),2)
        for name,data in first.items():self.assertEqual((c.root/'responses'/name).read_bytes(),data)
        self.assertTrue(any(r.get('retry_after_seconds')==600 for r in self.attempts(c)))
