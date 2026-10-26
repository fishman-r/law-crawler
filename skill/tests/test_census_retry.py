import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from law_crawler.census_to_oss import prepare_retry


class ScopedRetryTests(unittest.TestCase):
    def test_status_scope_history_and_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            db = sqlite3.connect(out / 'queue.sqlite3')
            db.execute('create table jobs(id text,status text,reason text,updated_at text,platform text)')
            db.executemany('insert into jobs values(?,?,?,?,?)', [
                ('a','no_original','none','t','dpr'), ('b','completed','ok','t','dpr'),
                ('c','incomplete','403','t','dpr'), ('d','no_original','none','t','finance')])
            db.commit()
            (out/'outcomes').mkdir()
            (out/'outcomes/a.json').write_text('{"status":"no_original"}')
            plan = prepare_retry(db,out,'no_original','repair-1','dpr')
            self.assertEqual([row[0] for row in plan['jobs']], ['a'])
            self.assertEqual(dict(db.execute('select id,status from jobs')),dict(a='pending',b='completed',c='incomplete',d='no_original'))
            self.assertEqual(json.loads((out/'retries/repair-1/outcomes/a.json').read_text())['status'],'no_original')
            db.execute("update jobs set status='no_original' where id='a'");db.commit()
            prepare_retry(db,out,'no_original','repair-1','dpr')
            self.assertEqual(db.execute("select status from jobs where id='a'").fetchone()[0],'no_original')
            with self.assertRaises(ValueError):prepare_retry(db,out,'failed','repair-1','dpr')
            (out/'retries/repair-1/plan.json').write_text('{}')
            with self.assertRaises(ValueError):prepare_retry(db,out,'no_original','repair-1','dpr')

    def test_invalid_run_identifier(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                prepare_retry(sqlite3.connect(':memory:'),Path(temporary),'no_original','../escape')
