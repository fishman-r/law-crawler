"""已归档原件的持久只读法规目录；身份、用途和版本均保留证据边界。"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unicodedata
from urllib.parse import unquote, urlsplit, quote, urlencode

from observer.library import LibraryError, LibraryReader, _display, _fingerprint
from observer.archive_export import ArchiveExporter
from observer.snapshot import redact

TRUSTED = {'observed', 'html_context_observed', 'verified', 'independently_verified'}
CLASSIFICATIONS = {
    'regulation': '法规', 'judicial_guidance': '司法解释及司法指引',
    'administrative_decision': '行政决定（非目标法规）', 'other': '其他官方材料',
    'pending': '待核验材料',
}
SORTS = {
    'published_desc': 'published_at IS NULL, published_at DESC, id',
    'published_asc': 'published_at IS NULL, published_at ASC, id',
    'title_asc': 'title COLLATE NOCASE ASC, id',
    'title_desc': 'title COLLATE NOCASE DESC, id',
    'year_desc': "year IS NULL, year DESC, id",
    'year_asc': "year IS NULL, year ASC, id",
}
SHA = re.compile(r'[a-f0-9]{64}\Z')
MAX_TITLE_TRANSLATION_BYTES = 8 * 1024 * 1024
MAX_TITLE_TRANSLATIONS = 20000
TITLE_TRANSLATION_STATUSES = {'reference_translation', 'reviewed_reference_translation'}


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _value(metadata, key):
    item = metadata.get(key)
    return item.get('value') if isinstance(item, dict) and 'value' in item else item


def _normalize(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(value)).casefold()).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ''


def _success(row):
    return (row.get('capture_status') in (None, 'saved', 'reused') and row.get('complete') is True
            and type(row.get('status_code')) is int and 200 <= row['status_code'] < 300
            and isinstance(row.get('sha256'), str) and SHA.fullmatch(row['sha256'].lower()))


def _date(metadata):
    field = metadata.get('published_at')
    if not isinstance(field, dict) or field.get('status') not in TRUSTED:
        return None
    value = field.get('value')
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:T[^\s]+)?', value):
        return None
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return value


def _identity(metadata):
    identity = _value(metadata, 'legal_identity')
    field = metadata.get('legal_identity')
    keys = ('jurisdiction', 'issuer', 'type', 'number', 'year')
    if not isinstance(identity, dict):
        return {key: None for key in keys}, None
    public = {key: _display(identity.get(key), 250) or None for key in keys}
    canonical = tuple(_normalize(identity.get(key)) for key in keys)
    return public, canonical if isinstance(field, dict) and field.get('status') in TRUSTED and all(canonical) else None


def _version(metadata):
    item = metadata.get('version_id') or metadata.get('consolidation_date')
    if (isinstance(item, dict) and item.get('status') in TRUSTED and item.get('source_url')
            and _normalize(item.get('value'))):
        return _display(item['value'], 250)
    return None


def _role(metadata, url, target):
    item = metadata.get('file_roles', {})
    item = item.get(url) if isinstance(item, dict) else None
    if not isinstance(item, dict) or item.get('status') not in TRUSTED:
        return 'pending'
    return ({'regulation_body': 'body', 'regulation_annex': 'annex',
             'related_document': 'related', 'supporting_document': 'related'}.get(item.get('role'), 'pending')
            if target else ('related' if item.get('role') in ('related_document', 'supporting_document') else 'pending'))


def _name(receipt, path):
    try:
        name = unquote(urlsplit(receipt.get('final_url') or receipt.get('url') or '').path).rsplit('/', 1)[-1]
    except ValueError:
        name = ''
    name = re.sub(r'[\\/:\x00-\x1f\x7f]', '_', name).strip('. ')[:180]
    if not name or not Path(name).suffix or len(name) < 5:
        name = path.name
    elif Path(name).suffix.lower() != path.suffix.lower():
        name += path.suffix.lower()
    return name


def _strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('中文参考译名 JSON 不得包含重复键')
        result[key] = value
    return result


def _read_title_translations(path, optional=False):
    """读取独立中文参考译名；不追随链接，不接受未绑定原文的译名。"""
    path = Path(path).absolute()
    reader = LibraryReader(path.parent)
    try:
        with reader._open((path.name,)) as (descriptor, before):
            if before.st_size > MAX_TITLE_TRANSLATION_BYTES:
                raise ValueError('中文参考译名文件超过 8 MiB 上限')
            chunks, size = [], 0
            while True:
                chunk = os.read(descriptor, min(65536, MAX_TITLE_TRANSLATION_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_TITLE_TRANSLATION_BYTES:
                    raise ValueError('中文参考译名文件超过 8 MiB 上限')
                chunks.append(chunk)
            if size != before.st_size or _fingerprint(before) != _fingerprint(os.fstat(descriptor)):
                raise ValueError('中文参考译名读取期间发生变化')
    except LibraryError as exc:
        if optional and exc.status == 404:
            return {}, None
        raise ValueError('中文参考译名文件不存在或无法安全读取') from None
    raw = b''.join(chunks)
    try:
        payload = json.loads(raw.decode('utf-8'), object_pairs_hook=_strict_pairs)
    except (ValueError, UnicodeError):
        raise ValueError('中文参考译名不是有效的 UTF-8 JSON') from None
    if (not isinstance(payload, dict) or type(payload.get('schema_version')) is not int
            or payload['schema_version'] != 1 or not isinstance(payload.get('translations'), list)
            or len(payload['translations']) > MAX_TITLE_TRANSLATIONS):
        raise ValueError('中文参考译名结构无效或超过 20000 项上限')
    result = {}
    for item in payload['translations']:
        if not isinstance(item, dict):
            raise ValueError('中文参考译名条目无效')
        identifier, original_sha = item.get('record_id'), item.get('original_title_sha256')
        title, status = item.get('title_zh'), item.get('status')
        if (not isinstance(identifier,str) or not SHA.fullmatch(identifier)
                or not isinstance(original_sha,str) or not SHA.fullmatch(original_sha)
                or not isinstance(title,str) or not title.strip() or len(title)>1500
                or any(ord(char)<32 or ord(char)==127 for char in title)
                or not isinstance(status,str) or status not in TITLE_TRANSLATION_STATUSES or identifier in result):
            raise ValueError('中文参考译名须包含唯一记录 ID、原文 SHA、译名和有效状态')
        result[identifier] = dict(original_title_sha256=original_sha,title_zh=title.strip(),status=status)
    return result, hashlib.sha256(raw).hexdigest()


def build_archive_index(download_base, state_path, output, title_translations_path=None):
    """逐文件核对完整收据、路径、格式与 SHA，再原子替换索引；不触碰原始状态。"""
    reader = LibraryReader(download_base)
    translation_path = (Path(title_translations_path) if title_translations_path is not None else
                        reader.download_base / 'Indonesia/pipeline/library/title-translations.json')
    title_translations, title_translations_sha = _read_title_translations(translation_path, optional=title_translations_path is None)
    state_path, output = Path(state_path), Path(output)
    before = state_path.stat()
    raw = state_path.read_bytes()
    after = state_path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('归档状态读取期间发生变化，请稍后重建索引')
    state = json.loads(raw)
    documents = state.get('documents', {})
    groups, verified, rejected, validated = {}, {}, [], {}
    exporter = ArchiveExporter(download_base)
    # 同五字段的不同文种不能自动归并成已确认法规。
    kinds = defaultdict(set)
    for doc in documents.values():
        metadata = doc.get('metadata') or {}
        _, key = _identity(metadata)
        if key:
            kinds[key].add(_value(metadata, 'document_type'))
    for detail_key, doc in documents.items():
        metadata = doc.get('metadata') or {}
        identity, canonical = _identity(metadata)
        kind = _value(metadata, 'document_type')
        eligible = metadata.get('processing_gate', 'eligible') == 'eligible'
        target = kind in ('regulation', 'judicial_guidance') and eligible
        classification = kind if kind in CLASSIFICATIONS and kind != 'pending' else 'pending'
        if not target and kind not in ('administrative_decision', 'other', 'court_judgment'):
            classification = 'pending'
        if kind == 'court_judgment':
            classification = 'other'
        confirmed = canonical is not None and len(kinds[canonical]) == 1
        if target and not confirmed:
            classification = 'pending'
        record_id = _digest(['legal_identity', canonical]) if confirmed else _digest(['detail', str(detail_key)])
        accepted = []
        for receipt in (doc.get('files') or {}).values():
            if not isinstance(receipt, dict) or not _success(receipt):
                continue
            digest = receipt['sha256'].lower()
            try:
                path = Path(receipt.get('path') or '')
                relative = path.relative_to(reader.download_base)
                parts = relative.parts
                if (len(parts) != 5 or parts[:4] != ('Indonesia', 'pipeline', 'raw', 'objects')
                        or not re.fullmatch(digest + r'\.[a-zA-Z0-9]{1,12}', parts[-1])):
                    raise ValueError('原件未位于登记的印尼原始归档目录')
                size = receipt.get('size_bytes')
                if type(size) is not int or size <= 0:
                    raise ValueError('原件大小无效')
                headers = receipt.get('headers') or {}
                media = str(headers.get('content-type') or 'application/octet-stream').split(';', 1)[0].strip().lower()
                if not re.fullmatch(r'[a-z0-9.+-]+/[a-z0-9.+-]+', media):
                    media = 'application/octet-stream'
                cache_key = (parts, size, digest)
                with reader._open(parts) as (descriptor, info):
                    fingerprint = _fingerprint(info)
                    head = os.read(descriptor, 64)
                if validated.get(cache_key) != fingerprint:
                    spec = dict(parts=list(parts),size_bytes=size,sha256=digest)
                    exporter._copy_verified(spec, None)
                    with reader._open(parts) as (descriptor, info):
                        if _fingerprint(info) != fingerprint:
                            raise ValueError('索引核验期间原件变化')
                    validated[cache_key] = fingerprint
                preview_kind = None
                if path.suffix.lower() == '.pdf' and re.match(br'%PDF-[12]\.[0-9]',head):
                    media, preview_kind = 'application/pdf', 'pdf'
                elif path.suffix.lower() == '.png' and head.startswith(b'\x89PNG\r\n\x1a\n'):
                    media, preview_kind = 'image/png', 'image'
                elif path.suffix.lower() in ('.jpg','.jpeg') and head.startswith(b'\xff\xd8\xff'):
                    media, preview_kind = 'image/jpeg', 'image'
                elif path.suffix.lower() == '.gif' and head.startswith((b'GIF87a',b'GIF89a')):
                    media, preview_kind = 'image/gif', 'image'
                elif path.suffix.lower() == '.webp' and head.startswith(b'RIFF') and head[8:12] == b'WEBP':
                    media, preview_kind = 'image/webp', 'image'
                elif path.suffix.lower() in ('.md','.txt','.xml','.html','.htm','.json','.csv'):
                    # Render textual original formats only through escaped text preview.
                    preview_kind = 'text'
                verified.setdefault(digest,dict(parts=list(parts),sha256=digest,size_bytes=size,
                    media_type=media,name=_name(receipt,path),preview_kind=preview_kind))
                accepted.append((digest, _role(metadata, receipt.get('url'), target)))
            except (OSError, ValueError, TypeError, LibraryError) as exc:
                rejected.append(dict(sha256=digest,reason=exc.message if isinstance(exc,LibraryError) else type(exc).__name__))
        if not accepted:
            continue
        source = dict(detail_url=redact(doc.get('official_detail_url') or doc.get('detail_url') or ''),
                      platform=_display(doc.get('platform'), 250))
        group = groups.setdefault(record_id, dict(id=record_id, title='', identity=identity,
            identity_status='identified' if confirmed else 'pending', classification=classification,
            dates=set(), versions=set(), version_pending=False, files=defaultdict(set), sources=[], complete=True))
        title = _display(_value(metadata, 'title'), 1500)
        if title and (not group['title'] or title < group['title']):
            group['title'] = title
        published = _date(metadata)
        if published:
            group['dates'].add(published)
        version = _version(metadata)
        if version:
            group['versions'].add(version)
        else:
            group['version_pending'] = True
        for digest, role in accepted:
            group['files'][digest].add(role)
        if source not in group['sources']:
            group['sources'].append(source)
        group['complete'] = group['complete'] and doc.get('status') == 'archived' and len(accepted) == len(doc.get('files') or {})
    rows = []
    for group in groups.values():
        files = []
        for digest, roles in sorted(group['files'].items()):
            spec = verified[digest]
            files.append(dict(id=digest, name=spec['name'], size_bytes=spec['size_bytes'], media_type=spec['media_type'],
                role=next(iter(roles)) if len(roles) == 1 else 'pending', version=None, preview_kind=spec['preview_kind'],
                download_url='/api/regulations/files/' + digest + '/download',
                preview_url='/api/regulations/files/' + digest + '/preview'))
        versions = sorted(group['versions'])
        year = group['identity'].get('year')
        row = dict(id=group['id'], title=group['title'] or '名称待核验', identity=group['identity'],
            identity_status=group['identity_status'], classification=group['classification'],
            published_at=max(group['dates']) if group['dates'] else None,
            year=int(year) if year and re.fullmatch(r'\d{4}', str(year)) else None,
            version_status='pending' if group['version_pending'] or not versions else 'observed', versions=versions,
            files=files, sources=sorted(group['sources'], key=lambda s: (s['platform'], s['detail_url'])),
            download_status='complete' if group['complete'] else 'partial')
        translated = title_translations.get(row['id'])
        row['title_zh'], row['title_translation_status'] = None, 'missing'
        if translated:
            original_sha = hashlib.sha256(row['title'].encode('utf-8')).hexdigest()
            if translated['original_title_sha256'] == original_sha:
                row['title_zh'] = translated['title_zh']
                row['title_translation_status'] = translated['status']
            else:
                row['title_translation_status'] = 'stale'
        rows.append(row)
    summary = dict(records=len(rows), unique_files=len(verified), regulations=sum(r['classification'] == 'regulation' for r in rows),
        judicial_guidance=sum(r['classification'] == 'judicial_guidance' for r in rows),
        pending=sum(r['classification'] == 'pending' for r in rows), rejected_files=len(rejected),
        translated_titles=sum(r['title_zh'] is not None for r in rows),
        pending_title_translations=sum(r['title_zh'] is None for r in rows),
        stale_title_translations=sum(r['title_translation_status']=='stale' for r in rows))
    snapshot_id = _digest(dict(state_sha256=hashlib.sha256(raw).hexdigest(), title_translations_sha256=title_translations_sha, files=sorted(verified), records=sorted(r['id'] for r in rows)))
    meta = dict(snapshot_id=snapshot_id, created_at=datetime.now(timezone.utc).isoformat(),
        state_sha256=hashlib.sha256(raw).hexdigest(), title_translations_sha256=title_translations_sha, summary=summary,
        warnings=['仅展示已完整核对 SHA 的原始文件；身份、文件用途和版本未确认项保留待核验。',
                  '目录范围仅限当前已下载材料，不能据此认定印尼全国法规已收齐。',
                  '中文标题为非官方参考译名；原文标题保持不变，缺失或原文绑定失效的译名不予展示。'], rejected=rejected)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        raise ValueError('索引目标不得为符号链接')
    descriptor, temporary = tempfile.mkstemp(prefix='.archive-index-', suffix='.sqlite3', dir=output.parent)
    os.close(descriptor)
    try:
        db = sqlite3.connect(temporary)
        try:
            db.executescript('''CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE records(id TEXT PRIMARY KEY,title TEXT NOT NULL,classification TEXT NOT NULL,published_at TEXT,year INTEGER,search TEXT NOT NULL,data TEXT NOT NULL);
                CREATE INDEX classification_idx ON records(classification);
                CREATE INDEX published_idx ON records(published_at);
                CREATE TABLE files(id TEXT PRIMARY KEY,data TEXT NOT NULL);''')
            db.executemany('INSERT INTO meta VALUES (?,?)', [(key, _json(value)) for key, value in meta.items()])
            db.executemany('INSERT INTO records VALUES (?,?,?,?,?,?,?)', [(r['id'], r['title'],r['classification'],r['published_at'],r['year'],
                _normalize(' '.join([r['title'], r['title_zh'] or '', *[str(v or '') for v in r['identity'].values()], *[s['platform'] for s in r['sources']]])),_json(r)) for r in rows])
            db.executemany('INSERT INTO files VALUES (?,?)', [(key, _json(spec)) for key,spec in verified.items()])
            db.commit()
        finally:
            db.close()
        os.chmod(temporary, 0o640)
        with open(temporary, 'rb') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return meta


class ArchiveCatalog:
    def __init__(self, download_base, index_path):
        self.download_base, self.index_path = Path(download_base), Path(index_path)

    def _connect(self):
        if self.index_path.is_symlink() or not self.index_path.is_file():
            raise LibraryError(503, '法规库索引尚未就绪', 'archive_index_unavailable')
        try:
            db = sqlite3.connect('file:' + quote(str(self.index_path.absolute()), safe='/') + '?mode=ro', uri=True, timeout=5)
            db.execute('PRAGMA query_only=ON')
            return db
        except sqlite3.Error:
            raise LibraryError(503, '法规库索引暂不可用', 'archive_index_unavailable') from None

    def listing(self, q='', page=1, page_size=20, classification='all', sort='published_desc'):
        result = self.selection(q=q,page=page,page_size=page_size,classification=classification,sort=sort)
        result.pop('_files', None)
        return result

    def selection(self, q='', page=1, page_size=20, classification='all', sort='published_desc'):
        if (not isinstance(q,str) or len(q)>500 or type(page) is not int or not 1<=page<=100000
                or type(page_size) is not int or not 1<=page_size<=100 or classification not in ('all',*CLASSIFICATIONS)
                or sort not in SORTS):
            raise LibraryError(400, '法规检索参数无效', 'invalid_query')
        normalized = _normalize(q)
        clauses, args = [], []
        if normalized:
            clauses.append("search LIKE ? ESCAPE '\\'")
            args.append('%'+normalized.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
        if classification != 'all':
            clauses.append('classification=?'); args.append(classification)
        where = ' WHERE '+' AND '.join(clauses) if clauses else ''
        db = self._connect()
        try:
            meta = {key:json.loads(value) for key,value in db.execute('SELECT key,value FROM meta')}
            total = db.execute('SELECT count(*) FROM records'+where,args).fetchone()[0]
            items = [json.loads(row[0]) for row in db.execute('SELECT data FROM records'+where+' ORDER BY '+SORTS[sort]+' LIMIT ? OFFSET ?',args+[page_size,(page-1)*page_size])]
            counts = dict(db.execute('SELECT classification,count(*) FROM records GROUP BY classification'))
            digests = sorted({f['id'] for r in items for f in r['files']})
            files = [json.loads(db.execute('SELECT data FROM files WHERE id=?',(digest,)).fetchone()[0]) for digest in digests]
            query = dict(q=q,page=page,page_size=page_size,classification=classification,sort=sort)
            token = _digest(dict(snapshot_id=meta['snapshot_id'],query=query,records=[dict(id=r['id'],files=[f['id'] for f in r['files']]) for r in items]))
            return dict(available=True,items=items,total=total,page=page,page_size=page_size,total_pages=(total+page_size-1)//page_size,
                snapshot_id=meta['snapshot_id'],page_token=token,facets=dict(classification=[dict(value=k,label=v,count=counts.get(k,0)) for k,v in CLASSIFICATIONS.items()]),
                summary=meta['summary'],warnings=meta['warnings'],updated_at=meta['created_at'],query=query,
                archive_url='/api/regulations/archive.zip?'+urlencode(dict(query,token=token)),_files=files)
        except (sqlite3.Error, KeyError, ValueError, TypeError):
            raise LibraryError(503,'法规库索引不可用，请重建索引','archive_index_invalid') from None
        finally:
            db.close()

    def get_file(self, identifier):
        if not isinstance(identifier,str) or not SHA.fullmatch(identifier):
            raise LibraryError(400,'文件标识无效','invalid_id')
        db=self._connect()
        try:
            result=db.execute('SELECT data FROM files WHERE id=?',(identifier,)).fetchone()
            if result is None:
                raise LibraryError(404,'法规原件不存在','not_found')
            return json.loads(result[0])
        except (sqlite3.Error,ValueError):
            raise LibraryError(503,'法规库索引暂不可用','archive_index_invalid') from None
        finally:
            db.close()
