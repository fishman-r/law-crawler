"""已归档法规的安全原件读取和当页 ZIP 导出，不接受客户端任意路径。"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

from observer.library import (
    CHUNK_BYTES, LibraryError, LibraryReader, PreparedFile, _fingerprint,
    _parts, _ZIP_GATE,
)

MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_FILES = 2000
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_EXT = re.compile(r'\.[A-Za-z0-9]{1,12}\Z')
_PUBLIC_ITEM_KEYS = ('id', 'title', 'title_zh', 'title_translation_status', 'identity', 'identity_status', 'classification',
                     'published_at', 'version_status', 'versions', 'files',
                     'sources', 'download_status')


def page_token(selection):
    """与目录页共用的规范 token；查询、排序、分页或文件集合变化都拒绝旧导出。"""
    payload = dict(snapshot_id=selection.get('snapshot_id'), query=selection.get('query'),
                   records=[dict(id=item.get('id'),
                                 files=[value.get('id') for value in item.get('files', [])])
                            for item in selection.get('items', [])])
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def _safe_name(spec):
    # Retain the archived original format, not a potentially misleading URL name.
    suffix = Path(spec['parts'][-1]).suffix
    return spec['sha256'] + (suffix.lower() if _EXT.fullmatch(suffix) else '.bin')


class ArchiveExporter:
    def __init__(self, download_base):
        self.reader = LibraryReader(download_base)

    def _validate_spec(self, spec):
        if not isinstance(spec, dict):
            raise LibraryError(409, '原件登记无效', 'invalid_metadata')
        parts, sha, size = spec.get('parts'), spec.get('sha256'), spec.get('size_bytes')
        if (not isinstance(parts, (list, tuple)) or not parts
                or not all(isinstance(part, str) for part in parts)):
            raise LibraryError(409, '原件登记路径无效', 'invalid_metadata')
        # Require exact stored segments, never normalize away traversal or separators.
        if (tuple(parts) != _parts('/'.join(parts)) or len(parts) != 5
                or parts[0] != 'Indonesia' or tuple(parts[1:4]) != ('pipeline', 'raw', 'objects')):
            raise LibraryError(409, '原件登记路径无效', 'invalid_metadata')
        if (not isinstance(sha, str) or not _SHA.fullmatch(sha)
                or not isinstance(size, int) or isinstance(size, bool) or size < 1):
            raise LibraryError(409, '原件 SHA 或大小登记无效', 'invalid_metadata')
        if not re.fullmatch(re.escape(sha) + r'\.[A-Za-z0-9]{1,12}', parts[-1]):
            raise LibraryError(409, '原件路径与 SHA 登记不一致', 'invalid_metadata')
        if size > MAX_ARCHIVE_BYTES:
            raise LibraryError(413, '原件超过 1 GiB 导出上限', 'size_limit')
        return tuple(parts), sha, size

    def _copy_verified(self, spec, destination):
        parts, expected_sha, expected_size = self._validate_spec(spec)
        with self.reader._open(parts) as (descriptor, before):
            if before.st_size != expected_size:
                raise LibraryError(409, '原件大小与登记不一致，请刷新目录', 'integrity_mismatch')
            digest, total = hashlib.sha256(), 0
            while True:
                chunk = os.read(descriptor, min(CHUNK_BYTES, expected_size + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > expected_size:
                    raise LibraryError(409, '读取期间原件已变化', 'integrity_mismatch')
                digest.update(chunk)
                if destination is not None:
                    destination.write(chunk)
            if (total != expected_size or digest.hexdigest() != expected_sha
                    or _fingerprint(os.fstat(descriptor)) != _fingerprint(before)):
                raise LibraryError(409, '原件 SHA 或大小与登记不一致，已拒绝下载', 'integrity_mismatch')

    @contextmanager
    def prepare_file(self, spec):
        self._validate_spec(spec)
        with tempfile.TemporaryFile(mode='w+b') as output:
            self._copy_verified(spec, output)
            size = output.tell()
            output.seek(0)
            head = output.read(64)
            output.seek(0)
            media = spec.get('media_type')
            if not isinstance(media, str) or not re.fullmatch(r'[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+', media):
                media = 'application/octet-stream'
            # Active documents are only shown as escaped JSON text, never inline HTML.
            binary_signatures = {
                'image/png': head.startswith(b'\x89PNG\r\n\x1a\n'),
                'image/jpeg': head.startswith(b'\xff\xd8\xff'),
                'image/gif': head.startswith((b'GIF87a', b'GIF89a')),
                'image/webp': head.startswith(b'RIFF') and head[8:12] == b'WEBP',
            }
            kind = ('pdf' if media == 'application/pdf' and re.match(br'%PDF-[12]\.[0-9]', head)
                    else 'image' if binary_signatures.get(media) else 'text' if (
                        media.startswith('text/') or media in ('application/json', 'application/xml')
                        or spec.get('preview_kind') == 'text') else None)
            yield PreparedFile(output, size, _safe_name(spec), media, kind)

    def _selection(self, selection, expected_token):
        if not isinstance(selection, dict) or not isinstance(selection.get('items'), list):
            raise LibraryError(409, '当前页目录无效', 'invalid_selection')
        items = selection['items']
        if (len(items) > 100 or type(selection.get('page_size')) is not int
                or not 1 <= selection['page_size'] <= 100
                or len(items) > selection['page_size']):
            raise LibraryError(400, '当页导出超出分页范围', 'invalid_selection')
        actual = page_token(selection)
        if (selection.get('page_token') != actual
                or (expected_token is not None and expected_token != actual)):
            raise LibraryError(409, '目录页已变化，请刷新后重新导出', 'stale_page')
        specs = selection.get('_files')
        if not isinstance(specs, list):
            raise LibraryError(409, '当前页原件登记缺失', 'invalid_selection')
        public_files = {value.get('id') for item in items for value in item.get('files', [])}
        # Public SHA identifiers and internal registered specs must be exactly bound.
        internal = set()
        for spec in specs:
            _, sha, _ = self._validate_spec(spec)
            internal.add(sha)
        if internal != public_files:
            raise LibraryError(409, '当前页与原件登记不一致', 'invalid_selection')
        return specs

    @contextmanager
    def archive(self, selection, expected_token=None):
        specs = self._selection(selection, expected_token)
        if not specs:
            raise LibraryError(404, '当前页没有可导出的已下载原件', 'empty_page')
        if not _ZIP_GATE.acquire(blocking=False):
            raise LibraryError(503, '正在生成其他 ZIP，请稍后重试', 'archive_busy')
        try:
            with tempfile.TemporaryFile(mode='w+b') as output:
                self._build_archive(output, selection, specs)
                size = output.tell()
                if size > MAX_ARCHIVE_BYTES:
                    raise LibraryError(413, 'ZIP 超过 1 GiB 导出上限，请减少每页数量', 'size_limit')
                output.seek(0)
                yield PreparedFile(output, size, 'indonesia-regulations-page-' + str(selection['page']) + '.zip',
                                   'application/zip')
        finally:
            _ZIP_GATE.release()

    def _build_archive(self, output, selection, specs):
        by_sha = {}
        total = 0
        for spec in specs:
            _, sha, size = self._validate_spec(spec)
            if sha in by_sha and by_sha[sha]['size_bytes'] != size:
                raise LibraryError(409, '相同 SHA 的原件登记冲突', 'invalid_metadata')
            if sha not in by_sha:
                by_sha[sha] = spec
                total += size
        if len(by_sha) + 1 > MAX_ARCHIVE_FILES:
            raise LibraryError(413, 'ZIP 文件数超过安全上限', 'file_limit')
        if total + (len(by_sha) + 1) * 1024 > MAX_ARCHIVE_BYTES:
            raise LibraryError(413, 'ZIP 超过 1 GiB 导出上限，请减少每页数量', 'size_limit')
        manifest = dict(schema_version=1, kind='indonesia-regulation-page-export',
                        created_at=datetime.now(timezone.utc).isoformat(),
                        snapshot_id=selection.get('snapshot_id'), page_token=selection['page_token'],
                        query=selection.get('query'), total=selection.get('total'), page=selection['page'],
                        page_size=selection['page_size'],
                        regulations=[{key: item.get(key) for key in _PUBLIC_ITEM_KEYS if key in item}
                                     for item in selection['items']],
                        files=[dict(sha256=sha, path='originals/' + _safe_name(spec),
                                    size_bytes=spec['size_bytes'], media_type=spec.get('media_type'))
                               for sha, spec in by_sha.items()],
                        note='按当页法规导出；文件按 SHA 去重。身份、用途、版本待核验项保留原标注，不代表法规效力核验。')
        payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
        if len(payload) > MAX_MANIFEST_BYTES or total + len(payload) + (len(by_sha) + 1) * 1024 > MAX_ARCHIVE_BYTES:
            raise LibraryError(413, '导出清单或 ZIP 超过安全上限', 'size_limit')
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
            for sha, spec in by_sha.items():
                with archive.open('originals/' + _safe_name(spec), 'w') as destination:
                    self._copy_verified(spec, destination)
            # Validate alternate registered locations too; dedup must not hide corruption.
            for spec in specs:
                if spec is not by_sha[spec['sha256']]:
                    self._copy_verified(spec, None)
            archive.writestr('manifest.json', payload)
