"""CLI取得的官方原件离线导入与显式大文件上限，全部使用本地合成样本。"""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from law_crawler.crawl_store import CrawlStore, unknown_metadata
from law_crawler import download_regulations as download
from law_crawler import import_browser_download as importer
from law_crawler.platform_adapters.base import field


PDF = b'%PDF-1.4\nofficial cli regulation fixture\n%%EOF\n'
DETAIL = 'https://www.mkri.id/peraturan/test'
DOWNLOAD = 'https://s.mkri.id/public/a.pdf'


class OfficialCLIImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / 'index.md').write_text('[MK](https://mkri.id/)\n')
        country = self.root / 'countries/Indonesia'
        country.mkdir(parents=True)
        (country / 'index.md').write_text('[MK](https://mkri.id/)\n')
        (self.root / 'config.properties').write_text('downdloadDir=downloads\n')
        self.local = self.root / 'official.pdf'
        self.local.write_bytes(PDF)
        self.manifest = self.root / 'manifest.json'
        metadata = unknown_metadata(DETAIL)
        metadata['title'] = field('Peraturan Mahkamah Konstitusi Nomor 1 Tahun 2026',
                                  'Peraturan Mahkamah Konstitusi Nomor 1 Tahun 2026',
                                  'Judul', DETAIL, 'Official catalogue title')
        metadata['document_type'] = field('regulation', 'Peraturan Mahkamah Konstitusi',
                                          'Jenis', DETAIL, 'Official regulation category')
        self.item = dict(platform='mkri.id', detail_url=DETAIL, download_url=DOWNLOAD,
                         final_url=DOWNLOAD, final_url_verified=True,
                         local_file='official.pdf', metadata=metadata)
        self.manifest.write_text(json.dumps(self.item))
        self.directory = self.root / 'downloads/Indonesia'
        self.network = self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('offline import')))
        self.fetch = self.enterContext(patch('law_crawler.download_regulations.fetch', side_effect=AssertionError('offline import')))

    def tearDown(self):
        self.network.assert_not_called()
        self.fetch.assert_not_called()

    def invoke(self, arguments):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return importer.main(['--root', str(self.root), '--config', 'config.properties', *arguments])

    def new_import(self, batch='cli-import', method='official_cli_download', extra=()):
        arguments = ['--jurisdiction', 'Indonesia', '--mode', 'content',
                     '--manifest', 'manifest.json', '--batch-id', batch, *extra]
        if method is not None:
            arguments.extend(['--acquisition-method', method])
        return self.invoke(arguments)

    def report(self, batch='cli-import', attempt='0001'):
        return json.loads((self.root / 'downloads/.batches' / batch / 'attempts' / attempt / 'report.json').read_text())

    def test_official_cli_import_records_offline_mode_and_source_method(self):
        self.assertEqual(self.new_import(extra=('--max-mib', '128')), 0)
        report = self.report()
        self.assertEqual(report['mode'], 'official_file_import')
        self.assertEqual(report['acquisition_method'], 'official_cli_download')
        self.assertEqual(report['network_requests'], 0)
        self.assertEqual(report['max_mib'], 128)
        self.assertEqual(report['counts'], {'downloaded': 1})
        self.assertEqual(Path(report['files'][0]['file']).read_bytes(), PDF)
        self.assertEqual(self.local.read_bytes(), PDF)
        with CrawlStore(self.directory) as store:
            source = store.records()[0]['sources'][0]
            self.assertEqual(source['discovery_method'], 'official_cli_download')
            self.assertEqual(source['official_file_import']['metadata'], self.item['metadata'])
            self.assertTrue(source['final_url_verified'])
            self.assertNotIn('browser_import', source)
        self.assertTrue((self.directory / 'official-file-import-last-run.json').exists())
        self.assertFalse((self.directory / 'browser-import-last-run.json').exists())

    def test_default_rerun_reuses_method_snapshot_and_valid_content_without_new_file(self):
        self.assertEqual(self.new_import(extra=('--max-mib', '128')), 0)
        batch = self.root / 'downloads/.batches/cli-import'
        plan = json.loads((batch / 'plan.json').read_text())
        snapshot_before = (batch / 'manifest-snapshot.json').read_bytes()
        self.assertEqual(plan['kind'], 'official_file_import')
        self.assertEqual(plan['acquisition_method'], 'official_cli_download')
        self.assertEqual(json.loads(snapshot_before)[0]['local_file'], str(self.local))
        stored = list(self.directory.glob('*.pdf'))
        bytes_before = stored[0].read_bytes()
        self.manifest.write_text('{unusable modified manifest')
        self.local.unlink()  # A valid previous success needs only its cached original.
        self.assertEqual(self.invoke(['--rerun-batch', 'cli-import']), 0)
        report = self.report(attempt='0002')
        self.assertEqual(report['counts'], {'existing': 1})
        self.assertEqual(report['mode'], 'official_file_import')
        self.assertEqual(report['acquisition_method'], 'official_cli_download')
        self.assertEqual(report['max_mib'], 128)
        self.assertEqual(report['states']['0']['attempts'], 0)
        self.assertEqual(report['network_requests'], 0)
        self.assertEqual((batch / 'manifest-snapshot.json').read_bytes(), snapshot_before)
        self.assertEqual(list(self.directory.glob('*.pdf')), stored)
        self.assertEqual(stored[0].read_bytes(), bytes_before)
        with CrawlStore(self.directory) as store:
            self.assertEqual(store.records()[0]['sources'][0]['discovery_method'], 'official_cli_download')

    def test_rerun_all_reads_original_manifest_snapshot_and_deduplicates(self):
        self.assertEqual(self.new_import(), 0)
        self.manifest.write_text(json.dumps(dict(self.item, download_url='https://outsider.example/a.pdf')))
        self.assertEqual(self.invoke(['--rerun-batch', 'cli-import', '--rerun-all']), 0)
        report = self.report(attempt='0002')
        self.assertEqual(report['counts'], {'content_duplicate': 1})
        self.assertEqual(report['items'][0]['download_url'], DOWNLOAD)
        self.assertEqual(report['items'][0]['discovery_method'], 'official_cli_download')
        self.assertEqual(len(list(self.directory.glob('*.pdf'))), 1)

    def test_rerun_cannot_change_acquisition_method_in_either_direction(self):
        self.assertEqual(self.new_import(), 0)
        with self.assertRaises(SystemExit) as caught:
            self.invoke(['--rerun-batch', 'cli-import', '--acquisition-method', 'browser_download'])
        self.assertEqual(caught.exception.code, 1)
        self.assertEqual(len(list((self.root / 'downloads/.batches/cli-import/attempts').iterdir())), 1)
        self.assertEqual(self.new_import(batch='browser-import', method=None), 0)
        with self.assertRaises(SystemExit) as caught:
            self.invoke(['--rerun-batch', 'browser-import', '--acquisition-method', 'official_cli_download'])
        self.assertEqual(caught.exception.code, 1)

    def test_default_and_legacy_browser_import_keep_browser_method_on_rerun(self):
        self.assertEqual(self.new_import(batch='legacy-browser', method=None), 0)
        report = self.report(batch='legacy-browser')
        self.assertEqual(report['mode'], 'browser_import')
        self.assertEqual(report['acquisition_method'], 'browser_download')
        with CrawlStore(self.directory) as store:
            source = store.records()[0]['sources'][0]
            self.assertEqual(source['discovery_method'], 'browser_download')
            self.assertIn('browser_import', source)
            self.assertNotIn('official_file_import', source)
        batch = self.root / 'downloads/.batches/legacy-browser'
        plan = json.loads((batch / 'plan.json').read_text())
        del plan['acquisition_method']  # Old plans did not record the method explicitly.
        (batch / 'plan.json').write_text(json.dumps(plan))
        self.manifest.unlink()
        self.assertEqual(self.invoke(['--rerun-batch', 'legacy-browser']), 0)
        report = self.report(batch='legacy-browser', attempt='0002')
        self.assertEqual(report['mode'], 'browser_import')
        self.assertEqual(report['acquisition_method'], 'browser_download')
        self.assertEqual(report['counts'], {'existing': 1})

    def test_cli_import_requires_explicit_limit_for_oversized_file(self):
        data = b'%PDF-1.4\n' + b'0' * (1024 * 1024) + b'\n%%EOF\n'
        self.local.write_bytes(data)
        self.assertEqual(self.new_import(batch='limited-cli', extra=('--max-mib', '1')), 1)
        self.assertIn('max-mib', self.report(batch='limited-cli')['errors'][0]['error'])
        self.assertEqual(list(self.directory.glob('*.pdf')), [])
        self.assertEqual(self.new_import(batch='expanded-cli', extra=('--max-mib', '128')), 0)
        report = self.report(batch='expanded-cli')
        self.assertEqual(report['files'][0]['size_bytes'], len(data))
        self.assertEqual(Path(report['files'][0]['file']).read_bytes(), data)


