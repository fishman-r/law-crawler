"""当页原始归档导出的业务和安全回归；由服务器执行。"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from observer.archive_export import ArchiveExporter, page_token
from observer.library import LibraryError


class ArchiveExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve() / 'downloads'
        self.base.mkdir()
        self.exporter = ArchiveExporter(self.base)

    def tearDown(self):
        self.temp.cleanup()

    def spec(self, data=b'original regulation', name='law.pdf'):
        sha = hashlib.sha256(data).hexdigest()
        parts = ['Indonesia', 'pipeline', 'raw', 'objects', sha + '.pdf']
        path = self.base.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return dict(parts=parts, sha256=sha, size_bytes=len(data), name=name,
                    media_type='application/pdf')

    def selection(self, specs):
        items = [dict(id='law-1', title='测试法规', identity_status='pending_verification',
                      files=[dict(id=spec['sha256'], role='pending_verification') for spec in specs],
                      sources=[dict(platform='official', detail_url='https://official.go.id/law')])]
        selection = dict(snapshot_id='snapshot-1', query=dict(q='', page=1, page_size=20,
                         classification='', sort='title_asc'), items=items,
                         total=1, page=1, page_size=20, _files=specs)
        selection['page_token'] = page_token(selection)
        return selection

    def test_page_zip_contains_original_and_public_manifest(self):
        spec = self.spec()
        selected = self.selection([spec])
        with self.exporter.archive(selected, expected_token=selected['page_token']) as prepared:
            with zipfile.ZipFile(prepared.stream) as archive:
                self.assertEqual(len(archive.namelist()), 2)
                manifest = json.loads(archive.read('manifest.json'))
                self.assertEqual(manifest['regulations'][0]['id'], 'law-1')
                self.assertEqual(archive.read(manifest['files'][0]['path']), b'original regulation')
                self.assertNotIn(str(self.base), json.dumps(manifest))
                self.assertNotIn('_files', manifest)
                self.assertEqual(manifest['regulations'][0]['identity_status'], 'pending_verification')

    def test_same_sha_is_exported_once(self):
        first = self.spec()
        second = dict(first)
        with self.exporter.archive(self.selection([first, second])) as prepared:
            with zipfile.ZipFile(prepared.stream) as archive:
                self.assertEqual(len(archive.namelist()), 2)

    def test_stale_page_or_query_is_rejected(self):
        selected = self.selection([self.spec()])
        old = selected['page_token']
        selected['query']['sort'] = 'newest'
        with self.assertRaises(LibraryError) as caught:
            with self.exporter.archive(selected, old):
                pass
        self.assertEqual(caught.exception.code, 'stale_page')

    def test_wrong_expected_token_is_rejected(self):
        with self.assertRaises(LibraryError) as caught:
            with self.exporter.archive(self.selection([self.spec()]), '0' * 64):
                pass
        self.assertEqual(caught.exception.status, 409)

    def test_injected_file_not_on_current_page_is_rejected(self):
        selected = self.selection([self.spec()])
        selected['_files'].append(self.spec(b'other law'))
        with self.assertRaises(LibraryError) as caught:
            with self.exporter.archive(selected):
                pass
        self.assertEqual(caught.exception.code, 'invalid_selection')

    def test_tampered_bytes_rejects_entire_zip(self):
        spec = self.spec()
        self.base.joinpath(*spec['parts']).write_bytes(b'x' * spec['size_bytes'])
        with self.assertRaises(LibraryError) as caught:
            with self.exporter.archive(self.selection([spec])):
                pass
        self.assertEqual(caught.exception.code, 'integrity_mismatch')

    def test_digest_filename_mismatch_is_rejected(self):
        spec = self.spec()
        spec['parts'][-1] = 'other.pdf'
        with self.assertRaises(LibraryError) as caught:
            with self.exporter.prepare_file(spec):
                pass
        self.assertEqual(caught.exception.code, 'invalid_metadata')

    def test_symlink_and_hardlink_are_rejected(self):
        for kind in ('symlink', 'hardlink'):
            with self.subTest(kind=kind):
                spec = self.spec()
                path = self.base.joinpath(*spec['parts'])
                outside = self.base.parent / (kind + '.pdf')
                path.rename(outside)
                if kind == 'symlink':
                    path.symlink_to(outside)
                else:
                    import os
                    os.link(outside, path)
                with self.assertRaises(LibraryError) as caught:
                    with self.exporter.prepare_file(spec):
                        pass
                self.assertEqual(caught.exception.code, 'unsafe_file')
                path.unlink()

    def test_traversal_and_nested_separator_are_rejected(self):
        for segment in ('..', 'x/../objects', 'x\\objects'):
            spec = self.spec()
            spec['parts'][2] = segment
            with self.assertRaises(LibraryError):
                with self.exporter.prepare_file(spec):
                    pass

    def test_export_size_limit(self):
        with patch('observer.archive_export.MAX_ARCHIVE_BYTES', 10):
            with self.assertRaises(LibraryError) as caught:
                with self.exporter.archive(self.selection([self.spec()])):
                    pass
            self.assertEqual(caught.exception.status, 413)

    def test_export_file_count_limit(self):
        with patch('observer.archive_export.MAX_ARCHIVE_FILES', 1):
            with self.assertRaises(LibraryError) as caught:
                with self.exporter.archive(self.selection([self.spec()])):
                    pass
            self.assertEqual(caught.exception.code, 'file_limit')

    def test_zip_name_never_contains_user_path(self):
        spec = self.spec(name='../../unsafe.pdf')
        with self.exporter.archive(self.selection([spec])) as prepared:
            with zipfile.ZipFile(prepared.stream) as archive:
                self.assertTrue(all('..' not in member for member in archive.namelist()))

    def test_empty_page_no_zip(self):
        with self.assertRaises(LibraryError) as caught:
            with self.exporter.archive(self.selection([])):
                pass
        self.assertEqual(caught.exception.code, 'empty_page')

    def test_non_archive_indonesia_file_is_rejected(self):
        spec = self.spec()
        spec['parts'] = ['Indonesia', 'private', 'config', 'objects', spec['parts'][-1]]
        with self.assertRaises(LibraryError):
            with self.exporter.prepare_file(spec):
                pass

    def test_one_record_page_is_exportable(self):
        selected = self.selection([self.spec()])
        selected['page_size'] = selected['query']['page_size'] = 1
        selected['page_token'] = page_token(selected)
        with self.exporter.archive(selected) as prepared:
            self.assertGreater(prepared.size, 0)

    def test_mislabeled_pdf_does_not_allow_inline_preview(self):
        spec = self.spec(b'<html>official error response</html>')
        with self.exporter.prepare_file(spec) as prepared:
            self.assertIsNone(prepared.preview_kind)
            self.assertEqual(prepared.stream.read(), b'<html>official error response</html>')

    def test_single_active_content_remains_attachment_only(self):
        spec = self.spec(b'<script>alert(1)</script>', name='law.html')
        spec['media_type'] = 'text/html'
        with self.exporter.prepare_file(spec) as prepared:
            self.assertEqual(prepared.preview_kind, 'text')
            self.assertEqual(prepared.stream.read(), b'<script>alert(1)</script>')


if __name__ == '__main__':
    unittest.main()
