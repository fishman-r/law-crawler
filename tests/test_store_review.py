"""独立审查用例：只使用临时目录和假响应，不访问网络或现有下载。"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from law_crawler import download_regulations as app
from law_crawler.crawl_store import CrawlStore, canonical_url


PDF_A = b'%PDF-1.4\nreview fixture A\n%%EOF\n'
PDF_B = b'%PDF-1.4\nreview fixture B\n%%EOF\n'
URL_A = 'https://example.com/a.pdf'
URL_B = 'https://example.com/b.pdf'


class StoreReviewTests(unittest.TestCase):
    def test_same_url_keeps_multiple_detail_sources_without_second_request(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            fetcher = Mock(return_value=(PDF_A, URL_A))
            store.save(URL_A, 'https://example.com/detail/one', 'platform', fetcher)
            status, record = store.save(URL_A, 'https://example.com/detail/two', 'platform', fetcher)
            self.assertEqual(status, 'existing')
            self.assertEqual(fetcher.call_count, 1)
            self.assertEqual({s['detail_url'] for s in record['sources']},
                             {'https://example.com/detail/one', 'https://example.com/detail/two'})

    def test_distinct_urls_share_bytes_and_keep_separate_provenance(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            first = store.save(URL_A, 'detail-a', 'one', lambda _: (PDF_A, URL_A))[1]
            status, second = store.save(URL_B, 'detail-b', 'two', lambda _: (PDF_A, URL_B))
            self.assertEqual(status, 'content_duplicate')
            self.assertEqual(first['file'], second['file'])
            self.assertEqual(len(list(Path(tmp).glob('*.pdf'))), 1)
            self.assertEqual({s['download_url'] for s in second['sources']}, {URL_A, URL_B})

    def test_refresh_keeps_both_versions_and_newest_survives_index_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            with CrawlStore(tmp) as store:
                old = store.save(URL_A, 'detail', 'platform', lambda _: (PDF_A, URL_A))[1]
                new = store.save(URL_A, 'detail', 'platform', lambda _: (PDF_B, URL_A), refresh=True)[1]
                self.assertNotEqual(old['file']['path'], new['file']['path'])
                self.assertEqual(len(store.records()), 2)
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM versions').fetchone()[0], 2)
            Path(tmp, 'crawl-state.sqlite3').unlink()
            with CrawlStore(tmp) as store:
                unexpected_fetch = Mock(side_effect=AssertionError('缓存重建后不应访问网络'))
                status, current = store.save(URL_A, 'detail', 'platform', unexpected_fetch)
                self.assertEqual(status, 'existing')
                self.assertEqual(current['file']['sha256'], hashlib.sha256(PDF_B).hexdigest())
                self.assertEqual(Path(tmp, old['file']['path']).read_bytes(), PDF_A)

    def test_failure_is_persistent_and_retryable_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            with CrawlStore(tmp) as store:
                with self.assertRaises(TimeoutError):
                    store.save(URL_A, 'detail', 'platform', Mock(side_effect=TimeoutError('offline')))
                request = store.db.execute('SELECT * FROM requests').fetchone()
                self.assertEqual((request['status'], request['attempts']), ('failed', 1))
                self.assertEqual(store.records(), [])
            with CrawlStore(tmp) as store:
                self.assertEqual(store.save(URL_A, 'detail', 'platform', lambda _: (PDF_A, URL_A))[0],
                                 'downloaded')
                request = store.db.execute('SELECT * FROM requests').fetchone()
                self.assertEqual((request['status'], request['attempts']), ('succeeded', 2))

    def test_failed_refresh_is_not_hidden_by_old_success_on_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            with CrawlStore(tmp) as store:
                old = store.save(URL_A, 'detail', 'platform', lambda _: (PDF_A, URL_A))[1]
                with self.assertRaises(TimeoutError):
                    store.save(URL_A, 'detail', 'platform', Mock(side_effect=TimeoutError('offline')),
                               refresh=True)
                self.assertTrue(store.valid(old))
            with CrawlStore(tmp) as store:
                self.assertEqual(store.db.execute('SELECT status FROM requests').fetchone()[0], 'failed')
                fetcher = Mock(return_value=(PDF_B, URL_A))
                self.assertEqual(store.save(URL_A, 'detail', 'platform', fetcher)[0], 'downloaded')
                self.assertEqual(fetcher.call_count, 1)
                self.assertEqual(len(store.records()), 2)

    def test_invalid_response_never_becomes_success_or_content(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            with self.assertRaises(ValueError):
                store.save(URL_A, 'detail', 'platform', lambda _: (b'<html>Error</html>', URL_A))
            self.assertEqual(store.db.execute('SELECT status FROM requests').fetchone()[0], 'failed')
            self.assertEqual(store.records(), [])
            self.assertEqual(list(Path(tmp).glob('*.pdf')), [])
            self.assertEqual(list(Path(tmp).glob('*.json')), [])

    def test_redirect_alias_skips_network_and_keeps_observed_sources(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            store.save(URL_A, 'detail-a', 'platform', lambda _: (PDF_A, URL_B))
            fetcher = Mock(side_effect=AssertionError('别名缓存未命中'))
            status, record = store.save(URL_B, 'detail-b', 'platform', fetcher)
            self.assertEqual(status, 'existing')
            self.assertEqual({s['download_url'] for s in record['sources']}, {URL_A, URL_B})

    def test_canonical_url_changes_only_safe_components(self):
        self.assertEqual(canonical_url('HTTPS://Example.COM:443/a.pdf#page=2'), URL_A)
        self.assertEqual(canonical_url('https://example.com'), 'https://example.com/')
        self.assertNotEqual(canonical_url(URL_A + '?a=1&b=2'), canonical_url(URL_A + '?b=2&a=1'))
        self.assertNotEqual(canonical_url(URL_A + '?a=1&a=2'), canonical_url(URL_A + '?a=2&a=1'))
        self.assertNotEqual(canonical_url('https://example.com/A.pdf'), canonical_url(URL_A))
        self.assertNotEqual(canonical_url('https://www.example.com/a.pdf'), canonical_url(URL_A))
        self.assertNotEqual(canonical_url('https://example.com:8443/a.pdf'), canonical_url(URL_A))

    def test_normalized_url_dedups_fetch_but_preserves_original_spelling(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            url = 'HTTPS://EXAMPLE.COM:443/a.pdf#page=2'
            fetcher = Mock(return_value=(PDF_A, URL_A))
            store.save(url, 'detail', 'platform', fetcher)
            status, record = store.save(URL_A, 'detail', 'platform', fetcher)
            self.assertEqual(status, 'existing')
            self.assertEqual(fetcher.call_count, 1)
            self.assertEqual({s['download_url'] for s in record['sources']}, {url, URL_A})

    def test_corrupt_cached_pdf_is_repaired_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            with CrawlStore(tmp) as store:
                record = store.save(URL_A, 'detail', 'platform', lambda _: (PDF_A, URL_A))[1]
            path = Path(tmp, record['file']['path'])
            path.write_bytes(b'truncated')
            with CrawlStore(tmp) as store:
                self.assertEqual(len(store.migration_errors), 1)
                fetcher = Mock(return_value=(PDF_A, URL_A))
                self.assertEqual(store.save(URL_A, 'detail', 'platform', fetcher)[0], 'downloaded')
                self.assertEqual(fetcher.call_count, 1)
                self.assertTrue(store.valid(store.records()[0]))
            self.assertEqual(path.read_bytes(), PDF_A)

    def test_broken_json_is_repaired_from_valid_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            with CrawlStore(tmp) as store:
                record = store.save(URL_A, 'detail', 'platform', lambda _: (PDF_A, URL_A))[1]
            sidecar = Path(tmp, record['file']['path']).with_suffix('.json')
            sidecar.write_text('{broken', encoding='utf-8')
            with CrawlStore(tmp) as store:
                fetcher = Mock(side_effect=AssertionError('可由已验证索引恢复'))
                self.assertEqual(store.save(URL_A, 'detail', 'platform', fetcher)[0], 'existing')
            self.assertEqual(json.loads(sidecar.read_text())['file']['sha256'], hashlib.sha256(PDF_A).hexdigest())

    def test_legacy_migration_keeps_unknown_fields_backup_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp, 'legacy.pdf')
            pdf.write_bytes(PDF_A)
            legacy = dict(platform='platform', detail_url='https://example.com/detail',
                          download_url=URL_A, final_url=URL_B, file=str(pdf), size=len(PDF_A),
                          sha256=hashlib.sha256(PDF_A).hexdigest(),
                          downloaded_at='2026-01-01T00:00:00+00:00',
                          custom_user_annotation={'review': 'keep this'})
            original = json.dumps(legacy).encode()
            pdf.with_suffix('.json').write_bytes(original)
            for _ in range(2):
                with CrawlStore(tmp) as store:
                    self.assertEqual(store.migration_errors, [])
                    self.assertEqual(len(store.records()), 1)
                    record = store.records()[0]
                    self.assertEqual(len(record['sources']), 1)
                    self.assertEqual(record['legacy_metadata'], legacy)
                    self.assertEqual(record['document']['published_at']['status'], 'not_provided')
                    self.assertEqual(store.db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 2)
            self.assertEqual(Path(tmp, '.metadata-backup', 'legacy.json').read_bytes(), original)
            self.assertEqual(pdf.read_bytes(), PDF_A)

    def test_bad_legacy_hash_leaves_original_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp, 'legacy.pdf')
            pdf.write_bytes(PDF_A)
            sidecar = pdf.with_suffix('.json')
            original = json.dumps(dict(file=str(pdf), sha256='0' * 64, download_url=URL_A)).encode()
            sidecar.write_bytes(original)
            with CrawlStore(tmp) as store:
                self.assertEqual(len(store.migration_errors), 1)
                self.assertEqual(store.records(), [])
            self.assertEqual(sidecar.read_bytes(), original)
            self.assertEqual(pdf.read_bytes(), PDF_A)

    def test_directory_lock_does_not_allow_two_active_writers(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp):
            with self.assertRaisesRegex(ValueError, '已有爬虫运行'):
                CrawlStore(tmp)

    def test_limit_counts_unique_content_and_shared_url_preserves_sources(self):
        host = 'jdih.kemendag.go.id'
        root = f'https://{host}/'
        detail_urls = [root + 'peraturan/peraturan-' + n for n in ('one', 'two', 'three')]
        pdf_urls = [root + n + '.pdf' for n in ('a', 'b', 'c')]
        responses = {
            root: ''.join(f'<a href="{u}">detail</a>' for u in detail_urls).encode(),
            detail_urls[0]: f'<h1>Peraturan Menteri Perdagangan Nomor 1 Tahun 2026</h1><a href="{pdf_urls[0]}">PDF</a>'.encode(),
            detail_urls[1]: f'<h1>Peraturan Menteri Perdagangan Nomor 1 Tahun 2026</h1><a href="{pdf_urls[0]}">PDF</a><a href="{pdf_urls[1]}">mirror</a>'.encode(),
            detail_urls[2]: f'<h1>Peraturan Menteri Perdagangan Nomor 2 Tahun 2026</h1><a href="{pdf_urls[2]}">PDF</a>'.encode(),
            pdf_urls[0]: PDF_A,
            pdf_urls[1]: PDF_A,
            pdf_urls[2]: PDF_B,
        }
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config, index = base / 'config.properties', base / 'index.md'
            config.write_text('downdloadDir=downloads\n')
            index.write_text('[Ministry](https://kemendag.go.id/)\n')
            args = SimpleNamespace(country='Indonesia', root=base, config=config, index=index, platform='all',
                                   limit=2, max_details=20, timeout=1, delay=0,
                                   refresh=False, refresh_metadata=False)
            with patch.object(app, 'fetch', side_effect=lambda url, _: (responses[url], url)) as fetcher:
                self.assertEqual(app.run(args), 0)
            report = json.loads((base / 'downloads/Indonesia' / 'last-run.json').read_text())
            self.assertEqual(report['unique_files'], 2)
            self.assertEqual(len(list((base / 'downloads/Indonesia').glob('*.pdf'))), 2)
            self.assertEqual(sum(call.args[0] == pdf_urls[0] for call in fetcher.call_args_list), 1)
            self.assertEqual(report['counts']['content_duplicate'], 1)
            with CrawlStore(base / 'downloads/Indonesia') as store:
                record = store.by_hash(hashlib.sha256(PDF_A).hexdigest())
                self.assertEqual(len(record['sources']), 3)


if __name__ == '__main__':
    unittest.main()
