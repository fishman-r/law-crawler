import json
from pathlib import Path
import tempfile
import unittest
from law_crawler.translation_tables import compare,audit

VALID='<table><tr><th>A</th><th>Price Rp/kg</th></tr><tr><td rowspan="2">Item</td><td>19.000*****</td></tr><tr><td>21.000******</td></tr></table>'

class TranslationTableTests(unittest.TestCase):
    def test_valid_rowspan(self):
        self.assertEqual(compare(VALID,{'zh':VALID,'en':VALID})['status'],'pass_structure_only')

    def test_nested_and_split_tables(self):
        for changed in [VALID.replace('Item','Item<table><tr><td>x</td></tr></table>'),VALID.replace('</tr><tr>','</tr></table><table><tr>',1),VALID[:-8]]:
            self.assertEqual(compare(VALID,{'zh':changed})['status'],'requires_review')

    def test_changed_shape_and_holes(self):
        for changed in [VALID.replace('rowspan="2"','rowspan="1"'),VALID.replace('rowspan="2"','rowspan="3"')]:
            self.assertEqual(compare(VALID,{'en':changed})['status'],'requires_review')

    def test_numbers_currency_and_stars(self):
        for changed in [VALID.replace('19.000','19.00'),VALID.replace('Rp/kg','USD/kg'),VALID.replace('21.000******','21.000*****')]:
            self.assertEqual(compare(VALID,{'zh':changed})['status'],'requires_review')

    def test_audit_reuse_and_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);paths=[]
            for name in ('source','zh','en'):
                path=root/name;path.write_text(VALID);paths.append(path)
            output=root/'audit.json';audit(*paths,output);before=output.read_bytes()
            self.assertTrue(audit(*paths,output)['reused']);self.assertEqual(before,output.read_bytes())
            value=json.loads(output.read_text());value['status']='changed';output.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'integrity mismatch'):audit(*paths,output)

    def test_no_html_tables_is_not_pass(self):
        self.assertEqual(compare('plain text',{'zh':'普通文字'})['status'],'requires_review')

    def test_chinese_adjacent_numbers_and_currency_remain_audited(self):
        source='<table><tr><td>Kadar Air 15%</td><td>Rp 19.000</td></tr></table>'
        translated='<table><tr><td>含水量15%</td><td>价格Rp 19.000</td></tr></table>'
        self.assertEqual(compare(source,{'zh':translated})['status'],'pass_structure_only')
        self.assertEqual(compare(source,{'zh':translated.replace('15%','16%')})['status'],'requires_review')
        self.assertEqual(compare(source,{'zh':translated.replace('Rp','USD')})['status'],'requires_review')
