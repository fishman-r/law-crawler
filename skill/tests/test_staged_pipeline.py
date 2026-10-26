import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from law_crawler.staged_pipeline import run, load, make_plan, extract_pdf


class StagedPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'countries/Indonesia').mkdir(parents=True)
        (self.root / 'countries/Indonesia/index.md').write_text('[official](https://example.gov)')
        (self.root / 'countries/global-config.properties').write_text('downloadDir=downloads')
        self.source = self.root / 'input.txt'
        self.source.write_text('Article 12. Source content.')
        self.manifest = self.root / 'manifest.json'
        self.manifest.write_text(json.dumps({'documents': [{'id': 'doc1', 'path': str(self.source)}]}))
        self.config = self.root / 'model.json'
        self.config.write_text(json.dumps({'provider': 'ollama', 'base_url': 'http://localhost:11434', 'model': 'small'}))
        self.args = argparse.Namespace(root=str(self.root), jurisdiction='Indonesia', manifest=str(self.manifest),
            model_config=str(self.config), batch_id='test', rerun_batch=None, stage='run', review=None,
            chunk_chars=500, max_chars=10000, max_calls=10)
        self.base = self.root / 'downloads/Indonesia/pipeline'

    def model(self, *args):
        return {'output': {'zh': '第12条。原始内容。', 'en': 'Article 12. Source content.'},
                'usage': {'input_tokens': 20, 'output_tokens': 20}, 'model': 'small', 'duration_seconds': .01}

    def rerun(self):
        self.args.batch_id = None
        self.args.rerun_batch = 'test'

    def test_all_sources_archived_before_first_model(self):
        from law_crawler.staged_pipeline import archive_document
        manifest = load(self.manifest)
        manifest['documents'].append({'id': 'doc2', 'path': str(self.source)})
        self.manifest.write_text(json.dumps(manifest))
        events = []
        def archive(*args):
            events.append(args[0]['id'])
            return archive_document(*args)
        def model(*args):
            self.assertEqual(events, ['doc1', 'doc2'])
            return self.model(*args)
        with patch('law_crawler.staged_pipeline.archive_document', side_effect=archive), \
             patch('law_crawler.staged_pipeline.call_json', side_effect=model):
            result, _ = run(self.args)
        self.assertEqual(result['counts']['success'], 2)

    def test_end_to_end_cache_and_review(self):
        with patch('law_crawler.staged_pipeline.call_json', side_effect=self.model) as model:
            result, paths = run(self.args)
            self.assertEqual(result['counts']['failed'], 0)
            self.assertTrue(paths['html'].is_file())
            model.assert_called_once()
        self.rerun()
        with patch('law_crawler.staged_pipeline.call_json', side_effect=AssertionError('cache must avoid call')):
            result, _ = run(self.args)
            self.assertEqual(result['usage']['reused_chunks'], 1)
        review = load(self.base / 'batches/test/review-request.json')
        review.update(reviewer='independent-agent', model='review-model', cost_usd=None, duration_seconds=12)
        for row in review['files']:
            row.update(verdict='pass', issues=[])
            self.assertEqual(Path(row['source']).read_text(), self.source.read_text())
        self.args.review = str(self.root / 'review.json')
        Path(self.args.review).write_text(json.dumps(review))
        self.args.stage = 'review-import'
        result, _ = run(self.args)
        self.assertEqual(result['files'][0]['review_status'], 'approved')
        self.assertIsNone(result['billed_cost_usd'])

    def test_render_change_keeps_reviewed_bytes_and_reuses_model_cache(self):
        from law_crawler.staged_pipeline import artifact, render_language_markdown
        with patch('law_crawler.staged_pipeline.call_json', side_effect=self.model):
            first, _ = run(self.args)
        old = first['files'][0]['artifacts']
        self.rerun()
        def changed_notice(doc, lang, content):
            return render_language_markdown(doc, lang, content) + b'\nUpdated rendering notice.\n'
        with patch('law_crawler.staged_pipeline.render_language_markdown', side_effect=changed_notice), \
             patch('law_crawler.staged_pipeline.call_json') as model:
            second, _ = run(self.args)
            self.assertEqual(second['counts']['success'], 1)
            self.assertEqual(second['usage']['reused_chunks'], 1)
            self.assertNotEqual(second['files'][0]['zh'], first['files'][0]['zh'])
            for item in old:
                self.assertEqual(artifact(item['path']), item)
            replay, _ = run(self.args)
            self.assertEqual(replay['files'][0]['artifacts'], second['files'][0]['artifacts'])
            self.assertEqual(replay['usage']['reused_chunks'], 1)
            model.assert_not_called()

    def test_reviewed_request_is_frozen_across_render_changes(self):
        from law_crawler.staged_pipeline import render_language_markdown
        with patch('law_crawler.staged_pipeline.call_json', side_effect=self.model):
            run(self.args)
        self.review_file()
        run(self.args)
        request_path = self.base / 'batches/test/review-request.json'
        before = request_path.read_bytes()
        review_path = self.base / 'batches/test/review.json'
        reviewed = review_path.read_bytes()
        self.args.stage = 'process'
        with patch('law_crawler.staged_pipeline.call_json') as model:
            same, _ = run(self.args)
            self.assertEqual(same['usage']['reused_chunks'], 1)
            self.assertEqual(request_path.read_bytes(), before)
            def changed_notice(doc, lang, content):
                return render_language_markdown(doc, lang, content) + b'\nNew notice.\n'
            with patch('law_crawler.staged_pipeline.render_language_markdown', side_effect=changed_notice):
                with self.assertRaisesRegex(ValueError, '请新建批次'):
                    run(self.args)
            model.assert_not_called()
        self.assertEqual(request_path.read_bytes(), before)
        self.assertEqual(review_path.read_bytes(), reviewed)
        self.assertTrue(list((self.base / 'batches/test/attempts').glob('*/unpublished-review-request.json')))

    def test_unreviewed_failed_request_can_recover_on_replay(self):
        with patch('law_crawler.staged_pipeline.call_json', side_effect=ValueError('transient model failure')):
            run(self.args)
        request_path = self.base / 'batches/test/review-request.json'
        self.assertTrue(load(request_path)['failed_files'])
        self.rerun()
        self.args.stage = 'process'
        with patch('law_crawler.staged_pipeline.call_json', side_effect=self.model):
            result, _ = run(self.args)
        self.assertEqual(result['counts']['success'], 1)
        self.assertFalse(load(request_path)['failed_files'])
        self.assertEqual(len(load(request_path)['files']), 1)

    def test_existing_processed_product_cannot_be_silently_overwritten(self):
        with patch('law_crawler.staged_pipeline.call_json', side_effect=self.model):
            first, _ = run(self.args)
        product = next(Path(item['path']) for item in first['files'][0]['artifacts']
                       if item['path'].endswith('/source.md'))
        product.write_bytes(b'externally changed reviewed artifact')
        self.rerun()
        with patch('law_crawler.staged_pipeline.call_json') as model:
            result, _ = run(self.args)
        self.assertEqual(result['counts']['failed'], 1)
        self.assertIn('禁止覆盖', result['files'][0]['reason'])
        self.assertEqual(product.read_bytes(), b'externally changed reviewed artifact')
        model.assert_not_called()

    def test_tampered_chunk_reprocesses_and_tampered_review_rejected(self):
        with patch('law_crawler.staged_pipeline.call_json', side_effect=self.model):
            run(self.args)
        chunk = next((self.base / 'processed').glob('*/chunk-000000.json'))
        chunk.write_text('{}')
        self.rerun()
        with patch('law_crawler.staged_pipeline.call_json', side_effect=self.model) as model:
            run(self.args)
            model.assert_called_once()
        review = load(self.base / 'batches/test/review-request.json')
        review.update(reviewer='agent', model='review-model', duration_seconds=12)
        review['files'][0].update(verdict='pass', issues=[])
        Path(review['files'][0]['zh']).write_text('changed')
        self.args.review = str(self.root / 'review.json')
        Path(self.args.review).write_text(json.dumps(review))
        self.args.stage = 'review-import'
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            run(self.args)

    def test_per_batch_call_cap_and_no_silent_failure(self):
        self.args.max_calls = 1
        self.args.chunk_chars = 10
        with patch('law_crawler.staged_pipeline.call_json', side_effect=self.model) as model:
            result, _ = run(self.args)
            self.assertEqual(result['counts']['failed'], 1)
            model.assert_called_once()
        self.rerun()
        with patch('law_crawler.staged_pipeline.call_json') as model:
            result, _ = run(self.args)
            self.assertEqual(result['counts']['failed'], 1)
            model.assert_not_called()

    def test_non_2xx_never_calls_model(self):
        self.manifest.write_text(json.dumps({'documents': [{'id': 'doc1', 'url': 'https://example.gov/doc'}]}))
        with patch('law_crawler.staged_pipeline.capture', return_value={'status_code': 403, 'complete': True}), \
             patch('law_crawler.staged_pipeline.call_json') as model:
            result, _ = run(self.args)
            self.assertEqual(result['counts']['failed'], 1)
            model.assert_not_called()

    def test_cloud_rejected_before_batch_and_source_mutation_rejected(self):
        config = load(self.config)
        config['provider'] = 'openai-compatible'
        with self.assertRaisesRegex(ValueError, '预算授权'):
            make_plan(self.root, 'Indonesia', load(self.manifest), config, 100, 1000, 2)
        self.args.stage = 'capture'
        run(self.args)
        self.source.write_text('changed')
        self.rerun()
        self.args.stage = 'run'
        with patch('law_crawler.staged_pipeline.call_json') as model:
            result, _ = run(self.args)
            self.assertEqual(result['counts']['failed'], 1)
            model.assert_not_called()

    def test_pdf_empty_page_blocks_processing(self):
        from types import SimpleNamespace
        module = SimpleNamespace(PdfReader=lambda path: SimpleNamespace(pages=[
            SimpleNamespace(extract_text=lambda: 'Article 12'), SimpleNamespace(extract_text=lambda: '')]))
        with patch.dict('sys.modules', {'pypdf': module}):
            with self.assertRaisesRegex(ValueError, 'needs_OCR'):
                extract_pdf(self.root / 'anything.pdf')

    def setup_transcription(self, count=2):
        from law_crawler.staged_pipeline import digest
        self.source = self.root / 'input.pdf'
        self.source.write_bytes(b'%PDF-fake-original')
        self.sidecar = self.root / 'pages.json'
        self.transcription = {'version': 1, 'pdf_sha256': digest(self.source.read_bytes()),
            'page_count': count, 'pages': [{'page': i, 'text': 'Article 12. Page ' + str(i),
                'method': 'ocr' if i == 1 else 'text_layer', 'producer': 'test-tool',
                'evidence': 'Original page ' + str(i)} for i in range(1, count + 1)]}
        self.sidecar.write_text(json.dumps(self.transcription))
        self.manifest.write_text(json.dumps({'documents': [{'id': 'doc1', 'path': str(self.source),
            'transcription_path': str(self.sidecar)}]}))

    def pdf_reader(self):
        from types import SimpleNamespace
        return SimpleNamespace(PdfReader=lambda path: SimpleNamespace(pages=[
            SimpleNamespace(extract_text=lambda: ''), SimpleNamespace(extract_text=lambda: 'native text')]))

    def test_transcription_mixed_pdf_provenance_and_review_binding(self):
        self.setup_transcription()
        with patch.dict('sys.modules', {'pypdf': self.pdf_reader()}), \
             patch('law_crawler.staged_pipeline.call_json', side_effect=self.model):
            result, _ = run(self.args)
        self.assertEqual(result['counts']['success'], 1)
        row = result['files'][0]
        self.assertEqual(row['extraction']['native_page_characters'], [0, 11])
        self.assertEqual(row['extraction']['pages'][1]['method'], 'text_layer')
        self.assertIn(row['extraction']['transcription'], row['artifacts'])
        self.assertIn('external_transcription_review_required', [f['kind'] for f in row['quality_flags']])
        self.assertIn('pdf_visual_review_required', [f['kind'] for f in row['quality_flags']])
        self.assertEqual(Path(row['artifacts'][0]['path']).read_bytes(), self.source.read_bytes())
        self.assertEqual(Path(row['source']).read_text(), '\n\n'.join(p['text'] for p in self.transcription['pages']))
        self.review_file()
        with self.assertRaisesRegex(ValueError, 'quality_flag'):
            run(self.args)

    def test_transcription_missing_pdf_page_never_calls_model(self):
        self.setup_transcription(count=1)
        with patch.dict('sys.modules', {'pypdf': self.pdf_reader()}), \
             patch('law_crawler.staged_pipeline.call_json') as model:
            result, _ = run(self.args)
        self.assertEqual(result['counts']['failed'], 1)
        self.assertIn('页数', result['files'][0]['reason'])
        model.assert_not_called()
        self.assertTrue(list((self.base / 'raw/objects').glob('*.pdf')))

    def test_transcription_frozen_change_rejected(self):
        self.setup_transcription()
        self.args.stage = 'capture'
        run(self.args)
        self.sidecar.write_text(self.sidecar.read_text() + ' ')
        self.rerun()
        self.args.stage = 'process'
        with patch('law_crawler.staged_pipeline.call_json') as model:
            result, _ = run(self.args)
        self.assertIn('冻结计划', result['files'][0]['reason'])
        model.assert_not_called()

    def test_transcription_wrong_pdf_hash_archives_original_but_never_calls_model(self):
        self.setup_transcription()
        self.transcription['pdf_sha256'] = '0' * 64
        self.sidecar.write_text(json.dumps(self.transcription))
        with patch('law_crawler.staged_pipeline.call_json') as model:
            result, _ = run(self.args)
        self.assertEqual(result['counts']['failed'], 1)
        model.assert_not_called()
        raw = next((self.base / 'raw/objects').glob('*.pdf'))
        self.assertEqual(raw.read_bytes(), self.source.read_bytes())

    def test_transcription_archived_tamper_rejected(self):
        self.setup_transcription()
        self.args.stage = 'capture'
        run(self.args)
        next((self.base / 'raw/transcriptions').glob('*.json')).write_text('{}')
        self.rerun()
        self.args.stage = 'process'
        with patch('law_crawler.staged_pipeline.call_json') as model:
            result, _ = run(self.args)
        self.assertIn('SHA-256', result['files'][0]['reason'])
        model.assert_not_called()

    def test_transcription_schema_rejects_gaps_and_implicit_blank(self):
        from law_crawler.staged_pipeline import validate_transcription
        self.setup_transcription()
        self.transcription['pages'][1]['page'] = 3
        with self.assertRaisesRegex(ValueError, '连续页码'):
            validate_transcription(self.transcription)
        self.transcription['pages'][1]['page'] = 2
        self.transcription['pages'][1]['text'] = ''
        with self.assertRaisesRegex(ValueError, 'blank=true'):
            validate_transcription(self.transcription)
        self.transcription['pages'][1]['blank'] = True
        validate_transcription(self.transcription)

    def test_tree_layout_survives_fallback_and_normal_markdown(self):
        from law_crawler.staged_pipeline import fallback_input
        tree = 'Root\n  ├── Unit A\n  └── Unit B\n'
        self.assertEqual(fallback_input(tree, 'id')[0], tree)
        self.source.write_text(tree)
        with patch('law_crawler.staged_pipeline.call_json', side_effect=self.model):
            result, _ = run(self.args)
        row = result['files'][0]
        source_md = next(Path(a['path']) for a in row['artifacts'] if a['path'].endswith('/source.md'))
        self.assertIn('```text\n' + tree + '```', source_md.read_text())

    def test_numeric_qa_flags_do_not_claim_approval(self):
        def model(*args):
            result = self.model()
            result['output']['zh'] = '没有数字'
            return result
        with patch('law_crawler.staged_pipeline.call_json', side_effect=model):
            result, _ = run(self.args)
        self.assertEqual(result['files'][0]['quality_flags'][0]['kind'], 'missing_numbers')
        product = next((self.base / 'processed').glob('*/document-*/translation.json'))
        self.assertEqual(load(product)['review_status'], 'pending')

    def review_file(self, **updates):
        review = load(self.base / 'batches/test/review-request.json')
        review.update(reviewer='review-agent', model='review-model', duration_seconds=12, cost_usd=None)
        for row in review['files']:
            row.update(verdict='pass', issues=[])
        review.update(updates)
        self.args.review = str(self.root / 'review.json')
        Path(self.args.review).write_text(json.dumps(review))
        self.rerun()
        self.args.stage = 'review-import'
        return review

    def test_empty_review_blocked(self):
        with patch('law_crawler.staged_pipeline.call_json', side_effect=ValueError('failed')):
            run(self.args)
        self.review_file()
        with self.assertRaisesRegex(ValueError, '空集合'):
            run(self.args)

    def test_quality_flag_requires_specific_evidence(self):
        def model(*args):
            result = self.model()
            result['output']['zh'] = '没有数字'
            return result
        with patch('law_crawler.staged_pipeline.call_json', side_effect=model):
            run(self.args)
        review = self.review_file()
        with self.assertRaisesRegex(ValueError, 'quality_flag'):
            run(self.args)
        review['files'][0]['resolved_quality_flags'] = [
            {'flag': flag, 'evidence': 'Reviewed source and translation; issue explicitly assessed.'}
            for flag in review['files'][0]['quality_flags']]
        Path(self.args.review).write_text(json.dumps(review))
        result, _ = run(self.args)
        self.assertEqual(result['files'][0]['review_status'], 'approved')

    def test_partial_review_keeps_failed_document(self):
        self.manifest.write_text(json.dumps({'documents': [
            {'id': 'doc1', 'path': str(self.source)},
            {'id': 'doc2', 'url': 'https://example.gov/bad'}]}))
        with patch('law_crawler.staged_pipeline.call_json', side_effect=self.model), \
             patch('law_crawler.staged_pipeline.capture', return_value={'status_code': 403, 'complete': True}):
            run(self.args)
        self.review_file()
        result, _ = run(self.args)
        self.assertEqual(result['counts']['failed'], 1)
        self.assertEqual(result['files'][1]['id'], 'doc2')

    def test_same_content_new_document_reuses_model_but_preserves_metadata(self):
        self.manifest.write_text(json.dumps({'documents': [
            {'id': 'doc1', 'path': str(self.source), 'document': {'published_at': None}},
            {'id': 'doc2', 'path': str(self.source), 'document': {'title': 'Other identity'}}]}))
        with patch('law_crawler.staged_pipeline.call_json', side_effect=self.model) as model:
            result, _ = run(self.args)
        model.assert_called_once()
        self.assertEqual(result['usage']['reused_chunks'], 1)
        products = list((self.base / 'processed').glob('*/document-*/translation.json'))
        self.assertEqual(len(products), 2)
        metadata = {load(path)['id']: load(path)['metadata']['document'] for path in products}
        self.assertIsNone(metadata['doc1']['published_at'])
        self.assertEqual(metadata['doc2']['title'], 'Other identity')

    def test_split_preserves_all_characters_and_xml_names(self):
        from law_crawler.staged_pipeline import split_chunks, extract
        text = 'First paragraph.\n\nSecond paragraph with many words.\nThird line.'
        chunks = list(split_chunks(text, 25))
        self.assertEqual(''.join(piece for _, piece in chunks), text)
        self.assertTrue(all(len(piece) <= 25 for _, piece in chunks))
        xml = self.root / 'source.xml'
        xml.write_text('<root><year>2026</year><id>12</id></root>')
        self.assertIn('<year>2026</year>', extract(xml))
        html = self.root / 'source.html'
        html.write_text('<table><tr><td>Year</td><td>2026</td></tr></table>')
        self.assertIn('Year | 2026', extract(html))

    def test_failed_call_has_unknown_tokens_and_remote_ollama_rejected(self):
        with patch('law_crawler.staged_pipeline.call_json', side_effect=ValueError('timeout')):
            result, _ = run(self.args)
        self.assertTrue(result['usage']['tokens_unknown'])
        config = load(self.config)
        config['base_url'] = 'https://remote.example'
        with self.assertRaisesRegex(ValueError, '预算授权'):
            make_plan(self.root, 'Indonesia', load(self.manifest), config, 100, 1000, 2)

    def test_process_reads_archive_without_network(self):
        self.args.stage = 'capture'
        run(self.args)
        self.source.unlink()
        self.rerun()
        self.args.stage = 'process'
        with patch('law_crawler.staged_pipeline.capture', side_effect=AssertionError('network forbidden')), \
             patch('law_crawler.staged_pipeline.call_json', side_effect=self.model):
            result, _ = run(self.args)
        self.assertEqual(result['counts']['failed'], 0)

    def test_rejected_translation_still_records_known_usage(self):
        def model(*args):
            result = self.model()
            result['output']['zh'] = ''
            return result
        with patch('law_crawler.staged_pipeline.call_json', side_effect=model):
            result, _ = run(self.args)
        self.assertEqual(result['counts']['failed'], 1)
        self.assertEqual(result['usage']['input_tokens'], 20)
        self.assertEqual(result['usage']['output_tokens'], 20)
        self.assertFalse(result['usage']['tokens_unknown'])

    def test_html_ordered_list_numbering_and_complex_structure_flag(self):
        from law_crawler.staged_pipeline import extract, TextHTML
        html = self.root / 'source.html'
        html.write_text('<ol start="3"><li>Third<ul><li>Nested bullet</li></ul></li><li value="7">Seventh</li><li>Eighth</li></ol>')
        text = extract(html)
        self.assertIn('3. Third', text)
        self.assertIn('7. Seventh', text)
        self.assertIn('8. Eighth', text)
        self.assertNotIn('4. Nested', text)
        parser = TextHTML()
        parser.feed('<ol type="I" reversed start="4"><li value="3">Third</li></ol>')
        self.assertTrue(parser.structure_review_required)
        self.assertIn('<ol type="I" reversed start="4">', ''.join(parser.parts))
        self.assertIn('<li value="3">', ''.join(parser.parts))
        self.assertIn('</li></ol>', ''.join(parser.parts).replace('\n', ''))

    def test_truncation_fallback_is_bounded_recorded_and_cached(self):
        from law_crawler.model_client import ModelClientError
        with patch('law_crawler.staged_pipeline.call_json', side_effect=[
                ModelClientError('Model output is incomplete or truncated'), self.model()]) as model:
            result, _ = run(self.args)
        self.assertEqual(result['counts']['failed'], 0)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(result['usage']['model_calls'], 2)
        self.assertTrue(result['usage']['tokens_unknown'])
        chunk = load(next((self.base / 'processed').glob('*/chunk-000000.json')))
        self.assertEqual(chunk['prompt_variant'], 'truncation_fallback')
        events = sorted((self.base / 'batches/test/attempts/0001/events').glob('model-*.json'))
        self.assertEqual(len(events), 2)
        self.assertEqual(load(events[0])['status'], 'failed')
        self.assertIsNone(load(events[0])['usage']['input_tokens'])
        self.assertEqual(load(events[1])['prompt_variant'], 'truncation_fallback')
        self.rerun()
        with patch('law_crawler.staged_pipeline.call_json') as model:
            result, _ = run(self.args)
        model.assert_not_called()
        self.assertEqual(result['usage']['reused_chunks'], 1)

    def test_truncation_fallback_stops_at_second_try_and_batch_cap(self):
        from law_crawler.model_client import ModelClientError
        with patch('law_crawler.staged_pipeline.call_json', side_effect=ModelClientError('Model output is incomplete or truncated')) as model:
            result, _ = run(self.args)
        self.assertEqual(result['counts']['failed'], 1)
        self.assertEqual(model.call_count, 2)
        self.args.batch_id = 'cap'
        self.args.max_calls = 1
        with patch('law_crawler.staged_pipeline.call_json', side_effect=ModelClientError('Model output is incomplete or truncated')) as model:
            result, _ = run(self.args)
        self.assertEqual(result['counts']['failed'], 1)
        model.assert_called_once()

    def test_non_truncation_error_never_retries(self):
        from law_crawler.model_client import ModelClientError
        with patch('law_crawler.staged_pipeline.call_json', side_effect=ModelClientError('Model returned invalid JSON')) as model:
            result, _ = run(self.args)
        self.assertEqual(result['counts']['failed'], 1)
        model.assert_called_once()

    def test_fallback_normalization_keeps_source_and_marks_only_id_glyphs(self):
        from law_crawler.staged_pipeline import fallback_input
        source = '  Indonesian body\n\n  Œ  \nDate Ж 2026\nПример\nÉcole\n'
        model_input, changes = fallback_input(source, 'id')
        self.assertIn('[unreadable source glyph U+0152]', model_input)
        self.assertIn('[unreadable source glyph U+0416]', model_input)
        self.assertIn('École', model_input)
        for item in changes:
            if item['kind'] == 'unknown_source_glyph':
                self.assertEqual(source[item['position']], item['character'])
                self.assertEqual(item['codepoint'], 'U+%04X' % ord(item['character']))
        untouched, other_changes = fallback_input(source, 'ru')
        self.assertIn('Пример', untouched)
        self.assertIn('Œ', untouched)
        self.assertTrue(all(item['kind'] == 'whitespace' for item in other_changes))
        self.assertNotIn('\n\n', model_input)
        self.assertTrue(source.startswith('  Indonesian'))

    def test_fallback_adds_review_flags_and_preserves_exact_source(self):
        from law_crawler.model_client import ModelClientError
        self.source.write_text('Article 12\n\nŒ\nDate Ж 2026')
        self.manifest.write_text(json.dumps({'documents': [{'id': 'doc1', 'path': str(self.source), 'source_language': 'id'}]}))
        with patch('law_crawler.staged_pipeline.call_json', side_effect=[
                ModelClientError('Model output is incomplete or truncated'), self.model()]) as model:
            result, _ = run(self.args)
        normalized = model.call_args_list[1].args[1][1]['content']
        self.assertIn('[unreadable source glyph U+0152]', normalized)
        row = result['files'][0]
        self.assertEqual(Path(row['source']).read_text(), self.source.read_text())
        kinds = {flag['kind'] for flag in row['quality_flags']}
        self.assertTrue({'model_input_normalized', 'unknown_source_glyphs', 'missing_unreadable_markers'} <= kinds)
        chunk = load(next((self.base / 'processed').glob('*/chunk-000000.json')))
        import hashlib
        self.assertEqual(chunk['model_input_sha256'], hashlib.sha256(normalized.encode()).hexdigest())

    def test_untranslated_indonesian_copy_gets_language_flags_even_on_cache_reuse(self):
        text = ('Peraturan Menteri Perdagangan Republik Indonesia Nomor 24 Tahun 2026. '
                'Ketentuan pelaksanaan peraturan ini berlaku sejak tanggal ditetapkan.')
        self.source.write_text(text)
        self.manifest.write_text(json.dumps({'documents': [{'id': 'doc1', 'path': str(self.source), 'source_language': 'id'}]}))
        response = self.model()
        response['output'] = {'zh': text, 'en': text}
        with patch('law_crawler.staged_pipeline.call_json', return_value=response):
            result, _ = run(self.args)
        flags = result['files'][0]['quality_flags']
        self.assertTrue(any(flag['kind'] == 'target_language_missing' and flag['language'] == 'zh' for flag in flags))
        self.assertEqual({flag['language'] for flag in flags if flag['kind'] == 'untranslated_source_copy'}, {'zh', 'en'})
        zh_before = Path(result['files'][0]['zh']).read_bytes()
        self.rerun()
        with patch('law_crawler.staged_pipeline.call_json') as model:
            result, _ = run(self.args)
        model.assert_not_called()
        self.assertEqual(Path(result['files'][0]['zh']).read_bytes(), zh_before)
        self.assertEqual(result['files'][0]['quality_flags'], flags)

    def prepare_repair(self):
        from law_crawler.staged_pipeline import artifact
        with patch('law_crawler.staged_pipeline.call_json', side_effect=self.model):
            run(self.args)
        review = self.review_file()
        review['files'][0].update(verdict='needs_revision', issues=[{'id': 'I1', 'description': 'Correct source'}, {'id': 'I2', 'description': 'Correct translation'}])
        Path(self.args.review).write_text(json.dumps(review))
        run(self.args)
        row = review['files'][0]
        files = {}
        for lang, text in {'source': 'Article 12. Corrected source.', 'zh': '第12条。校正后的原始内容。', 'en': 'Article 12. Corrected source.'}.items():
            path = self.root / ('repair-' + lang + '.txt')
            path.write_text(text)
            files[lang] = artifact(path)
        repair = {'parent_batch': 'test', 'reviewer_or_editor': 'editor', 'model': 'editor-model',
                  'duration_seconds': None, 'cost_usd': None, 'files': [dict(id='doc1', base_artifacts=row['artifacts'], **files,
                  changes=[{'issue_id': 'I1', 'description': 'Fixed source', 'evidence': [{'page': 1, 'detail': 'Compared original page'}]},
                           {'issue_id': 'I2', 'description': 'Fixed both translations', 'evidence': []}])]}
        self.args.stage = 'repair-import'
        self.args.batch_id, self.args.rerun_batch = 'revision', None
        self.args.parent_batch = 'test'
        self.args.repair = str(self.root / 'repair.json')
        Path(self.args.repair).write_text(json.dumps(repair))
        return repair

    def test_repair_is_immutable_offline_and_separately_reviewed(self):
        from law_crawler.staged_pipeline import artifact
        repair = self.prepare_repair()
        old = {item['path']: Path(item['path']).read_bytes() for item in repair['files'][0]['base_artifacts']}
        with patch('law_crawler.staged_pipeline.call_json') as model, patch('law_crawler.staged_pipeline.capture') as capture:
            result, paths = run(self.args)
        model.assert_not_called()
        capture.assert_not_called()
        self.assertTrue(paths['html'].exists())
        self.assertGreaterEqual(result['duration_seconds'], 0)
        self.assertEqual(result['duration_seconds'], result['import_duration_seconds'])
        self.assertIsNone(result['editor_duration_seconds'])
        self.assertIsNone(result['editor_cost_usd'])
        self.assertEqual(result['billed_cost_usd'], 0)
        self.assertEqual(result['files'][0]['review_status'], 'pending')
        self.assertEqual(old, {path: Path(path).read_bytes() for path in old})
        products = list((self.base / 'revisions/revision').rglob('*.*'))
        before = [artifact(path) for path in products]
        self.args.batch_id, self.args.rerun_batch = None, 'revision'
        Path(self.args.repair).unlink()  # Rerun consumes frozen plan, not mutable manifest.
        with patch('law_crawler.staged_pipeline.call_json') as model, patch('law_crawler.staged_pipeline.capture') as capture:
            result, _ = run(self.args)
        model.assert_not_called()
        capture.assert_not_called()
        self.assertEqual(before, [artifact(path) for path in products])
        request = load(self.base / 'batches/revision/review-request.json')
        request.update(reviewer='editor', model='independent-model', duration_seconds=10, cost_usd=None)
        request['files'][0].update(verdict='pass', issues=[])
        Path(self.args.review).write_text(json.dumps(request))
        self.args.stage = 'review-import'
        with self.assertRaisesRegex(ValueError, 'reviewer'):
            run(self.args)
        request['reviewer'] = 'independent-agent'
        Path(self.args.review).write_text(json.dumps(request))
        with self.assertRaisesRegex(ValueError, 'quality_flag'):
            run(self.args)
        request['files'][0]['resolved_quality_flags'] = [{'flag': flag, 'evidence': 'Compared original page and all three revised files.'} for flag in request['files'][0]['quality_flags']]
        Path(self.args.review).write_text(json.dumps(request))
        result, _ = run(self.args)
        self.assertEqual(result['files'][0]['review_status'], 'approved')
        self.assertEqual(old, {path: Path(path).read_bytes() for path in old})
        self.args.stage = 'process'
        with self.assertRaisesRegex(ValueError, '禁止运行旧处理'):
            run(self.args)

    def test_repair_rejects_missing_issue_and_source_without_page_evidence(self):
        repair = self.prepare_repair()
        changes = repair['files'][0]['changes']
        repair['files'][0]['changes'] = changes[:1]
        Path(self.args.repair).write_text(json.dumps(repair))
        with self.assertRaisesRegex(ValueError, 'issues'):
            run(self.args)
        self.assertFalse((self.base / 'batches/revision').exists())
        repair['files'][0]['changes'] = changes
        changes[0]['evidence'] = []
        Path(self.args.repair).write_text(json.dumps(repair))
        with self.assertRaisesRegex(ValueError, '页证据'):
            run(self.args)

    def test_repair_rejects_input_base_and_output_tampering(self):
        repair = self.prepare_repair()
        path = Path(repair['files'][0]['zh']['path'])
        original = path.read_bytes()
        path.write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            run(self.args)
        path.write_bytes(original)
        run(self.args)
        self.args.batch_id, self.args.rerun_batch = None, 'revision'
        target = self.base / 'revisions/revision/doc1/zh.txt'
        target.write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            run(self.args)
        target.write_bytes(original)
        base = Path(repair['files'][0]['base_artifacts'][0]['path'])
        base.write_text('tampered original')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            run(self.args)

    def test_repair_markdown_preserves_organisation_tree_without_changing_txt(self):
        from law_crawler.staged_pipeline import artifact
        repair = self.prepare_repair()
        tree = 'KEPALA KDEI\n└── BIDANG PERDAGANGAN\n    ├── SEKSI A\n    └── SEKSI B'
        text = 'Lampiran\n\n' + tree + '\n\nPenutup\n'
        for lang in ('source', 'zh', 'en'):
            path = Path(repair['files'][0][lang]['path'])
            path.write_text(text)
            repair['files'][0][lang] = artifact(path)
        Path(self.args.repair).write_text(json.dumps(repair))
        run(self.args)
        for lang in ('source', 'zh', 'en'):
            folder = self.base / 'revisions/revision/doc1'
            self.assertEqual((folder / (lang + '.txt')).read_text(), text)
            markdown = (folder / (lang + '.md')).read_text()
            self.assertIn('```text\n' + tree + '\n```', markdown)
            self.assertIn('```\n\nPenutup', markdown)

    def test_lettered_article_and_slash_identifier_loss_is_flagged(self):
        self.source.write_text('Pasal 16A. Peraturan 24/M-DAG/PER/5/2016.')
        response = self.model()
        response['output'] = {'zh': '第16条。法规24号，5月，2016年。',
                              'en': 'Article 16A. Regulation 24/M-DAG/PER/5/2016.'}
        with patch('law_crawler.staged_pipeline.call_json', return_value=response):
            result, _ = run(self.args)
        flags = result['files'][0]['quality_flags']
        matching = [flag for flag in flags if flag['kind'] == 'missing_legal_identifiers']
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['language'], 'zh')
        self.assertEqual(matching[0]['values'], ['16A', '24/M-DAG/PER/5/2016'])
        self.assertFalse(any(flag['kind'] == 'missing_numbers' for flag in flags))
        self.assertEqual(result['counts']['failed'], 0)  # Flag requires review, not automatic rejection.
        self.rerun()
        with patch('law_crawler.staged_pipeline.call_json') as model:
            rerun, _ = run(self.args)
        model.assert_not_called()
        self.assertEqual(rerun['files'][0]['quality_flags'], flags)

    def test_repair_rerun_reports_current_import_time_and_historical_editor_cost(self):
        repair = self.prepare_repair()
        repair.update(duration_seconds=210, cost_usd=1.25)
        Path(self.args.repair).write_text(json.dumps(repair))
        for attempt in range(2):
            with patch('law_crawler.staged_pipeline.time.monotonic', side_effect=[100.0, 102.5]):
                result, _ = run(self.args)
            self.assertEqual(result['duration_seconds'], 2.5)
            self.assertEqual(result['import_duration_seconds'], 2.5)
            self.assertEqual(result['editor_duration_seconds'], 210)
            self.assertEqual(result['editor_cost_usd'], 1.25)
            self.assertEqual(result['billed_cost_usd'], 0)
            self.assertEqual(result['usage'], {'model_calls': 0, 'download_calls': 0})
            self.args.batch_id, self.args.rerun_batch = None, 'revision'
