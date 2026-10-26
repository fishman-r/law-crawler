"""读取下载器的小型快照及受信服务状态；不加载原件或大型断点，不写采集证据。"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time

from observer.snapshot import SnapshotReader, _number, _text, _time


class DownloadProgressReader:
    def __init__(self, workspace, download_base, runtime_dir, service_reader=None):
        self.download_base_input = Path(download_base).absolute()
        self.downloads = SnapshotReader(workspace, download_base)
        self.runtime = SnapshotReader(workspace, runtime_dir)
        self.service_reader = service_reader or self._service
        self.guard = threading.Lock()
        self.cached_at = 0
        self.cached = None

    @staticmethod
    def _read(reader, parts, optional=False):
        try:
            with reader._open(tuple(parts)) as (fd, info):
                if info.st_size > 128 * 1024:
                    raise ValueError('进度文件超过128KiB上限')
                with os.fdopen(os.dup(fd), 'rb') as stream:
                    raw = stream.read(128 * 1024 + 1)
                if len(raw) > 128 * 1024:
                    raise ValueError('进度文件过大')
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError('进度文件结构错误')
                return value, datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()
        except FileNotFoundError:
            if optional:
                return {}, None
            raise

    @staticmethod
    def _service(unit):
        # 单元名通过白名单校验；只读show，不执行shell、启动或停止动作。
        result = subprocess.run(['/usr/bin/systemctl', 'show', unit,
                                 '--property=ActiveState,SubState,MainPID,ExecMainStatus'],
                                capture_output=True, text=True, timeout=1.5, check=True)
        return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)

    def snapshot(self):
        with self.guard:
            now = time.monotonic()
            if self.cached is not None and now - self.cached_at < 4:
                return self.cached
            try:
                result = self._snapshot()
            except (OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.SubprocessError):
                result = {'available': False, 'status': 'unknown',
                          'warnings': ['下载状态无法安全读取，请检查服务配置或进度摘要。']}
            self.cached, self.cached_at = result, now
            return result

    def _snapshot(self):
        try:
            with self.runtime._open((), directory=True) as (fd, _):
                names = sorted(os.listdir(fd))[:100]
        except FileNotFoundError:
            return {'available': False, 'status': 'unknown', 'warnings': []}
        pointers = []
        for name in names:
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', name):
                continue
            try:
                item, modified = self._read(self.runtime, (name, 'active-download.json'), optional=True)
                if item:
                    pointers.append((modified, item, name))
            except (OSError, ValueError, RecursionError):
                continue
        if not pointers:
            return {'available': False, 'status': 'unknown', 'warnings': []}
        _, pointer, pointer_dir = max(pointers, key=lambda pair: pair[0])
        batch = pointer.get('batch_id')
        if not isinstance(batch, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,119}', batch):
            raise ValueError('批次ID不合法')
        try:
            parts = Path(pointer['state']).relative_to(self.downloads.download_base).parts
        except ValueError:
            parts = Path(pointer['state']).relative_to(self.download_base_input).parts
        census = len(parts) == 5 and parts[1:] == ('pipeline', 'census-download', batch, 'status.json')
        bulk = len(parts) == 5 and parts[1:] == ('pipeline', 'bulk', batch, 'state.json')
        if (not (census or bulk) or parts[0] in ('.', '..') or '\\' in parts[0]):
            raise ValueError('进度路径越界')
        prefix = parts[:-1]
        unit = pointer.get('unit', '')
        valid_unit = (unit == 'law-crawler-census-oss.service' if census else
                      isinstance(unit, str) and re.fullmatch(r'law-crawler-download-[A-Za-z0-9_-]{1,120}\.service', unit))
        if not valid_unit:
            raise ValueError('服务名不合法')
        if census:
            # 下载器发布不含凭据的公开摘要；不打开root私有的SQLite或原件目录。
            snapshot_file = pointer.get('snapshot_file')
            if snapshot_file is not None:
                if snapshot_file != 'census-oss-status.json':
                    raise ValueError('公开摘要路径不合法')
                status, modified = self._read(self.runtime, (pointer_dir, snapshot_file))
            else:
                status, modified = self._read(self.downloads, parts)
            if status.get('batch_id') != batch:
                raise ValueError('公开摘要批次不一致')
            summary = self._census_summary(status)
            status = {**status, 'phase': ('finished' if status.get('status') in ('finished', 'completed', 'finished_with_gaps')
                       else 'stopped' if status.get('status') in ('stopped', 'paused', 'error', 'failed') else 'capture')}
        else:
            status, modified = self._read(self.downloads, prefix + ('fast-download', 'status.json'))
            summary = status.get('summary')
            if not isinstance(summary, dict):
                summary, _ = self._read(self.downloads, prefix + ('fast-download', 'progress.json'))
        warnings = []
        try:
            service = self.service_reader(unit)
        except (OSError, ValueError, subprocess.SubprocessError):
            service = {}
            warnings.append('未能核验下载服务是否运行。')
        active = (service.get('ActiveState') in ('active', 'activating', 'deactivating')
                  and str(service.get('MainPID', '')).isdigit() and int(service['MainPID']) > 0
                  and service.get('SubState') != 'exited')
        phase = status.get('phase')
        if active:
            state = 'running'
            if phase == 'finished':
                warnings.append('新进程已启动；当前累计计数仍为上一轮结束快照，等待下载器更新。')
        elif phase == 'finished':
            state = 'finished'
        elif service and phase in ('capture', 'stopped', 'final_report', 'error'):
            state = 'paused'
            if phase != 'stopped':
                warnings.append('服务已停止，但尚未记录正常结束；保留断点，可续跑。')
        else:
            state = 'unknown'
        if service.get('ActiveState') == 'deactivating':
            warnings.append('正在停止并保存下载断点。')
        numeric = {key: _number(summary.get(key)) for key in (
            'total_details', 'attempted_details', 'successful_files', 'failed_file_references')}
        reasons = summary.get('failure_reasons', [])
        if not isinstance(reasons, list):
            reasons = []
        reasons = [{'reason': _text(r.get('reason'), 160), 'count': _number(r.get('count'))}
                   for r in reasons[:30] if isinstance(r, dict) and _number(r.get('count')) is not None]
        workers = _number(pointer.get('workers'))
        run_workers = _number(summary.get('run_workers'))
        if workers != run_workers:
            warnings.append('配置并发与上轮执行并发不同；新配置在下次续跑时生效。')
        operation_keys = ('total', 'completed', 'active', 'pending', 'failed', 'needs_verification')
        source_ops = summary.get('operations', {})
        if not isinstance(source_ops, dict):
            source_ops = {}
        operations = {key: _number(source_ops.get(key)) for key in operation_keys}
        valid = (all(v is not None for v in operations.values())
                 and operations['total'] == sum(operations[k] for k in operation_keys if k != 'total'))
        if not valid:
            operations = dict.fromkeys(operation_keys)
            warnings.append('任务分区摘要缺失或不一致，下载任务数量待核验。')
        elif not active and service:
            operations['pending'] += operations['active']
            operations['active'] = 0
        elif not service:
            operations['active'] = operations['pending'] = None
            valid = False
        operations.update(unit='条详情归档任务', partition_valid=valid)
        def counters(name, keys):
            data = summary.get(name, {})
            return {key: _number(data.get(key)) if isinstance(data, dict) else None for key in keys}
        identity_counts = counters('identity_counts', ('regulations', 'judicial_guidance', 'total_legal_instruments',
            'identity_pending_details', 'non_target_details', 'duplicate_source_records', 'versions',
            'multiple_version_regulations', 'confirmed_version_records', 'version_pending_regulations'))
        file_counts = counters('file_counts', ('total_references', 'downloaded_references', 'unique_downloaded_files',
            'body_files', 'annex_files', 'related_files', 'role_pending_files', 'failed_references'))
        source_timing = summary.get('timing', {})
        if not isinstance(source_timing, dict):
            source_timing = {}
        timing = {key: _time(source_timing.get(key)) for key in ('started_at', 'ended_at', 'run_started_at')}
        timing.update({key: _number(source_timing.get(key)) for key in ('elapsed_seconds', 'active_download_seconds', 'run_elapsed_seconds')})
        if active and phase != 'finished' and timing['started_at']:
            timing['elapsed_seconds'] = max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(timing['started_at'])).total_seconds()))
        timing.update(basis='批次创建至快照的历时（含目录发现、等待和暂停）',
                      active_time_status='pending_verification' if timing['active_download_seconds'] is None else 'recorded')
        warnings.append('总量仅指已发现的详情；队列结束不代表印尼全国法规全集已收齐。')
        updated_at = _time(summary.get('updated_at')) or modified
        snapshot_age = max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(updated_at)).total_seconds()))
        stale = snapshot_age > 180
        if stale:
            warnings.append('下载摘要超过3分钟未更新；显示的是最后一次快照，请勿视作实时进度。')
        return dict(available=True, batch_id=batch, batch_kind='census_oss' if census else 'bulk',
                    failure_reasons_updated_at=(_time(status.get('failure_reasons_updated_at')) if census else None),
                    failure_reason_basis=(_text(status.get('failure_reason_basis'), 240) if census else None),
                    oss=({'unique_files': _number(status.get('unique_oss_files')), 'bytes': _number(status.get('oss_bytes'))} if census else None),
                    task_outcomes=({key: _number(status.get(key)) for key in ('failed', 'incomplete', 'no_original')} if census else None),
                    stale=stale, snapshot_age_seconds=snapshot_age, jurisdiction=_text(parts[0], 80),
                    status=state, phase=_text(phase, 40), has_gaps=(status.get('download_complete') is False
                        or bool(numeric['failed_file_references']) or bool(operations.get('needs_verification')) or bool(operations.get('failed'))),
                    **numeric, operations=operations, identity_counts=identity_counts, file_counts=file_counts, timing=timing,
                    failure_reasons=reasons, workers=workers, run_workers=run_workers,
                    active_concurrency_limit=(run_workers if active else (0 if service else None)),
                    updated_at=updated_at, warnings=warnings)

    @staticmethod
    def _census_summary(status):
        """将目录归档队列映射为互斥任务分区；OSS文件不是法规数量。"""
        failed = _number(status.get('failed'))
        incomplete = _number(status.get('incomplete'))
        failure_count = failed + incomplete if failed is not None and incomplete is not None else None
        elapsed = status.get('elapsed_this_run_seconds')
        if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and 0 <= elapsed < 10**12:
            elapsed = int(elapsed)
        else:
            elapsed = None
        batch_elapsed = None
        started, updated = _time(status.get('started_at')), _time(status.get('updated_at'))
        if started and updated:
            batch_elapsed = max(0, int((datetime.fromisoformat(updated) - datetime.fromisoformat(started)).total_seconds()))
        return dict(total_details=status.get('total'), attempted_details=status.get('attempted'),
            successful_files=status.get('unique_oss_files'), failed_file_references=None,
            run_workers=status.get('workers'), updated_at=status.get('updated_at'),
            failure_reasons=status.get('failure_reasons', []),
            operations=dict(total=status.get('total'), completed=status.get('completed'),
                active=status.get('downloading'), pending=status.get('pending'), failed=failure_count,
                needs_verification=status.get('no_original')),
            file_counts=dict(unique_downloaded_files=status.get('unique_oss_files')),
            timing=dict(started_at=status.get('started_at'), run_started_at=status.get('run_started_at'),
                ended_at=status.get('ended_at'), elapsed_seconds=batch_elapsed, run_elapsed_seconds=elapsed))
