"""Verify queue repairs replace stale download snapshots in the Web summary."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('publisher', Path(__file__).parents[1] / 'tools/publish_census_progress.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PublisherTests(unittest.TestCase):
    def test_archived_dpr_diagnosis_reason(self):
        self.assertEqual(module.category('no_original', '官方归档详情的下载入口文件名为空，未取得对应原件'), '官方按钮缺少文件名')
        self.assertEqual(module.category('no_original', '官方归档详情未提供可识别的下载链接'), '官方详情缺少下载入口')

    def test_retraction_updates_counts_and_reason_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'status.json').write_text(json.dumps(dict(total=2, completed=2,
                no_original=0, updated_at='2026-10-23T00:00:00+00:00')))
            with sqlite3.connect(root / 'queue.sqlite3') as db:
                db.executescript('CREATE TABLE jobs(id,status,reason,updated_at); CREATE TABLE objects(sha,bytes);')
                db.executemany('INSERT INTO jobs VALUES (?,?,?,?)', [
                    ('a', 'completed', '', '2026-10-23T00:00:00+00:00'),
                    ('b', 'no_original', '替代来源原件身份冲突', '2026-10-23T01:00:00+00:00')])
                db.execute('INSERT INTO objects VALUES (?,?)', ('file', 123))
            result = module.Publisher(root, root / 'public.json').snapshot()
            self.assertEqual((result['total'], result['completed'], result['no_original']), (2, 1, 1))
            self.assertEqual((result['unique_oss_files'], result['oss_bytes']), (1, 123))
            self.assertEqual(result['updated_at'], '2026-10-23T01:00:00+00:00')
            self.assertEqual(result['failure_reasons'], [{'reason': '原件身份冲突：待取得正确文件', 'count': 1}])
