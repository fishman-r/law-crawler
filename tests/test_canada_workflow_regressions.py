"""加拿大官方别名及原始字节去重，不把重复入口当作未知版本冲突。"""
import tempfile
import unittest
from pathlib import Path
from copy import deepcopy
from law_crawler.crawl_store import CrawlStore, unknown_metadata
from law_crawler.platform_adapters.base import field
from law_crawler.regulation_selection import select_latest

XML=b'<Statute xmlns:lims="http://justice.gc.ca/lims" lims:id="1"><Identification><LongTitle>Law</LongTitle><Chapter><ConsolidatedNumber>A-1</ConsolidatedNumber></Chapter></Identification><Body><Section><Text>Provision text.</Text></Section></Body></Statute>'


def candidate(host,platform,id):
    detail='https://'+host+'/eng/acts/A-1/index.html';url='https://'+host+'/eng/XML/A-1.xml'
    md=unknown_metadata(detail)
    md['document_type']=field('regulation','Act','official type',detail,'Official statutory type')
    md['title']=field('Law','Law','title',detail,'Official title')
    md['legal_identity']=field(dict(jurisdiction='CA',issuer=None,type='Act',number='A-1',year=None),'A-1','identity',detail,'identity')
    md['official_resource_uri']=field('https://laws-lois.justice.gc.ca/eng/XML/A-1.xml',url,'official alias',detail,'Equivalent official resource')
    md['text_version']=field('consolidated','current','version',detail,'Official current consolidation')
    md['consolidated_to']=field('2026-09-21','2026-09-21','current to',detail,'Consolidation coverage date')
    return dict(id=id,country='Canada',platform=platform,detail_url=detail,file_urls=[url],metadata=md)


class CanadaWorkflowRegressions(unittest.TestCase):
    def test_equivalent_sources_choose_one_without_inventing_publication_date(self):
        cs=[candidate('laws-lois.justice.gc.ca','open.canada.ca','open'),candidate('laws.justice.gc.ca','laws.justice.gc.ca','justice')]
        selected=select_latest(cs)
        self.assertEqual(len(selected),1)
        self.assertEqual(selected[0]['selection']['reason'],'same_official_resource_date_unknown')
        self.assertTrue(selected[0]['selection']['latest_unverified'])
        self.assertTrue(all(c['publication_date'] is None for c in cs))
        self.assertEqual(sum(c['selection']['reason']=='duplicate_official_resource' for c in cs),1)

    def test_conflicting_version_observations_remain_unresolved(self):
        cs=[candidate('laws-lois.justice.gc.ca','open.canada.ca','open'),candidate('laws.justice.gc.ca','laws.justice.gc.ca','justice')]
        cs[1]['metadata']['consolidated_to']['value']='2026-08-01'
        self.assertEqual(select_latest(cs),[])

    def test_same_raw_bytes_have_stable_markdown_across_source_aliases(self):
        with tempfile.TemporaryDirectory() as tmp,CrawlStore(tmp) as store:
            a=candidate('laws-lois.justice.gc.ca','open.canada.ca','open')
            b=candidate('laws.justice.gc.ca','laws.justice.gc.ca','justice')
            first=deepcopy(store.save(a['file_urls'][0],a['detail_url'],a['platform'],lambda u:(XML,u),a['metadata'],convert_text=True)[1])
            result,second=store.save(b['file_urls'][0],b['detail_url'],b['platform'],lambda u:(XML,u),b['metadata'],convert_text=True)
            self.assertEqual(result,'content_duplicate')
            self.assertEqual(first['file']['sha256'],second['file']['sha256'])
            self.assertEqual(len(second['sources']),2)
            self.assertTrue(store.valid(second))
            self.assertEqual(len(list(Path(tmp).glob('*.md'))),1)
            self.assertEqual(len(list((Path(tmp)/'raw').glob('*.xml'))),1)
