"""Offline fixtures; run on the deployment/test host, not the operator workstation."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from law_crawler.recovery_workflow import select_candidates, freeze_reviews, verify_review_evidence
from law_crawler.recovery_validation import digest


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)/'batch'; self.batch.mkdir()
        (self.batch/'outcomes').mkdir()
        self.target, self.source, self.sha = 'a'*64, 'b'*64, 'c'*64
        db = sqlite3.connect(self.batch/'queue.sqlite3')
        db.execute('CREATE TABLE jobs(id TEXT,status TEXT,record TEXT)')
        db.executemany('INSERT INTO jobs VALUES(?,?,?)', [(self.target,'no_original','{}'),(self.source,'completed','{}')]); db.commit(); db.close()
        self.write(self.batch/'outcomes'/f'{self.target}.json', {'status':'no_original'})
        self.write(self.batch/'outcomes'/f'{self.source}.json', {'status':'completed','oss_metadata':{'status':'uploaded_verified'},'metadata':{'file_roles':{'https://official/body':{'role':'regulation_body'}}},'files':[{'url':'https://official/body','sha256':self.sha,'size_bytes':10,'complete':True,'oss':{'status':'uploaded_verified','raw_key':'ods/raw/'+self.sha+'.pdf'}}]})
        self.manifest = self.batch/'manifest.json'
        self.write(self.manifest, {'batch_id':'batch','jobs':[[self.target]]})
        self.common = dict(batch=self.batch, manifest=self.manifest, manifest_sha256=digest(self.manifest.read_bytes()))
        self.item = dict(target_id=self.target,source_id=self.source,sha256=self.sha,original_verified=True,first_page_text='PERATURAN 1 2020 complete subject',extracted_first_page_sha256=digest(b'PERATURAN 1 2020 complete subject'),target_identity=['PP','1','2020'],target_title='complete subject')
        self.item['raw_key'] = 'ods/raw/' + self.sha + '.pdf'
        self.candidates = self.batch/'candidates.json'; self.write(self.candidates, {'items':[self.item]})

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def select(self, directory='review'):
        path = self.batch/directory/'selection.json'
        result = select_candidates(**self.common,candidates=self.candidates,output=path,limit=1)
        return path, result

    def review(self):
        selection, receipt = self.select()
        evidence = dict(review_status='independent_native_text_identity_and_subject_match',target_identity=self.item['target_identity'],target_title=self.item['target_title'],review_note='Read full retained text',selection_path=str(selection),selection_sha256=receipt['sha256'],extracted_page_sha256=[self.item['extracted_first_page_sha256']])
        evidence.update(original_verified=True,original_sha256=self.sha,original_key=self.item['raw_key'])
        repair = dict(target_id=self.target,source_id=self.source,approved_primary_sha256s=[self.sha],evidence=evidence)
        path = self.batch/'review.json'; self.write(path, {'repairs':[repair]})
        return path, repair

    def freeze(self, path, output='plan.json'):
        return freeze_reviews(**self.common,reviews=[(path,digest(path.read_bytes()))],output=self.batch/output,run_id='run')

    def test_selection_excludes_prior_and_never_changes_queue(self):
        before = (self.batch/'queue.sqlite3').read_bytes()
        self.assertEqual(self.select()[1]['count'],1)
        self.assertEqual(self.select('next')[1]['count'],0)
        self.assertEqual(before,(self.batch/'queue.sqlite3').read_bytes())

    def test_text_hash_corruption_refused(self):
        self.item['first_page_text'] = 'corrupt'; self.write(self.candidates, {'items':[self.item]})
        with self.assertRaisesRegex(ValueError,'SHA256'): self.select()

    def test_freeze_binds_and_cannot_overwrite(self):
        path, _ = self.review(); before = (self.batch/'queue.sqlite3').read_bytes()
        self.assertEqual(self.freeze(path)['count'],1)
        plan = json.loads((self.batch/'plan.json').read_text())
        self.assertEqual(plan['repairs'][0]['target_record_sha256'],digest(b'{}'))
        self.assertEqual((self.batch/'plan.sha256').read_text().strip(),digest((self.batch/'plan.json').read_bytes()))
        self.assertEqual(before,(self.batch/'queue.sqlite3').read_bytes())
        with self.assertRaises(ValueError): self.freeze(path)

    def test_duplicate_review_refused(self):
        path, repair = self.review(); self.write(path, {'repairs':[repair,repair]})
        with self.assertRaisesRegex(ValueError,'Duplicate'): self.freeze(path)

    def test_pending_evidence_refused(self):
        _, repair = self.review(); repair['evidence']['review_status']='pending'
        with self.assertRaises(ValueError): verify_review_evidence(repair)

    def test_excluded_source_sha_refused(self):
        path, _ = self.review()
        self.write(self.batch/'recovery'/'invalid-source-files.json',[{'source_id':self.source,'sha256':self.sha}])
        with self.assertRaisesRegex(ValueError,'ledger'): self.freeze(path)

    def test_completed_target_skipped(self):
        path, _ = self.review()
        with sqlite3.connect(self.batch/'queue.sqlite3') as db: db.execute('UPDATE jobs SET status=? WHERE id=?',('completed',self.target))
        self.assertEqual(self.freeze(path)['count'],0)
        plan=json.loads((self.batch/'plan.json').read_text())
        self.assertEqual(plan['skipped'][0]['reason'],'already_completed')

    def test_selection_tampering_refused(self):
        path, repair = self.review()
        Path(repair['evidence']['selection_path']).write_text('{}')
        with self.assertRaisesRegex(ValueError,'SHA256'): self.freeze(path)

    def test_manifest_tampering_refused(self):
        self.manifest.write_text('{}')
        with self.assertRaises(ValueError): self.select()

    def test_source_status_refused(self):
        path, _ = self.review()
        with sqlite3.connect(self.batch/'queue.sqlite3') as db: db.execute('UPDATE jobs SET status=? WHERE id=?',('failed',self.source))
        with self.assertRaisesRegex(ValueError,'status'): self.freeze(path)

    def test_arbitrary_output_name_refused(self):
        with self.assertRaisesRegex(ValueError, 'selection.json'):
            select_candidates(**self.common,candidates=self.candidates,output=self.batch/'a.json',limit=1)

    def test_external_unregistered_output_refused(self):
        with self.assertRaises(ValueError):
            select_candidates(**self.common,candidates=self.candidates,output=Path(self.tmp.name)/'external'/'selection.json',limit=1)

    def test_blank_retained_page_preserved(self):
        self.item['page_texts']=[self.item['first_page_text'],'']
        self.item['extracted_page_sha256']=[self.item['extracted_first_page_sha256'],digest(b'')]
        self.write(self.candidates, {'items':[self.item]})
        self.assertEqual(self.select()[1]['count'],1)

    def test_registry_excludes_other_external_review_directory(self):
        counts=[]
        for name in ('one','two'):
            directory=Path(self.tmp.name)/name;directory.mkdir()
            result=select_candidates(**self.common,candidates=self.candidates,output=directory/'selection.json',limit=1,exclude_review_dirs=[directory])
            counts.append(result['count'])
        self.assertEqual(counts,[1,0])

    def test_unfinished_reservation_refuses_selection(self):
        self.write(self.batch/'recovery'/'selection-registry'/'reserved.json',{'state':'reserved','path':'unknown','target_ids':[self.target]})
        with self.assertRaisesRegex(ValueError,'Unfinished'):
            self.select()

    def test_cross_review_pending_conflict_refused(self):
        path, _ = self.review()
        other=self.batch/'pending.json';self.write(other,{'repairs':[],'pending':[{'target_id':self.target}]})
        with self.assertRaisesRegex(ValueError,'conflicting'):
            freeze_reviews(**self.common,reviews=[(path,digest(path.read_bytes())),(other,digest(other.read_bytes()))],output=self.batch/'plan.json',run_id='run')

    def rejection(self, **changes):
        entry = dict(scope='candidate_pair', target_id=changes.get('target_id', self.target),
                    source_id=changes.get('source_id', self.source),
                    sha256=changes.get('sha256', self.sha), reason='Formal type and subject differ')
        review=self.batch/('rejected-'+digest(json.dumps(entry).encode())+'.json')
        self.write(review,{'rejections':[dict(entry,decision='reject_target_source_sha_pair_only')]})
        return dict(entry,review_path=str(review),review_sha256=digest(review.read_bytes()))

    def pair_ledger(self, entries):
        path = self.batch/'recovery'/'rejected-candidate-pairs.json'
        self.write(path, {'schema_version':1, 'batch_id':self.batch.name, 'rejections':entries})
        return path

    def add_target(self, target):
        with sqlite3.connect(self.batch/'queue.sqlite3') as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?)', (target,'no_original','{}'))
        self.write(self.batch/'outcomes'/f'{target}.json', {'status':'no_original'})
        value=json.loads(self.manifest.read_text());value['jobs'].append([target]);self.write(self.manifest,value)
        self.common['manifest_sha256']=digest(self.manifest.read_bytes())

    def test_history_selection_does_not_exclude_source_for_another_target(self):
        self.select()
        other='d'*64;self.add_target(other)
        self.write(self.candidates, {'items':[dict(self.item,target_id=other)]})
        path, receipt=self.select('second')
        self.assertEqual(receipt['count'],1)
        self.assertEqual(json.loads(path.read_text())['items'][0]['target_id'],other)

    def test_scoped_rejection_blocks_only_exact_candidate_not_shared_source(self):
        self.pair_ledger([self.rejection()]);other='d'*64;self.add_target(other)
        self.write(self.candidates, {'items':[self.item,dict(self.item,target_id=other)]})
        path, receipt=self.select()
        self.assertEqual(receipt['count'],1)
        self.assertEqual(json.loads(path.read_text())['items'][0]['target_id'],other)

    def test_rejected_pair_allows_different_source_for_same_target(self):
        self.pair_ledger([self.rejection()]);other='d'*64
        with sqlite3.connect(self.batch/'queue.sqlite3') as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?)',(other,'completed','{}'))
        self.write(self.candidates, {'items':[self.item,dict(self.item,source_id=other)]})
        path, receipt=self.select()
        self.assertEqual(receipt['count'],1)
        self.assertEqual(json.loads(path.read_text())['items'][0]['source_id'],other)

    def test_scoped_rejection_allows_different_original_for_same_pair(self):
        self.pair_ledger([self.rejection(sha256='d'*64)])
        self.assertEqual(self.select()[1]['count'],1)

    def test_explicit_scoped_exclusion_does_not_expand_to_target_or_source(self):
        explicit=self.batch/'explicit.json';self.write(explicit,{'rejections':[self.rejection(source_id='d'*64)]})
        result=select_candidates(**self.common,candidates=self.candidates,output=self.batch/'review/selection.json',limit=1,exclusion_manifests=[explicit])
        self.assertEqual(result['count'],1)

    def test_explicit_legacy_target_and_source_exclusions_remain_supported(self):
        for index, exclusion in enumerate(({'target_id':self.target},{'source_id':self.source,'sha256':self.sha})):
            with self.subTest(exclusion=exclusion):
                explicit=self.batch/f'explicit-{index}.json';self.write(explicit,[exclusion])
                result=select_candidates(**self.common,candidates=self.candidates,output=self.batch/f'review-{index}/selection.json',limit=1,exclusion_manifests=[explicit])
                self.assertEqual(result['count'],0)

    def test_freeze_refuses_exact_rejected_pair(self):
        path,_=self.review();self.pair_ledger([self.rejection()])
        with self.assertRaisesRegex(ValueError,'Rejected candidate pair'):self.freeze(path)
        self.assertFalse((self.batch/'plan.json').exists())

    def test_freeze_allows_source_used_by_rejection_for_another_target(self):
        path,_=self.review();self.pair_ledger([self.rejection(target_id='d'*64)])
        self.assertEqual(self.freeze(path)['count'],1)

    def test_freeze_allows_target_with_another_rejected_source(self):
        path,_=self.review();self.pair_ledger([self.rejection(source_id='d'*64)])
        self.assertEqual(self.freeze(path)['count'],1)

    def test_scoped_schema_invalid_entries_fail_closed(self):
        cases=[dict(self.rejection(),scope='source'),dict(self.rejection(),source_id='bad'),
               dict(self.rejection(),sha256='BAD'),dict(self.rejection(),reason=''),
               dict(self.rejection(),source_id=self.target),dict(self.rejection(),review_sha256='d'*64)]
        for entry in cases:
            with self.subTest(entry=entry):
                self.pair_ledger([entry])
                with self.assertRaises((ValueError,KeyError)):self.select()

    def test_rejected_pair_ledger_wrong_batch_fails_closed(self):
        path=self.pair_ledger([self.rejection()]);value=json.loads(path.read_text());value['batch_id']='other';self.write(path,value)
        with self.assertRaisesRegex(ValueError,'batch mismatch'):self.select()

    def test_rejection_review_hash_is_checked(self):
        review=self.batch/'negative-review.json';self.write(review,{'pending':[{'target_id':self.target}]})
        self.pair_ledger([dict(self.rejection(),review_path=str(review),review_sha256='f'*64)])
        with self.assertRaisesRegex(ValueError,'SHA256 mismatch'):self.select()

    def test_global_invalid_source_ledger_keeps_source_sha_semantics(self):
        self.write(self.batch/'recovery/invalid-source-files.json',[{'target_id':'d'*64,'source_id':self.source,'sha256':self.sha}])
        self.assertEqual(self.select()[1]['count'],0)

    def test_execution_checks_pair_rejection_added_after_freeze(self):
        from law_crawler.recovery_validation import validate_repair_sources
        path,_=self.review();self.freeze(path);item=json.loads((self.batch/'plan.json').read_text())['repairs'][0]
        before=(self.batch/'queue.sqlite3').read_bytes()
        self.pair_ledger([self.rejection()])
        with self.assertRaisesRegex(ValueError,'Rejected candidate pair'):validate_repair_sources(self.batch,item)
        self.assertEqual(before,(self.batch/'queue.sqlite3').read_bytes())

    def test_execution_rechecks_explicit_pending_resolution(self):
        from law_crawler.recovery_validation import validate_repair_sources
        path,_=self.review();self.freeze(path);item=json.loads((self.batch/'plan.json').read_text())['repairs'][0]
        item['evidence']['pending_resolution']={'kind':'unknown'}
        with self.assertRaisesRegex(ValueError,'resolution kind'):validate_repair_sources(self.batch,item)

    def test_rejection_requires_matching_explicit_decision(self):
        for change in ({'target_id':'d'*64},{'source_id':'d'*64},{'sha256':'d'*64}):
            entry=dict(self.rejection(),**change);self.pair_ledger([entry])
            with self.assertRaisesRegex(ValueError,'explicitly reject'):self.select()

    def test_rejection_cannot_cite_approval_or_omit_review(self):
        path,_=self.review()
        entry=dict(self.rejection(),review_path=str(path),review_sha256=digest(path.read_bytes()))
        self.pair_ledger([entry])
        with self.assertRaisesRegex(ValueError,'explicitly reject'):self.freeze(path)
        entry.pop('review_path');self.pair_ledger([entry])
        with self.assertRaisesRegex(ValueError,'review path required'):self.freeze(path)

    def test_negative_review_status_never_matches_approval_substring(self):
        _,repair=self.review()
        for status in ('unverified','not_approved','identity_mismatch','not_verified'):
            repair['evidence']['review_status']=status
            with self.assertRaisesRegex(ValueError,'Approved independent'):verify_review_evidence(repair)
