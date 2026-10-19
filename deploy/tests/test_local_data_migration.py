"""本地目录迁移须保留证据字节、失败请求和版本，只改文件位置索引。"""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tools import migrate_local_data
from tools.migrate_local_data import migrate


class LocalDataMigrationTests(unittest.TestCase):
    def test_preview_apply_and_repeat_preserve_evidence_and_request_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            country = root / 'downloads/Canada'
            country.mkdir(parents=True)
            raw = country / 'original.pdf'
            raw.write_bytes(b'preserved original bytes')
            sidecar = country / 'original.json'
            sidecar.write_bytes(b'{"historical_evidence":"unchanged"}\n')
            report = root / 'reports/evidence.json'
            report.parent.mkdir()
            report.write_bytes(b'{"old_absolute_path":"/historical/path"}\n')
            temporary = root / 'tmp/fragment.pdf'
            temporary.parent.mkdir()
            temporary.write_bytes(b'temporary source bytes')
            plan = root / 'downloads/.batches/preserved/plan.json'
            plan.parent.mkdir(parents=True)
            plan.write_bytes(b'{"download_base":"old frozen root"}\n')
            evidence = {p.relative_to(root): p.read_bytes() for p in (raw, sidecar, report, temporary, plan)}
            database = country / 'crawl-state.sqlite3'
            with sqlite3.connect(database) as db:
                db.executescript('CREATE TABLE objects(sha256 TEXT, path TEXT, record_json TEXT);'
                    'CREATE TABLE requests(url TEXT,status TEXT,attempts INTEGER);'
                    'CREATE TABLE versions(url TEXT,sha256 TEXT);')
                db.execute('INSERT INTO objects VALUES(?,?,?)', ('sha', str(raw), 'frozen source metadata'))
                db.execute('INSERT INTO requests VALUES(?,?,?)', ('https://official.example/file', 'failed', 3))
                db.execute('INSERT INTO versions VALUES(?,?)', ('https://official.example/old', 'old-sha'))
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            self.assertEqual(migrate(root)['status'], 'dry_run')
            self.assertEqual({p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}, before)
            result = migrate(root, apply=True)
            self.assertEqual(result['status'], 'migrated')
            self.assertEqual(result['database_paths_updated'], {'Canada': 1})
            for old_path, contents in evidence.items():
                self.assertEqual((root / 'data' / old_path).read_bytes(), contents)
                self.assertFalse((root / old_path).exists())
            with sqlite3.connect(root / 'data/downloads/Canada/crawl-state.sqlite3') as db:
                self.assertEqual(db.execute('SELECT path,record_json FROM objects').fetchone(),
                    (str(root / 'data/downloads/Canada/original.pdf'), 'frozen source metadata'))
                self.assertEqual(db.execute('SELECT * FROM requests').fetchall(),
                    [('https://official.example/file', 'failed', 3)])
                self.assertEqual(db.execute('SELECT * FROM versions').fetchall(),
                    [('https://official.example/old', 'old-sha')])
            relocation = json.loads((root / 'data/downloads/.relocation.json').read_text())
            self.assertEqual(relocation['batch_plans']['preserved'], hashlib.sha256(evidence[plan.relative_to(root)]).hexdigest())
            self.assertFalse((root / 'data/migration-backups').exists())
            self.assertEqual(migrate(root, apply=True)['status'], 'already_migrated')

    def test_existing_destination_is_rejected_without_merging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'downloads').mkdir()
            (root / 'data/downloads').mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, '同时存在'):
                migrate(root, apply=True)
            self.assertTrue((root / 'downloads').is_dir())

    def test_invalid_database_path_rolls_back_directory_moves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            country = root / 'downloads/Canada'
            country.mkdir(parents=True)
            db_path = country / 'crawl-state.sqlite3'
            with sqlite3.connect(db_path) as db:
                db.executescript('CREATE TABLE objects(sha256 TEXT, path TEXT, record_json TEXT);'
                    'CREATE TABLE requests(url TEXT,status TEXT);CREATE TABLE versions(url TEXT,sha256 TEXT);')
                db.execute('INSERT INTO objects VALUES(?,?,?)', ('sha', '/outside/original.pdf', 'unchanged'))
            before = db_path.read_bytes()
            with self.assertRaisesRegex(ValueError, '不属于旧国家目录'):
                migrate(root, apply=True)
            self.assertEqual(db_path.read_bytes(), before)
            self.assertFalse((root / 'data/downloads').exists())

    def test_interruption_after_rename_restores_original_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            original = root / 'downloads/source.pdf'
            original.parent.mkdir()
            original.write_bytes(b'original evidence')
            real_snapshot = migrate_local_data.snapshot

            def interrupted(path):
                if path == root / 'data/downloads':
                    raise KeyboardInterrupt()
                return real_snapshot(path)

            with patch.object(migrate_local_data, 'snapshot', side_effect=interrupted):
                with self.assertRaises(KeyboardInterrupt):
                    migrate(root, apply=True)
            self.assertEqual(original.read_bytes(), b'original evidence')
            self.assertFalse((root / 'data/downloads').exists())


if __name__ == '__main__':
    unittest.main()
