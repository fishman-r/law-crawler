"""贸易部数字分页、冻结筛选、类别门禁与原始附件归档边界。"""
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit
import unittest

from law_crawler.platform_adapters.trade import TradeAdapter

BASE = 'https://jdih.kemendag.go.id'


def catalog(number, total=5, last=3, shown=None, duplicate=False, query=''):
    shown = shown if shown is not None else (1 if number == 3 else 2)
    ids = [1, 2] if duplicate else list(range((number - 1) * 2 + 1, (number - 1) * 2 + shown + 1))
    cards = ''.join(f'<div class="card peraturan-card"><span class="badge">Keputusan Menteri</span><a href="/peraturan/keputusan-{i}">{i}</a></div>' for i in ids)
    # Next 故意跳页，Last 和数字页足以枚举连续页。
    links = f'<a aria-label="Next" href="/peraturan?{query}page={last}">Next</a><a aria-label="Last" href="/peraturan?{query}page={last}">Last</a>'
    return (f'<html>Menampilkan <strong>{shown}</strong> dari <strong>{total}</strong> peraturan{cards}{links}</html>').encode()


def detail(kind='Keputusan Menteri', title=None):
    title = title or kind + ' Perdagangan Republik Indonesia Nomor 1 Tahun 2026 tentang Harga'
    return f'''<html><h1>{title}</h1><table>
    <tr><th>Judul</th><td>{title}</td></tr><tr><th>Jenis Peraturan</th><td>{kind}</td></tr>
    <tr><th>Tipe Dokumen</th><td>Peraturan</td></tr></table>
    <a href="/uploads/peraturan/source.pdf">Dokumen</a>
    <a href="/uploads/peraturan/appendix.xlsx">Lampiran</a>
    <a href="/uploads/peraturan/abstrak.pdf">Abstrak</a>
    <a href="/frontend-new/app.zip">app</a></html>'''.encode()


