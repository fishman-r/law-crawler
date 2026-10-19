"""读取采集证据的只读快照；不调用会取锁或重建数据的 CrawlStore。"""
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
from urllib.parse import urlsplit, urlunsplit


MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_BATCHES = 500
MAX_ATTEMPTS = 200
MAX_STATES = 2000
MAX_ERRORS = 100
MAX_PLATFORMS = 100
MAX_DIRECTORY_ENTRIES = 5000
MAX_TEXT = 4000
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}\Z")
_URL = re.compile(r"(?i)\bhttps?://[^\s<>\"']+")
_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:[a-z0-9_-]*(?:api[_-]?key|auth[_-]?token|access[_-]?token|"
    r"refresh[_-]?token|client[_-]?secret|password|passwd|secret|"
    r"access[_-]?key(?:[_-]?id)?|credential)|authorization|token)"
    r"\b[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)"
)
_BEARER = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
_KEY = re.compile(r"\b(?:sk|pk)-(?:ant-)?[A-Za-z0-9_-]{12,}\b")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S
)
_SECRET_KEYS = {
    "key", "apikey", "xapikey", "authorization", "proxyauthorization", "token",
    "accesstoken", "refreshtoken", "authtoken", "password", "passwd", "secret",
    "clientsecret", "privatekey", "cookie", "setcookie",
}
_ALIASES = {
    "success": "success", "succeeded": "success", "fail": "failed",
    "failed": "failed", "pending": "pending", "running": "running",
    "skip": "skipped", "skipped": "skipped",
}


def _redact_url(match):
    value = match.group(0)
    try:
        parts = urlsplit(value)
        netloc = parts.netloc
        if "@" in netloc:
            netloc = "[REDACTED]@" + netloc.rsplit("@", 1)[1]
        return urlunsplit((parts.scheme, netloc, parts.path,
                           "[REDACTED]" if parts.query else "",
                           "[REDACTED]" if parts.fragment else ""))
    except ValueError:
        return "[REDACTED URL]"


def redact(value):
    """脱敏常见密钥赋值、认证头和 URL 的全部 query/fragment；不改动证据原件。"""
    if isinstance(value, str):
        value = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", value)
        value = _URL.sub(_redact_url, value)
        value = _BEARER.sub(lambda m: m.group(1) + " [REDACTED]", value)
        value = _ASSIGNMENT.sub(lambda m: m.group(1) + "[REDACTED]", value)
        return _KEY.sub("[REDACTED]", value)
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            sensitive = normalized in _SECRET_KEYS or normalized.endswith(
                ("apikey", "authtoken", "accesstoken", "refreshtoken", "password", "clientsecret",
                 "secretaccesskey", "accesskeyid", "credential")
            )
            result[key] = "[REDACTED]" if sensitive else redact(item)
        return result
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def _text(value, maximum=MAX_TEXT):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    return redact(str(value))[:maximum]


