import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crawl_store import CrawlStore
from import_body_review import import_body_reviews
from platform_adapters.base import field


class BodyReviewTests(unittest.TestCase):
    def populate(self, store, suffix, published):
        metadata = dict(document_type=field('regulation','regulation','type','https://official.example/detail','Type'),
            legal_identity=field(dict(jurisdiction='ID', issuer='Ministry', type='Peraturan Menteri', number='1',year='2026'),
                'identity','title','https://official.example/detail','Identity'),
            published_at=field(published,published,'Pengundangan','https://official.example/detail','Promulgation'))
        url = 'https://official.example/' + suffix + '.pdf'
        return store.save(url,url,'official.example',lambda u:(b'%PDF-1.4\n'+suffix.encode()+b'\n%%EOF',u),metadata)[1]

    def review(self, status):
        return dict(status=status,reason='Explicit first-page identity review only',
            evidence=dict(audit_file='review.json',method='manual title comparison'),reviewed_at='2026-10-14T23:00:00+08:00')

    def test_conflict_excluded_unknown_not_passed_original_and_review_survive_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            with CrawlStore(directory) as store:
                matched=self.populate(store,'old','2026-01-01')
                conflict=self.populate(store,'draft','2026-02-01')
                unknown=self.populate(store,'scan','2026-03-01')
                sha_conflict=conflict['file']['sha256']; sha_unknown=unknown['file']['sha256']
                for record,status in [(matched,'title_type_number_year_match'),(conflict,'catalogue_body_type_conflict'),
                                      (unknown,'unknown_no_extractable_text_first_two_pages')]:
                    store.register_body_review(record['file']['sha256'],self.review(status))
                original_before=(directory/conflict['file']['path']).read_bytes()
                candidates=store.update_current_selection('Indonesia')
                self.assertNotIn(sha_conflict,[c['id'] for c in candidates])
                unverified=next(c for c in candidates if c['id']==sha_unknown)
                self.assertFalse(unverified['body_identity_verified'])
                self.assertEqual(unverified['body_review']['status'],'unknown_no_extractable_text_first_two_pages')
            (directory/'crawl-state.sqlite3').unlink()
            with CrawlStore(directory) as rebuilt:
                restored=rebuilt.by_hash(sha_conflict)
                self.assertEqual(restored['body_review'],self.review('catalogue_body_type_conflict'))
                self.assertEqual((directory/restored['file']['path']).read_bytes(),original_before)
                self.assertEqual(len(rebuilt.records()),3)
                self.assertNotIn(sha_conflict,[c['id'] for c in rebuilt.update_current_selection('Indonesia')])
                sidecar=json.loads((directory/restored['file']['path']).with_suffix('.json').read_text())
                self.assertEqual(sidecar['body_review']['status'],'catalogue_body_type_conflict')

    def test_cache_and_source_refresh_preserve_body_review(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            record=self.populate(store,'original','2026-01-01'); sha=record['file']['sha256']
            store.register_body_review(sha,self.review('catalogue_body_type_conflict'))
            newer=self.populate(store,'original','2026-05-01')
            self.assertEqual(newer['body_review']['status'],'catalogue_body_type_conflict')
            url='https://official.example/original.pdf'
            status,refreshed=store.save(url,url,'official.example',
                lambda u:(b'%PDF-1.4\noriginal\n%%EOF',u),refresh=True)
            self.assertEqual(status,'content_duplicate')
            self.assertEqual(refreshed['body_review']['status'],'catalogue_body_type_conflict')
            self.assertEqual(store.update_current_selection('Indonesia'),[])

    def test_raw_and_markdown_sidecars_both_keep_review_after_rebuild(self):
        from tests.test_canadian_xml import law_xml
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp); raw=law_xml(); sha=hashlib.sha256(raw).hexdigest()
            metadata=dict(document_type=field('regulation','Act','type','https://official.example/law','Type'))
            with CrawlStore(directory) as store:
                _,record=store.save('https://official.example/law.xml','https://official.example/law','official.example',
                    lambda u:(raw,u),metadata,convert_text=True)
                store.register_body_review(sha,self.review('title_type_number_year_match'))
                original=directory/record['original_file']['path']
                raw_sidecar=json.loads(original.with_name(original.name+'.metadata.json').read_text())
                md_sidecar=json.loads((directory/record['file']['path']).with_suffix('.json').read_text())
                self.assertEqual(raw_sidecar['body_review'],md_sidecar['body_review'])
                self.assertEqual(original.read_bytes(),raw)
            (directory/'crawl-state.sqlite3').unlink()
            with CrawlStore(directory) as store:
                restored=store.by_hash(sha)
                self.assertTrue(store.valid(restored))
                self.assertEqual(restored['body_review']['status'],'title_type_number_year_match')

    def test_batch_rerun_reports_registered_conflict_without_network(self):
        from tests.test_regulation_workflow import arguments, workspace, Fixture, PDF
        from regulation_pipeline import execute
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workspace(root)
            def fetch(url,*args): return (PDF if url.endswith('.pdf') else b'page'),url
            with patch('regulation_pipeline.ADAPTERS',{'gesetze-im-internet.de':Fixture()}),patch('download_regulations.fetch',side_effect=fetch):
                self.assertEqual(execute(arguments(root,mode='content',batch_id='review-rerun')),0)
            with CrawlStore(root/'downloads/Germany') as store:
                record=store.records()[0]
                store.register_body_review(record['file']['sha256'],self.review('catalogue_body_type_conflict'))
            with patch('regulation_pipeline.ADAPTERS',{'gesetze-im-internet.de':Fixture()}),patch(
                    'download_regulations.fetch',side_effect=AssertionError('replay must not request valid file')):
                self.assertEqual(execute(arguments(root,jurisdiction=None,mode=None,rerun_batch='review-rerun')),0)
            attempt=root/'downloads/.batches/review-rerun/attempts/0002'
            report=json.loads((attempt/'report.json').read_text())
            self.assertEqual(report['files'][0]['body_review']['status'],'catalogue_body_type_conflict')
            current=json.loads((root/'downloads/Germany/current-regulations.json').read_text())
            self.assertEqual(current['records'],[])

    def test_import_validates_all_entries_before_any_review_write(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            record=self.populate(store,'original','2026-01-01')
            item=dict(file=record['file']['path'],text_review='title_type_number_year_match',reason='Explicit match')
            bad=dict(item,text_review='automatic_pass')
            with self.assertRaises(ValueError):
                import_body_reviews(store,dict(method='manual',files=[item,bad]),'audit.json',tmp)
            self.assertEqual(store.by_hash(record['file']['sha256'])['body_review']['status'],'not_reviewed')
            imported=import_body_reviews(store,dict(method='manual',files=[item]),'audit.json',tmp,
                                        '2026-10-14T23:00:00+08:00')
            self.assertEqual(imported[0]['body_review']['evidence']['original_sha256'],record['file']['sha256'])
            self.assertEqual(imported[0]['body_review']['status'],'title_type_number_year_match')

    def test_registration_rejects_unknown_status_hash_or_missing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            record=self.populate(store,'original','2026-01-01'); sha=record['file']['sha256']
            for review in [dict(self.review('not_reviewed'),status='passed'),
                           dict(self.review('not_reviewed'),evidence=None),
                           dict(self.review('not_reviewed'),reviewed_at='2026-10-14')]:
                with self.assertRaises(ValueError): store.register_body_review(sha,review)
            with self.assertRaises(ValueError):
                store.register_body_review('0'*64,self.review('title_type_number_year_match'))


if __name__=='__main__': unittest.main()
