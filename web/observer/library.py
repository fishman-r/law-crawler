"""只读法规资源库：只接受 schema 2 sidecar 登记的文件，不提供通用文件管理。

所有读取从固定根目录逐层用 dir_fd/O_NOFOLLOW 打开。响应和 ZIP 都在临时
文件完整准备后才交给 HTTP 层，SHA 校验失败不会变成一次看似成功的下载。
"""
import base64
import binascii
import codecs
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
from urllib.parse import quote
import zipfile

from observer.snapshot import redact


CHUNK_BYTES = 64 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_RELOCATION_BYTES = 512 * 1024
MAX_PREVIEW_BYTES = 256 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_FILES = 2000
MAX_DIRECTORY_ENTRIES = 10000
MAX_COUNTRIES = 100
MAX_LIBRARY_FILES = 10000
MAX_WARNINGS = 100
MAX_PAGE_SIZE = 200
_BATCH_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,1024}\Z")
_ZIP_GATE = threading.BoundedSemaphore(1)
_DOWNLOAD_EXTENSIONS = {
    '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.md', '.txt', '.xml',
    '.html', '.htm', '.svg', '.js', '.rtf', '.doc', '.docx', '.xls', '.xlsx',
    '.ppt', '.pptx', '.odt', '.ods', '.odp', '.zip',
}
_BINARY_PREVIEWS = {
    '.pdf': ('application/pdf', 'pdf'), '.png': ('image/png', 'image'),
    '.jpg': ('image/jpeg', 'image'), '.jpeg': ('image/jpeg', 'image'),
    '.gif': ('image/gif', 'image'), '.webp': ('image/webp', 'image'),
}


class LibraryError(Exception):
    """只含可公开的固定错误文字；不携带底层 exception 或服务器绝对路径。"""

    def __init__(self, status, message, code='library_error'):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


def _segment(value):
    return (isinstance(value, str) and bool(value) and value not in ('.', '..')
            and not value.startswith('.') and len(value.encode('utf-8')) <= 255
            and not any(char in value for char in ('/', '\\', '%', ':', '\x00'))
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _parts(value):
    if not isinstance(value, str) or len(value) > 1000:
        raise LibraryError(400, '文件路径无效', 'invalid_path')
    parts = tuple(value.split('/'))
    if not parts or not all(_segment(part) for part in parts):
        raise LibraryError(400, '文件路径无效', 'invalid_path')
    return parts


def file_id(country, path):
    """URL 中只放不透明标识；解码后仍必须与对应 sidecar 核对。"""
    raw = (country + '/' + path).encode('utf-8')
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def decode_file_id(identifier):
    if not isinstance(identifier, str) or not _IDENTIFIER.fullmatch(identifier):
        raise LibraryError(400, '资源标识无效', 'invalid_id')
    try:
        raw = base64.b64decode(identifier + '=' * (-len(identifier) % 4),
                               altchars=b'-_', validate=True).decode('utf-8')
        parts = _parts(raw)
        if len(parts) not in (2, 3) or (len(parts) == 3 and parts[1] != 'raw'):
            raise ValueError()
        if file_id(parts[0], '/'.join(parts[1:])) != identifier:
            raise ValueError()
    except (ValueError, UnicodeError, binascii.Error):
        raise LibraryError(400, '资源标识无效', 'invalid_id') from None
    return parts


def parse_range(header, size):
    """只接受单段 bytes Range；异常/多段/超长数字都返回 416。"""
    if header is None:
        return None
    invalid = LibraryError(416, '请求的字节范围无效', 'invalid_range')
    if not isinstance(header, str) or len(header) > 100:
        raise invalid
    match = re.fullmatch(r'bytes=([0-9]{0,20})-([0-9]{0,20})', header)
    if not match or not any(match.groups()) or size <= 0:
        raise invalid
    first, last = match.groups()
    if any(value and int(value) > 2 ** 63 - 1 for value in (first, last)):
        raise invalid
    if first:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
        if start >= size or end < start:
            raise invalid
    else:
        length = int(last)
        if length <= 0:
            raise invalid
        start, end = max(0, size - length), size - 1
    return start, end


def content_disposition(filename, inline=False):
    """UTF-8 文件名不进入原始头字段，避免引号或 CRLF 注入。"""
    fallback = re.sub(r'[^A-Za-z0-9._-]', '_', filename)[:120] or 'regulation'
    return (f'{"inline" if inline else "attachment"}; filename="{fallback}"; '
            f"filename*=UTF-8''{quote(filename, safe='')}")


def _display(value, limit=1000):
    if isinstance(value, dict):
        value = value.get('value')
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return str(redact(str(value)))[:limit]
    return ''


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_nlink)


