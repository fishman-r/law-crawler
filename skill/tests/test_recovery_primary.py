"""Run only on the deployment test host; replacement primary end-to-end fixtures."""
import copy
import io
import json
from pathlib import Path
import sqlite3
import unittest

import test_recovery_workflow as fixtures
from law_crawler.recovery_validation import digest
from law_crawler.recovery_workflow import verify_review_evidence
from law_crawler.verified_source_recovery import apply_verified_recovery
from law_crawler.recovery_audit import audit_recovery


def encoded(x):
    return json.dumps(x, sort_keys=True).encode()


class Sink:
    prefix = 'ods/Indonesia'
    bucket_name = 'law-crawler'

    def __init__(self):
        self.objects = {}; self.reads = []; self.writes = []

    def _bucket(self):
        return self

    def get_object(self, key):
        self.reads.append(key)
        return io.BytesIO(self.objects[key])

    def read_verified_archive_metadata(self, key, sha):
        raw = self.get_object(key).read()
        if digest(raw) != sha:
            raise ValueError('metadata bytes mismatch')
        return json.loads(raw)

    def verify_original(self, receipt):
        raw = self.get_object(receipt['oss_raw_key']).read()
        if digest(raw) != receipt['sha256'] or len(raw) != receipt['size_bytes']:
            raise ValueError('original bytes mismatch')
        return dict(status='reused_verified', raw_key=receipt['oss_raw_key'],
                    sha256=receipt['sha256'], size_bytes=len(raw))

    def put_metadata(self, key, outcome, **kw):
        raw = encoded(dict(schema_version=1, batch_id=kw['batch_id'], detail_key=key, document=outcome))
        sha = digest(raw)
        path = f'{self.prefix}/metadata/{kw["batch_id"]}/{digest(key.encode())}/{sha}.json'
        self.objects[path] = raw; self.writes.append(path)
        return dict(status='uploaded_verified', metadata_key=path, sha256=sha, size_bytes=len(raw))

    def flush_cache(self):
        pass


