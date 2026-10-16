"""真实格式与跨格式去重的离线回归，不依赖文件名和 HTTP MIME。"""
import binascii
import io
import struct
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest.mock import Mock, patch
from crawl_store import CrawlStore, validate_pdf
from document_formats import identify_document

PDF = b'%PDF-1.4\nfixture\n%%EOF\n'
RTF = b'{\\rtf1\\ansi Official regulation \\{quoted\\}}'


def archive(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, content in entries.items():
            z.writestr(name, content)
    return buffer.getvalue()


def office(ext, custom_main=None, relationship_target=None):
    main, content, namespace, root = {
        'docx': ('word/document.xml', 'wordprocessingml.document', 'wordprocessingml', 'document'),
        'xlsx': ('xl/workbook.xml', 'spreadsheetml.sheet', 'spreadsheetml', 'workbook'),
        'pptx': ('ppt/presentation.xml', 'presentationml.presentation', 'presentationml', 'presentation'),
    }[ext]
    main = custom_main or main
    target = relationship_target if relationship_target is not None else main
    relationship = f'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="{target}"/>' if target else ''
    return archive({'[Content_Types].xml': f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/{main}" ContentType="application/vnd.openxmlformats-officedocument.{content}.main+xml"/></Types>', '_rels/.rels': f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{relationship}</Relationships>', main: f'<{root} xmlns="http://schemas.openxmlformats.org/{namespace}/2006/main"/>'})


def png(body=zlib.compress(b'\x00\x00\x00\x00')):
    def chunk(kind, raw):
        return struct.pack('>I', len(raw)) + kind + raw + struct.pack('>I', binascii.crc32(kind + raw) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', body) + chunk(b'IEND', b'')


class FormatTests(unittest.TestCase):
    def test_office_and_odf_are_not_plain_zip(self):
        for ext in ('docx', 'xlsx', 'pptx'):
            self.assertEqual(identify_document(office(ext))['extension'], ext)
        for ext, kind in (('odt', 'text'), ('ods', 'spreadsheet'), ('odp', 'presentation')):
            raw = archive({'mimetype': f'application/vnd.oasis.opendocument.{kind}', 'content.xml': '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"/>', 'META-INF/manifest.xml': '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"/>'})
            self.assertEqual(identify_document(raw)['extension'], ext)

    def test_office_uses_unique_relationship_target_not_fixed_filename(self):
        self.assertEqual(identify_document(office('docx', custom_main='custom/main.xml'))['extension'], 'docx')
        for target in ('', 'missing.xml', 'https://example.com/document.xml', '../outside.xml'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                identify_document(office('docx', relationship_target=target))
        original = office('docx')
        with zipfile.ZipFile(io.BytesIO(original)) as z:
            entries = {name: z.read(name) for name in z.namelist()}
        for replacement in (b'TargetMode="External" Target=', b'Target="word/document.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target='):
            changed = dict(entries)
            changed['_rels/.rels'] = entries['_rels/.rels'].replace(b'Target=', replacement)
            with self.assertRaises(ValueError):
                identify_document(archive(changed))

    def test_signature_does_not_admit_error_or_truncated_files(self):
        for raw in (b'<html>denied</html>', b'{"error":403}', PDF[:-8], office('docx')[:-22], b'{\\rtf1broken', b'\xff\xd8\xff\xd9', png()[:-5], b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1', b'II*\x00'):
            with self.subTest(raw=raw[:20]), self.assertRaises(ValueError):
                identify_document(raw)

    def test_png_crc_and_compressed_stream_checked(self):
        self.assertEqual(identify_document(png())['extension'], 'png')
        for raw in (png(b'not zlib'), png(zlib.compress(b'')), png()[:40] + b'X' + png()[41:]):
            with self.assertRaises(ValueError):
                identify_document(raw)

    def test_zip_crc_and_size_limit_checked(self):
        raw = archive({'a.txt': b'content'})
        self.assertEqual(identify_document(raw)['extension'], 'zip')
        with patch('document_formats.MAX_EXPANDED', 3), self.assertRaises(ValueError):
            identify_document(raw)
        broken = bytearray(raw)
        name_end = 30 + len('a.txt')
        broken[name_end] ^= 0xff
        with self.assertRaises(ValueError):
            identify_document(bytes(broken))

    def test_incomplete_office_not_downgraded_to_zip(self):
        for entries in ({'[Content_Types].xml': '<Types/>'}, {'mimetype': 'application/vnd.oasis.opendocument.text'}, {'[Content_Types].xml': '<!DOCTYPE a [<!ENTITY x "a">]><Types/>'}):
            with self.assertRaises(ValueError):
                identify_document(archive(entries))

    def test_jpeg_requires_frame_components_and_nonempty_scan(self):
        raw = Path(__file__).with_name('fixtures').joinpath('format-rgb-2x2.jpg').read_bytes()
        self.assertEqual(identify_document(raw)['extension'], 'jpg')
        sos = raw.index(b'\xff\xda')
        length = int.from_bytes(raw[sos + 2:sos + 4], 'big')
        no_scan = raw[:sos + 2 + length] + b'\xff\xd9'
        forged = bytes.fromhex('ffd8ffc00008080001000100ffda0002ffd9')
        for invalid in (no_scan, forged):
            with self.assertRaises(ValueError):
                identify_document(invalid)

    def test_redownload_repairs_corrupt_file_metadata_and_preserves_path(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            url = 'https://official.example/original'
            old = store.save(url, None, 'platform', lambda _: (RTF, url))[1]
            original_path = old['file']['path']
            old['file'].update(media_type='application/pdf', size_bytes=0)
            store.persist(old)
            self.assertFalse(store.valid(old))
            result, repaired = store.save(url, None, 'platform', lambda _: (RTF, url))
            self.assertEqual(result, 'downloaded')
            self.assertEqual(repaired['file']['path'], original_path)
            self.assertEqual(repaired['file']['media_type'], 'application/rtf')
            self.assertEqual(repaired['file']['size_bytes'], len(RTF))
            self.assertTrue(store.valid(repaired))
            no_fetch = Mock(side_effect=AssertionError('repair must stop repeated downloads'))
            self.assertEqual(store.save(url, None, 'platform', no_fetch)[0], 'existing')

    def test_rtf_escapes_and_pdf_compatibility(self):
        self.assertEqual(identify_document(RTF)['extension'], 'rtf')
        self.assertIsNone(validate_pdf(PDF))
        with self.assertRaises(ValueError):
            identify_document(b'{\\rtf1\\bin3 abc}')

    def test_store_deduplicates_bytes_and_rebuilds_multiple_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            with CrawlStore(tmp) as store:
                for ext, raw in (('pdf', PDF), ('docx', office('docx')), ('rtf', RTF), ('png', png())):
                    url = f'https://official.example/download?id={ext}'
                    result, record = store.save(url, 'detail', 'platform', lambda _: (raw, url))
                    self.assertEqual(result, 'downloaded')
                    self.assertTrue(record['file']['path'].endswith('.' + ext))
                    other = url + '&mirror=1'
                    self.assertEqual(store.save(other, 'detail2', 'platform', lambda _: (raw, other))[0], 'content_duplicate')
                    self.assertTrue(store.valid(record))
            Path(tmp, 'crawl-state.sqlite3').unlink()
            with CrawlStore(tmp) as store:
                self.assertFalse(store.migration_errors)
                self.assertEqual(len(store.records()), 4)
                fetch = Mock(side_effect=AssertionError('cached files must not redownload'))
                self.assertEqual(store.save('https://official.example/download?id=rtf', 'detail', 'platform', fetch)[0], 'existing')

    def test_same_url_changes_format_and_keeps_versions(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            url = 'https://official.example/original'
            old = store.save(url, None, 'platform', lambda _: (PDF, url))[1]
            new = store.save(url, None, 'platform', lambda _: (RTF, url), refresh=True)[1]
            self.assertTrue(store.valid(old) and store.valid(new))
            self.assertEqual(store.db.execute('select count(*) from versions').fetchone()[0], 2)
            new['file']['media_type'] = 'application/pdf'
            self.assertFalse(store.valid(new))