class OfficialFetchLimitTests(unittest.TestCase):
    def test_default_limit_is_compatible_with_two_argument_fetch(self):
        def legacy_fetch(url, timeout):
            return PDF, url
        with patch('law_crawler.download_regulations.fetch', side_effect=legacy_fetch) as fetch:
            self.assertEqual(download.official_fetch(DOWNLOAD, 12, ('s.mkri.id',)), (PDF, DOWNLOAD))
            fetch.assert_called_once_with(DOWNLOAD, 12)

    def test_explicit_expanded_limit_is_forwarded_and_fetch_read_is_bounded(self):
        limit = 128 * 1024 * 1024
        with patch('law_crawler.download_regulations.fetch', return_value=(PDF, DOWNLOAD)) as fetch:
            self.assertEqual(download.official_fetch(DOWNLOAD, 12, ('s.mkri.id',), max_bytes=limit), (PDF, DOWNLOAD))
            fetch.assert_called_once_with(DOWNLOAD, 12, limit)
        response = Mock(url=DOWNLOAD)
        response.read.return_value = PDF
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch('urllib.request.urlopen', return_value=response):
            self.assertEqual(download.official_fetch(DOWNLOAD, 12, ('s.mkri.id',), max_bytes=limit), (PDF, DOWNLOAD))
        response.read.assert_called_once_with(limit + 1)

    def test_expanded_limit_still_checks_request_and_redirect_host_whitelist(self):
        limit = 128 * 1024 * 1024
        with patch('law_crawler.download_regulations.fetch', return_value=(PDF, 'https://outsider.example/a.pdf')) as fetch:
            with self.assertRaisesRegex(ValueError, '响应跳转到未适配主机'):
                download.official_fetch(DOWNLOAD, 12, ('s.mkri.id',), max_bytes=limit)
            fetch.assert_called_once_with(DOWNLOAD, 12, limit)
        with patch('law_crawler.download_regulations.fetch') as fetch:
            with self.assertRaisesRegex(ValueError, '请求主机未在平台白名单中'):
                download.official_fetch('https://outsider.example/a.pdf', 12, ('s.mkri.id',), max_bytes=limit)
            fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
