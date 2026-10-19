import unittest
from law_crawler.platform_adapters.eu_other import LegifranceAdapter, NormattivaAdapter, WettenAdapter


class OtherEUTests(unittest.TestCase):
    def test_france_challenge_not_success(self):
        with self.assertRaisesRegex(ValueError, '未返回'):
            LegifranceAdapter().discover(lambda u: (b'<html>verify</html>', u), 1)

    def test_france_filters_cases(self):
        html = b'<a href="/jorf/id/JORFTEXT123">law</a><a href="/juri/id/JURITEXT123">case</a>'
        self.assertEqual(LegifranceAdapter().discover(lambda u: (html, u), 2), ['https://www.legifrance.gouv.fr/jorf/id/JORFTEXT123'])

    def test_italy_dynamic_discovery(self):
        html = b'<a href="/uri-res/N2Ls?urn:nir:stato:legge:2026-01-01;1">law</a><a href="/showNewsDetail?id=1">news</a>'
        self.assertEqual(len(NormattivaAdapter().discover(lambda u: (html, u), 2)), 1)

    def test_italy_metadata_matching_export(self):
        html = '''<meta property="eli:title" content="Example"><meta property="eli:id_local" content="26G00178"><meta property="eli:type_document" resource="types#DECRETO-LEGGE"><meta property="eli:date_publication" content="2026-09-10"><a href="/do/atto/caricaAKN?dataGU=20260910&amp;codiceRedaz=26G00178&amp;dataVigenza=20261014">export</a><a href="/do/atto/caricaAKN?dataGU=20260910&amp;codiceRedaz=OTHER">wrong</a>'''
        doc = NormattivaAdapter().read_document('https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.legge:2026-09-10;157', lambda u: (html.encode(), u))
        self.assertEqual(len(doc.file_urls), 1)
        self.assertEqual(doc.metadata['published_at']['value'], '2026-09-10')
        self.assertIsNone(doc.metadata['updated_at']['value'])
        self.assertIsNone(doc.metadata['effective_region']['value'])

    def test_external_redirect_rejected(self):
        with self.assertRaisesRegex(ValueError, '跳转'):
            NormattivaAdapter().discover(lambda u: (b'', 'https://example.org/'), 1)

    def test_netherlands_date_from_form(self):
        calls = []
        def fetch(url):
            calls.append(url)
            if url.endswith('/zoeken'):
                return b'<input name="ZoekOp_Geldigheidsdatum" value="2026-10-14">', url
            return b'<a href="/BWBR0019384/2007-01-01/0">law</a>', url
        self.assertEqual(len(WettenAdapter().discover(fetch, 1)), 1)
        self.assertIn('/d/14-10-2026/dx/0', calls[1])

    def test_netherlands_preview_not_original(self):
        with self.assertRaisesRegex(ValueError, 'POST'):
            WettenAdapter().read_document('https://wetten.overheid.nl/BWBR0019384/2007-01-01/0', lambda u: (b'<html>law preview</html>', u))


if __name__ == '__main__':
    unittest.main()
