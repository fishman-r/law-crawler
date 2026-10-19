"""国家平台的离线行为测试；不把合成 HTML 当作现场下载证据。"""
from pathlib import Path
import unittest
import json
from urllib.error import HTTPError, URLError

from law_crawler.platform_adapters.national import ADAPTERS


FIXTURES = Path(__file__).parent / 'fixtures' / 'national'


def response(html):
    return lambda url: (html.encode(), url)


class NationalAdaptersTests(unittest.TestCase):
    def test_peraturan_discovery_deduplicates_and_bounds_official_details(self):
        adapter = ADAPTERS['peraturan.go.id']
        html = '''<a href="/id/pp-no-4-tahun-2026">PP</a>
        <a href="/id/pp-no-4-tahun-2026#file">same</a>
        <a href="https://outside.example/id/pp-no-1-tahun-2026">other host</a>
        <a href="/berita/item">news</a><a href="/id/uu-no-1-tahun-2026">UU</a>'''
        self.assertEqual(adapter.discover(response(html), 1),
                         ['https://peraturan.go.id/id/pp-no-4-tahun-2026'])
        self.assertEqual(adapter.discover(response(html), 3),
                         ['https://peraturan.go.id/id/pp-no-4-tahun-2026',
                          'https://peraturan.go.id/id/uu-no-1-tahun-2026'])

    def test_peraturan_www_fallback_is_bounded_and_does_not_disable_tls(self):
        adapter = ADAPTERS['peraturan.go.id']
        calls = []
        def fetch(url):
            calls.append(url)
            if url == adapter.entry_urls[0]:
                raise URLError('TLS handshake failure')
            return b'<a href="/id/pp-no-4-tahun-2026">PP</a>', url
        self.assertEqual(adapter.discover(fetch, 1),
                         ['https://www.peraturan.go.id/id/pp-no-4-tahun-2026'])
        self.assertEqual(calls, list(adapter.entry_urls))

    def test_blocked_entry_preserves_failure_instead_of_silent_empty_result(self):
        adapter = ADAPTERS['bpk.go.id']
        def fetch(url):
            raise HTTPError(url, 403, 'Forbidden', {}, None)
        with self.assertRaisesRegex(OSError, '403.*Forbidden'):
            adapter.discover(fetch, 3)

    def test_bpk_reads_download_not_preview_and_keeps_metadata_provenance(self):
        adapter = ADAPTERS['bpk.go.id']
        detail = 'https://peraturan.bpk.go.id/Details/352154/pmk-no-53-tahun-2026'
        doc = adapter.read_document(detail, response((FIXTURES / 'bpk-detail-synthetic.html').read_text()))
        self.assertEqual(doc.pdf_urls,
                         ['https://peraturan.bpk.go.id/Download/415963/2026pmkeuangan053.pdf'])
        self.assertEqual(doc.metadata['published_at']['value'], '2026-07-29')
        self.assertEqual(doc.metadata['published_at']['source_label'], 'Tanggal Pengundangan')
        self.assertEqual(doc.metadata['published_at']['source_url'], detail)
        self.assertIsNone(doc.metadata['updated_at']['value'])
        self.assertIsNone(doc.metadata['effective_region']['value'])
        identity = doc.metadata['legal_identity']['value']
        self.assertEqual(identity, {'jurisdiction': 'ID', 'issuer': 'Indonesia, Kementerian Keuangan',
                                    'type': 'Peraturan Menteri Keuangan', 'number': '53', 'year': '2026'})
        self.assertEqual(doc.metadata['document_type']['value'], 'regulation')

    def test_bpk_discovery_requires_official_details_route(self):
        html = '<a href="/Details/352154/pmk-no-53-tahun-2026">PMK</a><a href="/Download/415963/a.pdf">PDF</a>'
        self.assertEqual(ADAPTERS['bpk.go.id'].discover(response(html), 20),
                         ['https://peraturan.bpk.go.id/Details/352154/pmk-no-53-tahun-2026'])

    def test_jdihn_three_column_metadata_and_download_roles(self):
        adapter = ADAPTERS['jdihn.go.id']
        detail = 'https://jdihn.go.id/pencarian/detail/2194567'
        doc = adapter.read_document(detail, response((FIXTURES / 'jdihn-detail-synthetic.html').read_text()))
        self.assertEqual(doc.detail_url, detail)
        self.assertEqual(doc.pdf_urls, ['https://jdihn.go.id/files/111/regulation.pdf',
                                       'https://jdihn.go.id/file/download?id_anggota=111&id_dokumen=22'])
        self.assertEqual(doc.metadata['published_at']['value'], '2026-01-23')
        self.assertEqual(doc.metadata['legal_identity']['value']['issuer'], 'Indonesia. Kementerian Perdagangan')
        self.assertEqual(doc.metadata['member_detail_url']['value'],
                         'https://jdih.kemendag.go.id/peraturan/detail/3276/1')
        self.assertEqual(doc.metadata['member_detail_url']['source_url'], detail)
        self.assertIsNone(doc.metadata['effective_region']['value'])

    def test_jdihn_member_download_allowlist_does_not_allow_all_go_id(self):
        detail = 'https://jdihn.go.id/pencarian/detail/2194567'
        html = '''<h1>Peraturan Menteri Nomor 2 Tahun 2026</h1><table>
        <tr><td>Unduhan</td><td>:</td><td><a href="https://jdih.kemendag.go.id/uploads/a.pdf">Original</a>
        <a href="https://unverified.go.id/a.pdf">Other</a><a href="https://outside.example/a.pdf">Other</a></td></tr>
        </table>'''
        doc = ADAPTERS['jdihn.go.id'].read_document(detail, response(html))
        self.assertEqual(doc.pdf_urls, ['https://jdih.kemendag.go.id/uploads/a.pdf'])

    def test_jdihn_preserves_index_identity_and_does_not_follow_members_during_read(self):
        adapter = ADAPTERS['jdihn.go.id']
        html = '<a href="/pencarian/detail/2194567/title">R</a><a href="https://jdih.kemendag.go.id/pencarian/detail/99">M</a>'
        self.assertEqual(adapter.discover(response(html), 20),
                         ['https://jdihn.go.id/pencarian/detail/2194567/title'])
        with self.assertRaisesRegex(ValueError, '本平台索引详情'):
            adapter.read_document('https://jdih.kemendag.go.id/peraturan/detail/3276/1', response(html))

    def test_draft_and_decision_not_misclassified_as_regulation(self):
        adapter = ADAPTERS['jdihn.go.id']
        for kind in ['KEPUTUSAN KEMENTERIAN', 'Rancangan Peraturan', 'Putusan Pengadilan']:
            html = '<h1>Example document</h1><table><tr><td>Jenis Dokumen</td><td>:</td><td>' + kind + '</td></tr></table>'
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, '非本适配器法规类型'):
                adapter.read_document('https://jdihn.go.id/pencarian/detail/1', response(html))

    def test_unexpected_redirect_is_not_read_as_official_metadata(self):
        adapter = ADAPTERS['peraturan.go.id']
        with self.assertRaisesRegex(ValueError, '未允许的主机'):
            adapter.read_document('https://peraturan.go.id/id/pp-no-4-tahun-2026',
                                  lambda url: (b'<h1>R</h1>', 'https://outside.example/a'))

    def test_success_status_access_denied_page_is_not_a_regulation(self):
        adapter = ADAPTERS['peraturan.go.id']
        with self.assertRaisesRegex(ValueError, '未提供可识别的法规'):
            adapter.read_document('https://peraturan.go.id/id/pp-no-4-tahun-2026',
                                  response('<h1>Access Denied</h1><a href="/files/help.pdf">Help</a>'))

    def test_pdf_labels_and_filenames_exclude_summaries_but_keep_lampiran(self):
        adapter = ADAPTERS['peraturan.go.id']
        html = '''<h1>Peraturan Pemerintah Nomor 4 Tahun 2026</h1>
        <a href="/files/pp-no-4-tahun-2026.pdf">Download</a>
        <a href="/files/other.pdf">Abstrak</a>
        <a href="/files/FAQ-pp-no-4-tahun-2026.pdf">Download</a>
        <a href="/files/pp-no-4-tahun-2026_abstrak.pdf">Download</a>
        <a href="/files/chinese.pdf">摘要</a>
        <a href="/files/lampiran-pp-no-4-tahun-2026.pdf">Lampiran</a>
        <table><tr><td>Abstrak</td><td><a href="/files/summary.pdf">Download</a></td></tr></table>'''
        doc = adapter.read_document('https://peraturan.go.id/id/pp-no-4-tahun-2026', response(html))
        self.assertEqual(doc.pdf_urls, ['https://peraturan.go.id/files/pp-no-4-tahun-2026.pdf',
                                       'https://peraturan.go.id/files/lampiran-pp-no-4-tahun-2026.pdf'])

    def test_bpk_abstract_download_route_not_counted_as_original(self):
        adapter = ADAPTERS['bpk.go.id']
        html = '''<h1>Peraturan Menteri Keuangan Nomor 53 Tahun 2026</h1>
        <a href="/Download/415963/2026pmkeuangan053.pdf">Download</a>
        <a href="/Download/415964/2026pmkeuangan053_ABSTRAK.pdf">Download</a>
        <a href="/Download/415965/description.pdf">Abstrak</a>'''
        doc = adapter.read_document('https://peraturan.bpk.go.id/Details/352154/pmk-no-53-tahun-2026', response(html))
        self.assertEqual(doc.pdf_urls, ['https://peraturan.bpk.go.id/Download/415963/2026pmkeuangan053.pdf'])


