import unittest

from law_crawler.platform_adapters.census_minor import (
    bkpm_storage_document, missing_original_evidence, reject_access_error)
from law_crawler.platform_adapters.trade import TradeAdapter


class MinorDiscoveryTests(unittest.TestCase):
    def test_only_registered_bkpm_storage_pdf(self):
        good = 'https://minio-jb.globalpiranti.co.id/jdih/jdih/example.pdf'
        self.assertEqual(bkpm_storage_document(good), good)
        for bad in (good.replace('/jdih/jdih/', '/elsewhere/'),
                    good.replace('https:', 'http:'), good + '?target=elsewhere',
                    good.replace('.co.id/', '.co.id.evil.example/'),
                    good.replace('https://', 'https://user:pass@')):
            self.assertIsNone(bkpm_storage_document(bad))

    def test_http200_rejection_not_missing_original(self):
        with self.assertRaisesRegex(ValueError, 'access_blocked'):
            reject_access_error('<html><title>Request Rejected</title></html>')
        reject_access_error('<h1>A regulation about Request Rejected notices</h1>')

    def test_official_missing_evidence(self):
        self.assertEqual(missing_original_evidence('kemendag.go.id',
            '<p>Dokumen peraturan tidak tersedia.</p>')['code'],
            'official_primary_document_unavailable')
        self.assertIsNone(missing_original_evidence('kemendag.go.id', '<p>Document</p>'))

    def test_extensionless_translation_preserves_missing_primary(self):
        base = 'https://jdih.kemendag.go.id/peraturan/example'
        link = '/peraturan/download/1070b6a7-0fdf-4b00-bb6f-4f93741ddd2e/file_peraturan_en'
        html = ('<h1>Peraturan Menteri Perdagangan Nomor 30 Tahun 2010</h1>'
                '<div id="peraturan-pane">Dokumen peraturan tidak tersedia.</div>'
                '<a href="' + link + '">Peraturan [EN]</a>'
                '<a href="/frontend-new/files/JDIH_Kemendag_RI.zip">Download</a>')
        doc = TradeAdapter().read_document_bulk(base, lambda _: (html.encode(), base))
        self.assertEqual(doc.file_urls, ['https://jdih.kemendag.go.id' + link])
        self.assertTrue(doc.metadata['primary_original_missing'])
        self.assertEqual(doc.metadata['file_roles'][doc.file_urls[0]]['role'], 'official_translation')

    def test_extensionless_foreign_download_not_allowed(self):
        base = 'https://jdih.kemendag.go.id/peraturan/example'
        html = '<a href="https://other.example/peraturan/download/1070b6a7-0fdf-4b00-bb6f-4f93741ddd2e/file_peraturan_en">Peraturan [EN]</a>'
        doc = TradeAdapter().read_document_bulk(base, lambda _: (html.encode(), base))
        self.assertEqual(doc.file_urls, [])


if __name__ == '__main__':
    unittest.main()
