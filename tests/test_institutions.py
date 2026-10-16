"""机构适配器离线回归；MA 使用真实公开列表/详情，MK/DPR 标明解析用例。

离线通过只验证解析行为，实时网络结果以运行报告为准。2026-10-13 后续主流程
已读取 DPR 列表及三份详情；三份 berkas 正文仍返回 403，不能将列表描述为持续
不可访问。对应报告摘录见 fixtures/institutions/verification-2026-10-13.json。
"""
import unittest
from pathlib import Path
from unittest.mock import Mock

from law_crawler.platform_adapters.institutions import ADAPTERS, DPRAdapter, MAAdapter, MKAdapter

FIXTURES = Path(__file__).parent / 'fixtures' / 'institutions'


def fixture(name):
    return (FIXTURES / name).read_bytes()


class InstitutionTests(unittest.TestCase):
    def test_all_three_registered_and_limit_zero_does_not_fetch(self):
        self.assertEqual(set(ADAPTERS), {'mkri.id', 'mahkamahagung.go.id', 'dpr.go.id'})
        fetch = Mock(side_effect=AssertionError('limit=0 must not fetch'))
        for adapter in ADAPTERS.values():
            self.assertEqual(adapter.discover(fetch, 0), [])

    def test_mk_discovers_formal_pmk_without_judgment_or_draft(self):
        adapter = MKAdapter()
        body = fixture('mk-list-parser.html')
        fetch = lambda url: (body, url)
        details = adapter.discover(fetch, 3)
        self.assertEqual(len(details), 1)
        self.assertIn('search=PMK+Nomor+2+Tahun+2023', details[0])
        document = adapter.read_document(details[0], fetch)
        self.assertEqual(document.pdf_urls, ['https://www.mkri.id/public/content/pmk/PMK%20no.%202%202023.pdf'])
        self.assertEqual(document.metadata['document_type']['value'], 'regulation')
        self.assertEqual(document.metadata['legal_identity']['value']['number'], '2')
        self.assertEqual(document.metadata['legal_identity']['value']['year'], '2023')
        for name in ('published_at', 'updated_at', 'effective_region'):
            self.assertIsNone(document.metadata[name]['value'])

    def test_mk_does_not_accept_unmatched_search_result(self):
        adapter = MKAdapter()
        with self.assertRaisesRegex(ValueError, '唯一匹配'):
            adapter.read_document(adapter.entry_urls[0] + '?search=PMK+Nomor+1+Tahun+2026',
                                  lambda url: (fixture('mk-list-parser.html'), url))

    def test_ma_uses_real_public_registry_when_jdih_fails(self):
        adapter = MAAdapter()
        fetch = Mock(side_effect=[OSError('HTTP 403'), (fixture('ma-category.html'), adapter.entry_urls[1])])
        details = adapter.discover(fetch, 10)
        self.assertEqual(len(details), 5)
        self.assertTrue(all('catid=16' in url for url in details))
        self.assertTrue(all('juklak-sema' not in url for url in details))
        self.assertIn('1199:perma-nomor-2-tahun-2015', details[0])
        self.assertEqual(fetch.call_count, 2)
        document = adapter.read_document(details[0], lambda url: (fixture('ma-good-detail.html'), url))
        self.assertEqual(document.pdf_urls, ['https://kepaniteraan.mahkamahagung.go.id/images/peraturan/perma/PERMA_02_2015_FIX4.pdf'])
        self.assertEqual(document.metadata['legal_identity']['value']['number'], '2')
        self.assertEqual(document.metadata['legal_identity']['value']['year'], '2015')
        self.assertIsNone(document.metadata['published_at']['value'])
        self.assertIsNone(document.metadata['effective_region']['value'])

    def test_ma_article_does_not_collect_unrelated_sidebar_pdf(self):
        adapter = MAAdapter()
        url = adapter.entry_urls[1].split('?')[0] + '?view=article&id=1&catid=16'
        html = fixture('ma-good-detail.html') + b'<a href="/images/unrelated.pdf">PDF</a>'
        document = adapter.read_document(url, lambda _: (html, url))
        self.assertEqual(len(document.pdf_urls), 1)

    def test_ma_jdih_extensionless_body_and_date_semantics(self):
        adapter = MAAdapter()
        url = 'https://jdih.mahkamahagung.go.id/legal-product/perma-nomor-3-tahun-2026/detail'
        document = adapter.read_document(url, lambda _: (fixture('ma-jdih-parser.html'), url))
        self.assertEqual(document.pdf_urls, [url.replace('/detail', '/download/dokumen')])
        self.assertEqual(document.metadata['enacted_at']['value'], '2026-06-23')
        self.assertEqual(document.metadata['published_at']['value'], '2026-07-09')
        # 主题出现 Putusan 仍是 PERMA，不按关键词错分为司法裁决。
        self.assertEqual(document.metadata['document_type']['value'], 'regulation')

    def test_ma_rejects_draft_type_and_unrelated_article(self):
        adapter = MAAdapter()
        url = 'https://jdih.mahkamahagung.go.id/legal-product/draft/detail'
        html = fixture('ma-jdih-parser.html').replace(b'<td>Peraturan Mahkamah Agung</td>', b'<td>Rancangan Peraturan Mahkamah Agung</td>')
        with self.assertRaisesRegex(ValueError, '正式法规'):
            adapter.read_document(url, lambda _: (html, url))
        url = adapter.entry_urls[1]
        with self.assertRaisesRegex(ValueError, 'PERMA'):
            adapter.read_document(url, lambda _: (b'<h1>Article</h1><a href="/x.pdf">PDF</a>', url))

    def test_dpr_discovers_actual_details_not_catalog_pdfs_or_drafts(self):
        adapter = DPRAdapter()
        base = 'https://jdih.dpr.go.id'
        html = b'''<a href="/setjen/detail-dokumen/tipe/peraturan_dpr/id/56">[DETAIL]</a>
        <a href="/setjen/detail-dokumen/tipe/rancangan_uu/id/1">Draft</a>
        <a href="https://berkas.dpr.go.id/jdih/document/peraturan_dpr/katalog.pdf">Katalog</a>'''
        self.assertEqual(adapter.discover(lambda _: (html, base), 5),
                         [base + '/setjen/detail-dokumen/tipe/peraturan_dpr/id/56'])
        # 实际旧列表仅有目录卡片，无逐条正文详情，必须报告没有可发现详情。
        self.assertEqual(adapter.discover(lambda _: (fixture('dpr-list.html'), base), 5), [])

    def test_dpr_reads_only_explicit_body_and_preserves_dates(self):
        adapter = DPRAdapter()
        url = 'https://jdih.dpr.go.id/setjen/detail-dokumen/tipe/peraturan_dpr/test'
        document = adapter.read_document(url, lambda _: (fixture('dpr-detail-parser.html'), url))
        self.assertEqual(document.pdf_urls, ['https://berkas.dpr.go.id/jdih/document/peraturan_dpr/2025perdpr002.pdf?t=20260831061812'])
        self.assertEqual(document.metadata['enacted_at']['value'], '2025-07-08')
        self.assertEqual(document.metadata['published_at']['value'], '2025-08-01')
        self.assertEqual(document.metadata['legal_identity']['value']['year'], '2025')
        self.assertIsNone(document.metadata['effective_region']['value'])

    def test_dpr_refuses_abstract_when_body_is_missing(self):
        adapter = DPRAdapter()
        url = 'https://jdih.dpr.go.id/setjen/detail-dokumen/tipe/peraturan_dpr/test'
        body = fixture('dpr-detail-parser.html').replace(b'UNDUH BERKAS', b'UNDUH ABSTRAK')
        with self.assertRaisesRegex(ValueError, '正文文件'):
            adapter.read_document(url, lambda _: (body, url))

    def test_network_failures_are_not_replaced_with_fixed_files(self):
        fetch = Mock(side_effect=OSError('HTTP 403 Forbidden'))
        for adapter in (MKAdapter(), MAAdapter(), DPRAdapter()):
            with self.assertRaises((OSError, ValueError)):
                adapter.discover(fetch, 1)


if __name__ == '__main__':
    unittest.main()
