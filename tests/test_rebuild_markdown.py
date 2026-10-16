"""离线重建公开CLI端到端测试；所有索引、字节、批次与SQLite均在临时目录。"""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from crawl_store import CrawlStore, write_json
import rebuild_markdown as cli
from text_documents import CONVERSION_METHOD


SOURCE = 'https://official.example/source.xml'
FINAL = 'https://official.example/frozen.xml'
RAW = (b'<Regulation xmlns:lims="http://justice.gc.ca/lims" lims:id="fixture">'
       b'<Identification><LongTitle>Fixture regulation</LongTitle><InstrumentNumber>SOR/2007-151</InstrumentNumber>'
       b'<RegistrationDate><Date><YYYY>2007</YYYY><MM>6</MM><DD>14</DD></Date></RegistrationDate>'
       b'</Identification><Body><Section><Label>1</Label><Text>Original legal wording.</Text></Section></Body></Regulation>')
LEGACY = b'# Frozen title\n\n2007\n\n6\n\n14\n\n## 1\n\nOriginal legal wording.\n'


class RebuildFixture:
    def __init__(self, root):
        self.root = root
        (root / 'countries/Canada').mkdir(parents=True)
        (root / 'countries/Canada/index.md').write_text('[Justice](https://laws-lois.justice.gc.ca/)\n')
        (root / 'countries/global-config.properties').write_text('downdloadDir=originals\n')
        self.storage = root / 'originals/Canada'
        self.batch = root / 'originals/.batches/offline-fixture'

    def seed(self, suffix='', raw=RAW):
        metadata = {name: {'value': value, 'source_url': FINAL + suffix} for name, value in
                    [('document_type', 'regulation'), ('title', 'Frozen title')]}
        with CrawlStore(self.storage) as store:
            _, record = store.save(SOURCE + suffix, 'https://official.example/detail' + suffix,
                                   'official.example', lambda url: (raw, FINAL + suffix),
                                   metadata=metadata, convert_text=True)
            path = self.storage / record['file']['path']
            path.write_bytes(LEGACY)
            record['file'].update(sha256=hashlib.sha256(LEGACY).hexdigest(), size_bytes=len(LEGACY))
            record['conversion']['method'] = 'structured_text_v1'
            write_json(path.with_suffix('.json'), record)
            store._index(record)
            store.db.commit()
        return copy.deepcopy(record)

    def sidecar(self, record):
        return self.storage / Path(record['file']['path']).with_suffix('.json')

    def request_snapshot(self):
        with contextlib.closing(sqlite3.connect(self.storage / 'crawl-state.sqlite3')) as database:
            return database.execute('SELECT * FROM requests ORDER BY canonical_url').fetchall()

    def run(self, replay=False, *extra):
        args = ['--root', str(self.root), '--jurisdiction', 'Canada',
                '--rerun-batch' if replay else '--batch-id', 'offline-fixture', *extra]
        output = io.StringIO()
        # 真实网络必须被阻断；CLI也不能通过抓取补齐缺失原件。
        with patch('urllib.request.urlopen', side_effect=AssertionError('离线CLI不得联网')), \
                patch('socket.create_connection', side_effect=AssertionError('离线CLI不得联网')), \
                contextlib.redirect_stdout(output):
            status = cli.main(args)
        summary = json.loads(output.getvalue())
        attempt = self.batch / 'attempts' / f"{summary['attempt']:04d}"
        return status, json.loads((attempt / 'report.json').read_text()), attempt


