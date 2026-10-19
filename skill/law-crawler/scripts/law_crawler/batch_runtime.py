"""持久执行批次、有限退避重试，以及可供人工阅读的运行报告。

只使用标准库；与任意 agent / 平台适配器配合。max_attempts 是总请求次数，
包含首次请求（默认 3 次），并非首次之后再请求 3 次。
"""
import copy
import html
import json
import math
import re
import socket
import ssl
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from law_crawler.crawl_store import atomic_write, write_json
from law_crawler.file_lock import FileLock


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,119}', value):
        raise ValueError('批次标识必须为安全的单个目录名')
    return value


def _text(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return '' if value is None else str(value)


def _md(value):
    text = _text(value).replace('\\', '\\\\').replace('<', '&lt;').replace('>', '&gt;')
    for char in ('|', '`', '[', ']', '*', '_'):
        text = text.replace(char, '\\' + char)
    return text.replace('\n', '<br>')


def _safe_url(value):
    try:
        parts = urlsplit(value)
        return parts.scheme in ('https', 'http') and bool(parts.hostname) and not parts.username and not any(ord(c) < 32 for c in value)
    except (TypeError, ValueError):
        return False


def _cell(value):
    if isinstance(value, (dict, list)):
        details = html.escape(json.dumps(value, ensure_ascii=False, indent=2), quote=True)
        return f'<details><summary>查看详情（{len(value)} 项）</summary><pre>{details}</pre></details>'
    text = _text(value)
    escaped = html.escape(text, quote=True)
    if _safe_url(text):
        return f'<a href="{escaped}" rel="noopener noreferrer">{escaped}</a>'
    return escaped


def _rows(value):
    if isinstance(value, list):
        return [item if isinstance(item, dict) else {'value': item} for item in value]
    if isinstance(value, dict):
        return [{'key': key, **item} if isinstance(item, dict) else {'key': key, 'value': item} for key, item in value.items()]
    return []


LABELS = {'schema_version':'报告版本','jurisdiction':'适用国家/法域','mode':'采集模式',
'batch_id':'批次编号','plan_file':'执行计划','selection_policy':'版本选择规则',
'migration_errors':'历史数据恢复问题','result':'执行结果','unique_files':'原件文件数',
'metadata_count':'元数据条目数','attempt_number':'执行轮次','started_at':'开始时间（UTC）',
'finished_at':'结束时间（UTC）','candidate_id':'法规条目标识','country':'国家',
'file':'本地文件','title':'法规名称','selection':'最新版本选择','key':'任务标识',
'platform':'官方平台','detail_url':'详情来源','status':'状态','reason':'原因',
'attempts':'请求次数','checked_at':'记录时间（UTC）','metadata_file':'元数据文件',
'url':'原件来源','download_url':'原件链接','request_events':'重试明细','error':'失败原因',
'detail_pages':'检查详情页数','errors':'错误记录','sha256':'内容SHA-256',
'downloaded':'新增文件','existing':'复用已有文件','content_duplicate':'内容重复',
'previous_success':'复用前轮成功结果','metadata_updated':'元数据已更新',
'body_review':'正文身份核验','evidence':'核验证据','reviewed_at':'核验时间（UTC）'}


def _field_label(key):
    return LABELS.get(key, key)


def emit_report(report, directory, name='report'):
    """原子输出 JSON / Markdown / HTML。来源值全部转义，HTML 不包含脚本。"""
    safe_id(name)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    title = report.get('title') or '法规下载执行报告'
    md = [f'# {_md(title)}', '', '> 本报告记录本次执行结果；下载成功不代表法律效力已核验。', '']
    body = [f'<h1>{html.escape(_text(title))}</h1>', '<p class="note">本报告记录本次执行结果；下载成功不代表法律效力已核验。</p>']
    overview = {key: value for key, value in report.items() if key not in ('title', 'counts', 'files', 'errors', 'platforms', 'states', 'catalog')}
    if overview:
        md += ['## 执行信息', '', '| 项目 | 值 |', '| --- | --- |']
        body.append('<section><h2>执行信息</h2><dl>')
        for key, value in overview.items():
            md.append(f'| {_md(_field_label(key))} | {_md(value)} |')
            body.append(f'<dt>{html.escape(_field_label(key))}</dt><dd>{_cell(value)}</dd>')
        md.append('')
        body.append('</dl></section>')
    counts = report.get('counts', {})
    if isinstance(counts, dict) and counts:
        md += ['## 数量汇总', '', '| 状态 | 数量 |', '| --- | --- |']
        body.append('<section><h2>数量汇总</h2><div class="cards">')
        for key, value in counts.items():
            md.append(f'| {_md(_field_label(key))} | {_md(value)} |')
            body.append(f'<div class="card"><span>{html.escape(_field_label(key))}</span><strong>{_cell(value)}</strong></div>')
        md.append('')
        body.append('</div></section>')
    for key, label in [('catalog', '法规目录与元数据'), ('states', '逐文件执行状态'), ('files', '下载文件'), ('errors', '错误与原因'), ('platforms', '平台结果')]:
        rows = _rows(report.get(key))
        md += [f'## {label}', '']
        body.append(f'<section><h2>{label}</h2>')
        if rows:
            columns = list(dict.fromkeys(column for row in rows for column in row))
            md += ['| ' + ' | '.join(_md(_field_label(c)) for c in columns) + ' |', '| ' + ' | '.join('---' for _ in columns) + ' |']
            body.append('<div class="table-wrap"><table><thead><tr>' + ''.join(f'<th>{html.escape(_field_label(c))}</th>' for c in columns) + '</tr></thead><tbody>')
            for row in rows:
                md.append('| ' + ' | '.join(_md(row.get(c)) for c in columns) + ' |')
                body.append('<tr>' + ''.join(f'<td>{_cell(row.get(c))}</td>' for c in columns) + '</tr>')
            body.append('</tbody></table></div>')
        else:
            md.append('无记录。')
            body.append('<p class="note">无记录。</p>')
        md.append('')
        body.append('</section>')
    css = '''body{font:16px/1.55 system-ui,sans-serif;margin:0;background:#f3f5f8;color:#202a38}main{max-width:1280px;margin:32px auto;padding:0 24px}h1{font-size:28px}h2{font-size:20px;margin-top:0}section{background:white;border:1px solid #dce2eb;border-radius:10px;padding:20px;margin:20px 0}.note{color:#667386}.cards{display:flex;flex-wrap:wrap;gap:16px}.card{background:#edf3fb;border-radius:8px;min-width:120px;padding:14px}.card span,.card strong{display:block}.card strong{font-size:24px}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;vertical-align:top;border-bottom:1px solid #dce2eb;padding:10px;overflow-wrap:anywhere;min-width:100px}th{background:#edf3fb}tr:nth-child(even){background:#f8fafc}a{color:#2458a4}dl{display:grid;grid-template-columns:180px 1fr;gap:8px 16px}dt{font-weight:600}dd{margin:0;overflow-wrap:anywhere}summary{cursor:pointer;color:#2458a4}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-width:600px;font-size:13px;background:#f3f5f8;padding:10px}@media(max-width:600px){main{padding:0 12px}dl{grid-template-columns:1fr}dd{margin-bottom:10px}}@media print{body{background:white}main{margin:0}.table-wrap{overflow:visible}section{break-inside:avoid}}'''
    page = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'"><title>' + html.escape(_text(title)) + '</title><style>' + css + '</style></head><body><main>' + ''.join(body) + '</main></body></html>\n'
    paths = {suffix: directory / f'{name}.{suffix}' for suffix in ('json', 'md', 'html')}
    write_json(paths['json'], report)
    atomic_write(paths['md'], ('\n'.join(md) + '\n').encode('utf-8'))
    atomic_write(paths['html'], page.encode('utf-8'))
    return paths


def _temporary(exc):
    if isinstance(exc, HTTPError):
        return exc.code == 429 or 500 <= exc.code <= 599
    if isinstance(exc, ssl.SSLCertVerificationError):
        return False
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, URLError):
        return not isinstance(exc.reason, ssl.SSLCertVerificationError)
    return False


