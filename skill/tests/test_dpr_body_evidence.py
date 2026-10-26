import unittest
import tempfile
import json
import hashlib
import io
from unittest.mock import Mock
from pathlib import Path
from law_crawler.dpr_body_evidence import compare_body, first_page_identity, official_html_text, scan


class BodyEvidenceTests(unittest.TestCase):
    def test_long_html_retains_text_matching_declared_hash(self):
        text = self.text + (' 法规正文 ' * 6000) + ' END_OF_EVIDENCE'
        raw = ('<html><body>' + text + '</body></html>').encode()
        sink = Mock()
        sink._bucket.return_value.get_object.return_value = io.BytesIO(raw)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'result.jsonl'
            scan([dict(target_id='a', source_id='b', sha256=hashlib.sha256(raw).hexdigest(),
                       raw_key='original.html', size_bytes=len(raw), evidence={})], output, sink)
            result = json.loads(output.read_text())
        self.assertTrue(result['original_verified'])
        self.assertGreater(len(result['first_page_text']), 20000)
        self.assertTrue(result['first_page_text'].endswith('END_OF_EVIDENCE'))
        self.assertEqual(result['page_texts'], [result['first_page_text']])
        self.assertEqual(result['extracted_first_page_sha256'],
                         hashlib.sha256(result['first_page_text'].encode()).hexdigest())
        self.assertEqual(result['extracted_page_sha256'],
                         [hashlib.sha256(page.encode()).hexdigest() for page in result['page_texts']])

    def test_declared_size_rejected_before_oss_access(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'result.jsonl'
            item=dict(target_id='a',source_id='b',sha256='c',raw_key='file.pdf',size_bytes=33*1024**2)
            scan([item],output,object())
            self.assertIn('pdf_exceeds_review_limit_32MiB',json.loads(output.read_text())['reason'])

    def test_review_limits_cannot_disable_bounds(self):
        for size in (0,129,True):
            with self.assertRaises(ValueError):scan([], 'unused', object(), max_mib=size)
        with self.assertRaises(ValueError):scan([], 'unused', object(), max_pages=3)

    text = 'KEPUTUSAN PRESIDEN REPUBLIK INDONESIA (KEPPRES)\nNOMOR 33 TAHUN 1984 (33/1984)\nTENTANG\nPERUBAHAN ATAS KEPUTUSAN PRESIDEN NOMOR 1 TAHUN 1980 TENTANG DEWAN\nPRESIDEN REPUBLIK INDONESIA, Menimbang'

    def test_header_does_not_parse_cited_law(self):
        body = first_page_identity(self.text)
        self.assertEqual(body['identity'], ['keppres', '33', '1984'])
        self.assertTrue(body['title'].startswith('PERUBAHAN ATAS'))

    def test_penpres_is_recognized_as_conflict_not_unreadable(self):
        text=self.text.replace('KEPUTUSAN PRESIDEN','PENETAPAN PRESIDEN').replace('(KEPPRES)','(PENPRES)')
        body=first_page_identity(text)
        self.assertEqual(body['identity'],['penpres','33','1984'])
        result=compare_body(dict(identity=['keppres','33','1984'],fields={'judul':body['title']}),text)
        self.assertEqual(result['reason'],'body_identity_conflicts_with_catalogue')

    def test_legacy_penpres_fields_are_recognized(self):
        text='Bentuk: PENETAPAN PRESIDEN (PENPRES) Oleh: PRESIDEN REPUBLIK INDONESIA Nomor: 7 TAHUN 1965 (7/1965) Tanggal: 1 JANUARI 1965 Sumber: LN Tentang: PERUBAHAN DEWAN Indeks: DEWAN'
        self.assertEqual(first_page_identity(text)['identity'],['penpres','7','1965'])

    def test_complete_title_required(self):
        body = first_page_identity(self.text)
        e = dict(identity=body['identity'], fields={'judul': body['title']})
        self.assertEqual(compare_body(e, self.text)['status'], 'exact_body_identity_and_title')
        e['fields']['judul'] = 'DEWAN'
        self.assertEqual(compare_body(e, self.text)['status'], 'independent_review_required')

    def test_no_title_numeric_correction(self):
        body = first_page_identity(self.text)
        e = dict(identity=body['identity'], fields={'judul': body['title'].replace('1980', '1981')})
        self.assertEqual(compare_body(e, self.text)['status'], 'independent_review_required')

    def test_non_header_citation_rejected(self):
        self.assertIsNone(first_page_identity('MENIMBANG: ' + self.text))

    def test_explicit_legacy_fields(self):
        text = 'Bentuk: KEPUTUSAN PRESIDEN (KEPPRES) Oleh: PRESIDEN REPUBLIK INDONESIA Nomor: 33 TAHUN 1984 (33/1984) Tanggal: 9 MEI 1984 Sumber: LN 1984/1 Tentang: PEMBERLAKUAN UNDANG-UNDANG NOMOR 5 TAHUN 1960 Indeks: REFERENSI'
        body = first_page_identity(text)
        self.assertEqual(body['identity'], ['keppres', '33', '1984'])
        self.assertEqual(body['title'], 'PEMBERLAKUAN UNDANG-UNDANG NOMOR 5 TAHUN 1960')

    def test_invocation_is_not_subject(self):
        text = self.text.replace('PRESIDEN REPUBLIK INDONESIA, Menimbang', 'DENGAN RAHMAT TUHAN YANG MAHA ESA PRESIDEN REPUBLIK INDONESIA, Menimbang')
        self.assertEqual(first_page_identity(text)['title'], first_page_identity(self.text)['title'])

    def test_html_head_and_styles_excluded(self):
        html = '<html><head><title>Wrong law</title><style>hidden</style></head><body><p>' + self.text + '</p><script>ignore</script></body></html>'
        self.assertEqual(first_page_identity(official_html_text(html))['identity'], ['keppres', '33', '1984'])

    def test_official_letterhead_separate_from_identity(self):
        text = 'SALINAN\nPRESIDEN\nREPUBLIK INDONESIA\n' + self.text
        self.assertEqual(first_page_identity(text)['identity'], ['keppres', '33', '1984'])


if __name__ == '__main__':
    unittest.main()
