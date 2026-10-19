"""临时资料搬到 data 后重跑；验证缓存、原始证据和迁移授权边界。"""
import argparse
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from law_crawler import import_browser_download, rebuild_markdown
from law_crawler.crawl_store import CrawlStore, unknown_metadata
from law_crawler.platform_adapters.base import Document, field
from law_crawler.regulation_pipeline import execute
from law_crawler.relocation import plan_relocation
from law_crawler.text_documents import CONVERSION_METHOD


PDF = b'%PDF-1.4\nrelocation regulation fixture\n%%EOF\n'
DE = 'https://www.gesetze-im-internet.de'
MK_DETAIL = 'https://www.mkri.id/peraturan/test'
MK_FILE = 'https://s.mkri.id/public/a.pdf'
XML = (b'<Statute xmlns:lims="http://justice.gc.ca/lims" lims:id="fixture">'
       b'<Identification><LongTitle>Fixture law</LongTitle>'
       b'<Chapter><ConsolidatedNumber>A-1</ConsolidatedNumber></Chapter></Identification>'
       b'<Body><Section><Label>1</Label><Text>Original wording.</Text></Section></Body></Statute>')


def metadata(url):
    result = unknown_metadata(url)
    result['title'] = field('Fixture law', 'Fixture law', 'Title', url, 'Official title')
    result['document_type'] = field('regulation', 'Act', 'Type', url, 'Official law type')
    result['published_at'] = field('2026-01-01', '2026-01-01', 'Date', url, 'Publication date')
    return result


class GermanFixture:
    country = 'Germany'
    allowed_hosts = ('www.gesetze-im-internet.de',)

    def discover(self, fetch, limit):
        fetch(DE + '/')
        return [DE + '/law']

    def read_document(self, url, fetch):
        fetch(url)
        return Document(url, [DE + '/law.pdf'], metadata(url))


class DataRelocationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'project'
        self.root.mkdir()
        for country, url in (('Germany', DE + '/'), ('Canada', 'https://laws-lois.justice.gc.ca/'),
                             ('Indonesia', 'https://mkri.id/')):
            directory = self.root / 'countries' / country
            directory.mkdir(parents=True)
            (directory / 'index.md').write_text('[Official](' + url + ')\n', encoding='utf-8')
        self.config = self.root / 'countries/global-config.properties'
        self.config.write_text('downloadDir=downloads\n', encoding='utf-8')
        self.old_base = self.root / 'downloads'
        self.base = self.root / 'data/downloads'
        # 所有可能联网的底层入口均阻断；样本采集只使用测试内显式替身。
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('offline test')))
        self.enterContext(patch('socket.create_connection', side_effect=AssertionError('offline test')))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def arguments(self, **changes):
        values = dict(root=self.root, config=self.config, index=None, jurisdiction='Germany',
                      mode='content', batch_id='moved-download', rerun_batch=None,
                      rerun_all=False, plan_only=False, platform='all', limit=1,
                      per_platform_limit=1, max_details=1, timeout=1, delay=0,
                      refresh=False, refresh_metadata=False, max_attempts=1,
                      backoff_base=0, experimental_sources=False)
        values.update(changes)
        return argparse.Namespace(**values)

    def move_data(self):
        (self.root / 'data').mkdir()
        for name in ('downloads', 'reports', 'tmp'):
            before = self.root / name
            before.mkdir(exist_ok=True)
            before.rename(self.root / 'data' / name)
        plans = {path.parent.name: hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in (self.base / '.batches').glob('*/plan.json')}
        self.manifest = dict(schema_version=1, old_download_base=str(self.old_base),
                             download_base=str(self.base), created_at='2026-10-19T00:00:00Z',
                             roots=[dict(kind=name, before=str(self.root / name),
                                         after=str(self.root / 'data' / name))
                                    for name in ('downloads', 'reports', 'tmp')],
                             batch_plans=plans)
        self.write_manifest(self.manifest)
        self.config.write_text('downloadDir=data/downloads\n', encoding='utf-8')

    def write_manifest(self, manifest):
        (self.base / '.relocation.json').write_text(json.dumps(manifest), encoding='utf-8')

    def history(self, batch):
        return {path.relative_to(batch): path.read_bytes() for path in batch.rglob('*')
                if path.is_file() and (path.name in ('plan.json', 'plan.md', 'manifest-snapshot.json')
                                       or path.is_relative_to(batch / 'attempts'))}

    def assert_history(self, batch, before):
        for name, raw in before.items():
            self.assertEqual((batch / name).read_bytes(), raw, str(name))

    def seed_download(self):
        with patch('law_crawler.regulation_pipeline.ADAPTERS', {'gesetze-im-internet.de': GermanFixture()}), \
                patch('law_crawler.download_regulations.fetch',
                      side_effect=lambda url, *args: (PDF if url.endswith('.pdf') else b'page', url)):
            self.assertEqual(execute(self.arguments()), 0)

    def replay_download(self):
        with patch('law_crawler.regulation_pipeline.ADAPTERS', {'gesetze-im-internet.de': GermanFixture()}):
            return execute(self.arguments(jurisdiction=None, mode=None, batch_id=None,
                                          rerun_batch='moved-download'))

    def test_download_replay_uses_valid_cache_and_preserves_old_reports(self):
        self.seed_download()
        before = self.history(self.old_base / '.batches/moved-download')
        self.move_data()
        with patch('law_crawler.download_regulations.fetch', side_effect=AssertionError('valid cache')) as fetch:
            self.assertEqual(self.replay_download(), 0)
            fetch.assert_not_called()
        batch = self.base / '.batches/moved-download'
        report = json.loads((batch / 'attempts/0002/report.json').read_text())
        self.assertEqual(report['counts'], {'previous_success': 1})
        self.assertTrue(Path(report['files'][0]['file']).is_relative_to(self.base))
        self.assert_history(batch, before)
        # 原计划字节改变后不能借搬迁绕过冻结；拒绝应早于创建新轮次。
        with (batch / 'plan.json').open('ab') as stream:
            stream.write(b'\n')
        with self.assertRaisesRegex(ValueError, 'SHA'):
            self.replay_download()
        self.assertFalse((batch / 'attempts/0003').exists())

    def test_missing_download_cache_still_requires_repair(self):
        self.seed_download()
        self.move_data()
        next((self.base / 'Germany').glob('*.pdf')).unlink()
        with patch('law_crawler.download_regulations.fetch', side_effect=lambda url, *args: (PDF, url)) as fetch:
            self.assertEqual(self.replay_download(), 0)
            self.assertEqual([call.args[0] for call in fetch.call_args_list], [DE + '/law.pdf'])

    def test_absent_manifest_keeps_download_base_change_rejected(self):
        self.seed_download()
        self.move_data()
        (self.base / '.relocation.json').unlink()
        with self.assertRaisesRegex(ValueError, '下载基础目录'):
            self.replay_download()
        self.assertFalse((self.base / '.batches/moved-download/attempts/0002').exists())

    def test_manifest_is_bound_to_plan_and_allows_only_three_exact_roots(self):
        self.seed_download()
        self.move_data()
        raw = (self.base / '.batches/moved-download/plan.json').read_bytes()
        moved = plan_relocation(self.base, 'moved-download', raw)
        self.assertEqual(moved.map_path(self.root / 'tmp/input.pdf'), self.root / 'data/tmp/input.pdf')
        external = self.root / 'downloads-other/input.pdf'
        self.assertEqual(moved.map_path(external), external)
        with self.assertRaises(ValueError):
            moved.map_path(str(self.old_base / 'Germany') + '/../outside.pdf')
        variants = []
        wrong = copy.deepcopy(self.manifest)
        wrong['roots'][1]['after'] = str(self.root / 'outside')
        variants.append(wrong)
        wrong = copy.deepcopy(self.manifest)
        wrong['roots'][1]['before'] = str(self.root / 'outside')
        variants.append(wrong)
        wrong = copy.deepcopy(self.manifest)
        wrong['roots'][1]['kind'] = 'downloads'
        variants.append(wrong)
        wrong = copy.deepcopy(self.manifest)
        wrong['batch_plans']['moved-download'] = '0' * 64
        variants.append(wrong)
        wrong = copy.deepcopy(self.manifest)
        wrong['batch_plans'] = {}
        variants.append(wrong)
        for value in variants:
            with self.subTest(manifest=value):
                self.write_manifest(value)
                with self.assertRaises(ValueError):
                    plan_relocation(self.base, 'moved-download', raw)
        self.write_manifest(self.manifest)
        new_plan = json.dumps(dict(download_base=str(self.base))).encode()
        new = plan_relocation(self.base, 'after-migration', new_plan)
        self.assertTrue(new.matches_download_base(str(self.base)))
        self.assertFalse(new.matches_download_base(str(self.old_base)))
        self.assertEqual(new.map_path(self.root / 'tmp/input.pdf'), self.root / 'tmp/input.pdf')
        (self.root / 'data/tmp/link').symlink_to(self.root / 'countries', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            moved.map_path(self.root / 'tmp/link/file.pdf')
        manifest_path = self.base / '.relocation.json'
        raw_manifest = manifest_path.read_bytes()
        manifest_path.write_bytes(b'{invalid migration record')
        with self.assertRaisesRegex(ValueError, 'UTF-8 JSON'):
            plan_relocation(self.base, 'moved-download', raw)
        manifest_path.write_bytes(raw_manifest)
        manifest_path.unlink()
        alternate = self.root / 'manifest-copy.json'
        alternate.write_bytes(raw_manifest)
        manifest_path.symlink_to(alternate)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            plan_relocation(self.base, 'moved-download', raw)

    def seed_rebuild(self):
        directory = self.old_base / 'Canada'
        url = 'https://laws-lois.justice.gc.ca/fixture.xml'
        with CrawlStore(directory) as store:
            _, record = store.save(url, url, 'laws-lois.justice.gc.ca', lambda target: (XML, target),
                                   metadata(url), convert_text=True)
        self.assertEqual(rebuild_markdown.main(['--root', str(self.root), '--jurisdiction', 'Canada',
                                               '--batch-id', 'moved-rebuild']), 0)
        return record

    def test_offline_rebuild_maps_frozen_country_directory_without_changing_plan(self):
        record = self.seed_rebuild()
        before = self.history(self.old_base / '.batches/moved-rebuild')
        self.move_data()
        with patch('law_crawler.download_regulations.fetch', side_effect=AssertionError('offline rebuild')):
            self.assertEqual(rebuild_markdown.main(['--root', str(self.root),
                                                   '--rerun-batch', 'moved-rebuild']), 0)
        batch = self.base / '.batches/moved-rebuild'
        self.assert_history(batch, before)
        self.assertEqual((self.base / 'Canada' / record['original_file']['path']).read_bytes(), XML)
        report = json.loads((batch / 'attempts/0002/report.json').read_text())
        self.assertEqual(report['counts'], {'already_current': 1})
        self.assertEqual(report['network_requests'], 0)

    def test_offline_rebuild_keeps_frozen_converter_version_check(self):
        self.seed_rebuild()
        plan_path = self.old_base / '.batches/moved-rebuild/plan.json'
        plan = json.loads(plan_path.read_text())
        plan['conversion_method'] = CONVERSION_METHOD + '-older'
        plan_path.write_text(json.dumps(plan), encoding='utf-8')
        self.move_data()
        with self.assertRaises(SystemExit):
            rebuild_markdown.main(['--root', str(self.root), '--rerun-batch', 'moved-rebuild'])
        self.assertFalse((self.base / '.batches/moved-rebuild/attempts/0002').exists())

    def import_args(self, *extra):
        return ['--root', str(self.root), *extra]

    def test_import_checks_original_snapshot_then_reads_moved_input_and_keeps_missing_file_errors(self):
        (self.root / 'tmp').mkdir()
        (self.root / 'reports').mkdir()
        (self.root / 'tmp/input.pdf').write_bytes(PDF)
        item = dict(platform='mkri.id', detail_url=MK_DETAIL, download_url=MK_FILE,
                    local_file='../tmp/input.pdf', metadata=metadata(MK_DETAIL))
        (self.root / 'reports/manifest.json').write_text(json.dumps(item), encoding='utf-8')
        self.assertEqual(import_browser_download.main(self.import_args(
            '--jurisdiction', 'Indonesia', '--mode', 'content', '--manifest', 'reports/manifest.json',
            '--acquisition-method', 'official_cli_download', '--batch-id', 'moved-import')), 0)
        before = self.history(self.old_base / '.batches/moved-import')
        self.move_data()
        # 缺失已导入原件时必须从迁移后的 tmp 输入恢复，不能虚报缓存成功。
        next((self.base / 'Indonesia').glob('*.pdf')).unlink()
        self.assertEqual(import_browser_download.main(self.import_args(
            '--rerun-batch', 'moved-import', '--rerun-all')), 0)
        self.assertEqual(next((self.base / 'Indonesia').glob('*.pdf')).read_bytes(), PDF)
        batch = self.base / '.batches/moved-import'
        self.assert_history(batch, before)
        (self.root / 'data/tmp/input.pdf').unlink()
        # 正常重跑保留缓存语义；强制重跑仍要求真实输入文件存在。
        self.assertEqual(import_browser_download.main(self.import_args('--rerun-batch', 'moved-import')), 0)
        self.assertEqual(import_browser_download.main(self.import_args(
            '--rerun-batch', 'moved-import', '--rerun-all')), 1)
        report = json.loads((batch / 'attempts/0004/report.json').read_text())
        self.assertTrue(report['errors'])
        self.assertIn('local_file', report['errors'][0]['error'])
        self.assert_history(batch, before)
        # 将旧快照偷偷换成新路径也须拒绝，不能用路径等价掩盖证据改变。
        snapshot_path = batch / 'manifest-snapshot.json'
        snapshot = json.loads(snapshot_path.read_text())
        snapshot[0]['local_file'] = str(self.root / 'data/tmp/input.pdf')
        changed = json.dumps(snapshot).encode()
        snapshot_path.write_bytes(changed)
        with self.assertRaises(SystemExit):
            import_browser_download.main(self.import_args('--rerun-batch', 'moved-import'))
        report = json.loads((batch / 'attempts/0005/report.json').read_text())
        self.assertIn('manifest 快照已变化', report['errors'][0]['error'])
        self.assertEqual(snapshot_path.read_bytes(), changed)


if __name__ == '__main__':
    unittest.main()
