"""批量下载只读进度：状态、计数、并发及安全边界。"""
import json
from pathlib import Path
import tempfile
import unittest

from observer.download_progress import DownloadProgressReader


class DownloadProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = self.root/'workspace'; self.workspace.mkdir()
        self.base = self.root/'downloads'; self.runtime = self.root/'runtime'
        self.batch = self.base/'Indonesia/pipeline/bulk/id-test'
        self.pointer = self.runtime/'server-download-test/active-download.json'
        self.status = self.batch/'fast-download/status.json'
        self.write(self.pointer, dict(batch_id='id-test', state=str(self.batch/'state.json'),
                   unit='law-crawler-download-test.service', workers=5))
        self.summary = dict(total_details=10, attempted_details=10, successful_files=12,
                            failed_file_references=2, failure_reasons=[{'reason':'HTTP 403', 'count':2}],
                            run_workers=2, updated_at='2026-10-22T03:09:35+00:00')
        self.write(self.status, dict(phase='finished', download_complete=False, summary=self.summary))
        self.service = {'ActiveState':'failed', 'MainPID':'0', 'ExecMainStatus':'1'}

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def read(self):
        return DownloadProgressReader(self.workspace, self.base, self.runtime,
                                      service_reader=lambda _: self.service).snapshot()

    def test_finished_exit_one_is_finished_with_gaps_config_not_running(self):
        row = self.read()
        self.assertTrue(row['available']); self.assertEqual(row['status'], 'finished')
        self.assertTrue(row['has_gaps']); self.assertEqual(row['successful_files'], 12)
        self.assertEqual(row['workers'], 5); self.assertEqual(row['run_workers'], 2)
        self.assertEqual(row['active_concurrency_limit'], 0)
        self.assertEqual(row['updated_at'], self.summary['updated_at'])
        self.assertEqual(row['failed_file_references'], 2)
        # 不创建或读取大型state，也不暴露内部绝对路径。
        self.assertFalse((self.batch/'state.json').exists())
        self.assertNotIn(str(self.root), json.dumps(row))

    def test_operation_partition_and_stopped_active_task_requeues(self):
        self.summary['operations'] = dict(total=10,completed=6,active=2,pending=1,failed=1,needs_verification=0)
        self.summary['identity_counts'] = dict(regulations=4, identity_pending_details=3, versions=None)
        self.summary['file_counts'] = dict(unique_downloaded_files=12, body_files=9, annex_files=1, role_pending_files=2)
        self.summary['timing'] = dict(started_at='2026-10-22T02:09:35+00:00',ended_at='2026-10-22T03:09:35+00:00',elapsed_seconds=3600)
        self.write(self.status, dict(phase='stopped', summary=self.summary))
        row=self.read()
        self.assertEqual(row['operations']['active'],0)
        self.assertEqual(row['operations']['pending'],3)
        self.assertEqual(row['identity_counts']['regulations'],4)
        self.assertIsNone(row['identity_counts']['versions'])
        self.assertEqual(row['file_counts']['annex_files'],1)
        self.assertEqual(row['timing']['elapsed_seconds'],3600)
        self.assertIsNone(row['timing']['active_download_seconds'])

    def test_invalid_task_sum_is_unknown_not_fake_completion(self):
        self.summary['operations']=dict(total=10,completed=10,active=1,pending=0,failed=0,needs_verification=0)
        self.write(self.status,dict(phase='finished',summary=self.summary))
        row=self.read()
        self.assertFalse(row['operations']['partition_valid'])
        self.assertIsNone(row['operations']['completed'])

    def test_running_stopped_and_stale_capture(self):
        for service, phase, expected in [('active','capture','running'), ('inactive','stopped','paused'),
                                         ('failed','capture','paused'), ('active','finished','running')]:
            with self.subTest(service=service, phase=phase):
                self.service['ActiveState'] = service
                self.service['MainPID'] = '123' if service == 'active' else '0'
                self.service['SubState'] = 'running' if service == 'active' else 'dead'
                self.write(self.status, dict(phase=phase, summary=self.summary))
                self.assertEqual(self.read()['status'], expected)

    def test_exited_service_without_pid_is_not_downloading(self):
        self.service = {'ActiveState':'active', 'SubState':'exited', 'MainPID':'0'}
        self.assertEqual(self.read()['status'], 'finished')

    def test_missing_service_is_unknown_for_stale_capture(self):
        self.service = {}
        self.write(self.status, dict(phase='capture', summary=self.summary))
        row = self.read(); self.assertEqual(row['status'], 'unknown')
        self.assertIsNone(row['active_concurrency_limit'])

    def test_backfilled_summary_uses_original_download_time(self):
        self.write(self.status, dict(phase='finished', download_complete=False))
        self.write(self.status.with_name('progress.json'), self.summary)
        self.assertEqual(self.read()['updated_at'], self.summary['updated_at'])

    def test_invalid_pointer_paths_units_and_symlinks_are_rejected(self):
        original = json.loads(self.pointer.read_text())
        for change in [dict(state='/etc/passwd'), dict(batch_id='../escape'),
                       dict(unit='nginx.service'), dict(state=str(self.base/'../secret/pipeline/bulk/id-test/state.json'))]:
            with self.subTest(change=change):
                self.write(self.pointer, {**original, **change})
                self.assertFalse(self.read()['available'])
        self.write(self.pointer, original)
        self.status.unlink(); self.status.symlink_to(self.pointer)
        self.assertFalse(self.read()['available'])

    def test_malformed_or_large_summary_is_isolated(self):
        self.status.write_text('[' * 2000 + ']' * 2000)
        self.assertFalse(self.read()['available'])
        self.status.write_text('{bad')
        self.assertFalse(self.read()['available'])
        self.status.write_text(' ' * (129 * 1024))
        self.assertFalse(self.read()['available'])

    def test_missing_pointer_does_not_create_runtime(self):
        reader = DownloadProgressReader(self.workspace, self.base, self.root/'absent')
        self.assertFalse(reader.snapshot()['available'])
        self.assertFalse((self.root/'absent').exists())


