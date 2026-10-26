"""执行冻结的无原件修复清单；只补目标，不重下成功任务。"""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import tempfile
import time
import uuid
from .census_to_oss import Runner, connect, atomic, valid, now
from .file_lock import FileLock
from .platform_adapters.census import allowed_hosts
from .raw_capture import file_sha256
from .verified_source_recovery import apply_verified_recovery
from .recovery_validation import digest, validate_plan, validate_repair_sources


def publish_results(path, value):
    """发布完整且不可覆盖的执行结果；不修改已提交的法规归档。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError('Execution result already exists; choose a new attempt path')
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.' + path.name + '.', suffix='.tmp',
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        # 原子创建最终名字，目标已存在时拒绝；不使用可覆盖目标的 replace。
        # NTFS、macOS 和 Linux 支持同目录硬链接，不支持时明确失败而非降级覆盖。
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def execution_envelope(plan, plan_sha256, results, *, attempt_id, started_at,
                       limit=0, stopped=False, auth_failure=False):
    """包含冻结计划的全部目标，显式标注未调度项，不把部分执行计为完成。"""
    targets = [item['target_id'] for item in plan['repairs']]
    by_target = {item['target_id']: item for item in results}
    if len(by_target) != len(results) or not set(by_target).issubset(targets):
        raise ValueError('Execution results contain duplicate or unknown targets')
    complete = []
    for index, target in enumerate(targets):
        if target in by_target:
            complete.append(by_target[target])
        else:
            reason = ('limit' if limit and index >= limit else
                      'auth_failure' if auth_failure else 'stopped' if stopped else 'not_dispatched')
            complete.append(dict(target_id=target, status='not_scheduled', reason=reason))
    return dict(schema_version=1, kind='recovery_execution_results',
                batch_id=plan['batch_id'], plan_sha256=plan_sha256,
                run_id=plan['run_id'], attempt_id=attempt_id, started_at=started_at,
                finished_at=now(), results=complete)


def direct_recovery(runner, item, run_id, allowed):
    key = item['target_id']
    if key not in allowed:
        raise ValueError('修复目标不在冻结清单')
    db = connect(runner.out/'queue.sqlite3')
    try:
        if db.execute('select status from jobs where id=?',(key,)).fetchone() != ('no_original',):
            return dict(target_id=key,status='skipped_status_changed')
        validate_repair_sources(runner.out,item)
        path = runner.out/'outcomes'/(key+'.json')
        before = json.loads(path.read_text())
        evidence = item['evidence']
        snapshot = Path(evidence['original_detail_snapshot'])
        if file_sha256(snapshot) != evidence['original_detail_sha256']:
            raise ValueError('官方详情证据SHA不符')
        history = runner.out/'recovery'/run_id/'outcomes-before'
        history.mkdir(parents=True,exist_ok=True)
        if not (history/path.name).exists():atomic(history/path.name,before)
        outcome = deepcopy(before)
        outcome.pop('oss_metadata',None)
        outcome['files'] = []
        hosts = allowed_hosts(before['platform'])
        roles = {}
        for candidate in item['files']:
            receipt = runner.file(db,candidate['url'],runner.out/'staging'/key,hosts)
            if candidate.get('sha256') and receipt.get('sha256')!=candidate['sha256']:
                receipt={**receipt,'capture_status':'failed','reason':'原件与独立身份核验时SHA不同，需重新核验'}
            outcome['files'].append(receipt)
            roles[candidate['url']] = dict(role=candidate['role'],status='official_link_observed',detail_url=before['detail_url'])
        okay = bool(outcome['files']) and all(valid(f) and f.get('oss',{}).get('status') in ('uploaded_verified','reused_verified') for f in outcome['files'])
        acceptable = any(f['role'] in ('regulation_body','official_non_regulatory_material') for f in item['files'])
        missing = evidence.get('known_missing_attachments', [])
        if missing and not okay:
            return dict(target_id=key,status='original_verification_failed')
        outcome['status'] = 'completed' if okay and acceptable and not evidence.get('primary_original_missing') and not missing else 'incomplete'
        outcome['reason'] = '已修复原件发现并归档OSS' if outcome['status']=='completed' else '已归档可获取版本；原文仍缺失或部分文件失败'
        outcome['finished_at'] = now()
        outcome['metadata'].update(file_roles=roles,primary_original_missing=bool(evidence.get('primary_original_missing')),all_attachments_verified=False)
        if missing:
            outcome['metadata']['known_missing_attachments'] = deepcopy(missing)
            outcome['reason'] += '；已确认缺失附件：' + '；'.join(missing)
        outcome['recovery'] = dict(run_id=run_id,evidence=evidence,source_relation='alternative_official_source_not_original_platform_hosted_file' if evidence.get('alternative_detail_key') else 'original_official_detail_link')
        uploaded = runner.sink.put_metadata(key,outcome,batch_id=runner.out.name)
        if uploaded.get('status') not in ('uploaded_verified','reused_verified'):
            return dict(target_id=key,status='metadata_upload_failed')
        outcome['oss_metadata'] = uploaded
        with db:
            changed=db.execute('update jobs set status=?,reason=?,files=?,bytes=?,updated_at=? where id=? and status="no_original"',(outcome['status'],outcome['reason'],len(outcome['files']),sum(f.get('size_bytes',0) for f in outcome['files']),outcome['finished_at'],key))
            if changed.rowcount!=1:raise ValueError('目标状态已变化，拒绝提交修复')
            for f in outcome['files']:
                if f.get('oss',{}).get('status') in ('uploaded_verified','reused_verified'):
                    db.execute('insert or ignore into objects values(?,?,?)',(f['sha256'],f['size_bytes'],f['oss']['raw_key']))
            atomic(path,outcome)
        return dict(target_id=key,status=outcome['status'],files=len(outcome['files']))
    finally:
        db.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch',required=True);p.add_argument('--repair-plan',required=True)
    p.add_argument('--target-manifest',required=True)
    p.add_argument('--credentials',required=True);p.add_argument('--workers',type=int,default=5)
    p.add_argument('--limit',type=int,default=0)
    p.add_argument('--results-output', help='Optional immutable final result JSON; use a new path per attempt')
    args=p.parse_args()
    if not 1<=args.workers<=5:raise ValueError('并发必须为1..5')
    if args.limit < 0:raise ValueError('limit must be nonnegative')
    batch=Path(args.batch);repair_path=Path(args.repair_plan)
    repair_bytes = repair_path.read_bytes()
    plan_sha256 = digest(repair_bytes)
    if plan_sha256!=repair_path.with_suffix('.sha256').read_text().strip():raise ValueError('修复计划SHA不符')
    repairs=json.loads(repair_bytes);plan=json.loads((batch/'plan.json').read_text())
    allowed=validate_plan(batch,repairs,Path(args.target_manifest))
    for name in ('bucket','endpoint','region','prefix'):setattr(args,name,plan[name])
    with FileLock(batch/'.lock'):
        if args.results_output:
            output = Path(args.results_output)
            if output.exists() or output.is_symlink():
                raise FileExistsError('Execution result already exists; choose a new attempt path')
            # 拒绝与可变 checkpoint 使用同一路径，避免旧写入流程破坏最终证据。
            if output.resolve() == (repair_path.parent/'execution-results.json').resolve():
                raise ValueError('Final result must differ from mutable execution checkpoint')
            output.parent.mkdir(parents=True, exist_ok=True)
        attempt_id, started_at = uuid.uuid4().hex, now()
        db=connect(batch/'queue.sqlite3');runner=Runner(args,batch,db,plan)
        signal.signal(signal.SIGTERM,lambda *unused:runner.stop.set())
        signal.signal(signal.SIGINT,lambda *unused:runner.stop.set())
        results=[];tasks=iter(repairs['repairs'][:args.limit] if args.limit else repairs['repairs'])
        def execute(item):
            run_id=repairs['run_id']+'-'+item['target_id'][:16]
            if item.get('source_id'):
                return apply_verified_recovery(batch,[item],runner.sink,allowed,run_id)[0]
            return direct_recovery(runner,item,run_id,allowed)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            pending={}
            while True:
                while len(pending)<args.workers and not runner.stop.is_set() and not runner.sink.auth_failure:
                    item=next(tasks,None)
                    if item is None:break
                    pending[pool.submit(execute,item)]=item['target_id']
                if not pending:break
                done,_=wait(pending,timeout=2,return_when=FIRST_COMPLETED)
                for future in done:
                    key=pending.pop(future)
                    try:results.append(future.result())
                    except Exception as exc:results.append(dict(target_id=key,status='repair_failed',reason=type(exc).__name__))
                runner.report('running')
                atomic(repair_path.parent/'execution-results.json',results)
        runner.report('paused' if runner.stop.is_set() or runner.sink.auth_failure else 'finished_with_gaps',True)
        runner.sink.put_metadata('recovery-results',dict(plan_sha256=plan_sha256,results=results),batch_id=batch.name)
        runner.sink.flush_cache();db.close()
        if args.results_output:
            publish_results(output, execution_envelope(repairs, plan_sha256, results,
                attempt_id=attempt_id, started_at=started_at, limit=args.limit,
                stopped=runner.stop.is_set(), auth_failure=bool(runner.sink.auth_failure)))


if __name__=='__main__':main()
