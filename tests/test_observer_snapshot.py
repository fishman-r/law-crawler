import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from observer.snapshot import SnapshotReader, host_metrics, redact


class SnapshotReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.base = self.root / "downloads"
        self.reader = SnapshotReader(self.root, self.base)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative, value):
        target = self.base / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return target

    def plan(self, batch="safe", **extras):
        return self.write(f".batches/{batch}/plan.json", {
            "jurisdiction": "Canada", "mode": "metadata", "created_at": "2026-10-16T01:00:00Z", **extras,
        })

    def states(self, values, batch="safe", number="0001"):
        return self.write(f".batches/{batch}/attempts/{number}/states.json", {
            "batch_id": batch, "attempt_number": int(number), "started_at": "2026-10-16T02:00:00Z",
            "states": values,
        })

    def report(self, batch="safe", number="0001", **extras):
        return self.write(f".batches/{batch}/attempts/{number}/report.json", {
            "started_at": "2026-10-16T02:00:00Z", "finished_at": "2026-10-16T03:00:00Z", **extras,
        })

    def fingerprint(self):
        return {str(path.relative_to(self.root)): (path.stat().st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest())
                for path in self.root.rglob("*") if path.is_file()}

    def test_empty_workspace_does_not_create_data(self):
        self.assertEqual(self.reader.batches(), [])
        self.assertFalse(self.base.exists())
        with self.assertRaises(FileNotFoundError):
            self.reader.batch("missing")
        self.assertFalse(self.base.exists())

    def test_old_schema_uses_task_counts_and_preserves_failure_reasons(self):
        self.plan(platforms=["official.example"])
        self.states({
            "catalog:a": {"status": "success", "reason": "目录已保存"},
            "a": {"status": "succeeded", "reason": "仅元数据"},
            "file:b": {"status": "fail", "reason": "HTTP 403", "attempts": 1},
            "file:c": {"status": "failed", "reason": "timeout"},
            "file:d": {"status": "skipped", "reason": "达到限量"},
            "file:e": {"status": "pending", "reason": "等待"},
        })
        self.report(errors=[{"error": "source refused"}], metadata_count=1, unique_files=0)
        row = self.reader.batch("safe")
        self.assertEqual(row["status"], "completed_with_errors")
        self.assertEqual(row["counts"], dict(success=2, failed=2, pending=1, running=0, skipped=1))
        self.assertEqual(row["states"][2]["reason"], "HTTP 403")
        self.assertEqual(row["states"][2]["status"], "failed")
        self.assertEqual(row["report"]["error_count"], 1)
        self.assertIsNone(row["report"]["result"])
        self.assertEqual(row["platforms"], [{"platform": "official.example", "status": "unknown"}])
        simple = self.reader.batches()[0]
        self.assertFalse({"states", "attempts", "errors"} & simple.keys())

    def test_rereads_are_read_only_and_do_not_initialize_or_repair_sqlite(self):
        self.plan()
        self.states({"a": {"status": "running", "reason": "ANTHROPIC_AUTH_TOKEN=private-value"}})
        # 有损坏数据库也不应被读取器打开、创建表或自动修复。
        database = self.base / "crawl-state.sqlite3"
        database.write_bytes(b"not a database")
        before = self.fingerprint()
        for _ in range(3):
            self.reader.batches()
            self.reader.batch("safe")
        self.assertEqual(self.fingerprint(), before)
        self.assertEqual(self.reader.batch("safe")["status"], "unfinished")

    def test_attempts_sort_as_numbers_and_report_status_is_bounded(self):
        self.plan()
        self.states({}, number="9999")
        self.report(number="9999", result="completed")
        self.states({"a": {"status": "running", "reason": "请求中"}}, number="10000")
        row = self.reader.batch("safe")
        self.assertEqual(row["attempt_number"], 10000)
        self.assertEqual(row["attempt_count"], 2)
        self.assertEqual([item["number"] for item in row["attempts"]], [10000, 9999])
        self.assertEqual(row["status"], "unfinished")
        self.assertIsNone(row["report"])
        self.report(number="10000", result="interrupted")
        self.assertEqual(self.reader.batch("safe")["status"], "unfinished")

    def test_plan_without_attempt_is_pending_and_plan_only_is_explicit(self):
        self.plan()
        self.assertEqual(self.reader.batch("safe")["status"], "pending")
        self.report(result="planned_only")
        self.assertEqual(self.reader.batch("safe")["status"], "planned_only")

    def test_corrupt_missing_and_oversize_json_are_isolated(self):
        self.plan()
        states = self.states({})
        states.write_text("{broken", encoding="utf-8")
        self.report(result="completed", states={"x": {"status": "success", "reason": "历史状态"}})
        row = self.reader.batch("safe")
        self.assertEqual(row["counts"]["success"], 1)
        self.assertTrue(row["warnings"])
        with patch("observer.snapshot.MAX_JSON_BYTES", 16):
            oversized = self.reader.batch("safe")
        self.assertTrue(any("大小上限" in item for item in oversized["warnings"]))
        (self.base / ".batches/safe/plan.json").unlink()
        self.assertEqual(self.reader.batches()[0]["id"], "safe")

    def test_invalid_ids_and_symlinks_never_read_external_data(self):
        self.plan()
        external = self.root / "outside"
        external.mkdir()
        (external / "plan.json").write_text('{"jurisdiction":"PRIVATE"}')
        (self.base / ".batches/linked").symlink_to(external, target_is_directory=True)
        for identifier in ("../outside", "/absolute", "x/y", "..", "", "%2e%2e", "名字", "a\\b"):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.reader.batch(identifier)
        with self.assertRaises(ValueError):
            self.reader.batch("linked")
        self.assertEqual([row["id"] for row in self.reader.batches()], ["safe"])
        self.states({})
        report = self.base / ".batches/safe/attempts/0001/report.json"
        report.symlink_to(external / "plan.json")
        row = self.reader.batch("safe")
        self.assertIsNone(row["report"])
        self.assertNotIn("PRIVATE", json.dumps(row))
        self.assertTrue(any("安全读取" in item for item in row["warnings"]))

    def test_symlink_root_and_nested_attempt_directory_are_rejected(self):
        self.plan()
        alias = self.root / "alias"
        alias.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(ValueError):
            SnapshotReader(self.root, alias)
        outside = self.root / "outside"
        outside.mkdir()
        (self.base / ".batches/safe/attempts").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.reader.batch("safe")

    def test_does_not_follow_arbitrary_report_paths(self):
        self.plan(download_base=str(self.root / "outside"))
        self.states({"a": {"status": "success", "reason": "ok", "file": "/etc/passwd"}})
        self.report(result="completed", plan_file="/etc/passwd", files=[{"file": "/etc/passwd"}])
        row = self.reader.batch("safe")
        self.assertNotIn("/etc/passwd", json.dumps(row))
        self.assertEqual(row["status"], "completed")

    def test_limits_apply_to_tasks_batches_attempts_and_unknown_status(self):
        self.plan()
        self.states({"a": {"status": "unknown", "reason": "unrecognized"},
                     "b": {"status": "success", "reason": "ok"},
                     "c": {"status": "success", "reason": "ok"}})
        self.states({}, number="0002")
        self.plan("second")
        with patch("observer.snapshot.MAX_STATES", 2), patch("observer.snapshot.MAX_ATTEMPTS", 1):
            # 单独读取第一轮状态，以便验证任务数量上限。
            (self.base / ".batches/safe/attempts/0002/states.json").unlink()
            (self.base / ".batches/safe/attempts/0002").rmdir()
            row = self.reader.batch("safe")
        self.assertEqual(len(row["states"]), 2)
        self.assertEqual(row["counts"]["success"], 1)
        self.assertTrue(any("状态未知" in item for item in row["warnings"]))
        self.assertTrue(any("截断" in item for item in row["warnings"]))
        with patch("observer.snapshot.MAX_BATCHES", 1):
            rows = self.reader.batches()
        self.assertEqual(len(rows), 1)
        self.assertTrue(any("批次列表" in item for item in rows[0]["warnings"]))

    def test_attempt_history_limit_does_not_hide_latest_or_change_total(self):
        self.plan()
        for number in ("0001", "0002", "0003"):
            self.states({}, number=number)
            self.report(number=number, result="completed")
        with patch("observer.snapshot.MAX_ATTEMPTS", 1):
            row = self.reader.batch("safe")
        self.assertEqual(row["attempt_count"], 3)
        self.assertEqual(row["attempt_number"], 3)
        self.assertEqual([item["number"] for item in row["attempts"]], [3])
        self.assertTrue(any("最近 1 轮" in item for item in row["warnings"]))

    def test_unexpected_field_types_do_not_break_old_or_damaged_records(self):
        self.plan(platforms={"bad": "shape"}, jurisdiction={"bad": "shape"})
        self.states({"a": {"status": {"bad": "shape"}, "reason": ["bad"], "attempts": True},
                     "b": "bad"})
        self.report(result={"bad": "shape"}, errors="legacy error", unique_files=True)
        row = self.reader.batch("safe")
        self.assertEqual(row["states"][0]["status"], "unknown")
        self.assertEqual(row["states"][0]["attempts"], 0)
        self.assertEqual(row["report"]["error_count"], 1)
        self.assertIsNone(row["report"]["unique_files"])
        self.assertEqual(row["platforms"], [])

    def test_reason_and_error_redaction(self):
        self.plan()
        self.states({"x": {"status": "failed", "reason":
            "ANTHROPIC_AUTH_TOKEN=secret-value https://gov.example/a?signature=URL-SECRET&x=1#private"}})
        self.report(result="completed_with_errors", errors=[{
            "error": "Authorization: Bearer abc.def.ghi api_key='key-secret'",
            "url": "https://user:pass@gov.example/a?token=other-secret",
        }])
        serialized = json.dumps(self.reader.batch("safe"))
        for secret in ("secret-value", "URL-SECRET", "abc.def.ghi", "key-secret", "other-secret", "user:pass"):
            self.assertNotIn(secret, serialized)
        self.assertIn("REDACTED", serialized)
        self.assertEqual(redact({"ANTHROPIC_API_KEY": "secret", "token_count": 2}),
                         {"ANTHROPIC_API_KEY": "[REDACTED]", "token_count": 2})
        self.assertNotIn("cloud-secret", redact("AWS_SECRET_ACCESS_KEY=cloud-secret"))
        self.assertNotIn("db-secret", redact("DB_PASSWORD=db-secret"))


