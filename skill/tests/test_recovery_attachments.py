import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from law_crawler.verified_source_recovery import apply_verified_recovery

sha=lambda b:hashlib.sha256(b).hexdigest()

class Sink:
    fail_metadata=False
    fail_attachment=False
    def verify_original(self,r):
        return {'status':'failed' if self.fail_attachment and r['sha256']=='d'*64 else 'reused_verified','raw_key':r['oss_raw_key'],'sha256':r['sha256'],'size_bytes':r['size_bytes']}
    def read_verified_archive_metadata(self,k,h):
        if sha(json.dumps(self.envelope).encode())!=h:raise ValueError('bad envelope hash')
        return self.envelope
    def put_metadata(self,key,outcome,**kw):
        self.saved=copy.deepcopy(outcome)
        return {'status':'failed' if self.fail_metadata else 'uploaded_verified'}
    def flush_cache(self):pass

class AttachmentRecoveryTests(unittest.TestCase):
    def fixture(self,folder):
        p=Path(folder);(p/'outcomes').mkdir();t,s='a'*64,'b'*64
        primary={'url':'https://jdih.kemenkeu.go.id/body.htm','sha256':'c'*64,'size_bytes':10,'oss':{'status':'uploaded_verified','raw_key':'ods/Indonesia/raw/'+'c'*64+'.html'}}
        r={'url':'https://jdih.kemenkeu.go.id/annex.htm','final_url':'https://jdih.kemenkeu.go.id/annex.htm','sha256':'d'*64,'size_bytes':20,'status_code':200,'complete':True,'capture_status':'saved'}
        src={'target_id':t,'parent_source_id':s,'parent_original_sha256':'c'*64,'role':'attachment','attachment_kind':'annex'}
        sink=Sink();sink.envelope={'capture':copy.deepcopy(r),'source':src,'raw_key':'ods/Indonesia/raw/'+'d'*64+'.html'};mh=sha(json.dumps(sink.envelope).encode())
        r['oss']={'status':'uploaded_verified','raw_key':sink.envelope['raw_key'],'metadata_key':'ods/Indonesia/metadata/test/'+mh+'.json'}
        evidence={**src,'receipt':copy.deepcopy(r),'oss':r['oss'],'original_sha256':'d'*64,'full_text_sha256':'e'*64,'attachment_identity_approved':True}
        report=p/'evidence.json';report.write_text(json.dumps({'items':[evidence]}))
        target={'status':'no_original','files':[]};source={'status':'completed','platform':'kemenkeu.go.id','detail_url':'https://jdih.kemenkeu.go.id/dok/test','oss_metadata':{'status':'uploaded_verified'},'files':[primary],'metadata':{'file_roles':{primary['url']:{'role':'regulation_body'}}}}
        for key,o in [(t,target),(s,source)]:(p/'outcomes'/f'{key}.json').write_text(json.dumps(o))
        db=sqlite3.connect(p/'queue.sqlite3');db.executescript('CREATE TABLE jobs(id TEXT,status TEXT,reason TEXT,files INT,bytes INT,updated_at TEXT,record TEXT);CREATE TABLE objects(sha TEXT PRIMARY KEY,bytes INT,key TEXT);');db.executemany('INSERT INTO jobs(id,status,record) VALUES(?,?,?)',[(t,'no_original','{}'),(s,'completed','{}')]);db.commit();db.close()
        item={'target_id':t,'source_id':s,'target_record_sha256':sha(b'{}'),'source_record_sha256':sha(b'{}'),'target_outcome_sha256':sha((p/'outcomes'/f'{t}.json').read_bytes()),'source_outcome_sha256':sha((p/'outcomes'/f'{s}.json').read_bytes()),'evidence':{'identity':'reviewed'},'approved_primary_sha256s':['c'*64],'additional_verified_attachments':[{'role':'attachment','attachment_kind':'annex','parent_primary_sha256':'c'*64,'receipt':r,'evidence_report_path':str(report),'evidence_report_sha256':sha(report.read_bytes()),'full_text_sha256':'e'*64,'metadata_sha256':mh}]}
        return p,t,s,item,sink
    def test_success_combines_roles_preserves_source_and_replay(self):
        with tempfile.TemporaryDirectory() as d:
            p,t,s,item,sink=self.fixture(d);old=(p/'outcomes'/f'{s}.json').read_bytes()
            self.assertEqual(apply_verified_recovery(p,[item],sink,[t],'test')[0]['files'],2)
            self.assertEqual((p/'outcomes'/f'{s}.json').read_bytes(),old)
            self.assertEqual({r['role'] for r in sink.saved['metadata']['file_roles'].values()},{'attachment','regulation_body'})
            self.assertEqual(apply_verified_recovery(p,[item],sink,[t],'again')[0]['status'],'already_completed')
    def test_failures_leave_target_and_source_unchanged(self):
        for scenario in ['report','parent','role','host','envelope','original','metadata','target','duplicate','alias','excluded','host_bound']:
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as d:
                p,t,s,item,sink=self.fixture(d);old={k:(p/'outcomes'/f'{k}.json').read_bytes() for k in [t,s]};a=item['additional_verified_attachments'][0]
                if scenario=='report':a['evidence_report_sha256']='f'*64
                if scenario=='parent':a['parent_primary_sha256']='f'*64
                if scenario=='role':a['role']='regulation_body'
                if scenario=='host':a['receipt']['final_url']='https://evil.example/a'
                if scenario=='envelope':sink.envelope['source']['target_id']='f'*64
                if scenario=='original':sink.fail_attachment=True
                if scenario=='metadata':sink.fail_metadata=True
                if scenario=='target':item['target_outcome_sha256']='f'*64
                if scenario=='alias':a['receipt']['raw_key']='ods/Indonesia/raw/'+'f'*64+'.html'
                if scenario=='excluded':
                    (p/'recovery').mkdir();(p/'recovery/invalid-source-files.json').write_text(json.dumps([{'source_id':s,'sha256':'d'*64}]))
                if scenario=='host_bound':
                    a['receipt']['url']=a['receipt']['final_url']='https://evil.example/a'
                    sink.envelope['capture'].update(url='https://evil.example/a',final_url='https://evil.example/a')
                    ep=Path(a['evidence_report_path']);ev=json.loads(ep.read_text());ev['items'][0]['receipt']=copy.deepcopy(a['receipt']);ep.write_text(json.dumps(ev));a['evidence_report_sha256']=sha(ep.read_bytes());a['metadata_sha256']=sha(json.dumps(sink.envelope).encode())
                if scenario=='duplicate':item['additional_verified_attachments']*=2
                try:apply_verified_recovery(p,[item],sink,[t],'test')
                except ValueError:pass
                self.assertEqual({k:(p/'outcomes'/f'{k}.json').read_bytes() for k in [t,s]},old)
                db=sqlite3.connect(p/'queue.sqlite3');self.assertEqual(db.execute('select status from jobs where id=?',(t,)).fetchone()[0],'no_original');db.close()

