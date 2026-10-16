"""新skill入口的关键行为：显式范围、目录优先、版本选择、批次及退避。"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from urllib.error import HTTPError
from law_crawler.crawl_store import CrawlStore, unknown_metadata
from law_crawler.platform_adapters.base import Document, field
from law_crawler.regulation_pipeline import execute
from law_crawler.regulation_selection import select_latest

HOST='www.gesetze-im-internet.de'
BASE='https://'+HOST
PDF=b'%PDF-1.4\nworkflow fixture\n%%EOF'
XML=b'<Statute xmlns:lims="http://justice.gc.ca/lims" lims:id="1"><Identification><LongTitle>Law</LongTitle><Chapter><ConsolidatedNumber>A-1</ConsolidatedNumber></Chapter></Identification><Body><Section><Text>Provision text.</Text></Section></Body></Statute>'


def metadata(date=None):
    md=unknown_metadata(BASE+'/detail')
    md['title']=field('Example Law','Example Law','title',BASE+'/detail','Official title')
    md['document_type']=field('regulation','Act','type',BASE+'/detail','Official law type')
    md['legal_identity']=field(dict(jurisdiction='DE',issuer='Federal',type='Act',number='1',year='2020'),
                               'Act 1/2020','identity',BASE+'/detail','Identity')
    md['published_at']=field(date,date,'publication',BASE+'/detail','Publication date')
    return md


class Fixture:
    country='Germany';platform='gesetze-im-internet.de'
    entry_urls=(BASE+'/',);allowed_hosts=(HOST,)
    def discover(self,fetch,limit):
        fetch(BASE+'/');return [BASE+'/old',BASE+'/new'][:limit]
    def read_document(self,url,fetch):
        fetch(url)
        return Document(url,[url+'.pdf'],metadata('2020-01-01' if url.endswith('/old') else '2026-01-01'))


def arguments(root, **changes):
    fields=dict(root=root,config=root/'countries/global-config.properties',index=None,jurisdiction='Germany',
        mode='metadata',batch_id=None,rerun_batch=None,rerun_all=False,plan_only=False,
        platform='all',limit=2,per_platform_limit=1,max_details=2,timeout=1,delay=0,
        refresh=False,refresh_metadata=False,max_attempts=3,backoff_base=0,experimental_sources=False)
    fields.update(changes);return argparse.Namespace(**fields)


def workspace(root):
    p=root/'countries/Germany';p.mkdir(parents=True)
    (p/'index.md').write_text('[Law](https://www.gesetze-im-internet.de/)')
    (root/'countries/global-config.properties').write_text('downdloadDir=downloads')


class WorkflowTests(unittest.TestCase):
    def test_metadata_does_not_fetch_original_and_creates_plan_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workspace(root);calls=[]
            def fetch(url,*a):
                calls.append(url)
                self.assertFalse(url.endswith('.pdf'))
                self.assertTrue(list((root/'downloads/.batches').glob('*/plan.json')))
                return b'page',url
            with patch('law_crawler.regulation_pipeline.ADAPTERS',{'gesetze-im-internet.de':Fixture()}),patch('law_crawler.download_regulations.fetch',side_effect=fetch):
                self.assertEqual(execute(arguments(root,batch_id='metadata-test')),0)
            self.assertEqual(len(list((root/'downloads/Germany/catalog').glob('*.json'))),2)
            self.assertFalse(list((root/'downloads/Germany').glob('*.pdf')))
            attempt=root/'downloads/.batches/metadata-test/attempts/0001'
            for suffix in ('json','md','html'):self.assertTrue((attempt/('report.'+suffix)).exists())
            snap=json.loads((attempt/'candidates.json').read_text())
            self.assertEqual([c['selection']['selected'] for c in snap],[False,True])

    def test_latest_original_retry_failure_and_exact_batch_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workspace(root);calls=[]
            def fail(url,*a):
                calls.append(url)
                if url.endswith('.pdf'):raise TimeoutError('temporary')
                return b'page',url
            with patch('law_crawler.regulation_pipeline.ADAPTERS',{'gesetze-im-internet.de':Fixture()}),patch('law_crawler.download_regulations.fetch',side_effect=fail):
                # A completed catalog is useful but report must expose file failure.
                execute(arguments(root,mode='content',batch_id='content-test'))
            self.assertEqual(calls.count(BASE+'/new.pdf'),3)
            self.assertNotIn(BASE+'/old.pdf',calls)
            state=json.loads((root/'downloads/.batches/content-test/attempts/0001/states.json').read_text())['states']
            failures=[s for s in state.values() if s['status']=='fail']
            self.assertEqual(len(failures),1);self.assertEqual(failures[0]['attempts'],3)
            replay=[]
            def success(url,*a):
                replay.append(url);self.assertEqual(url,BASE+'/new.pdf');return PDF,url
            with patch('law_crawler.regulation_pipeline.ADAPTERS',{'gesetze-im-internet.de':Fixture()}),patch('law_crawler.download_regulations.fetch',side_effect=success):
                self.assertEqual(execute(arguments(root,jurisdiction=None,mode=None,rerun_batch='content-test')),0)
                self.assertEqual(execute(arguments(root,jurisdiction=None,mode=None,rerun_batch='content-test')),0)
            self.assertEqual(replay,[BASE+'/new.pdf'])
            self.assertEqual(len(list((root/'downloads/.batches/content-test/attempts').iterdir())),3)
            self.assertEqual(len(list((root/'downloads/Germany').glob('*.pdf'))),1)

    def test_403_does_not_retry_and_unknown_jurisdiction_has_no_side_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workspace(root)
            with self.assertRaises(ValueError):execute(arguments(root,jurisdiction='Unknown'))
            self.assertFalse((root/'downloads').exists())
            calls=[]
            def denied(url,*a):
                calls.append(url)
                if url.endswith('.pdf'):raise HTTPError(url,403,'Forbidden',{},None)
                return b'page',url
            with patch('law_crawler.regulation_pipeline.ADAPTERS',{'gesetze-im-internet.de':Fixture()}),patch('law_crawler.download_regulations.fetch',side_effect=denied):
                execute(arguments(root,mode='content'))
            self.assertEqual(calls.count(BASE+'/new.pdf'),1)

    def test_text_raw_and_markdown_have_separate_metadata_and_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);md=metadata('2026-01-01');url=BASE+'/law.xml'
            with CrawlStore(root) as store:
                result,record=store.save(url,BASE+'/detail','fixture',lambda u:(XML,u),md,convert_text=True)
                self.assertTrue(store.valid(record));self.assertEqual(record['file']['media_type'],'text/markdown')
                self.assertEqual(record['original_file']['sha256'],hashlib.sha256(XML).hexdigest())
                self.assertTrue((root/record['file']['path']).with_suffix('.json').exists())
                raw=root/record['original_file']['path']
                self.assertTrue(raw.with_name(raw.name+'.metadata.json').exists())
                self.assertIn('Provision text.',(root/record['file']['path']).read_text())
            (root/'crawl-state.sqlite3').unlink()
            with CrawlStore(root) as store:
                fetcher=Mock(side_effect=AssertionError('valid raw+md must reuse cache'))
                self.assertEqual(store.save(url,BASE+'/detail','fixture',fetcher,md,convert_text=True)[0],'existing')
                self.assertFalse(store.migration_errors);fetcher.assert_not_called()

    def test_cached_xml_converts_without_network(self):
        with tempfile.TemporaryDirectory() as tmp,CrawlStore(tmp) as store:
            md=metadata();url=BASE+'/law.xml'
            store.save(url,BASE+'/detail','fixture',lambda u:(XML,u),md)
            result,record=store.save(url,BASE+'/detail','fixture',Mock(side_effect=AssertionError('network')),md,convert_text=True)
            self.assertEqual(result,'existing');self.assertTrue(store.valid(record))
            self.assertEqual(record['file']['media_type'],'text/markdown')

    def test_rerun_repairs_corrupt_previously_successful_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workspace(root)
            def fetch(url,*a):return (PDF if url.endswith('.pdf') else b'page'),url
            with patch('law_crawler.regulation_pipeline.ADAPTERS',{'gesetze-im-internet.de':Fixture()}),patch('law_crawler.download_regulations.fetch',side_effect=fetch):
                self.assertEqual(execute(arguments(root,mode='content',batch_id='repair-file')),0)
            original=next((root/'downloads/Germany').glob('*.pdf'));original.write_bytes(b'broken')
            calls=[]
            def repair(url,*a):calls.append(url);return PDF,url
            with patch('law_crawler.regulation_pipeline.ADAPTERS',{'gesetze-im-internet.de':Fixture()}),patch('law_crawler.download_regulations.fetch',side_effect=repair):
                self.assertEqual(execute(arguments(root,jurisdiction=None,mode=None,rerun_batch='repair-file')),0)
            self.assertEqual(calls,[BASE+'/new.pdf'])
            self.assertEqual(original.read_bytes(),PDF)

    def test_failed_discovery_recovers_on_batch_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workspace(root)
            with patch('law_crawler.regulation_pipeline.ADAPTERS',{'gesetze-im-internet.de':Fixture()}),patch('law_crawler.download_regulations.fetch',side_effect=TimeoutError('directory offline')):
                self.assertEqual(execute(arguments(root,batch_id='recover-catalog')),1)
            original=(root/'downloads/.batches/recover-catalog/plan.json').read_bytes()
            with patch('law_crawler.regulation_pipeline.ADAPTERS',{'gesetze-im-internet.de':Fixture()}),patch('law_crawler.download_regulations.fetch',side_effect=lambda u,*a:(b'page',u)):
                self.assertEqual(execute(arguments(root,jurisdiction=None,mode=None,rerun_batch='recover-catalog')),0)
            self.assertEqual((root/'downloads/.batches/recover-catalog/plan.json').read_bytes(),original)
            self.assertEqual(len(json.loads((root/'downloads/.batches/recover-catalog/candidates.json').read_text())),2)

    def test_current_index_prefers_new_version_and_retains_old_original(self):
        with tempfile.TemporaryDirectory() as tmp,CrawlStore(tmp) as store:
            store.save(BASE+'/old.pdf',BASE+'/old','fixture',lambda u:(PDF+b'old',u),metadata('2020-01-01'))
            store.save(BASE+'/new.pdf',BASE+'/new','fixture',lambda u:(PDF+b'new',u),metadata('2026-01-01'))
            rows=store.update_current_selection('Germany')
            selected=[c for c in rows if c['selection']['selected']]
            self.assertEqual(len(selected),1)
            self.assertEqual(selected[0]['metadata']['published_at']['value'],'2026-01-01')
            self.assertEqual(len(list(Path(tmp).glob('*.pdf'))),2)

    def test_markdown_repairs_with_fixed_evidence_and_no_orphan_raw(self):
        html=b'<!doctype html><html><body><main><h1>Law</h1><p>Article 1 provision.</p></main></body></html>'
        md=metadata('2020-01-01')
        md.update(text_body_confirmed=field(True,'main','body',BASE+'/detail','body confirmed'),
                  text_body_selector=field('main','main','body',BASE+'/detail','body selector'))
        with tempfile.TemporaryDirectory() as tmp,CrawlStore(tmp) as store:
            _,record=store.save(BASE+'/law.html',BASE+'/old','fixture',lambda u:(html,u),md,convert_text=True)
            self.assertFalse(list(Path(tmp).glob('*.html')))
            _,record=store.save(BASE+'/law.html',BASE+'/new','fixture',Mock(side_effect=AssertionError('network')),metadata('2026-01-01'),convert_text=True)
            self.assertTrue(store.valid(record))
            path=Path(tmp)/record['file']['path'];path.write_text('damaged')
            _,record=store.save(BASE+'/law.html',BASE+'/new','fixture',lambda u:(html,u),metadata('2026-01-01'),convert_text=True)
            self.assertTrue(store.valid(record));self.assertEqual(record['file']['media_type'],'text/markdown')

    def test_unknown_competing_versions_are_not_guessed(self):
        candidates=[dict(id=str(i),country='Germany',platform='fixture',detail_url=BASE+'/'+str(i),metadata=metadata()) for i in (1,2)]
        self.assertFalse(select_latest(candidates))
        self.assertTrue(all(c['selection']['latest_unverified'] for c in candidates))

    def test_same_bytes_prefer_newest_source_metadata(self):
        with tempfile.TemporaryDirectory() as tmp,CrawlStore(tmp) as store:
            store.save(BASE+'/old.pdf',BASE+'/old','fixture',lambda u:(PDF,u),metadata('2020-01-01'))
            _,record=store.save(BASE+'/new.pdf',BASE+'/new','fixture',lambda u:(PDF,u),metadata('2026-01-01'))
            self.assertEqual(record['document']['published_at']['value'],'2026-01-01')
            self.assertEqual(len(record['sources']),2)