class RebuildMarkdownCLITests(unittest.TestCase):
    def test_offline_cli_rebuilds_with_reports_backup_and_preserves_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RebuildFixture(Path(tmp))
            original = fixture.seed()
            before_requests = fixture.request_snapshot()
            original_path = fixture.storage / original['original_file']['path']
            raw_sidecar = original_path.with_name(original_path.name + '.metadata.json')
            status, report, attempt = fixture.run()
            self.assertEqual(status, 0)
            self.assertEqual(report['result'], 'completed')
            self.assertEqual(report['counts'], {'rebuilt': 1})
            self.assertEqual(report['network_requests'], 0)
            self.assertEqual(report['errors'], [])
            key = original['original_file']['sha256']
            self.assertEqual(report['states'][key]['status'], 'success')
            self.assertEqual(report['states'][key]['attempts'], 0)
            updated = json.loads(fixture.sidecar(original).read_text())
            self.assertEqual(updated['conversion']['method'], CONVERSION_METHOD)
            self.assertEqual(updated['original_file'], original['original_file'])
            self.assertEqual(updated['sources'], original['sources'])
            self.assertEqual(updated['conversion']['metadata'], original['conversion']['metadata'])
            self.assertEqual(updated['conversion']['source_url'], FINAL)
            self.assertEqual(original_path.read_bytes(), RAW)
            self.assertEqual(fixture.request_snapshot(), before_requests)
            markdown = (fixture.storage / updated['file']['path']).read_text()
            self.assertIn('RegistrationDate', markdown)
            self.assertIn('2007-06-14', markdown)
            self.assertIn('## Section 1', markdown)
            self.assertIn('Original legal wording.', markdown)
            self.assertEqual(updated['file']['sha256'], hashlib.sha256(markdown.encode()).hexdigest())
            self.assertEqual(json.loads(raw_sidecar.read_text())['file'], original['original_file'])
            backup = fixture.storage / 'conversion-history' / key / (hashlib.sha256(LEGACY).hexdigest() + '.md')
            self.assertEqual(backup.read_bytes(), LEGACY)
            backup_metadata = fixture.storage / updated['conversion_history'][-1]['backup_metadata']
            self.assertEqual(json.loads(backup_metadata.read_text())['conversion']['method'], 'structured_text_v1')
            plan = json.loads((fixture.batch / 'plan.json').read_text())
            self.assertEqual(plan['jurisdiction'], 'Canada')
            self.assertEqual(len(plan['files']), 1)
            self.assertEqual(plan['files'][0]['source_url'], FINAL)
            for parent, name in [(attempt, 'report'), (fixture.storage, 'markdown-rebuild-last-run')]:
                for suffix in ['json', 'md', 'html']:
                    artifact = parent / f'{name}.{suffix}'
                    self.assertTrue(artifact.is_file(), str(artifact))
                    self.assertGreater(artifact.stat().st_size, 0)
                self.assertEqual(json.loads((parent / f'{name}.json').read_text())['result'], 'completed')
                self.assertIn('rebuilt', (parent / f'{name}.md').read_text())
                self.assertIn('rebuilt', (parent / f'{name}.html').read_text())

    def test_rerun_batch_is_idempotent_and_keeps_frozen_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RebuildFixture(Path(tmp))
            record = fixture.seed()
            fixture.run()
            frozen_plan = (fixture.batch / 'plan.json').read_bytes()
            markdown_path = fixture.storage / record['file']['path']
            markdown = markdown_path.read_bytes()
            updated = json.loads(fixture.sidecar(record).read_text())
            history = copy.deepcopy(updated['conversion_history'])
            before_requests = fixture.request_snapshot()
            backups = {str(p): p.read_bytes() for p in (fixture.storage / 'conversion-history').rglob('*') if p.is_file()}
            for expected_attempt in [2, 3]:
                status, report, _ = fixture.run(True)
                self.assertEqual(status, 0)
                self.assertEqual(report['attempt_number'], expected_attempt)
                self.assertEqual(report['counts'], {'already_current': 1})
                self.assertEqual(report['network_requests'], 0)
                self.assertEqual((fixture.batch / 'plan.json').read_bytes(), frozen_plan)
                self.assertEqual(markdown_path.read_bytes(), markdown)
                self.assertEqual(json.loads(fixture.sidecar(record).read_text())['conversion_history'], history)
                self.assertEqual(fixture.request_snapshot(), before_requests)
                self.assertEqual({str(p): p.read_bytes() for p in (fixture.storage / 'conversion-history').rglob('*') if p.is_file()}, backups)

    def test_unknown_jurisdiction_rejected_before_any_output_directory_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            RebuildFixture(root)
            before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['--root', tmp, '--jurisdiction', 'Atlantis', '--batch-id', 'invalid-country']), 1)
            self.assertIn('错误:', errors.getvalue())
            self.assertNotIn('Traceback', errors.getvalue())
            self.assertFalse((root / 'originals').exists())
            self.assertEqual({str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}, before)

    def test_corrupt_raw_fails_individually_and_does_not_prevent_other_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RebuildFixture(Path(tmp))
            good = fixture.seed()
            bad_raw = RAW.replace(b'Original legal wording.', b'Second original legal wording.')
            bad = fixture.seed('?second', bad_raw)
            bad_path = fixture.storage / bad['original_file']['path']
            damaged = bad_raw.replace(b'Second original legal wording.', b'Tampered legal wording.')
            bad_path.write_bytes(damaged)
            before_requests = fixture.request_snapshot()
            status, report, _ = fixture.run()
            self.assertEqual(status, 1)
            self.assertEqual(report['result'], 'completed_with_errors')
            self.assertEqual(report['counts'], {'rebuilt': 1})
            self.assertEqual(len(report['errors']), 1)
            self.assertEqual(len(report['files']), 1)
            self.assertEqual(report['files'][0]['original_sha256'], good['original_file']['sha256'])
            self.assertEqual(report['states'][bad['original_file']['sha256']]['status'], 'fail')
            self.assertEqual(report['states'][bad['original_file']['sha256']]['attempts'], 0)
            self.assertEqual(report['states'][good['original_file']['sha256']]['status'], 'success')
            self.assertEqual(bad_path.read_bytes(), damaged)
            self.assertEqual((fixture.storage / bad['file']['path']).read_bytes(), LEGACY)
            self.assertEqual(json.loads(fixture.sidecar(bad).read_text())['conversion']['method'], 'structured_text_v1')
            self.assertFalse((fixture.storage / 'conversion-history' / bad['original_file']['sha256']).exists())
            self.assertEqual(fixture.request_snapshot(), before_requests)

    def test_rerun_refuses_changed_frozen_conversion_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RebuildFixture(Path(tmp))
            record = fixture.seed()
            fixture.run()
            sidecar = fixture.sidecar(record)
            altered = json.loads(sidecar.read_text())
            altered['conversion']['source_url'] = 'https://official.example/other.xml'
            write_json(sidecar, altered)
            before_requests = fixture.request_snapshot()
            before_markdown = (fixture.storage / record['file']['path']).read_bytes()
            status, report, _ = fixture.run(True)
            self.assertEqual(status, 1)
            self.assertEqual(report['result'], 'completed_with_errors')
            self.assertEqual(report['files'], [])
            self.assertEqual(len(report['errors']), 1)
            self.assertEqual(report['states'][record['original_file']['sha256']]['status'], 'fail')
            self.assertIn('冻结', report['errors'][0]['error'])
            self.assertEqual((fixture.storage / record['file']['path']).read_bytes(), before_markdown)
            self.assertEqual(fixture.request_snapshot(), before_requests)

    def test_rejected_source_drift_cannot_be_rebuilt_as_a_selection_side_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RebuildFixture(Path(tmp))
            record = fixture.seed()
            fixture.run()
            sidecar = fixture.sidecar(record)
            altered = json.loads(sidecar.read_text())
            altered['conversion']['source_url'] = 'https://official.example/changed.xml'
            altered['conversion']['method'] = 'structured_text_v1'
            write_json(sidecar, altered)
            old_markdown = (fixture.storage / record['file']['path']).read_bytes()
            old_history = copy.deepcopy(altered['conversion_history'])
            status, report, _ = fixture.run(True)
            self.assertEqual(status, 1)
            self.assertEqual(report['files'], [])
            self.assertEqual(report['states'][record['original_file']['sha256']]['status'], 'fail')
            after = json.loads(sidecar.read_text())
            self.assertEqual(after['conversion'], altered['conversion'], '拒绝来源漂移后不能通过更新当前清单偷偷重建')
            self.assertEqual(after['conversion_history'], old_history)
            self.assertEqual((fixture.storage / record['file']['path']).read_bytes(), old_markdown)

    def test_country_directory_symlink_cannot_redirect_outputs_outside_country(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RebuildFixture(Path(tmp))
            outside = Path(tmp) / 'other-country'
            outside.mkdir()
            marker = outside / 'untouched.txt'
            marker.write_text('Keep this evidence')
            fixture.storage.parent.mkdir()
            fixture.storage.symlink_to(outside, target_is_directory=True)
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['--root', tmp, '--jurisdiction', 'Canada', '--batch-id', 'invalid-path']), 1)
            self.assertEqual(marker.read_text(), 'Keep this evidence')
            self.assertEqual(list(outside.iterdir()), [marker])
            self.assertFalse((Path(tmp) / 'originals/.batches').exists())

    def test_derived_file_path_escape_is_a_per_file_failure_without_external_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RebuildFixture(Path(tmp))
            record = fixture.seed()
            outside = fixture.storage.parent / 'outside.md'
            outside.write_text('Outside must stay unchanged')
            sidecar = fixture.sidecar(record)
            record['file']['path'] = '../outside.md'
            write_json(sidecar, record)
            before_requests = fixture.request_snapshot()
            status, report, _ = fixture.run()
            self.assertEqual(status, 1)
            self.assertEqual(report['files'], [])
            self.assertEqual(len(report['errors']), 1)
            self.assertEqual(report['states'][record['original_file']['sha256']]['status'], 'fail')
            self.assertEqual(outside.read_text(), 'Outside must stay unchanged')
            self.assertEqual(fixture.request_snapshot(), before_requests)

    def test_cli_precondition_errors_have_readable_stderr_without_traceback(self):
        script = Path(cli.__file__).resolve()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = RebuildFixture(root)
            cases = [(['--jurisdiction', 'Atlantis'], '未在工程索引中登记'),
                     (['--jurisdiction', 'Canada', '--config', 'missing.properties'], '配置文件不存在'),
                     (['--rerun-batch', 'missing-batch'], '找不到原批次计划')]
            for arguments, expected in cases:
                with self.subTest(arguments=arguments):
                    result = subprocess.run([sys.executable, str(script), '--root', str(root), *arguments],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn('错误:', result.stderr)
                    self.assertIn(expected, result.stderr)
                    self.assertNotIn('Traceback', result.stderr)
                    self.assertEqual(result.stdout, '')
            self.assertFalse(fixture.storage.parent.exists())

    def test_cli_argument_errors_keep_argparse_status_two(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            cli.main([])
        self.assertEqual(raised.exception.code, 2)

    def test_non_ascii_batch_replay_does_not_depend_on_default_text_encoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RebuildFixture(Path(tmp))
            fixture.seed()
            read_text = Path.read_text

            def explicit_encoding(path, *args, **kwargs):
                self.assertIn(kwargs.get('encoding'), ('utf-8', 'utf-8-sig'),
                              f'读取 {path} 不得依赖操作系统默认编码')
                return read_text(path, *args, **kwargs)

            for option in ('--batch-id', '--rerun-batch'):
                with patch.object(Path, 'read_text', autospec=True, side_effect=explicit_encoding), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(['--root', str(fixture.root), '--jurisdiction', 'Canada',
                                               option, 'offline-fixture']), 0)
            plan = json.loads((fixture.batch / 'plan.json').read_text(encoding='utf-8'))
            self.assertIn('冻结原响应身份', plan['steps'][0])

    def test_malformed_rerun_plan_is_a_readable_failure_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RebuildFixture(Path(tmp))
            fixture.batch.mkdir(parents=True)
            for payload in ('invalid-json', '[]', '{}'):
                with self.subTest(payload=payload):
                    (fixture.batch / 'plan.json').write_text(payload, encoding='utf-8')
                    output = io.StringIO()
                    with contextlib.redirect_stderr(output):
                        status = cli.main(['--root', tmp, '--rerun-batch', 'offline-fixture'])
                    self.assertEqual(status, 1)
                    self.assertIn('错误:', output.getvalue())
                    self.assertNotIn('Traceback', output.getvalue())
                    self.assertFalse((fixture.batch / 'attempts').exists())
                    self.assertEqual((fixture.batch / 'plan.json').read_text(encoding='utf-8'), payload)


if __name__ == '__main__':
    unittest.main()
