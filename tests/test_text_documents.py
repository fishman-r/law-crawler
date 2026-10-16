"""合成文本正文与错误响应；不代表任何官方网站实跑验收。"""
import json
import unittest

from law_crawler.text_documents import detect_text_document, render_markdown


def metadata(**values):
    fields = {'document_type': 'regulation', 'title': 'Example Act'}
    fields.update(values)
    return {k: {'value': v, 'source_url': 'https://official.example/law'} for k, v in fields.items()}


CANADA = b'''<?xml version="1.0"?><Statute xmlns:lims="http://justice.gc.ca/lims" lims:id="1">
<Identification><LongTitle>Example Act</LongTitle><Chapter><ConsolidatedNumber>A-1</ConsolidatedNumber></Chapter></Identification>
<Body><Section><Label>1</Label><Text>The <Emphasis>first</Emphasis> provision.</Text><Subsection><Label>(1)</Label><Text>Nested provision.</Text></Subsection></Section></Body></Statute>'''
HTML = b'''<!DOCTYPE html><html><head><title>Example Act</title></head><body><nav>Catalog</nav>
<main id="law-body"><h1>Example Act</h1><h2>Article 1</h2><p>The <strong>first</strong> provision.</p>
<ol><li>Point one</li><li>Point two</li></ol><p>See <a href="/other">other law</a>.</p><script>danger()</script></main></body></html>'''


