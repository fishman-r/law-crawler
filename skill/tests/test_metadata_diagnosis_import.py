import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from law_crawler.metadata_diagnosis_import import import_diagnoses
from law_crawler.file_lock import FileLock, FileLockBusy


class Sink:
    fail=False
    calls=0
    def put_metadata(self,key,value,**kwargs):
        self.calls+=1
        assert value['status']=='no_original'
        return {'status':'failed' if self.fail else 'uploaded_verified'}
    def flush_cache(self):pass


class DiagnosisTests(unittest.TestCase):
    def setup_batch(self, folder):
        root=Path(folder);(root/'outcomes').mkdir();key='a'*64
        db=sqlite3.connect(root/'queue.sqlite3')
        db.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,status TEXT,record TEXT,reason TEXT,updated_at TEXT)')
        db.execute('INSERT INTO jobs VALUES(?,?,?,?,?)',(key,'no_original','{}','old',None));db.commit();db.close()
        (root/'outcomes'/(key+'.json')).write_text(json.dumps({'status':'no_original','files':[],'metadata':{}}))
        manifest=root/'manifest.json';manifest.write_text(json.dumps({'batch_id':root.name,'count':1,'jobs':[[key,'no_original']]}))
        patch={'target_id':key,'source_record_sha256':hashlib.sha256(b'{}').hexdigest(),'metadata_patch':{'no_original_reason':'官方书目未提供可下载全文；不等于0条法规','source_availability':{'status':'full_text_not_provided_in_catalogue'}}}
        patches=root/'patches.jsonl';patches.write_text(json.dumps(patch)+'\n')
        return root,key,manifest,patches,patch
    def run_import(self, root, manifest, patches, sink):
        return import_diagnoses(root,patches,manifest,hashlib.sha256(manifest.read_bytes()).hexdigest(),sink,'diagnosis')
    def test_success_retains_no_original_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root,key,m,p,_=self.setup_batch(folder);sink=Sink()
            report=self.run_import(root,m,p,sink)
            self.assertEqual(report['counts'],{'diagnosis_saved':1});self.assertEqual(report['download_completed_added'],0)
            outcome=json.loads((root/'outcomes'/(key+'.json')).read_text())
            self.assertEqual(outcome['status'],'no_original');self.assertEqual(outcome['files'],[])
            self.assertTrue((root/'recovery/diagnosis/outcomes-before'/(key+'.json')).exists())
            self.assertEqual(self.run_import(root,m,p,sink)['counts'],{'already_applied':1});self.assertEqual(sink.calls,1)
    def test_failed_upload_preserves_local_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root,key,m,p,_=self.setup_batch(folder);sink=Sink();sink.fail=True
            path=root/'outcomes'/(key+'.json');before=path.read_bytes()
            self.assertEqual(self.run_import(root,m,p,sink)['counts'],{'metadata_upload_failed':1})
            self.assertEqual(path.read_bytes(),before)
            db=sqlite3.connect(root/'queue.sqlite3');self.assertEqual(db.execute('select status,reason from jobs').fetchone(),('no_original','old'));db.close()
    def test_record_sha_change_rejected_without_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            root,key,m,p,patch=self.setup_batch(folder);patch['source_record_sha256']='b'*64;p.write_text(json.dumps(patch));sink=Sink()
            self.assertEqual(self.run_import(root,m,p,sink)['counts'],{'failed':1});self.assertEqual(sink.calls,0)
    def test_completed_task_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            root,key,m,p,_=self.setup_batch(folder);db=sqlite3.connect(root/'queue.sqlite3');db.execute("UPDATE jobs SET status='completed'");db.commit();db.close();sink=Sink()
            self.assertEqual(self.run_import(root,m,p,sink)['counts'],{'skipped_status_changed':1});self.assertEqual(sink.calls,0)
    def test_lock_prevents_parallel_writer(self):
        with tempfile.TemporaryDirectory() as folder:
            root,key,m,p,_=self.setup_batch(folder)
            with FileLock(root/'.lock'):
                with self.assertRaises(FileLockBusy):self.run_import(root,m,p,Sink())
    def test_out_of_scope_duplicate_and_metadata_injection_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root,key,m,p,patch=self.setup_batch(folder)
            p.write_text(json.dumps(patch)+'\n'+json.dumps(patch))
            with self.assertRaises(ValueError):self.run_import(root,m,p,Sink())
            patch['target_id']='b'*64;p.write_text(json.dumps(patch))
            with self.assertRaises(ValueError):self.run_import(root,m,p,Sink())
            patch['target_id']=key;patch['metadata_patch']['status']='completed';p.write_text(json.dumps(patch))
            with self.assertRaises(ValueError):self.run_import(root,m,p,Sink())
