"""结构语义回归；短样本取材加拿大 LIMS 结构，不替代官网或法律效力验收。"""
import json
import re
import unittest

from law_crawler.text_documents import render_markdown


SOURCE = 'https://official.example/regulation.xml'


def metadata(**values):
    fields = {'document_type': 'regulation', 'title': 'Structure fixture'}
    fields.update(values)
    return {key: {'value': value, 'source_url': SOURCE} for key, value in fields.items()}


def canada(body, identification=''):
    return ('<Regulation xmlns:lims="http://justice.gc.ca/lims" lims:id="test">'
            '<Identification><LongTitle>Structure fixture</LongTitle>'
            '<InstrumentNumber>SOR/2007-151</InstrumentNumber>' + identification +
            '</Identification><Body>' + body + '</Body></Regulation>').encode()


def render(data, **values):
    return render_markdown(data, metadata(**values), SOURCE).decode()


def html_definition_blocks(output):
    """用具名源边界划分dt/dd；不要求内容与标签同一行或固定Markdown排版。"""
    labels = list(re.finditer(r'\b(Term|Definition)\s*\((dt|dd)\)', output, re.I))
    return [(match.group(1).lower(), match.group(2).lower(),
             output[match.end():labels[index + 1].start() if index + 1 < len(labels) else len(output)])
            for index, match in enumerate(labels)]


def dated(year, month, day):
    return f'<Date><YYYY>{year}</YYYY><MM>{month}</MM><DD>{day}</DD></Date>'


