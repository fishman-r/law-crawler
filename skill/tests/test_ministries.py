import unittest
from pathlib import Path

from law_crawler.platform_adapters.ministries import ADAPTERS


FIXTURES = Path(__file__).parent / 'fixtures' / 'ministries'


class MinistryAdaptersTests(unittest.TestCase):
    def page(self, name, suffix):
        return (FIXTURES / f'{name}-{suffix}.html').read_bytes()

    def test_real_lists_discover_regulations_not_articles_or_decisions(self):
        for name, expected in [('kemkes', '/documents/peraturan-menteri-kesehatan-nomor-6-tahun-2026'),
                               ('kemenkeu', '/dok/pmk-74-tahun-2026'),
                               ('bkpm', '/id/document/peraturan-menteri-investasi-dan-hilirisasikepala-badan-koordinasi-penanaman-modal-nomor-6-tahun-2026')]:
            with self.subTest(platform=name):
                adapter = ADAPTERS[name + '.go.id']
                calls = []
                def fetch(url):
                    calls.append(url)
                    return self.page(name, 'list'), url
                details = adapter.discover(fetch, 1)
                self.assertEqual(len(details), 1)
                self.assertIn(expected, details[0])
                self.assertEqual(calls, list(adapter.entry_urls))

    def test_real_details_dates_identity_primary_pdf_and_explicit_unknowns(self):
        for name, published, number, expected_file in [
                ('kemkes', '2026-06-12', '6', '2026permenkes006.pdf'),
                ('kemenkeu', '2026-10-08', '74', '2026pmkeuangan74.pdf'),
                ('bkpm', '2026-07-16', '6', '2026Permeninveshil006-.pdf')]:
            with self.subTest(platform=name):
                adapter = ADAPTERS[name + '.go.id']
                url = adapter.discover(lambda u: (self.page(name, 'list'), u), 1)[0]
                doc = adapter.read_document(url, lambda u: (self.page(name, 'detail'), u))
                self.assertEqual(len(doc.pdf_urls), 1)  # 摘要不是法规正文。
                self.assertTrue(doc.pdf_urls[0].endswith(expected_file))
                self.assertEqual(doc.metadata['document_type']['value'], 'regulation')
                self.assertEqual(doc.metadata['published_at']['value'], published)
                self.assertEqual(doc.metadata['legal_identity']['value']['number'], number)
                self.assertEqual(doc.metadata['legal_identity']['value']['year'], '2026')
                self.assertEqual(doc.metadata['legal_identity']['value']['jurisdiction'], 'ID')
                raw = doc.metadata['legal_identity']['raw']
                for label in ('Judul', 'Judul Dokumen'):
                    if label in raw:
                        self.assertEqual(raw[label], doc.metadata['title']['raw'])
                self.assertEqual(doc.metadata['published_at']['source_url'], url)
                self.assertIsNone(doc.metadata['updated_at']['value'])
                self.assertIsNone(doc.metadata['effective_region']['value'])

    def test_preview_file_cannot_leave_allowed_hosts(self):
        adapter = ADAPTERS['kemkes.go.id']
        html = self.page('kemkes', 'detail').replace(
            b'https%3A%2F%2Fjdih.kemkes.go.id%2Fstorage', b'https%3A%2F%2Funtrusted.example%2Fstorage')
        with self.assertRaisesRegex(ValueError, '正文文件'):
            adapter.read_document(adapter.entry_urls[0], lambda u: (html, u))

    def test_kemenkeu_explicit_html_fulltext_is_an_original(self):
        adapter = ADAPTERS['kemenkeu.go.id']
        html = self.page('kemenkeu', 'detail')
        # UU40/2007 uses the same observed official fulltext route, with .htm.
        html = html.replace(b'/api/download/d860feb1-5e64-401d-b50e-8d57b7b94a7b/2026pmkeuangan74.pdf', b'/api/download/fulltext/2007/40TAHUN2007UU.htm')
        doc = adapter.read_document('https://jdih.kemenkeu.go.id/dok/uu-40-tahun-2007',
                                    lambda u: (html, u))
        self.assertEqual(len(doc.file_urls), 1)
        self.assertIn('/api/download/fulltext/', doc.file_urls[0])
        self.assertTrue(doc.file_urls[0].endswith('40TAHUN2007UU.htm'))

    def test_html_navigation_is_not_a_fulltext_download(self):
        adapter = ADAPTERS['kemenkeu.go.id']
        html = self.page('kemenkeu', 'detail').replace(
            b'/api/download/d860feb1-5e64-401d-b50e-8d57b7b94a7b/2026pmkeuangan74.pdf', b'/news/40TAHUN2007UU.htm')
        with self.assertRaisesRegex(ValueError, '正文文件'):
            adapter.read_document(adapter.entry_urls[0], lambda u: (html, u))

    def test_misleading_slug_does_not_override_document_type(self):
        adapter = ADAPTERS['bkpm.go.id']
        html = self.page('bkpm', 'detail').replace(b'<dd>Peraturan</dd>', b'<dd>Monografi Hukum</dd>')
        with self.assertRaisesRegex(ValueError, '法规类型'):
            adapter.read_document('https://jdih.bkpm.go.id/id/document/peraturan-misleading', lambda u: (html, u))

    def test_list_excludes_external_hosts_and_draft_slugs(self):
        adapter = ADAPTERS['kemkes.go.id']
        html = b'<a href="https://elsewhere.example/documents/peraturan-1">x</a><a href="/documents/rancangan-peraturan-2">y</a><a href="/documents/peraturan-3">z</a>'
        self.assertEqual(adapter.discover(lambda u: (html, u), 10),
                         ['https://jdih.kemkes.go.id/documents/peraturan-3'])

    def test_invalid_date_is_preserved_without_invention(self):
        adapter = ADAPTERS['kemkes.go.id']
        html = self.page('kemkes', 'detail').replace(b'12 Jun 2026', b'32 Jun 2026')
        doc = adapter.read_document(adapter.entry_urls[0], lambda u: (html, u))
        self.assertIsNone(doc.metadata['published_at']['value'])
        self.assertEqual(doc.metadata['published_at']['raw'], '32 Jun 2026')
        self.assertEqual(doc.metadata['published_at']['status'], 'unparsed')


if __name__ == '__main__':
    unittest.main()
