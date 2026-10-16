"""加拿大适配器离线回归；真实网络验收另见 reports。"""
import unittest
from platform_adapters.canada import JusticeCanadaAdapter, OpenCanadaAdapter

ACT='https://laws.justice.gc.ca/eng/acts/A-1/index.html'
REG='https://laws.justice.gc.ca/eng/regulations/SOR-86-304/index.html'
HTML='''<meta name="dcterms.modified" content="2026-06-14"><h1 class="HeadTitle">Access to Information Act (R.S.C., 1985, c. A-1)</h1><p id="assentedDate">Act current to 2026-09-21 and last amended on 2026-06-14.</p><a href="/eng/XML/A-1.xml">XML</a><a href="/PDF/A-1.pdf">PDF</a><a href="/eng/XML/B-1.xml">Other Act</a>'''

class CanadaAdapterTests(unittest.TestCase):
    def test_xml_preferred_and_metadata_dates_separate(self):
        doc=JusticeCanadaAdapter().read_document(ACT,lambda u:(HTML.encode(),u))
        self.assertEqual(doc.file_urls,['https://laws.justice.gc.ca/eng/XML/A-1.xml'])
        self.assertEqual(doc.metadata['legal_identity']['value']['jurisdiction'],'CA')
        self.assertEqual(doc.metadata['updated_at']['value'],'2026-06-14')
        self.assertEqual(doc.metadata['consolidated_to']['value'],'2026-09-21')
        self.assertEqual(doc.metadata['last_amended_at']['value'],'2026-06-14')
        self.assertIsNone(doc.metadata['published_at']['value'])
        self.assertIsNone(doc.metadata['effective_region']['value'])

    def test_discovery_interleaves_law_and_regulation_without_pdf_or_foreign(self):
        a=JusticeCanadaAdapter()
        pages={a.entry_urls[0]:f'<a href="{ACT}">Act</a><a href="{ACT}">Repeated</a><a href="https://outsider.example/eng/acts/X/index.html">X</a>',a.entry_urls[1]:f'<a href="{REG}">Reg</a>'}
        self.assertEqual(a.discover(lambda u:(pages[u].encode(),u),2),[ACT,REG])

    def test_error_or_case_or_mismatched_xml_is_rejected(self):
        a=JusticeCanadaAdapter()
        for url,html in [(ACT,'<h1>Access denied</h1>'),(ACT,HTML.replace('/eng/XML/A-1.xml','/eng/XML/X.xml')),('https://decisions.scc-csc.ca/item/1',HTML)]:
            with self.assertRaises(ValueError):a.read_document(url,lambda u:(html.encode(),u))

    def test_open_data_catalogue_is_discovery_not_saved_document(self):
        a=OpenCanadaAdapter();catalog='https://laws-lois.justice.gc.ca/eng/XML/Legis.xml'
        data=f'<ActsRegsList><Acts><Act><Language>eng</Language><LinkToHTMLToC>{ACT.replace("https:","http:")}</LinkToHTMLToC></Act><Act><Language>fra</Language><LinkToHTMLToC>{ACT}</LinkToHTMLToC></Act></Acts></ActsRegsList>'
        pages={a.entry_urls[0]:f'<a href="{catalog}">XML catalogue</a>',catalog:data,ACT:HTML}
        fetch=lambda u:(pages[u].encode(),u)
        self.assertEqual(a.discover(fetch,2),[ACT])
        doc=a.read_document(ACT,fetch)
        self.assertNotIn(catalog,doc.file_urls)
        self.assertEqual(doc.metadata['catalogue_url']['value'],a.entry_urls[0])
        with self.assertRaises(ValueError):a.read_document(catalog,fetch)
