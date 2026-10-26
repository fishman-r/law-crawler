import json
import unittest
import sqlite3
from pathlib import Path
import test_recovery_workflow as fixtures
from law_crawler.recovery_supplement import select_supplemental_candidates, verify_previous_pending
from law_crawler.recovery_validation import digest


class SupplementTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.WorkflowTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f=self.fixture
        self.old,receipt=f.select()
        self.review=f.batch/'pending.json'
        f.write(self.review,{'selection_sha256':receipt['sha256'],'repairs':[],
            'pending':[{'target_id':f.target,'source_id':f.source}]})
        self.item=dict(f.item,previous_review={'path':str(self.review),'sha256':digest(self.review.read_bytes())},
            previous_selection={'path':str(self.old),'sha256':receipt['sha256']})
        self.candidates=f.batch/'supplement-candidates.json';self.save()

    def save(self):
        self.fixture.write(self.candidates,{'items':[self.item]})

    def select(self):
        f=self.fixture
        return select_supplemental_candidates(**f.common,candidates=self.candidates,output=f.batch/'supplement/selection.json')

    def test_preserves_history_and_queue(self):
        f=self.fixture;before=(f.batch/'queue.sqlite3').read_bytes();old=self.old.read_bytes()
        result=self.select();self.assertEqual(result['count'],1)
        self.assertEqual(before,(f.batch/'queue.sqlite3').read_bytes());self.assertEqual(old,self.old.read_bytes())
        self.assertEqual(f.select('normal-next')[1]['count'],0)

    def test_non_pending_target_refused(self):
        f=self.fixture;f.write(self.review,{'repairs':[],'pending':[]})
        self.item['previous_review']['sha256']=digest(self.review.read_bytes());self.save()
        with self.assertRaisesRegex(ValueError,'must be pending'):self.select()

    def test_old_selection_not_bound_to_review_refused(self):
        f=self.fixture;v=json.loads(self.review.read_text());v['selection_sha256']='d'*64;f.write(self.review,v)
        self.item['previous_review']['sha256']=digest(self.review.read_bytes());self.save()
        with self.assertRaisesRegex(ValueError,'does not bind'):self.select()

    def test_target_identity_cannot_be_silently_rewritten(self):
        self.item['target_identity']=['PP','2','2020'];self.save()
        with self.assertRaisesRegex(ValueError,'preserve original'):self.select()

    def test_rejected_candidate_is_still_blocked(self):
        f=self.fixture;f.pair_ledger([f.rejection()])
        with self.assertRaisesRegex(ValueError,'excluded'):self.select()

    def test_old_review_tampering_detected_again_after_selection(self):
        f=self.fixture;r=self.select();v=json.loads(Path(r['path']).read_text())['items'][0]
        self.review.write_text('{}')
        with self.assertRaisesRegex(ValueError,'SHA256 mismatch'):verify_previous_pending(v)

    def test_existing_output_never_overwritten(self):
        self.select()
        with self.assertRaisesRegex(ValueError,'Immutable'):self.select()

    def test_cli_supplement_dispatch(self):
        from law_crawler.recovery_workflow import main
        f=self.fixture
        self.assertEqual(main(['supplement','--batch',str(f.batch),'--manifest',str(f.manifest),
            '--manifest-sha256',f.common['manifest_sha256'],'--candidates',str(self.candidates),
            '--output',str(f.batch/'cli/selection.json')]),0)

    def test_different_source_can_replace_rejected_pair(self):
        f=self.fixture;f.pair_ledger([f.rejection()])
        with sqlite3.connect(f.batch/'queue.sqlite3') as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?)',('e'*64,'completed','{}'))
        self.item['source_id']='e'*64;self.save()
        self.assertEqual(self.select()['count'],1)

    def test_old_binding_tampering_refused_before_execution(self):
        from law_crawler.recovery_validation import validate_repair_sources
        from law_crawler.recovery_workflow import freeze_reviews
        f=self.fixture;result=self.select()
        e=dict(review_status='approved_identity',target_identity=self.item['target_identity'],
            target_title=self.item['target_title'],review_note='independent review',
            selection_path=result['path'],selection_sha256=result['sha256'],
            original_verified=True,original_sha256=f.sha,original_key=self.item['raw_key'],
            extracted_page_sha256=[self.item['extracted_first_page_sha256']])
        reviewed=f.batch/'new-review.json'
        f.write(reviewed,{'repairs':[dict(target_id=f.target,source_id=f.source,approved_primary_sha256s=[f.sha],evidence=e)]})
        freeze_reviews(**f.common,reviews=[(reviewed,digest(reviewed.read_bytes()))],output=f.batch/'new-plan.json',run_id='new')
        item=json.loads((f.batch/'new-plan.json').read_text())['repairs'][0]
        before=(f.batch/'queue.sqlite3').read_bytes()
        self.old.write_text('{}')
        with self.assertRaisesRegex(ValueError,'SHA256 mismatch'):validate_repair_sources(f.batch,item)
        self.assertEqual(before,(f.batch/'queue.sqlite3').read_bytes())
