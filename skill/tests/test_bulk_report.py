"""汇总不得把空集、已改变产物或旧失败审核写成全量通过。"""
import tempfile
from pathlib import Path
import unittest
from law_crawler import bulk_report as br


class BulkReportTests(unittest.TestCase):
    def test_live_state_is_saved_as_exact_evidence_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / 'bulk' / 'a'
            archive.mkdir(parents=True)
            br.write_json(archive / 'state.json', {'documents': {}, 'platforms': {}, 'events': []})
            br.write_json(archive / 'plan.json', {})
            original = (archive / 'state.json').read_bytes()
            report = br.build(root, ['a'], [], [], 'Indonesia')
            br.write_json(archive / 'state.json', {'documents': {}, 'platforms': {}, 'events': [], 'next': 'changed'})
            br.emit(report, root / 'out')
            stored = br.load(root / 'out' / 'execution-report.json')
            digest = stored['archives'][0]['state_sha256']
            self.assertEqual((root / 'out' / 'snapshots' / (digest + '.json')).read_bytes(), original)

    def test_empty_snapshot_is_not_national_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            report = br.build(Path(temp), [], [], [])
            self.assertFalse(report['national_complete'])
            self.assertEqual(report['verified_unique_originals_in_this_report'], 0)

    def test_failed_details_without_files_are_not_hidden(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / 'bulk' / 'a'
            archive.mkdir(parents=True)
            br.write_json(archive / 'state.json', {'documents': {
                'a': {'status': 'failed', 'reason': '403', 'files': {}},
                'b': {'status': 'incomplete', 'reason': 'no originals', 'files': {}}}, 'platforms': {}, 'events': []})
            br.write_json(archive / 'plan.json', {})
            row = br.build(root, ['a'], [], [])['archives'][0]
            self.assertEqual((row['failed_details'], row['incomplete_details']), (1, 1))
            self.assertEqual(len(row['detail_gaps']), 2)

    def test_review_rechecks_bytes_and_imported_quality_flags(self):
        with tempfile.TemporaryDirectory() as temp:
            batch = Path(temp) / 'test'
            batch.mkdir()
            source = batch / 'original.pdf'
            source.write_bytes(b'%PDF-fixture')
            artifact = {'path': str(source), 'sha256': br.sha(source)}
            flag = {'kind': 'visual_structure_unresolved', 'page': 2}
            required = {'id': 'a', 'artifacts': [artifact], 'quality_flags': [flag]}
            br.write_json(batch / 'plan.json', {'model_config': {'model': 'local'}})
            br.write_json(batch / 'review-request.json', {'files': [required]})
            review = {'batch_id': 'test', 'imported_at': 'recorded', 'reviewer': 'independent',
                'model': 'strong', 'files': [{'id': 'a', 'artifacts': [artifact], 'verdict': 'pass',
                    'issues': [], 'resolved_quality_flags': [{'flag': flag, 'evidence': 'original-page check'}]}]}
            br.write_json(batch / 'review.json', review)
            self.assertEqual(br.review_snapshot(batch)['status'], 'approved')
            failure = {'id': 'b', 'status': 'failed', 'reason': 'OCR failed'}
            br.write_json(batch / 'review-request.json', {'files': [required], 'failed_files': [failure]})
            review['files'].append(failure)
            br.write_json(batch / 'review.json', review)
            mixed = br.review_snapshot(batch)
            self.assertEqual(mixed['status'], 'needs_revision')
            self.assertEqual(mixed['failed_files'], [failure])
            review['files'][0]['resolved_quality_flags'] = []
            br.write_json(batch / 'review.json', review)
            with self.assertRaisesRegex(ValueError, '质量标记'):
                br.review_snapshot(batch)
            source.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'SHA'):
                br.review_snapshot(batch)

    def test_pending_review_is_visible_and_source_text_is_escaped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = br.build(root, [], [], [])
            report['completion_reason'] = '<script>source text</script>'
            report['review_snapshots'] = [{'batch_id': 'pending', 'status': 'pending', 'issues': None}]
            br.emit(report, root)
            page = (root / 'execution-report.html').read_text()
            self.assertIn('&lt;script&gt;', page)
            self.assertNotIn('<script>', page)
            self.assertIn('pending', page)


if __name__ == '__main__':
    unittest.main()
