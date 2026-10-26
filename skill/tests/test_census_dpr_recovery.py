import unittest

from law_crawler.platform_adapters.census_dpr import extract_dpr_evidence, identity, strict_match, normalized_title


class DprRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.record = dict(type='presidential_instruction', year='1975', title='Program bantuan sekolah')
        self.html = '<table><tr><th>Judul</th><td>Program bantuan sekolah</td></tr><tr><th>Nomor</th><td>01</td></tr></table><a href="https://berkas.dpr.go.id/jdih/document/inpres/?t=1">UNDUH BERKAS</a>'

    def test_placeholder_is_not_file(self):
        e = extract_dpr_evidence(self.record, self.html)
        self.assertEqual(e['reason'], 'official_download_filename_empty')
        self.assertEqual(e['concrete_links'], [])
        self.assertEqual(e['identity'], ('inpres', '1', '1975'))

    def test_exact_identity_and_title(self):
        e = extract_dpr_evidence(self.record, self.html)
        other = dict(type='Instruksi Presiden', year='1975', number='1', title='Instruksi Presiden Nomor 1 Tahun 1975 tentang Program Bantuan Sekolah')
        self.assertTrue(strict_match(self.record, e, other))
        for key, wrong in [('year', '1976'), ('number', '2'), ('type', 'Keputusan Presiden'), ('title', 'Program bantuan sekolah lainnya')]:
            self.assertFalse(strict_match(self.record, e, dict(other, **{key: wrong})))

    def test_missing_or_ambiguous_identity_not_guessed(self):
        self.assertIsNone(identity(dict(type='presidential_instruction', number='1/1975', year='1975')))
        self.assertIsNone(identity(dict(type='Peraturan Menteri', number='1', year='1975')))

    def test_kemenkeu_structured_number(self):
        self.assertEqual(identity(dict(type='Peraturan Pemerintah', number='PP 1 TAHUN 2000', year='2000', official_metadata={'no': 1})), ('pp', '1', '2000'))

    def test_title_suffix_cannot_be_lost_by_structured_number(self):
        record=dict(type='Keputusan Presiden', number='68', year='1983', title='Keputusan Presiden 68/M Tahun1983 Tentang Pengangkatan')
        self.assertIsNone(identity(record))
        self.assertIsNone(identity(dict(record,title='Pengangkatan',official_metadata={'no':68,'judul':record['title']})))
        record.update(title='Keputusan Presiden 68 Tahun 1983 Tentang Perubahan Atas Keputusan Presiden 9/M Tahun 1980')
        self.assertEqual(identity(record), ('keppres','68','1983'))

    def test_untrusted_link_rejected(self):
        e = extract_dpr_evidence(self.record, self.html.replace('berkas.dpr.go.id', 'evil.example'))
        self.assertEqual(e['reason'], 'official_download_link_absent')

    def test_subject_tentang_is_preserved(self):
        title = 'Perubahan atas Keputusan Presiden Nomor 59 Tahun 1983 tentang Pembentukan Dewan'
        full = 'Keputusan Presiden 6 Tahun 1984 Tentang ' + title
        self.assertEqual(normalized_title(title), normalized_title(full))
        self.assertNotEqual(normalized_title(title), normalized_title('Pembentukan Dewan'))

    def test_formal_title_type_conflict_blocks_misclassified_candidate(self):
        record=dict(type='Keputusan Presiden',number='7',year='1965',title='Penetapan Presiden 7 TAHUN 1965 Tentang Perubahan DPRD')
        self.assertIsNone(identity(record))
        self.assertIsNone(identity(dict(record,title='Perubahan DPRD',official_metadata={'judul':record['title']})))
        self.assertEqual(identity(dict(record,type='Penetapan Presiden')),('penpres','7','1965'))

    def test_subject_citation_does_not_override_formal_type(self):
        record=dict(type='Keputusan Presiden',number='7',year='1965',title='Perubahan atas Penetapan Presiden 7 Tahun 1964 tentang Dewan')
        self.assertEqual(identity(record),('keppres','7','1965'))


if __name__ == '__main__':
    unittest.main()
