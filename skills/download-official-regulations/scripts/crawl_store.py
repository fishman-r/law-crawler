"""下载状态与内容去重。SQLite 为可重建索引，原件/JSON 保留来源证据。"""
import hashlib
import json
import os
import sqlite3
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from document_formats import identify_document, validate_pdf
from file_lock import FileLock


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical_url(url):
    """只规范化已知等价部分；保留路径大小写、查询顺序、签名和重复参数。"""
    parts = urlsplit(url)
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname or parts.username:
        raise ValueError(f'不支持的 URL: {url}')
    scheme, host = parts.scheme.lower(), parts.hostname.lower()
    host = f'[{host}]' if ':' in host else host
    port = parts.port
    if port is not None and (scheme, port) not in (('http', 80), ('https', 443)):
        host += f':{port}'
    return urlunsplit((scheme, host, parts.path or '/', parts.query, ''))


def fingerprint(url):
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()


def atomic_write(path, data):
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.part', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_json(path, record):
    atomic_write(path, (json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode())


def unknown_metadata(url):
    def unknown(meaning):
        return dict(value=None, raw=None, source_label=None, source_url=url,
                    status='not_provided', meaning=meaning)
    return dict(title=unknown('尚未获取详情元数据'),
                published_at=unknown('法规公布日期，非下载时间'),
                updated_at=unknown('官网明确提供的更新时间，非本地抓取时间'),
                effective_region=unknown('需官方明确适用地区，不能从制定地点推断'),
                legal_status='not_verified')


def unreviewed_body():
    return dict(status='not_reviewed', reason='尚未显式登记正文身份核验；文件完整性不代表正文类型或身份通过。',
                evidence=None, reviewed_at=None)


def retain_body_review(record, review, original_sha256):
    """历史快照只保存同一原件的显式审核，并避免重复快照。"""
    if not review or review.get('status') == 'not_reviewed':
        return
    snapshot = dict(original_sha256=original_sha256,
                    **{key: review[key] for key in ('status', 'reason', 'evidence', 'reviewed_at')})
    history = record.setdefault('body_review_history', [])
    if snapshot not in history:
        history.append(snapshot)


class CrawlStore:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(self.directory / '.crawler.lock', '此下载目录已有爬虫运行，请稍后重试')
        try:
            self._initialize_database()
        except Exception:
            if hasattr(self, 'db'):
                self.db.close()
            self.lock.close()
            raise

    def _initialize_database(self):
        self.db = sqlite3.connect(self.directory / 'crawl-state.sqlite3')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS objects (
                sha256 TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, record_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requests (
                canonical_url TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL, sha256 TEXT REFERENCES objects(sha256),
                attempts INTEGER NOT NULL DEFAULT 0, checked_at TEXT, error TEXT
            );
            CREATE TABLE IF NOT EXISTS versions (
                canonical_url TEXT NOT NULL, sha256 TEXT NOT NULL REFERENCES objects(sha256),
                first_seen_at TEXT NOT NULL, PRIMARY KEY(canonical_url, sha256)
            );
        ''')
        self.migration_errors = []
        self.reconcile()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        try:
            self.db.close()
        finally:
            self.lock.close()

    def _path(self, record):
        path = Path(record['file']['path'])
        path = path if path.is_absolute() else self.directory / path
        path = path.resolve()
        if not path.is_relative_to(self.directory):
            raise ValueError('文件路径越出下载目录')
        return path

    def valid(self, record):
        try:
            data = self._path(record).read_bytes()
            if record['file'].get('media_type') == 'text/markdown' and record.get('original_file'):
                original = record['original_file']
                raw = self._path({'file': original}).read_bytes()
                from text_documents import detect_text_document
                detected_raw = detect_text_document(raw, record.get('conversion', {}).get('metadata', record.get('document')))
                if (not detected_raw or detected_raw['media_type'] != original['media_type']
                        or record.get('conversion', {}).get('original_sha256') != original['sha256']
                        or len(raw) != original['size_bytes']
                        or hashlib.sha256(raw).hexdigest() != original['sha256']):
                    return False
                detected = {'media_type': 'text/markdown'}
            else:
                detected = identify_document(data)
            if record['file'].get('media_type') != detected['media_type']:
                return False
            return len(data) == record['file']['size_bytes'] and hashlib.sha256(data).hexdigest() == record['file']['sha256']
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def _index(self, record):
        sha = record.get('original_file', record['file'])['sha256']
        self.db.execute('INSERT INTO objects VALUES (?,?,?) ON CONFLICT(sha256) DO UPDATE SET path=excluded.path, record_json=excluded.record_json',
                        (sha, str(self._path(record)), json.dumps(record, ensure_ascii=False)))

    def persist(self, record):
        self.upgrade_conversion(record, persist=False)
        record.setdefault('body_review', unreviewed_body())
        record['crawl']['last_checked_at'] = now()
        write_json(self._path(record).with_suffix('.json'), record)
        if record.get('original_file'):
            raw_path = self._path({'file': record['original_file']})
            raw_sidecar = raw_path.with_name(raw_path.name + '.metadata.json')
            write_json(raw_sidecar, dict(schema_version=2, document=record['document'],
                file=record['original_file'], derived_file=record['file'],
                source_url=record.get('conversion', {}).get('source_url'), body_review=record['body_review'],
                body_review_history=record.get('body_review_history', [])))
        self._index(record)
        self.db.commit()

    def register_body_review(self, original_sha256, review):
        """显式审核绑定原始字节 SHA；不执行提取、类型猜测、OCR 或法律效力判断。"""
        allowed = {'title_type_number_year_match', 'unknown_no_extractable_text_first_two_pages',
                   'catalogue_body_type_conflict', 'not_reviewed'}
        if not isinstance(review, dict) or review.get('status') not in allowed:
            raise ValueError('正文审核状态不受支持')
        if not isinstance(review.get('reason'), str) or not review['reason'].strip():
            raise ValueError('正文审核必须包含原因')
        if not isinstance(review.get('evidence'), dict) or not review['evidence']:
            raise ValueError('正文审核必须包含显式证据')
        try:
            reviewed = datetime.fromisoformat(review['reviewed_at'].replace('Z', '+00:00'))
            if reviewed.tzinfo is None:
                raise ValueError('正文审核时间必须包含时区')
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise ValueError('正文审核必须包含有效 reviewed_at 时间和时区') from exc
        record = self.by_hash(original_sha256)
        if not record or not self.valid(record):
            raise ValueError('审核指定的原字节 SHA 不存在或文件校验失败')
        current_review = {key: review[key] for key in ('status', 'reason', 'evidence', 'reviewed_at')}
        if record.get('body_review') != current_review:
            retain_body_review(record, record.get('body_review'), original_sha256)
        record['body_review'] = current_review
        self.persist(record)
        return record

    def _request(self, url, sha, checked, attempt=False):
        url = canonical_url(url)
        self.db.execute('''INSERT INTO requests VALUES (?,?,'succeeded',?,?,?,NULL)
            ON CONFLICT(canonical_url) DO UPDATE SET status='succeeded',sha256=excluded.sha256,
            attempts=requests.attempts+excluded.attempts,checked_at=excluded.checked_at,error=NULL
            WHERE excluded.checked_at >= COALESCE(requests.checked_at,'')''',
                        (url, fingerprint(url), sha, int(attempt), checked))
        self.db.execute('INSERT OR IGNORE INTO versions VALUES (?,?,?)', (url, sha, checked))

    def reconcile(self):
        """迁移旧 JSON；索引删除后可重建。迁移前备份且不删除原始 PDF。"""
        for sidecar in sorted(self.directory.glob('*.json')):
            if sidecar.name == 'last-run.json':
                continue
            try:
                record = json.loads(sidecar.read_text(encoding='utf-8'))
                if not isinstance(record, dict):
                    continue
                if record.get('schema_version') == 2 and 'file' in record:
                    if not self.valid(record):
                        raise ValueError('原件或派生文件缺失/完整性校验失败；原响应完好时可离线重建Markdown，否则须在重新遇到来源时修复')
                elif all(k in record for k in ('sha256', 'file', 'download_url')):
                    old = record
                    path = Path(old['file'])
                    if not path.is_absolute():
                        path = self.directory / path
                    path = path.resolve()
                    if not path.is_relative_to(self.directory):
                        raise ValueError('旧文件路径越出下载目录，保留原记录')
                    data = path.read_bytes()
                    validate_pdf(data)
                    if hashlib.sha256(data).hexdigest() != old['sha256']:
                        raise ValueError('旧 PDF 哈希不符，保留原记录等待修复')
                    record = dict(schema_version=2, document=unknown_metadata(old.get('detail_url')),
                                  file=dict(path=str(path.relative_to(self.directory)), size_bytes=len(data), sha256=old['sha256'],
                                            media_type='application/pdf', downloaded_at=old.get('downloaded_at')),
                                  sources=[], crawl=dict(last_checked_at=now()), legacy_metadata=old)
                    self.add_source(record, old['download_url'], old.get('detail_url'), old.get('platform'),
                                    old.get('final_url') or old['download_url'], record['document'],
                                    checked=old.get('downloaded_at') or now())
                    backup = self.directory / '.metadata-backup' / sidecar.name
                    backup.parent.mkdir(exist_ok=True)
                    if not backup.exists():
                        atomic_write(backup, sidecar.read_bytes())
                    write_json(sidecar, record)
                else:
                    continue
                # 相同内容已有记录时保留全部来源，不删除遗留物理文件。
                original_sha = record.get('original_file', record['file'])['sha256']
                previous = self.by_hash(original_sha)
                if previous and self.valid(previous) and previous['file']['path'] != record['file']['path']:
                    # 同字节旧路径合并时仍保留显式审核，不用目录元数据覆写正文证据。
                    incoming_review = record.get('body_review', {})
                    for historical in record.get('body_review_history', []):
                        if historical.get('original_sha256') == original_sha:
                            retain_body_review(previous, historical, original_sha)
                    if incoming_review.get('reviewed_at') and (
                            not previous.get('body_review', {}).get('reviewed_at')
                            or incoming_review['reviewed_at'] > previous['body_review']['reviewed_at']):
                        if incoming_review != previous.get('body_review'):
                            retain_body_review(previous, previous.get('body_review'), original_sha)
                        previous['body_review'] = incoming_review
                    elif incoming_review != previous.get('body_review'):
                        retain_body_review(previous, incoming_review, original_sha)
                    for source in record['sources']:
                        self.add_source(previous, source['download_url'], source.get('detail_url'), source.get('platform'),
                                        source['final_url'], source.get('metadata'), source.get('download_checked_at'))
                    record = previous
                    write_json(self._path(record).with_suffix('.json'), record)
                self._index(record)
                for source in record['sources']:
                    checked = source.get('download_checked_at') or record['file']['downloaded_at'] or now()
                    for url in {source['download_url'], source['final_url']}:
                        self._request(url, record.get('original_file', record['file'])['sha256'], checked)
                self.db.commit()
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.db.rollback()
                self.migration_errors.append(dict(file=str(sidecar), error=str(exc)))

    def by_hash(self, sha):
        row = self.db.execute('SELECT record_json FROM objects WHERE sha256=?', (sha,)).fetchone()
        return json.loads(row[0]) if row else None

    def records(self):
        return [json.loads(row[0]) for row in self.db.execute('SELECT record_json FROM objects ORDER BY path')]

    def update_current_selection(self, country):
        """物理历史保留；当前法规清单只引用最新有证据的公布版本。"""
        from regulation_selection import select_latest
        records=[r for r in self.records() if self.valid(r) and r.get('sources')]
        candidates=[]
        eligible=[]
        excluded=[]
        for record in records:
            review = record.get('body_review') or unreviewed_body()
            if review['status'] == 'catalogue_body_type_conflict':
                record['selection'] = dict(selected=False, reason='catalogue_body_type_conflict',
                    latest_unverified=True, selected_id=None)
                self.persist(record)
                excluded.append(dict(id=record.get('original_file',record['file'])['sha256'],
                    file=record['file'],body_review=review,reason='catalogue_body_type_conflict'))
                continue
            preferred=record.get('preferred_source',{}).get('download_url')
            source=next((s for s in record['sources'] if s['download_url']==preferred),record['sources'][0])
            candidates.append(dict(id=record.get('original_file',record['file'])['sha256'],country=country,
                platform=source.get('platform') or '',detail_url=source.get('detail_url') or source['download_url'],
                metadata=record['document'],file=record['file'],body_review=review,
                body_identity_verified=review['status']=='title_type_number_year_match'))
            eligible.append(record)
        select_latest(candidates)
        for record,candidate in zip(eligible,candidates):
            record['selection']=candidate['selection']
            self.persist(record)
        write_json(self.directory/'current-regulations.json',dict(schema_version=1,country=country,
            generated_at=now(),records=candidates,excluded_records=excluded))
        return candidates

    def add_source(self, record, url, detail, platform, final, metadata=None, checked=None):
        timestamp = now()
        key = (platform, detail, url)
        source = next((s for s in record['sources'] if (s.get('platform'), s.get('detail_url'), s['download_url']) == key), None)
        if source is None:
            source = dict(platform=platform, detail_url=detail, download_url=url,
                          canonical_url=canonical_url(url), final_url=final,
                          first_seen_at=timestamp, last_seen_at=timestamp,
                          metadata=metadata or unknown_metadata(detail))
            record['sources'].append(source)
        else:
            source.update(final_url=final, last_seen_at=timestamp)
            if metadata:
                # 只在相同原件、相同来源内续补证据；目录缺失/未解析不能抹掉已核实字段。
                previous_metadata = source.get('metadata') or {}
                merged_metadata = dict(previous_metadata, **metadata)
                for name, previous_field in previous_metadata.items():
                    incoming_field = metadata.get(name)
                    if (isinstance(previous_field, dict) and previous_field.get('status') == 'observed'
                            and (not isinstance(incoming_field, dict)
                                 or incoming_field.get('status') != 'observed')):
                        merged_metadata[name] = previous_field
                source['metadata'] = merged_metadata
        if checked:
            source['download_checked_at'] = checked
        # 同内容的多个来源：有官方公布日期的最新来源提供主元数据。
        from regulation_selection import published_date
        dated = [(published_date(s.get('metadata', {})), s) for s in record['sources']]
        known = [(d, s) for d, s in dated if d]
        preferred = max(known, key=lambda pair: pair[0])[1] if known else record['sources'][0]
        if preferred.get('metadata'):
            record['document'] = dict(preferred['metadata'], legal_status='not_verified')
        record['preferred_source'] = dict(download_url=preferred['download_url'],
            published_at=published_date(preferred.get('metadata', {})),
            reason='latest_published_at' if known else 'publication_unknown',
            latest_unverified=any(not d for d, _ in dated))
        return source

    def upgrade_conversion(self, record, *, persist=True, force=False):
        """从已校验原字节离线升级派生Markdown，保留旧输出，不产生网络请求。"""
        from text_documents import CONVERSION_METHOD, detect_text_document, render_markdown
        if record.get('file', {}).get('media_type') != 'text/markdown' or not record.get('original_file'):
            return False
        conversion = record.get('conversion', {})
        if not force and conversion.get('method') == CONVERSION_METHOD:
            return False
        original = deepcopy(record['original_file'])
        raw = self._path({'file': original}).read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        metadata = conversion.get('metadata') or record.get('document')
        final = conversion.get('source_url')
        detected = detect_text_document(raw, metadata)
        if (sha != original['sha256'] or len(raw) != original['size_bytes']
                or conversion.get('original_sha256') != sha or not detected
                or detected['media_type'] != original['media_type']):
            raise ValueError('转换升级的原始字节/类型/哈希校验失败；保留现状')
        if not isinstance(final, str):
            raise ValueError('转换升级缺少冻结来源 URL')
        canonical_url(final)
        # 先渲染全部输出，失败时不写备份或修改任何文件/索引。
        markdown = render_markdown(raw, metadata, final)
        path = self._path(record)
        old_bytes = path.read_bytes() if path.exists() else None
        if (conversion.get('method') == CONVERSION_METHOD and old_bytes == markdown
                and hashlib.sha256(markdown).hexdigest() == record['file']['sha256']):
            return False
        previous = deepcopy(record)
        observed = hashlib.sha256(old_bytes).hexdigest() if old_bytes is not None else None
        key = observed or 'missing-' + record['file']['sha256']
        history_dir = self.directory / 'conversion-history' / sha
        history_dir.mkdir(parents=True, exist_ok=True)
        backup_md = history_dir / (key + '.md')
        # 同一Markdown字节可能来自不同转换方法；元数据按完整快照寻址。
        snapshot_bytes = (json.dumps(previous, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
        snapshot_sha = hashlib.sha256(snapshot_bytes).hexdigest()
        backup_json = history_dir / (key + '-metadata-' + snapshot_sha + '.json')
        if old_bytes is not None:
            if backup_md.exists() and backup_md.read_bytes() != old_bytes:
                raise ValueError('转换历史备份冲突，保留现状')
            if not backup_md.exists():
                atomic_write(backup_md, old_bytes)
        if backup_json.exists() and backup_json.read_bytes() != snapshot_bytes:
            raise ValueError('转换历史元数据备份冲突，保留现状')
        if not backup_json.exists():
            atomic_write(backup_json, snapshot_bytes)
        historical = dict(method=conversion.get('method'), file=previous['file'],
            observed_sha256=observed, expected_sha256=previous['file']['sha256'],
            original_sha256=sha, integrity='matched' if observed == previous['file']['sha256'] else 'missing_or_modified',
            backup_file=str(backup_md.relative_to(self.directory)) if old_bytes is not None else None,
            backup_metadata=str(backup_json.relative_to(self.directory)),
            backup_metadata_sha256=snapshot_sha, backup_metadata_integrity='matched', upgraded_at=now())
        self._make_markdown(record, raw, detected, sha, metadata, final,
                            original.get('downloaded_at'), markdown=markdown)
        # 离线生成时间与实际网络取得时间分别保留；原响应的元数据不改写。
        record['original_file'] = original
        record.setdefault('conversion_history', []).append(historical)
        record['conversion']['upgraded_from'] = conversion.get('method')
        if persist:
            self.persist(record)
        return True

    def _make_markdown(self, record, data, detected, sha, metadata, final, timestamp, markdown=None):
        from text_documents import CONVERSION_METHOD, render_markdown
        raw_path = self.directory / 'raw' / f"{sha}.{detected['extension']}"
        if markdown is None:
            markdown = render_markdown(data, metadata, final)
        raw_path.parent.mkdir(exist_ok=True)
        if not raw_path.exists() or raw_path.read_bytes() != data:
            atomic_write(raw_path, data)
        record['original_file'] = dict(path=str(raw_path.relative_to(self.directory)),
            sha256=sha, size_bytes=len(data), media_type=detected['media_type'], downloaded_at=timestamp)
        record['file'] = dict(path=f'{sha}.md', sha256=hashlib.sha256(markdown).hexdigest(),
            size_bytes=len(markdown), media_type='text/markdown', downloaded_at=timestamp)
        record['conversion'] = dict(format='markdown', original_sha256=sha,
            method=CONVERSION_METHOD, source_url=final, metadata=deepcopy(metadata), converted_at=now())
        atomic_write(self._path(record), markdown)
        write_json(raw_path.with_name(raw_path.name + '.metadata.json'), dict(
            schema_version=2, document=metadata, file=record['original_file'],
            derived_file=record['file'], source_url=final))

    def save(self, url, detail, platform, fetcher, metadata=None, refresh=False, convert_text=False):
        canonical = canonical_url(url)
        row = self.db.execute('SELECT * FROM requests WHERE canonical_url=?', (canonical,)).fetchone()
        try:
            if row and row['status'] == 'succeeded' and not refresh:
                record = self.by_hash(row['sha256'])
                if record:
                    try:
                        self.upgrade_conversion(record)
                    except (OSError, ValueError):
                        # 原响应损坏时仍沿用有界下载修复，不虚假复用缓存。
                        pass
                if record and self.valid(record):
                    old_source = next((s for s in record['sources'] if s['canonical_url'] == canonical), None)
                    final = old_source['final_url'] if old_source else url
                    self.add_source(record, url, detail, platform, final, metadata, row['checked_at'])
                    if convert_text and record['file']['media_type'] != 'text/markdown':
                        from text_documents import detect_text_document
                        raw = self._path(record).read_bytes()
                        text_format = detect_text_document(raw, record['document'])
                        if text_format:
                            self._make_markdown(record, raw, text_format, row['sha256'], record['document'], final, now())
                    self.persist(record)
                    return 'existing', record
            data, final = fetcher(url)
            canonical_url(final)
            sha = hashlib.sha256(data).hexdigest()
            record = self.by_hash(sha)
            if record:
                try:
                    self.upgrade_conversion(record)
                except (OSError, ValueError):
                    # 本次已取得校验过的新响应，旧raw损坏不应阻止正常下载修复。
                    pass
            conversion_metadata = metadata
            conversion_source = final
            if record and record.get('original_file'):
                convert_text = True
                conversion_metadata = record['conversion']['metadata']
                conversion_source = record['conversion']['source_url']
            text_format = None
            if convert_text:
                from text_documents import detect_text_document
                text_format = detect_text_document(data, conversion_metadata)
            if convert_text and text_format is None and data.lstrip().startswith((b'<', b'[', b'{')) and not data.startswith(b'{\\rtf'):
                raise ValueError('文本响应尚未确认法规正文或缺少明确正文结构；不能将目录或错误页作为原件')
            detected = text_format or identify_document(data)
            timestamp = now()
            status = 'content_duplicate' if record and self.valid(record) else 'downloaded'
            if record is None:
                record = dict(schema_version=2, document=metadata or unknown_metadata(detail),
                              file=dict(path=f"{sha}.{detected['extension']}", size_bytes=len(data), sha256=sha,
                                        media_type=detected['media_type'], downloaded_at=timestamp),
                              sources=[], crawl=dict(last_checked_at=timestamp))
            if status == 'downloaded':
                # 已有内容索引的文件字段也可能损坏；以本次验证的字节修复，保留原路径兼容旧记录。
                record['file'].update(size_bytes=len(data), sha256=sha,
                                      media_type=detected['media_type'], downloaded_at=timestamp)
                if not (convert_text and text_format):
                    atomic_write(self._path(record), data)
            self.add_source(record, url, detail, platform, final, metadata, timestamp)
            if convert_text and text_format:
                self._make_markdown(record, data, detected, sha, conversion_metadata, conversion_source, timestamp)
            self.persist(record)
            self._request(url, sha, timestamp, attempt=True)
            if canonical_url(final) != canonical:
                self._request(final, sha, timestamp)
            self.db.commit()
            return status, record
        except Exception as exc:
            self.db.execute('''INSERT INTO requests VALUES (?,?,'failed',NULL,1,?,?)
                ON CONFLICT(canonical_url) DO UPDATE SET status='failed', attempts=requests.attempts+1,
                checked_at=excluded.checked_at,error=excluded.error''',
                            (canonical, fingerprint(url), now(), str(exc)))
            self.db.commit()
            raise
