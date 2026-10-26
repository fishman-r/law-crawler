"""当前导出与历史元数据快照必须分开入队。"""
import tempfile
from pathlib import Path
import unittest

from law_crawler import bulk_process as bp


class ActiveInputsTest(unittest.TestCase):
    def test_only_active_snapshot_is_processed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / 'manifests'
            directory.mkdir()
            old, current = directory / 'old.json', directory / 'current.json'
            bp.write_json(old, {'documents': [{'path': 'old'}]})
            bp.write_json(current, {'documents': [{'path': 'current'}]})
            bp.write_json(root / 'active-manifests.json', {'manifests': [
                {'path': str(current), 'sha256': bp.sha(current), 'raw_sha256': 'a' * 64}]})
            self.assertEqual(bp.manifest_inputs(directory), [{'path': str(current.resolve()), 'sha256': bp.sha(current)}])
            self.assertTrue(old.exists())

    def test_changed_or_duplicate_active_original_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / 'manifests'
            directory.mkdir()
            path = directory / 'current.json'
            bp.write_json(path, {'documents': []})
            row = {'path': str(path), 'sha256': bp.sha(path), 'raw_sha256': 'a' * 64}
            bp.write_json(root / 'active-manifests.json', {'manifests': [row, row]})
            with self.assertRaisesRegex(ValueError, '重复'):
                bp.manifest_inputs(directory)
            bp.write_json(root / 'active-manifests.json', {'manifests': [row]})
            path.write_text('changed')
            with self.assertRaisesRegex(ValueError, 'SHA'):
                bp.manifest_inputs(directory)


if __name__ == '__main__':
    unittest.main()
