"""Claude Code 的本项目事件采集；只使用 Python 标准库。

用户级 hooks 的 command 从 stdin 读取 JSON，采集失败始终以 0 退出，
不写 stdout、不返回审批决定，也不向模型注入上下文。记录的是运行迹象，
不是业务任务的成功证明。Skill 工具和直接 slash 都只表示技能被调用。

install_hooks 默认安装当前官方文档中的基础事件。较新的
UserPromptExpansion 需要安装时显式设置环境变量
CLAUDE_OBSERVER_USER_PROMPT_EXPANSION=1；先在目标版本 /hooks 中确认支持。
不支持此事件的版本无法仅凭 Skill 工具完整覆盖用户直接 /skill-name。

持久化格式是有界 JSONL 日志与原子 summary；JSONL 先写入，summary 随后
更新，读取时重放未进入 summary 的日志。所有读写共用 POSIX 文件锁。
只在 launcher 的 run_id、boot_id、PID 和启动 tick 均可校验时报告
process_alive；未关联会话保持 None。心跳和 PID 数字本身不构成存活证明。
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import uuid


MAX_INPUT_BYTES = 64 * 1024
MAX_EVENT_BYTES = 2048
MAX_LOG_BYTES = 256 * 1024
MAX_HISTORY_EVENTS = 512
MAX_SUMMARY_BYTES = 64 * 1024
MAX_SETTINGS_BYTES = 1024 * 1024
MAX_SESSIONS = 64
MAX_NAMES = 64
STALE_AFTER_SECONDS = 15 * 60

CONFIG_NAME = "event-config.json"
_DEFAULT_WORKSPACE = Path(__file__).resolve().parent.parent
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,127}\Z")
_SECRET_PREFIXES = ("sk-", "sk_", "ghp_", "github_pat_", "akia", "bearer")
_STATUSES = frozenset({"running", "idle", "waiting", "ended", "unknown"})
_SOURCES = frozenset({"startup", "resume", "clear", "compact", "fork"})
_REASONS = frozenset({"clear", "resume", "logout", "prompt_input_exit", "other"})
_NOTIFICATIONS = frozenset({
    "permission_prompt", "idle_prompt", "agent_needs_input", "elicitation_dialog",
    "elicitation_url_dialog", "elicitation_complete", "elicitation_response",
})
BASE_HOOK_EVENTS = (
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
    "PostToolUseFailure", "PermissionRequest", "PermissionDenied", "Notification",
    "SubagentStart", "SubagentStop", "Stop", "StopFailure", "SessionEnd",
)
_EVENTS = frozenset(BASE_HOOK_EVENTS + ("UserPromptExpansion",))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp() -> str:
    return _now().isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _date(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _safe_text(value, pattern):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        return None
    if value.lower().startswith(_SECRET_PREFIXES):
        return None
    if value.startswith("eyJ") and value.count(".") == 2:
        return None
    return value


def _safe_id(value):
    return _safe_text(value, _ID_RE)


def _safe_name(value):
    return _safe_text(value, _NAME_RE)


def _enum(value, values):
    return value if isinstance(value, str) and value in values else None


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def _directory(path: Path, create: bool) -> int:
    path = Path(path).expanduser().absolute()
    if path.is_symlink():
        raise ValueError("observer directory must not be a symlink")
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError("unsafe observer directory") from exc
        raise
    if create:
        os.fchmod(fd, 0o700)
    return fd


def _regular(dir_fd: int, name: str, flags: int) -> int:
    try:
        fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError("observer file must not be a symlink") from exc
        raise
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise ValueError("observer file must be a regular file with one link")
    return fd


def _check_target(dir_fd: int, name: str) -> None:
    try:
        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("unsafe observer file target")


def _atomic_write(dir_fd: int, name: str, data: bytes) -> None:
    _check_target(dir_fd, name)
    temporary = ".observer-" + uuid.uuid4().hex + ".tmp"
    fd = _regular(dir_fd, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        try:
            os.fsync(dir_fd)
        except OSError as exc:
            if exc.errno not in {errno.EINVAL, errno.ENOTSUP}:
                raise
    finally:
        try:
            os.unlink(temporary, dir_fd=dir_fd)
        except FileNotFoundError:
            pass


def _read_json(dir_fd: int, name: str, limit: int):
    try:
        fd = _regular(dir_fd, name, os.O_RDONLY)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("observer JSON file exceeds its size limit")
    try:
        return json.loads(data)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None


@contextmanager
def _locked(runtime_dir: Path, create: bool = False):
    root_fd = _directory(runtime_dir, create)
    lock_fd = None
    sessions_fd = None
    try:
        flags = os.O_RDWR | os.O_CREAT if create else os.O_RDONLY
        lock_fd = _regular(root_fd, "events.lock", flags)
        fcntl.flock(lock_fd, fcntl.LOCK_EX if create else fcntl.LOCK_SH)
        if create:
            try:
                os.mkdir("sessions", mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
        sessions_fd = os.open(
            "sessions", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd
        )
        yield root_fd, sessions_fd
    finally:
        if sessions_fd is not None:
            os.close(sessions_fd)
        if lock_fd is not None:
            os.close(lock_fd)
        os.close(root_fd)


def _workspace(value: Path) -> Path:
    result = Path(value).expanduser().resolve(strict=True)
    if not result.is_dir():
        raise ValueError("workspace must be a directory")
    return result


def _in_workspace(cwd, workspace: Path) -> bool:
    if not isinstance(cwd, str) or len(cwd) > 4096 or not Path(cwd).is_absolute():
        return False
    try:
        Path(cwd).resolve(strict=True).relative_to(workspace)
        return Path(cwd).is_dir()
    except (OSError, ValueError, RuntimeError):
        return False


def _configured_workspace(root_fd: int) -> Path:
    config = _read_json(root_fd, CONFIG_NAME, 8192)
    if config is None:
        # An existing but broken scope file must never broaden the collection scope.
        try:
            os.stat(CONFIG_NAME, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return _workspace(_DEFAULT_WORKSPACE)
        raise ValueError("invalid observer scope configuration")
    if not isinstance(config, dict) or not isinstance(config.get("workspace"), str):
        raise ValueError("invalid observer scope configuration")
    return _workspace(Path(config["workspace"]))


def _event_from_payload(payload: dict, at: str):
    kind = payload.get("hook_event_name")
    if _enum(kind, _EVENTS) is None:
        return None
    tool = _safe_name(payload.get("tool_name"))
    skill = None
    if tool == "Skill" and kind in {"PreToolUse", "PostToolUse", "PostToolUseFailure"}:
        tool_input = payload.get("tool_input")
        if isinstance(tool_input, dict):
            skill = _safe_name(tool_input.get("skill"))
    if kind == "UserPromptExpansion" and payload.get("expansion_type", "slash_command") == "slash_command":
        skill = _safe_name(payload.get("command_name"))
    event = {
        "at": at, "event": kind, "tool_name": tool, "status": "unknown",
        "skill_name": skill, "agent_id": _safe_id(payload.get("agent_id")),
        "agent_type": _safe_name(payload.get("agent_type")),
    }
    # These private, bounded identifiers are needed for replay, never tool contents.
    event["_tool_use_id"] = _safe_id(payload.get("tool_use_id"))
    event["_source"] = _enum(payload.get("source"), _SOURCES)
    event["_reason"] = _enum(payload.get("reason"), _REASONS)
    notification = payload.get("notification_type")
    event["_notification"] = _enum(notification, _NOTIFICATIONS)
    event["_run_id"] = _safe_id(os.environ.get("LAW_CRAWLER_RUN_ID"))
    event["_is_interrupt"] = payload.get("is_interrupt") is True
    duration = payload.get("duration_ms")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool) and 0 <= duration <= 31_536_000_000:
        event["_duration_ms"] = int(duration)
    return event


def _new_state(session_id: str, at: str) -> dict:
    return {
        "id": session_id, "started_at": at, "updated_at": at, "status": "unknown",
        "process_alive": None, "current_tool": None, "skill_names": [],
        "subagents": [], "event_count": 0, "run_id": None,
        "_active_tools": [], "_source": "hooks",
    }


def _clean_event(value):
    if not isinstance(value, dict) or _enum(value.get("event"), _EVENTS) is None or _date(value.get("at")) is None:
        return None
    seq = value.get("_seq")
    if type(seq) is not int or not 1 <= seq < 2**63:
        return None
    result = {key: None for key in ("tool_name", "skill_name", "agent_id", "agent_type")}
    result.update({"at": value["at"], "event": value["event"], "_seq": seq})
    for key in ("tool_name", "skill_name", "agent_type"):
        result[key] = _safe_name(value.get(key))
    result["agent_id"] = _safe_id(value.get("agent_id"))
    result["status"] = _enum(value.get("status"), _STATUSES) or "unknown"
    result["_tool_use_id"] = _safe_id(value.get("_tool_use_id"))
    result["_source"] = _enum(value.get("_source"), _SOURCES)
    result["_reason"] = _enum(value.get("_reason"), _REASONS)
    result["_notification"] = _enum(value.get("_notification"), _NOTIFICATIONS)
    result["_run_id"] = _safe_id(value.get("_run_id"))
    result["_is_interrupt"] = value.get("_is_interrupt") is True
    return result


def _agent(value):
    if not isinstance(value, dict):
        return None
    agent_id = _safe_id(value.get("id") or value.get("agent_id"))
    if agent_id is None:
        return None
    return {
        "id": agent_id, "agent_id": agent_id, "agent_type": _safe_name(value.get("agent_type")),
        "status": _enum(value.get("status"), _STATUSES) or "unknown",
        "started_at": value.get("started_at") if _date(value.get("started_at")) else None,
        "updated_at": value.get("updated_at") if _date(value.get("updated_at")) else None,
    }


def _clean_state(value, session_id: str):
    if not isinstance(value, dict) or value.get("id") != session_id:
        return None
    if _date(value.get("started_at")) is None or _date(value.get("updated_at")) is None:
        return None
    count = value.get("event_count")
    if type(count) is not int or not 0 <= count < 2**63:
        return None
    state = _new_state(session_id, value["started_at"])
    state["updated_at"] = value["updated_at"]
    state["event_count"] = count
    state["status"] = _enum(value.get("status"), _STATUSES) or "unknown"
    state["run_id"] = _safe_id(value.get("run_id"))
    names = value.get("skill_names", [])
    if isinstance(names, list):
        state["skill_names"] = list(dict.fromkeys(name for item in names[-MAX_NAMES:] if (name := _safe_name(item))))
    agents = value.get("subagents", [])
    if isinstance(agents, list):
        state["subagents"] = [agent for item in agents[-MAX_NAMES:] if (agent := _agent(item))]
    active = value.get("_active_tools", [])
    if isinstance(active, list):
        for item in active[-MAX_NAMES:]:
            if not isinstance(item, dict) or not _safe_name(item.get("tool_name")):
                continue
            state["_active_tools"].append({
                "tool_name": item["tool_name"], "agent_id": _safe_id(item.get("agent_id")),
                "id": _safe_id(item.get("id")),
            })
    state["_source"] = _enum(value.get("_source"), _SOURCES) or "hooks"
    _current_tool(state)
    return state


def _current_tool(state: dict) -> None:
    active = state["_active_tools"]
    main = [item for item in active if item["agent_id"] is None]
    state["current_tool"] = (main or active)[-1]["tool_name"] if active else None


def _apply_event(state: dict, event: dict) -> None:
    kind, at = event["event"], event["at"]
    state["updated_at"] = at
    state["event_count"] = event["_seq"]
    if kind == "SessionStart":
        state["run_id"] = event.get("_run_id")
    elif event.get("_run_id"):
        state["run_id"] = event["_run_id"]
    skill = event.get("skill_name")
    if skill:
        state["skill_names"] = [name for name in state["skill_names"] if name != skill] + [skill]
        state["skill_names"] = state["skill_names"][-MAX_NAMES:]
    agent_id = event.get("agent_id")
    tool = event.get("tool_name")
    call_id = event.get("_tool_use_id")
    if kind == "SessionStart":
        source = event.get("_source")
        state["_source"] = source or "hooks"
        if source != "compact" or state["status"] not in {"running", "waiting"}:
            state["status"] = "idle"
            state["_active_tools"] = []
    elif kind in {"UserPromptSubmit", "UserPromptExpansion"}:
        state["status"] = "running"
    elif kind == "PreToolUse":
        state["status"] = "waiting" if tool in {"AskUserQuestion", "ExitPlanMode"} else "running"
        if tool:
            state["_active_tools"] = [item for item in state["_active_tools"] if not (
                item["agent_id"] == agent_id and (item["id"] == call_id if call_id else item["tool_name"] == tool)
            )]
            state["_active_tools"].append({"id": call_id, "agent_id": agent_id, "tool_name": tool})
            state["_active_tools"] = state["_active_tools"][-MAX_NAMES:]
    elif kind in {"PostToolUse", "PostToolUseFailure", "PermissionDenied"}:
        state["_active_tools"] = [item for item in state["_active_tools"] if not (
            item["agent_id"] == agent_id and (item["id"] == call_id if call_id else item["tool_name"] == tool)
        )]
        state["status"] = "unknown" if kind == "PermissionDenied" or event.get("_is_interrupt") else "running"
    elif kind == "PermissionRequest":
        state["status"] = "waiting"
    elif kind == "Notification":
        notification = event.get("_notification")
        if notification == "idle_prompt":
            state["status"] = "idle"
        elif notification in {"permission_prompt", "agent_needs_input", "elicitation_dialog", "elicitation_url_dialog"}:
            state["status"] = "waiting"
        elif notification in {"elicitation_complete", "elicitation_response"}:
            state["status"] = "running"
    elif kind in {"SubagentStart", "SubagentStop"}:
        if agent_id:
            existing = next((agent for agent in state["subagents"] if agent["id"] == agent_id), None)
            state["subagents"] = [agent for agent in state["subagents"] if agent["id"] != agent_id]
            state["subagents"].append({
                "id": agent_id, "agent_id": agent_id, "agent_type": event.get("agent_type"),
                "status": "running" if kind == "SubagentStart" else "idle",
                "started_at": existing["started_at"] if existing else (at if kind == "SubagentStart" else None),
                "updated_at": at,
            })
            state["subagents"] = state["subagents"][-MAX_NAMES:]
            if kind == "SubagentStop":
                state["_active_tools"] = [item for item in state["_active_tools"] if item["agent_id"] != agent_id]
        if kind == "SubagentStart":
            state["status"] = "running"
    elif kind == "Stop":
        state["status"] = "idle"
        state["_active_tools"] = [item for item in state["_active_tools"] if item["agent_id"] is not None]
    elif kind == "StopFailure":
        state["status"] = "unknown"
        state["_active_tools"] = [item for item in state["_active_tools"] if item["agent_id"] is not None]
    elif kind == "SessionEnd":
        state["status"] = "ended"
        state["_active_tools"] = []
        for agent in state["subagents"]:
            if agent["status"] in {"running", "waiting"}:
                agent["status"] = "unknown"
    _current_tool(state)
    event["status"] = state["status"]


def _read_events(dir_fd: int, session_id: str) -> list:
    try:
        fd = _regular(dir_fd, session_id + ".jsonl", os.O_RDONLY)
    except FileNotFoundError:
        return []
    with os.fdopen(fd, "rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        offset = max(0, size - MAX_LOG_BYTES)
        stream.seek(offset)
        if offset:
            stream.readline(MAX_EVENT_BYTES + 1)
        data = stream.read(MAX_LOG_BYTES)
    records = []
    for line in data.splitlines(keepends=True):
        if not line.endswith(b"\n") or len(line) > MAX_EVENT_BYTES:
            continue
        try:
            event = _clean_event(json.loads(line))
        except (ValueError, UnicodeDecodeError, RecursionError):
            continue
        if event:
            records.append(event)
    return records[-MAX_HISTORY_EVENTS:]


def _load_state(dir_fd: int, session_id: str):
    raw = _read_json(dir_fd, session_id + ".json", MAX_SUMMARY_BYTES)
    state = _clean_state(raw, session_id)
    history = _read_events(dir_fd, session_id)
    if state is None:
        if not history:
            raise FileNotFoundError(session_id)
        state = _new_state(session_id, history[0]["at"])
    for event in history:
        if event["_seq"] > state["event_count"]:
            _apply_event(state, event)
    return state, history


def _append_event(dir_fd: int, session_id: str, event: dict, history: list) -> None:
    line = _json_bytes(event) + b"\n"
    if len(line) > MAX_EVENT_BYTES:
        raise ValueError("observer event exceeds its size limit")
    name = session_id + ".jsonl"
    fd = _regular(dir_fd, name, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    try:
        size = os.fstat(fd).st_size
        if size + len(line) <= MAX_LOG_BYTES and len(history) < MAX_HISTORY_EVENTS:
            # A crash can leave a partial tail. Replace that tail before the next append.
            read_fd = _regular(dir_fd, name, os.O_RDONLY)
            with os.fdopen(read_fd, "rb") as previous:
                if size:
                    previous.seek(size - 1)
                    complete = previous.read(1) == b"\n"
                else:
                    complete = True
            if complete:
                with os.fdopen(fd, "ab") as stream:
                    fd = None
                    stream.write(line)
                    stream.flush()
                    os.fsync(stream.fileno())
                return
        retained = history[-(MAX_HISTORY_EVENTS // 2):]
        lines = [_json_bytes(item) + b"\n" for item in retained] + [line]
        while len(lines) > 1 and sum(map(len, lines)) > MAX_LOG_BYTES:
            lines.pop(0)
        _atomic_write(dir_fd, name, b"".join(lines))
    finally:
        if fd is not None:
            os.close(fd)


def _prune(dir_fd: int, keep: str) -> None:
    sessions = {}
    for filename in os.listdir(dir_fd):
        suffix = ".jsonl" if filename.endswith(".jsonl") else ".json" if filename.endswith(".json") else None
        if not suffix:
            continue
        session_id = filename[:-len(suffix)]
        if not _safe_id(session_id):
            continue
        info = os.stat(filename, dir_fd=dir_fd, follow_symlinks=False)
        if stat.S_ISREG(info.st_mode):
            sessions[session_id] = max(sessions.get(session_id, 0), info.st_mtime_ns)
    excess = len(sessions) - MAX_SESSIONS
    for session_id in sorted(sessions, key=sessions.get):
        if excess <= 0:
            break
        if session_id == keep:
            continue
        for suffix in (".json", ".jsonl"):
            try:
                os.unlink(session_id + suffix, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
        excess -= 1


def _record(runtime_dir: Path, payload: dict, workspace: Path | None = None) -> None:
    if not isinstance(payload, dict):
        return
    session_id = _safe_id(payload.get("session_id"))
    if session_id is None or _enum(payload.get("hook_event_name"), _EVENTS) is None:
        return
    with _locked(runtime_dir, create=True) as (root_fd, sessions_fd):
        root = _workspace(workspace) if workspace is not None else _configured_workspace(root_fd)
        if not _in_workspace(payload.get("cwd"), root):
            return
        event = _event_from_payload(payload, _timestamp())
        try:
            state, history = _load_state(sessions_fd, session_id)
        except FileNotFoundError:
            state, history = _new_state(session_id, event["at"]), []
        event["_seq"] = state["event_count"] + 1
        _apply_event(state, event)
        summary = _json_bytes(state)
        if len(summary) > MAX_SUMMARY_BYTES:
            raise ValueError("observer summary exceeds its size limit")
        _append_event(sessions_fd, session_id, event, history)
        _atomic_write(sessions_fd, session_id + ".json", summary)
        _prune(sessions_fd, session_id)


def record_event(runtime_dir: Path, payload: dict) -> None:
    """记录本项目的白名单元数据；无效事件或项目外 cwd 直接忽略。"""
    _record(Path(runtime_dir), payload)


def _process_alive(root_fd: int, run_id):
    if not _safe_id(run_id):
        return None
    runs_fd = None
    try:
        runs_fd = os.open("runs", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        run = _read_json(runs_fd, run_id + ".json", 8192)
        if not isinstance(run, dict) or run.get("id") != run_id:
            return None
        pid, boot_id, started = run.get("pid"), run.get("boot_id"), run.get("process_start")
        if type(pid) is not int or not 1 <= pid <= 2**31:
            return None
        if not isinstance(boot_id, str) or not _safe_id(boot_id):
            return None
        if not isinstance(started, str) or not started.isascii() or not started.isdigit() or len(started) > 32:
            return None
        current_boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if current_boot != boot_id:
            return False
        try:
            with Path(f"/proc/{pid}/stat").open("r") as process:
                fields = process.read(8192).rsplit(")", 1)[1].split()
        except FileNotFoundError:
            return False
        return fields[19] == started and fields[0] not in {"Z", "X"}
    except (OSError, ValueError, IndexError):
        return None
    finally:
        if runs_fd is not None:
            os.close(runs_fd)


def _public_summary(state: dict, root_fd: int) -> dict:
    result = {key: state[key] for key in (
        "id", "started_at", "updated_at", "status", "process_alive", "current_tool",
        "skill_names", "subagents", "event_count", "run_id",
    )}
    result["process_alive"] = _process_alive(root_fd, state["run_id"])
    age = (_now() - _date(state["updated_at"])).total_seconds()
    if age > STALE_AFTER_SECONDS or result["process_alive"] is False:
        if result["status"] in {"running", "waiting"}:
            result["status"] = "unknown"
        result["current_tool"] = None
    if result["process_alive"] is False and result["status"] != "ended":
        result["status"] = "unknown"
    result["subagents"] = [dict(agent) for agent in state["subagents"]]
    for agent in result["subagents"]:
        agent_time = _date(agent["updated_at"])
        stale = agent_time is None or (_now() - agent_time).total_seconds() > STALE_AFTER_SECONDS
        if agent["status"] in {"running", "waiting"} and (stale or result["process_alive"] is False):
            agent["status"] = "unknown"
    return result


def session_list(runtime_dir: Path) -> list:
    """返回最近会话；缺失 runtime 视为空列表，不猜测进程存活。"""
    try:
        with _locked(Path(runtime_dir)) as (root_fd, sessions_fd):
            ids = {name.rsplit(".", 1)[0] for name in os.listdir(sessions_fd) if name.endswith((".json", ".jsonl"))}
            results = []
            for session_id in ids:
                if not _safe_id(session_id):
                    continue
                try:
                    state, _ = _load_state(sessions_fd, session_id)
                    results.append(_public_summary(state, root_fd))
                except (OSError, ValueError):
                    continue
            return sorted(results, key=lambda row: row["updated_at"], reverse=True)[:MAX_SESSIONS]
    except FileNotFoundError:
        return []


def session_detail(runtime_dir: Path, session_id: str) -> dict:
    """会话不存在时抛 FileNotFoundError；危险 ID 抛 ValueError。"""
    if _safe_id(session_id) is None:
        raise ValueError("invalid session identifier")
    with _locked(Path(runtime_dir)) as (root_fd, sessions_fd):
        state, history = _load_state(sessions_fd, session_id)
        result = _public_summary(state, root_fd)
        result["source"] = state["_source"]
        result["events"] = [{key: event.get(key) for key in (
            "at", "event", "tool_name", "status", "skill_name", "agent_id", "agent_type",
        )} for event in history]
        return result


def _managed_handler(handler) -> bool:
    if not isinstance(handler, dict) or handler.get("type") != "command":
        return False
    try:
        parts = shlex.split(handler.get("command", ""))
    except (TypeError, ValueError):
        return False
    return any(parts[index:index + 2] == ["-m", "observer.events"] for index in range(len(parts) - 1)) and "--runtime-dir" in parts and "--workspace" in parts


def install_hooks(settings_path: Path, runtime_dir: Path, workspace: Path, python_executable: str) -> None:
    """幂等合并用户 hooks；不修改其他设置，不把密钥写入配置。"""
    settings_path = Path(settings_path).expanduser().absolute()
    runtime_dir = Path(runtime_dir).expanduser().absolute()
    workspace = _workspace(workspace)
    if not python_executable or "\0" in python_executable:
        raise ValueError("invalid Python executable")
    command = "cd " + shlex.quote(str(_DEFAULT_WORKSPACE)) + " && " + " ".join(shlex.quote(value) for value in (
        str(python_executable), "-m", "observer.events", "--runtime-dir", str(runtime_dir),
        "--workspace", str(workspace),
    ))
    enabled = os.environ.get("CLAUDE_OBSERVER_USER_PROMPT_EXPANSION", "").lower() in {"1", "true", "yes"}
    chosen = BASE_HOOK_EVENTS + (("UserPromptExpansion",) if enabled else ())
    with _locked(runtime_dir, create=True) as (root_fd, _):
        parent_fd = _directory(settings_path.parent, True)
        settings_lock = None
        try:
            settings_lock = _regular(parent_fd, ".observer-hooks.lock", os.O_RDWR | os.O_CREAT)
            fcntl.flock(settings_lock, fcntl.LOCK_EX)
            settings = _read_json(parent_fd, settings_path.name, MAX_SETTINGS_BYTES)
            if settings is None:
                try:
                    os.stat(settings_path.name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    settings = {}
                else:
                    raise ValueError("settings JSON is invalid; refusing to overwrite it")
            if not isinstance(settings, dict) or not isinstance(settings.get("hooks", {}), dict):
                raise ValueError("settings hooks must be an object")
            hooks = settings.setdefault("hooks", {})
            for name in tuple(hooks):
                groups = hooks[name]
                if not isinstance(groups, list):
                    raise ValueError("hook event groups must be lists")
                remaining = []
                for group in groups:
                    if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                        raise ValueError("hook groups must contain a hooks list")
                    handlers = [handler for handler in group["hooks"] if not _managed_handler(handler)]
                    if handlers or not group["hooks"]:
                        remaining.append(dict(group, hooks=handlers))
                if remaining:
                    hooks[name] = remaining
                else:
                    del hooks[name]
            for name in chosen:
                hooks.setdefault(name, []).append({"hooks": [{"type": "command", "command": command, "timeout": 2}]})
            data = _json_bytes(settings)
            if len(data) > MAX_SETTINGS_BYTES:
                raise ValueError("merged settings exceed the size limit")
            _atomic_write(root_fd, CONFIG_NAME, _json_bytes({"version": 1, "workspace": str(workspace), "prompt_expansion": enabled}))
            _atomic_write(parent_fd, settings_path.name, data)
        finally:
            if settings_lock is not None:
                os.close(settings_lock)
            os.close(parent_fd)


def hooks_installed(settings_path: Path) -> bool:
    """确认基础事件均有本模块 handler；无效或不安全配置返回 False。"""
    settings_path = Path(settings_path).expanduser().absolute()
    parent_fd = None
    try:
        parent_fd = _directory(settings_path.parent, False)
        settings = _read_json(parent_fd, settings_path.name, MAX_SETTINGS_BYTES)
        hooks = settings.get("hooks", {}) if isinstance(settings, dict) else {}
        if not isinstance(hooks, dict):
            return False
        return all(any(
            _managed_handler(handler)
            for group in hooks.get(name, []) if isinstance(group, dict)
            for handler in group.get("hooks", [])
        ) for name in BASE_HOOK_EVENTS)
    except (OSError, ValueError, TypeError):
        return False
    finally:
        if parent_fd is not None:
            os.close(parent_fd)


class _SilentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid observer command arguments")


def main(argv=None) -> int:
    """Hook 的 fail-open 入口：任何输入/存储错误均静默返回 0。"""
    try:
        parser = _SilentParser(add_help=False, exit_on_error=False)
        parser.add_argument("--runtime-dir", required=True, type=Path)
        parser.add_argument("--workspace", required=True, type=Path)
        args, extra = parser.parse_known_args(argv)
        if extra:
            return 0
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            return 0
        payload = json.loads(raw)
        _record(args.runtime_dir, payload, args.workspace)
    except BaseException:
        # 此入口不能改变 Claude 的审批或执行结果，包括 argparse/SystemExit。
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
