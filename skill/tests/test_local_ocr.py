import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from law_crawler.local_ocr import generate, observation_text, probe, sha256, validate_cache, write_json


class LocalOCRTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pdf = self.root / 'source.pdf'
        self.pdf.write_bytes(b'%PDF-1.7\noriginal immutable bytes')

    def test_cached_artifact_tampering_rejected(self):
        output = self.root / 'ocr'
        output.mkdir()
        artifact = output / 'transcription.json'
        artifact.write_text('{}')
        write_json(output / 'receipt.json', {'status': 'completed', 'pdf_sha256': 'pdf', 'config_sha256': 'cfg',
                   'artifacts': [{'path': artifact.name, 'sha256': sha256(artifact)}]})
        self.assertTrue(validate_cache(output, 'pdf', 'cfg')['reused'])
        artifact.write_text('{"changed": true}')
        with self.assertRaisesRegex(RuntimeError, 'changed or missing'):
            validate_cache(output, 'pdf', 'cfg')

    def test_failed_backend_preserves_original_and_records_failure(self):
        output = self.root / 'ocr'
        with patch('law_crawler.local_ocr.probe', side_effect=RuntimeError('No installed OCR')):
            with self.assertRaisesRegex(RuntimeError, 'No installed OCR'):
                generate(self.pdf, output, 'tesseract', ['ind'])
        self.assertEqual((output / 'original.pdf').read_bytes(), self.pdf.read_bytes())
        self.assertEqual(json.loads((output / 'receipt.json').read_text())['status'], 'failed')
        original_receipt = (output / 'receipt.json').read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            generate(self.pdf, output, 'tesseract', ['ind'])
        self.assertEqual(original_receipt, (output / 'receipt.json').read_bytes())

    def test_tesseract_words_keep_line_relationship(self):
        raw = {'observations': [{'text': 'Article', 'line_id': [1, 1, 1]},
                                {'text': '12', 'line_id': [1, 1, 1]},
                                {'text': 'Next', 'line_id': [1, 1, 2]}]}
        self.assertEqual(observation_text(raw), 'Article 12\nNext')

    def test_vision_rejected_off_macos(self):
        with patch('law_crawler.local_ocr.platform.system', return_value='Linux'):
            with self.assertRaisesRegex(RuntimeError, 'requires macOS'):
                probe('apple-vision', ['id-ID'])

    def test_empty_ocr_never_implicitly_blank(self):
        output = self.root / 'ocr'
        def fake_run(args, timeout=180):
            if args[0] == 'pdfinfo':
                return 'Pages: 1\n'
            Path(args[-1] + '.png').write_bytes(b'image')
            return ''
        with patch('law_crawler.local_ocr.probe', return_value={'engine': 'tesseract'}), \
             patch('law_crawler.local_ocr.executable', side_effect=lambda name: name), \
             patch('law_crawler.local_ocr.run', side_effect=fake_run), \
             patch('law_crawler.local_ocr.subprocess.run') as version, \
             patch('law_crawler.local_ocr.recognize', return_value={'observations': [], 'duration_seconds': 0.1}):
            version.return_value.stdout = 'pdftoppm test'
            version.return_value.stderr = ''
            with self.assertRaisesRegex(RuntimeError, 'blank status cannot be inferred'):
                generate(self.pdf, output, 'tesseract', ['ind'])
        self.assertFalse((output / 'transcription.json').exists())
        self.assertEqual(json.loads((output / 'receipt.json').read_text())['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
