"""加拿大司法部 XML 结构白名单；测试使用最小合成样本。"""
import unittest
from unittest.mock import patch

from document_formats import identify_document


def law_xml(kind='Statute'):
    number = '<Chapter><ConsolidatedNumber>A-1</ConsolidatedNumber></Chapter>' if kind == 'Statute' else '<InstrumentNumber>SOR/86-304</InstrumentNumber>'
    return (f'<?xml version="1.0"?><{kind} xmlns:lims="http://justice.gc.ca/lims" lims:id="167">'
            f'<Identification><LongTitle>Example legislation</LongTitle>{number}</Identification>'
            f'<Body><Section><Label>1</Label><Text>Operative provision.</Text></Section></Body></{kind}>').encode()


class CanadianXMLTests(unittest.TestCase):
    def test_statute_and_regulation(self):
        for kind in ('Statute', 'Regulation'):
            with self.subTest(kind=kind):
                self.assertEqual(identify_document(law_xml(kind)), {'extension': 'xml', 'media_type': 'application/xml'})

    def test_utf8_bom_and_unicode(self):
        data = b'\xef\xbb\xbf' + law_xml().replace(b'Example legislation', 'Loi sur l’accès'.encode())
        self.assertEqual(identify_document(data)['extension'], 'xml')

    def test_directories_errors_and_html_are_rejected(self):
        for data in (b'<Statutes><Statute>A-1</Statute></Statutes>', b'<Error>Access denied</Error>', b'<html><body>Error</body></html>', b'<?xml version="1.0"?><root/>'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                identify_document(data)

    def test_missing_identity_title_and_number_are_rejected(self):
        for kind in ('Statute', 'Regulation'):
            data = law_xml(kind)
            start, end = data.index(b'<Identification>'), data.index(b'</Identification>') + len(b'</Identification>')
            variants = [data[:start] + data[end:], data.replace(b'<LongTitle>Example legislation</LongTitle>', b''), data.replace(b'A-1', b'').replace(b'SOR/86-304', b'')]
            for invalid in variants:
                with self.subTest(kind=kind, invalid=invalid), self.assertRaises(ValueError):
                    identify_document(invalid)

    def test_missing_or_empty_body_rejected(self):
        data = law_xml()
        for invalid in (data.replace(b'<Body>', b'<Other>').replace(b'</Body>', b'</Other>'), data.replace(b'Operative provision.', b''), data.replace(b'Section', b'Unknown'), data.replace(b'<Body>', b'<Body></Body><Body>')):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                identify_document(invalid)

    def test_wrong_or_missing_lims_namespace_rejected(self):
        for invalid in (law_xml().replace(b'http://justice.gc.ca/lims', b'http://example.org'), law_xml().replace(b' lims:id="167"', b'')):
            with self.assertRaises(ValueError):
                identify_document(invalid)

    def test_dtd_entity_and_invalid_xml_rejected(self):
        for invalid in (law_xml()[:-1], law_xml().replace(b'<Statute ', b'<!DOCTYPE Statute><Statute '), law_xml().replace(b'<Statute ', b'<!DOCTYPE Statute [<!ENTITY x "y">]><Statute '), law_xml().replace(b'Operative provision.', b'&undefined;')):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                identify_document(invalid)

    def test_non_utf8_rejected(self):
        for invalid in (law_xml().decode().encode('utf-16'), law_xml().replace(b'Example', b'Ex\xffample')):
            with self.assertRaises(ValueError):
                identify_document(invalid)

    def test_size_limit(self):
        with patch('document_formats.MAX_LAW_XML', 10), self.assertRaises(ValueError):
            identify_document(law_xml())


if __name__ == '__main__':
    unittest.main()