class CensusOSSProgressTests(DownloadProgressTests):
    def census(self, **overrides):
        self.batch = self.base/'Indonesia/pipeline/census-download/id-test'
        self.write(self.pointer, dict(batch_id='id-test', state=str(self.batch/'status.json'),
            unit='law-crawler-census-oss.service', workers=5, snapshot_file='census-oss-status.json'))
        self.public = self.pointer.with_name('census-oss-status.json')
        value = dict(batch_id='id-test', status='running', total=100, completed=30, downloading=4,
            pending=55, failed=2, incomplete=6, no_original=3, attempted=41,
            unique_oss_files=45, oss_bytes=12345678, workers=5, elapsed_this_run_seconds=90.25,
            started_at='2026-10-22T01:00:00+00:00', updated_at='2026-10-22T01:02:00+00:00',
            failure_reasons=[dict(reason='HTTP 403',count=8)])
        self.write(self.public, {**value, **overrides})
        self.service = dict(ActiveState='active',SubState='running',MainPID='42')

    def test_census_public_summary_without_private_directory_access(self):
        self.census()
        row=self.read()
        self.assertTrue(row['available']); self.assertEqual(row['status'],'running')
        self.assertEqual(row['batch_kind'],'census_oss')
        self.assertEqual(row['operations'], dict(total=100,completed=30,active=4,pending=55,
            failed=8,needs_verification=3,unit='条详情归档任务',partition_valid=True))
        self.assertEqual(row['oss'],dict(unique_files=45,bytes=12345678))
        self.assertEqual(row['task_outcomes'],dict(failed=2,incomplete=6,no_original=3))
        self.assertEqual(row['failure_reasons'],[dict(reason='HTTP 403',count=8)])
        self.assertEqual(row['timing']['run_elapsed_seconds'],90)
        self.assertIsNone(row['timing']['run_started_at'])
        self.assertTrue(row['has_gaps']); self.assertTrue(row['stale'])
        self.assertFalse(self.batch.exists())
        self.assertNotIn(str(self.root), json.dumps(row))

    def test_census_stopped_requeues_active_and_preserves_failures(self):
        self.census()
        self.service=dict(ActiveState='inactive',SubState='dead',MainPID='0')
        row=self.read()
        self.assertEqual(row['status'],'paused')
        self.assertEqual(row['operations']['active'],0)
        self.assertEqual(row['operations']['pending'],59)
        self.assertEqual(row['operations']['failed'],8)
        self.assertEqual(row['timing']['elapsed_seconds'],120)
        self.assertEqual(row['timing']['run_elapsed_seconds'],90)

    def test_census_unknown_service_does_not_claim_live_counts(self):
        self.census(); self.service={}
        row=self.read()
        self.assertEqual(row['status'],'unknown')
        self.assertIsNone(row['operations']['active'])
        self.assertFalse(row['operations']['partition_valid'])

    def test_census_invalid_sum_or_negative_is_not_displayed_as_complete(self):
        for change in [dict(total=99),dict(downloading=-1),dict(completed=True)]:
            self.census(**change)
            row=self.read()
            self.assertFalse(row['operations']['partition_valid'])
            self.assertIsNone(row['operations']['completed'])

    def test_census_rejects_mismatched_batch_and_public_path(self):
        self.census(batch_id='other')
        self.assertFalse(self.read()['available'])
        self.census()
        pointer=json.loads(self.pointer.read_text()); pointer['snapshot_file']='../secret.json'
        self.write(self.pointer,pointer)
        self.assertFalse(self.read()['available'])
        self.census()
        self.public.unlink(); self.public.symlink_to(self.pointer)
        self.assertFalse(self.read()['available'])

    def test_census_finished_is_finished_with_gaps(self):
        self.census(status='finished_with_gaps',downloading=0,pending=59)
        self.service=dict(ActiveState='inactive',SubState='dead',MainPID='0')
        row=self.read()
        self.assertEqual(row['status'],'finished')
        self.assertTrue(row['has_gaps'])


if __name__ == '__main__':
    unittest.main()