class HostMetricsTests(unittest.TestCase):
    def test_non_linux_or_missing_paths_return_partial_metrics(self):
        with patch("observer.snapshot.Path.read_text", side_effect=FileNotFoundError), \
             patch("observer.snapshot.os.getloadavg", side_effect=OSError), \
             patch("observer.snapshot.os.statvfs", side_effect=FileNotFoundError):
            metrics = host_metrics("/missing")
        self.assertTrue(metrics["hostname"])
        self.assertIsNone(metrics["uptime_seconds"])
        self.assertIsNone(metrics["memory_total_mb"])
        self.assertIsNone(metrics["disk_free_gb"])

    def test_linux_proc_units_and_available_disk(self):
        from types import SimpleNamespace

        def read(path, **_):
            if str(path) == "/proc/uptime":
                return "123.5 99.0\n"
            return "MemTotal: 2097152 kB\nMemAvailable: 1048576 kB\n"

        with patch("observer.snapshot.Path.read_text", autospec=True, side_effect=read), \
             patch("observer.snapshot.os.getloadavg", return_value=(0.25, 0.2, 0.1)), \
             patch("observer.snapshot.os.statvfs", return_value=SimpleNamespace(
                 f_blocks=1024 ** 2, f_bavail=512 ** 2, f_frsize=4096)):
            metrics = host_metrics("/data")
        self.assertEqual(metrics["uptime_seconds"], 123)
        self.assertEqual(metrics["memory_total_mb"], 2048)
        self.assertEqual(metrics["memory_available_mb"], 1024)
        self.assertEqual(metrics["disk_total_gb"], 4)
        self.assertEqual(metrics["disk_free_gb"], 1)


if __name__ == "__main__":
    unittest.main()
