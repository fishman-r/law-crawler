"""任务分区和法律身份计数不能混用页面、文件、版本。"""
import hashlib
import unittest
from law_crawler.download_metrics import progress_summary


def key(url):
    return hashlib.sha256(url.encode()).hexdigest()


def good(digest='a'):
    return dict(url='https://fixture.test/body.pdf', complete=True, capture_status='saved', status_code=200, sha256=digest*64)


def metadata(number='1'):
    return dict(document_type={'value':'regulation'}, processing_gate='eligible',
                legal_identity={'status':'observed','value':dict(jurisdiction='ID',issuer='Ministry',type='Regulation',number=number,year='2025')},
                file_roles={'https://fixture.test/body.pdf':{'role':'regulation_body','status':'html_context_observed'}})


class DownloadMetricsTests(unittest.TestCase):
    def state(self, docs):
        return dict(created_at='2026-10-21T00:00:00+00:00',
                    platforms={'test':{'details':list(docs)}},
                    documents={key(url):doc for url,doc in docs.items()})

    def doc(self, meta=None, file=None, status='archived'):
        return dict(status=status, metadata=meta or metadata(), files={'body':file or good()})

    def test_same_law_multiple_sources_formats_do_not_create_versions(self):
        s=self.state({'https://fixture.test/one':self.doc(), 'https://fixture.test/two':self.doc(file=good('b'))})
        r=progress_summary(s,5,'2026-10-21T01:00:00+00:00',phase='finished')
        self.assertEqual(r['operations']['completed'],2)
        self.assertEqual(r['identity_counts']['regulations'],1)
        self.assertEqual(r['identity_counts']['duplicate_source_records'],1)
        self.assertIsNone(r['identity_counts']['versions'])
        self.assertEqual(r['identity_counts']['version_pending_regulations'],1)
        self.assertEqual(r['file_counts']['unique_downloaded_files'],2)
        self.assertEqual(r['file_counts']['body_files'],2)
        self.assertEqual(r['timing']['elapsed_seconds'],3600)
        self.assertIsNone(r['timing']['active_download_seconds'])

    def test_same_hash_conflicting_roles_go_to_pending_once(self):
        m=metadata();m['file_roles']['https://fixture.test/body.pdf']['role']='regulation_annex'
        s=self.state({'https://fixture.test/one':self.doc(), 'https://fixture.test/two':self.doc(meta=m)})
        r=progress_summary(s,2)
        self.assertEqual(r['file_counts']['downloaded_references'],2)
        self.assertEqual(r['file_counts']['unique_downloaded_files'],1)
        self.assertEqual(r['file_counts']['role_pending_files'],1)
        self.assertEqual(r['file_counts']['body_files'],0)
        self.assertEqual(r['file_counts']['annex_files'],0)

    def test_missing_identity_unclassified_and_non_target_not_laws(self):
        a=metadata();del a['legal_identity']['value']['issuer']
        b=metadata();b['document_type']={'value':'unclassified_official_document'}
        c=metadata();c['document_type']={'value':'administrative_decision'}
        r=progress_summary(self.state({'https://fixture.test/a':self.doc(a), 'https://fixture.test/b':self.doc(b), 'https://fixture.test/c':self.doc(c)}),5)
        self.assertEqual(r['identity_counts']['regulations'],0)
        self.assertEqual(r['identity_counts']['identity_pending_details'],2)
        self.assertEqual(r['identity_counts']['non_target_details'],1)
        self.assertEqual(r['file_counts']['role_pending_files'],1)  # same SHA across all roles is ambiguous

    def test_explicit_official_version_evidence_only_and_law_count_unchanged(self):
        a=metadata();a['version_id']={'value':'v1','status':'observed','source_url':'https://fixture.test/a'}
        b=metadata();b['version_id']={'value':'v2','status':'observed','source_url':'https://fixture.test/b'}
        r=progress_summary(self.state({'https://fixture.test/a':self.doc(a), 'https://fixture.test/b':self.doc(b,file=good('b'))}),5)
        self.assertEqual(r['identity_counts']['regulations'],1)
        self.assertEqual(r['identity_counts']['versions'],2)
        self.assertEqual(r['identity_counts']['multiple_version_regulations'],1)

    def test_retry_active_pending_failed_and_completed_partition(self):
        urls=['https://fixture.test/'+str(i) for i in range(5)]
        docs={urls[0]:self.doc(),urls[1]:self.doc(status='failed'),urls[2]:self.doc(status='failed'),urls[3]:self.doc(status='incomplete')}
        s=self.state(docs);s['platforms']['test']['details'].append(urls[4])
        r=progress_summary(s,5,active_keys=[key(urls[1])],queued_keys=[key(urls[2])])['operations']
        self.assertEqual([r[k] for k in ['total','completed','active','pending','failed','needs_verification']],[5,1,1,2,1,0])
        self.assertEqual(r['total'],sum(r[k] for k in ['completed','active','pending','failed','needs_verification']))

    def test_pending_identity_or_file_role_never_count_as_confirmed(self):
        for status in ('unverified', 'pending_identity_review', None):
            m=metadata();m['legal_identity']['status']=status
            m['file_roles']['https://fixture.test/body.pdf']['status']=status
            r=progress_summary(self.state({'https://fixture.test/a':self.doc(m)}),5)
            self.assertEqual(r['identity_counts']['regulations'],0)
            self.assertEqual(r['identity_counts']['identity_pending_details'],1)
            self.assertEqual(r['file_counts']['body_files'],0)
            self.assertEqual(r['file_counts']['role_pending_files'],1)

    def test_duplicate_detail_urls_count_as_one_task(self):
        s=self.state({'https://fixture.test/a':self.doc()});s['platforms']['alias']={'details':['https://fixture.test/a']}
        self.assertEqual(progress_summary(s,2)['operations']['total'],1)


if __name__=='__main__':unittest.main()
