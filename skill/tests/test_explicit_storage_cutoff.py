"""Run on deployment host: no implicit deployment target or historical cutoff."""
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from law_crawler.storage_config import validate_storage_config, storage_from_batch
from law_crawler.oss_sink import OSSSink
from law_crawler.catalog_census_common import Census, resolve_cutoff


class ExplicitConfigurationTests(unittest.TestCase):
    def destination(self, country='Canada'):
        return dict(bucket='example-law-library',endpoint='https://oss-eu-west-1.aliyuncs.com',region='eu-west-1',prefix='legal/'+country)

    def test_sink_requires_destination_before_credentials_or_network(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'cache.json'
            with self.assertRaises(TypeError):OSSSink(credentials='/missing/secret',cache_path=path)
            for key in self.destination():
                cfg=self.destination();cfg[key]=''
                with self.subTest(key=key),self.assertRaises(ValueError):
                    OSSSink(credentials='/missing/secret',cache_path=path,**cfg)
            self.assertFalse(path.exists())

    def test_destinations_do_not_inherit_bucket_region_or_country(self):
        with tempfile.TemporaryDirectory() as d:
            for country in ('Canada','European-Union'):
                cfg=self.destination(country)
                sink=OSSSink(credentials=None,cache_path=Path(d)/(country+'.json'),bucket_factory=lambda:object(),**cfg)
                self.assertEqual(sink.bucket_name,cfg['bucket'])
                self.assertEqual(sink.prefix,cfg['prefix'])
                self.assertEqual(sink._scope,dict(bucket=cfg['bucket'],endpoint=cfg['endpoint'],prefix=cfg['prefix']))

    def test_read_frozen_destination_does_not_change_plan(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'plan.json';raw=json.dumps(self.destination()).encode();p.write_bytes(raw)
            self.assertEqual(storage_from_batch(d),self.destination());self.assertEqual(p.read_bytes(),raw)
            (Path(d)/'plan.sha256').write_text(hashlib.sha256(raw).hexdigest()+'\n')
            self.assertEqual(storage_from_batch(d),self.destination())
            changed=self.destination();changed['bucket']='another-law-library';p.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):storage_from_batch(d)

    def test_reject_unsafe_destination(self):
        for field,value in [('endpoint','https://secret:password@example.com'),('endpoint','ftp://example.com'),('endpoint','https://example.com/?token=secret'),('prefix','../other'),('prefix',''),('prefix','a//b'),('bucket','Bad Bucket'),('region','')]:
            cfg=self.destination();cfg[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):validate_storage_config(cfg)

    def test_new_census_needs_valid_explicit_cutoff_before_files(self):
        with tempfile.TemporaryDirectory() as d:
            for value in (None,'2026-02-30','20261021','2026-1-01','today','2026-10-21T00:00:00'):
                root=Path(d)/('attempt-'+str(value).replace('/','-'))
                with self.subTest(value=value),self.assertRaises(ValueError):Census(root,'platform',{'example.gov'},value)
                self.assertFalse(root.exists())

    def test_resume_inherits_cutoff_without_rewriting_frozen_plan(self):
        with tempfile.TemporaryDirectory() as d:
            c=Census(d,'platform',{'example.gov'},'2024-02-29');c.db.close()
            p=Path(d)/'platform/plan.json';before=p.read_bytes()
            c=Census(d,'platform',{'example.gov'});self.assertEqual(c.cutoff,'2024-02-29');c.db.close()
            self.assertEqual(p.read_bytes(),before)
            with self.assertRaises(ValueError):Census(d,'platform',{'example.gov'},'2025-01-01')
            self.assertEqual(p.read_bytes(),before)

    def test_parent_cutoff_inherits_and_conflicts_fail(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'plan.json';p.write_text(json.dumps({'cutoff':'2025-12-31'}))
            self.assertEqual(resolve_cutoff(d,'new'),'2025-12-31')
            with self.assertRaises(ValueError):resolve_cutoff(d,'new','2025-12-30')
            sub=Path(d)/'new';sub.mkdir();(sub/'plan.json').write_text(json.dumps({'cutoff':'2024-12-31'}))
            with self.assertRaises(ValueError):resolve_cutoff(d,'new')


if __name__=='__main__':unittest.main()
