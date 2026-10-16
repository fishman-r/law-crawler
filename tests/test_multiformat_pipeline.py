"""多格式发现到入库集成；本文件的合成响应不是官网验收证据。"""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import download_regulations as cli
import import_browser_download as importer
from crawl_store import CrawlStore
from platform_adapters.base import document_url
from platform_adapters.trade import TradeAdapter
from platform_adapters.national import ADAPTERS as NATIONAL

RTF = b'{\\rtf1\\ansi Peraturan fixture}'
ENTRY = 'https://jdih.kemendag.go.id/'
DETAIL = ENTRY + 'peraturan/peraturan-menteri-perdagangan-nomor-1-tahun-2026'
FILE = ENTRY + 'original.rtf'
HTML = '<h1>Peraturan Menteri Perdagangan Nomor 1 Tahun 2026</h1><a href="/original.rtf">Unduh</a><a href="/logo.png">Logo</a>'


class MultiFormatPipelineTests(unittest.TestCase):
    def test_discovery_preserves_original_filters_and_no_page_images(self):
        doc = TradeAdapter().read_document(DETAIL, lambda u: (HTML.encode(), u))
        self.assertEqual(doc.file_urls, [FILE])
        self.assertEqual(doc.pdf_urls, doc.file_urls)  # 旧接口兼容
        self.assertTrue(document_url('https://official.example/A%2EDOCX?token=1'))
        self.assertFalse(document_url('https://official.example/view.html?file=x.docx'))
        html = '<h1>Peraturan Menteri Nomor 1 Tahun 2026</h1><a href="/original.docx">Unduh</a><a href="/abstrak.docx">Abstrak</a><a href="https://outside.example/a.docx">Other</a>'
        doc = NATIONAL['peraturan.go.id'].read_document('https://peraturan.go.id/id/test', lambda u: (html.encode(), u))
        self.assertEqual(doc.file_urls, ['https://peraturan.go.id/original.docx'])

    def test_cli_then_browser_import_preserve_shared_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'config.properties').write_text('downdloadDir=originals\n')
            (root/'countries/Indonesia').mkdir(parents=True)
            (root/'countries/Indonesia/index.md').write_text('[Trade](https://kemendag.go.id/)')
            (root/'index.md').write_text('[Trade](https://kemendag.go.id/)')
            responses = {ENTRY: f'<a href="{DETAIL}">Peraturan</a>'.encode(), DETAIL: HTML.encode(), FILE: RTF}
            def fetch(url, timeout, max_bytes=40*1024*1024):
                return responses[url], url
            args = ['--root', tmp, '--country', 'Indonesia', '--config', 'config.properties', '--index', 'countries/Indonesia/index.md', '--mode', 'content', '--platform', 'kemendag.go.id', '--limit', '1', '--delay', '0']
            with patch.object(cli, 'fetch', side_effect=fetch), patch('sys.argv', ['download_regulations.py', *args]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(), 0)
                self.assertEqual(cli.main(), 0)
            self.assertEqual(len(list((root/'originals/Indonesia').glob('*.rtf'))), 1)
            self.assertFalse(list((root/'originals/Indonesia').glob('*.pdf')))
            metadata = TradeAdapter().read_document(DETAIL, lambda u:(HTML.encode(),u)).metadata
            (root/'browser.pdf').write_bytes(RTF)  # misleading local filename
            manifest = dict(platform='kemendag.go.id', detail_url=DETAIL, download_url=FILE, local_file='browser.pdf', metadata=metadata)
            (root/'manifest.json').write_text(json.dumps(manifest))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(importer.main(['--root', tmp, '--country', 'Indonesia', '--config', 'config.properties', '--index','countries/Indonesia/index.md','--mode','content','--manifest','manifest.json']),0)
            report = json.loads((root/'originals/Indonesia/browser-import-last-run.json').read_text())
            self.assertEqual(report['counts'], {'content_duplicate':1})
            self.assertEqual(report['files'][0]['media_type'],'application/rtf')
            with CrawlStore(root/'originals/Indonesia') as store:
                self.assertFalse(store.migration_errors)
                self.assertEqual(len(store.records()),1)
                self.assertTrue(store.valid(store.records()[0]))
