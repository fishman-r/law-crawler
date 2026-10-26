"""法规索引身份、分页与成功原件边界；仅在服务器执行。"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from observer.archive_index import ArchiveCatalog, build_archive_index
from observer.archive_export import page_token
from observer.library import LibraryError


class ArchiveIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.base = self.root / 'downloads'
        self.base.mkdir()
        self.state = self.root / 'state.json'
        self.output = self.root / 'runtime' / 'archive.sqlite3'
        self.catalog = ArchiveCatalog(self.base, self.output)

    def tearDown(self):
        self.temp.cleanup()

    def receipt(self, content=b'%PDF-1.7\nno EOF is an archived original', extension='.pdf'):
        digest = hashlib.sha256(content).hexdigest()
        path = self.base / 'Indonesia/pipeline/raw/objects' / (digest + extension)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return dict(path=str(path),sha256=digest,size_bytes=len(content),complete=True,
                    capture_status='saved',status_code=200,url='https://official.go.id/law'+extension,
                    headers={'content-type':'application/pdf'})

    def document(self, receipt=None, number='1', kind='regulation', title='Official Regulation', date=None, identified=True):
        metadata = dict(title={'value':title},document_type={'value':kind},processing_gate='eligible')
        if identified:
            metadata['legal_identity'] = dict(status='observed',value=dict(jurisdiction='Indonesia',
                issuer='Ministry',type='Ministerial Regulation',number=number,year='2024'))
        if date:
            metadata['published_at'] = dict(value=date,status='observed')
        receipt = receipt or self.receipt()
        metadata['file_roles'] = {receipt['url']:dict(role='regulation_body',status='observed')}
        return dict(metadata=metadata,files={'file':receipt},status='archived',
            detail_url='https://official.go.id/law?token=secret',platform='official.go.id')

    def build(self, docs):
        self.state.write_text(json.dumps(dict(documents=docs)))
        return build_archive_index(self.base,self.state,self.output)

    def test_complete_five_field_identity_merges_sources_but_not_files(self):
        a=self.document(self.receipt(b'%PDF-1.7\nfirst'))
        b=self.document(self.receipt(b'%PDF-1.7\nsecond'))
        b['detail_url']='https://another.go.id/law'
        self.build({'a':a,'b':b})
        result=self.catalog.listing()
        self.assertEqual(result['total'],1)
        self.assertEqual(result['summary']['regulations'],1)
        item=result['items'][0]
        self.assertEqual(len(item['files']),2)
        self.assertEqual(len(item['sources']),2)
        self.assertEqual(item['identity_status'],'identified')
        self.assertEqual(item['version_status'],'pending')
        self.assertNotIn('secret',json.dumps(result))
        self.assertNotIn(str(self.base),json.dumps(result))

    def test_same_title_unknown_identity_never_merges_and_type_facets(self):
        self.build({'a':self.document(identified=False),'b':self.document(identified=False),
                    'c':self.document(number='3',kind='administrative_decision')})
        result=self.catalog.listing()
        self.assertEqual(result['total'],3)
        self.assertEqual(self.catalog.listing(classification='pending')['total'],2)
        counts={f['value']:f['count'] for f in result['facets']['classification']}
        self.assertEqual(counts['administrative_decision'],1)
        self.assertEqual(result['summary']['regulations'],0)

    def test_null_dates_last_both_directions_and_stable_page_token(self):
        self.build({'a':self.document(number='1',date='2023-01-02'),
                    'b':self.document(number='2',date='2024-02-03'),
                    'c':self.document(number='3')})
        for sort, expected in [('published_asc','2023-01-02'),('published_desc','2024-02-03')]:
            first=self.catalog.selection(sort=sort,page_size=1)
            self.assertEqual(first['items'][0]['published_at'],expected)
            self.assertEqual(self.catalog.listing(sort=sort,page=3,page_size=1)['items'][0]['published_at'],None)
            self.assertEqual(first['page_token'],page_token(first))
            self.assertEqual(first['page_token'],self.catalog.selection(sort=sort,page_size=1)['page_token'])
            self.assertIn('/api/regulations/archive.zip?',first['archive_url'])
            self.assertIn('token='+first['page_token'],first['archive_url'])

    def test_sql_injection_and_wildcards_are_literal(self):
        self.build({'a':self.document()})
        for q in ("%' OR 1=1 --",'%', '_'):
            self.assertEqual(self.catalog.listing(q=q)['total'],0)
        self.assertEqual(self.catalog.listing(q='official')['total'],1)

    def test_bad_reference_does_not_poison_good_same_sha_other_path(self):
        good=self.receipt()
        bad=deepcopy(good)
        bad_path=Path(good['path']).with_suffix('.bin')
        bad_path.write_bytes(b'bad source')
        bad['path']=str(bad_path)
        self.build({'a':self.document(bad,number='1'),'b':self.document(good,number='2')})
        result=self.catalog.listing()
        self.assertEqual(result['summary']['unique_files'],1)
        self.assertEqual(result['total'],1)
        self.assertEqual(result['items'][0]['identity']['number'],'2')

    def test_same_sha_same_path_different_size_must_recheck(self):
        good=self.receipt()
        bad=dict(good,size_bytes=good['size_bytes']+1)
        self.build({'a':self.document(good,number='1'),'b':self.document(bad,number='2')})
        self.assertEqual(self.catalog.listing()['total'],1)

    def test_failed_receipt_excluded_even_with_existing_bytes(self):
        row=self.receipt()
        row['capture_status']='failed'
        self.build({'a':self.document(row)})
        self.assertEqual(self.catalog.listing()['total'],0)
        self.assertEqual(self.catalog.listing()['summary']['unique_files'],0)

    def test_unknown_or_invalid_file_id_rejected_and_missing_index_not_created(self):
        with self.assertRaises(LibraryError):
            self.catalog.listing()
        self.assertFalse(self.output.exists())
        self.build({'a':self.document()})
        for identifier,status in [('../etc/passwd',400),('0'*64,404)]:
            with self.assertRaises(LibraryError) as raised:
                self.catalog.get_file(identifier)
            self.assertEqual(raised.exception.status,status)
        for kwargs in ({'page_size':101},{'sort':'title;DROP TABLE records'},{'page':0}):
            with self.assertRaises(LibraryError):
                self.catalog.listing(**kwargs)

    def test_all_successful_original_formats_remain_even_without_pdf_eof(self):
        docs={}
        for n in range(8):
            receipt=self.receipt(('%PDF-1.7\n original '+str(n)).encode())
            docs[str(n)]=self.document(receipt,number=str(n))
        self.build(docs)
        result=self.catalog.listing()
        self.assertEqual(result['summary']['unique_files'],8)
        for item in result['items']:
            file=item['files'][0]
            self.assertEqual(file['preview_kind'],'pdf')
            spec=self.catalog.get_file(file['id'])
            self.assertEqual(spec['sha256'],file['id'])
            self.assertEqual(spec['parts'][:4],['Indonesia','pipeline','raw','objects'])

    def test_target_kind_conflict_is_pending_and_no_title_merge(self):
        self.build({'a':self.document(kind='regulation'),'b':self.document(kind='judicial_guidance')})
        result=self.catalog.listing()
        self.assertEqual(result['total'],2)
        self.assertTrue(all(i['identity_status']=='pending' for i in result['items']))
        self.assertTrue(all(i['classification']=='pending' for i in result['items']))


    def translation_overlay(self, rows):
        path=self.base/'Indonesia/pipeline/library/title-translations.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(dict(schema_version=1,translations=rows),ensure_ascii=False))
        return path

    def translation_item(self, record, title='国内贸易管理规定（参考译名）'):
        return dict(record_id=record['id'],original_title_sha256=hashlib.sha256(record['title'].encode()).hexdigest(),
                    title_zh=title,status='reference_translation')

    def test_chinese_overlay_search_keeps_original_and_changes_snapshot(self):
        docs={'a':self.document()}
        self.build(docs)
        first=self.catalog.listing()
        item=first['items'][0]
        self.assertIsNone(item['title_zh'])
        self.assertEqual(item['title_translation_status'],'missing')
        self.translation_overlay([self.translation_item(item)])
        self.build(docs)
        result=self.catalog.selection(q='国内贸易')
        self.assertEqual(result['total'],1)
        self.assertEqual(result['items'][0]['title'],item['title'])
        self.assertEqual(result['items'][0]['title_zh'],'国内贸易管理规定（参考译名）')
        self.assertEqual(result['items'][0]['title_translation_status'],'reference_translation')
        self.assertNotEqual(result['snapshot_id'],first['snapshot_id'])
        self.assertEqual(self.catalog.listing(q='official')['total'],1)
        self.assertEqual(result['summary']['translated_titles'],1)

    def test_stale_chinese_overlay_not_applied(self):
        docs={'a':self.document()}
        self.build(docs)
        entry=self.translation_item(self.catalog.listing()['items'][0])
        entry['original_title_sha256']='0'*64
        self.translation_overlay([entry])
        self.build(docs)
        item=self.catalog.listing()['items'][0]
        self.assertIsNone(item['title_zh'])
        self.assertEqual(item['title_translation_status'],'stale')
        self.assertEqual(self.catalog.listing(q='国内贸易')['total'],0)

    def test_invalid_chinese_overlay_preserves_prior_index(self):
        docs={'a':self.document()}
        self.build(docs)
        before=self.output.read_bytes()
        entry=self.translation_item(self.catalog.listing()['items'][0])
        self.translation_overlay([entry,entry])
        with self.assertRaises(ValueError):
            self.build(docs)
        self.assertEqual(self.output.read_bytes(),before)
        entry['status']=[]
        self.translation_overlay([entry])
        with self.assertRaises(ValueError):
            self.build(docs)

    def test_chinese_overlay_size_and_symlink_rejected(self):
        docs={'a':self.document()}
        self.build(docs)
        entry=self.translation_item(self.catalog.listing()['items'][0])
        path=self.translation_overlay([entry])
        with patch('observer.archive_index.MAX_TITLE_TRANSLATION_BYTES',8):
            with self.assertRaises(ValueError):
                self.build(docs)
        actual=path.with_suffix('.original.json')
        path.rename(actual)
        path.symlink_to(actual)
        with self.assertRaises(ValueError):
            self.build(docs)

    def test_explicit_chinese_overlay_argument(self):
        docs={'a':self.document()}
        self.build(docs)
        entry=self.translation_item(self.catalog.listing()['items'][0])
        path=self.root/'custom-titles.json'
        path.write_text(json.dumps(dict(schema_version=1,translations=[entry])))
        build_archive_index(self.base,self.state,self.output,title_translations_path=path)
        self.assertEqual(self.catalog.listing()['items'][0]['title_zh'],entry['title_zh'])
        with self.assertRaises(ValueError):
            build_archive_index(self.base,self.state,self.output,title_translations_path=self.root/'absent.json')


if __name__=='__main__':
    unittest.main()
