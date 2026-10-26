import unittest
from law_crawler.platform_adapters.census_finance import source_availability


class FinanceAvailabilityTests(unittest.TestCase):
    def record(self):
        return {'url': 'https://jdih.kemenkeu.go.id/dok/id', 'official_metadata': {
            'bentuk': 'Monografi Hukum', 'judul': 'Book title', 'full_text_pdf': '',
            'blocks': [], 'jumlah_pasal': 0,
            'image': 'https://perpustakaan.kemenkeu.go.id/img/FileCover/book.jpg'}}

    def test_cover_and_shell_are_not_original(self):
        result = source_availability(self.record(), '<img src="not-found.abc.png">')
        self.assertIn('no_original_reason', result)
        self.assertFalse(result['source_availability']['evidence']['cover_is_original'])
        self.assertTrue(result['source_availability']['evidence']['detail_not_found_shell_observed'])

    def test_fulltext_not_misclassified(self):
        r = self.record(); r['official_metadata']['full_text_pdf'] = '/book.pdf'
        self.assertEqual(source_availability(r), {})

    def test_variant_has_fulltext(self):
        r = self.record(); r['catalogue_variants'] = [dict(r['official_metadata'], blocks=['text'])]
        self.assertEqual(source_availability(r), {})

    def test_discovered_link_not_marked_missing(self):
        result = source_availability(self.record(), file_urls=['https://jdih.kemenkeu.go.id/book.pdf'])
        self.assertNotIn('no_original_reason', result)

    def test_regulation_and_incomplete_catalogue_unchanged(self):
        r = self.record(); r['official_metadata']['bentuk'] = 'Peraturan Menteri'
        self.assertEqual(source_availability(r), {})
        self.assertEqual(source_availability({'url': r['url']}), {})
