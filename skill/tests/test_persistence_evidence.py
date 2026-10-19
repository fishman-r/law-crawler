"""同原件审核历史与同来源元数据证据的持久化回归。"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from law_crawler.crawl_store import CrawlStore, unknown_metadata
from law_crawler.platform_adapters.base import field


URL = 'https://official.example/law.pdf'
DETAIL = 'https://official.example/detail'
PLATFORM = 'official.example'
PDF = b'%PDF-1.4\noriginal legislation\n%%EOF'


def metadata(published='2026-01-02'):
    return dict(title=field('Example law', 'Example law', 'Title', DETAIL, 'Official title'),
                published_at=field(published, '2 January 2026', 'PDF promulgation block', URL,
                                   'Publication verified against the original PDF'))


def review(status, timestamp='2026-10-14T10:00:00+08:00'):
    return dict(status=status, reason='Explicit evidence checked against the original file',
                evidence=dict(method='manual page inspection', pages=[1, 2]), reviewed_at=timestamp)


def save(store, data=PDF, document=None, **kwargs):
    return store.save(URL, DETAIL, PLATFORM, lambda url: (data, url), document, **kwargs)


class PersistenceEvidenceTests(unittest.TestCase):
    def test_correction_archives_previous_review_once_with_original_sha(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            _, record = save(store)
            sha = record['file']['sha256']
            prior = review('catalogue_body_type_conflict')
            corrected = review('title_type_number_year_match', '2026-10-14T11:00:00+08:00')
            first = store.register_body_review(sha, prior)
            self.assertEqual(first.get('body_review_history', []), [])
            current = store.register_body_review(sha, corrected)
            expected = [dict(original_sha256=sha, **prior)]
            self.assertEqual(current['body_review_history'], expected)
            self.assertEqual(store.register_body_review(sha, corrected)['body_review_history'], expected)
            # A later correction back to a previously seen review does not duplicate its snapshot.
            store.register_body_review(sha, prior)
            current = store.register_body_review(sha, corrected)
            self.assertEqual(current['body_review_history'], expected + [dict(original_sha256=sha, **corrected)])

    def test_history_survives_cache_same_bytes_refresh_and_sqlite_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            prior = review('unknown_no_extractable_text_first_two_pages')
            corrected = review('title_type_number_year_match', '2026-10-14T11:00:00+08:00')
            with CrawlStore(directory) as store:
                _, record = save(store, document=metadata())
                sha = record['file']['sha256']
                store.register_body_review(sha, prior)
                store.register_body_review(sha, corrected)
                expected = [dict(original_sha256=sha, **prior)]
                status, cached = store.save(URL, DETAIL, PLATFORM,
                    lambda url: self.fail('cached record must not fetch'), unknown_metadata(DETAIL))
                self.assertEqual(status, 'existing')
                self.assertEqual(cached['body_review_history'], expected)
                status, refreshed = save(store, document=unknown_metadata(DETAIL), refresh=True)
                self.assertEqual(status, 'content_duplicate')
                self.assertEqual(refreshed['body_review_history'], expected)
                self.assertEqual(refreshed['body_review'], corrected)
                self.assertEqual((directory / refreshed['file']['path']).read_bytes(), PDF)
            (directory / 'crawl-state.sqlite3').unlink()
            with CrawlStore(directory) as store:
                restored = store.by_hash(sha)
                self.assertEqual(restored['body_review_history'], expected)
                self.assertEqual(restored['body_review'], corrected)
                sidecar = json.loads((directory / restored['file']['path']).with_suffix('.json').read_text())
                self.assertEqual(sidecar['body_review_history'], expected)

    def test_raw_and_markdown_sidecars_keep_original_sha_history(self):
        raw = (b'<?xml version="1.0"?><Statute xmlns:lims="http://justice.gc.ca/lims" lims:id="167">'
               b'<Identification><LongTitle>Example legislation</LongTitle>'
               b'<Chapter><ConsolidatedNumber>A-1</ConsolidatedNumber></Chapter></Identification>'
               b'<Body><Section><Label>1</Label><Text>Operative provision.</Text></Section></Body></Statute>')
        sha = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with CrawlStore(directory) as store:
                document = dict(metadata(), document_type=field('regulation', 'Act', 'Type', DETAIL, 'Document type'))
                _, record = store.save('https://official.example/law.xml', DETAIL, PLATFORM,
                    lambda url: (raw, url), document, convert_text=True)
                prior = review('unknown_no_extractable_text_first_two_pages')
                store.register_body_review(sha, prior)
                current = store.register_body_review(sha, review('title_type_number_year_match'))
                original = directory / record['original_file']['path']
                raw_sidecar = json.loads(original.with_name(original.name + '.metadata.json').read_text())
                md_sidecar = json.loads((directory / record['file']['path']).with_suffix('.json').read_text())
                self.assertEqual(raw_sidecar['body_review_history'], [dict(original_sha256=sha, **prior)])
                self.assertEqual(raw_sidecar['body_review_history'], md_sidecar['body_review_history'])
                self.assertNotEqual(current['file']['sha256'], sha)
            (directory / 'crawl-state.sqlite3').unlink()
            with CrawlStore(directory) as store:
                self.assertEqual(store.by_hash(sha)['body_review_history'], raw_sidecar['body_review_history'])

    def test_duplicate_physical_paths_merge_history_without_cross_sha_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            prior = review('catalogue_body_type_conflict')
            corrected = review('title_type_number_year_match', '2026-10-14T11:00:00+08:00')
            earlier = review('unknown_no_extractable_text_first_two_pages', '2026-10-14T09:00:00+08:00')
            with CrawlStore(directory) as store:
                _, record = save(store)
                sha = record['file']['sha256']
                store.register_body_review(sha, prior)
                record = store.register_body_review(sha, corrected)
                duplicate = json.loads(json.dumps(record))
                duplicate['file']['path'] = 'z-duplicate.pdf'
                duplicate['body_review'] = prior
                duplicate['body_review_history'] = [dict(original_sha256=sha, **earlier),
                                                    dict(original_sha256='0' * 64, **earlier)]
                (directory / duplicate['file']['path']).write_bytes(PDF)
                (directory / 'z-duplicate.json').write_text(json.dumps(duplicate))
            (directory / 'crawl-state.sqlite3').unlink()
            with CrawlStore(directory) as store:
                restored = store.by_hash(sha)
                self.assertEqual(store.migration_errors, [])
                self.assertEqual(restored['body_review'], corrected)
                self.assertEqual(restored['body_review_history'], [dict(original_sha256=sha, **prior),
                                                                 dict(original_sha256=sha, **earlier)])

    def test_unknown_refresh_keeps_observed_fields_and_pdf_date_provenance(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            _, record = save(store, document=metadata())
            observed = record['document']['published_at']
            for status in ('not_provided', 'unparsed', 'partial', 'unknown'):
                with self.subTest(status=status):
                    incoming = unknown_metadata(DETAIL)
                    incoming['published_at']['status'] = status
                    incoming['published_at']['raw'] = 'unparseable directory date'
                    _, refreshed = save(store, document=incoming, refresh=True)
                    self.assertEqual(refreshed['sources'][0]['metadata']['published_at'], observed)
                    self.assertEqual(refreshed['document']['published_at'], observed)
                    self.assertEqual(refreshed['document']['title'], metadata()['title'])
            _, refreshed = save(store, document=metadata('2026-02-03'))
            self.assertEqual(refreshed['document']['published_at']['value'], '2026-02-03')

    def test_same_source_observed_evidence_survives_sqlite_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with CrawlStore(directory) as store:
                _, record = save(store, document=metadata())
                sha = record['file']['sha256']
            (directory / 'crawl-state.sqlite3').unlink()
            with CrawlStore(directory) as store:
                _, record = save(store, document=unknown_metadata(DETAIL))
                self.assertEqual(record['sources'][0]['metadata']['published_at'], metadata()['published_at'])
                self.assertEqual(store.by_hash(sha)['document']['published_at'], metadata()['published_at'])

    def test_new_source_does_not_inherit_old_source_observations(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            _, record = save(store, document=metadata())
            for index, (platform, detail, url) in enumerate([
                    ('other.example', DETAIL, URL),
                    (PLATFORM, DETAIL + '/other', URL),
                    (PLATFORM, DETAIL, 'https://official.example/other.pdf')]):
                with self.subTest(platform=platform, detail=detail, url=url):
                    incoming = unknown_metadata(detail)
                    _, refreshed = store.save(url, detail, platform, lambda value: (PDF, value), incoming)
                    source = refreshed['sources'][index + 1]
                    self.assertEqual(source['metadata'], incoming)
                    self.assertEqual(source['metadata']['published_at']['status'], 'not_provided')
                    self.assertEqual(refreshed['document']['published_at'], metadata()['published_at'])

    def test_changed_original_sha_does_not_inherit_observations_or_review_history(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            _, record = save(store, document=metadata())
            sha = record['file']['sha256']
            store.register_body_review(sha, review('catalogue_body_type_conflict'))
            store.register_body_review(sha, review('title_type_number_year_match'))
            incoming = unknown_metadata(DETAIL)
            status, changed = save(store, data=PDF.replace(b'original', b'changed'),
                                   document=incoming, refresh=True)
            self.assertEqual(status, 'downloaded')
            self.assertNotEqual(changed['file']['sha256'], sha)
            self.assertEqual(changed['sources'][0]['metadata'], incoming)
            self.assertEqual(changed['document']['published_at']['status'], 'not_provided')
            self.assertEqual(changed['body_review']['status'], 'not_reviewed')
            self.assertEqual(changed.get('body_review_history', []), [])
            self.assertEqual(len(store.by_hash(sha)['body_review_history']), 1)


if __name__ == '__main__':
    unittest.main()
