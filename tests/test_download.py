import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from law_crawler import download_regulations as app
from law_crawler.crawl_store import CrawlStore, canonical_url
from law_crawler.regulation_metadata import extract_metadata

PDF = b'%PDF-1.4\nfixture\n%%EOF\n'


class DownloadTests(unittest.TestCase):
    def test_config_resolves_from_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'global-config.properties'
            config.write_text('downdloadDir=./downloads/\n')
            self.assertEqual(app.download_dir(config), app.ROOT / 'downloads')
            self.assertEqual(app.download_dir(config, Path(tmp)), Path(tmp).resolve() / 'downloads')
            config.write_text(f'downdloadDir={tmp}\n')
            self.assertEqual(app.download_dir(config), Path(tmp).resolve())

    def test_link_resolution_and_canonical_safety(self):
        links = app.Links('<a href="/a.pdf#view">A</a><iframe src="/a.pdf"></iframe><a href="javascript:void(0)">X</a>', 'https://example.com/detail')
        self.assertEqual(links.urls, ['https://example.com/a.pdf'])
        self.assertEqual(canonical_url('HTTPS://EXAMPLE.com:443/a.pdf#view'), 'https://example.com/a.pdf')
        self.assertEqual(canonical_url('https://example.com:0/a'), 'https://example.com:0/a')
        self.assertNotEqual(canonical_url('https://example.com/a?version=1'), canonical_url('https://example.com/a?version=2'))
        self.assertNotEqual(canonical_url('https://example.com/a?b=2&a=1'), canonical_url('https://example.com/a?a=1&b=2'))

    def test_invalid_pdf_leaves_only_failed_state(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            with self.assertRaises(ValueError):
                store.save('https://example.com/a.pdf', 'detail', 'platform', lambda u: (b'<html>Error</html>', u))
            self.assertEqual(list(Path(tmp).glob('*.pdf')), [])
            self.assertEqual(store.db.execute('SELECT status FROM requests').fetchone()[0], 'failed')
            self.assertEqual(store.db.execute('SELECT count(*) FROM objects').fetchone()[0], 0)

    def test_repeat_skips_network_and_corruption_repairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = 'https://example.com/a.pdf'
            with patch.object(app, 'fetch', return_value=(PDF, url)) as fetch:
                args = (Path(tmp), url, 'detail', 'platform', 5)
                status, record = app.save_pdf(*args)
                self.assertEqual(status, 'downloaded')
                self.assertEqual(app.save_pdf(*args)[0], 'existing')
                self.assertEqual(fetch.call_count, 1)
                target = Path(tmp) / record['file']['path']
                target.write_bytes(b'truncated')
                self.assertEqual(app.save_pdf(*args)[0], 'downloaded')
                self.assertEqual(fetch.call_count, 2)
                target.with_suffix('.json').write_text('{broken')
                self.assertEqual(app.save_pdf(*args)[0], 'existing')
                json.loads(target.with_suffix('.json').read_text())
                self.assertEqual(fetch.call_count, 2)  # SQLite 恢复元数据，无需再下载。

    def test_metadata_official_fixtures(self):
        for number, date in [('25', '2026-10-12'), ('24', '2026-09-18'), ('23', '2026-09-09')]:
            with self.subTest(number=number):
                html = (Path(__file__).parent / 'fixtures' / f'kemendag-{number}-2026.html').read_text()
                metadata = extract_metadata(html, 'https://jdih.kemendag.go.id/peraturan/test', 'kemendag.go.id')
                self.assertEqual(metadata['published_at']['value'], date)
                self.assertIsNone(metadata['updated_at']['value'])
                self.assertIsNone(metadata['effective_region']['value'])
                self.assertEqual(metadata['legal_identity']['value']['number'], number)
                self.assertEqual(metadata['legal_identity']['value']['jurisdiction'], 'ID')

    def test_invalid_date_and_unrelated_jsonld_not_used(self):
        html = '<table><tr><td>Tanggal Pengundangan</td><td>32 October 2026</td></tr><tr><td>Tempat Penetapan</td><td>Jakarta</td></tr></table><script type="application/ld+json">{"@type":"Organization","dateModified":"2026-01-01"}</script>'
        metadata = extract_metadata(html, 'https://jdih.kemendag.go.id/test', 'kemendag.go.id')
        self.assertIsNone(metadata['published_at']['value'])
        self.assertEqual(metadata['published_at']['status'], 'unparsed')
        self.assertIsNone(metadata['updated_at']['value'])
        self.assertIsNone(metadata['effective_region']['value'])

    def test_run_limit_counts_unique_content_and_preserves_sources(self):
        host = 'https://jdih.kemendag.go.id'
        pages = {
            host + '/': b'<a href="/peraturan/peraturan-a">A</a><a href="/peraturan/peraturan-b">B</a><a href="/peraturan/peraturan-c">C</a>',
            host + '/peraturan/peraturan-a': b'<h1>Peraturan Menteri Perdagangan Nomor 1 Tahun 2026</h1><a href="/a.pdf">A</a>',
            host + '/peraturan/peraturan-b': b'<h1>Peraturan Menteri Perdagangan Nomor 1 Tahun 2026</h1><a href="/b.pdf">B</a>',
            host + '/peraturan/peraturan-c': b'<h1>Peraturan Menteri Perdagangan Nomor 2 Tahun 2026</h1><a href="/c.pdf">C</a>',
            host + '/a.pdf': PDF, host + '/b.pdf': PDF,
            host + '/c.pdf': PDF.replace(b'fixture', b'other'),
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, index = root / 'config', root / 'index'
            config.write_text('downdloadDir=./downloads/')
            index.write_text('[trade](https://kemendag.go.id/)')
            args = argparse.Namespace(country='Indonesia', root=root, config=config, index=index, platform='all', limit=2,
                                      max_details=20, timeout=5, delay=0, refresh=False, refresh_metadata=False)
            with patch.object(app, 'fetch', side_effect=lambda u, *a: (pages[u], u)) as fetch:
                self.assertEqual(app.run(args), 0)
                self.assertEqual(sum(call.args[0].endswith('.pdf') for call in fetch.call_args_list), 3)
            report = json.loads((root / 'downloads/Indonesia/last-run.json').read_text())
            self.assertEqual(report['unique_files'], 2)
            self.assertEqual(report['counts']['content_duplicate'], 1)
            self.assertEqual(sorted(r['source_count'] for r in report['files']), [1, 2])
            args.refresh_metadata = True
            with patch.object(app, 'fetch', side_effect=lambda u, *a: (pages[u], u)) as fetch:
                self.assertEqual(app.run(args), 0)
                self.assertFalse(any(call.args[0].endswith('.pdf') for call in fetch.call_args_list))


if __name__ == '__main__':
    unittest.main()
