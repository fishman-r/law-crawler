"""法规资源白名单、批次精确导出与 HTTP 预览的业务/安全回归。"""
import base64
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import zipfile

from observer.library import CHUNK_BYTES, LibraryError, LibraryReader, file_id
from observer.server import Handler, Observer, Server


PDF = b'%PDF-1.4\nfixture official law\n%%EOF\n'
XML = b'<Statute><Body>Article 1 original response</Body></Statute>'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')


def sha(data):
    return hashlib.sha256(data).hexdigest()


class LibraryFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # macOS /var 是系统符号链接，测试使用真实 /private/var 路径。
        self.root = Path(self.temp.name).resolve()
        self.base = self.root / 'downloads'
        self.base.mkdir()
        self.library = LibraryReader(self.base)

    def tearDown(self):
        self.temp.cleanup()

    def write_json(self, path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

    def register(self, country='Indonesia', data=PDF, extension='pdf', media_type='application/pdf',
                 title='法规甲', name=None):
        name = name or sha(data) + '.' + extension
        path = self.base / country / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        record = dict(schema_version=2,
                      file=dict(path=name, size_bytes=len(data), sha256=sha(data), media_type=media_type),
                      document=dict(title=dict(value=title), published_at=dict(value=None),
                                    legal_identity=dict(value=dict(type='Act', number='17', year='2020'))),
                      sources=[dict(platform='official', detail_url='https://official.gov/law',
                                    download_url='https://official.gov/law.pdf')],
                      body_review=dict(status='not_reviewed'))
        self.write_json(path.with_suffix('.json'), record)
        return path, record

    def converted(self, country='Canada'):
        path, record = self.register(country, data='# Act\n\nArticle 1 中文正文。\n'.encode(),
                                     extension='md', media_type='text/markdown', title='Converted Act')
        original = dict(path='raw/' + sha(XML) + '.xml', size_bytes=len(XML),
                        sha256=sha(XML), media_type='application/xml')
        raw = self.base / country / original['path']
        raw.parent.mkdir()
        raw.write_bytes(XML)
        record['original_file'] = original
        record['conversion'] = dict(original_sha256=original['sha256'], source_url='https://official.gov/law.xml')
        self.write_json(path.with_suffix('.json'), record)
        self.write_json(raw.with_name(raw.name + '.metadata.json'),
                        dict(schema_version=2, document=record['document'], file=original,
                             derived_file=record['file']))
        return path, record, raw

    def batch(self, identifier, resources=(), number='0001', mode='content', finished=True, errors=None):
        attempt = self.base / '.batches' / identifier / 'attempts' / number
        files = [dict(file=str(path), country=path.parent.name, sha256=record['file']['sha256'],
                      metadata_file=str(path.with_suffix('.json')), result='downloaded')
                 for path, record in resources]
        report = dict(batch_id=identifier, mode=mode, files=files, errors=errors or [], result='completed')
        if finished:
            report['finished_at'] = '2026-10-16T01:00:00+00:00'
        self.write_json(attempt / 'report.json', report)
        self.write_json(attempt.parents[1] / 'plan.json', dict(mode=mode))
        return attempt, report

    def identifier(self, path):
        country, *parts = path.relative_to(self.base).parts
        return file_id(country, '/'.join(parts))

    def assert_library_error(self, status, operation):
        with self.assertRaises(LibraryError) as caught:
            operation()
        self.assertEqual(caught.exception.status, status)
        self.assertNotIn(str(self.root), str(caught.exception))
        return caught.exception

    def prepare_bytes(self, identifier):
        with self.library.prepare(identifier) as prepared:
            return prepared.stream.read()

    def archive_bytes(self, identifier):
        with self.library.archive(identifier) as prepared:
            return prepared.stream.read()


class LibraryTests(LibraryFixture):
    def test_library_search_pagination_counts_and_unregistered_files(self):
        self.register(title='中国出口法')
        self.register(data=PDF + b'another', title='Example Act')
        self.register('Canada', title='Canadian Act')
        (self.base / 'Indonesia/unregistered.pdf').write_bytes(PDF)
        (self.base / 'Indonesia/crawl-state.sqlite3').write_bytes(b'private database')
        self.write_json(self.base / 'Indonesia/current-regulations.json', dict(schema_version=1, files=['private']))
        self.write_json(self.base / 'Indonesia/catalog/candidate.json', dict(schema_version=2, document={}))
        self.batch('hidden-batch', mode='metadata')
        response = self.library.listing(country='Indonesia', q='Example', offset=0, limit=1)
        self.assertEqual(response['countries'], [dict(name='Canada', file_count=1), dict(name='Indonesia', file_count=2)])
        self.assertEqual(response['total'], 1)
        self.assertEqual(response['files'][0]['title'], 'Example Act')
        self.assertIsNone(response['files'][0]['published_at'])
        self.assertEqual(response['files'][0]['identity'], dict(type='Act', number='17', year='2020'))
        self.assertEqual(self.library.listing(q='2020', offset=1, limit=1)['total'], 3)
        self.assertEqual(len(self.library.listing(q='2020', offset=1, limit=1)['files']), 1)
        self.assertEqual(self.library.listing(country='Unknown')['total'], 0)
        self.assertEqual(response['warnings'], [])

    def test_missing_library_does_not_create_directory(self):
        absent = self.root / 'not-created'
        self.assertEqual(LibraryReader(absent).listing()['total'], 0)
        self.assertFalse(absent.exists())

    def test_query_limits_are_explicit(self):
        for query in (dict(country='../Canada'), dict(limit=201), dict(limit=0),
                      dict(offset=-1), dict(q='x' * 201), dict(limit=True)):
            self.assert_library_error(400, lambda query=query: self.library.listing(**query))

    def test_batches_do_not_export_other_batch_or_country_inventory(self):
        first = self.register(title='Batch one')
        second = self.register(data=PDF + b'other', title='Batch two')
        third = self.register('Canada', data=PDF + b'canadian', title='Unrelated inventory')
        self.batch('one', [first])
        self.batch('two', [second])
        self.assertEqual(self.library.listing()['total'], 3)
        response = self.library.batch_files('one')
        self.assertEqual(response['file_count'], 1)
        self.assertEqual(response['total_bytes'], len(PDF))
        self.assertEqual(response['files'][0]['title'], 'Batch one')
        with zipfile.ZipFile(io.BytesIO(self.archive_bytes('one'))) as archive:
            expected = {'Indonesia/' + first[0].name, 'Indonesia/' + first[0].with_suffix('.json').name, 'manifest.json'}
            self.assertEqual(set(archive.namelist()), expected)
            self.assertNotIn(second[0].name, '\n'.join(archive.namelist()))
            self.assertNotIn(third[0].name, '\n'.join(archive.namelist()))
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['batch_id'], 'one')
            self.assertEqual(sha(archive.read(manifest['files'][0]['path'])), manifest['files'][0]['sha256'])
            self.assertEqual(archive.read(manifest['files'][0]['metadata']), first[0].with_suffix('.json').read_bytes())
            self.assertTrue(manifest['complete'])
            for name in archive.namelist():
                self.assertFalse(name.startswith('/'))
                self.assertNotIn('..', name.split('/'))

    def test_duplicate_bytes_keep_country_registrations_and_deduplicate_zip_payload(self):
        first = self.register('Indonesia', title='Indonesia registration')
        second = self.register('Canada', title='Canada registration')
        self.batch('shared', [first, first, second])
        response = self.library.batch_files('shared')
        self.assertEqual(response['file_count'], 2)
        with zipfile.ZipFile(io.BytesIO(self.archive_bytes('shared'))) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual({row['country'] for row in manifest['files']}, {'Indonesia', 'Canada'})
            self.assertEqual(len({row['path'] for row in manifest['files']}), 1)
            self.assertEqual(sum(name.endswith('.pdf') for name in archive.namelist()), 1)
            self.assertEqual(sum(name.endswith('.json') for name in archive.namelist()), 3)

    def test_metadata_batch_has_no_original_archive_even_with_existing_country_inventory(self):
        primary = self.register()
        self.batch('metadata-only', mode='metadata')
        response = self.library.batch_files('metadata-only')
        self.assertEqual((response['file_count'], response['total_bytes'], response['files'], response['archive_url']),
                         (0, 0, [], None))
        self.assert_library_error(404, lambda: self.archive_bytes('metadata-only'))
        self.batch('metadata-anomaly', [primary], mode='metadata')
        anomalous = self.library.batch_files('metadata-anomaly')
        self.assertEqual(anomalous['file_count'], 0)
        self.assertTrue(anomalous['warnings'])

    def test_latest_finished_attempt_is_exact_and_running_attempt_blocks_archive(self):
        first, second = self.register(), self.register(data=PDF + b'new')
        self.batch('rerun', [first])
        self.batch('rerun', [second], number='0002')
        self.assertEqual(self.library.batch_files('rerun')['files'][0]['name'], second[0].name)
        self.batch('rerun', [first], number='0003', finished=False)
        response = self.library.batch_files('rerun')
        self.assertEqual(response['files'][0]['name'], second[0].name)
        self.assertIsNone(response['archive_url'])
        self.assertEqual(response['attempt_number'], 2)
        self.assertEqual(response['finished_at'], '2026-10-16T01:00:00+00:00')
        self.assertTrue(any('尚未结束' in value for value in response['warnings']))
        self.assert_library_error(409, lambda: self.archive_bytes('rerun'))
        self.batch('not-finished', [first], finished=False)
        unfinished = self.library.batch_files('not-finished')
        self.assertEqual(unfinished['file_count'], 0)
        self.assertIsNone(unfinished['archive_url'])

    def test_converted_markdown_exports_original_and_both_sidecars(self):
        path, record, raw = self.converted()
        self.batch('converted', [(path, record)])
        listing = self.library.listing(country='Canada')
        self.assertEqual(listing['total'], 1)
        row = listing['files'][0]
        self.assertEqual(row['preview_kind'], 'text')
        self.assertEqual(row['original']['media_type'], 'application/xml')
        self.assertIsNone(row['original']['preview_url'])
        self.assertEqual(self.prepare_bytes(row['original']['id']), XML)
        with zipfile.ZipFile(io.BytesIO(self.archive_bytes('converted'))) as archive:
            self.assertEqual(len(archive.namelist()), 5)
            manifest = json.loads(archive.read('manifest.json'))
            item = manifest['files'][0]
            self.assertEqual(sha(archive.read(item['path'])), record['file']['sha256'])
            self.assertEqual(archive.read(item['original']['path']), XML)
            self.assertEqual(item['original']['sha256'], record['original_file']['sha256'])
            self.assertEqual(archive.read(item['original']['metadata']), raw.with_name(raw.name + '.metadata.json').read_bytes())

    def test_unsafe_latest_attempt_never_silently_falls_back_to_old_archive(self):
        primary = self.register()
        attempt, report = self.batch('unsafe-latest', [primary])
        target = self.root / 'outside-attempt'
        target.mkdir()
        (attempt.parent / '0002').symlink_to(target, target_is_directory=True)
        self.assert_library_error(409, lambda: self.library.batch_files('unsafe-latest'))
        self.assert_library_error(409, lambda: self.archive_bytes('unsafe-latest'))

    def test_changed_metadata_during_archive_is_rejected_including_original_binding(self):
        path, record, raw = self.converted()
        self.batch('change-metadata', [(path, record)])
        original_copy = self.library._copy_verified
        def change_after_primary(spec, destination):
            result = original_copy(spec, destination)
            if spec.parts[-1] == path.name:
                modified = json.loads(path.with_suffix('.json').read_text())
                modified['original_file']['sha256'] = '0' * 64
                self.write_json(path.with_suffix('.json'), modified)
            return result
        with patch.object(self.library, '_copy_verified', side_effect=change_after_primary):
            self.assert_library_error(409, lambda: self.archive_bytes('change-metadata'))

    def test_report_and_sidecar_bindings_reject_different_sha_paths_and_country(self):
        path, record = self.register()
        attempt, report = self.batch('bad-report', [(path, record)])
        for changes in (dict(sha256='0' * 64), dict(country='Canada'),
                        dict(file=str(self.root / path.name)),
                        dict(file=str(self.base / 'Indonesia/../Indonesia' / path.name)),
                        dict(metadata_file=str(self.base / 'Indonesia/other.json'))):
            changed = json.loads(json.dumps(report))
            changed['files'][0].update(changes)
            self.write_json(attempt / 'report.json', changed)
            response = self.library.batch_files('bad-report')
            self.assertEqual(response['file_count'], 0)
            self.assertIsNone(response['archive_url'])
            self.assertTrue(response['warnings'])
            self.assert_library_error(409, lambda: self.archive_bytes('bad-report'))

    def test_missing_or_corrupt_original_is_not_successful_download(self):
        path, record = self.register()
        self.batch('corrupt', [(path, record)])
        path.write_bytes(PDF.replace(b'official', b'tampered'))
        # 同大小的变更必须通过 SHA 拒绝，不依赖大小检查。
        self.assertEqual(path.stat().st_size, record['file']['size_bytes'])
        self.assert_library_error(409, lambda: self.prepare_bytes(self.identifier(path)))
        self.assert_library_error(409, lambda: self.archive_bytes('corrupt'))
        path.unlink()
        response = self.library.batch_files('corrupt')
        self.assertEqual(response['file_count'], 0)
        self.assertTrue(response['warnings'])
        self.assertIsNone(response['archive_url'])

    def test_derived_text_never_hides_corrupt_raw_or_unbound_raw_sidecar(self):
        path, record, raw = self.converted()
        self.batch('bad-raw', [(path, record)])
        raw.write_bytes(XML.replace(b'Article', b'Altered'))
        self.assert_library_error(409, lambda: self.prepare_bytes(self.identifier(path)))
        self.assert_library_error(409, lambda: self.archive_bytes('bad-raw'))
        raw.write_bytes(XML)
        raw_metadata = raw.with_name(raw.name + '.metadata.json')
        metadata = json.loads(raw_metadata.read_text())
        metadata['derived_file']['sha256'] = '0' * 64
        self.write_json(raw_metadata, metadata)
        self.assert_library_error(409, lambda: self.prepare_bytes(self.identifier(raw)))
        self.assertEqual(self.library.listing()['total'], 0)

    def test_encoded_traversal_invalid_ids_and_arbitrary_files_are_rejected(self):
        path, _ = self.register()
        for value in ('Indonesia/../outside.pdf', '/Indonesia/file.pdf',
                      'Indonesia/%2e%2e/outside.pdf', 'Indonesia/raw/../file.pdf',
                      'Indonesia/..\\outside.pdf', '.batches/plan.pdf', 'Indonesia/a//b.pdf'):
            identifier = base64.urlsafe_b64encode(value.encode()).decode().rstrip('=')
            self.assert_library_error(400, lambda identifier=identifier: self.prepare_bytes(identifier))
        for identifier in ('=', 'x', 'A' * 1025, self.identifier(path) + '='):
            self.assert_library_error(400, lambda identifier=identifier: self.prepare_bytes(identifier))
        arbitrary = path.parent / 'unregistered.pdf'
        arbitrary.write_bytes(PDF)
        self.assert_library_error(404, lambda: self.prepare_bytes(self.identifier(arbitrary)))
        self.assert_library_error(404, lambda: self.prepare_bytes(file_id('Indonesia', 'crawl-state.sqlite3')))

    def test_sidecar_cannot_register_configuration_or_escape_country_directory(self):
        path, record = self.register()
        for invalid in ('../private.pdf', '/etc/passwd', 'raw/original.pdf', 'crawl-state.sqlite3', 'settings.json'):
            changed = json.loads(json.dumps(record))
            changed['file']['path'] = invalid
            self.write_json(path.with_suffix('.json'), changed)
            self.assert_library_error(409, lambda: self.prepare_bytes(self.identifier(path)))

    def test_file_sidecar_directory_symlinks_and_hardlinks_are_rejected(self):
        path, record = self.register()
        outside = self.root / 'outside.pdf'
        outside.write_bytes(PDF)
        identifier = self.identifier(path)
        path.unlink()
        path.symlink_to(outside)
        self.assert_library_error(409, lambda: self.prepare_bytes(identifier))
        path.unlink()
        path.hardlink_to(outside)
        self.assert_library_error(409, lambda: self.prepare_bytes(identifier))
        path.unlink()
        path.write_bytes(PDF)
        metadata = path.with_suffix('.json')
        saved_metadata = metadata.read_bytes()
        external_metadata = self.root / 'metadata.json'
        external_metadata.write_bytes(saved_metadata)
        metadata.unlink()
        metadata.symlink_to(external_metadata)
        self.assert_library_error(409, lambda: self.prepare_bytes(identifier))
        metadata.unlink()
        metadata.hardlink_to(external_metadata)
        self.assert_library_error(409, lambda: self.prepare_bytes(identifier))
        metadata.unlink()
        metadata.write_bytes(saved_metadata)
        country = path.parent
        moved = self.root / 'country'
        country.rename(moved)
        country.symlink_to(moved, target_is_directory=True)
        self.assert_library_error(409, lambda: self.prepare_bytes(identifier))
        self.assertEqual(self.library.listing()['total'], 0)

    def test_raw_directory_and_download_root_symlinks_are_rejected(self):
        path, record, raw = self.converted()
        moved = self.root / 'raw'
        raw.parent.rename(moved)
        raw.parent.symlink_to(moved, target_is_directory=True)
        self.assert_library_error(409, lambda: self.prepare_bytes(self.identifier(path)))
        linked_root = self.root / 'linked-downloads'
        linked_root.symlink_to(self.base, target_is_directory=True)
        self.assert_library_error(409, lambda: LibraryReader(linked_root).listing())

    def test_raw_file_and_raw_metadata_links_are_rejected(self):
        path, record, raw = self.converted()
        external = self.root / 'original.xml'
        external.write_bytes(XML)
        raw.unlink()
        raw.symlink_to(external)
        self.assert_library_error(409, lambda: self.prepare_bytes(self.identifier(raw)))
        raw.unlink()
        raw.hardlink_to(external)
        self.assert_library_error(409, lambda: self.prepare_bytes(self.identifier(path)))
        raw.unlink()
        raw.write_bytes(XML)
        metadata = raw.with_name(raw.name + '.metadata.json')
        external_metadata = self.root / 'original-metadata.json'
        external_metadata.write_bytes(metadata.read_bytes())
        metadata.unlink()
        metadata.symlink_to(external_metadata)
        self.assert_library_error(409, lambda: self.prepare_bytes(self.identifier(path)))
        metadata.unlink()
        metadata.hardlink_to(external_metadata)
        self.assert_library_error(409, lambda: self.prepare_bytes(self.identifier(path)))

    def test_file_replaced_by_symlink_immediately_before_open_is_rejected(self):
        path, record = self.register()
        external = self.root / 'secret.pdf'
        external.write_bytes(PDF)
        actual_open = os.open
        def replace_on_open(value, flags, *args, **kwargs):
            if value == path.name:
                path.unlink(missing_ok=True)
                path.symlink_to(external)
            return actual_open(value, flags, *args, **kwargs)
        with patch('observer.library.os.open', side_effect=replace_on_open):
            self.assert_library_error(409, lambda: self.prepare_bytes(self.identifier(path)))

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO only')
    def test_fifo_does_not_block_and_nonregular_file_is_rejected(self):
        path, _ = self.register()
        path.unlink()
        os.mkfifo(path)
        started = time.monotonic()
        self.assert_library_error(409, lambda: self.prepare_bytes(self.identifier(path)))
        self.assertLess(time.monotonic() - started, 1)

    def test_fake_pdf_is_rejected_and_active_content_has_no_preview(self):
        fake, _ = self.register(data=b'<html><script>alert(1)</script></html>')
        self.assert_library_error(409, lambda: self.prepare_bytes(self.identifier(fake)))
        for extension, media in (('html', 'text/html'), ('svg', 'image/svg+xml'),
                                 ('js', 'application/javascript'), ('docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')):
            path, _ = self.register(data=b'potential active content', extension=extension, media_type=media)
            with self.library.prepare(self.identifier(path)) as prepared:
                self.assertIsNone(prepared.preview_kind)
            row = self.library.row(self.library.resource(self.identifier(path)))
            self.assertIsNone(row['preview_url'])

    def test_verified_snapshot_is_unchanged_when_source_is_replaced_after_preparation(self):
        path, _ = self.register()
        with self.library.prepare(self.identifier(path)) as prepared:
            path.write_bytes(PDF.replace(b'official', b'tampered'))
            self.assertEqual(prepared.stream.read(), PDF)
        self.assert_library_error(409, lambda: self.prepare_bytes(self.identifier(path)))

    def test_archive_and_download_temps_close_on_disconnect_and_gate_is_released(self):
        path, record = self.register()
        self.batch('cleanup', [(path, record)])
        created = []
        real_tempfile = tempfile.TemporaryFile
        def tracking_temp(*args, **kwargs):
            stream = real_tempfile(*args, **kwargs)
            created.append(stream)
            return stream
        with patch('observer.library.tempfile.TemporaryFile', side_effect=tracking_temp):
            with self.assertRaises(BrokenPipeError):
                with self.library.prepare(self.identifier(path)):
                    raise BrokenPipeError()
            self.assertTrue(created[-1].closed)
            with self.assertRaises(ConnectionResetError):
                with self.library.archive('cleanup'):
                    self.assert_library_error(503, lambda: self.archive_bytes('cleanup'))
                    raise ConnectionResetError()
            self.assertTrue(all(stream.closed for stream in created))
            self.assertTrue(self.archive_bytes('cleanup').startswith(b'PK'))
        self.assertTrue(all(stream.closed for stream in created))

    def test_failed_integrity_check_closes_temp_and_releases_archive_gate(self):
        path, record = self.register()
        self.batch('recover', [(path, record)])
        path.write_bytes(PDF.replace(b'official', b'tampered'))
        real_tempfile, streams = tempfile.TemporaryFile, []
        def tracked(*args, **kwargs):
            result = real_tempfile(*args, **kwargs)
            streams.append(result)
            return result
        with patch('observer.library.tempfile.TemporaryFile', side_effect=tracked):
            self.assert_library_error(409, lambda: self.archive_bytes('recover'))
            self.assert_library_error(409, lambda: self.prepare_bytes(self.identifier(path)))
            self.assertTrue(all(stream.closed for stream in streams))
            path.write_bytes(PDF)
            self.assertTrue(self.archive_bytes('recover'))

    def test_file_and_total_archive_limits_are_explicit(self):
        path, record = self.register()
        self.batch('limited', [(path, record)])
        with patch('observer.library.MAX_ARCHIVE_FILES', 2):
            error = self.assert_library_error(413, lambda: self.archive_bytes('limited'))
            self.assertEqual(error.code, 'file_limit')
        with patch('observer.library.MAX_ARCHIVE_BYTES', 100):
            self.assert_library_error(413, lambda: self.archive_bytes('limited'))
        self.assertTrue(self.archive_bytes('limited'))

    def test_large_original_is_copied_in_chunks(self):
        data = b'%PDF-1.7\n' + b'x' * (CHUNK_BYTES * 3) + b'\n%%EOF'
        path, record = self.register(data=data)
        resource = self.library.resource(self.identifier(path))
        class Sink:
            def __init__(self):
                self.lengths = []
            def write(self, chunk):
                self.lengths.append(len(chunk))
        sink = Sink()
        self.library._copy_verified(resource.file, sink)
        self.assertGreater(len(sink.lengths), 2)
        self.assertLessEqual(max(sink.lengths), CHUNK_BYTES)
        self.assertEqual(sum(sink.lengths), len(data))

    def test_manifest_preserves_failure_signal_without_private_report_data(self):
        primary = self.register()
        self.batch('partial', [primary], errors=[dict(error=str(self.root) + '/private token=secret-value')])
        with zipfile.ZipFile(io.BytesIO(self.archive_bytes('partial'))) as archive:
            raw = archive.read('manifest.json')
            manifest = json.loads(raw)
            self.assertFalse(manifest['complete'])
            self.assertTrue(manifest['failures'])
            self.assertTrue(manifest['warnings'])
            self.assertNotIn(str(self.root).encode(), raw)
            self.assertNotIn(b'secret-value', raw)


