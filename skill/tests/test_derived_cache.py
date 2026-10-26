"""Failed/stale/modified caches must remain immutable and cannot be recycled."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from law_crawler import derived_cache as dc
from law_crawler import automatic_tables as at


class DerivedCacheTests(unittest.TestCase):
    def fixture(self,folder,config=None,status='completed_needs_review'):
        folder.mkdir(parents=True)
        data=folder/'transcription.json';data.write_text('derived output')
        receipt={'status':status,'config':config or {'implementation':'old'},
                 'artifacts':[{'path':data.name,'sha256':dc.file_sha256(data)}]}
        (folder/'receipt.json').write_text(json.dumps(receipt))
        return receipt

    def test_stale_completed_cache_and_failed_cache_are_preserved(self):
        with tempfile.TemporaryDirectory() as name:
            base=Path(name)/'tables';self.fixture(base)
            old=(base/'receipt.json').read_bytes()
            second=base.with_name('tables-attempt-2');self.fixture(second,{'implementation':'new'},status='failed')
            selected,audit=dc.select_output_directory(base,{'config':{'implementation':'new'}},completed_statuses=('completed_needs_review',))
            self.assertEqual(selected,base.with_name('tables-attempt-3'))
            self.assertEqual(len(audit['skipped']),2)
            self.assertFalse(selected.exists())
            self.assertEqual((base/'receipt.json').read_bytes(),old)

    def test_compatible_cache_reused_but_modified_artifact_is_isolated(self):
        with tempfile.TemporaryDirectory() as name:
            base=Path(name)/'ocr';self.fixture(base,{'implementation':'new'},'completed')
            selected,audit=dc.select_output_directory(base,{'config':{'implementation':'new'}})
            self.assertEqual(selected,base)
            self.assertTrue(audit['reuse_eligible'])
            (base/'transcription.json').write_text('changed')
            selected,audit=dc.select_output_directory(base,{'config':{'implementation':'new'}})
            self.assertEqual(selected,base.with_name('ocr-attempt-2'))
            self.assertIn('SHA-256',audit['skipped'][0]['reason'])

    def test_corrupt_receipt_missing_manifest_and_escape_are_not_reused(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);base=root/'cache';self.fixture(base)
            receipt=json.loads((base/'receipt.json').read_text())
            receipt['artifacts'][0]['path']='../secret'
            (base/'receipt.json').write_text(json.dumps(receipt))
            selected,_=dc.select_output_directory(base,{'config':{'implementation':'old'}},completed_statuses=('completed_needs_review',))
            self.assertNotEqual(selected,base)
            (base/'receipt.json').write_text('{invalid json private body')
            _,audit=dc.select_output_directory(base,{'config':{'implementation':'old'}})
            self.assertEqual(audit['skipped'][0]['reason'],'JSONDecodeError')
            self.assertNotIn('invalid json private body',json.dumps(audit))
            with self.assertRaisesRegex(ValueError,'candidate limit'):
                dc.select_output_directory(base,{'config':{'implementation':'old'}},max_candidates=1)

    def test_generate_compatible_uses_actual_new_output_and_does_not_loop_stale_cache(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);base=root/'tables';self.fixture(base,{'implementation':'old'})
            before=(base/'receipt.json').read_bytes()
            wanted={'implementation':'new'};created=[]
            def producer(sidecar,output,threshold):
                created.append(output)
                if output.exists():
                    return {**json.loads((output/'receipt.json').read_text()),'reused':True}
                return {**self.fixture(output,wanted),'reused':False}
            with patch.object(at,'input_configuration',return_value=({},[],wanted)),patch.object(at,'generate',side_effect=producer):
                result=at.generate_compatible(root/'source.json',base)
                rerun=at.generate_compatible(root/'source.json',base)
            self.assertEqual(result['output_directory'],str(base.with_name('tables-attempt-2')))
            self.assertEqual(rerun['output_directory'],result['output_directory'])
            self.assertTrue(rerun['reused'])
            self.assertEqual((base/'receipt.json').read_bytes(),before)
            self.assertTrue((Path(result['output_directory'])/'cache-selection.json').is_file())
            self.assertEqual(created,[Path(result['output_directory'])]*2)


if __name__=='__main__':unittest.main()
