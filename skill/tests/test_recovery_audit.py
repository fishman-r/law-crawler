"""GET-only audit tests: no live credentials, uploads or queue changes."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from law_crawler.recovery_audit import audit_recovery, _read_object


def digest(raw): return hashlib.sha256(raw).hexdigest()
def encoded(value): return json.dumps(value, sort_keys=True).encode()


class Sink:
    prefix = 'ods/Indonesia'
    bucket_name = 'law-crawler'
    def __init__(self): self.objects = {}; self.reads = []; self.streams = []
    def _bucket(self): return self
    def get_object(self, key):
        self.reads.append(key); stream = io.BytesIO(self.objects[key]); self.streams.append(stream); return stream


class RecoveryAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.batch = self.root/'batch1'; self.batch.mkdir()
        (self.batch/'outcomes').mkdir(); self.sink = Sink()
        self.target, self.source = 'a'*64, 'b'*64
        self.record = '{}'; self.before = encoded({'status': 'no_original'})
        self.source_raw = encoded({'status': 'completed'})
        (self.batch/'outcomes'/f'{self.source}.json').write_bytes(self.source_raw)
        self.original = b'%PDF-original'; self.sha = digest(self.original)
        key = f'{self.sink.prefix}/raw/{self.sha}.pdf'; self.sink.objects[key] = self.original
        self.source_file={'url':'https://official/body.pdf','sha256':self.sha,'size_bytes':len(self.original),'complete':True,'status_code':200,'capture_status':'saved','oss':{'raw_key':key}}
        self.source_raw=encoded({'status':'completed','files':[self.source_file],'metadata':{'file_roles':{'https://official/body.pdf':{'role':'regulation_body'}}}})
        (self.batch/'outcomes'/f'{self.source}.json').write_bytes(self.source_raw)
        self.item = dict(target_id=self.target, source_id=self.source,
            target_record_sha256=digest(self.record.encode()), target_outcome_sha256=digest(self.before),
            source_record_sha256=digest(self.record.encode()), source_outcome_sha256=digest(self.source_raw),
            approved_primary_sha256s=[self.sha], evidence={'review_status': 'approved'})
        self.manifest = self.root/'manifest.json'; self.manifest.write_bytes(encoded({'batch_id':'batch1','count':1,'jobs':[[self.target]]}))
        self.plan = self.root/'repair.json'
        self.repair = {'batch_id':'batch1','run_id':'run1','target_manifest_sha256':digest(self.manifest.read_bytes()),'repairs':[self.item]}
        self.results = self.root/'results.json'; self.results.write_bytes(encoded([{'target_id':self.target,'status':'completed'}]))
        run = 'run1-'+self.target[:16]; history = self.batch/'recovery'/run/'outcomes-before'; history.mkdir(parents=True)
        (history/f'{self.target}.json').write_bytes(self.before)
        self.outcome = {'status':'completed','files':[copy.deepcopy(self.source_file)],
            'metadata':{'file_roles':{'https://official/body.pdf':{'role':'regulation_body'}}},
            'recovery':{'run_id':run,'source_detail_key':self.source,'source_outcome_sha256':digest(self.source_raw),'evidence':self.item['evidence']}}
        db=sqlite3.connect(self.batch/'queue.sqlite3')
        db.execute('CREATE TABLE jobs(id TEXT, record TEXT, status TEXT, files INTEGER, bytes INTEGER)')
        db.executemany('INSERT INTO jobs VALUES(?,?,?,?,?)', [(self.target,self.record,'completed',1,len(self.original)),(self.source,self.record,'completed',0,0)])
        db.commit(); db.close(); self.save()

    def save(self):
        review=self.root/'review.json'; reviewed=copy.deepcopy(self.item); reviewed.pop('review_binding',None)
        review.write_bytes(encoded({'repairs':[reviewed]})); binding={'path':str(review),'sha256':digest(review.read_bytes())}
        self.item['review_binding']=binding; self.repair['review_bindings']=[binding]
        self.plan.write_bytes(encoded(self.repair)); self.plan.with_suffix('.sha256').write_text(digest(self.plan.read_bytes()))
        document=copy.deepcopy(self.outcome); document.pop('oss_metadata',None)
        body=encoded({'schema_version':1,'batch_id':'batch1','detail_key':self.target,'document':document})
        sha=digest(body); key=f'{self.sink.prefix}/metadata/batch1/{digest(self.target.encode())}/{sha}.json'
        self.sink.objects[key]=body; self.outcome['oss_metadata']={'metadata_key':key,'sha256':sha,'size_bytes':len(body)}
        (self.batch/'outcomes'/f'{self.target}.json').write_bytes(encoded(self.outcome))

    def audit(self, verifier=None):
        return audit_recovery(batch=self.batch,manifest=self.manifest,plan=self.plan,results=self.results,
            output=self.root/'audit.json',sink=self.sink,evidence_verifier=verifier or (lambda item: {'verified':True}))

    def test_actual_get_and_immutable_reports_no_state_mutation(self):
        before=(self.batch/'queue.sqlite3').read_bytes(); source=(self.batch/'outcomes'/f'{self.source}.json').read_bytes()
        report=self.audit(); self.assertTrue(report['ok'], report.get('errors')); self.assertEqual(len(self.sink.reads),2)
        self.assertTrue(all(x.closed for x in self.sink.streams)); self.assertEqual(before,(self.batch/'queue.sqlite3').read_bytes())
        self.assertEqual(source,(self.batch/'outcomes'/f'{self.source}.json').read_bytes())
        for ext in ('json','md','html'):self.assertTrue((self.root/f'audit.{ext}').exists())
        with self.assertRaises(FileExistsError):self.audit()
        self.assertFalse(report['all_manifest_targets_completed'])

    def test_original_tamper_fails_even_receipt_verified(self):
        self.sink.objects[next(iter(self.sink.objects))]=b'X'*len(self.original)
        self.assertFalse(self.audit()['ok'])

    def test_source_bytes_mutation_fails(self):
        (self.batch/'outcomes'/f'{self.source}.json').write_bytes(self.source_raw+b' ')
        self.assertFalse(self.audit()['ok'])

    def test_extra_original_fails(self):
        self.outcome['files'].append(copy.deepcopy(self.outcome['files'][0]));self.save()
        self.assertFalse(self.audit()['ok'])

    def test_missing_result_fails(self):
        self.results.write_text('[]');self.assertFalse(self.audit()['ok'])

    def test_duplicate_result_fails(self):
        row={'target_id':self.target,'status':'completed'};self.results.write_bytes(encoded([row,row]))
        self.assertFalse(self.audit()['ok'])

    def test_sidecar_mismatch_fails(self):
        self.plan.with_suffix('.sha256').write_text('0'*64);self.assertFalse(self.audit()['ok'])

    def test_changed_evidence_fails_and_does_not_leak(self):
        def fail(item):raise RuntimeError('SECRET_DO_NOT_OUTPUT')
        report=self.audit(fail);self.assertFalse(report['ok']);self.assertNotIn('SECRET_DO_NOT_OUTPUT',(self.root/'audit.json').read_text())

    def test_metadata_document_tamper_with_valid_digest_fails(self):
        receipt=self.outcome['oss_metadata'];body=json.loads(self.sink.objects[receipt['metadata_key']]);body['document']['status']='other'
        raw=encoded(body);sha=digest(raw);key=f'{self.sink.prefix}/metadata/batch1/{digest(self.target.encode())}/{sha}.json'
        self.sink.objects[key]=raw;self.outcome['oss_metadata']={'metadata_key':key,'sha256':sha,'size_bytes':len(raw)}
        (self.batch/'outcomes'/f'{self.target}.json').write_bytes(encoded(self.outcome))
        self.assertFalse(self.audit()['ok'])

    def test_known_missing_requires_incomplete(self):
        self.item['evidence'].update(known_missing_attachments=['annex'],primary_text_identity_verified=True);self.save()
        self.assertFalse(self.audit()['ok'])

    def test_already_completed_still_checks_evidence(self):
        self.results.write_bytes(encoded([{'target_id':self.target,'status':'already_completed'}]))
        self.assertTrue(self.audit()['ok'])

    def test_incomplete_audit_never_counts_completed(self):
        self.item['evidence'].update(known_missing_attachments=['annex'], primary_text_identity_verified=True)
        self.outcome['status']='incomplete'; self.save()
        db=sqlite3.connect(self.batch/'queue.sqlite3');db.execute("UPDATE jobs SET status='incomplete' WHERE id=?",(self.target,));db.commit();db.close()
        self.results.write_bytes(encoded([{'target_id':self.target,'status':'incomplete'}]))
        report=self.audit();self.assertTrue(report['ok'], report.get('errors'));self.assertEqual(report['audited_completed_count'],0);self.assertEqual(report['audited_incomplete_count'],1)

    def test_excluded_source_pair_fails(self):
        (self.batch/'recovery'/'invalid-source-files.json').write_bytes(encoded([{'source_id':self.source,'sha256':self.sha}]))
        self.assertFalse(self.audit()['ok'])

    def test_other_source_same_sha_not_excluded(self):
        (self.batch/'recovery'/'invalid-source-files.json').write_bytes(encoded([{'source_id':'c'*64,'sha256':self.sha}]))
        self.assertTrue(self.audit()['ok'])

    def write_pair_ledger(self, **changes):
        entry=dict(scope='candidate_pair',target_id=self.target,source_id=self.source,
                   sha256=self.sha,reason='Wrong formal type for this target')
        entry.update(changes)
        review=self.batch/'pair-rejection-review.json'
        review.write_bytes(encoded({'rejections':[dict(entry,decision='reject_target_source_sha_pair_only')]}))
        entry.update(review_path=str(review),review_sha256=digest(review.read_bytes()))
        path=self.batch/'recovery'/'rejected-candidate-pairs.json'
        path.write_bytes(encoded({'schema_version':1,'batch_id':self.batch.name,'rejections':[entry]}))
        return path

    def test_exact_candidate_rejection_fails_even_after_completed_execution(self):
        self.write_pair_ledger()
        report=self.audit();self.assertFalse(report['ok'])
        self.assertTrue(any(e['check']=='rejected_candidate_pair' for e in report['errors']))

    def test_rejected_pair_for_another_target_does_not_poison_source(self):
        self.write_pair_ledger(target_id='d'*64)
        self.assertTrue(self.audit()['ok'])

    def test_same_target_with_different_source_rejection_does_not_block_audit(self):
        self.write_pair_ledger(source_id='d'*64)
        self.assertTrue(self.audit()['ok'])

    def test_pair_ledger_added_during_audit_refuses_stale_acceptance(self):
        def mutate(item):
            self.write_pair_ledger()
            return {'files':[]}
        report=self.audit(mutate);self.assertFalse(report['ok'])
        self.assertTrue(any(e['check']=='rejected_pair_ledger_changed_during_read' for e in report['errors']))

    def test_pair_ledger_changed_during_audit_refuses_stale_acceptance(self):
        self.write_pair_ledger(target_id='d'*64)
        def mutate(item):
            self.write_pair_ledger()
            return {'files':[]}
        report=self.audit(mutate);self.assertFalse(report['ok'])
        self.assertTrue(any(e['check'] in ('audit_inputs_changed_during_read','rejected_pair_ledger_changed_during_read') for e in report['errors']))

    def test_pair_ledger_wrong_batch_fails_closed(self):
        path=self.write_pair_ledger(target_id='d'*64)
        value=json.loads(path.read_text());value['batch_id']='other';path.write_bytes(encoded(value))
        self.assertFalse(self.audit()['ok'])

    def test_review_content_changed_fails(self):
        (self.root/'review.json').write_text('{}');self.assertFalse(self.audit()['ok'])

    def test_queue_counter_mismatch_fails(self):
        db=sqlite3.connect(self.batch/'queue.sqlite3');db.execute('UPDATE jobs SET files=7 WHERE id=?',(self.target,));db.commit();db.close()
        self.assertFalse(self.audit()['ok'])

    def test_wrong_primary_role_fails(self):
        self.outcome['metadata']['file_roles']['https://official/body.pdf']['role']='attachment';self.save()
        self.assertFalse(self.audit()['ok'])

    def test_result_envelope_wrong_plan_sha_fails(self):
        self.results.write_bytes(encoded({'plan_sha256':'0'*64,'results':[{'target_id':self.target,'status':'completed'}]}))
        self.assertFalse(self.audit()['ok'])

    def test_readonly_audit_detects_concurrent_outcome_change(self):
        def mutate(item):
            p=self.batch/'outcomes'/f'{self.source}.json';p.write_bytes(self.source_raw+b' ')
        self.assertFalse(self.audit(mutate)['ok'])

    def test_legacy_review_binding_is_verified(self):
        binding=self.item.pop('review_binding');self.repair.pop('review_bindings')
        self.item['evidence']=dict(self.item['evidence'],review_document_path=binding['path'],review_document_sha256=binding['sha256'])
        self.outcome['recovery']['evidence']=self.item['evidence']
        self.plan.write_bytes(encoded(self.repair));self.plan.with_suffix('.sha256').write_text(digest(self.plan.read_bytes()))
        document=copy.deepcopy(self.outcome);document.pop('oss_metadata')
        raw=encoded({'batch_id':'batch1','detail_key':self.target,'document':document});sha=digest(raw)
        key=f'{self.sink.prefix}/metadata/batch1/{digest(self.target.encode())}/{sha}.json';self.sink.objects[key]=raw
        self.outcome['oss_metadata']={'metadata_key':key,'sha256':sha,'size_bytes':len(raw)}
        (self.batch/'outcomes'/f'{self.target}.json').write_bytes(encoded(self.outcome))
        self.assertTrue(self.audit()['ok'])

    def test_conflicting_alias_fails(self):
        self.outcome['files'][0]['oss']['key']='ods/Indonesia/raw/wrong.pdf';self.save()
        self.assertFalse(self.audit()['ok'])

    def test_real_evidence_helper_hashes_retained_text(self):
        text='KEPUTUSAN PRESIDEN NOMOR 1 TAHUN 2000 TENTANG TEST'
        selection=self.root/'selection.json'
        candidate={'target_id':self.target,'source_id':self.source,'sha256':self.sha,'original_verified':True,'raw_key':f'{self.sink.prefix}/raw/{self.sha}.pdf',
                   'target_identity':['keppres','1','2000'],'target_title':'TEST','first_page_text':text,
                   'extracted_first_page_sha256':digest(text.encode())}
        selection.write_bytes(encoded({'items':[candidate]}))
        self.item['evidence']={'review_status':'independent_approved','target_identity':candidate['target_identity'],
            'target_title':'TEST','original_verified':True,'original_sha256':self.sha,'original_key':candidate['raw_key'],'review_note':'Full retained subject and identity independently matched.',
            'selection_path':str(selection),'selection_sha256':digest(selection.read_bytes()),
            'extracted_page_sha256':[digest(text.encode())]}
        self.outcome['recovery']['evidence']=self.item['evidence'];self.save()
        report=audit_recovery(batch=self.batch,manifest=self.manifest,plan=self.plan,results=self.results,
            output=self.root/'audit.json',sink=self.sink)
        self.assertTrue(report['ok'], report.get('errors'))

    def test_not_scheduled_envelope_cannot_pass(self):
        self.results.write_bytes(encoded({'schema_version':1,'kind':'recovery_execution_results',
            'plan_sha256':digest(self.plan.read_bytes()),'batch_id':'batch1','run_id':'run1','attempt_id':'c'*32,
            'results':[{'target_id':self.target,'status':'not_scheduled','reason':'limit'}]}))
        report=self.audit();self.assertFalse(report['ok'])
        self.assertTrue(any(e['check']=='execution_result_status' for e in report['errors']))

    def test_concurrent_queue_commit_rejects_inconsistent_snapshot(self):
        db=sqlite3.connect(self.batch/'queue.sqlite3');db.execute('PRAGMA journal_mode=WAL');db.close()
        def commit(item):
            other=sqlite3.connect(self.batch/'queue.sqlite3')
            other.execute('UPDATE jobs SET bytes=bytes+1 WHERE id=?',(self.source,));other.commit();other.close()
            return {'files':[]}
        report=self.audit(commit);self.assertFalse(report['ok'])
        self.assertTrue(any(e['check']=='queue_changed_during_read' for e in report['errors']))

    def test_incomplete_target_capture_fails_despite_matching_bytes(self):
        self.outcome['files'][0]['complete']=False;self.save()
        report=self.audit();self.assertFalse(report['ok'])
        self.assertTrue(any(e['check']=='target_capture_incomplete' for e in report['errors']))

    def test_http_error_target_capture_fails_despite_matching_bytes(self):
        self.outcome['files'][0]['status_code']=403;self.save()
        self.assertFalse(self.audit()['ok'])

    def test_source_capture_error_fails_even_frozen_current(self):
        source=json.loads(self.source_raw);source['files'][0]['status_code']=500
        self.source_raw=encoded(source);(self.batch/'outcomes'/f'{self.source}.json').write_bytes(self.source_raw)
        self.item['source_outcome_sha256']=digest(self.source_raw)
        self.outcome['recovery']['source_outcome_sha256']=digest(self.source_raw);self.save()
        report=self.audit();self.assertFalse(report['ok'])
        self.assertTrue(any(e['check']=='source_capture_incomplete' for e in report['errors']))

    def test_manifest_status_counts_and_missing_ids(self):
        manifest={'batch_id':'batch1','count':2,'jobs':[[self.target],['e'*64]]}
        self.manifest.write_bytes(encoded(manifest));self.repair['target_manifest_sha256']=digest(self.manifest.read_bytes());self.save()
        report=self.audit();self.assertFalse(report['ok'])
        self.assertEqual(report['manifest_status_counts'],{'completed':1})
        self.assertEqual(report['manifest_missing_target_ids'],['e'*64])

    def test_html_tables_escape_untrusted_state(self):
        db=sqlite3.connect(self.batch/'queue.sqlite3');db.execute('UPDATE jobs SET status=? WHERE id=?',('<script>alert(1)</script>',self.source));db.commit();db.close()
        report=self.audit();self.assertFalse(report['ok'])
        rendered=(self.root/'audit.html').read_text()
        self.assertIn('<table>',rendered);self.assertNotIn('<script>',rendered)
        self.assertIn('&lt;script&gt;',rendered)

    def test_stream_overrun_closes(self):
        self.sink.objects['ods/Indonesia/raw/test']=b'ab'
        with self.assertRaises(ValueError):_read_object(self.sink,'ods/Indonesia/raw/test',digest(b'a'),1)
        self.assertTrue(self.sink.streams[-1].closed)



class LegacyCompoundReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.target='a'*64;self.source='b'*64;self.primary='c'*64;self.extra='d'*64
        self.identity={'target_title':'Title','observed_header':'HEADER','decision':'approved_identity','reason':'Specific spelling-only adjudication.'}
        self.text='Full retained primary text';self.hash=digest(self.text.encode());self.key='ods/Indonesia/raw/'+self.primary+'.html'
        result={'target_id':self.target,'source_id':self.source,'sha256':self.primary,'raw_key':self.key,'original_verified':True,
            'target_identity':['perpres','1','2000'],'target_title':'Title','body':{'identity':['perpres','1','2000']},
            'page_texts':[self.text],'extracted_page_sha256':[self.hash],'first_page_text':self.text,'extracted_first_page_sha256':self.hash}
        results=self.root/'results.jsonl';results.write_bytes(encoded(result)+b'\n')
        primary={'target_id':self.target,'source_id':self.source,'category':'attachment_and_typo_pending','full_retained_text_read':True,
            'original_verified':True,'original_sha256':self.primary,'raw_key':self.key,'target_identity':result['target_identity'],
            'target_title':'Title','observed_header':'HEADER','results_path':str(results),'results_sha256':digest(results.read_bytes()),
            'results_line':1,'page_sha256':[self.hash],'full_text_lengths':[len(self.text)]}
        prior=self.root/'primary-review.json';prior.write_bytes(encoded({'pending':[primary]}))
        proposal=self.root/'proposal.json';proposal.write_bytes(encoded({'candidates':[{'target_id':self.target,'source_id':self.source,
            'subject_review':self.identity,'approved_primary_sha256s':[self.primary]}]}))
        text=self.root/'attachment.txt';text.write_bytes(b'Full independently read annex')
        self.report=self.root/'attachment-review.json';self.report.write_bytes(encoded({'items':[{'target_id':self.target,'parent_source_id':self.source,
            'original_sha256':self.extra,'parent_original_sha256':self.primary,'attachment_identity_approved':True,
            'full_text_path':str(text),'full_text_sha256':digest(text.read_bytes())}]}))
        evidence={'primary_text_identity_verified':True,'identity_review':self.identity,'proposal_path':str(proposal),
            'proposal_sha256':digest(proposal.read_bytes()),'full_primary_review_path':str(prior),'full_primary_review_sha256':digest(prior.read_bytes()),
            'attachment_review_path':str(self.report),'attachment_review_sha256':digest(self.report.read_bytes())}
        self.review={'target_id':self.target,'source_id':self.source,'approved_primary_sha256s':[self.primary],'evidence':evidence}
        self.item=copy.deepcopy(self.review);self.item['evidence'].update(original_verified=True,original_key=self.key,original_sha256=self.primary,extracted_page_sha256=[self.hash])
        self.item['additional_verified_attachments']=[{'receipt':{'sha256':self.extra},'evidence_report_path':str(self.report),
            'evidence_report_sha256':digest(self.report.read_bytes()),'full_text_sha256':digest(text.read_bytes())}]

    def verify(self):
        from law_crawler.recovery_audit import _verify_legacy_attachment_review
        return _verify_legacy_attachment_review(self.item,self.review)

    def test_valid_derived_fields_require_complete_bound_chain(self):
        self.assertTrue(self.verify()['verified'])

    def test_derived_sha_is_not_ignored(self):
        self.item['evidence']['extracted_page_sha256']=['e'*64]
        with self.assertRaises(ValueError):self.verify()

    def test_extra_evidence_field_not_silently_removed(self):
        self.item['evidence']['unsupported_claim']='approved'
        with self.assertRaises(ValueError):self.verify()

    def test_pending_attachment_cannot_be_promoted(self):
        value=json.loads(self.report.read_bytes());value['items'][0]['attachment_identity_approved']=False
        self.report.write_bytes(encoded(value));sha=digest(self.report.read_bytes())
        self.review['evidence']['attachment_review_sha256']=sha;self.item['evidence']['attachment_review_sha256']=sha
        self.item['additional_verified_attachments'][0]['evidence_report_sha256']=sha
        with self.assertRaises(ValueError):self.verify()

    def test_attached_full_text_tamper_fails(self):
        (self.root/'attachment.txt').write_text('tampered')
        with self.assertRaises(ValueError):self.verify()


if __name__=='__main__':unittest.main()
