"""贸易部正文身份门槛；合成拒绝页不作为线上下载证据。"""
from pathlib import Path
import unittest

from law_crawler.platform_adapters.trade import TradeAdapter


DETAIL = 'https://jdih.kemendag.go.id/peraturan/peraturan-menteri-perdagangan-nomor-25-tahun-2026'
TITLE = 'Peraturan Menteri Perdagangan Nomor 25 Tahun 2026'


class TradeTypeTests(unittest.TestCase):
    def read(self, html, final=DETAIL):
        return TradeAdapter().read_document(DETAIL, lambda u: (html.encode(), final))

    def test_official_metadata_fixtures_preserve_identity_dates(self):
        for number, published in [('25', '2026-10-12'), ('24', '2026-09-18'), ('23', '2026-09-09')]:
            html = (Path(__file__).parent / 'fixtures' / f'kemendag-{number}-2026.html').read_text()
            doc = self.read(html)
            self.assertEqual(doc.metadata['legal_identity']['value']['number'], number)
            self.assertEqual(doc.metadata['published_at']['value'], published)
            self.assertEqual(doc.metadata['document_type']['value'], 'regulation')
            # 已保存的详情片段只有元数据，不能将其视为正文链接验收。
            self.assertEqual(doc.file_urls, [])

    def test_misleading_official_route_rejects_non_regulation_titles(self):
        for title in ('Rancangan ' + TITLE, 'Putusan Pengadilan Nomor 25 Tahun 2026',
                      'FAQ ' + TITLE, 'Keputusan Menteri Nomor 25 Tahun 2026',
                      'Access Denied', 'Directory of ' + TITLE, 'Peraturan Menteri Perdagangan'):
            with self.subTest(title=title), self.assertRaisesRegex(ValueError, '正式法规标题'):
                self.read('<h1>' + title + '</h1><a href="/original.pdf">Unduh</a>')

    def test_explicit_conflicting_type_cannot_be_overridden_by_title(self):
        for label, kind in [('Tipe Dokumen', 'Monografi Hukum'), ('Jenis / Bentuk Peraturan', 'Rancangan Peraturan Menteri'),
                            ('Jenis', 'Putusan Pengadilan'), ('Jenis Peraturan', 'Peraturan Menteri (Draft)')]:
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, '类型不是正式法规'):
                self.read('<h1>' + TITLE + '</h1><table><tr><td>' + label + '</td><td>' + kind +
                          '</td></tr></table><a href="/original.pdf">Unduh</a>')

    def test_rejects_abstract_faq_draft_and_untrusted_files_keeps_attachment(self):
        html = '<h1>' + TITLE + '</h1>' + '''
        <a href="/original.pdf">Unduh</a><a href="/a.pdf">Abstrak</a>
        <a href="/faq-original.pdf">Unduh</a><a href="/b.pdf" title="FAQ">Unduh</a>
        <a href="/rancangan-original.pdf">Unduh</a><a href="/lampiran.pdf">Lampiran</a>
        <a href="/frontend-new/files/JDIH_Kemendag_RI.zip">Unduh Aplikasi</a>
        <a href="https://outside.example/original.pdf">Unduh</a>'''
        self.assertEqual(self.read(html).file_urls,
                         ['https://jdih.kemendag.go.id/original.pdf', 'https://jdih.kemendag.go.id/lampiran.pdf'])

    def test_redirect_is_not_trusted(self):
        with self.assertRaisesRegex(ValueError, '未允许'):
            self.read('<h1>' + TITLE + '</h1>', 'https://outside.example/detail')


if __name__ == '__main__':
    unittest.main()