class MarkdownStructureTests(unittest.TestCase):
    def test_lims_dates_keep_distinct_field_and_parent_context(self):
        # MV Sonia 的三种日期角色；制令日故意与登记日不同，防止字段串位。
        identification = (
            '<RegistrationDate>' + dated(2007, 6, 14) + '</RegistrationDate>'
            '<ConsolidationDate>' + dated(2026, 6, 15) + '</ConsolidationDate>'
            '<RegulationMakerOrder><RegulationMaker>P.C.</RegulationMaker>'
            '<OrderNumber>2007-1002</OrderNumber>' + dated(2007, 6, 13) + '</RegulationMakerOrder>')
        output = render(canada('<Section><Label>1</Label><Text>Operative provision.</Text></Section>', identification))
        for field, value in [('RegistrationDate', '2007-06-14'),
                             ('ConsolidationDate', '2026-06-15'),
                             ('命令日期', '2007-06-13')]:
            with self.subTest(field=field):
                self.assertRegex(output, rf'(?m)^.*{field}.*{value}.*$')
        self.assertIn('RegulationMaker', output)
        self.assertIn('P.C.', output)
        self.assertIn('OrderNumber', output)
        self.assertIn('2007-1002', output)
        self.assertNotRegex(output, r'(?m)^\s*(?:2007|2026|6|13|14|15)\s*$')

    def test_field_labels_omit_parenthesized_xml_paths(self):
        identification = ('<RegistrationDate>' + dated(2007, 6, 14) + '</RegistrationDate>'
                          '<EnablingAuthority>CUSTOMS TARIFF</EnablingAuthority>')
        output = render(canada('<Section><Label>1</Label><Text>Operative wording.</Text></Section>', identification))
        self.assertIn('**依据法律（EnablingAuthority）**: CUSTOMS TARIFF', output)
        self.assertIn('**登记日期（RegistrationDate）**: 2007-06-14', output)
        self.assertIn('**Identification**', output)
        self.assertNotIn('** (XML ', output)

    def test_stage_attributes_distinguish_assent_from_consolidation(self):
        data = ('<LegalDocument><BillHistory><Stages stage="consolidation">' + dated(2026, 6, 15) +
                '</Stages><Stages stage="assented-to">' + dated(2019, 6, 21) +
                '</Stages></BillHistory><Text>Operative provision.</Text></LegalDocument>').encode()
        output = render(data, text_body_confirmed=True, text_body_root='LegalDocument')
        for stage, value in [('consolidation', '2026-06-15'), ('assented-to', '2019-06-21')]:
            self.assertRegex(output, rf'(?m)^.*{stage}.*{value}.*$')
        self.assertNotRegex(output, r'(?m)^\s*(?:2026|2019|6|15|21)\s*$')

    def test_nested_provisions_keep_named_levels_and_inline_label_spacing(self):
        body = ('<Section><Label>2</Label><Text>The conditions are:</Text>'
                '<Subsection><Label>(1)</Label><Text>General condition.</Text>'
                '<Paragraph><Label>(a)</Label><Text>the importer files evidence;</Text>'
                '<Subparagraph><Label>(i)</Label><Text>including the invoice.</Text></Subparagraph>'
                '</Paragraph></Subsection></Section>')
        output = render(canada(body))
        levels = []
        for label in ['Section 2', 'Subsection (1)', 'Paragraph (a)', 'Subparagraph (i)']:
            match = re.search(r'(?m)^(#{2,6}) ' + re.escape(label) + r'\s*$', output)
            self.assertIsNotNone(match, label)
            levels.append(len(match.group(1)))
        self.assertEqual(levels, sorted(set(levels)))
        for text in ['The conditions are:', 'General condition.', 'the importer files evidence;', 'including the invoice.']:
            self.assertIn(text, output)
        self.assertNotIn('(a)the', output)
        self.assertNotIn('(i)including', output)

    def test_footnote_reference_is_traceable_without_mangling_legal_name(self):
        body = ('<Section><Label>1</Label><Text>Under the <XRefExternal link="C-54.011">Customs Tariff</XRefExternal>'
                '<FootnoteRef idref="fna_e">a</FootnoteRef>, remission is granted.</Text>'
                '<Footnote id="fna_e"><Label>a</Label><Text>S.C. 1997, c. 36</Text></Footnote></Section>')
        output = render(canada(body))
        self.assertIn('Customs Tariff', output)
        self.assertNotIn('Tariffa', output)
        self.assertIn('[^fna_e]', output)
        self.assertRegex(output, r'(?m)^\[\^fna_e\]:.*S\.C\. 1997, c\. 36')
        self.assertEqual(output.count('[^fna_e]:'), 1)
        self.assertIn(', remission is granted.', output)

    def test_depth_beyond_markdown_heading_limit_retains_parent_context(self):
        body = ('<Section><Label>2</Label><Subsection><Label>(1)</Label>'
                '<Paragraph><Label>(a)</Label><Subparagraph><Label>(i)</Label>'
                '<Clause><Label>(A)</Label><Text>Clause wording.</Text>'
                '<Subclause><Label>(I)</Label><Text>Subclause wording.</Text>'
                '</Subclause></Clause></Subparagraph></Paragraph></Subsection></Section>')
        output = render(canada(body))
        self.assertIn('###### Clause (A)', output)
        self.assertIn('###### Subclause (I)', output)
        self.assertIn('/Regulation/Body/Section/Subsection/Paragraph/Subparagraph/Clause/Subclause', output)
        self.assertIn('Subclause wording.', output)

    def test_unresolved_and_duplicate_footnotes_do_not_invent_a_target(self):
        for notes in ['', '<Footnote id="dup"><Text>First note.</Text></Footnote><Footnote id="dup"><Text>Second note.</Text></Footnote>']:
            with self.subTest(notes=notes):
                output = render(canada('<Section><Label>1</Label><Text>Rule<FootnoteRef idref="dup">a</FootnoteRef>.</Text>' + notes + '</Section>'))
                self.assertIn('FootnoteRef', output)
                self.assertIn('dup', output)
                self.assertNotIn('[^dup]', output)
                if notes:
                    self.assertIn('First note.', output)
                    self.assertIn('Second note.', output)

    def test_cals_table_preserves_headers_and_rows(self):
        body = ('<Section><Label>1</Label><Text>Schedule values.</Text><table><tgroup cols="2">'
                '<colspec colname="c1"/><colspec colname="c2"/>'
                '<thead><row><entry>Act</entry><entry>Provision</entry></row></thead>'
                '<tbody><row><entry colname="c1">Aeronautics Act</entry><entry colname="c2">section 5.32</entry></row>'
                '<row><entry>Broadcasting Act</entry><entry>subsection 25.3(2)</entry></row></tbody>'
                '</tgroup></table></Section>')
        output = render(canada(body))
        self.assertRegex(output, r'(?m)^\|\s*Act\s*\|\s*Provision\s*\|\s*$')
        self.assertRegex(output, r'(?m)^\|[ :\-|]+\|\s*$')
        self.assertRegex(output, r'(?m)^\|\s*Aeronautics Act\s*\|\s*section 5\.32\s*\|\s*$')
        self.assertRegex(output, r'(?m)^\|\s*Broadcasting Act\s*\|\s*subsection 25\.3\(2\)\s*\|\s*$')

    def test_cals_spans_keep_original_coordinates_and_cell_relationships(self):
        body = ('<Section><Label>1</Label><Text>Lighting.</Text><table><tgroup cols="3">'
                '<colspec colname="c1"/><colspec colname="c2"/><colspec colname="c3"/>'
                '<thead><row><entry>Item</entry><entry>Task</entry><entry>Level</entry></row></thead>'
                '<tbody><row><entry morerows="1">1</entry><entry namest="c2" nameend="c3">DESK WORK</entry></row>'
                '<row><entry>Drafting</entry><entry>1 000 lx</entry></row></tbody></tgroup></table></Section>')
        output = render(canada(body))
        for value in ['morerows', 'namest', 'nameend', 'c2', 'c3', 'DESK WORK']:
            self.assertIn(value, output)
        self.assertEqual(output.count('DESK WORK'), 1)
        # 合并单元格下方条目仍须在 Task / Level 两列；不能左移到 Item。
        self.assertRegex(output, r'(?m)^\|[^\n|]*\|\s*Drafting\s*\|\s*1 000 lx\s*\|\s*$')

    def test_html_table_retains_header_and_span_semantics(self):
        data = (b'<html><main><p>Rates.</p><table><thead><tr><th>Class</th><th>Rate</th></tr></thead>'
                b'<tbody><tr><td rowspan="2">A</td><td>10</td></tr><tr><td>20</td></tr>'
                b'<tr><td colspan="2">Exempt</td></tr></tbody></table></main></html>')
        output = render(data, text_body_confirmed=True, text_body_selector='main')
        self.assertRegex(output, r'(?m)^\|\s*Class\s*\|\s*Rate\s*\|\s*$')
        self.assertIn('rowspan', output)
        self.assertIn('colspan', output)
        self.assertRegex(output, r'(?m)^\|[^\n|]*\|\s*20\s*\|\s*$')
        self.assertEqual(output.count('Exempt'), 1)

    def test_html_definition_list_preserves_field_names_and_values_without_gluing(self):
        data = (b'<html><main><p>Identification.</p><dl><dt>Number</dt><dd>123</dd>'
                b'<dt>Date</dt><dd>2007-06-14</dd></dl></main></html>')
        output = render(data, text_body_confirmed=True, text_body_selector='main')
        blocks = html_definition_blocks(output)
        self.assertEqual([(role, tag) for role, tag, _ in blocks],
                         [('term', 'dt'), ('definition', 'dd'), ('term', 'dt'), ('definition', 'dd')])
        for (_, _, block), content in zip(blocks, ['Number', '123', 'Date', '2007-06-14']):
            self.assertIn(content, block)
        for term, value in [('Number', '123'), ('Date', '2007-06-14')]:
            self.assertLess(output.index(term), output.index(value))
        self.assertNotIn('Number123', output)
        self.assertNotIn('Date2007-06-14', output)

    def test_html_repeated_definition_terms_and_values_keep_source_order_and_boundaries(self):
        # HTML允许多个term/definition；不得猜成两个一一配对的字段。
        data = (b'<html><main><p>Source wording.</p><dl><dt>Alpha term</dt><dt>Beta term</dt>'
                b'<dd>First definition</dd><dd>Second definition</dd></dl></main></html>')
        output = render(data, text_body_confirmed=True, text_body_selector='main')
        entries = [('Term', 'dt', 'Alpha term'), ('Term', 'dt', 'Beta term'),
                   ('Definition', 'dd', 'First definition'), ('Definition', 'dd', 'Second definition')]
        blocks = html_definition_blocks(output)
        self.assertEqual(len(blocks), len(entries))
        for (role, tag, content), (actual_role, actual_tag, block) in zip(entries, blocks):
            self.assertEqual((actual_role, actual_tag), (role.lower(), tag))
            self.assertEqual(output.count(content), 1)
            self.assertIn(content, block)
            for _, _, other in entries:
                if other != content:
                    self.assertNotIn(other, block, '每个dt/dd须独立标明原语义边界，不能猜配对后合并')

    def test_html_superscript_and_subscript_keep_named_inline_semantics(self):
        data = (b'<html><main><p>Under the Customs Tariff<sup>a</sup>, fee applies.'
                b' The formula is H<sub>2</sub>O.</p></main></html>')
        output = render(data, text_body_confirmed=True, text_body_selector='main')
        self.assertIn('Customs Tariff', output)
        self.assertIn(', fee applies.', output)
        self.assertRegex(output, r'(?i)\bsup\b[^\n]*\ba\b')
        self.assertRegex(output, r'(?i)\bsub\b[^\n]*2')
        self.assertNotIn('Tariffa', output)
        self.assertNotIn('H2O', output)
        self.assertNotIn('[^a]', output, 'sup字形本身不能被猜成已解析脚注目标')
        self.assertNotIn('<sup>', output)
        self.assertNotIn('<sub>', output)

    def test_html_time_retains_source_datetime_without_assigning_legal_date_role(self):
        data = (b'<html><main><p>Source date: <time datetime="2007-06-14">14 June 2007</time>.'
                b'</p></main></html>')
        output = render(data, text_body_confirmed=True, text_body_selector='main')
        for value in ['time', 'datetime', '2007-06-14', '14 June 2007']:
            self.assertIn(value, output)
        for guessed_role in ['PublicationDate', 'EffectiveDate', 'RegistrationDate', 'ConsolidationDate']:
            self.assertNotIn(guessed_role, output)
        self.assertNotIn('<time', output)

    def test_html_image_keeps_inert_alt_and_source_attributes(self):
        data = (b'<html><main><p>See official annex.</p><img src="/annex.png" alt="Schedule A chart">'
                b'<script>untrusted()</script></main></html>')
        output = render(data, text_body_confirmed=True, text_body_selector='main')
        for value in ['img', 'alt', 'src', 'Schedule A chart', '/annex.png']:
            self.assertIn(value, output)
        self.assertNotIn('<img', output)
        self.assertNotIn('<script', output)
        self.assertNotIn('untrusted()', output)
        self.assertNotIn('![', output, '原件图片仅保留惰性来源引用，不能自动渲染远程图')

    def test_unknown_xml_fields_and_attributes_are_conservative_source_labels(self):
        data = (b'<LegalDocument version="draft"><Title>Example</Title><MysteryCode authority="official">ZX-7</MysteryCode>'
                b'<Article><Num>2</Num><Text>Value <MysteryUnit unit="kg">5</MysteryUnit> applies.</Text>'
                b'</Article></LegalDocument>')
        output = render(data, text_body_confirmed=True, text_body_root='LegalDocument')
        for value in ['LegalDocument', 'version', 'draft', 'MysteryCode', 'authority', 'official', 'ZX-7', 'MysteryUnit', 'unit', 'kg']:
            self.assertIn(value, output)
        self.assertIn('Article 2', output)
        self.assertIn('applies.', output)
        self.assertNotIn('Value5', output)
        self.assertNotIn('5applies', output)

    def test_json_array_indexes_and_fields_are_meaningful_without_bare_headings(self):
        data = json.dumps({'articles': [{'number': '2', 'text': 'First rule', 'points': ['One', 'Two']},
                                        {'number': '9', 'text': 'Second rule'}], 'status': None}).encode()
        output = render(data, text_body_confirmed=True, text_body_root='articles')
        for field in ['articles', 'number', 'text', 'points', 'status']:
            self.assertIn(field, output)
        for text in ['First rule', 'Second rule', '1. One', '2. Two', 'null']:
            self.assertIn(text, output)
        self.assertNotRegex(output, r'(?m)^#{1,6}\s+\d+\s*$')
        self.assertRegex(output, r'articles.*(?:\[0\]|\[1\]|item 1|Item 1)')


if __name__ == '__main__':
    unittest.main()
