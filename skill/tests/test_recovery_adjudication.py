"""Supplemental review provenance regressions; execute on the validation server."""
import json
from pathlib import Path
import tempfile
import unittest

from law_crawler.recovery_adjudication import validate_pending_resolution
from law_crawler.recovery_validation import digest


class AdjudicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = self.bind('old.json', json.dumps({'pending': [{'target_id': 'a' * 64}]}).encode())
        self.page = self.bind('page.png', b'fixture image bytes')
        self.page.update(original_sha256='c' * 64, page_number=1)
        self.evidence = {'pending_resolution': {'kind': 'ocr_transcription_error',
            'previous_review': self.old, 'rationale': 'Actually inspected the year on the source page.',
            'evidence_files': [self.page]}, 'evidence_pages': [self.page],
            'viewed_image_sha256s': [self.page['sha256']]}
        self.repair = {'target_id': 'a' * 64, 'approved_primary_sha256s': ['c' * 64], 'evidence': self.evidence}

    def bind(self, name, raw):
        (self.root / name).write_bytes(raw)
        return {'path': name, 'sha256': digest(raw)}

    def validate(self):
        return validate_pending_resolution(self.repair, base_dir=self.root)

    def test_verified_supplement_preserves_old_review(self):
        before = (self.root / 'old.json').read_bytes()
        self.assertTrue(self.validate())
        self.assertEqual(before, (self.root / 'old.json').read_bytes())

    def test_other_target_cannot_supply_pending_provenance(self):
        self.repair['target_id'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'exactly one pending'):
            self.validate()

    def test_new_evidence_tampering_refused(self):
        (self.root / 'page.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            self.validate()

    def test_ocr_confidence_without_viewed_image_refused(self):
        self.evidence['viewed_image_sha256s'] = []
        with self.assertRaisesRegex(ValueError, 'actually viewed'):
            self.validate()

    def test_page_from_different_original_refused(self):
        self.page['original_sha256'] = 'd' * 64
        with self.assertRaisesRegex(ValueError, 'approved original'):
            self.validate()

    def test_page_number_required(self):
        self.page.pop('page_number')
        with self.assertRaisesRegex(ValueError, 'positive page number'):
            self.validate()

    def test_old_pending_alone_cannot_resolve(self):
        self.evidence['pending_resolution']['evidence_files'] = [self.old]
        with self.assertRaisesRegex(ValueError, 'alone'):
            self.validate()

    def test_catalog_correction_requires_official_snapshot(self):
        r = self.evidence['pending_resolution']; r['kind'] = 'catalog_metadata_error'
        r['corrected_fields'] = [{'field': 'title', 'recorded_value': 'A', 'verified_value': 'B'}]
        with self.assertRaisesRegex(ValueError, 'official snapshots'):
            self.validate()
        r['official_evidence'] = [dict(self.page, url='https://official.example/detail')]
        self.assertTrue(self.validate())

    def test_internal_anomaly_not_silently_corrected(self):
        self.evidence['pending_resolution']['kind'] = 'source_internal_anomaly'
        with self.assertRaisesRegex(ValueError, 'anomalies'):
            self.validate()
        self.evidence['source_text_anomalies'] = [dict(self.page, location='page 1', quote='15 (14/1995)')]
        self.evidence['legal_content_accuracy'] = 'not_verified'
        self.assertTrue(self.validate())

    def test_missing_annex_requires_each_source_statement(self):
        r = self.evidence['pending_resolution']; r['kind'] = 'missing_attachment_confirmed'
        self.evidence.update(known_missing_attachments=['Treaty annex'], primary_text_identity_verified=True)
        r['missing_attachment_evidence'] = [dict(self.page, quote='Lampiran tidak diturunkan', description='Treaty annex')]
        self.assertTrue(self.validate())
        self.evidence['known_missing_attachments'].append('Map')
        with self.assertRaisesRegex(ValueError, 'Every missing'):
            self.validate()

    def test_missing_annex_image_requires_actual_view(self):
        r = self.evidence['pending_resolution']; r['kind'] = 'missing_attachment_confirmed'
        self.evidence.update(known_missing_attachments=['Treaty annex'], primary_text_identity_verified=True)
        r['missing_attachment_evidence'] = [dict(self.page, quote='Lampiran tidak diturunkan', description='Treaty annex')]
        self.evidence['viewed_image_sha256s'] = []
        with self.assertRaisesRegex(ValueError, 'actually viewed'):
            self.validate()

    def test_recovery_cannot_clear_missing_without_receipts(self):
        self.evidence['pending_resolution']['kind'] = 'attachment_recovered'
        with self.assertRaisesRegex(ValueError, 'subordinate receipts'):
            self.validate()

    def test_old_reviews_remain_auditable(self):
        self.assertEqual(validate_pending_resolution({'evidence': {}}), [])
