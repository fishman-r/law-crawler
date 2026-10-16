"""国家路径隔离：保留历史基础目录、拒绝逃逸，并验证各国独立持久化去重。"""
import argparse
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import download_regulations as app
import import_browser_download as browser_import
from platform_adapters.base import Document


class CountryPathsTests(unittest.TestCase):
    def test_standard_index_and_absolute_or_relative_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            config = root / 'config'
            config.write_text('downdloadDir=downloads\n')
            self.assertEqual(app.country_download_dir(config, root, root/'countries/Canada/index.md'),
                             root/'downloads/Canada')
            config.write_text('downloadDir=' + str(root/'outside') + '\n')
            self.assertEqual(app.country_download_dir(config, root, root/'countries/Indonesia/index.md'),
                             root/'outside/Indonesia')

    def test_nonstandard_index_requires_explicit_country(self):
        with self.assertRaises(ValueError):
            app.index_country(Path('index.md'))
        self.assertEqual(app.index_country(Path('index.md'), 'Canada'), 'Canada')
        with self.assertRaises(ValueError):
            app.index_country(Path('countries/Canada/index.md'), 'Indonesia')

    def test_rejects_path_traversal_and_malformed_country(self):
        for country in ('../Canada', '.', '..', '/Canada', 'Canada/other', 'Canada\\other',
                        '', ' Canada', 'Canada ', 'Canada\x00', 'Canada:stream'):
            with self.subTest(country=country), self.assertRaises(ValueError):
                app.index_country(Path('index.md'), country)

    def test_rejects_existing_country_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'config'
            config.write_text('downdloadDir=downloads\n')
            (root/'downloads').mkdir()
            (root/'elsewhere').mkdir()
            (root/'downloads/Canada').symlink_to(root/'elsewhere', target_is_directory=True)
            with self.assertRaises(ValueError):
                app.country_download_dir(config, root, root/'countries/Canada/index.md')

    def test_new_runs_preserve_legacy_files_and_isolate_country_dedup(self):
        class FixtureAdapter:
            entry_urls = ['https://official.example/']
            allowed_hosts = ['official.example']
            def discover(self, fetch, limit):
                return ['https://official.example/detail']
            def read_document(self, detail, fetch):
                return Document(detail, ['https://official.example/original.pdf'], {})

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root/'config'
            config.write_text('downdloadDir=downloads\n')
            (root/'downloads').mkdir()
            legacy = root/'downloads/legacy.pdf'
            legacy.write_bytes(b'legacy bytes left unchanged')
            for country in ('Canada', 'Indonesia'):
                index = root/'countries'/country/'index.md'
                index.parent.mkdir(parents=True)
                index.write_text('[Official](https://official.example/)')
                args = argparse.Namespace(root=root, config=config, index=index, platform='all',
                    limit=1, max_details=1, timeout=1, delay=0, refresh=False, refresh_metadata=False)
                with patch.dict(app.ADAPTERS, {'official.example': FixtureAdapter()}), \
                     patch.object(app, 'fetch', return_value=(b'%PDF-1.4\nfixture\n%%EOF', 'https://official.example/original.pdf')) as fetch, \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(app.run(args), 0)
                    self.assertEqual(app.run(args), 0)
                    self.assertEqual(fetch.call_count, 1)
                directory = root/'downloads'/country
                report = json.loads((directory/'last-run.json').read_text())
                self.assertEqual(report['country'], country)
                self.assertEqual(report['counts'], {'existing': 1})
                self.assertEqual(len(list(directory.glob('*.pdf'))), 1)
            self.assertEqual(legacy.read_bytes(), b'legacy bytes left unchanged')
            self.assertEqual(list((root/'downloads').glob('*.pdf')), [legacy])


    def test_wrong_country_platform_rejected_before_network_or_store(self):
        class CanadianFixture:
            country = 'Canada'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'config').write_text('downdloadDir=downloads')
            (root/'index.md').write_text('[Canadian official](https://official.example/)')
            (root/'manifest.json').write_text('[{}]')
            args = argparse.Namespace(root=root, config=root/'config', index=root/'index.md',
                country='Indonesia', platform='all', manifest=root/'manifest.json', max_mib=1)
            with patch.dict(app.ADAPTERS, {'official.example': CanadianFixture()}), \
                 patch.object(app, 'fetch', side_effect=AssertionError('no request allowed')) as fetch:
                with self.assertRaisesRegex(ValueError, 'Canada'):
                    app.run(args)
                with self.assertRaisesRegex(ValueError, 'Canada'):
                    browser_import.run(args)
                fetch.assert_not_called()
                self.assertFalse((root/'downloads').exists())
                app.validate_platform_countries(['official.example'], 'Canada')

    def test_country_unknown_adapter_is_not_misclassified(self):
        with patch.dict(app.ADAPTERS, {'unknown.example': object()}):
            app.validate_platform_countries(['unknown.example', 'unsupported.example'], 'Canada')
