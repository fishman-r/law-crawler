"""独立原始响应归档。saved 仅说明传输完整，不代表法规身份或有效性。"""
import hashlib
import codecs
from contextlib import contextmanager
from datetime import datetime, timezone
import http.client
import json
import math
import os
from pathlib import Path
import tempfile
import time
from urllib import error, parse, request
import uuid

from law_crawler.file_lock import FileLock, FileLockBusy
from law_crawler.download_transport import (TRANSIENT_STATUS, retry_after_seconds,
    stream_response, is_retryable, retry_delay, wait_delay, RetryDeferred)

_SAFE_HEADERS = {'content-type', 'content-length', 'content-encoding', 'etag',
                 'last-modified', 'date', 'retry-after'}
_TRANSIENT = TRANSIENT_STATUS


def _url(value):
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError('URL 必须是有效 HTTP(S) 字符串')
    p = parse.urlsplit(value)
    if p.scheme.lower() not in {'http', 'https'} or not p.hostname or p.username is not None or p.password is not None:
        raise ValueError('URL 必须使用 HTTP(S)，且不得含用户凭据')
    host = p.hostname.lower()
    host = '[' + host + ']' if ':' in host else host
    if p.port is not None and (p.scheme.lower(), p.port) not in {('http', 80), ('https', 443)}:
        host += ':' + str(p.port)
    return parse.urlunsplit((p.scheme.lower(), host, p.path or '/', p.query, ''))


def _safe(path):
    path = Path(os.path.abspath(path))
    for item in [path, *path.parents]:
        if item.is_symlink():
            raise ValueError(f'归档路径不得使用符号链接: {item}')
    return path


