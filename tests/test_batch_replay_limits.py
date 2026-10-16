"""通过公开执行入口验证重跑限量；所有索引、网络和存储均为临时夹具。"""
import argparse
from collections import Counter
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crawl_store import CrawlStore, unknown_metadata
from platform_adapters.base import Document, field
from regulation_pipeline import execute


def metadata(url):
    name = url.rsplit('/', 1)[-1]
    result = unknown_metadata(url)
    result.update(
        title=field(name, name, 'title', url, 'Fixture title'),
        document_type=field('regulation', 'Act', 'type', url, 'Fixture type'),
        legal_identity=field(
            dict(jurisdiction='DE', issuer='Fixture', type='Act', number=name, year='2026'),
            name, 'identity', url, 'Fixture identity'),
        published_at=field('2026-01-01', '2026-01-01', 'publication', url, 'Fixture date'))
    return result


class PlatformFixture:
    country = 'Germany'

    def __init__(self, platform, names):
        self.platform = platform
        self.allowed_hosts = (platform,)
        self.entry_urls = ('https://' + platform + '/',)
        self.details = [self.entry_urls[0] + name for name in names]

    def discover(self, fetch, limit):
        fetch(self.entry_urls[0])
        return self.details[:limit]

    def read_document(self, url, fetch):
        fetch(url)
        return Document(url, [url + '.pdf'], metadata(url))


class BatchFixture:
    """仅在临时根目录执行，未映射的任何网络请求都使测试失败。"""
    batch_id = 'replay-limits'

    def __init__(self, root, names_a, names_b, limit=6, per_platform=2, duplicates=None):
        self.root = root
        self.adapters = {
            'alpha.invalid': PlatformFixture('alpha.invalid', names_a),
            'beta.invalid': PlatformFixture('beta.invalid', names_b),
        }
        country = root / 'countries/Germany'
        country.mkdir(parents=True)
        (country / 'index.md').write_text('\n'.join(
            f'[{host}](https://{host}/)' for host in self.adapters))
        (root / 'countries/global-config.properties').write_text('downdloadDir=downloads')
        self.options = dict(
            root=root, config=root / 'countries/global-config.properties', index=None,
            jurisdiction='Germany', mode='content', batch_id=self.batch_id,
            rerun_batch=None, rerun_all=False, plan_only=False, platform='all',
            limit=limit, per_platform_limit=per_platform, max_details=20,
            timeout=1, delay=0, refresh=False, refresh_metadata=False,
            max_attempts=1, backoff_base=0, experimental_sources=False)
        self.payloads = {}
        for adapter in self.adapters.values():
            for detail in adapter.details:
                self.payloads[detail + '.pdf'] = b'%PDF-1.4\n' + detail.encode() + b'\n%%EOF'
        for target, source in (duplicates or {}).items():
            self.payloads[target] = self.payloads[source]
        self.pages = {url for adapter in self.adapters.values()
                      for url in (*adapter.entry_urls, *adapter.details)}
        self.calls = []
        self.failures = set()

    @property
    def batch_dir(self):
        return self.root / 'downloads/.batches' / self.batch_id

    @property
    def storage(self):
        return self.root / 'downloads/Germany'

    def fetch(self, url, *args):
        self.calls.append(url)
        if url in self.failures:
            raise TimeoutError('fixture failure')
        if url in self.payloads:
            return self.payloads[url], url
        if url in self.pages:
            return b'fixture page', url
        raise AssertionError('Unexpected network request: ' + url)

    def run(self, replay=False):
        options = dict(self.options)
        if replay:
            options.update(jurisdiction=None, mode=None, batch_id=None,
                           rerun_batch=self.batch_id)
        self.calls = []
        with patch('regulation_pipeline.ADAPTERS', self.adapters), \
                patch('download_regulations.fetch', side_effect=self.fetch):
            result = execute(argparse.Namespace(**options))
        attempts = sorted((self.batch_dir / 'attempts').iterdir())
        self.attempt_dir = attempts[-1]
        return result, json.loads((self.attempt_dir / 'report.json').read_text())

    def originals(self):
        return {str(path): path.read_bytes() for path in self.storage.glob('*.pdf')}