class TradeBulkTests(unittest.TestCase):
    def fetch(self, url):
        number = int(parse_qs(urlsplit(url).query).get('page', ['1'])[0])
        self.calls.append(number)
        return catalog(number), url

    def setUp(self):
        self.calls = []
        self.adapter = TradeAdapter()

    def test_next_jump_does_not_skip_middle_page_and_keputusan_not_dropped(self):
        snapshots = []
        state = self.adapter.discover_bulk(self.fetch, on_page=snapshots.append)
        self.assertEqual(self.calls, [1, 2, 3])
        self.assertTrue(state['complete'])
        self.assertEqual(len(state['details']), 5)
        self.assertTrue(any('keputusan' in url for url in state['details']))
        self.assertEqual(len(snapshots), 3)

    def test_limit_and_json_checkpoint_resume(self):
        first = self.adapter.discover_bulk(self.fetch, max_pages=1)
        self.assertFalse(first['complete'])
        self.assertTrue(first['limit_reached'])
        second = self.adapter.discover_bulk(self.fetch, checkpoint=deepcopy(first))
        self.assertEqual(self.calls, [1, 2, 3])
        self.assertTrue(second['complete'])

    def test_repeat_first_page_at_last_page_is_incomplete(self):
        def fetch(url):
            n = int(parse_qs(urlsplit(url).query)['page'][0])
            return catalog(n, duplicate=n == 3, shown=2 if n == 3 else None), url
        state = self.adapter.discover_bulk(fetch)
        self.assertFalse(state['complete'])
        self.assertIn('repeated_or_overlapping_page', state['streams']['peraturan']['stop_reasons'])
        self.assertEqual(len(state['details']), 4)

    def test_changed_total_or_terminal_page_does_not_pass(self):
        for changed_total, changed_last in ((6, 3), (5, 4)):
            def fetch(url):
                n = int(parse_qs(urlsplit(url).query)['page'][0])
                return catalog(n, total=changed_total if n == 2 else 5,
                    last=changed_last if n == 2 else 3), url
            state = self.adapter.discover_bulk(fetch)
            self.assertFalse(state['complete'])
            self.assertIn('catalog_changed', state['streams']['peraturan']['stop_reasons'])

    def test_filter_and_sort_preserved_and_redirect_changes_rejected(self):
        self.adapter.bulk_catalog_url = BASE + '/peraturan?search=harga&order=terlama'
        seen = []
        def fetch(url):
            query = parse_qs(urlsplit(url).query)
            seen.append(query)
            return catalog(int(query['page'][0]), query='search=harga&order=terlama&'), url
        self.assertTrue(self.adapter.discover_bulk(fetch)['complete'])
        self.assertTrue(all(q['search'] == ['harga'] and q['order'] == ['terlama'] for q in seen))
        changed = self.adapter.discover_bulk(lambda url: (catalog(1), BASE + '/peraturan?page=1'))
        self.assertFalse(changed['complete'])
        self.assertIn('改变页码或筛选排序', changed['pages'][0]['reason'])

    def test_nonofficial_checkpoint_rejected_before_network(self):
        first = self.adapter.discover_bulk(self.fetch, max_pages=1)
        first['streams']['peraturan']['pending_urls'][0] = 'https://evil.test/peraturan?page=2'
        with self.assertRaisesRegex(ValueError, 'checkpoint'):
            self.adapter.discover_bulk(lambda url: self.fail('must not fetch'), checkpoint=first)

    def test_bulk_archives_administrative_attachments_but_keeps_processing_pending(self):
        fetch = lambda url: (detail(), url)
        url = BASE + '/peraturan/keputusan-1'
        parsed = self.adapter.read_document_bulk(url, fetch)
        self.assertEqual(len(parsed.file_urls), 2)
        self.assertEqual(parsed.metadata['document_type']['value'], 'administrative_decision')
        self.assertEqual(parsed.metadata['processing_gate'], 'pending_document_type_review')
        with self.assertRaisesRegex(ValueError, '正式法规标题'):
            self.adapter.read_document(url, fetch)

    def test_formal_regulation_still_eligible_and_draft_is_excluded(self):
        parsed = self.adapter.read_document_bulk(BASE + '/peraturan/peraturan-1', lambda url: (detail('Peraturan Menteri'), url))
        self.assertEqual(parsed.metadata['processing_gate'], 'eligible')
        draft = self.adapter.read_document_bulk(BASE + '/peraturan/rancangan-1', lambda url: (detail('Rancangan Peraturan Menteri'), url))
        self.assertEqual(draft.file_urls, [])
        self.assertEqual(draft.metadata['processing_gate'], 'excluded_draft')

    def test_draft_category_is_counted_separately_not_silently_lost(self):
        def fetch(url):
            body = catalog(1, total=2, last=1).replace(b'Keputusan Menteri', b'Rancangan Peraturan', 1)
            return body, url
        state = self.adapter.discover_bulk(fetch)
        self.assertTrue(state['complete'])
        self.assertEqual(len(state['details']), 1)
        self.assertEqual(len(state['excluded_details']), 1)



    def test_catalog_sort_or_modified_date_change_keeps_batch_incomplete(self):
        def fetch(url):
            n = int(parse_qs(urlsplit(url).query)['page'][0])
            date = '2026-10-21' if n == 1 else '2026-10-22'
            return catalog(n).replace(b'<html>', ('<html><meta name="dateModified" content="'+date+'">').encode()), url
        state = self.adapter.discover_bulk(fetch)
        self.assertFalse(state['complete'])
        self.assertIn('catalog_changed', state['streams']['peraturan']['stop_reasons'])


    def test_bulk_explicit_text_and_scan_attachments_are_archived(self):
        html = detail().replace(b'</html>', b'<a href="/uploads/peraturan/raw.xml">Unduh</a><a href="/uploads/peraturan/scan.tiff">Lampiran</a><a href="/logo.png">Logo</a></html>')
        parsed = self.adapter.read_document_bulk(BASE + '/peraturan/keputusan-1', lambda url: (html, url))
        self.assertEqual(len(parsed.file_urls), 4)
        self.assertTrue(any(url.endswith('.xml') for url in parsed.file_urls))
        self.assertTrue(any(url.endswith('.tiff') for url in parsed.file_urls))
        self.assertFalse(any(url.endswith('logo.png') for url in parsed.file_urls))

if __name__ == '__main__':
    unittest.main()
