import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from datetime import timedelta
import unittest
from unittest.mock import mock_open, patch

from observer import events


REPO = Path(__file__).resolve().parents[2]
EVENT_ENTRYPOINT = REPO / "deploy" / "run.py"


class ObserverEventsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.runtime = self.root / "runtime"
        self.settings = self.root / "claude" / "settings.json"
        with patch.dict(os.environ, {"CLAUDE_OBSERVER_USER_PROMPT_EXPANSION": "0"}):
            events.install_hooks(self.settings, self.runtime, self.workspace, sys.executable)

    def record(self, kind, session="session-1", **values):
        payload = dict(hook_event_name=kind, session_id=session, cwd=str(self.workspace))
        payload.update(values)
        events.record_event(self.runtime, payload)

    def detail(self, session="session-1"):
        return events.session_detail(self.runtime, session)

    def test_project_scope_includes_children_and_resolves_symlink_escape(self):
        child = self.workspace / "nested"
        child.mkdir()
        self.record("SessionStart", cwd=str(child), source="startup")
        self.record("SessionStart", session="outside", cwd=str(self.root))
        outside = self.root / "other"
        outside.mkdir()
        (self.workspace / "escape").symlink_to(outside, target_is_directory=True)
        self.record("UserPromptSubmit", session="escaped", cwd=str(self.workspace / "escape"))
        self.record("UserPromptSubmit", session="relative", cwd="nested")
        self.assertEqual([row["id"] for row in events.session_list(self.runtime)], ["session-1"])
        self.assertEqual(self.detail()["status"], "idle")
        self.assertIsNone(self.detail()["process_alive"])

    def test_secret_payload_fields_are_never_persisted_or_returned(self):
        secret = "DO_NOT_RECORD_7c6b5a4d"
        self.record(
            "PreToolUse", tool_name="Bash", tool_use_id="tool-1",
            prompt=secret, error=secret, last_assistant_message=secret,
            transcript_path=secret, api_key=secret,
            tool_input={"command": secret * 10000, "description": secret},
            tool_response={"stdout": secret}, environment={"TOKEN": secret},
        )
        self.record("PostToolUseFailure", tool_name="Bash", tool_use_id="tool-1", error=secret)
        self.record("Stop", last_assistant_message=secret)
        files = [path for path in self.runtime.rglob("*") if path.is_file()]
        self.assertTrue(files)
        for path in files:
            self.assertNotIn(secret.encode(), path.read_bytes(), path.name)
        serialized = json.dumps(self.detail())
        self.assertNotIn(secret, serialized)
        self.assertEqual(self.detail()["event_count"], 3)
        self.assertEqual(set(self.detail()["events"][0]), {
            "at", "event", "tool_name", "status", "skill_name", "agent_id", "agent_type",
        })

    def test_parallel_tool_ids_do_not_clear_each_other(self):
        self.record("UserPromptSubmit")
        self.record("PreToolUse", tool_name="Read", tool_use_id="read-1")
        self.record("PreToolUse", tool_name="Bash", tool_use_id="bash-1")
        self.record("PostToolUse", tool_name="Read", tool_use_id="read-1")
        self.assertEqual(self.detail()["current_tool"], "Bash")
        self.record("PostToolUseFailure", tool_name="Bash", tool_use_id="bash-1")
        self.assertIsNone(self.detail()["current_tool"])
        self.assertEqual(self.detail()["status"], "running")

    def test_stop_is_idle_and_session_end_is_ended_even_on_clear_resume(self):
        self.record("SessionStart", source="startup")
        self.record("UserPromptSubmit")
        self.record("Stop")
        self.assertEqual(self.detail()["status"], "idle")
        for reason in ("clear", "resume"):
            self.record("SessionEnd", reason=reason)
            self.assertEqual(self.detail()["status"], "ended")
            self.assertIsNone(self.detail()["process_alive"])
            self.record("SessionStart", source="resume")
            self.assertEqual(self.detail()["status"], "idle")
            self.assertEqual(self.detail()["source"], "resume")

    def test_permission_wait_api_error_and_interruption_are_distinct(self):
        self.record("PreToolUse", tool_name="AskUserQuestion", tool_use_id="ask-1")
        self.assertEqual(self.detail()["status"], "waiting")
        self.record("PostToolUse", tool_name="AskUserQuestion", tool_use_id="ask-1")
        self.assertEqual(self.detail()["status"], "running")
        self.record("PermissionRequest", tool_name="Bash", tool_use_id="bash-1")
        self.assertEqual(self.detail()["status"], "waiting")
        self.record("PermissionDenied", tool_name="Bash", tool_use_id="bash-1")
        self.assertEqual(self.detail()["status"], "unknown")
        self.record("PostToolUseFailure", tool_name="Bash", is_interrupt=True)
        self.assertEqual(self.detail()["status"], "unknown")
        self.record("Notification", notification_type="permission_prompt", message="never persisted")
        self.assertEqual(self.detail()["status"], "waiting")
        self.record("StopFailure", error="authentication_failed", error_details="not persisted")
        self.assertEqual(self.detail()["status"], "unknown")

    def test_subagent_background_launch_is_not_completion(self):
        self.record("PreToolUse", tool_name="Agent", tool_use_id="launch-1")
        self.record("SubagentStart", agent_id="agent-1", agent_type="Explore")
        self.record("PostToolUse", tool_name="Agent", tool_use_id="launch-1", tool_response={"status": "async_launched"})
        self.record("Stop")
        detail = self.detail()
        self.assertEqual(detail["status"], "idle")
        self.assertEqual(detail["subagents"][0]["status"], "running")
        self.record("SubagentStop", agent_id="agent-1", agent_type="Explore", last_assistant_message="SECRET")
        detail = self.detail()
        self.assertEqual(detail["subagents"][0]["status"], "idle")
        self.assertNotIn("SECRET", json.dumps(detail))

    def test_skill_tool_and_direct_slash_are_invocations_not_business_success(self):
        self.record("PreToolUse", tool_name="Skill", tool_use_id="skill-1", tool_input={"skill": "review", "args": "SECRET"})
        self.record("PostToolUse", tool_name="Skill", tool_use_id="skill-1", tool_input={"skill": "review"})
        self.record("UserPromptExpansion", expansion_type="slash_command", command_name="plugin:download", command_args="SECRET", prompt="SECRET")
        self.record("UserPromptExpansion", expansion_type="mcp_prompt", command_name="not-a-skill")
        detail = self.detail()
        self.assertEqual(detail["skill_names"], ["review", "plugin:download"])
        self.assertEqual(detail["status"], "running")
        self.assertFalse(any(event["status"] in {"success", "succeeded", "completed"} for event in detail["events"]))
        self.assertNotIn("SECRET", json.dumps(detail))

    def test_stale_activity_becomes_unknown_without_inventing_process_death(self):
        now = events._now()
        with patch.object(events, "_now", return_value=now):
            self.record("PreToolUse", tool_name="Bash", tool_use_id="tool-1")
            self.record("SubagentStart", agent_id="agent-1", agent_type="Explore")
        with patch.object(events, "_now", return_value=now + timedelta(seconds=events.STALE_AFTER_SECONDS + 2)):
            detail = self.detail()
            self.assertEqual(detail["status"], "unknown")
            self.assertEqual(detail["subagents"][0]["status"], "unknown")
            self.assertIsNone(detail["current_tool"])
            self.assertIsNone(detail["process_alive"])

    def test_old_subagent_does_not_look_fresh_when_main_session_updates(self):
        now = events._now()
        with patch.object(events, "_now", return_value=now):
            self.record("SubagentStart", agent_id="agent-1", agent_type="Explore")
        with patch.object(events, "_now", return_value=now + timedelta(seconds=events.STALE_AFTER_SECONDS + 2)):
            self.record("UserPromptSubmit")
            self.assertEqual(self.detail()["status"], "running")
            self.assertEqual(self.detail()["subagents"][0]["status"], "unknown")

    def test_pid_identity_is_checked_not_just_heartbeat_or_pid(self):
        run_id = "run-123"
        with patch.dict(os.environ, {"LAW_CRAWLER_RUN_ID": run_id}):
            self.record("UserPromptSubmit")
        runs = self.runtime / "runs"
        runs.mkdir()
        value = dict(id=run_id, pid=123, boot_id="boot-123", process_start="9999", heartbeat_at=events._timestamp(), finished_at=None, exit_code=None)
        (runs / (run_id + ".json")).write_text(json.dumps(value))
        process_stat = "123 (name with ) parentheses) " + " ".join(["S"] + ["0"] * 18 + ["9999"])
        with patch.object(Path, "read_text", return_value="boot-123"), patch.object(Path, "open", mock_open(read_data=process_stat)):
            detail = self.detail()
            self.assertIs(detail["process_alive"], True)
            self.assertEqual(detail["run_id"], run_id)
        reused = process_stat.rsplit(" ", 1)[0] + " 10000"
        with patch.object(Path, "read_text", return_value="boot-123"), patch.object(Path, "open", mock_open(read_data=reused)):
            self.assertIs(self.detail()["process_alive"], False)
            self.assertEqual(self.detail()["status"], "unknown")
        with patch.object(Path, "read_text", return_value="new-boot"):
            self.assertIs(self.detail()["process_alive"], False)
        value["process_start"] = None
        (runs / (run_id + ".json")).write_text(json.dumps(value))
        self.assertIsNone(self.detail()["process_alive"])

    def test_new_unassociated_session_start_clears_previous_run_identity(self):
        with patch.dict(os.environ, {"LAW_CRAWLER_RUN_ID": "run-123"}):
            self.record("SessionStart")
        with patch.dict(os.environ, {}, clear=True):
            self.record("SessionStart", source="resume")
        self.assertIsNone(self.detail()["run_id"])

    def test_invalid_ids_and_malformed_fields_are_rejected_safely(self):
        for session_id in ("../escape", "/tmp/escape", "x/y", "a" * 129, "sk-sensitive", None):
            self.record("SessionStart", session=session_id)
        self.assertEqual(events.session_list(self.runtime), [])
        for session_id in ("../escape", "x/y", "sk-sensitive"):
            with self.assertRaises(ValueError):
                events.session_detail(self.runtime, session_id)
        self.record([], session="wrong-event-type")
        self.record("SessionStart", source=[], model="SECRET")
        self.record("Notification", notification_type={"bad": "value"})
        self.assertEqual(self.detail()["event_count"], 2)

    def test_runtime_session_log_summary_and_settings_symlinks_never_followed(self):
        victim = self.root / "victim"
        victim.write_text("UNCHANGED")
        linked_runtime = self.root / "runtime-link"
        linked_runtime.symlink_to(self.runtime, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            events.record_event(linked_runtime, dict(session_id="safe", cwd=str(self.workspace), hook_event_name="SessionStart"))
        for suffix in (".jsonl", ".json"):
            link = self.runtime / "sessions" / ("linked" + suffix)
            link.symlink_to(victim)
            with self.assertRaises((OSError, ValueError)):
                self.record("SessionStart", session="linked")
            link.unlink()
        settings_link = self.root / "linked-settings.json"
        settings_link.symlink_to(victim)
        with self.assertRaises((OSError, ValueError)):
            events.install_hooks(settings_link, self.runtime, self.workspace, sys.executable)
        self.assertFalse(events.hooks_installed(settings_link))
        self.assertEqual(victim.read_text(), "UNCHANGED")

    def test_hardlinked_data_is_not_modified(self):
        victim = self.root / "victim"
        victim.write_text("UNCHANGED")
        os.link(victim, self.runtime / "sessions" / "linked.jsonl")
        with self.assertRaises(ValueError):
            self.record("SessionStart", session="linked")
        self.assertEqual(victim.read_text(), "UNCHANGED")

    def test_sessions_directory_and_runs_symlinks_are_not_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        sessions = self.runtime / "sessions"
        sessions.rmdir()
        sessions.symlink_to(outside, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            self.record("SessionStart")
        self.assertEqual(list(outside.iterdir()), [])
        sessions.unlink()
        sessions.mkdir()
        with patch.dict(os.environ, {"LAW_CRAWLER_RUN_ID": "run-123"}):
            self.record("UserPromptSubmit")
        (self.runtime / "runs").symlink_to(outside, target_is_directory=True)
        self.assertIsNone(self.detail()["process_alive"])

    def test_journal_replays_write_that_preceded_a_failed_summary(self):
        self.record("SessionStart")
        original = events._atomic_write

        def fail_summary(dir_fd, name, data):
            if name == "session-1.json":
                raise OSError("simulated power loss before summary rename")
            return original(dir_fd, name, data)

        with patch.object(events, "_atomic_write", side_effect=fail_summary):
            with self.assertRaises(OSError):
                self.record("UserPromptSubmit")
        detail = self.detail()
        self.assertEqual(detail["event_count"], 2)
        self.assertEqual(detail["status"], "running")
        self.record("Stop")
        self.assertEqual(self.detail()["event_count"], 3)
        self.assertEqual(self.detail()["status"], "idle")

    def test_truncated_tail_does_not_poison_next_append(self):
        self.record("SessionStart")
        path = self.runtime / "sessions" / "session-1.jsonl"
        with path.open("ab") as stream:
            stream.write(b'{"partial":"not-a-complete-record"')
        self.record("UserPromptSubmit")
        self.assertEqual(self.detail()["event_count"], 2)
        self.assertEqual(len(path.read_text().splitlines()), 2)
        for line in path.read_text().splitlines():
            self.assertIsInstance(json.loads(line), dict)

    def test_log_history_and_session_count_remain_bounded(self):
        with patch.object(events, "MAX_HISTORY_EVENTS", 8), patch.object(events, "MAX_LOG_BYTES", 1800):
            for _ in range(25):
                self.record("UserPromptSubmit")
            path = self.runtime / "sessions" / "session-1.jsonl"
            self.assertLessEqual(path.stat().st_size, 1800)
            self.assertLessEqual(len(self.detail()["events"]), 8)
            self.assertEqual(self.detail()["event_count"], 25)
        with patch.object(events, "MAX_SESSIONS", 3):
            for index in range(6):
                self.record("SessionStart", session=f"new-{index}")
            self.assertEqual(len(events.session_list(self.runtime)), 3)
            self.assertEqual(len(list((self.runtime / "sessions").glob("*.json"))), 3)
            self.assertEqual(len(list((self.runtime / "sessions").glob("*.jsonl"))), 3)

    def test_real_cross_process_concurrent_writes_are_serialized(self):
        script = """import sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])
from observer.events import record_event
for index in range(20):
    record_event(Path(sys.argv[1]), dict(session_id='concurrent',cwd=sys.argv[2],hook_event_name='UserPromptSubmit'))
"""
        children = [subprocess.Popen([sys.executable, "-c", script, str(self.runtime), str(self.workspace), str(REPO / "web")], cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
        for child in children:
            stdout, stderr = child.communicate(timeout=30)
            self.assertEqual(child.returncode, 0, stderr.decode())
            self.assertEqual(stdout, b"")
        detail = self.detail("concurrent")
        self.assertEqual(detail["event_count"], 60)
        rows = [json.loads(line) for line in (self.runtime / "sessions" / "concurrent.jsonl").read_text().splitlines()]
        self.assertEqual([row["_seq"] for row in rows], list(range(1, 61)))

    def test_hook_install_is_idempotent_and_preserves_other_configuration(self):
        custom = dict(model="user-model", env={"EXISTING": "unchanged"}, hooks={
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-hook"}]}],
            "UserPromptExpansion": [{"hooks": [{"type": "command", "command": "echo user-expansion"}]}],
        })
        self.settings.write_text(json.dumps(custom))
        with patch.dict(os.environ, {"CLAUDE_OBSERVER_USER_PROMPT_EXPANSION": "1"}):
            events.install_hooks(self.settings, self.runtime, self.workspace, sys.executable)
            first = self.settings.read_bytes()
            events.install_hooks(self.settings, self.runtime, self.workspace, sys.executable)
            self.assertEqual(first, self.settings.read_bytes())
        self.assertTrue(events.hooks_installed(self.settings))
        value = json.loads(self.settings.read_text())
        self.assertEqual(value["model"], custom["model"])
        self.assertEqual(value["env"], custom["env"])
        self.assertEqual(value["hooks"]["PreToolUse"][0], custom["hooks"]["PreToolUse"][0])
        with patch.dict(os.environ, {"CLAUDE_OBSERVER_USER_PROMPT_EXPANSION": "0"}):
            events.install_hooks(self.settings, self.runtime, self.workspace, sys.executable)
        value = json.loads(self.settings.read_text())
        self.assertEqual(value["hooks"]["UserPromptExpansion"], custom["hooks"]["UserPromptExpansion"])
        self.assertEqual(self.settings.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.runtime.stat().st_mode & 0o777, 0o700)

    def test_hook_command_quotes_code_python_runtime_and_workspace_separately(self):
        workspace = self.root / "workspace ' dollars$ and backticks`"
        workspace.mkdir()
        runtime = self.root / "runtime ' dollars$ and parentheses()"
        events.install_hooks(self.settings, runtime, workspace, sys.executable)
        config = json.loads(self.settings.read_text())
        command = config["hooks"]["SessionStart"][-1]["hooks"][0]["command"]
        self.assertEqual(shlex.split(command), [
            sys.executable, str(EVENT_ENTRYPOINT), "events",
            "--runtime-dir", str(runtime), "--workspace", str(workspace.resolve()),
        ])
        payload = dict(session_id="quoted", hook_event_name="SessionStart", cwd=str(workspace))
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        result = subprocess.run(command, shell=True, input=json.dumps(payload), text=True,
                                capture_output=True, timeout=15, cwd=workspace, env=environment)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual(events.session_detail(runtime, "quoted")["status"], "idle")

    def test_old_hook_commands_are_replaced_once_by_the_current_entrypoint(self):
        command = " ".join(shlex.quote(value) for value in (
            sys.executable, "-m", "observer.events", "--runtime-dir", str(self.runtime),
            "--workspace", str(self.workspace),
        ))
        self.settings.write_text(json.dumps({"hooks": {
            name: [{"hooks": [{"type": "command", "command": command}]}]
            for name in events.BASE_HOOK_EVENTS
        }}))
        self.assertFalse(events.hooks_installed(self.settings))
        events.install_hooks(self.settings, self.runtime, self.workspace, sys.executable)
        value = json.loads(self.settings.read_text())
        for groups in value["hooks"].values():
            handlers = [handler for group in groups for handler in group["hooks"]]
            self.assertEqual(len(handlers), 1)
            self.assertEqual(shlex.split(handlers[0]["command"])[1:3], [str(EVENT_ENTRYPOINT), "events"])
        self.assertTrue(events.hooks_installed(self.settings))

    def test_invalid_existing_settings_are_never_overwritten(self):
        original = "{broken-json"
        self.settings.write_text(original)
        with self.assertRaises(ValueError):
            events.install_hooks(self.settings, self.runtime, self.workspace, sys.executable)
        self.assertEqual(self.settings.read_text(), original)
        self.assertFalse(events.hooks_installed(self.settings))

    def test_session_readers_work_with_read_only_runtime_and_never_create_it(self):
        missing = self.root / "not-created"
        self.assertEqual(events.session_list(missing), [])
        self.assertFalse(missing.exists())
        self.record("SessionStart")
        original_open = os.open

        def read_only_open(path, flags, *args, **kwargs):
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT))
            return original_open(path, flags, *args, **kwargs)

        with patch.object(events.os, "open", side_effect=read_only_open):
            self.assertEqual(events.session_list(self.runtime)[0]["id"], "session-1")
            self.assertEqual(self.detail()["status"], "idle")
        empty = self.root / "empty-runtime"
        empty.mkdir()
        self.assertEqual(events.session_list(empty), [])
        self.assertEqual(list(empty.iterdir()), [])

    def test_cli_fails_open_with_no_output_on_bad_input_storage_or_arguments(self):
        base = [sys.executable, str(EVENT_ENTRYPOINT), "events", "--runtime-dir", str(self.runtime), "--workspace", str(self.workspace)]
        for raw in ("not JSON", "[]", "{" + "x" * events.MAX_INPUT_BYTES):
            result = subprocess.run(base, input=raw, text=True, capture_output=True, cwd=REPO, timeout=15)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        result = subprocess.run([sys.executable, str(EVENT_ENTRYPOINT), "events"], input="{}", text=True, capture_output=True, cwd=REPO, timeout=15)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        payload = json.dumps(dict(session_id="cli", hook_event_name="SessionStart", cwd=str(self.workspace)))
        with patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload.encode()))), patch.object(events, "_record", side_effect=OSError("disk full")), patch.object(sys, "stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(events.main(base[3:]), 0)
            self.assertEqual(stdout.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
