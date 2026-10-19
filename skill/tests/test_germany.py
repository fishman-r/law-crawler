import unittest

from law_crawler.platform_adapters.germany import GermanyAdapter

ROOT = 'https://www.gesetze-im-internet.de/'
DETAIL = ROOT + 'gg/index.html'
HTML = '''<meta charset="iso-8859-1"><h1 class="headline">Grundgesetz für Deutschland</h1>
<h2 class="headline">Gesamtausgabe der Norm</h2><a href="GG.pdf">PDF</a>
<a href="xml.zip">XML</a><a href="BJNR000010949.html">HTML</a>'''.encode('iso-8859-1')


class GermanyTests(unittest.TestCase):
    def test_bounded_catalog_and_excludes_cases(self):
        pages = {ROOT+'aktuell.html': b'<a href="Teilliste_A.html">A</a><a href="Teilliste_B.html">B</a>',
                 ROOT+'Teilliste_A.html': b'<a href="https://www.rechtsprechung-im-internet.de/x/index.html">case</a><a href="gg/index.html">GG</a><a href="bgb/index.html">BGB</a>'}
        seen=[]
        def fetch(u):
            seen.append(u)
            return pages[u],u
        self.assertEqual(GermanyAdapter().discover(fetch,1),[DETAIL])
        self.assertEqual(len(seen),2)

    def test_original_zip_preferred_and_unknown_dates(self):
        doc = GermanyAdapter().read_document(DETAIL, lambda u:(HTML,u))
        self.assertEqual(doc.file_urls,[ROOT+'gg/xml.zip',ROOT+'gg/GG.pdf'])
        self.assertEqual(doc.metadata['title']['value'],'Grundgesetz für Deutschland')
        self.assertEqual(doc.metadata['legal_identity']['value']['number'],'BJNR000010949')
        for key in ('published_at','updated_at','effective_region'):
            self.assertIsNone(doc.metadata[key]['value'])

    def test_unverified_redirect_and_wrong_page_rejected(self):
        for final in ('https://evil.test/gg/index.html', ROOT+'titelsuche.html'):
            with self.assertRaises(ValueError):
                GermanyAdapter().read_document(DETAIL, lambda u:(HTML,final))
        with self.assertRaises(ValueError):
            GermanyAdapter().read_document(DETAIL, lambda u:(b'<h1 class="headline">Error</h1><a href="x.pdf">PDF</a>',u))

    def test_no_cross_directory_or_external_originals(self):
        html=HTML.replace(b'xml.zip',b'https://evil.test/xml.zip').replace(b'GG.pdf',b'/bgb/BGB.pdf')
        with self.assertRaises(ValueError):
            GermanyAdapter().read_document(DETAIL,lambda u:(html,u))

    def test_limit_zero_never_fetches(self):
        self.assertEqual(GermanyAdapter().discover(lambda u:self.fail('unexpected network'),0),[])