def _warning(warnings, message):
    if message not in warnings and len(warnings) < MAX_WARNINGS:
        warnings.append(message)


def _strict_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _absolute_root(value):
    """迁移声明只接受规范绝对目录；不能先归一化再丢失越界线索。"""
    if (not isinstance(value, str) or not value.startswith('/')
            or len(value) > 4096 or value == '/' or str(Path(value)) != value
            or any(part in ('', '.', '..') for part in value.split('/')[1:])
            or '\\' in value or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise LibraryError(409, '下载目录迁移记录中的路径无效', 'invalid_relocation')
    return Path(value)


@dataclass(frozen=True)
class FileSpec:
    parts: tuple
    size: int
    sha256: str
    media_type: str


@dataclass
class Resource:
    file: FileSpec
    primary: FileSpec
    original: FileSpec | None
    metadata: tuple
    primary_metadata: tuple
    original_metadata: tuple | None
    primary_metadata_sha256: str
    original_metadata_sha256: str | None
    title: str
    identity: dict
    published_at: str | None
    body_review: str
    sources: list


@dataclass
class PreparedFile:
    stream: object
    size: int
    name: str
    media_type: str
    preview_kind: str | None = None


@dataclass
class BatchSelection:
    identifier: str
    mode: str
    resources: list
    warnings: list
    invalid_count: int
    attempt: tuple | None
    report: dict
    blocked_reason: str | None = None


class LibraryReader:
    def __init__(self, download_base):
        # 不 resolve：逐层 open 必须能发现根目录本身和祖先中的符号链接。
        self.download_base = Path(download_base).absolute()
        if (os.open not in os.supports_dir_fd or os.scandir not in os.supports_fd
                or not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY')):
            raise ValueError('资源库需要支持 dir_fd 和 O_NOFOLLOW 的 POSIX 系统')

    @contextmanager
    def _open(self, parts=(), directory=False):
        """O_NONBLOCK 先打开再 fstat，FIFO/设备/硬链接永不作为文件读取。"""
        if any(not isinstance(part, str) or not part or part in ('.', '..')
               or '/' in part or '\\' in part or '\x00' in part for part in parts):
            raise LibraryError(400, '读取路径无效', 'invalid_path')
        descriptor = None
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        directory_flags = flags | os.O_DIRECTORY
        try:
            descriptor = os.open(self.download_base.anchor, directory_flags)
            root_parts = self.download_base.parts[1:]
            all_parts = (*root_parts, *parts)
            for index, part in enumerate(all_parts):
                is_directory = index < len(all_parts) - 1 or directory
                next_descriptor = os.open(part, directory_flags if is_directory else flags | os.O_NONBLOCK,
                                          dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
            info = os.fstat(descriptor)
            if directory:
                if not stat.S_ISDIR(info.st_mode):
                    raise LibraryError(409, '读取路径不是安全目录', 'unsafe_file')
            elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise LibraryError(409, '只允许读取非链接的常规文件', 'unsafe_file')
            yield descriptor, info
        except FileNotFoundError:
            raise LibraryError(404, '资源文件不存在', 'missing_file') from None
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.ENXIO, errno.EACCES, errno.EPERM):
                raise LibraryError(409, '读取路径包含链接或不可安全访问', 'unsafe_file') from None
            raise LibraryError(503, '暂时无法读取资源文件', 'unavailable') from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _entries(self, parts=()):
        result = []
        try:
            with self._open(parts, directory=True) as (descriptor, _):
                with os.scandir(descriptor) as entries:
                    for index, entry in enumerate(entries):
                        if index >= MAX_DIRECTORY_ENTRIES:
                            raise LibraryError(413, '目录条目超过安全扫描上限', 'scan_limit')
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        result.append((entry.name, info.st_mode, info.st_nlink))
        except LibraryError as exc:
            if exc.status != 404:
                raise
        return sorted(result)

    def _json(self, parts, limit=None):
        limit = MAX_JSON_BYTES if limit is None else limit
        with self._open(parts) as (descriptor, info):
            if info.st_size > limit:
                raise LibraryError(413, '元数据或报告超过读取大小上限', 'json_limit')
            raw = bytearray()
            while len(raw) <= limit:
                chunk = os.read(descriptor, min(CHUNK_BYTES, limit + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            if len(raw) > limit or _fingerprint(os.fstat(descriptor)) != _fingerprint(info):
                raise LibraryError(409, '读取期间元数据已变化，请刷新后重试', 'metadata_changed')
        try:
            document = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_strict_object,
                                  parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(document, dict):
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            raise LibraryError(409, '元数据或报告结构无效', 'invalid_metadata') from None
        return document, bytes(raw)

    def _spec(self, country, value, original=False):
        if not isinstance(value, dict):
            raise LibraryError(409, '文件登记信息缺失', 'invalid_metadata')
        try:
            relative = _parts(value.get('path'))
        except LibraryError:
            raise LibraryError(409, '元数据文件路径越界或无效', 'invalid_metadata') from None
        if ((original and (len(relative) != 2 or relative[0] != 'raw'))
                or (not original and len(relative) != 1)
                or Path(relative[-1]).suffix.lower() not in _DOWNLOAD_EXTENSIONS):
            raise LibraryError(409, '元数据文件不在允许的法规路径内', 'invalid_metadata')
        size, sha, media = value.get('size_bytes'), value.get('sha256'), value.get('media_type')
        if (not isinstance(size, int) or isinstance(size, bool) or size < 0
                or not isinstance(sha, str) or not _SHA.fullmatch(sha)
                or not isinstance(media, str) or not re.fullmatch(r'[a-z0-9.+-]+/[a-z0-9.+-]+', media)):
            raise LibraryError(409, '元数据大小、SHA 或文件类型无效', 'invalid_metadata')
        return FileSpec((country, *relative), size, sha, media)

    def _record(self, country, metadata_name):
        if not _segment(country) or not _segment(metadata_name) or not metadata_name.endswith('.json'):
            raise LibraryError(400, '元数据标识无效', 'invalid_id')
        metadata = (country, metadata_name)
        record, primary_raw = self._json(metadata)
        if record.get('schema_version') != 2:
            raise LibraryError(404, '文件尚未登记为法规原文', 'unregistered_file')
        primary = self._spec(country, record.get('file'))
        if Path(primary.parts[-1]).with_suffix('.json').name != metadata_name:
            raise LibraryError(409, '元数据与文件登记路径不一致', 'invalid_metadata')
        original, original_metadata, original_metadata_sha256 = None, None, None
        if record.get('original_file') is not None:
            if primary.media_type != 'text/markdown' or not primary.parts[-1].endswith('.md'):
                raise LibraryError(409, '派生文件关联信息无效', 'invalid_metadata')
            original = self._spec(country, record['original_file'], original=True)
            conversion = record.get('conversion')
            if (isinstance(conversion, dict) and conversion.get('original_sha256') is not None
                    and conversion['original_sha256'] != original.sha256):
                raise LibraryError(409, '原响应与转换 SHA 不一致', 'invalid_metadata')
            original_metadata = (*original.parts[:-1], original.parts[-1] + '.metadata.json')
            raw_record, original_raw = self._json(original_metadata)
            original_metadata_sha256 = hashlib.sha256(original_raw).hexdigest()
            if (raw_record.get('schema_version') != 2
                    or self._spec(country, raw_record.get('file'), original=True) != original
                    or self._spec(country, raw_record.get('derived_file')) != primary):
                raise LibraryError(409, '原响应元数据关联不一致', 'invalid_metadata')
        document = record.get('document')
        document = document if isinstance(document, dict) else {}
        identity = document.get('legal_identity')
        identity = identity.get('value') if isinstance(identity, dict) else None
        identity = identity if isinstance(identity, dict) else {}
        identity = {key: _display(identity.get(key), 200) or None for key in ('type', 'number', 'year')}
        title = _display(document.get('title')) or ' '.join(str(value) for value in identity.values() if value)
        review = record.get('body_review')
        review = review.get('status') if isinstance(review, dict) else review
        sources = []
        for source in (record.get('sources') or [])[:3] if isinstance(record.get('sources'), list) else []:
            if isinstance(source, dict):
                sources.append({key: _display(source.get(key), 500) for key in
                                ('platform', 'detail_url', 'download_url', 'final_url') if source.get(key)})
        return Resource(primary, primary, original, metadata, metadata, original_metadata,
                        hashlib.sha256(primary_raw).hexdigest(), original_metadata_sha256,
                        title or primary.parts[-1], identity, _display(document.get('published_at'), 80) or None,
                        _display(review, 200) or 'not_reviewed', sources)

    def resource(self, identifier):
        parts = decode_file_id(identifier)
        if len(parts) == 2:
            resource = self._record(parts[0], Path(parts[-1]).with_suffix('.json').name)
        else:
            raw_metadata = (*parts[:-1], parts[-1] + '.metadata.json')
            raw_record, _ = self._json(raw_metadata)
            derived = self._spec(parts[0], raw_record.get('derived_file'))
            resource = self._record(parts[0], Path(derived.parts[-1]).with_suffix('.json').name)
            if resource.original is None or resource.original.parts != parts:
                raise LibraryError(404, '资源未登记为法规原文', 'unregistered_file')
            resource.file = resource.original
            resource.metadata = raw_metadata
        if resource.file.parts != parts:
            raise LibraryError(404, '资源与登记文件不一致', 'unregistered_file')
        return resource

    def _file_info(self, spec, descriptor, info):
        if info.st_size != spec.size:
            raise LibraryError(409, '文件大小与登记不一致，下载已拒绝', 'integrity_mismatch')
        if spec.size > MAX_ARCHIVE_BYTES:
            raise LibraryError(413, '文件超过 1 GiB 导出上限', 'size_limit')
        head = os.read(descriptor, min(64, spec.size))
        os.lseek(descriptor, max(0, spec.size - 4096), os.SEEK_SET)
        tail = os.read(descriptor, min(4096, spec.size))
        os.lseek(descriptor, 0, os.SEEK_SET)
        suffix = Path(spec.parts[-1]).suffix.lower()
        expected = _BINARY_PREVIEWS.get(suffix)
        if expected:
            mime, kind = expected
            actual = False
            if suffix == '.pdf':
                actual = bool(re.match(br'%PDF-[12]\.[0-9]', head)) and b'%%EOF' in tail
            elif suffix == '.png':
                actual = (head.startswith(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR')
                          and tail.endswith(b'\x00\x00\x00\x00IEND\xaeB`\x82'))
            elif suffix in ('.jpg', '.jpeg'):
                actual = head.startswith(b'\xff\xd8\xff') and tail.endswith(b'\xff\xd9')
            elif suffix == '.gif':
                actual = head.startswith((b'GIF87a', b'GIF89a')) and tail.endswith(b';')
            elif suffix == '.webp':
                actual = (head.startswith(b'RIFF') and head[8:12] == b'WEBP'
                          and int.from_bytes(head[4:8], 'little') + 8 == spec.size)
            if not actual or spec.media_type != mime:
                raise LibraryError(409, '文件实际格式与登记类型不一致', 'format_mismatch')
            return kind
        if suffix == '.md' and spec.media_type == 'text/markdown':
            return 'text'
        return None

    def _available(self, resource):
        result = None
        for spec in dict.fromkeys((resource.file, resource.original)):
            if spec is None:
                continue
            with self._open(spec.parts) as (descriptor, info):
                kind = self._file_info(spec, descriptor, info)
                if spec == resource.file:
                    result = (info, kind)
        return result

    def row(self, resource):
        info, kind = self._available(resource)
        identifier = file_id(resource.file.parts[0], '/'.join(resource.file.parts[1:]))
        prefix = '/api/library/files/' + identifier
        row = dict(id=identifier, country=resource.file.parts[0], name=resource.file.parts[-1],
                    path='/'.join(resource.file.parts[1:]), title=resource.title,
                    size_bytes=resource.file.size,
                    modified_at=datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
                    media_type=resource.file.media_type, identity=resource.identity,
                    published_at=resource.published_at, body_review=resource.body_review,
                    preview_kind=kind, preview_url=prefix + '/preview' if kind else None,
                    download_url=prefix + '/download', metadata_url=prefix + '/metadata')
        if resource.original is not None and resource.file != resource.original:
            original = resource.original
            with self._open(original.parts) as (descriptor, original_info):
                original_kind = self._file_info(original, descriptor, original_info)
            original_id = file_id(original.parts[0], '/'.join(original.parts[1:]))
            original_prefix = '/api/library/files/' + original_id
            row['original'] = dict(id=original_id, name=original.parts[-1],
                                   path='/'.join(original.parts[1:]), size_bytes=original.size,
                                   media_type=original.media_type, preview_kind=original_kind,
                                   preview_url=original_prefix + '/preview' if original_kind else None,
                                   download_url=original_prefix + '/download', metadata_url=original_prefix + '/metadata')
        return row

    def listing(self, country=None, q='', offset=0, limit=50):
        if (country is not None and not _segment(country)) or not isinstance(q, str) or len(q) > 200:
            raise LibraryError(400, '国家或搜索条件无效', 'invalid_query')
        if (not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 1000000
                or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE_SIZE):
            raise LibraryError(400, '分页参数超出允许范围', 'invalid_query')
        entries = self._entries()
        countries, rows, warnings = [], [], []
        names = [name for name, mode, _ in entries if _segment(name) and stat.S_ISDIR(mode)]
        if len(names) > MAX_COUNTRIES:
            raise LibraryError(413, '国家目录超过安全扫描上限', 'scan_limit')
        scanned = 0
        for name in names:
            count = 0
            try:
                candidates = self._entries((name,))
            except LibraryError:
                _warning(warnings, name + '：无法安全读取国家目录')
                continue
            for metadata_name, mode, links in candidates:
                if not _segment(metadata_name) or not metadata_name.endswith('.json'):
                    continue
                scanned += 1
                if scanned > MAX_LIBRARY_FILES:
                    raise LibraryError(413, '资源登记数量超过安全扫描上限', 'scan_limit')
                try:
                    # 即使是链接也交给 _open 拒绝，不能在扫描后走 Path.open。
                    resource = self._record(name, metadata_name)
                    row = self.row(resource)
                except LibraryError as exc:
                    if exc.code != 'unregistered_file':
                        _warning(warnings, name + '/' + metadata_name + '：' + exc.message)
                    continue
                count += 1
                if country is not None and name != country:
                    continue
                haystack = ' '.join((row['title'], row['name'], name,
                                     *(str(value or '') for value in row['identity'].values())))
                if q.casefold() in haystack.casefold():
                    rows.append(row)
            # 空国家仍可选择，目录扫描不把 catalog 当成法规文件。
            countries.append(dict(name=name, file_count=count))
        rows.sort(key=lambda item: (item['country'].casefold(), item['title'].casefold(), item['path']))
        return dict(countries=countries, country=country, total=len(rows), offset=offset, limit=limit,
                    files=rows[offset:offset + limit], warnings=warnings)

    def _copy_verified(self, spec, destination):
        with self._open(spec.parts) as (descriptor, info):
            kind = self._file_info(spec, descriptor, info)
            digest, total = hashlib.sha256(), 0
            decoder = codecs.getincrementaldecoder('utf-8')() if kind == 'text' else None
            try:
                while True:
                    chunk = os.read(descriptor, min(CHUNK_BYTES, spec.size + 1 - total))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > spec.size:
                        raise LibraryError(409, '读取期间文件已变化，下载已拒绝', 'integrity_mismatch')
                    if decoder:
                        if b'\x00' in chunk:
                            raise UnicodeError()
                        decoder.decode(chunk)
                    digest.update(chunk)
                    if destination is not None:
                        destination.write(chunk)
                if decoder:
                    decoder.decode(b'', final=True)
            except UnicodeError:
                raise LibraryError(409, 'Markdown 不是有效的 UTF-8 文本', 'format_mismatch') from None
            if (total != spec.size or digest.hexdigest() != spec.sha256
                    or _fingerprint(os.fstat(descriptor)) != _fingerprint(info)):
                raise LibraryError(409, '文件 SHA 或大小与登记不一致，下载已拒绝', 'integrity_mismatch')
            return kind

    @contextmanager
    def prepare(self, identifier):
        resource = self.resource(identifier)
        with tempfile.TemporaryFile(mode='w+b') as output:
            # 派生文本也验证其原响应，不以完整 Markdown 掩盖损坏的原件。
            if resource.original is not None and resource.file != resource.original:
                self._copy_verified(resource.original, None)
            kind = self._copy_verified(resource.file, output)
            output.seek(0)
            # 附件一律 octet-stream；只有确实匹配的 PDF/图片允许 inline。
            yield PreparedFile(output, resource.file.size, resource.file.parts[-1],
                               resource.file.media_type, kind)

    def text_preview(self, prepared):
        raw = prepared.stream.read(MAX_PREVIEW_BYTES + 1)
        truncated = len(raw) > MAX_PREVIEW_BYTES
        text = raw[:MAX_PREVIEW_BYTES].decode('utf-8', errors='replace')
        return dict(text=text, truncated=truncated, size_bytes=prepared.size,
                    name=prepared.name, encoding='utf-8', media_type=prepared.media_type)

    def metadata(self, identifier):
        resource = self.resource(identifier)
        self._available(resource)
        _, raw = self._json(resource.metadata)
        expected_sha = (resource.original_metadata_sha256 if resource.file == resource.original
                        else resource.primary_metadata_sha256)
        if hashlib.sha256(raw).hexdigest() != expected_sha:
            raise LibraryError(409, '读取期间元数据已变化，请刷新后重试', 'metadata_changed')
        return raw, resource.metadata[-1]

    def _relocation_root(self, identifier):
        """历史引用只在迁移声明和原批次计划 SHA 均匹配时使用旧下载根。"""
        try:
            document, _ = self._json(('.relocation.json',), limit=MAX_RELOCATION_BYTES)
        except LibraryError as exc:
            if exc.status == 404:
                return None
            raise
        invalid = LibraryError(409, '下载目录迁移记录无效或与批次不一致', 'invalid_relocation')
        if (set(document) != {'schema_version', 'old_download_base', 'download_base',
                              'roots', 'batch_plans', 'created_at'}
                or type(document.get('schema_version')) is not int
                or document['schema_version'] != 1):
            raise invalid
        before = _absolute_root(document['old_download_base'])
        after = _absolute_root(document['download_base'])
        if (after != self.download_base or after.name != 'downloads'
                or after.parent.name != 'data' or before.name != 'downloads' or before == after):
            raise invalid
        roots, plans = document['roots'], document['batch_plans']
        if not isinstance(roots, list) or not 1 <= len(roots) <= 3:
            raise invalid
        kinds = set()
        for root in roots:
            if (not isinstance(root, dict) or set(root) != {'kind', 'before', 'after'}
                    or root.get('kind') not in ('downloads', 'reports', 'tmp')
                    or root['kind'] in kinds):
                raise invalid
            kind = root['kind']
            if (_absolute_root(root['before']) != before.parent / kind
                    or _absolute_root(root['after']) != after.parent / kind):
                raise invalid
            kinds.add(kind)
        if ('downloads' not in kinds or not isinstance(plans, dict)
                or len(plans) > MAX_DIRECTORY_ENTRIES
                or any(not _BATCH_ID.fullmatch(key) or not isinstance(value, str)
                       or not _SHA.fullmatch(value) for key, value in plans.items())):
            raise invalid
        try:
            created = document['created_at']
            if not isinstance(created, str) or len(created) > 80:
                raise ValueError()
            if datetime.fromisoformat(created.replace('Z', '+00:00')).tzinfo is None:
                raise ValueError()
        except ValueError:
            raise invalid from None
        if identifier not in plans:
            raise invalid
        _, raw = self._json(('.batches', identifier, 'plan.json'))
        if hashlib.sha256(raw).hexdigest() != plans[identifier]:
            raise invalid
        return before.parts[1:]

    def _report_path(self, value, identifier=None, relocation=None):
        if not isinstance(value, str):
            raise LibraryError(409, '批次文件登记路径无效', 'invalid_report')
        # 先检查原始分段，不能让 normpath/resolve 消去 .. 后再判断。
        if value.startswith('/'):
            raw = value.split('/')[1:]
            root = list(self.download_base.parts[1:])
            if raw[:len(root)] != root:
                if identifier is None:
                    raise LibraryError(409, '批次文件不在下载根目录内', 'invalid_report')
                # 每次批次读取只验证一次声明；普通浏览及新路径不依赖此文件。
                relocation = {} if relocation is None else relocation
                if 'root' not in relocation:
                    relocation['root'] = self._relocation_root(identifier)
                root = list(relocation['root'] or ())
                if not root or raw[:len(root)] != root:
                    raise LibraryError(409, '批次文件不在下载根目录内', 'invalid_report')
            value = '/'.join(raw[len(root):])
        parts = _parts(value)
        if len(parts) != 2:
            raise LibraryError(409, '批次只允许登记国家目录中的主文件', 'invalid_report')
        return parts

    def _batch(self, identifier):
        if not isinstance(identifier, str) or not _BATCH_ID.fullmatch(identifier):
            raise LibraryError(400, '批次标识无效', 'invalid_id')
        prefix = ('.batches', identifier)
        with self._open(prefix, directory=True):
            pass
        attempts = []
        for name, mode, _ in self._entries((*prefix, 'attempts')):
            if not name.isascii() or not name.isdigit():
                continue
            if len(name) > 8 or int(name) <= 0 or not stat.S_ISDIR(mode):
                raise LibraryError(409, '批次执行轮次包含链接、非目录或无效编号', 'unsafe_attempt')
            attempts.append((name, mode))
        attempts.sort(key=lambda item: int(item[0]), reverse=True)
        report, attempt, warnings = {}, None, []
        for name, _ in attempts:
            candidate = (*prefix, 'attempts', name)
            try:
                document, _ = self._json((*candidate, 'report.json'))
            except LibraryError as exc:
                if exc.status != 404:
                    raise
                continue
            finished = document.get('finished_at')
            try:
                parsed = datetime.fromisoformat(finished.replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    raise ValueError()
            except (AttributeError, TypeError, ValueError):
                continue
            report, attempt = document, candidate
            break
        mode = _display(report.get('mode'), 120)
        if not mode:
            try:
                plan, _ = self._json((*prefix, 'plan.json'))
                mode = _display(plan.get('mode'), 120)
            except LibraryError as exc:
                if exc.status != 404:
                    raise
        resources, invalid_count, blocked_reason = [], 0, None
        if attempt is None:
            _warning(warnings, '此批次尚无已结束执行轮次，不提供打包下载')
            return BatchSelection(identifier, mode, resources, warnings, invalid_count, attempt, report)
        if attempts and attempts[0][0] != attempt[-1]:
            blocked_reason = '此批次最新执行轮次尚未结束，暂不提供 ZIP 下载'
            _warning(warnings, blocked_reason + '；当前清单来自上一已结束轮次')
        if report.get('batch_id') not in (None, identifier):
            raise LibraryError(409, '报告批次标识不一致', 'invalid_report')
        if report.get('errors'):
            _warning(warnings, '采集报告包含失败项；ZIP 仅包含本轮报告登记的成功文件，请查看 manifest.json')
        if mode == 'metadata':
            if report.get('files'):
                _warning(warnings, '元数据批次不导出原文；报告中的异常文件登记已忽略')
            return BatchSelection(identifier, mode, resources, warnings, invalid_count, attempt, report, blocked_reason)
        files = report.get('files', [])
        if not isinstance(files, list):
            raise LibraryError(409, '批次文件清单结构无效', 'invalid_report')
        if len(files) > MAX_ARCHIVE_FILES:
            raise LibraryError(413, '批次文件数超过 2000 项导出上限', 'file_limit')
        seen, relocation = set(), {}
        for index, item in enumerate(files):
            try:
                if not isinstance(item, dict):
                    raise LibraryError(409, '批次文件登记结构无效', 'invalid_report')
                parts = self._report_path(item.get('file'), identifier, relocation)
                if item.get('country') not in (None, parts[0]):
                    raise LibraryError(409, '批次国家与文件路径不一致', 'invalid_report')
                if item.get('result') in ('failed', 'fail', 'skipped', 'skip'):
                    raise LibraryError(409, '批次文件未登记成功', 'invalid_report')
                resource = self.resource(file_id(parts[0], parts[1]))
                if item.get('sha256') != resource.primary.sha256:
                    raise LibraryError(409, '批次 SHA 与当前文件登记不一致', 'integrity_mismatch')
                if (item.get('original_sha256') is not None
                        and (resource.original is None or item['original_sha256'] != resource.original.sha256)):
                    raise LibraryError(409, '批次原响应 SHA 不一致', 'integrity_mismatch')
                if item.get('metadata_file') is not None:
                    if self._report_path(item['metadata_file'], identifier, relocation) != resource.primary_metadata:
                        raise LibraryError(409, '批次元数据路径不一致', 'invalid_report')
                self._available(resource)
                identity = (resource.primary.parts[0], resource.primary.sha256)
                if identity not in seen:
                    resources.append(resource)
                    seen.add(identity)
            except LibraryError as exc:
                invalid_count += 1
                _warning(warnings, f'报告文件第 {index + 1} 项：' + exc.message)
        return BatchSelection(identifier, mode, resources, warnings, invalid_count, attempt, report, blocked_reason)

    def batch_files(self, identifier):
        selected = self._batch(identifier)
        rows = [self.row(resource) for resource in selected.resources]
        allowed = bool(rows) and not selected.invalid_count and not selected.blocked_reason
        total = sum(row['size_bytes'] for row in rows)
        if total > MAX_ARCHIVE_BYTES:
            allowed = False
            _warning(selected.warnings, '批次总大小超过 1 GiB 导出上限')
        return dict(batch_id=identifier, mode=selected.mode, file_count=len(rows), total_bytes=total,
                    attempt_number=int(selected.attempt[-1]) if selected.attempt else None,
                    finished_at=_display(selected.report.get('finished_at'), 80) or None,
                    files=rows, archive_url=f'/api/batches/{identifier}/archive.zip' if allowed else None,
                    warnings=selected.warnings)

    @contextmanager
    def archive(self, identifier):
        if not _ZIP_GATE.acquire(blocking=False):
            raise LibraryError(503, '正在生成其他批次 ZIP，请稍后重试', 'archive_busy')
        try:
            selected = self._batch(identifier)
            if selected.blocked_reason:
                raise LibraryError(409, selected.blocked_reason, 'batch_running')
            if selected.invalid_count:
                raise LibraryError(409, '批次存在缺失、损坏或登记不一致的文件；请先查看批次文件警告', 'incomplete_batch')
            if not selected.resources:
                raise LibraryError(404, '此批次没有可打包的原文文件', 'empty_batch')
            with tempfile.TemporaryFile(mode='w+b') as output:
                self._build_archive(output, selected)
                size = output.tell()
                if size > MAX_ARCHIVE_BYTES:
                    raise LibraryError(413, 'ZIP 超过 1 GiB 导出上限', 'size_limit')
                output.seek(0)
                yield PreparedFile(output, size, identifier + '.zip', 'application/zip')
        finally:
            _ZIP_GATE.release()

    def _build_archive(self, output, selected):
        # ZIP_STORED 不压缩已有 PDF/图片，CPU、内存与 2 GB 实例相容。
        total, count, used, content = 0, 0, set(), {}
        manifest = dict(schema_version=1, batch_id=selected.identifier, mode=selected.mode,
                        attempt_number=int(selected.attempt[-1]), files=[],
                        warnings=selected.warnings, failures=[], complete=not bool(selected.report.get('errors')))
        # 报告本身可能含本机路径、错误原文或凭据；只附公开白名单摘要。
        errors = selected.report.get('errors')
        if isinstance(errors, list):
            manifest['failures'] = [dict(index=index + 1, message='采集失败，详见服务器原始采集报告')
                                    for index in range(min(len(errors), 100))]
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
            def reserve(name, size):
                nonlocal total, count
                _parts(name)
                if name in used:
                    raise LibraryError(409, 'ZIP 文件名冲突', 'archive_conflict')
                count += 1
                total += size
                if count > MAX_ARCHIVE_FILES:
                    raise LibraryError(413, 'ZIP 成员超过 2000 文件上限', 'file_limit')
                # 包括 ZIP 目录/文件头开销；也在构建完成后检查实际大小。
                if total + count * 1024 > MAX_ARCHIVE_BYTES:
                    raise LibraryError(413, 'ZIP 超过 1 GiB 导出上限', 'size_limit')
                used.add(name)

            def add_file(spec):
                name = '/'.join(spec.parts)
                if spec.sha256 in content:
                    # 同 SHA 的另一登记文件仍须验证，不把坏文件伪装成重复成功。
                    self._copy_verified(spec, None)
                    return content[spec.sha256]
                reserve(name, spec.size)
                with archive.open(name, 'w') as destination:
                    self._copy_verified(spec, destination)
                content[spec.sha256] = name
                return name

            def add_metadata(parts, expected, expected_metadata_sha256, original=False):
                record, raw = self._json(parts)
                country = expected.parts[0]
                if (hashlib.sha256(raw).hexdigest() != expected_metadata_sha256
                        or record.get('schema_version') != 2
                        or self._spec(country, record.get('file'), original=original) != expected):
                    raise LibraryError(409, '打包期间元数据登记已变化', 'metadata_changed')
                name, sha = '/'.join(parts), hashlib.sha256(raw).hexdigest()
                if sha in content:
                    return content[sha]
                reserve(name, len(raw))
                archive.writestr(name, raw)
                content[sha] = name
                return name

            for resource in selected.resources:
                item = dict(path=add_file(resource.primary), sha256=resource.primary.sha256,
                            size_bytes=resource.primary.size, title=resource.title,
                            country=resource.primary.parts[0], sources=resource.sources)
                item['metadata'] = add_metadata(resource.primary_metadata, resource.primary,
                                                resource.primary_metadata_sha256)
                if resource.original is not None:
                    item['original'] = dict(path=add_file(resource.original), sha256=resource.original.sha256,
                                            size_bytes=resource.original.size,
                                            metadata=add_metadata(resource.original_metadata, resource.original,
                                                                  resource.original_metadata_sha256, original=True))
                manifest['files'].append(item)
            payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
            reserve('manifest.json', len(payload))
            archive.writestr('manifest.json', payload)
