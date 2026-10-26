import json
import tempfile
import unittest
from pathlib import Path
from law_crawler.automatic_tables import reconstruct, continuation_candidates, detect, generate, sha


class AutomaticTablesTests(unittest.TestCase):
    def test_merged_cells_preserve_spans_no_duplicates(self):
        table={'xs':[0,50,100],'ys':[0,50,100],'width':100,'height':100,
               'horizontal':[(0,0,100,0),(0,50,100,50),(0,100,100,100)],
               'vertical':[(0,0,0,100),(50,50,50,100),(100,0,100,100)]}
        obs=[{'text':'merged','bbox':[.2,.1,.6,.1]},{'text':'L','bbox':[.1,.6,.1,.1]},{'text':'R','bbox':[.6,.6,.1,.1]}]
        result=reconstruct(table,obs)
        self.assertEqual(len(result['cells']),3)
        self.assertEqual(result['cells'][0]['colspan'],2)
        self.assertIn('colspan="2"',result['html'])
        self.assertEqual(result['html'].count('merged'),1)
        self.assertEqual(result['assigned_observation_indices'],[0,1,2])
        transposed={**table,'horizontal':[(0,0,100,0),(50,50,100,50),(0,100,100,100)],'vertical':[(0,0,0,100),(50,0,50,100),(100,0,100,100)]}
        result=reconstruct(transposed,[])
        self.assertEqual(result['cells'][0]['rowspan'],2)

    def test_uncertain_boundary_not_merged(self):
        table={'xs':[0,50,100],'ys':[0,50,100],'width':100,'height':100,
               'horizontal':[(0,0,100,0),(0,50,100,50),(0,100,100,100)],
               'vertical':[(0,0,0,100),(50,0,50,25),(50,50,50,100),(100,0,100,100)]}
        result=reconstruct(table,[])
        self.assertEqual(len(result['cells']),4)
        self.assertTrue(result['ambiguities'])

    def test_crosspage_candidates_never_merge(self):
        a={'columns':2,'column_edges':[.1,.5,.9],'row_edges':[.3,.9],'cells':[{'row':0,'text':'Number Product'}]}
        b={**a,'row_edges':[.1,.8]}
        links=continuation_candidates([{'page':1,'tables':[a]}, {'page':2,'tables':[b],'outside_text':['continued']}])
        self.assertEqual(len(links),1)
        self.assertEqual(links[0]['action'],'not_merged')
        self.assertTrue(links[0]['continuation_context_marker'])

    def test_detect_and_immutable_cache(self):
        try:
            from PIL import Image, ImageDraw
            import numpy
        except ImportError:
            self.skipTest('Optional numpy/Pillow not installed in test interpreter')
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);image=root/'page.png'
            drawing=Image.new('L',(400,400),255);draw=ImageDraw.Draw(drawing)
            for x in [40,200,360]:draw.line((x,40,x,360),fill=0,width=2)
            for y in [40,200,360]:draw.line((40,y,360,y),fill=0,width=2)
            drawing.save(image);self.assertEqual(len(detect(image)),1)
            raw=root/'raw.json';raw.write_text(json.dumps({'pdf_sha256':'pdf','page':1,'image_sha256':sha(image),'bbox_coordinate_system':'normalized_top_left_xywh','observations':[{'text':'word','bbox':[.2,.2,.1,.1]}]}))
            sidecar=root/'source.json';sidecar.write_text(json.dumps({'pdf_sha256':'pdf','page_count':1,'pages':[{'page':1,'image_path':str(image),'raw_ocr_path':str(raw),'text':'word','evidence':'source'}]}))
            output=root/'output';generate(sidecar,output);before=(output/'transcription.json').read_bytes()
            self.assertTrue(generate(sidecar,output)['reused'])
            self.assertEqual(before,(output/'transcription.json').read_bytes())
            (output/'table-audit.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'Cached artifact changed'):generate(sidecar,output)