class TextDocumentTests(unittest.TestCase):
    def test_canadian_xml_uses_existing_structure_gate(self):
        self.assertEqual(detect_text_document(CANADA, metadata()), {'extension': 'xml', 'media_type': 'application/xml'})
        output = render_markdown(CANADA, metadata(), 'https://official.example/A-1.xml').decode()
        self.assertIn('## Section 1', output)
        self.assertIn('### Subsection (1)', output)
        self.assertIn('The first provision.', output)
        self.assertIn('Nested provision.', output)

    def test_html_requires_explicit_body_confirmation_and_container(self):
        self.assertIsNone(detect_text_document(HTML, metadata()))
        self.assertIsNone(detect_text_document(HTML, metadata(text_body_confirmed='true')))
        with self.assertRaises(ValueError):
            detect_text_document(HTML, metadata(text_body_confirmed=True))
        meta = metadata(text_body_confirmed=True, text_body_selector='#law-body')
        self.assertEqual(detect_text_document(HTML, meta)['extension'], 'html')
        output = render_markdown(HTML, meta, 'https://official.example/law').decode()
        self.assertIn('## Article 1', output)
        self.assertIn('**first**', output)
        self.assertIn('1. Point one', output)
        self.assertIn('2. Point two', output)
        self.assertIn('[other law](https://official.example/other)', output)
        self.assertNotIn('Catalog', output)
        self.assertNotIn('danger()', output)

    def test_html_ordered_list_preserves_start_and_explicit_numbers(self):
        data = b'<html><main><ol start="7"><li>Seven</li><li value="10">Ten</li><li>Eleven</li></ol></main></html>'
        output = render_markdown(data, metadata(text_body_confirmed=True, text_body_selector='main'), 'https://official.example/law').decode()
        self.assertIn('7. Seven', output)
        self.assertIn('10. Ten', output)
        self.assertIn('11. Eleven', output)

    def test_html_errors_challenges_directories_and_duplicate_containers_rejected(self):
        meta = metadata(text_body_confirmed=True, text_body_selector='main')
        for data in (b'<html><title>Access Denied</title><main><p>Permission denied</p></main></html>',
                     b'<html><title>Just a moment...</title><main><p>Complete challenge</p></main></html>',
                     b'<html><main><a href="/law">Catalog item</a></main></html>',
                     b'<html><main><p>Law one</p></main><main><p>Law two</p></main></html>'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                detect_text_document(data, meta)

    def test_generic_xml_requires_root_evidence_and_preserves_paragraphs(self):
        data = '<LegalDocument><Title>Loi générale</Title><Article><Num>2</Num><Paragraph>Texte <Emphasis>accentué</Emphasis>.</Paragraph></Article></LegalDocument>'.encode()
        self.assertIsNone(detect_text_document(data, metadata()))
        meta = metadata(text_body_confirmed=True, text_body_root='LegalDocument')
        self.assertEqual(detect_text_document(data, meta)['extension'], 'xml')
        output = render_markdown(data, meta, 'https://official.example/law.xml').decode()
        self.assertIn('Loi générale', output)
        self.assertIn('Texte accentué.', output)
        self.assertIn('## Article 2', output)
        with self.assertRaises(ValueError):
            detect_text_document(data, metadata(text_body_confirmed=True, text_body_root='Directory'))

    def test_xml_entities_and_dtd_rejected_in_utf8_and_utf16(self):
        raw = '<!DOCTYPE LegalDocument [<!ENTITY law "Secret">]><LegalDocument><Text>&law;</Text></LegalDocument>'
        for data in (raw.encode(), raw.encode('utf-16')):
            with self.subTest(data=data[:12]), self.assertRaises(ValueError):
                detect_text_document(data, metadata(text_body_confirmed=True, text_body_root='LegalDocument'))

    def test_xml_directory_and_accessdenied_roots_rejected(self):
        for root in ('Catalog', 'Directory', 'AccessDenied', 'Error', 'html'):
            data = f'<{root}><Text>Some content</Text></{root}>'.encode()
            with self.subTest(root=root), self.assertRaises(ValueError):
                detect_text_document(data, metadata(text_body_confirmed=True, text_body_root=root))

    def test_json_requires_confirmed_actual_body_and_recursive_render(self):
        data = json.dumps({'title': 'Law', 'articles': [{'number': 1, 'text': '法条全文'}, {'number': 2, 'points': ['One', 'Two']}], 'effective': None}, ensure_ascii=False).encode()
        self.assertIsNone(detect_text_document(data, metadata()))
        meta = metadata(text_body_confirmed=True, text_body_root='articles')
        self.assertEqual(detect_text_document(data, meta)['extension'], 'json')
        output = render_markdown(data, meta, 'https://official.example/law.json').decode()
        self.assertIn('法条全文', output)
        self.assertIn('1. One', output)
        self.assertIn('2. Two', output)
        self.assertIn('null', output)

    def test_json_errors_empty_body_nonfinite_and_duplicate_keys_rejected(self):
        meta = metadata(text_body_confirmed=True, text_body_root='articles')
        for data in (b'{"error":"AccessDenied","articles":["bad"]}', b'{"articles":[]}',
                     b'{"articles":[NaN]}', b'{"articles":[1],"articles":[2]}', b'["catalog"]',
                     b'{"articles":true}', b'{"articles":[1,2]}', b'{"articles":"Access denied"}'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                detect_text_document(data, meta)

    def test_binary_not_converted(self):
        for data in (b'%PDF-1.4', b'PK\x03\x04zip', b'\x89PNGrest', b'{\\rtf1 text}'):
            with self.subTest(data=data):
                self.assertIsNone(detect_text_document(data, metadata(text_body_confirmed=True)))

    def test_wrong_document_type_never_opens_text_conversion(self):
        for doc_type in ('case', 'directory', 'unknown'):
            self.assertIsNone(detect_text_document(HTML, metadata(document_type=doc_type, text_body_confirmed=True, text_body_selector='main')))
        self.assertIsNone(detect_text_document(CANADA))

    def test_unicode_decoding_is_strict_and_bom_safe(self):
        raw = '<LegalDocument><Text>法条</Text></LegalDocument>'
        meta = metadata(text_body_confirmed=True, text_body_root='LegalDocument')
        for encoding in ('utf-8-sig', 'utf-16', 'utf-32'):
            self.assertIn('法条', render_markdown(raw.encode(encoding), meta, 'https://official.example/a.xml').decode())
        for data in (b'<LegalDocument>\xff</LegalDocument>', b'<LegalDocument>\x00</LegalDocument>'):
            with self.assertRaises(ValueError):
                detect_text_document(data, meta)

    def test_markdown_does_not_emit_active_html_from_json(self):
        data = b'{"articles":["<script>evil()</script>"]}'
        output = render_markdown(data, metadata(text_body_confirmed=True, text_body_root='articles'), 'https://official.example').decode()
        self.assertNotIn('<script>', output)
        self.assertIn('\\<script\\>', output)


if __name__ == '__main__':
    unittest.main()
