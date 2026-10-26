"""对组织图及真实重叠保留OCR，只有完全相同的观测格线候选才去重。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from law_crawler import automatic_tables as at


def grid():
    return {'xs':[0,50,100],'ys':[0,50,100],'width':100,'height':100,
            'horizontal':[(0,0,100,0),(0,50,100,50),(0,100,100,100)],
            'vertical':[(0,0,0,100),(50,0,50,100),(100,0,100,100)]}


class AutomaticTableIsolationTests(unittest.TestCase):
    def test_only_identical_geometry_and_line_sets_are_deduplicated(self):
        original=grid();duplicate=copy.deepcopy(original)
        duplicate['vertical'].reverse()
        unique,removed=at.deduplicate_exact_candidates([original,duplicate])
        self.assertEqual(len(unique),1)
        self.assertEqual(removed[0]['reason'],'identical_geometry_and_observed_line_sets')
        different=copy.deepcopy(original);different['vertical'][1]=(50,10,50,100)
        self.assertEqual(len(at.deduplicate_exact_candidates([original,different])[0]),2)

    def test_overlapping_distinct_candidates_preserve_whole_page(self):
        first=grid();second=copy.deepcopy(first);second['xs']=[0,60,100]
        observations=[{'text':'BAGIAN UMUM','bbox':[.1,.1,.1,.1]}]
        with patch.object(at,'detect',return_value=[first,second]):
            tables,duplicates,issue,rejected=at.prepare_page_tables(Path('unused'),observations,170)
        self.assertEqual(tables,[])
        self.assertFalse(duplicates)
        self.assertEqual(issue['kind'],'overlapping_table_candidates_unresolved')
        self.assertEqual(len(rejected),2)
        self.assertEqual(rejected[0]['assigned_observation_indices'],[0])

    def test_nested_bounds_without_ocr_are_still_unresolved(self):
        outer=grid();inner=copy.deepcopy(outer);inner['xs']=[10,30,70];inner['ys']=[10,30,70]
        with patch.object(at,'detect',return_value=[outer,inner]):
            self.assertEqual(at.prepare_page_tables(Path('unused'),[],170)[2]['kind'],'overlapping_table_candidates_unresolved')

    def test_organization_heading_never_becomes_a_table(self):
        observations=[{'text':'BAGAN SUSUNAN ORGANISASI BIRO UMUM','bbox':[.1,.1,.5,.1]},
                      {'text':'BAGIAN UMUM','bbox':[.4,.3,.2,.1]}]
        with patch.object(at,'detect',side_effect=AssertionError('Do not infer organizational relations from a table grid')):
            tables,duplicates,issue,rejected=at.prepare_page_tables(Path('unused'),observations,170)
        self.assertFalse(tables)
        self.assertEqual(issue['kind'],'organization_diagram_unresolved')
        self.assertIsNone(at.organization_heading([{'text':'Bagan susunan organisasi diatur dalam lampiran.'}]))

    def fixture(self,root,texts):
        pages=[]
        for n,text in enumerate(texts,1):
            image=root/f'page-{n}.png';image.write_bytes(b'image fixture '+str(n).encode())
            observations=[{'text':line,'bbox':[.1,.1+index*.1,.2,.05]} for index,line in enumerate(text.split('\n'))]
            raw=root/f'page-{n}.ocr.json'
            at.save(raw,{'pdf_sha256':'rawpdf','page':n,'image_sha256':at.sha(image),
                         'bbox_coordinate_system':'normalized_top_left_xywh','observations':observations})
            pages.append({'page':n,'text':text,'evidence':'unreviewed source','image_path':str(image),'raw_ocr_path':str(raw)})
        source=root/'transcription.json';at.save(source,{'page_count':len(pages),'pdf_sha256':'rawpdf','pages':pages})
        return source

    def test_isolation_keeps_text_exact_and_other_pages_continue(self):
        try:
            import numpy,PIL
        except ImportError:
            self.skipTest('numpy/Pillow optional runtime dependencies unavailable')
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);original='BAGAN SUSUNAN ORGANISASI BIRO UMUM\nBAGIAN UMUM\nSUBBAGIAN BARANG'
            source=self.fixture(root,[original,'plain next page'])
            output=root/'new-output'
            with patch.object(at,'detect',return_value=[]):
                receipt=at.generate(source,output)
                self.assertTrue(at.generate(source,output)['reused'])
            result=json.loads((output/'transcription.json').read_text())
            self.assertEqual(result['pages'][0]['text'],original)
            self.assertEqual(result['pages'][1]['text'],'plain next page')
            self.assertEqual(receipt['page_count'],2)
            self.assertEqual(receipt['unresolved_visual_structure_count'],1)
            self.assertEqual(receipt['table_count'],0)
            issue=result['visual_structure_issues'][0]
            self.assertEqual(set(issue),{'page','kind','reason','raw_ocr_path','image_path'})
            self.assertEqual(issue['page'],1)
            audit=json.loads((output/'table-audit.json').read_text())
            self.assertEqual(audit['pages'][0]['outside_observation_indices'],[0,1,2])
            self.assertFalse(audit['pages'][0]['tables'])

    def test_failed_prior_output_is_never_overwritten(self):
        try:
            import numpy,PIL
        except ImportError:
            self.skipTest('numpy/Pillow optional runtime dependencies unavailable')
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);source=self.fixture(root,['plain text'])
            old=root/'failed';old.mkdir();at.save(old/'receipt.json',{'status':'failed','error':'Overlapping detected tables'})
            before=(old/'receipt.json').read_bytes()
            with self.assertRaisesRegex(ValueError,'new output directory'):
                at.generate(source,old)
            self.assertEqual((old/'receipt.json').read_bytes(),before)
            with patch.object(at,'detect',return_value=[]):
                self.assertEqual(at.generate(source,root/'new-cache')['unresolved_visual_structure_count'],0)


if __name__=='__main__':
    unittest.main()
