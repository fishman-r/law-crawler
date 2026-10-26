import json
import tempfile
import unittest
from pathlib import Path
from law_crawler.reconstruct_ocr_tables import build_table, reconstruct, digest


class OCRTableTests(unittest.TestCase):
    def test_multiline_missing_and_boundary_are_preserved(self):
        raw = {'bbox_coordinate_system': 'normalized_top_left_xywh', 'observations': [
            {'text': 'after', 'bbox': [.1,.8,.1,.02]},
            {'text': ' second ', 'bbox': [.1,.2,.1,.02]},
            {'text': 'first', 'bbox': [.1,.1,.1,.02]},
            {'text': 'cross', 'bbox': [.48,.1,.1,.02]},
            {'text': 'outside overlap', 'bbox': [.89,.1,.1,.02]}]}
        grid = {'column_edges':[0,.5,.9], 'row_edges':[0,.4,.6]}
        text, audit = build_table(raw, grid)
        self.assertIn('first<br> second ', text)
        self.assertIn('[MISSING:', text)
        self.assertIn('AMBIGUOUS BBOX', text)
        self.assertIn('outside overlap', text)
        self.assertEqual(set(audit['assigned_observation_indices']) | set(audit['outside_observation_indices']), set(range(5)))
        self.assertEqual(len(audit['ambiguities']), 2)

    def test_reuse_and_reference_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / 'page.png'; image.write_bytes(b'image')
            raw = root / 'page.json'
            raw.write_text(json.dumps({'page':1,'pdf_sha256':'pdf','image_sha256':digest(image),'bbox_coordinate_system':'normalized_top_left_xywh','observations':[{'text':'word','bbox':[.1,.1,.1,.1]}]}))
            sidecar = root / 'source.json'
            sidecar.write_text(json.dumps({'pdf_sha256':'pdf','pages':[{'page':1,'raw_ocr_path':str(raw),'image_path':str(image),'text':'word','evidence':'original'}]}))
            grid = root / 'grid.json'
            grid.write_text(json.dumps({'grid_source':'human_confirmed','tables':[{'page':1,'column_edges':[0,1],'row_edges':[0,1]}]}))
            out = root / 'out'
            receipt = reconstruct(sidecar, grid, out)
            before = (out / 'transcription.json').read_bytes()
            self.assertTrue(reconstruct(sidecar, grid, out)['reused'])
            self.assertEqual(before, (out / 'transcription.json').read_bytes())
            self.assertEqual(receipt['sources']['sidecar_sha256'], digest(sidecar))
            value = json.loads(raw.read_text()); value['pdf_sha256']='other'
            raw.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, 'binding mismatch'):
                reconstruct(sidecar, grid, out)

    def test_bad_grid_rejected(self):
        with self.assertRaises(ValueError):
            build_table({'bbox_coordinate_system':'normalized_top_left_xywh','observations':[]}, {'column_edges':[.5,.1],'row_edges':[0,1]})
