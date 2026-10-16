"""跨国索引按文献国家落盘，导航和判例不发起下载。"""
import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from platform_adapters.base import Document
import download_regulations as app


class EURoutingTests(unittest.TestCase):
    def test_country_separation_scope_and_repeat(self):
        class German:
            country = 'Germany'
            entry_urls = ('https://www.gesetze-im-internet.de/',)
            allowed_hosts = ('www.gesetze-im-internet.de',)
            def discover(self, fetch, limit):
                return ['https://www.gesetze-im-internet.de/example/index.html']
            def read_document(self, url, fetch):
                return Document(url, ['https://www.gesetze-im-internet.de/example/example.pdf'], {})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = root/'countries/EU/index.md'
            index.parent.mkdir(parents=True)
            index.write_text('[navigation](https://eur-lex.europa.eu/n-lex/)\n[law](https://www.gesetze-im-internet.de/)\n[case](https://uitspraken.rechtspraak.nl/)')
            config = root/'config'
            config.write_text('downdloadDir=downloads')
            args = argparse.Namespace(root=root, index=index, config=config, platform='all', limit=7,
                                      per_platform_limit=1,max_details=1,timeout=1,delay=0,
                                      refresh=False,refresh_metadata=False)
            with patch.dict(app.ADAPTERS, {'gesetze-im-internet.de': German()}), patch.object(
                app, 'fetch', side_effect=lambda url, timeout: (b'%PDF-1.4\nfixture\n%%EOF', url)) as fetch:
                self.assertEqual(app.run(args), 0)
                self.assertEqual(app.run(args), 0)
                self.assertEqual(fetch.call_count, 1)
            report=json.loads((root/'downloads/EU/last-run.json').read_text())
            self.assertEqual([x['status'] for x in report['platforms']],
                             ['navigation_only','success','out_of_scope_case_law'])
            self.assertEqual(report['counts'], {'existing':1})
            self.assertEqual(len(list((root/'downloads/Germany').glob('*.pdf'))),1)
            self.assertFalse(list((root/'downloads/EU').glob('*.pdf')))
            self.assertEqual(report['platforms'][1]['country'],'Germany')
            index.write_text(index.read_text() + '\n[France](https://www.legifrance.gouv.fr/)')
            args.limit=1
            with patch.dict(app.ADAPTERS, {'gesetze-im-internet.de': German()}), patch.object(
                    app,'fetch',side_effect=AssertionError('cache and global limit must avoid network')):
                self.assertEqual(app.run(args),0)
            limited=json.loads((root/'downloads/EU/last-run.json').read_text())
            self.assertEqual(limited['platforms'][-1]['status'],'not_scanned_limit')
            self.assertEqual(limited['mode'],'download')
            self.assertEqual(limited['unique_files'],1)


    def test_eu_preflight_rejects_country_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'config').write_text('downdloadDir=downloads')
            index=root/'countries/EU/index.md'; index.parent.mkdir(parents=True)
            index.write_text('[Germany](https://www.gesetze-im-internet.de/)')
            (root/'downloads').mkdir(); (root/'elsewhere').mkdir()
            (root/'downloads/Germany').symlink_to(root/'elsewhere',target_is_directory=True)
            args=argparse.Namespace(root=root,index=index,config=root/'config',platform='all',refresh_metadata=False)
            with self.assertRaises(ValueError), patch.object(app,'fetch') as fetch:
                app.run(args)
            fetch.assert_not_called()