class ArchiveMetadataTests(unittest.TestCase):
    def test_real_reader_rejects_corrupt_bucket_key_and_oversize_and_closes(self):
        from law_crawler.oss_sink import OSSSink
        class Response:
            def __init__(self,data):self.data=data;self.closed=False
            def read(self,n):return self.data[:n]
            def close(self):self.closed=True
        class Bucket:
            def get_object(self,key):return self.response
        with tempfile.TemporaryDirectory() as folder:
            bucket=Bucket();sink=OSSSink(credentials=None,cache_path=Path(folder)/'cache',bucket_factory=lambda:bucket,bucket='law-crawler',endpoint='https://oss-ap-southeast-1-internal.aliyuncs.com',region='ap-southeast-1',prefix='ods/Indonesia')
            for mode in ('ok','digest','bucket','oversize','key'):
                data=json.dumps({'bucket':'other' if mode=='bucket' else 'law-crawler'}).encode()
                if mode=='oversize':data=b'x'*(2*1024*1024+1)
                digest=sha(data);key='ods/Indonesia/metadata/batch/'+digest+'.json'
                if mode=='digest':data+=b' '
                if mode=='key':key='outside/'+digest+'.json'
                bucket.response=Response(data)
                if mode=='ok':self.assertEqual(sink.read_verified_archive_metadata(key,digest)['bucket'],'law-crawler')
                else:
                    with self.assertRaises(ValueError):sink.read_verified_archive_metadata(key,digest)
                self.assertEqual(bucket.response.closed,mode!='key')

    def test_parent_must_be_in_actual_verified_primary_set(self):
        from law_crawler.recovery_attachments import verified_attachments
        with tempfile.TemporaryDirectory() as folder:
            p,t,s,item,sink=AttachmentRecoveryTests().fixture(folder)
            original=json.loads((p/'outcomes'/f'{s}.json').read_text())
            with self.assertRaises(ValueError):verified_attachments(item,original,[],sink,set())