class LibraryRelocationTests(LibraryFixture):
    """只搬动临时夹具，验证历史证据不改写及迁移兼容的安全边界。"""

    def setUp(self):
        super().setUp()
        primary = self.register()
        converted = self.converted()
        attempt, self.report = self.batch('historical', [primary, converted[:2]])
        self.old_base = self.base
        self.report_relative = (attempt / 'report.json').relative_to(self.base)
        self.plan_relative = attempt.parents[1].relative_to(self.base) / 'plan.json'
        self.saved_report = (self.base / self.report_relative).read_bytes()
        self.saved_plan = (self.base / self.plan_relative).read_bytes()
        self.saved_metadata = primary[0].with_suffix('.json').read_bytes()
        destination = self.root / 'data' / 'downloads'
        destination.parent.mkdir()
        self.base.rename(destination)
        self.base = destination
        self.library = LibraryReader(destination)
        self.primary = (destination / primary[0].relative_to(self.old_base), primary[1])
        self.converted_record = (destination / converted[0].relative_to(self.old_base),
                                 converted[1], destination / converted[2].relative_to(self.old_base))
        self.relocation = dict(
            schema_version=1, old_download_base=str(self.old_base), download_base=str(self.base),
            roots=[dict(kind=kind, before=str(self.old_base.parent / kind),
                        after=str(self.base.parent / kind)) for kind in ('downloads', 'reports', 'tmp')],
            batch_plans={'historical': sha(self.saved_plan)},
            created_at='2026-10-19T01:00:00+00:00',
        )
        self.manifest_path = self.base / '.relocation.json'
        self.write_json(self.manifest_path, self.relocation)

    def assert_historical_refused(self):
        response = self.library.batch_files('historical')
        self.assertIsNone(response['archive_url'])
        self.assertTrue(response['warnings'])
        self.assert_library_error(409, lambda: self.archive_bytes('historical'))

    def test_moved_library_browses_and_historical_zip_preserves_original_evidence(self):
        self.assertFalse(self.old_base.exists())
        listing = self.library.listing()
        self.assertEqual(listing['total'], 2)
        self.assertEqual(self.prepare_bytes(self.identifier(self.primary[0])), PDF)
        metadata, _ = self.library.metadata(self.identifier(self.primary[0]))
        self.assertEqual(metadata, self.saved_metadata)
        with patch.object(self.library, '_relocation_root', wraps=self.library._relocation_root) as read_relocation:
            response = self.library.batch_files('historical')
            self.assertEqual(read_relocation.call_count, 1)
        self.assertEqual(response['file_count'], 2)
        self.assertTrue(response['archive_url'])
        with zipfile.ZipFile(io.BytesIO(self.archive_bytes('historical'))) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(len(manifest['files']), 2)
            self.assertEqual(archive.read('Indonesia/' + self.primary[0].name), PDF)
            self.assertEqual(archive.read('Canada/raw/' + self.converted_record[2].name), XML)
            self.assertTrue(manifest['complete'])
            self.assertNotIn(str(self.old_base).encode(), archive.read('manifest.json'))
        self.assertEqual((self.base / self.report_relative).read_bytes(), self.saved_report)
        self.assertEqual((self.base / self.plan_relative).read_bytes(), self.saved_plan)
        self.assertEqual(self.primary[0].with_suffix('.json').read_bytes(), self.saved_metadata)
        self.assertFalse(self.old_base.exists())

    def test_relative_file_with_legacy_absolute_metadata_path_is_supported(self):
        report = json.loads(self.saved_report)
        for item in report['files']:
            item['file'] = str(Path(item['file']).relative_to(self.old_base))
        self.write_json(self.base / self.report_relative, report)
        before = (self.base / self.report_relative).read_bytes()
        self.assertEqual(self.library.batch_files('historical')['file_count'], 2)
        self.assertTrue(self.archive_bytes('historical').startswith(b'PK'))
        self.assertEqual((self.base / self.report_relative).read_bytes(), before)

    def test_missing_or_invalid_manifest_does_not_affect_current_library_or_new_batches(self):
        self.manifest_path.unlink()
        self.assert_historical_refused()
        self.assertEqual(self.library.listing()['total'], 2)
        self.batch('current', [self.primary])
        self.assertTrue(self.archive_bytes('current').startswith(b'PK'))
        self.manifest_path.write_text('{invalid JSON', encoding='utf-8')
        self.assert_historical_refused()
        self.assertEqual(self.library.listing()['total'], 2)
        self.assertTrue(self.archive_bytes('current').startswith(b'PK'))

    def test_relocation_schema_roots_and_timestamp_are_strict(self):
        changes = [
            dict(schema_version=True), dict(schema_version=2), dict(extra='unexpected'),
            dict(download_base=str(self.root / 'elsewhere' / 'downloads')),
            dict(old_download_base=str(self.root / 'downloads' / '..' / 'downloads')),
            dict(roots=[]), dict(roots=self.relocation['roots'][1:]),
            dict(roots=self.relocation['roots'][:1] * 2),
            dict(roots=[dict(kind='downloads', before=str(self.old_base), after=str(self.root / 'outside'))]),
            dict(roots=[dict(kind='downloads', before=str(self.root / 'other'), after=str(self.base))]),
            dict(roots=[dict(kind='other', before=str(self.old_base), after=str(self.base))]),
            dict(batch_plans=[]), dict(batch_plans={'historical': '0' * 63}),
            dict(batch_plans={'../escape': sha(self.saved_plan)}),
            dict(created_at='2026-10-19T01:00:00'), dict(created_at=123),
        ]
        for change in changes:
            with self.subTest(change=change):
                self.write_json(self.manifest_path, dict(self.relocation, **change))
                self.assert_historical_refused()
        self.write_json(self.manifest_path, self.relocation)
        self.assertTrue(self.archive_bytes('historical'))

    def test_original_batch_plan_sha_and_safe_file_are_required(self):
        plan = self.base / self.plan_relative
        self.write_json(self.manifest_path, dict(self.relocation, batch_plans={}))
        self.assert_historical_refused()
        self.write_json(self.manifest_path, self.relocation)
        plan.write_bytes(self.saved_plan + b'\n')
        self.assert_historical_refused()
        plan.unlink()
        self.assert_historical_refused()
        outside = self.root / 'external-plan.json'
        outside.write_bytes(self.saved_plan)
        for link in ('symbolic', 'hard'):
            with self.subTest(link=link):
                if link == 'symbolic':
                    plan.symlink_to(outside)
                else:
                    plan.hardlink_to(outside)
                self.assert_historical_refused()
                plan.unlink()
        plan.write_bytes(self.saved_plan)
        self.assertTrue(self.archive_bytes('historical'))

    def test_legacy_mapping_never_uses_other_roots_or_normalizes_traversal(self):
        filename = self.primary[0].name
        paths = [
            str(self.old_base.parent / kind / 'Indonesia' / filename) for kind in ('reports', 'tmp', 'downloads-other')
        ] + [
            str(self.old_base) + '/Indonesia/../Indonesia/' + filename,
            str(self.old_base) + '/Indonesia/%2e%2e/' + filename,
            str(self.old_base) + '//Indonesia/' + filename,
            str(self.root / 'unrelated' / 'Indonesia' / filename),
        ]
        for path in paths:
            with self.subTest(path=path):
                report = json.loads(self.saved_report)
                report['files'][0]['file'] = path
                self.write_json(self.base / self.report_relative, report)
                self.assert_historical_refused()
        report = json.loads(self.saved_report)
        report['files'][0]['metadata_file'] = str(self.old_base / 'Indonesia' / 'other.json')
        self.write_json(self.base / self.report_relative, report)
        self.assert_historical_refused()

    def test_relocated_batch_keeps_sha_sidecar_and_original_integrity_checks(self):
        report = json.loads(self.saved_report)
        report['files'][0]['sha256'] = '0' * 64
        self.write_json(self.base / self.report_relative, report)
        self.assert_historical_refused()
        (self.base / self.report_relative).write_bytes(self.saved_report)
        self.primary[0].write_bytes(PDF.replace(b'official', b'tampered'))
        self.assert_library_error(409, lambda: self.archive_bytes('historical'))
        self.primary[0].write_bytes(PDF)
        self.converted_record[2].write_bytes(XML.replace(b'Article', b'Altered'))
        self.assert_library_error(409, lambda: self.archive_bytes('historical'))
        self.converted_record[2].write_bytes(XML)
        record = dict(self.primary[1], file=dict(self.primary[1]['file'], path='../outside.pdf'))
        self.write_json(self.primary[0].with_suffix('.json'), record)
        self.assert_historical_refused()

    def test_manifest_links_size_limit_and_duplicate_keys_are_rejected(self):
        outside = self.root / 'external-relocation.json'
        outside.write_bytes(self.manifest_path.read_bytes())
        self.manifest_path.unlink()
        for link in ('symbolic', 'hard'):
            with self.subTest(link=link):
                if link == 'symbolic':
                    self.manifest_path.symlink_to(outside)
                else:
                    self.manifest_path.hardlink_to(outside)
                self.assert_historical_refused()
                self.manifest_path.unlink()
        self.write_json(self.manifest_path, self.relocation)
        with patch('observer.library.MAX_RELOCATION_BYTES', 16):
            self.assert_historical_refused()
        raw = self.manifest_path.read_text()
        self.manifest_path.write_text(raw.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'))
        self.assert_historical_refused()


class LibraryHTTPTests(LibraryFixture):
    def setUp(self):
        super().setUp()
        self.primary = self.register()
        self.converted_record = self.converted()
        self.batch('http-batch', [self.primary])
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        app = Observer(self.workspace, self.base, self.root / 'runtime', self.root / 'settings.json')
        self.server = Server(('127.0.0.1', 0), app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.prefix = '/api/library/files/' + self.identifier(self.primary[0])

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        super().tearDown()

    def request(self, path, method='GET', headers=None):
        request = urllib.request.Request(self.url + path, method=method, headers=headers or {})
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def test_http_contract_and_queries(self):
        status, headers, raw = self.request('/api/library?country=Indonesia&q=Act&offset=0&limit=50')
        response = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(set(response), {'countries', 'country', 'total', 'offset', 'limit', 'files', 'warnings'})
        expected = {'id', 'country', 'name', 'path', 'title', 'size_bytes', 'modified_at', 'media_type',
                    'identity', 'published_at', 'body_review', 'preview_kind', 'preview_url', 'download_url', 'metadata_url'}
        self.assertEqual(set(response['files'][0]), expected)
        for query in ('offset=-1', 'limit=9999999', 'limit=0', 'limit=3&limit=2', 'path=/etc/passwd',
                      'country=..%2f..', 'q=' + 'a' * 201):
            self.assertEqual(self.request('/api/library?' + query)[0], 400)

    def test_download_and_metadata_are_attachments_with_exact_bytes(self):
        status, headers, body = self.request(self.prefix + '/download')
        self.assertEqual((status, body), (200, PDF))
        self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(headers['Content-Type'], 'application/octet-stream')
        status, headers, raw = self.request(self.prefix + '/metadata')
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(raw, self.primary[0].with_suffix('.json').read_bytes())

    def test_pdf_preview_range_head_and_frame_policy(self):
        status, headers, body = self.request(self.prefix + '/preview')
        self.assertEqual((status, body), (200, PDF))
        self.assertEqual(headers['Content-Type'], 'application/pdf')
        self.assertIn("frame-ancestors 'self'", headers['Content-Security-Policy'])
        self.assertIn("object-src 'self'", headers['Content-Security-Policy'])
        self.assertTrue(headers['Content-Disposition'].startswith('inline;'))
        for value, expected in (('bytes=0-3', PDF[:4]), ('bytes=-5', PDF[-5:]),
                                ('bytes=5-', PDF[5:]), ('bytes=0-999', PDF)):
            status, headers, body = self.request(self.prefix + '/preview', headers={'Range': value})
            self.assertEqual((status, body), (206, expected))
            self.assertEqual(int(headers['Content-Length']), len(expected))
            self.assertIn('Content-Range', headers)
        status, headers, body = self.request(self.prefix + '/preview', method='HEAD')
        self.assertEqual((status, body, int(headers['Content-Length'])), (200, b'', len(PDF)))
        status, headers, body = self.request(self.prefix + '/preview', method='HEAD', headers={'Range': 'bytes=1-4'})
        self.assertEqual((status, body, int(headers['Content-Length'])), (206, b'', 4))

    def test_invalid_multiple_and_oversized_ranges_are_416(self):
        for value in ('bytes=0-1,3-5', 'bytes=5-1', 'bytes=999-', 'bytes=-0', 'bytes=-',
                      'items=0-1', 'bytes=+1-2', 'bytes=0-99999999999999999999',
                      'bytes=0-' + '9' * 10000):
            status, headers, raw = self.request(self.prefix + '/preview', headers={'Range': value})
            self.assertEqual(status, 416)
            self.assertEqual(headers['Content-Range'], 'bytes */' + str(len(PDF)))
            self.assertNotIn(str(self.root).encode(), raw)
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        connection.putrequest('GET', self.prefix + '/preview')
        connection.putheader('Range', 'bytes=0-2')
        connection.putheader('Range', 'bytes=3-5')
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 416)
        response.read()
        connection.close()

    def test_text_preview_is_json_and_original_xml_download_is_separate(self):
        path, record, raw = self.converted_record
        prefix = '/api/library/files/' + self.identifier(path)
        status, headers, body = self.request(prefix + '/preview')
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Type'].startswith('application/json'))
        preview = json.loads(body)
        self.assertIn('中文正文', preview['text'])
        self.assertFalse(preview['truncated'])
        raw_prefix = '/api/library/files/' + self.identifier(raw)
        self.assertEqual(self.request(raw_prefix + '/preview')[0], 415)
        status, headers, body = self.request(raw_prefix + '/download')
        self.assertEqual((status, body), (200, XML))
        self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(self.request(raw_prefix + '/metadata')[0], 200)

    def test_image_inline_and_active_html_preview_rejection(self):
        image, _ = self.register(data=PNG, extension='png', media_type='image/png')
        status, headers, body = self.request('/api/library/files/' + self.identifier(image) + '/preview')
        self.assertEqual((status, body, headers['Content-Type']), (200, PNG, 'image/png'))
        html, _ = self.register(data=b'<script>alert(1)</script>', extension='html', media_type='text/html')
        prefix = '/api/library/files/' + self.identifier(html)
        self.assertEqual(self.request(prefix + '/preview')[0], 415)
        status, headers, body = self.request(prefix + '/download')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'application/octet-stream')
        self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))

    def test_archive_and_new_routes_keep_host_origin_and_read_only_guards(self):
        status, headers, body = self.request('/api/batches/http-batch/archive.zip')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'application/zip')
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            self.assertIn('manifest.json', archive.namelist())
        for path in ('/api/library', self.prefix + '/download', '/api/batches/http-batch/archive.zip'):
            for headers in ({'Host': 'attacker.example'}, {'Origin': 'https://attacker.example'}):
                self.assertEqual(self.request(path, headers=headers)[0], 403)
            self.assertEqual(self.request(path, method='POST')[0], 405)
        for path in ('/api/library/files/%2e%2e/download', '/api/batches/http-batch%2f..%2fsecret/files',
                     '/api/library/files/' + self.identifier(self.primary[0]) + '%2f..%2fdownload'):
            self.assertEqual(self.request(path)[0], 404)
        self.assertIn("frame-src 'self'", self.request('/')[1]['Content-Security-Policy'])
        self.assertIn("frame-ancestors 'none'", self.request('/')[1]['Content-Security-Policy'])

    def test_http_integrity_error_happens_before_success_headers(self):
        self.primary[0].write_bytes(PDF.replace(b'official', b'tampered'))
        for path in (self.prefix + '/download', self.prefix + '/preview', '/api/batches/http-batch/archive.zip'):
            status, headers, body = self.request(path)
            self.assertEqual(status, 409)
            self.assertTrue(headers['Content-Type'].startswith('application/json'))
            self.assertNotIn('Content-Disposition', headers)
            self.assertNotIn(str(self.root).encode(), body)

    def test_http_client_disconnect_closes_prepared_archive(self):
        streams = []
        def disconnect(handler, prepared, *args, **kwargs):
            streams.append(prepared.stream)
            raise BrokenPipeError()
        with patch.object(Handler, '_stream', disconnect):
            connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
            connection.request('GET', '/api/batches/http-batch/archive.zip')
            with self.assertRaises(http.client.RemoteDisconnected):
                connection.getresponse()
            connection.close()
        self.assertEqual(len(streams), 1)
        self.assertTrue(streams[0].closed)
        self.assertEqual(self.request('/api/batches/http-batch/archive.zip')[0], 200)


if __name__ == '__main__':
    unittest.main()
