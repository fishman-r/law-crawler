import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from law_crawler.staged_pipeline import run, load, make_plan, json_hash
from law_crawler.model_client import ModelClientError


class TranslationStyleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();country=self.root/'countries/Indonesia';country.mkdir(parents=True)
        (country/'index.md').write_text('[official](https://example.gov)')
        (self.root/'countries/global-config.properties').write_text('downloadDir=downloads')
        self.source=self.root/'source.txt';self.source.write_text('Article 12. A person must file a notice.')
        self.manifest=self.root/'manifest.json';self.manifest.write_text(json.dumps({'documents':[{'id':'doc','path':str(self.source)}]}))
        self.config=self.root/'config.json';self.config.write_text(json.dumps({'provider':'ollama','base_url':'http://localhost:11434','model':'small'}))
        self.style=self.root/'style.json'
        self.profile={'id':'legal-style','version':1,'instructions':'Use mainland Chinese legislative wording and UK OPC English; retain original legal effect.','sources':[{'title':'Reference','url':'https://example.gov/guidance','scope':'style only'}],'targets':{'zh':'mainland China','en':'UK OPC'}}
        self.style.write_text(json.dumps(self.profile))
        self.args=argparse.Namespace(root=str(self.root),jurisdiction='Indonesia',manifest=str(self.manifest),model_config=str(self.config),batch_id='test',rerun_batch=None,stage='run',review=None,chunk_chars=500,max_chars=10000,max_calls=10)
        self.base=self.root/'downloads/Indonesia/pipeline'

    def styled(self):
        value=load(self.manifest);value['translation_style_path']='style.json';self.manifest.write_text(json.dumps(value))

    def model(self,*args):
        return {'output':{'zh':'第12条。当事人应当提交通知。','en':'Article 12. A person must file a notice.'},'usage':{'input_tokens':20,'output_tokens':20},'model':'small','duration_seconds':.01}

    def test_legacy_plan_and_exact_cache_key_unchanged(self):
        plan=make_plan(self.root,'Indonesia',load(self.manifest),load(self.config),500,10000,10)
        self.assertNotIn('translation_style',plan)
        with patch('law_crawler.staged_pipeline.call_json',side_effect=self.model):run(self.args)
        from law_crawler.staged_pipeline import digest, VERSION
        expected=json_hash({'version':VERSION,'processing_revision':3,'raw_sha256':digest(self.source.read_bytes()),'source_sha256':digest(self.source.read_bytes()),'model_config':plan['model_config'],'chunk_chars':500,'max_chars':10000})
        self.assertTrue((self.base/'processed'/expected).is_dir())
        request=load(self.base/'batches/test/review-request.json')
        self.assertNotIn('translation_style',request['files'][0])

    def test_both_prompts_profile_artifacts_and_frozen_replay(self):
        self.styled();prompts=[]
        def model(config,messages,schema):
            prompts.append(messages[0]['content'])
            if len(prompts)==1:raise ModelClientError('Model output is incomplete or truncated')
            return self.model()
        with patch('law_crawler.staged_pipeline.call_json',side_effect=model):run(self.args)
        self.assertEqual(len(prompts),2)
        self.assertTrue(all(self.profile['instructions'] in p for p in prompts))
        request=load(self.base/'batches/test/review-request.json');row=request['files'][0]
        self.assertEqual(row['translation_style'],self.profile)
        self.assertIn('legal_translation_style_review_required',[f['kind'] for f in row['quality_flags']])
        product=next(a for a in row['artifacts'] if a['path'].endswith('/translation.json'))
        self.assertEqual(load(Path(product['path']))['translation_style'],self.profile)
        self.style.write_text('{invalid changed external profile')
        self.args.batch_id=None;self.args.rerun_batch='test';self.args.stage='process'
        with patch('law_crawler.staged_pipeline.call_json',side_effect=AssertionError('frozen replay must reuse')):
            replay,_=run(self.args)
        self.assertEqual(replay['usage']['model_calls'],0)
        self.assertEqual(load(self.base/'batches/test/plan.json')['translation_style'],self.profile)

    def test_style_content_change_invalidates_cache(self):
        self.styled()
        with patch('law_crawler.staged_pipeline.call_json',side_effect=self.model) as mock:
            run(self.args)
            changed={**self.profile,'instructions':'Different legally precise drafting instructions.'}
            self.style.write_text(json.dumps(changed));self.args.batch_id='changed'
            run(self.args)
        self.assertEqual(mock.call_count,2)
        folders=[p for p in (self.base/'processed').iterdir() if p.is_dir()]
        self.assertEqual(len(folders),2)

    def test_profile_validation(self):
        self.styled();self.style.write_text(json.dumps({**self.profile,'targets':{'en':'UK'}}))
        with self.assertRaisesRegex(ValueError,'targets'):
            make_plan(self.root,'Indonesia',load(self.manifest),load(self.config),500,10000,10)