def _write(path, data):
    path = _safe(path)
    fd, tmp = tempfile.mkstemp(prefix='.capture-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _safe(path)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _json(path, data):
    _write(path, (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode())


def _extension(data):
    head = data.lstrip()
    if head.startswith(b'%PDF-'):
        return '.pdf'
    if head[:30].lower().startswith((b'<!doctype html', b'<html')):
        return '.html'
    if head.startswith(b'<?xml'):
        return '.xml'
    if not data or b'\x00' in data:
        return '.bin'
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return '.bin'
    try:
        json.loads(text)
        return '.json'
    except ValueError:
        return '.txt' if all(c.isprintable() or c in '\r\n\t' for c in text) else '.bin'


def file_sha256(path):
    """分块核验原始文件，内存占用不随文件大小增长。"""
    digest = hashlib.sha256()
    with _safe(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _stream_extension(stream, size, content_type=''):
    """优先魔数；大文本增量检测，避免为推断后缀读取整个文件。"""
    stream.seek(0)
    prefix = stream.read(65536)
    if size <= 65536:
        return _extension(prefix)
    head = prefix.lstrip()
    if head.startswith(b'%PDF-'):
        return '.pdf'
    if head[:30].lower().startswith((b'<!doctype html', b'<html')):
        return '.html'
    if head.startswith(b'<?xml'):
        return '.xml'
    decoder = codecs.getincrementaldecoder('utf-8')()
    stream.seek(0)
    try:
        for chunk in iter(lambda: stream.read(65536), b''):
            if b'\x00' in chunk:
                return '.bin'
            text = decoder.decode(chunk)
            if not all(c.isprintable() or c in '\r\n\t' for c in text):
                return '.bin'
        decoder.decode(b'', final=True)
    except UnicodeDecodeError:
        return '.bin'
    if 'json' in content_type.lower() and head.startswith((b'{', b'[')):
        return '.json'
    return '.txt'


@contextmanager
def _capture_temp(directory):
    fd, name = tempfile.mkstemp(prefix='.capture-', dir=directory)
    try:
        with os.fdopen(fd, 'w+b') as stream:
            yield stream, name
    finally:
        if os.path.exists(name):
            os.unlink(name)


class _Redirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def deferred_receipt(url, directory, cooldown):
    """Record a host cooldown without pretending a network attempt occurred."""
    canonical = _url(url)
    root = _safe(directory)
    root.mkdir(parents=True, exist_ok=True)
    folder = _safe(root / 'receipts'); folder.mkdir(exist_ok=True)
    path = _safe(folder / (uuid.uuid4().hex + '.json'))
    value = dict(url=url, final_url=None, status_code=None, headers={}, sha256=None,
        path=None, size_bytes=0, complete=False, capture_status='failed', reason=str(cooldown),
        attempts=0, retry_deferred=True, retry_not_before=cooldown.not_before,
        not_before=cooldown.not_before, retry_after_seconds=cooldown.retry_after_seconds,
        backoff_seconds=0, duration_seconds=0, captured_at=datetime.now(timezone.utc).isoformat(),
        receipts=[str(path)])
    _json(path, value)
    return value


def capture(url, directory: Path, *, timeout=25, max_bytes=40 * 1024 * 1024,
            max_attempts=3, backoff_base=1, refresh=False, lock_scope='global', stop_event=None, response_hook=None):
    """归档有界响应和逐次收据；输入/路径错误抛出，传输失败返回 failed。

    timeout 是 urllib 的 socket 操作超时，不是整个尝试或批次的硬截止时间。
    """
    canonical = _url(url)
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise ValueError('max_bytes 必须为正整数')
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
        raise ValueError('max_attempts 必须为正整数')
    if not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout 必须为有限正数')
    if not isinstance(backoff_base, (float, int)) or not math.isfinite(backoff_base) or backoff_base < 0:
        raise ValueError('backoff_base 必须为有限非负数')
    root = _safe(directory)
    root.mkdir(parents=True, exist_ok=True)
    if lock_scope not in ('global', 'url'):
        raise ValueError('lock_scope 必须为global/url')
    if lock_scope == 'global':
        with FileLock(_safe(root / '.capture.lock')):
            return _capture(url, canonical, root, timeout, max_bytes, max_attempts, backoff_base, refresh, stop_event, response_hook)
    locks = _safe(root / '.url-locks')
    locks.mkdir(exist_ok=True)
    with _url_capture_lock(locks / (hashlib.sha256(canonical.encode()).hexdigest() + '.lock'), stop_event):
        return _capture(url, canonical, root, timeout, max_bytes, max_attempts, backoff_base, refresh, stop_event, response_hook)


@contextmanager
def _url_capture_lock(path, stop_event=None):
    started = time.monotonic()
    while True:
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError('下载已请求停止，保留原始归档断点')
        try:
            lock = FileLock(_safe(path))
            break
        except FileLockBusy:
            if time.monotonic() - started >= 1800:
                raise ValueError('同URL归档锁等待达到1800秒上限，未重复发起请求')
            if stop_event is not None:
                stop_event.wait(.05)
            else:
                time.sleep(.05)
    try:
        yield
    finally:
        lock.close()


def _capture(url, canonical, root, timeout, limit, attempts, backoff, refresh, stop_event=None, response_hook=None):
    started = time.monotonic()
    for name in ('objects', 'receipts', 'index'):
        _safe(root / name).mkdir(exist_ok=True)
    index = _safe(root / 'index' / (hashlib.sha256(canonical.encode()).hexdigest() + '.json'))
    receipts = []

    def receipt(record):
        target = _safe(root / 'receipts' / (uuid.uuid4().hex + '.json'))
        receipts.append(str(target))
        record.update(receipts=list(receipts), captured_at=datetime.now(timezone.utc).isoformat(),
                      duration_seconds=round(time.monotonic() - started, 6))
        _json(target, record)
        return record

    if not refresh and index.exists():
        try:
            cached = json.loads(index.read_text())
            # Never trust an index-provided path outside this capture's objects.
            path = Path(cached['path'])
            if path.parent != root / 'objects':
                raise ValueError('缓存路径越界')
            _safe(path)
            if cached.get('url') == url or _url(cached['url']) == canonical:
                if cached.get('complete') and 200 <= cached['status_code'] < 300 and path.is_file() and path.stat().st_size <= limit:
                    if path.stat().st_size == cached['size_bytes'] and file_sha256(path) == cached['sha256']:
                        cached.update(url=url, capture_status='reused', reason='完整原件哈希验证通过', attempts=0)
                        return receipt(cached)
        except (OSError, ValueError, KeyError, TypeError):
            pass

    opener = request.build_opener(_Redirect())
    for attempt in range(1, attempts + 1):
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError('下载已请求停止，保留既有响应收据')
        record = dict(url=url, final_url=None, status_code=None, headers={}, sha256=None,
                      path=None, size_bytes=0, complete=False, capture_status='failed',
                      reason='', attempts=attempt)
        size = 0
        digest = hashlib.sha256()
        retry = False
        response = None
        with _capture_temp(root / 'objects') as (stream, temporary):
            def append(chunk):
                nonlocal size
                stream.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            try:
                req = request.Request(canonical, headers={'User-Agent': 'law-crawler/raw-capture (official-source archival)', 'Accept-Encoding': 'identity'})
                try:
                    response = opener.open(req, timeout=timeout)
                except error.HTTPError as exc:
                    response = exc
                record.update(final_url=_url(response.geturl()), status_code=response.code,
                              headers={k.lower(): v for k, v in response.headers.items() if k.lower() in _SAFE_HEADERS})
                stream_response(response, append, limit, stop_event=stop_event)
                record['complete'] = True
                if 200 <= response.code < 300:
                    record.update(capture_status='saved', reason='原始响应已归档；法规身份未验证')
                else:
                    record['reason'] = f'HTTP {response.code}'
                retry = is_retryable(status=response.code)
            except (error.URLError, OSError, http.client.HTTPException, ValueError) as exc:
                record['reason'] = f'{type(exc).__name__}: {exc}'
                # A malformed error body must not turn a permanent HTTP error into retries.
                retry = is_retryable(exc)
                if record['status_code'] is not None and not 200 <= record['status_code'] < 300:
                    retry = retry and is_retryable(status=record['status_code'])
            finally:
                if response is not None:
                    response.close()
            if size or record['status_code'] is not None:
                stream.flush()
                os.fsync(stream.fileno())
                checksum = digest.hexdigest()
                suffix = _stream_extension(stream, size, record['headers'].get('content-type', ''))
                path = _safe(root / 'objects' / (checksum + suffix))
                stream.close()  # Windows 也能原子替换已关闭的临时文件。
                if not path.exists() or file_sha256(path) != checksum:
                    _safe(path)
                    os.replace(temporary, path)
                record.update(sha256=checksum, path=str(path), size_bytes=size)
        pause = 0
        if retry and attempt < attempts:
            try:
                pause = retry_delay(attempt, backoff, record['headers'])
            except RetryDeferred as exc:
                retry = False
                record.update(reason=str(exc), retry_deferred=True, retry_not_before=exc.not_before, not_before=exc.not_before,
                              retry_after_seconds=exc.retry_after_seconds)
        record['backoff_seconds'] = pause
        receipt(record)
        if response_hook is not None:
            response_hook(record)
        if record['capture_status'] == 'saved':
            _json(index, record)
            return record
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError('下载停止，部分原始响应及收据已归档')
        if not retry or attempt == attempts:
            return record
        wait_delay(pause, stop_event=stop_event, sleep=time.sleep)
