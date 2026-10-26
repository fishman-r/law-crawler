"""MA 全量目录行为：真实分页、断点、文种和未穷尽证据。"""
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from law_crawler.platform_adapters.institutions import MAAdapter

BASE = 'https://jdih.mahkamahagung.go.id'


def page(kind, prefix, number, next_page=None):
    html = f'<a href="/legal-product/{prefix}-nomor-{number}-tahun-2026/detail">正式文献</a>'
    html += '<a href="/legal-product/putusan-nomor-1/detail">Putusan</a>'
    html += f'<a href="/dokumen?jenis%5B0%5D={kind}&amp;page={number}">{number}</a>'
    if next_page:
        html += f'<a href="/dokumen?jenis%5B0%5D={kind}&amp;page={next_page}">Next</a>'
    return html.encode()


class MABulkTests(unittest.TestCase):
    def test_walks_all_visible_pages_filters_judgments_and_checkpoints(self):
        snapshots, calls = [], []
        def fetch(url):
            calls.append(url)
            q = parse_qs(urlsplit(url).query)
            kind = q['jenis[0]'][0]
            number = int(q['page'][0])
            prefix = MAAdapter.bulk_kinds[kind][0]
            return page(kind, prefix, number, number + 1 if number < 3 else None), url
        result = MAAdapter().discover_bulk(fetch, on_page=snapshots.append)
        self.assertTrue(result['complete'])
        self.assertEqual(len(result['details']), 6)
        self.assertEqual(len(calls), 6)
        self.assertFalse(any('putusan' in u for u in result['details']))
        self.assertEqual(len(snapshots), 6)
        self.assertEqual(len(snapshots[0]['pages']), 1)  # 独立快照，未被后续 mutation 改写

    def test_limit_and_resume_do_not_claim_exhaustion(self):
        def fetch(url):
            q = parse_qs(urlsplit(url).query)
            kind, n = q['jenis[0]'][0], int(q['page'][0])
            return page(kind, MAAdapter.bulk_kinds[kind][0], n, 2 if n == 1 else None), url
        adapter = MAAdapter()
        first = adapter.discover_bulk(fetch, max_pages=1)
        self.assertFalse(first['complete'])
        self.assertTrue(first['limit_reached'])
        final = adapter.discover_bulk(fetch, checkpoint=first)
        self.assertTrue(final['complete'])
        self.assertEqual(final['requests_this_run'], 3)
        self.assertEqual(len(first['pages']), 1)

    def test_network_failure_is_pending_and_retried(self):
        adapter = MAAdapter()
        first = adapter.discover_bulk(lambda _: (_ for _ in ()).throw(OSError('403')))
        self.assertFalse(first['complete'])
        self.assertEqual(len(first['pending_urls']), 2)
        final = adapter.discover_bulk(lambda u: (b'<p>Data tidak ditemukan</p>', u), checkpoint=first)
        self.assertTrue(final['complete'])
        self.assertTrue(any(p['status'] == 'failed' for p in final['pages']))

    def test_repeated_page_and_no_pagination_stay_unverified(self):
        def fetch(url):
            q = parse_qs(urlsplit(url).query)
            kind = q['jenis[0]'][0]
            return page(kind, MAAdapter.bulk_kinds[kind][0], 1, 2), url
        result = MAAdapter().discover_bulk(fetch)
        self.assertFalse(result['complete'])
        self.assertTrue(any(p['status'] == 'repeated_page' for p in result['pages']))
        result = MAAdapter().discover_bulk(lambda u: (b'<a href="/legal-product/perma-one/detail">x</a>', u))
        self.assertFalse(result['complete'])

    def test_filter_escape_and_redirect_cannot_appear_exhausted(self):
        result = MAAdapter().discover_bulk(lambda u: (b'x', BASE + '/dokumen'))
        self.assertFalse(result['complete'])
        checkpoint = MAAdapter().discover_bulk(lambda u: (b'<p>Data tidak ditemukan</p>', u))
        checkpoint['streams']['peraturan-mahkamah-agung']['pending_urls'] = ['https://evil.invalid/dokumen']
        with self.assertRaisesRegex(ValueError, '非官方'):
            MAAdapter().discover_bulk(lambda u: (b'', u), checkpoint=checkpoint)

    def test_sema_identity_category_and_original_only(self):
        fixture = Path(__file__).parent / 'fixtures/institutions/ma-jdih-parser.html'
        html = fixture.read_bytes().replace(b'Peraturan Mahkamah Agung', b'Surat Edaran Mahkamah Agung').replace(b'PERMA', b'SEMA').replace(b'perma-', b'sema-')
        url = BASE + '/legal-product/sema-nomor-3-tahun-2026/detail'
        doc = MAAdapter().read_document(url, lambda _: (html, url))
        self.assertEqual(doc.metadata['document_type']['value'], 'judicial_guidance')
        self.assertEqual(doc.metadata['legal_identity']['value']['type'], 'Surat Edaran Mahkamah Agung')
        self.assertEqual(doc.pdf_urls, [url.replace('/detail', '/download/dokumen')])
        self.assertEqual(doc.metadata['published_at']['value'], '2026-07-09')
        bad = html.replace(b'Surat Edaran Mahkamah Agung', b'Putusan Mahkamah Agung')
        with self.assertRaises(ValueError):
            MAAdapter().read_document(url, lambda _: (bad, url))


if __name__ == '__main__':
    unittest.main()
