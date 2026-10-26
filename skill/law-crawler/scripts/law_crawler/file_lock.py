"""标准库跨平台非阻塞互斥锁；锁文件长期保留，防止删除重建绕过互斥。"""
import errno
import importlib
import os
from pathlib import Path


def _backend():
    """仅加载本机锁实现；不支持时明确报错，不能无锁继续写入。"""
    name = {'posix': 'fcntl', 'nt': 'msvcrt'}.get(os.name)
    if name is None:
        raise ValueError(f'当前系统 {os.name!r} 不支持安全文件锁；请使用 macOS、Linux 或 Windows 的标准 Python 运行环境')
    try:
        return name, importlib.import_module(name)
    except ImportError as exc:
        raise ValueError(f'当前 Python 缺少系统文件锁模块 {name}；请使用包含该标准库模块的完整 Python 运行环境') from exc


class FileLockBusy(ValueError):
    """锁已被其他执行者持有；与权限、路径或文件系统错误区分。"""


class FileLock:
    """立即取得独占锁；冲突不等待。close 可重复调用，且从不删除锁文件。"""
    def __init__(self, path, busy_message='已有任务持有执行锁，请稍后重试'):
        self.path = Path(path)
        self.stream = None
        self.locked = False
        self.backend_name, self.backend = _backend()
        if self.path.is_symlink():
            raise ValueError(f'执行锁文件不得使用符号链接: {self.path}')
        try:
            self.stream = self.path.open('a+b')
        except OSError as exc:
            raise ValueError(f'无法打开执行锁 {self.path}: {exc}；请检查目录和写入权限') from exc
        try:
            if self.backend_name == 'msvcrt':
                # Windows 锁定首字节；空文件先写入哨兵，追加写不截断既有锁文件。
                self.stream.seek(0, os.SEEK_END)
                if self.stream.tell() == 0:
                    self.stream.write(b'\0')
                    self.stream.flush()
                self.stream.seek(0)
                self.backend.locking(self.stream.fileno(), self.backend.LK_NBLCK, 1)
            else:
                self.backend.flock(self.stream, self.backend.LOCK_EX | self.backend.LOCK_NB)
            self.locked = True
        except OSError as exc:
            if self.stream is not None:
                self.stream.close()
            busy = (exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK) if self.backend_name != 'msvcrt'
                    else exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) or getattr(exc, 'winerror', None) in (33, 36))
            if busy:
                raise FileLockBusy(busy_message) from None
            raise ValueError(f'无法取得执行锁 {self.path}: {exc}；请检查目录权限和文件系统是否支持文件锁') from exc

    def close(self):
        if self.stream is None or self.stream.closed:
            return
        try:
            if self.locked:
                if self.backend_name == 'msvcrt':
                    self.stream.seek(0)
                    self.backend.locking(self.stream.fileno(), self.backend.LK_UNLCK, 1)
                else:
                    self.backend.flock(self.stream, self.backend.LOCK_UN)
                self.locked = False
        finally:
            self.stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
