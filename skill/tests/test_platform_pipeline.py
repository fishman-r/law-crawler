import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from law_crawler.platform_adapters.base import Adapter, Document
from law_crawler.crawl_store import CrawlStore, unknown_metadata
from law_crawler import download_regulations as app
class TestAdapter(Adapter):
    def __init__(self, host):
        self.platform=host
        self.allowed_hosts=(host,)
        self.entry_urls=(f'https://{host}/',)
    def discover(self, fetch, limit):
        fetch(self.entry_urls[0])
        return [self.entry_urls[0]+'detail-a',self.entry_urls[0]+'detail-b'][:limit]
    def read_document(self, url, fetch):
        return Document(url,[url+'.pdf'],unknown_metadata(url))


class PlatformPipelineTests(unittest.TestCase):
    def test_experimental_success_preserves_production_failure(self):
        adapter = TestAdapter('a.example')
        adapter.production_discovery = dict(status='failed', error='TLS EOF')
        metadata = unknown_metadata('https://a.example/detail-a')
        metadata['source_route'] = dict(value='experimental_official_fallback')
        adapter.read_document = lambda url, fetch: Document(url, [url+'.pdf'], metadata)
        args = argparse.Namespace(limit=1, per_platform_limit=1, max_details=1,
                                  timeout=2, delay=0, refresh=False)
        report = dict(files=[], platforms=[], errors=[], counts={}, download_dir='unused-test-output')
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(Path(tmp)) as store:
            with patch.dict(app.ADAPTERS, {'a.example': adapter}, clear=True), patch.object(
                    app, 'official_fetch', side_effect=lambda url, *a: (b'%PDF-1.4 test\n%%EOF', url)):
                app.download_platform(args, store, report, 'a.example', {})
        state = report['platforms'][0]
        self.assertEqual(state['status'], 'experimental_success')
        self.assertEqual(state['production_discovery']['error'], 'TLS EOF')

    def test_per_platform_limit_keeps_later_platforms_and_records_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'index').write_text('[a](https://a.example/)\n[b](https://b.example/)\n[c](https://c.example/)')
            (root/'config').write_text('downdloadDir=downloads')
            args=argparse.Namespace(country='Indonesia', root=root,index=root/'index',config=root/'config',platform='all',
                                    limit=10,per_platform_limit=1,max_details=5,timeout=2,delay=0,
                                    refresh=False,refresh_metadata=False)
            calls=[]
            def fetch(url, timeout):
                calls.append(url)
                if 'b.example' in url: raise TimeoutError('blocked')
                return (b'%PDF-1.4\n'+url.encode()+b'\n%%EOF',url) if url.endswith('.pdf') else (b'list',url)
            adapters={host:TestAdapter(host) for host in ('a.example','b.example','c.example')}
            with patch.dict(app.ADAPTERS,adapters,clear=True),patch.object(app,'fetch',side_effect=fetch):
                self.assertEqual(app.run(args),0)
            report=json.loads((root/'downloads/Indonesia/last-run.json').read_text())
            self.assertEqual([p['status'] for p in report['platforms']],['success','failed','success'])
            self.assertEqual(report['unique_files'],2)
            self.assertFalse(any('detail-b' in u for u in calls))
            states=json.loads((root/'downloads/Indonesia/platform-status.json').read_text())
            self.assertEqual(states['b.example']['errors'][0]['error'],'blocked')

    def test_refresh_preserves_old_metadata_when_detail_replaces_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'index').write_text('[a](https://a.example/)')
            (root/'config').write_text('downdloadDir=downloads')
            old=unknown_metadata('https://a.example/detail-a')
            old['title']['value']='OLD LAW'
            with CrawlStore(root/'downloads/Indonesia') as store:
                store.save('https://a.example/old.pdf','https://a.example/detail-a','a.example',
                           lambda u:(b'%PDF-1.4 old\n%%EOF',u),old)
            args=argparse.Namespace(country='Indonesia', root=root,index=root/'index',config=root/'config',platform='all',
                                    limit=10,per_platform_limit=1,timeout=2,delay=0,refresh_metadata=True)
            with patch.dict(app.ADAPTERS,{'a.example':TestAdapter('a.example')},clear=True):
                self.assertEqual(app.run(args),1)
            with CrawlStore(root/'downloads/Indonesia') as store:
                self.assertEqual(store.records()[0]['document']['title']['value'],'OLD LAW')
            report=json.loads((root/'downloads/Indonesia/last-run.json').read_text())
            self.assertEqual(report['errors'][0]['code'],'stale_source')

    def test_refresh_protects_old_version_when_final_url_alias_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'index').write_text('[a](https://a.example/)')
            (root/'config').write_text('downdloadDir=downloads')
            detail='https://a.example/detail-a'
            final='https://a.example/current.pdf'
            old=unknown_metadata(detail)
            old['title']['value']='OLD LAW'
            with CrawlStore(root/'downloads/Indonesia') as store:
                _, old_record=store.save('https://a.example/redirect',detail,'a.example',
                                         lambda u:(b'%PDF-1.4 old\n%%EOF',final),old)
                store.save(final,detail,'a.example',lambda u:(b'%PDF-1.4 new\n%%EOF',u),refresh=True)
            adapter=TestAdapter('a.example')
            latest=unknown_metadata(detail)
            latest['title']['value']='NEW LAW'
            adapter.read_document=lambda u, fetch: Document(u,[final],latest)
            args=argparse.Namespace(country='Indonesia', root=root,index=root/'index',config=root/'config',platform='all',
                                    limit=10,per_platform_limit=10,timeout=2,delay=0,refresh_metadata=True)
            with patch.dict(app.ADAPTERS,{'a.example':adapter},clear=True):
                self.assertEqual(app.run(args),0)
            with CrawlStore(root/'downloads/Indonesia') as store:
                self.assertEqual(store.by_hash(old_record['file']['sha256'])['document']['title']['value'],'OLD LAW')
            report=json.loads((root/'downloads/Indonesia/last-run.json').read_text())
            self.assertEqual(report['errors'][0]['code'],'stale_source')

    def test_metadata_refresh_respects_per_platform_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'index').write_text('[a](https://a.example/)')
            (root/'config').write_text('downdloadDir=downloads')
            with CrawlStore(root/'downloads/Indonesia') as store:
                for key in ('a','b'):
                    detail='https://a.example/detail-'+key
                    store.save(detail+'.pdf',detail,'a.example',
                               lambda u:(b'%PDF-1.4 '+u.encode()+b'\n%%EOF',u),unknown_metadata(detail))
            args=argparse.Namespace(country='Indonesia', root=root,index=root/'index',config=root/'config',platform='all',
                                    limit=10,per_platform_limit=1,timeout=2,delay=0,refresh_metadata=True)
            with patch.dict(app.ADAPTERS,{'a.example':TestAdapter('a.example')},clear=True):
                self.assertEqual(app.run(args),0)
            report=json.loads((root/'downloads/Indonesia/last-run.json').read_text())
            self.assertEqual(report['counts'],{'metadata_updated':1})

    def test_official_fetch_rejects_outside_host(self):
        with patch.object(app,'fetch') as fetch:
            with self.assertRaisesRegex(ValueError,'白名单'):
                app.official_fetch('https://not-official.example/a',2,('official.example',))
            fetch.assert_not_called()
        with patch.object(app,'fetch',return_value=(b'body','https://other.example/a')):
            with self.assertRaisesRegex(ValueError,'跳转'):
                app.official_fetch('https://official.example/a',2,('official.example',))

if __name__=='__main__':unittest.main()
