"""仅用隔离临时目录验证不可变结果发布，不访问真实队列或 OSS。"""
from concurrent.futures import ThreadPoolExecutor
import json
import hashlib
from pathlib import Path
import sqlite3
import threading
import tempfile
import unittest
from unittest.mock import patch

from law_crawler.recover_missing_original import execution_envelope, publish_results
from law_crawler.recover_missing_original import main


class RecoveryResultTests(unittest.TestCase):
    def envelope(self, results, **kwargs):
        plan = dict(batch_id='batch', run_id='run', repairs=[
            dict(target_id='a' * 64), dict(target_id='b' * 64), dict(target_id='c' * 64)])
        return execution_envelope(plan, 'd' * 64, results, attempt_id='attempt',
                                  started_at='2026-01-01T00:00:00Z', **kwargs)

    def test_limit_keeps_all_targets_and_distinguishes_incomplete(self):
        value = self.envelope([dict(target_id='a' * 64, status='incomplete')], limit=1)
        self.assertEqual([x['status'] for x in value['results']],
                         ['incomplete', 'not_scheduled', 'not_scheduled'])
        self.assertEqual(value['results'][1]['reason'], 'limit')
        self.assertEqual(value['plan_sha256'], 'd' * 64)
        self.assertEqual(value['run_id'], 'run')
        self.assertEqual(value['attempt_id'], 'attempt')

    def test_stop_auth_and_unscheduled_are_explicit(self):
        for flags, reason in [({'stopped': True}, 'stopped'),
                              ({'auth_failure': True}, 'auth_failure'), ({}, 'not_dispatched')]:
            self.assertEqual(self.envelope([], **flags)['results'][0]['reason'], reason)

    def test_duplicate_and_unknown_result_fail(self):
        for results in [[dict(target_id='x' * 64)],
                        [dict(target_id='a' * 64)] * 2]:
            with self.assertRaises(ValueError): self.envelope(results)

    def test_publish_preserves_existing_and_unicode(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'result.json'
            publish_results(output, {'说明': '法规原件'})
            before = output.read_bytes()
            self.assertEqual(json.loads(before), {'说明': '法规原件'})
            with self.assertRaises(FileExistsError): publish_results(output, {'other': True})
            self.assertEqual(output.read_bytes(), before)

    def test_parallel_publish_exactly_one_winner_no_partial_result(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'result.json'
            def write(index):
                try:
                    publish_results(output, {'winner': index, 'body': 'x' * 5000})
                    return index
                except FileExistsError:
                    return None
            with ThreadPoolExecutor(max_workers=6) as pool:
                winners = [x for x in pool.map(write, range(12)) if x is not None]
            self.assertEqual(len(winners), 1)
            self.assertEqual(json.loads(output.read_bytes())['winner'], winners[0])
            self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_publication_failure_never_changes_archived_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); archive = root / 'already-archived.json'
            archive.write_text('{"status":"completed"}')
            before = archive.read_bytes()
            with patch('law_crawler.recover_missing_original.os.link', side_effect=OSError('unsupported')):
                with self.assertRaises(OSError): publish_results(root / 'result.json', {'results': []})
            self.assertEqual(archive.read_bytes(), before)
            self.assertEqual(list(root.iterdir()), [archive])


class RecoveryMainResultTests(unittest.TestCase):
    """真实 CLI/锁/SQLite/计划/发布，仅模拟归档传输与执行原件的接口。"""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.batch = Path(temporary.name) / 'batch'
        self.batch.mkdir(); (self.batch / 'outcomes').mkdir()
        self.targets = ['a' * 64, 'b' * 64]; self.source = 'c' * 64
        self.manifest = self.batch / 'manifest.json'
        self.manifest.write_text(json.dumps({'batch_id': 'batch', 'jobs': [[t] for t in self.targets]}))
        (self.batch / 'plan.json').write_text(json.dumps(dict(
            bucket='isolated', endpoint='unused.invalid', region='test', prefix='ods/Test')))
        db = sqlite3.connect(self.batch / 'queue.sqlite3')
        db.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,record TEXT,status TEXT)')
        db.executemany('INSERT INTO jobs VALUES(?,?,?)',
                       [(t, '{}', 'no_original') for t in self.targets] + [(self.source, '{}', 'completed')])
        db.commit(); db.close()
        digest = lambda raw: hashlib.sha256(raw).hexdigest()
        source_raw = b'{"status":"completed","files":[]}'
        (self.batch / 'outcomes' / (self.source + '.json')).write_bytes(source_raw)
        repairs = []
        for target in self.targets:
            raw = b'{"status":"no_original","files":[]}'
            (self.batch / 'outcomes' / (target + '.json')).write_bytes(raw)
            repairs.append(dict(target_id=target, source_id=self.source,
                target_record_sha256=digest(b'{}'), source_record_sha256=digest(b'{}'),
                target_outcome_sha256=digest(raw), source_outcome_sha256=digest(source_raw),
                approved_primary_sha256s=['d' * 64], evidence={'review_status': 'approved'}))
        self.frozen = dict(batch_id='batch', run_id='isolated-run',
                           target_manifest_sha256=digest(self.manifest.read_bytes()), repairs=repairs)
        self.plan = self.batch / 'repair.json'
        self.plan.write_text(json.dumps(self.frozen))
        self.plan_sha = digest(self.plan.read_bytes())
        self.plan.with_suffix('.sha256').write_text(self.plan_sha)
        self.runner_instances = []

    def fake_runner(self, args, batch, db, plan):
        class Sink:
            auth_failure = False
            def put_metadata(self, *args, **kwargs): return {'status': 'uploaded_verified'}
            def flush_cache(self): pass
        class Runner:
            def __init__(self): self.stop = threading.Event(); self.sink = Sink(); self.reports = []
            def report(self, *args): self.reports.append(args)
        runner = Runner(); self.runner_instances.append(runner)
        return runner

    def fake_apply(self, batch, repairs, sink, allowed, run_id):
        item = repairs[0]; target = item['target_id']
        self.assertIn(target, allowed)
        self.assertEqual(run_id, self.frozen['run_id'] + '-' + target[:16])
        db = sqlite3.connect(Path(batch) / 'queue.sqlite3', timeout=10)
        try:
            if db.execute('SELECT status FROM jobs WHERE id=?', (target,)).fetchone()[0] == 'completed':
                return [dict(target_id=target, status='already_completed')]
            db.execute('UPDATE jobs SET status="completed" WHERE id=?', (target,)); db.commit()
            (Path(batch) / 'outcomes' / (target + '.json')).write_text(json.dumps(
                dict(status='completed', recovery={'run_id': run_id})))
            return [dict(target_id=target, status='completed')]
        finally:
            db.close()

    def invoke(self, output, *extra):
        argv = ['recover', '--batch', str(self.batch), '--repair-plan', str(self.plan),
                '--target-manifest', str(self.manifest), '--credentials', 'unused.json',
                '--workers', '2', '--results-output', str(output), *extra]
        with patch('sys.argv', argv), \
             patch('law_crawler.recover_missing_original.Runner', side_effect=self.fake_runner) as runner, \
             patch('law_crawler.recover_missing_original.apply_verified_recovery', side_effect=self.fake_apply) as apply, \
             patch('law_crawler.recover_missing_original.signal.signal'):
            main()
        return runner, apply

    def test_main_first_completion_and_independent_replay_preserve_first_result(self):
        first = self.batch / 'attempt-one.json'; self.invoke(first)
        before = first.read_bytes(); result = json.loads(before)
        self.assertEqual([x['status'] for x in result['results']], ['completed', 'completed'])
        self.assertEqual(result['plan_sha256'], self.plan_sha)
        self.assertEqual(result['run_id'], 'isolated-run')
        self.assertEqual(len(result['attempt_id']), 32)
        second = self.batch / 'attempt-two.json'; self.invoke(second)
        replay = json.loads(second.read_bytes())
        self.assertEqual([x['status'] for x in replay['results']], ['already_completed'] * 2)
        self.assertNotEqual(replay['attempt_id'], result['attempt_id'])
        self.assertEqual(first.read_bytes(), before)
        self.assertEqual((self.batch / 'outcomes' / (self.source + '.json')).read_bytes(),
                         b'{"status":"completed","files":[]}')
        checkpoint = json.loads((self.batch / 'execution-results.json').read_bytes())
        self.assertIsInstance(checkpoint, list)
        self.assertEqual({x['target_id'] for x in checkpoint}, set(self.targets))

    def test_main_existing_result_refused_before_runner_or_recovery(self):
        output = self.batch / 'attempt.json'; self.invoke(output)
        before = output.read_bytes()
        with patch('law_crawler.recover_missing_original.Runner') as runner, \
             patch('law_crawler.recover_missing_original.apply_verified_recovery') as apply, \
             patch('sys.argv', ['recover', '--batch', str(self.batch), '--repair-plan', str(self.plan),
                 '--target-manifest', str(self.manifest), '--credentials', 'unused', '--results-output', str(output)]):
            with self.assertRaises(FileExistsError): main()
            runner.assert_not_called(); apply.assert_not_called()
        self.assertEqual(output.read_bytes(), before)

    def test_main_limit_retains_every_target_without_claiming_full_execution(self):
        output = self.batch / 'limited.json'; _, apply = self.invoke(output, '--limit', '1')
        self.assertEqual(apply.call_count, 1)
        result = json.loads(output.read_bytes())
        self.assertEqual([x['status'] for x in result['results']], ['completed', 'not_scheduled'])
        self.assertEqual(result['results'][1]['reason'], 'limit')
        db = sqlite3.connect(self.batch / 'queue.sqlite3')
        self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?', (self.targets[1],)).fetchone()[0], 'no_original')
        db.close()
        self.assertEqual(len(json.loads((self.batch / 'execution-results.json').read_bytes())), 1)


if __name__ == '__main__':
    unittest.main()
