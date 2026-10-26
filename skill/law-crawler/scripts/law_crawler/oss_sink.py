"""线程安全的不可覆盖 OSS 原件与逐来源元数据归档。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
import time
from pathlib import Path
from typing import Callable
from .storage_config import validate_storage_config


def _sha_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(256 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class ArchiveValidationError(ValueError):
    """仅承载本模块常量错误，不包含 SDK 请求或认证数据。"""


class OSSSink:
    """调用 archive(receipt, source, batch_id)；成功仅表示两个对象均经回读核验。

    receipt 使用 raw capture 格式：path/sha256/size_bytes/complete/
    capture_status/status_code；source 至少提供 detail_url。
    cache_path 仅保存已核验对象定位信息，不保存凭据；重启后仍回读核验。
    每个实例的 worker 共享锁；部署时一个进程持有一个实例。
    """

    def __init__(self, *, credentials, cache_path, bucket,
                 endpoint, region, prefix, max_attempts=3,
                 bucket_factory: Callable | None = None):
        destination = validate_storage_config(dict(bucket=bucket, endpoint=endpoint, region=region, prefix=prefix))
        bucket, endpoint, region, prefix = (destination[k] for k in ('bucket', 'endpoint', 'region', 'prefix'))
        self.bucket_name = bucket
        self.prefix = prefix.strip('/')
        if not self.prefix or '..' in self.prefix.split('/'):
            raise ArchiveValidationError('OSS prefix 无效')
        self.max_attempts = min(3, max(1, int(max_attempts)))
        self.cache_path = Path(cache_path)
        self._guard = threading.RLock()
        self._locks = {}
        self._verified = {}
        self._local = threading.local()
        self._auth_failure = None
        self._cache = {}
        self._cache_dirty = 0
        self._cache_last_flush = time.monotonic()
        self._cache_has_flushed = False
        self._scope = {'bucket': bucket, 'endpoint': endpoint, 'prefix': self.prefix}
        if self.cache_path.exists():
            old = json.loads(self.cache_path.read_text())
            if old.get('scope') == self._scope:
                self._cache = old.get('objects', {})
        if bucket_factory is not None:
            self._factory = bucket_factory
        else:
            import oss2
            credential_path = Path(credentials)
            info = credential_path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ArchiveValidationError('OSS 凭据须为权限 600 或更严格的普通文件')
            secret = json.loads(credential_path.read_text())
            if secret.get('security_token'):
                auth = oss2.StsAuth(secret['access_key_id'], secret['access_key_secret'],
                                    secret['security_token'], auth_version='v4')
            else:
                auth = oss2.AuthV4(secret['access_key_id'], secret['access_key_secret'])
            self._factory = lambda: oss2.Bucket(auth, endpoint, bucket, region=region,
                                                connect_timeout=30)

    def _bucket(self):
        if not hasattr(self._local, 'bucket'):
            self._local.bucket = self._factory()
        return self._local.bucket

    def _lock(self, key):
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def _remember(self, digest, key, size):
        with self._guard:
            entry = {'key': key, 'size_bytes': size}
            if self._cache.get(digest) != entry:
                self._cache[digest] = entry
                self._cache_dirty += 1
            # 缓存仅为定位提示：少量未落盘条目在重启时由 HEAD/回读重新发现。
            # 首条立即持久化，此后合并 100 次更新或在下一次访问时检查 30 秒阈值。
            if self._cache_dirty and (not self._cache_has_flushed
                    or self._cache_dirty >= 100
                    or time.monotonic() - self._cache_last_flush >= 30):
                self.flush_cache()

    def flush_cache(self):
        """持久化定位缓存；任务正常退出时调用，不代表远端对象核验结果。"""
        with self._guard:
            if not self._cache_dirty:
                return
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_name(self.cache_path.name + '.tmp')
            temporary.write_text(json.dumps({'scope': self._scope, 'objects': self._cache},
                                            ensure_ascii=False, sort_keys=True))
            os.replace(temporary, self.cache_path)
            self._cache_dirty = 0
            self._cache_last_flush = time.monotonic()
            self._cache_has_flushed = True

    @staticmethod
    def _missing(error):
        return getattr(error, 'status', None) == 404 and getattr(error, 'code', '') in (
            'NoSuchKey', 'NoSuchObject', '')

    def _head(self, key):
        try:
            return self._bucket().head_object(key)
        except Exception as error:
            if self._missing(error):
                return None
            raise

    def _verify(self, key, digest, size):
        head = self._head(key)
        if head is None or head.content_length != size:
            raise ArchiveValidationError('OSS 对象不存在或大小冲突')
        recorded = head.headers.get('x-oss-meta-sha256')
        if recorded and recorded != digest:
            raise ArchiveValidationError('OSS 对象 SHA256 元数据冲突')
        remote = self._bucket().get_object(key)
        checksum = hashlib.sha256()
        try:
            for block in iter(lambda: remote.read(256 * 1024), b''):
                checksum.update(block)
        finally:
            remote.close()
        if checksum.hexdigest() != digest:
            raise ArchiveValidationError('OSS 对象回读 SHA256 冲突')

    def _put_verified(self, key, digest, size, *, path=None, content=None):
        previous = self._verified.get(key)
        if previous:
            if previous != (digest, size):
                raise ArchiveValidationError('本次运行对象键内容冲突')
            return 'reused_verified'
        exists = self._head(key) is not None
        if not exists:
            headers = {'x-oss-meta-sha256': digest, 'x-oss-forbid-overwrite': 'true'}
            try:
                if path is not None:
                    self._bucket().put_object_from_file(key, str(path), headers=headers)
                else:
                    self._bucket().put_object(key, content, headers=headers)
            except Exception as error:
                if getattr(error, 'status', None) != 409:
                    raise
                # 并发进程先写入时也必须回读验证，不覆盖远端内容。
                exists = True
        self._verify(key, digest, size)
        self._verified[key] = (digest, size)
        return 'reused_verified' if exists else 'uploaded_verified'

    def _raw_key(self, digest, suffix):
        with self._guard:
            cached = self._cache.get(digest)
        if cached and cached['key'].startswith(self.prefix + '/raw/' + digest):
            return cached['key']
        key = self.prefix + '/raw/' + digest + suffix
        if self._head(key) is not None:
            return key
        # 旧批次可能为同一字节内容推断出不同扩展名；优先复用已有对象。
        listing = self._bucket().list_objects(prefix=self.prefix + '/raw/' + digest,
                                              max_keys=100)
        for item in listing.object_list:
            name = item.key.rsplit('/', 1)[-1]
            if name == digest or re.fullmatch(re.escape(digest) + r'\.[A-Za-z0-9]{1,12}', name):
                return item.key
        return key

    def archive(self, receipt: dict, source: dict, batch_id: str) -> dict:
        """返回 status/raw_key/metadata_key/attempts/reason/duration_seconds。

        失败返回 status=failed；401/403 在当前实例内熔断，避免逐文件反复认证。
        元数据键包括批次、详情 URL、原件 SHA 和元数据 SHA，来源不会因原件去重丢失。
        """
        start = time.monotonic()
        result = {'status': 'failed', 'attempts': 0, 'sha256': receipt.get('sha256')}
        try:
            if not re.fullmatch(r'[A-Za-z0-9_.-]+', batch_id) or batch_id in ('.', '..'):
                raise ArchiveValidationError('批次标识无效')
            if (receipt.get('capture_status') not in ('saved', 'reused')
                    or not receipt.get('complete')
                    or not 200 <= int(receipt.get('status_code') or 0) < 300):
                raise ArchiveValidationError('仅允许上传成功且完整的原件 receipt')
            digest = receipt['sha256']
            if not re.fullmatch('[0-9a-f]{64}', digest):
                raise ArchiveValidationError('原件 SHA256 无效')
            path = Path(receipt['path'])
            size = path.stat().st_size
            if size != int(receipt['size_bytes']) or _sha_file(path) != digest:
                raise ArchiveValidationError('本地原件大小或 SHA256 与 receipt 不符')
            if not source.get('detail_url'):
                raise ArchiveValidationError('来源必须包含 detail_url')
            suffix = path.suffix.lower()
            if suffix and not re.fullmatch(r'\.[a-z0-9]{1,12}', suffix):
                raise ArchiveValidationError('原件扩展名无效')
            with self._lock(digest):
                for attempt in range(1, self.max_attempts + 1):
                    if self._auth_failure:
                        result['reason'] = self._auth_failure
                        break
                    result['attempts'] = attempt
                    try:
                        raw_key = self._raw_key(digest, suffix)
                        result['raw_key'] = raw_key
                        status = self._put_verified(raw_key, digest, size, path=path)
                        self._remember(digest, raw_key, size)
                        envelope = {'schema_version': 1, 'batch_id': batch_id,
                                    'bucket': self.bucket_name, 'raw_key': raw_key,
                                    'capture': receipt, 'source': source}
                        content = json.dumps(envelope, ensure_ascii=False, sort_keys=True,
                                             separators=(',', ':')).encode('utf-8')
                        meta_sha = hashlib.sha256(content).hexdigest()
                        identity = hashlib.sha256(source['detail_url'].encode('utf-8')).hexdigest()
                        meta_key = f'{self.prefix}/metadata/{batch_id}/{identity}/{digest}/{meta_sha}.json'
                        result['metadata_key'] = meta_key
                        self._put_verified(meta_key, meta_sha, len(content), content=content)
                        result.update(status=status, reason='原件及逐来源元数据均通过 OSS 回读 SHA256 核验')
                        break
                    except Exception as error:
                        reason = self._reason(error)
                        result['reason'] = reason
                        if getattr(error, 'status', None) in (401, 403):
                            self._auth_failure = reason
                            break
                        if isinstance(error, ValueError) or attempt == self.max_attempts:
                            break
                        time.sleep(2 ** (attempt - 1))
        except Exception as error:
            result['reason'] = self._reason(error)
        result['duration_seconds'] = round(time.monotonic() - start, 3)
        return result

    def _operation(self, action):
        start = time.monotonic()
        result = {'status': 'failed', 'attempts': 0}
        for attempt in range(1, self.max_attempts + 1):
            if self._auth_failure:
                result['reason'] = self._auth_failure
                break
            result['attempts'] = attempt
            try:
                result.update(action())
                break
            except Exception as error:
                result['reason'] = self._reason(error)
                if getattr(error, 'status', None) in (401, 403):
                    self._auth_failure = result['reason']
                    break
                if isinstance(error, ValueError) or attempt == self.max_attempts:
                    break
                time.sleep(2 ** (attempt - 1))
        result['duration_seconds'] = round(time.monotonic() - start, 3)
        return result

    @property
    def auth_failure(self):
        """返回安全的认证/授权失败说明；非空时停止新增下载任务。"""
        return self._auth_failure

    def read_verified_archive_metadata(self, key, digest):
        """有界读取不可变归档信封；只接受本库 metadata 键及内容摘要。"""
        if (not isinstance(key, str) or not key.startswith(self.prefix + '/metadata/')
                or '..' in key.split('/') or not re.fullmatch('[0-9a-f]{64}', digest)
                or not key.endswith('/' + digest + '.json')):
            raise ArchiveValidationError('附件元数据键或摘要无效')
        remote = self._bucket().get_object(key)
        try:
            data = remote.read(2 * 1024 * 1024 + 1)
        finally:
            remote.close()
        if len(data) > 2 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != digest:
            raise ArchiveValidationError('附件元数据大小或摘要不符')
        value = json.loads(data)
        if value.get('bucket') != self.bucket_name:
            raise ArchiveValidationError('附件元数据 bucket 不符')
        return value

    def verify_original(self, receipt: dict) -> dict:
        """核验 URL 缓存的远端原件，不要求本地 staging 文件存在。"""
        def action():
            digest = receipt.get('sha256', '')
            key = receipt.get('raw_key') or receipt.get('key') or receipt.get('oss_raw_key')
            size = int(receipt['size_bytes'])
            if not re.fullmatch('[0-9a-f]{64}', digest) or size < 0:
                raise ArchiveValidationError('远端原件 SHA256 或大小无效')
            expected = self.prefix + '/raw/' + digest
            if not isinstance(key, str) or not re.fullmatch(re.escape(expected) + r'(?:\.[A-Za-z0-9]{1,12})?', key):
                raise ArchiveValidationError('远端原件键与 SHA256 不一致')
            with self._lock(digest):
                if self._verified.get(key) != (digest, size):
                    self._verify(key, digest, size)
                    self._verified[key] = (digest, size)
                self._remember(digest, key, size)
            return {'status': 'reused_verified', 'key': key, 'raw_key': key,
                    'sha256': digest, 'size_bytes': size,
                    'reason': '远端原件通过 OSS 回读 SHA256 核验'}
        return self._operation(action)

    def put_original(self, receipt: dict) -> dict:
        """上传完整原件；返回 failed 或 uploaded_verified/reused_verified。

        返回成功后调用方可持久化对象收据；仅在详情元数据也上传成功后清理本批临时文件。
        本模块从不删除本地文件或凭据。
        """
        def action():
            if (receipt.get('capture_status') not in ('saved', 'reused')
                    or not receipt.get('complete')
                    or not 200 <= int(receipt.get('status_code') or 0) < 300):
                raise ArchiveValidationError('原件 receipt 未成功完整归档')
            digest = receipt.get('sha256', '')
            if not re.fullmatch('[0-9a-f]{64}', digest):
                raise ArchiveValidationError('原件 SHA256 无效')
            path = Path(receipt['path'])
            size = path.stat().st_size
            if size != int(receipt['size_bytes']) or _sha_file(path) != digest:
                raise ArchiveValidationError('本地原件大小或 SHA256 与 receipt 不符')
            suffix = path.suffix.lower()
            if suffix and not re.fullmatch(r'\.[a-z0-9]{1,12}', suffix):
                raise ArchiveValidationError('原件扩展名无效')
            with self._lock(digest):
                key = self._raw_key(digest, suffix)
                status = self._put_verified(key, digest, size, path=path)
                self._remember(digest, key, size)
                return {'status': status, 'key': key, 'raw_key': key,
                        'sha256': digest, 'size_bytes': size,
                        'reason': '原件通过 OSS 回读 SHA256 核验'}
        return self._operation(action)

    def put_metadata(self, detail_key: str, document: dict, *, batch_id: str) -> dict:
        """按批次+详情身份+内容哈希存不可覆盖快照，包括失败详情的结果也可存档。

        detail_key 推荐官方 detail_url；document 应保留所有原件 OSS 收据和来源信息。
        """
        def action():
            if not detail_key:
                raise ArchiveValidationError('详情身份不能为空')
            if not re.fullmatch(r'[A-Za-z0-9_.-]+', batch_id) or batch_id in ('.', '..'):
                raise ArchiveValidationError('批次标识无效')
            body = json.dumps({'schema_version': 1, 'batch_id': batch_id,
                               'detail_key': detail_key, 'document': document},
                              ensure_ascii=False, sort_keys=True,
                              separators=(',', ':')).encode('utf-8')
            digest = hashlib.sha256(body).hexdigest()
            identity = hashlib.sha256(detail_key.encode('utf-8')).hexdigest()
            key = f'{self.prefix}/metadata/{batch_id}/{identity}/{digest}.json'
            with self._lock('metadata:' + key):
                status = self._put_verified(key, digest, len(body), content=body)
            return {'status': status, 'key': key, 'metadata_key': key,
                    'sha256': digest, 'size_bytes': len(body),
                    'reason': '详情元数据通过 OSS 回读 SHA256 核验'}
        return self._operation(action)

    @staticmethod
    def _reason(error):
        if isinstance(error, ArchiveValidationError):
            return str(error)
        code = str(getattr(error, 'code', ''))
        safe_code = code if re.fullmatch(r'[A-Za-z0-9_.-]{0,100}', code) else 'redacted'
        return f'{type(error).__name__}:status={getattr(error, "status", None)}:code={safe_code}'
