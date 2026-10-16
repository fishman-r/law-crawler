import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import import_browser_download as app
from crawl_store import CrawlStore, unknown_metadata
from platform_adapters.base import field


PDF = b'%PDF-1.4\nbrowser regulation fixture\n%%EOF\n'
DETAIL = 'https://www.mkri.id/peraturan/test'
DOWNLOAD = 'https://s.mkri.id/public/a.pdf'


class BrowserImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'index.md').write_text('[MK](https://mkri.id/)\n')
        directory = self.root / 'countries/Indonesia'
        directory.mkdir(parents=True)
        (directory / 'index.md').write_text('[MK](https://mkri.id/)\n')
        (self.root / 'config.properties').write_text('downdloadDir=downloads\n')
        self.local = self.root / 'browser.pdf'
        self.local.write_bytes(PDF)
        self.manifest = self.root / 'manifest.json'
        metadata = unknown_metadata(DETAIL)
        metadata['title'] = field('Peraturan Mahkamah Konstitusi Nomor 1 Tahun 2026',
                                   'Peraturan Mahkamah Konstitusi Nomor 1 Tahun 2026',
                                   'Judul', DETAIL, '浏览器实际显示的法规标题。')
        metadata['document_type'] = field('regulation', 'Peraturan Mahkamah Konstitusi',
                                           'Jenis', DETAIL, '官方法规类别。')
        self.item = dict(platform='mkri.id', detail_url=DETAIL, download_url=DOWNLOAD,
                         local_file='browser.pdf', metadata=metadata)

    def run_import(self, payload=None, extra=()):
        self.manifest.write_text(json.dumps(self.item if payload is None else payload))
        with patch('urllib.request.urlopen', side_effect=AssertionError('导入不得访问网络')) as network:
            with contextlib.redirect_stdout(io.StringIO()):
                code = app.main(['--root', str(self.root), '--index', 'countries/Indonesia/index.md', '--jurisdiction', 'Indonesia', '--mode', 'content',
                                 '--config', 'config.properties', '--manifest', 'manifest.json', *extra])
            network.assert_not_called()
        report = json.loads((self.root / 'downloads/Indonesia/browser-import-last-run.json').read_text())
        return code, report

    def test_unknown_jurisdiction_rejected_before_batch(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            app.main(['--root', str(self.root), '--config', 'config.properties',
                      '--jurisdiction', 'France', '--mode', 'content', '--manifest', 'manifest.json'])
        self.assertFalse((self.root / 'downloads').exists())

    def test_browser_batch_replays_snapshot_and_skips_valid_success(self):
        code, report = self.run_import(extra=('--batch-id', 'browser-replay'))
        self.assertEqual(code, 0)
        batch_dir = self.root / 'downloads/.batches/browser-replay'
        snapshot = json.loads((batch_dir / 'manifest-snapshot.json').read_text())
        self.assertTrue(Path(snapshot[0]['local_file']).is_absolute())
        self.manifest.write_text('{broken new manifest')
        with contextlib.redirect_stdout(io.StringIO()), patch('urllib.request.urlopen', side_effect=AssertionError('offline')):
            code = app.main(['--root', str(self.root), '--config', 'config.properties', '--rerun-batch', 'browser-replay'])
        self.assertEqual(code, 0)
        report = json.loads((batch_dir / 'attempts/0002/report.json').read_text())
        self.assertEqual(report['counts'], {'existing': 1})
        self.assertEqual(report['states']['0']['attempts'], 0)
        for suffix in ('md', 'html', 'json'):
            self.assertTrue((batch_dir / f'attempts/0002/report.{suffix}').exists())

    def test_invalid_manifest_has_plan_and_three_failure_reports(self):
        self.manifest.write_text('{invalid json')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            app.main(['--root', str(self.root), '--config', 'config.properties',
                      '--jurisdiction', 'Indonesia', '--mode', 'content',
                      '--manifest', 'manifest.json', '--batch-id', 'bad-manifest'])
        directory = self.root / 'downloads/.batches/bad-manifest'
        self.assertTrue((directory / 'plan.json').exists())
        for suffix in ('md', 'html', 'json'):
            self.assertTrue((directory / f'attempts/0001/report.{suffix}').exists())
        result = json.loads((directory / 'attempts/0001/report.json').read_text())
        self.assertEqual(result['states']['manifest']['status'], 'failed')

    def test_import_is_offline_keeps_original_and_http_reports(self):
        directory = self.root / 'downloads/Indonesia'
        directory.mkdir(parents=True)
        for name in ('last-run.json', 'platform-status.json'):
            (directory / name).write_text('{"http":"unchanged"}')
        code, report = self.run_import()
        self.assertEqual(code, 0)
        self.assertEqual(report['counts'], {'downloaded': 1})
        self.assertEqual(report['country'], 'Indonesia')
        self.assertFalse(report['items'][0]['final_url_verified'])
        self.assertEqual(self.local.read_bytes(), PDF)
        for name in ('last-run.json', 'platform-status.json'):
            self.assertEqual((directory / name).read_text(), '{"http":"unchanged"}')
        with CrawlStore(directory) as store:
            self.assertEqual(store.migration_errors, [])
            source = store.records()[0]['sources'][0]
            self.assertEqual(source['discovery_method'], 'browser_download')
            self.assertEqual(source['final_url'], DOWNLOAD)
            self.assertFalse(source['final_url_verified'])

    def test_repeat_and_distinct_source_share_one_content_file(self):
        self.assertEqual(self.run_import()[0], 0)
        code, report = self.run_import()
        self.assertEqual(code, 0)
        self.assertEqual(report['counts'], {'content_duplicate': 1})
        other = copy.deepcopy(self.item)
        other.update(download_url='https://s.mkri.id/public/another.pdf', detail_url=None,
                     final_url='https://s.mkri.id/public/final.pdf', final_url_verified=True)
        code, report = self.run_import(other)
        self.assertEqual(code, 0)
        self.assertEqual(report['counts'], {'content_duplicate': 1})
        self.assertTrue(report['items'][0]['final_url_verified'])
        self.assertEqual(len(list((self.root / 'downloads/Indonesia').glob('*.pdf'))), 1)
        with CrawlStore(self.root / 'downloads/Indonesia') as store:
            self.assertEqual(len(store.records()), 1)
            self.assertEqual(len(store.records()[0]['sources']), 2)

    def test_same_url_changed_bytes_preserves_both_versions(self):
        self.assertEqual(self.run_import()[0], 0)
        self.local.write_bytes(PDF.replace(b'fixture', b'updated fixture'))
        code, report = self.run_import()
        self.assertEqual(code, 0)
        self.assertEqual(report['counts'], {'downloaded': 1})
        with CrawlStore(self.root / 'downloads/Indonesia') as store:
            self.assertEqual(len(store.records()), 2)
            self.assertEqual(store.db.execute('SELECT count(*) FROM versions').fetchone()[0], 2)

    def known_item(self):
        item = copy.deepcopy(self.item)
        item.update(final_url='https://s.mkri.id/public/verified-final.pdf', final_url_verified=True)
        item['metadata']['published_at'] = field('2026-01-02', '02 January 2026',
                                                 'Tanggal Pengundangan', DETAIL, '官方公布日期。')
        item['metadata']['enacted_at'] = field('2026-01-01', '01 January 2026',
                                               'Tanggal Penetapan', DETAIL, '官方制定日期。')
        return item

    def test_same_content_source_retains_known_evidence_and_records_unknown_declaration(self):
        known = self.known_item()
        self.assertEqual(self.run_import(known)[0], 0)
        code, report = self.run_import()
        self.assertEqual(code, 0)
        self.assertEqual(report['counts'], {'content_duplicate': 1})
        row = report['items'][0]
        self.assertFalse(row['final_url_verified'])  # 本次没有观察到最终跳转。
        self.assertTrue(row['stored_final_url_verified'])
        self.assertEqual(row['stored_final_url'], known['final_url'])
        with CrawlStore(self.root / 'downloads/Indonesia') as store:
            record = store.records()[0]
            source = record['sources'][0]
            self.assertEqual(source['metadata']['published_at'], known['metadata']['published_at'])
            self.assertEqual(source['metadata']['enacted_at'], known['metadata']['enacted_at'])
            self.assertEqual(record['document']['published_at'], known['metadata']['published_at'])
            self.assertTrue(source['final_url_verified'])
            self.assertEqual(source['final_url'], known['final_url'])
            declaration = source['browser_import']
            self.assertEqual(declaration['metadata'], self.item['metadata'])
            self.assertIsNone(declaration['final_url'])
            self.assertFalse(declaration['final_url_verified'])
            self.assertTrue(declaration['preserved_final_url'])
            self.assertEqual(set(declaration['preserved_metadata_fields']), {'published_at', 'enacted_at'})
            self.assertEqual(row['browser_import'], declaration)

    def test_new_content_does_not_inherit_previous_version_evidence(self):
        known = self.known_item()
        self.assertEqual(self.run_import(known)[0], 0)
        self.local.write_bytes(PDF.replace(b'fixture', b'new content'))
        code, report = self.run_import()
        self.assertEqual(code, 0)
        self.assertEqual(report['counts'], {'downloaded': 1})
        with CrawlStore(self.root / 'downloads/Indonesia') as store:
            records = store.records()
            new = next(r for r in records if r['file']['sha256'] == report['items'][0]['sha256'])
            old = next(r for r in records if r is not new)
            self.assertIsNone(new['document']['published_at']['value'])
            self.assertNotIn('enacted_at', new['document'])
            self.assertFalse(new['sources'][0]['final_url_verified'])
            self.assertEqual(new['sources'][0]['final_url'], DOWNLOAD)
            self.assertEqual(new['sources'][0]['browser_import']['preserved_metadata_fields'], [])
            self.assertEqual(old['document']['published_at'], known['metadata']['published_at'])
            self.assertTrue(old['sources'][0]['final_url_verified'])

    def test_same_content_other_source_does_not_borrow_known_evidence(self):
        self.assertEqual(self.run_import(self.known_item())[0], 0)
        other = copy.deepcopy(self.item)
        other['detail_url'] = 'https://www.mkri.id/peraturan/other-source'
        code, report = self.run_import(other)
        self.assertEqual(code, 0)
        self.assertEqual(report['counts'], {'content_duplicate': 1})
        with CrawlStore(self.root / 'downloads/Indonesia') as store:
            sources = store.records()[0]['sources']
            self.assertEqual(len(sources), 2)
            source = next(s for s in sources if s['detail_url'] == other['detail_url'])
            self.assertIsNone(source['metadata']['published_at']['value'])
            self.assertFalse(source['final_url_verified'])
            self.assertEqual(source['final_url'], DOWNLOAD)
            self.assertEqual(source['browser_import']['preserved_metadata_fields'], [])

    def test_new_observed_evidence_can_update_same_content_source(self):
        known = self.known_item()
        self.assertEqual(self.run_import(known)[0], 0)
        revised = self.known_item()
        revised['metadata']['published_at'] = field('2026-01-03', '03 January 2026',
                                                   'Tanggal Pengundangan', DETAIL, '官网明确更正的公布日期。')
        self.assertEqual(self.run_import(revised)[0], 0)
        with CrawlStore(self.root / 'downloads/Indonesia') as store:
            self.assertEqual(store.records()[0]['document']['published_at'], revised['metadata']['published_at'])

    def test_bad_source_urls_and_unverified_explicit_final_are_rejected(self):
        bad = []
        for key, value in [('download_url', 'https://outsider.example/a.pdf'),
                           ('detail_url', 'https://outsider.example/detail'),
                           ('download_url', 'file:///tmp/a.pdf'),
                           ('download_url', 'https://user:secret@s.mkri.id/a.pdf'),
                           ('download_url', 'https://@s.mkri.id/a.pdf'),
                           ('download_url', 'https://s.mkri.id:0/a.pdf'),
                           ('download_url', 'https://s.mkri.id:65536/a.pdf'),
                           ('download_url', 'https://s.mkri.id:no/a.pdf'),
                           ('download_url', 'https://s.mkri.id:/a.pdf'),
                           ('download_url', 'https://s.mkri.id/\na.pdf'),
                           ('platform', 'kemkes.go.id')]:
            item = copy.deepcopy(self.item)
            item[key] = value
            bad.append(item)
        item = copy.deepcopy(self.item)
        item['final_url'] = DOWNLOAD
        bad.append(item)
        item = copy.deepcopy(self.item)
        item.update(final_url='https://outsider.example/a.pdf', final_url_verified=True)
        bad.append(item)
        code, report = self.run_import(bad)
        self.assertEqual(code, 1)
        self.assertEqual(len(report['errors']), len(bad))
        self.assertEqual(report['unique_files'], 0)
        self.assertEqual(list((self.root / 'downloads/Indonesia').glob('*.pdf')), [])

    def test_rejects_missing_provenance_or_non_regulation_metadata(self):
        bad = []
        for change in ('missing_wrapper', 'missing_title', 'draft', 'fake_observed', 'foreign_evidence', 'verified'):
            item = copy.deepcopy(self.item)
            md = item['metadata']
            if change == 'missing_wrapper': md['updated_at'] = None
            elif change == 'missing_title': del md['title']
            elif change == 'draft': md['document_type']['value'] = 'draft'
            elif change == 'fake_observed': md['title']['raw'] = None
            elif change == 'foreign_evidence': md['title']['source_url'] = 'https://outsider.example/title'
            elif change == 'verified': md['legal_status'] = 'verified'
            bad.append(item)
        code, report = self.run_import(bad)
        self.assertEqual(code, 1)
        self.assertEqual(len(report['errors']), len(bad))
        self.assertEqual(report['unique_files'], 0)

    def test_oversized_file_requires_explicit_higher_limit_without_truncation(self):
        data = b'%PDF-1.4\n' + b'0' * (1024 * 1024) + b'\n%%EOF\n'
        self.local.write_bytes(data)
        code, report = self.run_import(extra=('--max-mib', '1'))
        self.assertEqual(code, 1)
        self.assertIn('max-mib', report['errors'][0]['error'])
        code, report = self.run_import(extra=('--max-mib', '2'))
        self.assertEqual(code, 0)
        self.assertEqual(report['files'][0]['size_bytes'], len(data))
        self.assertEqual(Path(report['files'][0]['file']).read_bytes(), data)
        self.assertEqual(self.local.read_bytes(), data)

    def test_invalid_pdf_is_rejected_by_store_and_original_retained(self):
        self.local.write_bytes(b'<html>Not a PDF</html>')
        code, report = self.run_import()
        self.assertEqual(code, 1)
        self.assertTrue(report['errors'])
        self.assertEqual(report['files'], [])
        self.assertEqual(list((self.root / 'downloads/Indonesia').glob('*.pdf')), [])
        self.assertTrue(self.local.exists())


if __name__ == '__main__':
    unittest.main()
