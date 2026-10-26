import unittest
import tempfile,json,sqlite3
from pathlib import Path
from law_crawler.catalog_census_report import cutoff_bucket,identity,scope,build

class CensusReportTests(unittest.TestCase):
    def test_cutoff_not_enactment(self):
        self.assertEqual(cutoff_bucket({'enacted_at':'2026-01-01','year':2026},'2026-10-21'),'cutoff_unknown')
        self.assertEqual(cutoff_bucket({'published_at':'2026-10-22'},'2026-10-21'),'date_after_cutoff')
        self.assertEqual(cutoff_bucket({'published_at':'2026-10-21'},'2026-10-21'),'date_on_or_before_cutoff')
        self.assertEqual(cutoff_bucket({'published_at':'2026-99-99','year':2025},'2026-10-21'),'year_before_cutoff_exact_date_unknown')
    def test_incomplete_identity_not_merged(self):
        self.assertIsNone(identity({'identity_status':'observed','identity':{'jurisdiction':'ID','year':'2020'}}))
        self.assertIsNone(identity({'title':'同名法规','year':2020}))
    def test_decision_pending(self):
        self.assertEqual(scope({'document_type':'administrative_decision'}),'pending_normative_or_administrative')
        self.assertEqual(scope({'classification':'draft'}),'excluded_draft')
        self.assertEqual(scope({'classification':'planning'}),'excluded_legislative_plan')
        self.assertEqual(scope({'scope_status':'candidate','title':'修改草案程序规定'}),'candidate')

    def test_experimental_records_not_production(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'plan.json').write_text(json.dumps({'cutoff':'2026-10-21'}))
            source=root/'jdihn.go.id';source.mkdir()
            conn=sqlite3.connect(source/'records.sqlite3');conn.execute('create table records(data text)')
            conn.execute('insert into records values (?)',(json.dumps({'url':'https://dev.example/1','source_environment':'development'}),));conn.commit();conn.close()
            report=build(root)
            self.assertEqual(report['catalogue_records'],0)
            self.assertIsNone(report['exact_all_platform_all_version_regulation_total'])
            self.assertEqual(len(report['platforms']),1)
            self.assertEqual(report['source_scope'],'observed_sources_only_unconfirmed_scope')

class SourceScopeTests(unittest.TestCase):
    def test_frozen_sources_include_not_started_and_no_invented_country(self):
        from law_crawler.catalog_census_report import write
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'plan.json').write_text(json.dumps({'cutoff':'2025-01-02','jurisdiction':'Canada','platforms':['laws.justice.gc.ca','gazette.gc.ca']}))
            result=write(root,root/'report')
            self.assertEqual(len(result['platforms']),2)
            self.assertEqual(result['jurisdiction'],'Canada')
            self.assertTrue(all(p['status']=='not_started' for p in result['platforms']))
            self.assertIn('Canada',(root/'report/census-report.md').read_text())
            self.assertNotIn('印尼11',(root/'report/census-report.html').read_text())

if __name__=='__main__': unittest.main()