class JDIHNExperimentalTests(unittest.TestCase):
    detail = 'https://dev-jdihn.bphn.go.id/doc/2249260'
    # Reduced public JSON observed in the real 2026-10-14 HTML; not a live-network test.
    item = dict(id=2249260, umbrellaType='peraturan', type='UNDANG-UNDANG',
                canonicalTitle='Undang-Undang Nomor 1 Tahun 2026 Tentang PENYESUAIAN PIDANA',
                promulgationDate='2026-01-02', enactedDate=None,
                attachmentUrl='https://jdih.setneg.go.id/api/hukumproduk/pdf?l=uploads&fl=P20495&f=Salinan%20UU%20Nomor%201%20Tahun%202026.pdf')

    def html(self, **changes):
        item = dict(self.item, **changes)
        payload = '5a:["$",null,{"document":' + json.dumps(item) + ',"turnstileSiteKey":"public-key"}]'
        return '<script>self.__next_f.push(' + json.dumps([1, payload]) + ')</script>'

    def test_opt_in_discovery_copy_and_host_path_bounds(self):
        original = ADAPTERS['jdihn.go.id']
        adapter = original.with_experimental_sources()
        calls = []
        def fetch(url):
            calls.append(url)
            if url.startswith('https://jdihn.go.id'):
                raise URLError('TLS')
            return (('<a href="/doc/2249260">R</a><a href="/doc/2249260">R</a>'
                     '<a href="/doc/not-number">X</a><a href="https://evil.example/doc/1">X</a>').encode(), url)
        with self.assertRaises(OSError):
            original.discover(fetch, 2)
        self.assertNotIn('https://dev-jdihn.bphn.go.id/', calls)
        self.assertEqual(adapter.discover(fetch, 2), [self.detail])
        self.assertFalse(original.experimental_sources)
        self.assertEqual(adapter.production_discovery['status'], 'failed')
        self.assertIn('TLS', adapter.production_discovery['error'])

    def test_production_success_does_not_probe_experimental(self):
        calls = []
        def fetch(url):
            calls.append(url)
            return b'<a href="/pencarian/detail/1">R</a>', url
        a = ADAPTERS['jdihn.go.id'].with_experimental_sources()
        self.assertEqual(a.discover(fetch, 1), ['https://jdihn.go.id/pencarian/detail/1'])
        self.assertEqual(len(calls), 1)

    def test_public_json_dates_route_and_unknowns(self):
        doc = ADAPTERS['jdihn.go.id'].read_document(self.detail, response(self.html()))
        self.assertEqual(doc.pdf_urls, [self.item['attachmentUrl']])
        self.assertEqual(doc.metadata['published_at']['value'], '2026-01-02')
        self.assertIsNone(doc.metadata['effective_region']['value'])
        self.assertIsNone(doc.metadata['updated_at']['value'])
        self.assertIsNone(doc.metadata['enacted_at']['value'])
        self.assertEqual(doc.metadata['source_route']['value'], 'experimental_official_fallback')
        self.assertEqual(doc.metadata['title']['source_url'], self.detail)

    def test_rejects_non_regulations_drafts_unknown_members_and_wrong_identity(self):
        for changes in [dict(umbrellaType='putusan'), dict(type='Rancangan Peraturan'),
                        dict(canonicalTitle='Draft Undang-Undang Nomor 1 Tahun 2026'),
                        dict(attachmentUrl='https://unverified.go.id/a.pdf'),
                        dict(attachmentUrl='https://jdih.setneg.go.id/not-the-api.pdf'), dict(id=99)]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ADAPTERS['jdihn.go.id'].read_document(self.detail, response(self.html(**changes)))

    def test_never_executes_javascript_and_rejects_conflicting_documents(self):
        for html in ['<script>self.__next_f.push(eval("1"))</script>',
                     self.html() + self.html(promulgationDate='2025-01-01')]:
            with self.assertRaises(ValueError):
                ADAPTERS['jdihn.go.id'].read_document(self.detail, response(html))


if __name__ == '__main__':
    unittest.main()
