"""Shared HTTP transfer primitives. No storage, parsing, or nested retries.

Callers own one retry loop. Returned bytes are raw (Accept-Encoding: identity).
"""
import http.client
import errno
import math
import random
import ssl
import time
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError

CHUNK_BYTES = 65536
TRANSIENT_STATUS = frozenset((408, 425, 429, 500, 502, 503, 504))
MAX_AUTOMATIC_WAIT = 300


class SizeLimitExceeded(ValueError):
    pass


class IncompleteResponse(http.client.HTTPException):
    pass


class EmptyResponse(IncompleteResponse):
    pass


class RetryDeferred(ValueError):
    """Server requested a long cooldown; do not retry before not_before."""
    def __init__(self, seconds):
        self.retry_after_seconds = seconds
        self.not_before = time.time() + seconds
        super().__init__('服务端要求较长冷却；本次停止自动重试，须在冷却期后另批续跑')


def retry_after_seconds(value, now=None):
    try:
        text = str(value).strip()
        seconds = float(text) if text.isdigit() else parsedate_to_datetime(text).timestamp() - (time.time() if now is None else now)
        return max(0.0, seconds) if math.isfinite(seconds) else 0.0
    except (ValueError, TypeError, OverflowError, AttributeError):
        return 0.0


def is_retryable(exc=None, status=None):
    if isinstance(exc, HTTPError):
        return exc.code in TRANSIENT_STATUS
    if status is not None:
        return status in TRANSIENT_STATUS
    if isinstance(exc, OSError) and getattr(exc, 'errno', None) in {
            errno.ENOSPC, errno.EACCES, errno.EPERM, errno.EROFS, errno.ENOENT,
            errno.ENOTDIR, errno.EISDIR, errno.EMFILE, errno.ENFILE}:
        return False
    if isinstance(exc, URLError):
        cause = exc.reason
        if isinstance(cause, BaseException):
            return is_retryable(cause)
        return True
    if isinstance(exc, (ssl.SSLCertVerificationError, ValueError, InterruptedError, PermissionError, FileNotFoundError, IsADirectoryError, NotADirectoryError)):
        return False
    return isinstance(exc, (OSError, http.client.HTTPException))


def retry_delay(attempt, base_delay=1, headers=None, jitter=None):
    if not isinstance(base_delay, (int, float)) or isinstance(base_delay, bool) or not math.isfinite(base_delay) or base_delay < 0:
        raise ValueError('base_delay 必须为有限非负数')
    hint = retry_after_seconds((headers or {}).get('Retry-After', (headers or {}).get('retry-after', '')))
    if hint > MAX_AUTOMATIC_WAIT:
        raise RetryDeferred(hint)
    base = min(base_delay * 2 ** min(max(attempt - 1, 0), 30), 30)
    sample = (random.random if jitter is None else jitter)()
    pause = min(30, base * (1 + .25 * max(0, min(1, sample))))
    return max(pause, hint)


def wait_delay(delay, *, stop_event=None, sleep=time.sleep):
    remaining = delay
    while remaining > 0:
        step = min(30, remaining)
        if stop_event is not None:
            if stop_event.wait(step):
                raise InterruptedError('退避期间停止，保留失败状态')
        else:
            sleep(step)
        remaining -= step


def stream_response(response, write, max_bytes, *, stop_event=None):
    """Copy at most max_bytes, retaining partial bytes before raising on failure.

    The caller closes response and decides whether/how to archive failed bodies.
    Read one extra byte to identify oversize responses; never write that byte.
    """
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise ValueError('max_bytes 必须为正整数')
    headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
    length = headers.get('content-length')
    invalid_length = length is not None and not length.isdigit()
    expected = int(length) if length is not None and not invalid_length else None
    size = 0
    def accept(chunk):
        nonlocal size
        keep = chunk[:max(0, max_bytes - size)]
        if keep:
            write(keep); size += len(keep)
        if len(keep) != len(chunk):
            raise SizeLimitExceeded('响应超过 max_bytes；保留有界部分')
    while True:
        if size and expected is not None and size == expected:
            break
        # The caller checks cancellation before opening. Once headers arrived,
        # retain the first bounded chunk so an already-complete response is not
        # discarded merely because cancellation raced with the server write.
        if size and stop_event is not None and stop_event.is_set():
            raise InterruptedError('下载停止，保留已接收部分及失败收据')
        try:
            chunk = response.read(min(CHUNK_BYTES, max_bytes + 1 - size))
        except http.client.IncompleteRead as exc:
            accept(exc.partial)
            raise IncompleteResponse('响应长度不完整') from exc
        if not chunk:
            break
        accept(chunk)
    if invalid_length:
        raise IncompleteResponse('Content-Length 无效')
    if expected is not None and size != expected:
        raise IncompleteResponse('响应长度不完整')
    if not size:
        raise EmptyResponse('空响应')
    return size
