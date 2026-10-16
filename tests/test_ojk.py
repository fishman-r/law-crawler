from pathlib import Path
import unittest
from law_crawler.platform_adapters.ojk import OJKAdapter


class OJKTests(unittest.TestCase):
    def test_formal_regulation_not_abstract_or_faq(self):
        adapter=OJKAdapter()
        url='https://www.ojk.go.id/id/regulasi/Pages/POJK-11-Tahun-2026-Laporan-Berkala-Lembaga-Penjamin.aspx'
        body=(Path(__file__).parent/'fixtures/ojk/pojk11-2026.html').read_bytes()
        doc=adapter.read_document(url,lambda _: (body,url))
        self.assertEqual(len(doc.pdf_urls),1)
        self.assertNotIn('Abstrak',doc.pdf_urls[0])
        self.assertNotIn('FAQ',doc.pdf_urls[0])
        self.assertEqual(doc.metadata['legal_identity']['value']['number'],'11')
        self.assertIsNone(doc.metadata['published_at']['value'])
        self.assertIsNone(doc.metadata['updated_at']['value'])
        self.assertEqual(doc.metadata['document_type']['value'],'regulation')
        self.assertEqual(doc.metadata['effective_at']['raw'],'1/1/2027')

    def test_listing_excludes_circulars_and_news(self):
        adapter=OJKAdapter()
        html=b'<a href="/id/regulasi/Pages/POJK-11.aspx">law</a><a href="/id/regulasi/Pages/SEOJK-11.aspx">circular</a><a href="/id/news/Pages/POJK-11.aspx">news</a>'
        urls=adapter.discover(lambda _: (html,adapter.entry_urls[0]),5)
        self.assertEqual(urls,['https://www.ojk.go.id/id/regulasi/Pages/POJK-11.aspx'])

if __name__=='__main__': unittest.main()