def retry_fetch(fetcher, url, max_attempts=3, base_delay=1, sleep=time.sleep, event_callback=None):
    """临时网络失败指数退避；403 / 数据格式错误不会重试。"""
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
        raise ValueError('max_attempts 必须为正整数（包括首次请求）')
    if not isinstance(base_delay, (int, float)) or not math.isfinite(base_delay) or base_delay < 0:
        raise ValueError('base_delay 必须为非负数')
    for attempt in range(1, max_attempts + 1):
        try:
            result = fetcher(url)
        except Exception as exc:
            retry = attempt < max_attempts and _temporary(exc)
            # 请求次数可配置，但单次等待最多 30 秒，以便 agent 能及时响应用户。
            delay = min(base_delay * 2 ** min(attempt - 1, 30), 30) if retry else 0
            if event_callback:
                event_callback(dict(url=url, attempt=attempt, attempts=attempt, status='retrying' if retry else 'failed', reason=str(exc), error_type=type(exc).__name__, backoff_seconds=delay, checked_at=timestamp()))
            if not retry:
                try:
                    exc.retry_attempts = attempt
                except (AttributeError, TypeError):
                    pass
                raise
            sleep(delay)
        else:
            if event_callback:
                event_callback(dict(url=url, attempt=attempt, attempts=attempt, status='succeeded', reason='请求成功', backoff_seconds=0, checked_at=timestamp()))
            return result


