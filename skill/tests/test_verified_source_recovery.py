import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from law_crawler.verified_source_recovery import apply_verified_recovery
from law_crawler.recovery_validation import _item


class Sink:
    fail = False
    def verify_original(self, receipt):
        return dict(status='reused_verified', raw_key='ods/Indonesia/raw/' + 'c'*64 + '.pdf')
    def put_metadata(self, *args, **kwargs):
        return dict(status='failed' if self.fail else 'uploaded_verified')
    def flush_cache(self):
        pass


class RecoveryTests(unittest.TestCase):
    def test_commit_only_after_metadata(self):
        self.run_recovery(False)

    def test_known_missing_attachment_stays_incomplete_after_metadata_and_replay(self):
        self.run_recovery(True)

    def run_recovery(self, missing):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'batch'; root.mkdir(); (root/'outcomes').mkdir()
            target, source = 'a'*64, 'b'*64
            db = sqlite3.connect(root/'queue.sqlite3')
            db.executescript('CREATE TABLE jobs(id TEXT,status TEXT,reason TEXT,files INT,bytes INT,updated_at TEXT,record TEXT);CREATE TABLE objects(sha TEXT PRIMARY KEY,bytes INT,key TEXT);')
            db.executemany('INSERT INTO jobs(id,status) VALUES(?,?)', [(target,'no_original'),(source,'completed')]);db.execute("UPDATE jobs SET record='{}'");db.commit()
            (root/'outcomes'/(target+'.json')).write_text(json.dumps(dict(status='no_original',files=[])))
            (root/'outcomes'/(source+'.json')).write_text(json.dumps(dict(status='completed',oss_metadata={'status':'uploaded_verified'}, metadata={'file_roles':{'https://official/file.pdf':{'role':'regulation_body'}}},files=[dict(url='https://official/file.pdf',sha256='c'*64,size_bytes=3,oss={'status':'uploaded_verified','raw_key':'ods/Indonesia/raw/'+'c'*64+'.pdf'})])))
            repairs=[dict(target_id=target,source_id=source,evidence={'identity':'test'},target_record_sha256=hashlib.sha256(b'{}').hexdigest(),source_record_sha256=hashlib.sha256(b'{}').hexdigest(),target_outcome_sha256=hashlib.sha256((root/'outcomes'/(target+'.json')).read_bytes()).hexdigest(),source_outcome_sha256=hashlib.sha256((root/'outcomes'/(source+'.json')).read_bytes()).hexdigest())]
            sink=Sink();sink.fail=True
            if missing:
                repairs[0]['evidence'].update(primary_text_identity_verified=True,
                    known_missing_attachments=['Official edition omits English treaty annex'])
                for invalid in (None, [], 'annex', [False], [' ']):
                    bad = dict(repairs[0], evidence=dict(repairs[0]['evidence'], known_missing_attachments=invalid))
                    with self.assertRaises(ValueError):
                        _item(bad)
                with self.assertRaises(ValueError):
                    _item(dict(repairs[0], evidence=dict(repairs[0]['evidence'], primary_text_identity_verified=False)))
            result=apply_verified_recovery(root,repairs,sink,[target],'test')
            self.assertEqual(result[0]['status'],'metadata_upload_failed')
            self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?',(target,)).fetchone()[0],'no_original')
            sink.fail=False
            excluded=root/'recovery'/'invalid-source-files.json'
            excluded.write_text(json.dumps([{'source_id':source,'sha256':'c'*64}]))
            result=apply_verified_recovery(root,repairs,sink,[target],'excluded')
            self.assertEqual(result[0]['status'],'no_verified_primary')
            self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?',(target,)).fetchone()[0],'no_original')
            excluded.write_text('[]')
            result=apply_verified_recovery(root,repairs,sink,[target],'test')
            expected = 'incomplete' if missing else 'completed'
            self.assertEqual(result[0]['status'],expected)
            self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?',(target,)).fetchone()[0],expected)
            outcome=json.loads((root/'outcomes'/(target+'.json')).read_text())
            self.assertTrue(outcome['metadata']['original_platform_file_missing'])
            if missing:
                self.assertFalse(outcome['metadata']['primary_original_missing'])
                self.assertEqual(outcome['metadata']['known_missing_attachments'],repairs[0]['evidence']['known_missing_attachments'])
            self.assertTrue((root/'recovery/test/outcomes-before'/(target+'.json')).exists())
            self.assertEqual(apply_verified_recovery(root,repairs,sink,[target],'test')[0]['status'],
                             'skipped_status_changed' if missing else 'already_completed')
            db.close()


if __name__ == '__main__':
    unittest.main()
