import copy
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from law_crawler import attachment_recovery as ar


class Sink:
    prefix='ods/Test';bucket_name='test'
    def __init__(self):self.objects={};self.gets=[];self.uploads=0;self.fail=False
    def _bucket(self):return self
    def get_object(self,key):self.gets.append(key);return io.BytesIO(self.objects[key])
    def put_metadata(self,target,document,batch_id):
        self.uploads+=1
        if self.fail:return {'status':'failed'}
        raw=ar.encoded(dict(schema_version=1,batch_id=batch_id,detail_key=target,document=document));digest=ar.sha(raw)
        key=self.prefix+'/metadata/'+batch_id+'/'+digest+'.json';self.objects[key]=raw
        return dict(status='uploaded_verified',metadata_key=key,sha256=digest,size_bytes=len(raw))
    def flush_cache(self):pass


class AttachmentClosureTests(unittest.TestCase):
    def fixture(self,folder,missing=None,fully=True):
        b=Path(folder)/'batch';b.mkdir();(b/'outcomes').mkdir()
        sink=Sink();t,s='a'*64,'b'*64
        def file(data,url):
            digest=ar.sha(data);key=sink.prefix+'/raw/'+digest+'.pdf';sink.objects[key]=data
            return dict(url=url,final_url=url,sha256=digest,size_bytes=len(data),complete=True,status_code=200,capture_status='saved',oss=dict(status='uploaded_verified',raw_key=key))
        body=file(b'original primary', 'https://jdih.kemenkeu.go.id/body.pdf')
        oldannex=file(b'previous attachment','https://jdih.kemenkeu.go.id/old.pdf')
        annex=file(b'new attachment','https://jdih.kemenkeu.go.id/new.pdf')
        missing=missing or ['Annex I']
        src=dict(target_id=t,parent_source_id=s,parent_original_sha256=body['sha256'],role='attachment',attachment_kind='annex')
        capture={k:v for k,v in annex.items() if k!='oss'}
        envelope=dict(bucket='test',source=src,capture=capture,raw_key=annex['oss']['raw_key'])
        raw=ar.encoded(envelope);mh=ar.sha(raw);mk=sink.prefix+'/metadata/archive/'+mh+'.json';sink.objects[mk]=raw;annex['oss']['metadata_key']=mk
        coverage=dict(description=missing[0],fully_resolved=fully,attachment_sha256s=[annex['sha256']],coverage_note='Independent reviewer read all pages; coverage is explicit')
        evidence=dict(**src,original_sha256=annex['sha256'],receipt=copy.deepcopy(annex),oss=annex['oss'],attachment_identity_approved=True,full_text_sha256='e'*64,missing_attachment_coverage=[coverage])
        ep=b/'evidence.json';ep.write_bytes(ar.encoded({'items':[evidence]}))
        item=dict(target_id=t,source_id=s,approved_primary_sha256s=[body['sha256']],resolved_missing_attachments=[copy.deepcopy(coverage)],additional_verified_attachments=[dict(role='attachment',attachment_kind='annex',parent_primary_sha256=body['sha256'],receipt=annex,evidence_report_path=str(ep),evidence_report_sha256=ar.sha(ep.read_bytes()),full_text_sha256='e'*64,metadata_sha256=mh)])
        before=dict(status='incomplete',files=[body,oldannex],recovery={'evidence':{'primary_text_identity_verified':True}},metadata=dict(known_missing_attachments=missing,all_attachments_verified=False,file_roles={body['url']:{'role':'regulation_body'},oldannex['url']:{'role':'attachment'}}))
        source=dict(status='completed',platform='kemenkeu.go.id',detail_url='https://jdih.kemenkeu.go.id/detail',files=[body])
        for key,value in ((t,before),(s,source)):(b/'outcomes'/f'{key}.json').write_bytes(ar.encoded(value))
        db=sqlite3.connect(b/'queue.sqlite3');db.executescript('CREATE TABLE jobs(id TEXT PRIMARY KEY,status TEXT,reason TEXT,files INT,bytes INT,updated_at TEXT,record TEXT);CREATE TABLE objects(sha TEXT PRIMARY KEY,bytes INT,key TEXT);');db.executemany('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)',[(t,'incomplete','old',2,sum(x['size_bytes'] for x in before['files']),'old','{}'),(s,'completed','source',1,body['size_bytes'],'old','{}')]);db.commit();db.close()
        rp=b/'review.json';rp.write_bytes(ar.encoded(dict(reviewer='independent reviewer',review_note='Read attachment and gap evidence',repairs=[item])))
        return b,t,s,sink,rp,before,item

    def freeze(self,b,rp,name='r1'):
        path=b/(name+'.json');ar.freeze(b,rp,ar.sha(rp.read_bytes()),path,name);return path,ar.sha(path.read_bytes())

    def test_complete_preserves_originals_source_and_rerun(self):
        with tempfile.TemporaryDirectory() as d:
            b,t,s,sink,rp,before,item=self.fixture(d);source=(b/'outcomes'/f'{s}.json').read_bytes();p,h=self.freeze(b,rp)
            r=ar.execute(b,p,h,sink,b/'attempt1.json');self.assertEqual(r['items'][0]['status'],'completed')
            after=json.loads((b/'outcomes'/f'{t}.json').read_bytes());self.assertEqual(after['files'][:2],before['files']);self.assertFalse(after['metadata']['all_attachments_verified']);self.assertEqual((b/'outcomes'/f'{s}.json').read_bytes(),source)
            self.assertTrue(ar.audit(b,p,h,sink,b/'audit.json')['ok']);uploads=sink.uploads
            self.assertEqual(ar.execute(b,p,h,sink,b/'attempt2.json')['items'][0]['status'],'completed');self.assertEqual(sink.uploads,uploads)
            self.assertTrue((b/'audit.md').exists());self.assertTrue((b/'audit.html').exists())

    def test_partial_list_and_partial_aggregate_stay_incomplete(self):
        for missing,fully in [(['Annex I','Annex II'],True),(['Annex in three languages'],False)]:
            with self.subTest(missing=missing),tempfile.TemporaryDirectory() as d:
                b,t,s,sink,rp,before,item=self.fixture(d,missing,fully);p,h=self.freeze(b,rp)
                self.assertEqual(ar.execute(b,p,h,sink,b/'run.json')['items'][0]['status'],'incomplete')
                self.assertTrue(ar.audit(b,p,h,sink,b/'audit.json')['ok'])
                self.assertEqual(json.loads((b/'outcomes'/f'{t}.json').read_bytes())['metadata']['known_missing_attachments'],missing[1:] if fully else missing)

    def test_two_stage_aggregate_gap_uses_old_and_new_attachments(self):
        with tempfile.TemporaryDirectory() as d:
            b,t,s,sink,rp,before,item=self.fixture(d,['Annex I and II'],False);p,h=self.freeze(b,rp)
            self.assertEqual(ar.execute(b,p,h,sink,b/'first.json')['items'][0]['status'],'incomplete')
            first=json.loads((b/'outcomes'/f'{t}.json').read_bytes());first_sha=item['additional_verified_attachments'][0]['receipt']['sha256']
            next_item=copy.deepcopy(item);a=next_item['additional_verified_attachments'][0];r=a['receipt'];data=b'Annex II actual bytes';digest=ar.sha(data)
            r.update(url='https://jdih.kemenkeu.go.id/second.pdf',final_url='https://jdih.kemenkeu.go.id/second.pdf',sha256=digest,size_bytes=len(data))
            r['oss']['raw_key']=sink.prefix+'/raw/'+digest+'.pdf';sink.objects[r['oss']['raw_key']]=data
            envelope=json.loads(sink.objects[item['additional_verified_attachments'][0]['receipt']['oss']['metadata_key']]);envelope['capture']={k:v for k,v in r.items() if k!='oss'};envelope['raw_key']=r['oss']['raw_key']
            raw=ar.encoded(envelope);a['metadata_sha256']=ar.sha(raw);r['oss']['metadata_key']=sink.prefix+'/metadata/archive/'+ar.sha(raw)+'.json';sink.objects[r['oss']['metadata_key']]=raw
            coverage=next_item['resolved_missing_attachments'][0];coverage.update(fully_resolved=True,coverage_note='Independently read both Annex I and Annex II',attachment_sha256s=[first_sha,digest])
            e=json.loads(Path(a['evidence_report_path']).read_bytes())['items'][0];e.update(original_sha256=digest,receipt=copy.deepcopy(r),oss=r['oss'],missing_attachment_coverage=[copy.deepcopy(coverage)])
            ep=b/'second-evidence.json';ep.write_bytes(ar.encoded({'items':[e]}));a.update(evidence_report_path=str(ep),evidence_report_sha256=ar.sha(ep.read_bytes()))
            review=b/'second-review.json';review.write_bytes(ar.encoded(dict(reviewer='independent',review_note='Read both annexes',repairs=[next_item])))
            p2,h2=self.freeze(b,review,'r2');self.assertEqual(ar.execute(b,p2,h2,sink,b/'second.json')['items'][0]['status'],'completed')
            after=json.loads((b/'outcomes'/f'{t}.json').read_bytes());self.assertEqual(after['files'][:3],first['files']);self.assertEqual(len(after['files']),4)
            self.assertTrue(ar.audit(b,p2,h2,sink,b/'audit.json')['ok'])

    def test_reject_bad_review_fields_and_missing_approval(self):
        for scenario in ('no_note','no_complete','bad_mapping','no_report_coverage','wrong_primary','old_file','unverified_primary'):
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as d:
                b,t,s,sink,rp,before,item=self.fixture(d);review=json.loads(rp.read_bytes());i=review['repairs'][0]
                if scenario=='no_note':i['resolved_missing_attachments'][0]['coverage_note']=''
                if scenario=='no_complete':i['resolved_missing_attachments'][0].pop('fully_resolved')
                if scenario=='bad_mapping':i['resolved_missing_attachments'][0]['description']='not known'
                if scenario=='wrong_primary':i['approved_primary_sha256s']=['f'*64]
                if scenario=='old_file':i['additional_verified_attachments'][0]['receipt']['sha256']=before['files'][1]['sha256']
                if scenario=='no_report_coverage':
                    ep=Path(i['additional_verified_attachments'][0]['evidence_report_path']);e=json.loads(ep.read_bytes());e['items'][0].pop('missing_attachment_coverage');ep.write_bytes(ar.encoded(e));i['additional_verified_attachments'][0]['evidence_report_sha256']=ar.sha(ep.read_bytes())
                if scenario=='unverified_primary':
                    before['recovery']['evidence']['primary_text_identity_verified']=False;(b/'outcomes'/f'{t}.json').write_bytes(ar.encoded(before))
                rp.write_bytes(ar.encoded(review))
                with self.assertRaises(ValueError):self.freeze(b,rp)

    def test_freeze_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            b,t,s,sink,rp,before,item=self.fixture(d);p,h=self.freeze(b,rp)
            with self.assertRaises(ValueError):self.freeze(b,rp)
            self.assertEqual(ar.sha(p.read_bytes()),h)

    def test_actual_get_corruption_and_metadata_upload_failure_leave_state(self):
        for scenario in ('primary','old_attachment','new_attachment','archive_metadata','upload'):
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as d:
                b,t,s,sink,rp,before,item=self.fixture(d);p,h=self.freeze(b,rp);old=(b/'outcomes'/f'{t}.json').read_bytes()
                if scenario=='upload':sink.fail=True
                else:
                    a=item['additional_verified_attachments'][0]
                    key={'primary':before['files'][0]['oss']['raw_key'],'old_attachment':before['files'][1]['oss']['raw_key'],'new_attachment':a['receipt']['oss']['raw_key'],'archive_metadata':a['receipt']['oss']['metadata_key']}[scenario];sink.objects[key]=b'corrupt'
                self.assertEqual(ar.execute(b,p,h,sink,b/'run.json')['items'][0]['status'],'failed');self.assertEqual((b/'outcomes'/f'{t}.json').read_bytes(),old)

    def test_audit_detects_remote_and_queue_corruption_without_cache(self):
        for scenario in ('remote_body','remote_detail','remote_detail_semantics','queue_files','queue_bytes','record','objects'):
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as d:
                b,t,s,sink,rp,before,item=self.fixture(d);p,h=self.freeze(b,rp);ar.execute(b,p,h,sink,b/'run.json')
                after=json.loads((b/'outcomes'/f'{t}.json').read_bytes())
                if scenario=='remote_body':sink.objects[before['files'][0]['oss']['raw_key']]=b'corrupt'
                elif scenario=='remote_detail':sink.objects[after['oss_metadata']['metadata_key']]=b'corrupt'
                elif scenario=='remote_detail_semantics':
                    envelope=json.loads(sink.objects[after['oss_metadata']['metadata_key']]);envelope['document']['metadata']['known_missing_attachments']=['not actually resolved'];raw=ar.encoded(envelope);digest=ar.sha(raw);key=sink.prefix+'/metadata/changed/'+digest+'.json';sink.objects[key]=raw
                    after['oss_metadata'].update(metadata_key=key,sha256=digest,size_bytes=len(raw));(b/'outcomes'/f'{t}.json').write_bytes(ar.encoded(after))
                else:
                    with sqlite3.connect(b/'queue.sqlite3') as db:
                        if scenario=='queue_files':db.execute('UPDATE jobs SET files=99 WHERE id=?',(t,))
                        if scenario=='queue_bytes':db.execute('UPDATE jobs SET bytes=99 WHERE id=?',(t,))
                        if scenario=='record':db.execute('UPDATE jobs SET record=? WHERE id=?',('{"changed":true}',t))
                        if scenario=='objects':db.execute('UPDATE objects SET bytes=99')
                self.assertFalse(ar.audit(b,p,h,sink,b/'audit.json')['ok'])

    def test_interruption_after_outcome_write_before_sqlite_commit_recovers(self):
        with tempfile.TemporaryDirectory() as d:
            b,t,s,sink,rp,before,item=self.fixture(d);p,h=self.freeze(b,rp);real=ar.atomic
            def crash(path,raw):real(path,raw);raise KeyboardInterrupt('simulated process death')
            with patch.object(ar,'atomic',side_effect=crash),self.assertRaises(KeyboardInterrupt):ar.execute(b,p,h,sink,b/'run.json')
            with sqlite3.connect(b/'queue.sqlite3') as db:self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?',(t,)).fetchone(),('incomplete',))
            self.assertEqual(json.loads((b/'outcomes'/f'{t}.json').read_bytes())['status'],'completed')
            uploads=sink.uploads
            self.assertEqual(ar.execute(b,p,h,sink,b/'retry.json')['items'][0]['status'],'completed');self.assertEqual(sink.uploads,uploads)
            self.assertTrue(ar.audit(b,p,h,sink,b/'audit.json')['ok'])

    def test_plan_review_and_evidence_tampering_rejected(self):
        for scenario in ('review','plan','evidence','source'):
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as d:
                b,t,s,sink,rp,before,item=self.fixture(d);p,h=self.freeze(b,rp)
                if scenario=='review':rp.write_bytes(rp.read_bytes()+b' ')
                if scenario=='plan':
                    plan=json.loads(p.read_bytes());plan['repairs'][0]['item']['resolved_missing_attachments'][0]['fully_resolved']=False;p.write_bytes(ar.encoded(plan));h=ar.sha(p.read_bytes())
                if scenario=='evidence':Path(item['additional_verified_attachments'][0]['evidence_report_path']).write_bytes(b'{}')
                if scenario=='source':(b/'outcomes'/f'{s}.json').write_bytes(b'{}')
                try:r=ar.execute(b,p,h,sink,b/'run.json')
                except ValueError:continue
                self.assertEqual(r['items'][0]['status'],'failed')

    def test_sdk_failure_recorded_and_redacted(self):
        class SDKError(Exception):pass
        with tempfile.TemporaryDirectory() as d:
            b,t,s,sink,rp,before,item=self.fixture(d);p,h=self.freeze(b,rp)
            with patch.object(sink,'get_object',side_effect=SDKError('secret access token')):
                r=ar.execute(b,p,h,sink,b/'run.json');self.assertEqual(r['items'][0]['status'],'failed')
                self.assertNotIn('secret',r['items'][0]['reason'])
                self.assertFalse(ar.audit(b,p,h,sink,b/'audit.json')['ok'])

    def test_lock_and_duplicate_attempt_refused(self):
        with tempfile.TemporaryDirectory() as d:
            b,t,s,sink,rp,before,item=self.fixture(d);p,h=self.freeze(b,rp)
            with ar.FileLock(b/'.lock'),self.assertRaises(ValueError):ar.execute(b,p,h,sink,b/'run.json')
            ar.execute(b,p,h,sink,b/'run.json')
            with self.assertRaises(ValueError):ar.execute(b,p,h,sink,b/'run.json')


if __name__=='__main__':unittest.main()