class BatchRuntime:
    """不可变计划和每轮状态。rerun_from 是同一 plan_dir 内已有批次 ID。"""
    def __init__(self, plan_dir, plan_dict=None, batch_id=None, rerun_from=None):
        self._is_rerun = rerun_from is not None
        self.root = Path(plan_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if rerun_from is not None:
            safe_id(rerun_from)
            if batch_id is not None and batch_id != rerun_from:
                raise ValueError('重跑必须使用原批次标识')
            self.batch_id = rerun_from
        else:
            self.batch_id = safe_id(batch_id) if batch_id is not None else datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:12]
        self.batch_dir = self.root / self.batch_id
        if self.batch_dir.is_symlink() or self.batch_dir.resolve().parent != self.root:
            raise ValueError('批次目录不能使用符号链接或越出计划根目录')
        plan_path = self.batch_dir / 'plan.json'
        if rerun_from is not None:
            if plan_path.is_symlink():
                raise ValueError('原批次计划不能使用符号链接')
            self._plan = json.loads(plan_path.read_text(encoding='utf-8'))
            if plan_dict is not None and plan_dict != self._plan:
                raise ValueError('重跑不能修改原批次计划')
        else:
            if self.batch_dir.exists():
                raise ValueError('批次已存在；请使用 rerun_from 重跑')
            if not isinstance(plan_dict, dict):
                raise ValueError('新批次必须提供完整计划字典')
            self._plan = json.loads(json.dumps(plan_dict, ensure_ascii=False))
            self.batch_dir.mkdir()
            write_json(plan_path, self._plan)
            plan_md = '# 法规下载执行计划\n\n批次：' + self.batch_id + '\n\n```json\n' + json.dumps(self._plan, ensure_ascii=False, indent=2) + '\n```\n'
            atomic_write(self.batch_dir / 'plan.md', plan_md.encode('utf-8'))
        lock_path=self.batch_dir/'.execution.lock'
        if lock_path.is_symlink():
            raise ValueError('批次执行锁不得使用符号链接')
        self.execution_lock=FileLock(lock_path, '同一批次已有执行任务，请稍后重跑')
        try:
            self._initialize_attempt()
        except Exception:
            self.execution_lock.close()
            raise

    def _initialize_attempt(self):
        attempts_dir = self.batch_dir / 'attempts'
        if attempts_dir.is_symlink():
            raise ValueError('执行轮次目录不能使用符号链接')
        attempts_dir.mkdir(exist_ok=True)
        # mkdir 是原子操作，多个进程重跑时不会覆盖彼此的状态。
        number = 1
        while True:
            target = attempts_dir / f'{number:04d}'
            try:
                target.mkdir()
                break
            except FileExistsError:
                number += 1
        self.attempt_number = number
        self.attempt_dir = target
        self.started_at = timestamp()
        execution_plan = dict(batch_id=self.batch_id, attempt_number=self.attempt_number,
                              created_at=self.started_at, rerun=self._is_rerun, plan=self.plan)
        write_json(self.attempt_dir/'execution-plan.json', execution_plan)
        atomic_write(self.attempt_dir/'execution-plan.md', ('# 本轮执行计划\n\n```json\n'+json.dumps(execution_plan,ensure_ascii=False,indent=2)+'\n```\n').encode())
        self.states = {}
        self.finished = False
        candidates = self._plan.get('candidates', self._plan.get('documents', self._plan.get('files', [])))
        if isinstance(candidates, list):
            for index, candidate in enumerate(candidates):
                if isinstance(candidate, dict):
                    key = candidate.get('key', candidate.get('candidate_id', candidate.get('id', candidate.get('download_url', candidate.get('url', str(index))))))
                else:
                    key = str(index)
                self.states[str(key)] = dict(status='pending', reason='等待执行', attempts=0, checked_at=self.started_at)
        self._persist_states()

    @property
    def plan(self):
        return copy.deepcopy(self._plan)

    def _persist_states(self):
        write_json(self.attempt_dir / 'states.json', dict(batch_id=self.batch_id, attempt_number=self.attempt_number, started_at=self.started_at, states=self.states))

    def record(self, key, status, reason, attempts=0, **extras):
        """每个候选文件有独立终态、原因和请求次数，支持附带逐次请求事件。"""
        if self.finished:
            raise ValueError('当前轮次已完成，不能再修改状态')
        if status not in ('pending', 'running', 'success', 'fail', 'skip', 'succeeded', 'failed', 'skipped'):
            raise ValueError('不支持的文件执行状态')
        if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
            raise ValueError('attempts 必须为非负整数')
        if not isinstance(reason, str) or not reason:
            raise ValueError('每个文件状态必须提供原因')
        self.states[str(key)] = {**extras, 'status': status, 'reason': reason, 'attempts': attempts, 'checked_at': timestamp()}
        self._persist_states()
        return copy.deepcopy(self.states[str(key)])

    def finish(self, report=None, reason='执行结束，候选文件未完成'):
        if self.finished:
            return self.report_paths
        for state in self.states.values():
            if state['status'] in ('pending', 'running'):
                state.update(status='fail', reason=reason, checked_at=timestamp())
        self._persist_states()
        payload = copy.deepcopy(report or {})
        payload.update(batch_id=self.batch_id, attempt_number=self.attempt_number, started_at=self.started_at, finished_at=timestamp(), states=copy.deepcopy(self.states))
        self.report_paths = emit_report(payload, self.attempt_dir)
        self.finished = True
        self.execution_lock.close()
        return self.report_paths

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.finish(reason=f'执行中止：{exc}' if exc else '执行结束，候选文件未完成')
        return False
