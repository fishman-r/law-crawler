"""离线转换版本迁移：临时原件与索引，绝不读写真实 downloads。"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from crawl_store import CrawlStore, write_json
from text_documents import render_markdown


URL = 'https://official.example/source.xml'
FINAL = 'https://official.example/final.xml'
META = {name: {'value': value, 'source_url': FINAL} for name, value in
        [('document_type', 'regulation'), ('title', 'Frozen source title')]}
RAW = (b'<Regulation xmlns:lims="http://justice.gc.ca/lims" lims:id="test">'
       b'<Identification><LongTitle>Fixture regulation</LongTitle><InstrumentNumber>SOR/2007-151</InstrumentNumber>'
       b'<RegistrationDate><Date><YYYY>2007</YYYY><MM>6</MM><DD>14</DD></Date></RegistrationDate>'
       b'</Identification><Body><Section><Label>1</Label><Text>Original legal wording.</Text></Section></Body></Regulation>')
LEGACY = b'# Frozen source title\n\n2007\n\n6\n\n14\n\n## 1\n\nOriginal legal wording.\n'


def legacy_record(store):
    """构造旧磁盘快照；特意不用 persist，否则会触发待测自动迁移。"""
    _, record = store.save(URL, 'https://official.example/detail', 'official.example',
                           lambda url: (RAW, FINAL), metadata=copy.deepcopy(META), convert_text=True)
    path = store.directory / record['file']['path']
    path.write_bytes(LEGACY)
    record['file'].update(sha256=hashlib.sha256(LEGACY).hexdigest(), size_bytes=len(LEGACY))
    record['conversion']['method'] = 'structured_text_v1'
    write_json(path.with_suffix('.json'), record)
    store._index(record)
    store.db.commit()
    return record


def requests(store):
    return [tuple(row) for row in store.db.execute('SELECT * FROM requests ORDER BY canonical_url')]


class ConversionUpgradeTests(unittest.TestCase):
    def test_offline_upgrade_preserves_raw_sources_requests_and_frozen_conversion_inputs(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            record = legacy_record(store)
            original_file = copy.deepcopy(record['original_file'])
            sources = copy.deepcopy(record['sources'])
            request_snapshot = requests(store)
            conversion_inputs = copy.deepcopy(record['conversion'])
            # 当前主文档元数据可以已续补；重建必须继续用首次转换冻结的输入。
            record['document']['title'] = {'value': 'Later catalogue title', 'source_url': URL}
            expected = render_markdown(RAW, conversion_inputs['metadata'], conversion_inputs['source_url'])
            self.assertTrue(store.upgrade_conversion(record))
            self.assertEqual(record['original_file'], original_file)
            self.assertEqual((Path(tmp) / original_file['path']).read_bytes(), RAW)
            self.assertEqual(record['sources'], sources)
            self.assertEqual(requests(store), request_snapshot)
            self.assertEqual(record['conversion']['metadata'], conversion_inputs['metadata'])
            self.assertEqual(record['conversion']['source_url'], FINAL)
            self.assertNotEqual(record['conversion']['method'], 'structured_text_v1')
            self.assertEqual((Path(tmp) / record['file']['path']).read_bytes(), expected)
            self.assertEqual(record['file']['sha256'], hashlib.sha256(expected).hexdigest())
            self.assertTrue(store.valid(record))
            backup_root = Path(tmp) / 'conversion-history' / original_file['sha256']
            backups = [path for path in backup_root.rglob('*') if path.is_file()]
            self.assertTrue(any(path.read_bytes() == LEGACY for path in backups), '历史Markdown须保留原字节')
            json_backups = [json.loads(path.read_text()) for path in backups if path.suffix == '.json']
            self.assertTrue(any(value.get('file', {}).get('sha256') == hashlib.sha256(LEGACY).hexdigest()
                                and value.get('conversion', {}).get('method') == 'structured_text_v1'
                                for value in json_backups), '旧转换元数据须可追溯')
            self.assertEqual(len(record['conversion_history']), 1)
            self.assertEqual(store.by_hash(original_file['sha256'])['file']['sha256'], record['file']['sha256'])

    def test_repeated_upgrade_is_idempotent_and_cache_reuse_does_not_fetch(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            record = legacy_record(store)
            request_snapshot = requests(store)
            self.assertTrue(store.upgrade_conversion(record))
            upgraded = copy.deepcopy(record)
            files_before = {str(p.relative_to(tmp)): p.read_bytes() for p in Path(tmp).rglob('*')
                            if p.is_file() and 'conversion-history' in p.parts}
            self.assertFalse(store.upgrade_conversion(record))
            self.assertEqual(record, upgraded)
            offline = Mock(side_effect=AssertionError('迁移及缓存复用不得请求网络'))
            status, reused = store.save(URL, 'https://official.example/detail', 'official.example', offline, metadata=META, convert_text=True)
            self.assertEqual(status, 'existing')
            offline.assert_not_called()
            self.assertEqual(reused['conversion_history'], upgraded['conversion_history'])
            self.assertEqual(requests(store), request_snapshot)
            self.assertEqual(files_before, {str(p.relative_to(tmp)): p.read_bytes() for p in Path(tmp).rglob('*')
                                           if p.is_file() and 'conversion-history' in p.parts})

    def test_cached_old_conversion_is_automatically_upgraded_without_fetch(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            record = legacy_record(store)
            request_snapshot = requests(store)
            offline = Mock(side_effect=AssertionError('旧版本缓存升级不得请求网络'))
            status, upgraded = store.save(URL, 'https://official.example/detail', 'official.example', offline, metadata=META, convert_text=True)
            self.assertEqual(status, 'existing')
            offline.assert_not_called()
            self.assertNotEqual(upgraded['conversion']['method'], 'structured_text_v1')
            self.assertIn('RegistrationDate', (Path(tmp) / upgraded['file']['path']).read_text())
            self.assertEqual(len(upgraded['conversion_history']), 1)
            self.assertEqual(requests(store), request_snapshot)
            self.assertEqual((Path(tmp) / record['original_file']['path']).read_bytes(), RAW)

    def test_persist_upgrades_old_conversion_and_does_not_change_requests(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            record = legacy_record(store)
            request_snapshot = requests(store)
            store.persist(record)
            self.assertNotEqual(record['conversion']['method'], 'structured_text_v1')
            self.assertEqual(len(record['conversion_history']), 1)
            self.assertEqual(requests(store), request_snapshot)
            self.assertTrue(store.valid(record))

    def test_corrupt_derived_markdown_is_rebuilt_from_intact_raw_with_actual_backup(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            record = legacy_record(store)
            old_metadata_sha = record['file']['sha256']
            damaged = b'# truncated derived content\n'
            markdown_path = Path(tmp) / record['file']['path']
            markdown_path.write_bytes(damaged)
            self.assertTrue(store.upgrade_conversion(record))
            self.assertEqual(markdown_path.read_bytes(), render_markdown(RAW, META, FINAL))
            actual_sha = hashlib.sha256(damaged).hexdigest()
            backup = Path(tmp) / 'conversion-history' / record['original_file']['sha256'] / (actual_sha + '.md')
            self.assertEqual(backup.read_bytes(), damaged)
            snapshot_path = Path(tmp) / record['conversion_history'][-1]['backup_metadata']
            snapshot = json.loads(snapshot_path.read_text())
            self.assertEqual(snapshot['file']['sha256'], old_metadata_sha)
            self.assertIn(actual_sha, json.dumps(record['conversion_history']))
            self.assertTrue(store.valid(record))

    def test_equal_markdown_across_methods_keeps_each_complete_previous_metadata(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            record = legacy_record(store)
            # 转换版本可以只改善其他格式；此XML两轮派生字节完全相同。
            identical_markdown = render_markdown(RAW, META, FINAL)
            markdown_path = Path(tmp) / record['file']['path']
            markdown_path.write_bytes(identical_markdown)
            record['file'].update(sha256=hashlib.sha256(identical_markdown).hexdigest(),
                                  size_bytes=len(identical_markdown))
            previous_v1 = copy.deepcopy(record)
            before_requests = requests(store)
            with patch('text_documents.CONVERSION_METHOD', 'method-A'):
                self.assertTrue(store.upgrade_conversion(record))
                self.assertEqual(markdown_path.read_bytes(), identical_markdown)
                self.assertEqual(record['conversion']['method'], 'method-A')
            previous_a = copy.deepcopy(record)
            with patch('text_documents.CONVERSION_METHOD', 'method-B'):
                self.assertTrue(store.upgrade_conversion(record))
                self.assertEqual(markdown_path.read_bytes(), identical_markdown)
                self.assertEqual(record['conversion']['method'], 'method-B')
                self.assertEqual(len(record['conversion_history']), 2)
                first, second = record['conversion_history']
                self.assertEqual(first['backup_file'], second['backup_file'])
                self.assertNotEqual(first['backup_metadata'], second['backup_metadata'],
                                    '相同MD字节不能覆盖或复用不同轮次的完整旧元数据')
                first_metadata = Path(tmp) / first['backup_metadata']
                second_metadata = Path(tmp) / second['backup_metadata']
                self.assertEqual(json.loads(first_metadata.read_text()), previous_v1)
                self.assertEqual(json.loads(second_metadata.read_text()), previous_a)
                self.assertEqual((Path(tmp) / first['backup_file']).read_bytes(), identical_markdown)
                unchanged = copy.deepcopy(record)
                all_backups = {str(path): path.read_bytes() for path in (Path(tmp) / 'conversion-history').rglob('*') if path.is_file()}
                self.assertFalse(store.upgrade_conversion(record))
                self.assertEqual(record, unchanged)
                self.assertEqual(all_backups, {str(path): path.read_bytes() for path in (Path(tmp) / 'conversion-history').rglob('*') if path.is_file()})
            self.assertEqual(requests(store), before_requests)
            self.assertEqual((Path(tmp) / record['original_file']['path']).read_bytes(), RAW)

    def test_corrupt_raw_cannot_be_used_for_a_new_conversion(self):
        with tempfile.TemporaryDirectory() as tmp, CrawlStore(tmp) as store:
            record = legacy_record(store)
            before = copy.deepcopy(record)
            request_snapshot = requests(store)
            raw_path = Path(tmp) / record['original_file']['path']
            raw_path.write_bytes(RAW.replace(b'Original legal wording.', b'Altered legal wording.'))
            with self.assertRaises(ValueError):
                store.upgrade_conversion(record)
            self.assertEqual(record, before)
            self.assertEqual((Path(tmp) / record['file']['path']).read_bytes(), LEGACY)
            self.assertEqual(requests(store), request_snapshot)
            self.assertFalse((Path(tmp) / 'conversion-history').exists())


if __name__ == '__main__':
    unittest.main()
