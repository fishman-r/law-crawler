"""A-1真实节选的双语边界回归；不代表全库结构或法律效力验收。"""
from copy import deepcopy
from pathlib import Path
import re
import unittest
from xml.etree import ElementTree as ET

from law_crawler.text_documents import render_markdown


FIXTURE = Path(__file__).parent / 'fixtures/canada/a-1-bilingual-excerpt.xml'
SOURCE = 'https://laws-lois.justice.gc.ca/eng/XML/A-1.xml'
META = {'document_type': {'value': 'regulation'}}


def render(data):
    return render_markdown(data, META, SOURCE).decode()


def language_items(output):
    return re.findall(r'\\\[(English|Français): (.*?)\\\]', output)


class CanadaBilingualStructureTests(unittest.TestCase):
    def test_real_schedule_rows_preserve_language_boundaries_and_provision_columns(self):
        output = render(FIXTURE.read_bytes())
        rows = [line for line in output.splitlines() if line.startswith('|')]
        for english, french, provision in [
            ('Aeronautics Act', 'Loi sur l’aéronautique',
             'subsection 4.79(1), section 5.32 and subsections 6.5(5), 22(2) and 24.2(4)'),
            ('Canada Labour Code', 'Code canadien du travail', 'subsection 144(3)'),
        ]:
            with self.subTest(english=english):
                row = next(row for row in rows if english in row)
                cells = row.split('|')[1:-1]
                self.assertEqual(len(cells), 2)
                self.assertEqual(language_items(cells[0]), [('English', english), ('Français', french)])
                self.assertEqual(cells[1].strip(), provision)
                self.assertNotIn(english + french, row)
                for text in (english, french, provision):
                    self.assertEqual(output.count(text), 1)
        self.assertIn('## Section 1', output)
        self.assertIn('## Schedule', output)
        self.assertIn('This Act may be cited as the Access to Information Act.', output)
        self.assertNotIn('[BilingualGroup:', output)
        self.assertNotIn('** (XML ', output)

    def test_real_non_table_group_keeps_title_and_each_language_item_in_source_order(self):
        output = render(FIXTURE.read_bytes())
        before_table = output.split('| Column 1 |')[0]
        self.assertIn('Departments and Ministries of State', before_table)
        self.assertEqual(language_items(before_table), [
            ('English', 'Department for Women and Gender Equality'),
            ('Français', 'Ministère des Femmes et de l’Égalité des genres'),
            ('English', 'Department of Agriculture and Agri-Food'),
            ('Français', 'Ministère de l’Agriculture et de l’Agroalimentaire'),
        ])

    def test_real_language_items_remain_distinct_in_text_and_standalone_group(self):
        root = ET.parse(FIXTURE).getroot()
        group = root.find('.//entry/BilingualGroup')
        for wrapper in ('Text', 'BilingualGroup'):
            with self.subTest(wrapper=wrapper):
                node = ET.Element(wrapper)
                if wrapper == 'Text':
                    node.text = 'Before '
                    node.append(deepcopy(group))
                    node[0].tail = ' after.'
                else:
                    node.extend(deepcopy(list(group)))
                body = root.find('Body')
                for child in list(body)[1:]:
                    body.remove(child)
                body.append(node)
                output = render(ET.tostring(root))
                self.assertEqual(language_items(output), [
                    ('English', 'Aeronautics Act'), ('Français', 'Loi sur l’aéronautique')])
                self.assertNotIn('Aeronautics ActLoi', output)
                if wrapper == 'Text':
                    self.assertIn('Before ', output)
                    self.assertIn(' after.', output)

    def test_unmatched_reversed_and_nested_items_keep_source_order_text_and_attributes(self):
        root = ET.parse(FIXTURE).getroot()
        body = root.find('Body')
        for child in list(body)[1:]:
            body.remove(child)
        body.append(ET.fromstring(
            '<Text><BilingualGroup role="source-group">'
            '<BilingualItemFr role="source-item">Nom <Emphasis>français</Emphasis></BilingualItemFr>'
            '<BilingualItemEn>English name</BilingualItemEn>'
            '<BilingualItemEn>Unpaired name</BilingualItemEn>'
            '</BilingualGroup></Text>'))
        output = render(ET.tostring(root))
        self.assertEqual(language_items(output), [
            ('Français', 'Nom français'), ('English', 'English name'), ('English', 'Unpaired name')])
        self.assertIn('@role="source-group"', output)
        self.assertIn('@role="source-item"', output)
        self.assertNotIn('[BilingualGroup:', output)


if __name__ == '__main__':
    unittest.main()
