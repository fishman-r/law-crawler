"""Configuration contract fixtures; execute on the deployment validation host."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from law_crawler import census_to_oss as c


class CensusConfigurationTests(unittest.TestCase):
    def fixture(self,root,country='Canada',platform='laws.justice.gc.ca',n=3):
        root=Path(root);registry=root/'registry';index=registry/'countries'/country/'index.md';index.parent.mkdir(parents=True)
        index.write_text(f'# {country}\n[Official](https://{platform}/)\n')
        census=root/'census';source=census/platform;source.mkdir(parents=True)
        (census/'plan.json').write_text(json.dumps(dict(platforms=[platform],cutoff='2026-01-02')))
        (source/'plan.json').write_text(json.dumps(dict(platform=platform,cutoff='2026-01-02')))
        with sqlite3.connect(source/'records.sqlite3') as db:
            db.execute('create table records(data text)')
            db.executemany('insert into records values(?)',[(json.dumps(dict(url=f'https://{platform}/law/{i}',title='Example')), ) for i in range(n)])
        args=SimpleNamespace(registry_root=str(registry),jurisdiction=country,census_root=str(census),bucket='fixture-bucket',endpoint='https://oss.example.test',region='example-region',prefix='ods/'+country,expected=None,workers=None,old_metadata=None)
        return args,root/'output',registry,census

    def test_dynamic_count_country_destination_and_thresholds(self):
        with tempfile.TemporaryDirectory() as d:
            a,out,_,_=self.fixture(d);a.min_free_bytes=12345;a.max_file_bytes=98765
            db,p=c.freeze(a,out);self.addCleanup(db.close)
            self.assertEqual(p['expected'],3);self.assertEqual(p['jurisdiction'],'Canada');self.assertEqual(p['prefix'],'ods/Canada')
            self.assertEqual(p['min_free_bytes'],12345);self.assertEqual(p['max_file_bytes'],98765)
            self.assertNotIn('53430',p['cutoff_scope']);self.assertEqual(len(p['sources']),1)
            self.assertIsNone(p['old_metadata_sha256'])

    def test_invalid_inputs_leave_no_output(self):
        for mode in ('unknown','missing_storage','invalid_storage','wrong_country','expected','source_missing','no_scope','unknown_platform'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                a,out,registry,census=self.fixture(d)
                if mode=='unknown':a.jurisdiction='Unregistered'
                elif mode=='missing_storage':a.bucket=None
                elif mode=='invalid_storage':a.bucket='Invalid Bucket!'
                elif mode=='expected':a.expected=4
                elif mode=='wrong_country':
                    p=registry/'countries/Indonesia/index.md';p.parent.mkdir();p.write_text('[Official](https://laws.justice.gc.ca/)');a.jurisdiction='Indonesia'
                elif mode=='source_missing':(census/'plan.json').write_text(json.dumps(dict(platforms=['missing-source'])))
                elif mode=='no_scope':(census/'plan.json').unlink()
                elif mode=='unknown_platform':(census/'laws.justice.gc.ca/plan.json').write_text(json.dumps(dict(platform='unknown.example')))
                with self.assertRaises(ValueError):c.freeze(a,out)
                self.assertFalse(out.exists())

    def test_explicit_subset_and_missing_declared_source(self):
        with tempfile.TemporaryDirectory() as d:
            a,out,_,census=self.fixture(d)
            (census/'plan.json').write_text(json.dumps(dict(platforms=['laws.justice.gc.ca','not-downloaded'])))
            with self.assertRaises(ValueError):c.freeze(a,out)
            a.sources=['laws.justice.gc.ca'];db,p=c.freeze(a,out);db.close();self.assertEqual(p['expected'],3)

    def test_frozen_resume_inherits_and_does_not_rewrite(self):
        with tempfile.TemporaryDirectory() as d:
            a,out,registry,census=self.fixture(d);db,p=c.freeze(a,out);db.close()
            # Legacy shape retains original identity/count/hash and omits newer execution knobs.
            for key in ('registry_index','registry_index_sha256','timeout','backoff_base','host_delay','detail_max_bytes'):p.pop(key,None)
            p['cutoff_scope']='historic_observed_count';c.atomic(out/'plan.json',p);(out/'plan.sha256').write_text(c.file_sha256(out/'plan.json')+'\n')
            before=(out/'plan.json').read_bytes()
            inherited=SimpleNamespace(registry_root=str(registry))
            db,p2=c.freeze(inherited,out);db.close()
            self.assertEqual((out/'plan.json').read_bytes(),before);self.assertEqual(p2,p)
            self.assertEqual(inherited.prefix,'ods/Canada');self.assertEqual(inherited.expected,3)
            for key,value in [('bucket','another-bucket'),('expected',4),('jurisdiction','Indonesia'),('min_free_bytes',1)]:
                with self.subTest(key=key),self.assertRaises(ValueError):c.freeze(SimpleNamespace(registry_root=str(registry),**{key:value}),out)
            self.assertEqual((out/'plan.json').read_bytes(),before)

    def test_source_wrong_host_or_duplicate_rejected_before_output(self):
        for mode in ('host','duplicate'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                a,out,_,census=self.fixture(d)
                with sqlite3.connect(census/'laws.justice.gc.ca/records.sqlite3') as db:
                    db.execute('insert into records values(?)',(json.dumps(dict(url='https://evil.test/a' if mode=='host' else 'https://laws.justice.gc.ca/law/0')),))
                with self.assertRaises(ValueError):c.freeze(a,out)
                self.assertFalse(out.exists())

    def test_freeze_only_cli_needs_no_credentials(self):
        with tempfile.TemporaryDirectory() as d:
            a,out,_,_=self.fixture(d)
            argv=['--output',str(out),'--jurisdiction',a.jurisdiction,'--registry-root',a.registry_root,'--census-root',a.census_root,'--bucket',a.bucket,'--endpoint',a.endpoint,'--region',a.region,'--prefix',a.prefix,'--freeze-only']
            with patch('law_crawler.census_to_oss.OSSSink',side_effect=AssertionError('no sink during freeze')):c.main(argv)
            self.assertTrue((out/'plan.json').exists())

    def test_non_indonesia_dispatch_uses_country_adapter(self):
        with tempfile.TemporaryDirectory() as d:
            a,out,_,_=self.fixture(d);db,p=c.freeze(a,out);a.credentials='unused'
            class Sink:
                def put_metadata(self,*args,**kw):return dict(status='uploaded_verified')
            with patch('law_crawler.census_to_oss.OSSSink',return_value=Sink()):r=c.Runner(a,out,db,p)
            row=db.execute('select id,url,platform,record from jobs limit 1').fetchone()
            class Doc:
                file_urls=[];metadata={'identity_country':'CA'}
            with patch.object(c.ADAPTERS['laws.justice.gc.ca'],'read_document',return_value=Doc()) as read,patch('law_crawler.census_to_oss.read_catalog_document',side_effect=AssertionError('ID rules forbidden')):
                result=r.process(row)
            self.assertEqual(result['metadata']['identity_country'],'CA');read.assert_called_once();db.close()