def _number(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _time(value):
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _status(report, counts, has_attempt):
    result = report.get("result") if report else None
    if result in ("completed", "completed_with_errors", "planned_only"):
        # 已结束的旧报告偶有 result 与逐项错误不一致，仍应展示错误。
        if result == "completed" and (counts["failed"] or report.get("errors")):
            return "completed_with_errors"
        return result
    if result in ("failed", "interrupted"):
        return "unfinished"
    if report and _time(report.get("finished_at")):
        return "completed_with_errors" if counts["failed"] or report.get("errors") else "completed"
    if report:
        return "unknown"
    return "unfinished" if has_attempt else "pending"


class SnapshotReader:
    """根目录由服务配置固定；API 输入只能选择安全批次 ID，不能指定文件路径。"""

    def __init__(self, workspace: Path, download_base: Path):
        for path in (workspace, download_base):
            if Path(path).is_symlink():
                raise ValueError("工作区及下载根目录不得使用符号链接")
        self.workspace = Path(workspace).resolve()
        self.download_base = Path(download_base).resolve()

    @contextmanager
    def _open(self, parts, directory=False):
        """POSIX 用目录描述符逐层打开，防止检查路径后被替换成符号链接。"""
        if any(not part or part in (".", "..") or "/" in part or "\\" in part for part in parts):
            raise ValueError("不允许的读取路径")
        descriptor = None
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        directory_flags = flags | getattr(os, "O_DIRECTORY", 0)
        try:
            if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
                descriptor = os.open(self.download_base, directory_flags)
                for index, part in enumerate(parts):
                    is_directory = index < len(parts) - 1 or directory
                    next_descriptor = os.open(
                        part, directory_flags if is_directory else flags | getattr(os, "O_NONBLOCK", 0),
                        dir_fd=descriptor,
                    )
                    os.close(descriptor)
                    descriptor = next_descriptor
            else:
                # 其他平台保留同样的固定根和逐层符号链接检查。
                target = self.download_base
                if target.is_symlink():
                    raise ValueError("读取根目录不得使用符号链接")
                for part in parts:
                    target = target / part
                    if target.is_symlink():
                        raise ValueError("读取路径不得使用符号链接")
                descriptor = os.open(target, directory_flags if directory else flags)
            info = os.fstat(descriptor)
            if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
                raise ValueError("只允许读取常规文件和目录")
            yield descriptor, info
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise ValueError("读取路径包含符号链接或非目录") from None
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _directories(self, parts):
        entries = []
        truncated = False
        try:
            with self._open(parts, directory=True) as (descriptor, _):
                # scandir(fd) 不跟随目录项指向的符号链接。
                source = descriptor if os.scandir in os.supports_fd else self.download_base.joinpath(*parts)
                with os.scandir(source) as iterator:
                    for index, entry in enumerate(iterator):
                        if index >= MAX_DIRECTORY_ENTRIES:
                            truncated = True
                            break
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                entries.append((entry.name, entry.stat(follow_symlinks=False).st_mtime))
                        except OSError:
                            continue
        except FileNotFoundError:
            pass
        return entries, truncated

    def _json(self, parts, warnings, modified, optional=False):
        label = "/".join(parts[2:]) or parts[-1]
        try:
            with self._open(parts) as (descriptor, info):
                modified.append(info.st_mtime)
                if info.st_size > MAX_JSON_BYTES:
                    warnings.append(label + ": 超过 JSON 读取大小上限")
                    return None
                chunks = []
                remaining = MAX_JSON_BYTES + 1
                while remaining:
                    chunk = os.read(descriptor, min(65536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                raw = b"".join(chunks)
                if len(raw) > MAX_JSON_BYTES:
                    warnings.append(label + ": 超过 JSON 读取大小上限")
                    return None
            return json.loads(raw.decode("utf-8-sig"))
        except FileNotFoundError:
            if not optional:
                warnings.append(label + ": 文件缺失")
        except (OSError, ValueError, UnicodeError, RecursionError):
            warnings.append(label + ": 无法安全读取或解析 JSON")
        return None

    def _mapping(self, parts, warnings, modified, optional=False):
        value = self._json(parts, warnings, modified, optional)
        if value is None:
            return None
        if not isinstance(value, dict):
            warnings.append(parts[-1] + ": 顶层结构应为对象")
            return None
        return value

    def _states(self, document, report, warnings):
        raw = document.get("states") if document else None
        if not isinstance(raw, (dict, list)):
            raw = report.get("states", {}) if report else {}
        if isinstance(raw, dict):
            iterator = raw.items()
            total = len(raw)
        elif isinstance(raw, list):
            iterator = ((str(index), item) for index, item in enumerate(raw))
            total = len(raw)
        else:
            warnings.append("任务状态结构无法识别")
            iterator, total = (), 0
        counts = dict(success=0, failed=0, pending=0, running=0, skipped=0)
        states = []
        unknown = 0
        for index, (key, item) in enumerate(iterator):
            if index >= MAX_STATES:
                break
            if not isinstance(item, dict):
                warnings.append("忽略无法识别的任务状态")
                continue
            raw_status = item.get("status")
            status = _ALIASES.get(raw_status, "unknown") if isinstance(raw_status, str) else "unknown"
            if status in counts:
                counts[status] += 1
            else:
                unknown += 1
            states.append(dict(
                key=_text(item.get("key", key), 500), status=status,
                reason=_text(item.get("reason")), platform=_text(item.get("platform"), 200),
                country=_text(item.get("country"), 120), checked_at=_time(item.get("checked_at")),
                attempts=_number(item.get("attempts")) or 0,
            ))
        if total > MAX_STATES:
            warnings.append(f"任务列表及计数已截断至 {MAX_STATES} 项；它们不是文件成功率")
        if unknown:
            warnings.append(f"{unknown} 项任务状态未知，未计入已知状态数量")
        return states, counts

    def _errors(self, report, warnings):
        raw = report.get("errors", []) if report else []
        if not isinstance(raw, list):
            raw = [raw] if raw else []
        errors = []
        for item in raw[:MAX_ERRORS]:
            if isinstance(item, dict):
                fields = {key: _text(item[key]) for key in
                          ("platform", "country", "url", "error", "reason", "type", "code") if key in item}
                errors.append(fields or {"error": "未识别的错误记录"})
            else:
                errors.append({"error": _text(item)})
        if len(raw) > MAX_ERRORS:
            warnings.append(f"错误详情已截断至 {MAX_ERRORS} 项")
        return errors, len(raw)

    def _platforms(self, plan, report, warnings):
        raw = report.get("platforms", plan.get("platforms", [])) if report else plan.get("platforms", [])
        if not isinstance(raw, list):
            return []
        values = []
        for item in raw[:MAX_PLATFORMS]:
            if isinstance(item, dict):
                row = {key: _text(item[key], 500) for key in
                       ("platform", "country", "status") if key in item}
                for key in ("file_count", "detail_pages"):
                    if _number(item.get(key)) is not None:
                        row[key] = item[key]
                if "checked_at" in item:
                    row["checked_at"] = _time(item["checked_at"])
                values.append(row)
            elif isinstance(item, str):
                values.append({"platform": _text(item, 200), "status": "unknown"})
        if len(raw) > MAX_PLATFORMS:
            warnings.append(f"平台列表已截断至 {MAX_PLATFORMS} 项")
        return values

    def _batch(self, identifier, detail):
        if not isinstance(identifier, str) or not _SAFE_ID.fullmatch(identifier):
            raise ValueError("批次标识必须为安全的单个目录名")
        prefix = (".batches", identifier)
        # 不把符号链接批次伪装成普通不存在的记录。
        with self._open(prefix, directory=True) as (_, info):
            fallback_modified = info.st_mtime
        warnings, modified = [], []
        plan = self._mapping(prefix + ("plan.json",), warnings, modified) or {}
        entries, truncated = self._directories(prefix + ("attempts",))
        entries = [(name, at) for name, at in entries if name.isascii() and name.isdigit() and int(name) > 0]
        entries.sort(key=lambda entry: int(entry[0]), reverse=True)
        attempt_count = len(entries)
        if truncated:
            warnings.append("执行轮次目录扫描达到上限，轮次数量可能不完整")
        if len(entries) > MAX_ATTEMPTS:
            warnings.append(f"轮次详情仅保留最近 {MAX_ATTEMPTS} 轮")
        histories = []
        latest_report = None
        latest_document = None
        execution_plan = {}
        states = []
        counts = dict(success=0, failed=0, pending=0, running=0, skipped=0)
        for index, (name, _) in enumerate(entries[:MAX_ATTEMPTS if detail else 1]):
            attempt_prefix = prefix + ("attempts", name)
            # 历史只需终态摘要；列表只读最新轮，避免轮询时读取全部历史。
            local_modified = modified if index == 0 else []
            report = self._mapping(attempt_prefix + ("report.json",), warnings, local_modified, optional=True)
            document = self._mapping(attempt_prefix + ("states.json",), warnings, local_modified,
                                     optional=report is not None)
            parsed_states, parsed_counts = self._states(document, report, warnings)
            if index == 0:
                latest_report, latest_document = report, document
                states, counts = parsed_states, parsed_counts
                execution_plan = self._mapping(attempt_prefix + ("execution-plan.json",), warnings,
                                               modified, optional=True) or {}
            histories.append(dict(
                number=int(name), status=_status(report, parsed_counts, True),
                started_at=_time((report or {}).get("started_at")) or _time((document or {}).get("started_at")),
                finished_at=_time((report or {}).get("finished_at")),
            ))
        report = latest_report
        errors, error_count = self._errors(report, warnings)
        timestamps = [_time(plan.get("created_at")), _time(execution_plan.get("created_at")),
                      _time((latest_document or {}).get("started_at")),
                      _time((report or {}).get("started_at")), _time((report or {}).get("finished_at"))]
        timestamps.extend(state["checked_at"] for state in states)
        timestamps = [value for value in timestamps if value]
        updated_at = max(timestamps) if timestamps else datetime.fromtimestamp(
            max(modified or [fallback_modified]), timezone.utc).isoformat()
        summary = None
        if report is not None:
            summary = dict(result=_text(report.get("result"), 200) or None,
                           metadata_count=_number(report.get("metadata_count")),
                           unique_files=_number(report.get("unique_files")), error_count=error_count,
                           finished_at=_time(report.get("finished_at")))
        result = dict(
            id=identifier, jurisdiction=_text(plan.get("jurisdiction") or plan.get("country") or
                                            (report or {}).get("jurisdiction") or (report or {}).get("country"), 120),
            mode=_text(plan.get("mode") or (report or {}).get("mode"), 120),
            created_at=_time(plan.get("created_at")) or _time(execution_plan.get("created_at")) or
                       _time((report or {}).get("started_at")),
            updated_at=updated_at, status=_status(report, counts, bool(entries)),
            attempt_number=int(entries[0][0]) if entries else None, attempt_count=attempt_count,
            counts=counts, platforms=self._platforms(plan, report, warnings), errors=errors,
            states=states, attempts=histories, report=summary,
            warnings=list(dict.fromkeys(warnings))[:100],
        )
        if not detail:
            for key in ("states", "attempts", "errors"):
                del result[key]
        return result

    def batches(self):
        """至多返回 MAX_BATCHES 条简表；无效及符号链接目录不参与扫描。"""
        entries, truncated = self._directories((".batches",))
        entries = [(name, at) for name, at in entries if _SAFE_ID.fullmatch(name)]
        entries.sort(key=lambda entry: entry[1], reverse=True)
        rows = []
        for name, _ in entries[:MAX_BATCHES]:
            try:
                row = self._batch(name, detail=False)
            except (OSError, ValueError):
                continue
            if truncated or len(entries) > MAX_BATCHES:
                row["warnings"].append(f"批次列表最多显示 {MAX_BATCHES} 项，目录扫描有上限")
            rows.append(row)
        rows.sort(key=lambda row: (row["updated_at"], row["id"]), reverse=True)
        return rows

    def batch(self, identifier):
        """不存在时抛 FileNotFoundError；非法 ID 或不安全批次路径抛 ValueError。"""
        return self._batch(identifier, detail=True)


def host_metrics(path):
    """Linux 从 proc 读取资源概况；不支持的项目返回 None，不启动外部命令。"""
    result = dict(hostname=socket.gethostname(), load_1=None, uptime_seconds=None,
                  memory_total_mb=None, memory_available_mb=None, disk_total_gb=None, disk_free_gb=None)
    try:
        load = os.getloadavg()[0]
        if math.isfinite(load):
            result["load_1"] = round(load, 2)
    except (AttributeError, OSError):
        pass
    try:
        seconds = float(Path("/proc/uptime").read_text(encoding="ascii").split()[0])
        if math.isfinite(seconds) and seconds >= 0:
            result["uptime_seconds"] = int(seconds)
    except (OSError, ValueError, IndexError):
        pass
    try:
        memory = {}
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            key, _, value = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                memory[key] = int(value.split()[0])
        if memory.get("MemTotal", -1) >= 0:
            result["memory_total_mb"] = round(memory["MemTotal"] / 1024, 1)
        if memory.get("MemAvailable", -1) >= 0:
            result["memory_available_mb"] = round(memory["MemAvailable"] / 1024, 1)
    except (OSError, ValueError, IndexError):
        pass
    try:
        info = os.statvfs(Path(path))
        result["disk_total_gb"] = round(info.f_blocks * info.f_frsize / (1024 ** 3), 2)
        result["disk_free_gb"] = round(info.f_bavail * info.f_frsize / (1024 ** 3), 2)
    except (AttributeError, OSError, TypeError):
        pass
    return result
