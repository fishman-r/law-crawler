"""Coverage evidence invariants; execute on deployment/test host only."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from law_crawler import coverage_matrix as cm


class CoverageMatrixTests(unittest.TestCase):
    def fixture(self,folder):
        root=Path(folder);registry=root/'registry';index=registry/'countries/Canada/index.md';index.parent.mkdir(parents=True)
        index.write_text('[Laws](https://laws.justice.gc.ca/)\n[Unknown](https://unknown.official.example/)')
        census=root/'census';census.mkdir();source=census/'laws.justice.gc.ca';source.mkdir()
        cp=dict(jurisdiction='Canada',cutoff='2025-12-31',versions='all_discoverable_versions',platforms=['laws.justice.gc.ca','unknown.official.example'],index_sha256=cm.sha(index.read_bytes()))
        (census/'plan.json').write_text(json.dumps(cp));(source/'plan.json').write_text(json.dumps(dict(platform='laws.justice.gc.ca',cutoff=cp['cutoff'])))
        with sqlite3.connect(source/'records.sqlite3') as db:
            db.execute('CREATE TABLE records(data TEXT)')
            for i in range(2):db.execute('INSERT INTO records VALUES(?)',(json.dumps(dict(url='https://laws.justice.gc.ca/'+str(i),published_at='2025-01-01')),))
        (source/'status.json').write_text(json.dumps(dict(status='finished',updated_at='2025-01-02T00:00:00Z',observed_total=2,catalogue_complete=True,all_versions_complete=True)))
        batch=root/'batch';batch.mkdir()
        with sqlite3.connect(batch/'queue.sqlite3') as db:
            db.executescript('CREATE TABLE jobs(id TEXT,status TEXT,platform TEXT,record TEXT);CREATE TABLE objects(sha TEXT,bytes INT,key TEXT);')
            for i in range(2):db.execute('INSERT INTO jobs VALUES(?,?,?,?)',(str(i),'completed','laws.justice.gc.ca',json.dumps(dict(url=str(i)))))
            for i in range(3):db.execute('INSERT INTO objects VALUES(?,?,?)',(str(i),10,'key'+str(i)))
        digest=self.queue_sha(batch)
        bp=dict(jurisdiction='Canada',expected=2,queue_sha256=digest,census_root=str(census),registry_index_sha256=cm.sha(index.read_bytes()))
        self.plan(batch,bp)
        return registry,census,source,batch,index

    def queue_sha(self,batch):
        digest=hashlib.sha256()
        with sqlite3.connect(batch/'queue.sqlite3') as db:
            for row in db.execute('SELECT id,platform,record FROM jobs ORDER BY id'):digest.update(json.dumps(row,ensure_ascii=False).encode())
        return digest.hexdigest()

    def plan(self,batch,value):
        raw=json.dumps(value).encode();(batch/'plan.json').write_bytes(raw);(batch/'plan.sha256').write_text(cm.sha(raw)+'\n')

    def valid_coverage(self,census,source):
        p=json.loads((census/'plan.json').read_text());scope=dict(platform=source.name,cutoff=p['cutoff'],versions=p['versions'])
        evidence=source/'official-total.html';evidence.write_text('Official explicit total: 4 distinct detail URLs')
        return dict(review_status='independently_verified',reviewer='independent fixture reviewer',review_note='Count unit and cutoff match',
            numerator=dict(value=2,unit='distinct_detail_urls',scope=scope),
            denominator=dict(value=4,unit='distinct_detail_urls',scope=scope,basis='official_explicit_total'),
            evidence=[dict(path=evidence.name,sha256=cm.sha(evidence.read_bytes()),source_url='https://laws.justice.gc.ca/catalog')])

    def test_all_queue_complete_is_not_legal_or_annex_version_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            registry,census,source,batch,index=self.fixture(d)
            before={p:cm.sha(p.read_bytes()) for p in [batch/'plan.json',batch/'queue.sqlite3',source/'records.sqlite3']}
            result=cm.build('Canada',registry,census,batch)
            self.assertEqual(result['queue']['queue_completion']['ratio'],1)
            self.assertEqual(result['queue']['unique_object_count'],3)
            self.assertEqual(result['queue']['total_tasks'],2)
            for metric in result['overall'].values():self.assertIsNone(metric['ratio'])
            observed=result['platforms'][0]['observed_sources'][0]
            self.assertIsNone(observed['catalogue_coverage']['ratio'])
            self.assertTrue(observed['all_versions_complete_reported'])
            self.assertIsNone(observed['historical_version_coverage']['ratio'])
            self.assertEqual(before,{p:cm.sha(p.read_bytes()) for p in before})

    def test_unadapted_index_entry_kept_and_no_live_access_claim(self):
        with tempfile.TemporaryDirectory() as d:
            registry,census,source,batch,index=self.fixture(d);r=cm.build('Canada',registry,census)
            unknown=next(x for x in r['platforms'] if x['platform']=='unknown.official.example')
            self.assertFalse(unknown['static_capabilities']['registered_adapter'])
            self.assertEqual(unknown['actual_access'],'not_observed')
            self.assertIsNone(unknown['observed_sources'][0]['distinct_detail_urls'])
            self.assertTrue(unknown['next_actions'])

    def test_only_review_bound_matching_scope_unit_can_produce_ratio(self):
        with tempfile.TemporaryDirectory() as d:
            registry,census,source,batch,index=self.fixture(d);block=self.valid_coverage(census,source)
            status=json.loads((source/'status.json').read_text());status['coverage_evidence']={'catalogue':block};(source/'status.json').write_text(json.dumps(status))
            metric=cm.build('Canada',registry,census)['platforms'][0]['observed_sources'][0]['catalogue_coverage']
            self.assertEqual(metric['ratio'],.5);self.assertEqual(metric['status'],'review_bound')
            for mode in ('unit','scope','bool','too_small','missing_review','bad_hash','bad_host'):
                bad=copy.deepcopy(block)
                if mode=='unit':bad['denominator']['unit']='catalogue_cards'
                if mode=='scope':bad['denominator']['scope']['cutoff']='2024-01-01'
                if mode=='bool':bad['denominator']['value']=True
                if mode=='too_small':bad['denominator']['value']=1
                if mode=='missing_review':bad.pop('reviewer')
                if mode=='bad_hash':bad['evidence'][0]['sha256']='a'*64
                if mode=='bad_host':bad['evidence'][0]['source_url']='https://unofficial.example/total'
                status['coverage_evidence']={'catalogue':bad};(source/'status.json').write_text(json.dumps(status))
                with self.subTest(mode=mode):self.assertIsNone(cm.build('Canada',registry,census)['platforms'][0]['observed_sources'][0]['catalogue_coverage']['ratio'])

    def test_zero_denominator_does_not_claim_100_percent(self):
        with tempfile.TemporaryDirectory() as d:
            registry,census,source,batch,index=self.fixture(d);b=self.valid_coverage(census,source)
            b['numerator']['value']=b['denominator']['value']=0
            self.assertIsNone(cm.ratio_from_evidence({'coverage_evidence':{'catalogue':b}},0,b['numerator']['scope'],source)['ratio'])

    def test_queue_tampering_or_scope_mismatch_refused(self):
        for mode in ('record','count','jurisdiction','census_path','source','signature'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                registry,census,source,batch,index=self.fixture(d);p=json.loads((batch/'plan.json').read_text())
                if mode=='record':
                    with sqlite3.connect(batch/'queue.sqlite3') as db:db.execute("UPDATE jobs SET record='changed' WHERE id='0'")
                if mode=='count':p['expected']=3;self.plan(batch,p)
                if mode=='jurisdiction':p['jurisdiction']='Indonesia';self.plan(batch,p)
                if mode=='census_path':p['census_root']=str(Path(d)/'other');self.plan(batch,p)
                if mode=='signature':(batch/'plan.sha256').write_text('f'*64)
                if mode=='source':
                    with sqlite3.connect(batch/'queue.sqlite3') as db:db.execute("UPDATE jobs SET platform='bkpm.go.id'")
                    p['queue_sha256']=self.queue_sha(batch);self.plan(batch,p)
                with self.assertRaises(ValueError):cm.build('Canada',registry,census,batch)

    def test_index_drift_is_explicit_not_silent(self):
        with tempfile.TemporaryDirectory() as d:
            registry,census,source,batch,index=self.fixture(d);index.write_text(index.read_text()+'\nchanged prose\n')
            r=cm.build('Canada',registry,census,batch)
            self.assertTrue(r['scope']['batch_index_binding']['drift']);self.assertTrue(r['scope']['census_index_binding']['drift'])
            self.assertIsNone(r['overall']['regulation_coverage']['ratio'])

    def test_extra_unknown_census_source_not_silently_merged(self):
        with tempfile.TemporaryDirectory() as d:
            registry,census,source,batch,index=self.fixture(d);extra=census/'other-source';extra.mkdir()
            (extra/'plan.json').write_text(json.dumps(dict(platform='other-source',cutoff='2025-12-31')))
            r=cm.build('Canada',registry,census);row=next(x for x in r['platforms'] if x['platform']=='other-source')
            self.assertFalse(row['index_registered']);self.assertTrue(row['unresolved'])

    def test_versions_missing_invalid_urls_and_source_hash_changes(self):
        for mode in ('versions','url','source_hash'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                registry,census,source,batch,index=self.fixture(d);block=self.valid_coverage(census,source)
                status=json.loads((source/'status.json').read_text());status['coverage_evidence']={'catalogue':block};(source/'status.json').write_text(json.dumps(status))
                if mode=='versions':
                    cp=json.loads((census/'plan.json').read_text());cp.pop('versions');(census/'plan.json').write_text(json.dumps(cp))
                if mode=='url':
                    with sqlite3.connect(source/'records.sqlite3') as db:db.execute('INSERT INTO records VALUES(?)',(json.dumps({'title':'no URL'}),))
                if mode=='source_hash':
                    bp=json.loads((batch/'plan.json').read_text());bp['sources']=[{'source':source.name,'plan_sha256':'a'*64,'records_sha256':'b'*64}];self.plan(batch,bp)
                    with self.assertRaises(ValueError):cm.build('Canada',registry,census,batch)
                else:
                    result=cm.build('Canada',registry,census)
                    self.assertIsNone(result['platforms'][0]['observed_sources'][0]['catalogue_coverage']['ratio'])

    def test_alias_index_hosts_share_one_platform_row(self):
        with tempfile.TemporaryDirectory() as d:
            registry,census,source,batch,index=self.fixture(d)
            index.write_text(index.read_text()+'\n[Alias](https://www.laws.justice.gc.ca/)')
            result=cm.build('Canada',registry,census,batch)
            # Registry normalizes www aliases; no second queue count is introduced.
            self.assertEqual(len([r for r in result['platforms'] if r['canonical_platform']=='laws.justice.gc.ca']),1)
            self.assertEqual(result['queue']['total_tasks'],2)

    def test_output_formats_and_refuse_overwriting_source_or_report(self):
        with tempfile.TemporaryDirectory() as d:
            registry,census,source,batch,index=self.fixture(d);out=Path(d)/'report'
            cm.write('Canada',out,registry,census,batch)
            self.assertTrue(all((out/('matrix.'+s)).is_file() for s in ('json','md','html')))
            self.assertIn('下一步',(out/'matrix.html').read_text())
            with self.assertRaises(ValueError):cm.write('Canada',out,registry,census,batch)
            with self.assertRaises(ValueError):cm.write('Canada',census,registry,census,batch)


if __name__=='__main__':unittest.main()
