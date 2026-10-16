import errno
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import file_lock
from file_lock import FileLock
from batch_runtime import BatchRuntime
from crawl_store import CrawlStore


class FileLockTests(unittest.TestCase):
    def test_real_cross_process_mutex_and_persistent_file(self):
        child = '''import sys
from file_lock import FileLock
try:
    lock = FileLock(sys.argv[1], 'busy')
except ValueError as exc:
    print(str(exc))
else:
    lock.close()
    print('acquired')
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, '.execution.lock')
            with FileLock(path):
                result = subprocess.run([sys.executable, '-c', child, str(path)],
                    cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True)
                self.assertEqual(result.stdout.strip(), 'busy')
            self.assertTrue(path.is_file(), '不能删除锁文件，否则旧句柄与新文件可同时取得锁')
            result = subprocess.run([sys.executable, '-c', child, str(path)],
                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), 'acquired')

    def test_windows_branch_locks_first_byte_nonblocking_and_releases(self):
        calls = []
        owner = []

        def locking(fd, operation, count):
            calls.append((operation, count, os.lseek(fd, 0, os.SEEK_CUR)))
            if operation == 1:
                self.assertGreaterEqual(os.fstat(fd).st_size, 1)
                if owner:
                    raise OSError(errno.EACCES, 'locked')
                owner.append(fd)
            else:
                self.assertEqual(owner.pop(), fd)

        windows = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=locking)
        with tempfile.TemporaryDirectory() as directory, patch.object(file_lock, '_backend', return_value=('msvcrt', windows)):
            path = Path(directory, '.crawler.lock')
            first = FileLock(path)
            with self.assertRaisesRegex(ValueError, '已有任务'):
                FileLock(path)
            first.close()
            first.close()
            with FileLock(path):
                pass
            self.assertEqual(calls, [(1, 1, 0), (1, 1, 0), (2, 1, 0), (1, 1, 0), (2, 1, 0)])
            self.assertEqual(path.read_bytes(), b'\0')

    def test_missing_or_unsupported_backend_explains_requirement(self):
        with patch.object(file_lock, 'os', SimpleNamespace(name='unknown')):
            with self.assertRaisesRegex(ValueError, '不支持安全文件锁'):
                file_lock._backend()
        with patch.object(file_lock, 'os', SimpleNamespace(name='nt')), patch.object(file_lock.importlib, 'import_module', side_effect=ImportError('missing')):
            with self.assertRaisesRegex(ValueError, '缺少系统文件锁模块 msvcrt'):
                file_lock._backend()

    def test_open_permission_error_is_not_reported_as_contention(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Path, 'open', side_effect=PermissionError(errno.EACCES, 'denied')):
            with self.assertRaisesRegex(ValueError, '无法打开执行锁.*写入权限'):
                FileLock(Path(directory, 'lock'))

    def test_filesystem_lock_failure_closes_handle_and_explains_failure(self):
        backend = SimpleNamespace(LOCK_EX=1, LOCK_NB=2,
            flock=lambda *args: (_ for _ in ()).throw(OSError(errno.ENOSYS, 'unsupported')))
        with tempfile.TemporaryDirectory() as directory, patch.object(file_lock, '_backend', return_value=('fcntl', backend)):
            with self.assertRaisesRegex(ValueError, '文件系统是否支持文件锁'):
                FileLock(Path(directory, 'lock'), 'busy')
        # Windows 临时目录删除要求句柄关闭；异常路径不得留下打开句柄。

    def test_store_init_failure_releases_country_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('crawl_store.sqlite3.connect', side_effect=sqlite3.OperationalError('unavailable')):
                with self.assertRaises(sqlite3.OperationalError):
                    CrawlStore(directory)
            with CrawlStore(directory):
                pass

    def test_batch_mutex_is_scoped_to_one_batch_and_init_failure_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            first = BatchRuntime(directory, {}, batch_id='a')
            with self.assertRaisesRegex(ValueError, '同一批次已有执行任务'):
                BatchRuntime(directory, rerun_from='a')
            with BatchRuntime(directory, {}, batch_id='b'):
                pass
            first.finish()
            with patch.object(BatchRuntime, '_persist_states', side_effect=OSError('disk unavailable')):
                with self.assertRaisesRegex(OSError, 'disk unavailable'):
                    BatchRuntime(directory, rerun_from='a')
            with BatchRuntime(directory, rerun_from='a') as resumed:
                self.assertTrue((resumed.batch_dir / '.execution.lock').exists())
                self.assertEqual(resumed.attempt_number, 3)


if __name__ == '__main__':
    unittest.main()
