"""Independent recovery integrity regressions; use isolated temporary batch and fake OSS."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from law_crawler.recover_missing_original import direct_recovery
from law_crawler.raw_capture import file_sha256


class FakeSink:
    fail = False
    def put_metadata(self, *args, **kwargs):
        return {'status': 'failed' if self.fail else 'uploaded_verified'}


class MissingRecoveryTests(unittest.TestCase):
    def setup_batch(self, folder, role):
        root = Path(folder); (root/'outcomes').mkdir()
        key = 'a'*64
        db = sqlite3.connect(root/'queue.sqlite3')
        db.executescript('CREATE TABLE jobs(id TEXT PRIMARY KEY,status TEXT,reason TEXT,files INT DEFAULT 0,bytes INT DEFAULT 0,updated_at TEXT,record TEXT);CREATE TABLE objects(sha TEXT PRIMARY KEY,bytes INT,key TEXT);')
        db.execute('INSERT INTO jobs(id,status) VALUES(?,?)',(key,'no_original'));db.execute("UPDATE jobs SET record='{}'");db.commit();db.close()
        (root/'outcomes'/(key+'.json')).write_text(json.dumps({'detail_key':key,'detail_url':'https://jdih.kemenkeu.go.id/dok/test','platform':'kemenkeu.go.id','metadata':{},'files':[],'status':'no_original'}))
        snapshot = root/'source.html'; snapshot.write_text('<a href="/a.pdf">File</a>')
        item={'target_id':key,'evidence':{'original_detail_snapshot':str(snapshot),'original_detail_sha256':file_sha256(snapshot)},'files':[{'url':'https://jdih.kemenkeu.go.id/a.pdf','role':role}]}
        item.update(target_record_sha256=hashlib.sha256(b'{}').hexdigest(),target_outcome_sha256=file_sha256(root/'outcomes'/(key+'.json')))
        sink=FakeSink()
        receipt={'url':item['files'][0]['url'],'capture_status':'saved','complete':True,'status_code':200,'sha256':'c'*64,'size_bytes':99,'oss':{'status':'uploaded_verified','raw_key':'ods/Indonesia/raw/a.pdf'}}
        runner=SimpleNamespace(out=root,sink=sink,file=lambda *args:receipt)
        return root,key,item,runner

    def test_attachment_only_never_completes(self):
        with tempfile.TemporaryDirectory() as folder:
            root,key,item,runner=self.setup_batch(folder,'attachment')
            try:direct_recovery(runner,item,'test',{key})
            except ValueError:pass
            db=sqlite3.connect(root/'queue.sqlite3')
            self.assertNotEqual(db.execute('select status from jobs').fetchone()[0],'completed');db.close()

    def test_metadata_failure_preserves_original_outcome_and_status(self):
        with tempfile.TemporaryDirectory() as folder:
            root,key,item,runner=self.setup_batch(folder,'regulation_body');runner.sink.fail=True
            original=(root/'outcomes'/(key+'.json')).read_bytes()
            direct_recovery(runner,item,'test',{key})
            self.assertEqual((root/'outcomes'/(key+'.json')).read_bytes(),original)
            db=sqlite3.connect(root/'queue.sqlite3');self.assertEqual(db.execute('select status from jobs').fetchone()[0],'no_original');db.close()

    def test_direct_success_updates_file_and_byte_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            root,key,item,runner=self.setup_batch(folder,'regulation_body')
            direct_recovery(runner,item,'test',{key})
            db=sqlite3.connect(root/'queue.sqlite3')
            self.assertEqual(db.execute('select files,bytes from jobs').fetchone(),(1,99));db.close()

    def test_direct_known_missing_attachment_requires_verified_original_and_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root,key,item,runner=self.setup_batch(folder,'regulation_body')
            item['evidence'].update(primary_text_identity_verified=True,known_missing_attachments=['Treaty annex omitted'])
            original=(root/'outcomes'/(key+'.json')).read_bytes()
            file=runner.file
            runner.file=lambda *args:dict(file(),oss={'status':'failed'})
            self.assertEqual(direct_recovery(runner,item,'failed-original',{key})['status'],'original_verification_failed')
            self.assertEqual((root/'outcomes'/(key+'.json')).read_bytes(),original)
            runner.file=file;runner.sink.fail=True
            self.assertEqual(direct_recovery(runner,item,'failed-metadata',{key})['status'],'metadata_upload_failed')
            self.assertEqual((root/'outcomes'/(key+'.json')).read_bytes(),original)
            runner.sink.fail=False
            self.assertEqual(direct_recovery(runner,item,'partial',{key})['status'],'incomplete')
            outcome=json.loads((root/'outcomes'/(key+'.json')).read_text())
            self.assertEqual(outcome['metadata']['known_missing_attachments'],['Treaty annex omitted'])
            self.assertFalse(outcome['metadata']['primary_original_missing'])
            self.assertEqual(direct_recovery(runner,item,'partial',{key})['status'],'skipped_status_changed')

class RecoveryPlanValidationTests(unittest.TestCase):
    def test_prior_missing_attachment_cannot_be_silently_dropped(self):
        from law_crawler.recovery_validation import validate_repair_sources
        with tempfile.TemporaryDirectory() as folder:
            root,key,item,runner=MissingRecoveryTests().setup_batch(folder,'regulation_body')
            path=root/'outcomes'/(key+'.json');value=json.loads(path.read_text())
            value['metadata']['known_missing_attachments']=['Treaty annex omitted']
            path.write_text(json.dumps(value));item['target_outcome_sha256']=file_sha256(path)
            with self.assertRaises(ValueError):validate_repair_sources(root,item)
            item['evidence'].update(primary_text_identity_verified=True,known_missing_attachments=['Treaty annex omitted'])
            validate_repair_sources(root,item)

    def test_manifest_tamper_duplicate_and_wrong_batch_rejected(self):
        from law_crawler.recovery_validation import validate_plan
        with tempfile.TemporaryDirectory() as folder:
            root,key,item,runner=MissingRecoveryTests().setup_batch(folder,'regulation_body')
            manifest=root/'manifest.json';manifest.write_text(json.dumps({'batch_id':root.name,'jobs':[[key,'no_original']]}))
            plan={'batch_id':root.name,'run_id':'test','target_manifest_sha256':file_sha256(manifest),'repairs':[item]}
            self.assertEqual(validate_plan(root,plan,manifest),{key})
            with self.assertRaises(ValueError):validate_plan(root,dict(plan,repairs=[item,item]),manifest)
            with self.assertRaises(ValueError):validate_plan(root,dict(plan,batch_id='wrong'),manifest)
            manifest.write_text('{}')
            with self.assertRaises(ValueError):validate_plan(root,plan,manifest)

    def test_changed_outcome_or_record_rejected(self):
        from law_crawler.recovery_validation import validate_repair_sources
        with tempfile.TemporaryDirectory() as folder:
            root,key,item,runner=MissingRecoveryTests().setup_batch(folder,'regulation_body')
            validate_repair_sources(root,item)
            path=root/'outcomes'/(key+'.json');before=path.read_bytes();path.write_text('{}')
            with self.assertRaises(ValueError):validate_repair_sources(root,item)
            path.write_bytes(before)
            db=sqlite3.connect(root/'queue.sqlite3');db.execute("UPDATE jobs SET record='changed'");db.commit();db.close()
            with self.assertRaises(ValueError):validate_repair_sources(root,item)