class ReplacementPrimaryTests(unittest.TestCase):
    def fixture(self):
        f = fixtures.WorkflowTests(); f.setUp(); self.addCleanup(f.doCleanups)
        manifest = json.loads(f.manifest.read_text()); manifest['count'] = 1; f.write(f.manifest, manifest)
        f.common['manifest_sha256'] = digest(f.manifest.read_bytes())
        db = sqlite3.connect(f.batch/'queue.sqlite3')
        for col in ('reason TEXT', 'files INTEGER DEFAULT 0', 'bytes INTEGER DEFAULT 0', 'updated_at TEXT'):
            db.execute('ALTER TABLE jobs ADD COLUMN '+col)
        db.execute('CREATE TABLE objects(sha TEXT PRIMARY KEY,bytes INTEGER,key TEXT)'); db.commit(); db.close()
        source_path = f.batch/'outcomes'/f'{f.source}.json'
        source = json.loads(source_path.read_text())
        source.update(platform='bkpm.go.id', detail_url='https://jdih.bkpm.go.id/id/document/target')
        f.write(source_path, source)
        self.source_before = source_path.read_bytes()
        sink = Sink(); raw = b'%PDF-new-correct-primary'; sha = digest(raw)
        key = f'{sink.prefix}/raw/{sha}.pdf'; sink.objects[key] = raw
        url = 'https://jdih-storage.bkpm.go.id/jdih/userfiles/Kepres_19_2008.pdf'
        receipt = dict(url=url, final_url=url, sha256=sha, size_bytes=len(raw),
                       status_code=200, complete=True, capture_status='saved')
        envelope = dict(bucket=sink.bucket_name, raw_key=key, capture=copy.deepcopy(receipt), source=dict(
            target_id=f.target, parent_source_id=f.source, role='regulation_body', detail_url=source['detail_url']))
        mh = digest(encoded(envelope)); mk = f'{sink.prefix}/metadata/archive/{mh}.json'
        sink.objects[mk] = encoded(envelope)
        receipt['oss'] = dict(status='uploaded_verified', raw_key=key, metadata_key=mk)
        f.sha = sha; f.item.update(sha256=sha, raw_key=key); f.write(f.candidates, {'items':[f.item]})
        review, repair = f.review()
        report = f.batch/'primary-report.json'
        f.write(report, {'items':[dict(target_id=f.target, parent_source_id=f.source, original_sha256=sha,
            primary_identity_approved=True, role='regulation_body', target_identity=f.item['target_identity'],
            target_title=f.item['target_title'], receipt=receipt, oss=receipt['oss'])]})
        repair['replacement_primary'] = dict(role='regulation_body', receipt=receipt, metadata_sha256=mh,
            evidence_report_path=str(report), evidence_report_sha256=digest(report.read_bytes()))
        f.write(review, {'repairs':[repair]})
        return f, sink, review, repair

    def freeze(self, f, review):
        f.freeze(review)
        return json.loads((f.batch/'plan.json').read_text())['repairs'][0]

    def execute(self, f, sink, repair):
        return apply_verified_recovery(f.batch, [repair], sink, [f.target], 'run-'+f.target[:16])

    def audit(self, f, sink, result):
        results = f.batch/'results-for-audit.json'; f.write(results, result)
        return audit_recovery(batch=f.batch, manifest=f.manifest, plan=f.batch/'plan.json',
            results=results, output=f.batch/'audit.json', sink=sink)

    def test_freeze_execute_actual_bytes_audit_source_unchanged(self):
        f, sink, review, _ = self.fixture(); repair = self.freeze(f, review)
        result = self.execute(f, sink, repair)
        self.assertEqual(result[0]['status'], 'completed')
        self.assertEqual((f.batch/'outcomes'/f'{f.source}.json').read_bytes(), self.source_before)
        report = self.audit(f, sink, result); self.assertTrue(report['ok'], report.get('errors'))
        new = repair['replacement_primary']['receipt']['oss']
        self.assertGreaterEqual(sink.reads.count(new['raw_key']), 2)
        self.assertGreaterEqual(sink.reads.count(new['metadata_key']), 2)
        self.assertEqual(self.execute(f, sink, repair)[0]['status'], 'already_completed')

    def test_freeze_refuses_without_explicit_replacement(self):
        f, sink, review, repair = self.fixture(); repair.pop('replacement_primary'); f.write(review, {'repairs':[repair]})
        with self.assertRaisesRegex(ValueError, 'body receipt'): f.freeze(review)

    def test_offline_binding_host_role_and_single_primary_fail_closed(self):
        for scenario in ('host','role','report_target','identity','capture','alias','mixed_primary','excluded'):
            with self.subTest(scenario=scenario):
                f, sink, review, repair = self.fixture(); item = repair['replacement_primary']
                report = Path(item['evidence_report_path']); body = json.loads(report.read_text()); ev = body['items'][0]
                if scenario == 'host':
                    for obj in (item['receipt'], ev['receipt']): obj['url'] = obj['final_url'] = 'https://evil.example/body.pdf'
                if scenario == 'role': item['role'] = 'attachment'
                if scenario == 'report_target': ev['target_id'] = 'f'*64
                if scenario == 'identity': ev['target_identity'] = ['pp','999','2020']
                if scenario == 'capture': item['receipt']['complete'] = ev['receipt']['complete'] = False
                if scenario == 'alias': item['receipt']['raw_key'] = 'other'
                if scenario == 'mixed_primary': repair['approved_primary_sha256s'].append('f'*64)
                if scenario == 'excluded': f.write(f.batch/'recovery/invalid-source-files.json', [{'source_id':f.source,'sha256':f.sha}])
                f.write(report, body); item['evidence_report_sha256'] = digest(report.read_bytes()); f.write(review, {'repairs':[repair]})
                with self.assertRaises(ValueError): f.freeze(review)
                self.assertEqual(sink.writes, [])

    def test_execution_rechecks_report_archive_bytes_provenance_and_source(self):
        for scenario in ('report','raw','metadata','provenance','source','plan_receipt','rejected'):
            with self.subTest(scenario=scenario):
                f, sink, review, _ = self.fixture(); repair = self.freeze(f, review); item = repair['replacement_primary']
                before = (f.batch/'outcomes'/f'{f.target}.json').read_bytes()
                if scenario == 'report': Path(item['evidence_report_path']).write_text('{}')
                if scenario == 'raw': sink.objects[item['receipt']['oss']['raw_key']] = b'corrupt'
                if scenario == 'metadata': sink.objects[item['receipt']['oss']['metadata_key']] += b' '
                if scenario == 'provenance':
                    # Valid digest and consistently reviewed receipt, but wrong archive provenance.
                    oldkey = item['receipt']['oss']['metadata_key']; env = json.loads(sink.objects[oldkey]); env['source']['parent_source_id'] = 'f'*64
                    data = encoded(env); mh = digest(data); mk = f'{sink.prefix}/metadata/archive/{mh}.json'; sink.objects[mk] = data
                    item['metadata_sha256'] = mh; item['receipt']['oss']['metadata_key'] = mk
                    rp = Path(item['evidence_report_path']); value = json.loads(rp.read_text()); value['items'][0]['receipt'] = copy.deepcopy(item['receipt']); value['items'][0]['oss'] = copy.deepcopy(item['receipt']['oss']); f.write(rp, value); item['evidence_report_sha256'] = digest(rp.read_bytes())
                    newreview = copy.deepcopy(repair); newreview.pop('review_binding'); f.write(review, {'repairs':[newreview]}); repair['review_binding']['sha256'] = digest(review.read_bytes())
                if scenario == 'source': (f.batch/'outcomes'/f'{f.source}.json').write_bytes(self.source_before+b' ')
                if scenario == 'plan_receipt': item['metadata_sha256'] = 'f'*64
                if scenario == 'rejected': f.pair_ledger([f.rejection()])
                try: self.execute(f, sink, repair)
                except ValueError: pass
                self.assertEqual(sink.writes, [])
                self.assertEqual((f.batch/'outcomes'/f'{f.target}.json').read_bytes(), before)
                with sqlite3.connect(f.batch/'queue.sqlite3') as db:
                    self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?',(f.target,)).fetchone()[0], 'no_original')

    def test_audit_rechecks_archive_metadata_after_execution(self):
        f, sink, review, _ = self.fixture(); repair = self.freeze(f, review)
        result = self.execute(f, sink, repair)
        sink.objects[repair['replacement_primary']['receipt']['oss']['metadata_key']] += b' '
        self.assertFalse(self.audit(f, sink, result)['ok'])