class BatchReplayLimitTests(unittest.TestCase):
    def assert_limits(self, report, total, per_platform):
        self.assertLessEqual(report['unique_files'], total)
        self.assertEqual(report['unique_files'], len(report['files']))
        counts = Counter(item['platform'] for item in report['files'])
        self.assertTrue(all(count <= per_platform for count in counts.values()), counts)
        self.assertEqual(len({item['sha256'] for item in report['files']}), len(report['files']))

    def test_default_replay_reuses_four_successes_without_expanding_six_file_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = BatchFixture(Path(tmp), ['a1', 'a2', 'a3'], ['b1', 'b2', 'b3'])
            result, initial = fixture.run()
            self.assertEqual(result, 0)
            self.assertEqual(initial['unique_files'], 4)
            original_files = fixture.originals()
            plan = (fixture.batch_dir / 'plan.json').read_bytes()
            snapshot = (fixture.batch_dir / 'candidates.json').read_bytes()
            for _ in range(2):
                result, report = fixture.run(replay=True)
                self.assertEqual(result, 0)
                self.assertEqual(fixture.calls, [])
                self.assertEqual(report['unique_files'], 4)
                self.assertEqual(report['counts'], {'previous_success': 4})
                self.assert_limits(report, 6, 2)
                self.assertEqual(fixture.originals(), original_files)
            self.assertEqual((fixture.batch_dir / 'plan.json').read_bytes(), plan)
            self.assertEqual((fixture.batch_dir / 'candidates.json').read_bytes(), snapshot)

    def test_later_successes_reserve_quota_before_earlier_failures_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = BatchFixture(Path(tmp), ['a0', 'a1', 'a2', 'a3'],
                                   ['b0', 'b1', 'b2'], limit=4)
            a0 = 'https://alpha.invalid/a0.pdf'
            b0 = 'https://beta.invalid/b0.pdf'
            b2 = 'https://beta.invalid/b2.pdf'
            fixture.failures = {a0, b0, b2}
            _, initial = fixture.run()
            self.assertEqual(initial['unique_files'], 3)
            initial_hashes = {item['sha256'] for item in initial['files']}
            fixture.failures.clear()
            result, report = fixture.run(replay=True)
            self.assertEqual(result, 0)
            # Alpha's two later successes fill its quota; Beta still has one free slot.
            self.assertEqual(fixture.calls, [b0])
            self.assertEqual(report['unique_files'], 4)
            self.assertEqual(report['counts']['previous_success'], 3)
            self.assertTrue(initial_hashes <= {item['sha256'] for item in report['files']})
            self.assert_limits(report, 4, 2)
            _, stable = fixture.run(replay=True)
            self.assertEqual(fixture.calls, [])
            self.assertEqual(stable['unique_files'], 4)

    def test_legacy_over_limit_successes_are_clipped_without_deleting_originals(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = BatchFixture(Path(tmp), ['a1', 'a2', 'a3'],
                                   ['b1', 'b2', 'b3'], limit=3)
            _, initial = fixture.run()
            self.assertEqual(initial['unique_files'], 3)
            states_path = fixture.attempt_dir / 'states.json'
            states = json.loads(states_path.read_text())
            # Reconstruct an old buggy run which saved limit-skipped candidates too.
            # Seed through the public store API so replay must validate real artifacts.
            with CrawlStore(fixture.storage) as store:
                for state in states['states'].values():
                    url = state.get('url', '')
                    if state['status'] != 'skip' or url not in fixture.payloads:
                        continue
                    _, record = store.save(
                        url, url.removesuffix('.pdf'), url.split('/')[2],
                        lambda target: (fixture.payloads[target], target),
                        metadata(url.removesuffix('.pdf')), convert_text=True)
                    state.update(status='success', file=str(fixture.storage / record['file']['path']))
            states_path.write_text(json.dumps(states))
            originals = fixture.originals()
            self.assertEqual(len(originals), 6)
            result, report = fixture.run(replay=True)
            self.assertEqual(result, 0)
            self.assertEqual(fixture.calls, [])
            self.assertEqual(report['unique_files'], 3)
            self.assertEqual([item['sha256'] for item in report['files']],
                             [item['sha256'] for item in initial['files']])
            self.assert_limits(report, 3, 2)
            self.assertEqual(fixture.originals(), originals)

    def test_duplicate_content_successes_use_one_slot_on_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = BatchFixture(
                Path(tmp), ['a1', 'a2', 'a3'], ['b1'], limit=3,
                duplicates={'https://alpha.invalid/a2.pdf': 'https://alpha.invalid/a1.pdf'})
            _, initial = fixture.run()
            self.assertEqual(initial['unique_files'], 3)
            self.assertEqual(initial['counts']['content_duplicate'], 1)
            self.assertEqual(len(fixture.originals()), 3)
            result, report = fixture.run(replay=True)
            self.assertEqual(result, 0)
            self.assertEqual(fixture.calls, [])
            self.assertEqual(report['unique_files'], 3)
            self.assertEqual(report['counts'], {'previous_success': 3})
            self.assert_limits(report, 3, 2)
            states = json.loads((fixture.attempt_dir / 'states.json').read_text())['states']
            self.assertEqual(sum(state['status'] == 'success' for state in states.values()), 4)

    def test_corrupt_success_is_repaired_without_admitting_limit_skipped_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = BatchFixture(Path(tmp), ['a1', 'a2', 'a3'],
                                   ['b1', 'b2', 'b3'], limit=4)
            _, initial = fixture.run()
            damaged = initial['files'][0]
            path = Path(damaged['file'])
            original_bytes = path.read_bytes()
            path.write_bytes(b'corrupted fixture')
            result, report = fixture.run(replay=True)
            self.assertEqual(result, 0)
            self.assertEqual(fixture.calls, ['https://alpha.invalid/a1.pdf'])
            self.assertEqual(path.read_bytes(), original_bytes)
            self.assertEqual(report['unique_files'], 4)
            self.assertEqual(report['counts']['previous_success'], 3)
            self.assertEqual({item['sha256'] for item in report['files']},
                             {item['sha256'] for item in initial['files']})
            self.assert_limits(report, 4, 2)
            self.assertEqual(len(fixture.originals()), 4)


if __name__ == '__main__':
    unittest.main()
