"""汇总持续批量任务的当前快照；单独呈现目录、原件、处理和独立审核。"""
import argparse
import hashlib
import html
import json
from pathlib import Path
from datetime import datetime, timezone

from law_crawler.batch_runtime import safe_id
from law_crawler.crawl_store import write_json
from law_crawler.download_regulations import country_download_dir
from law_crawler.jurisdiction_registry import resolve_jurisdiction
from law_crawler.resources import configuration_path
from law_crawler.bulk_archive import valid_receipt


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def review_snapshot(batch):
    """只有正式导入、精确集合及哈希/标记证据完整的审核可显示通过。"""
    review_path = batch / 'review.json'
    request_path = batch / 'review-request.json'
    if not review_path.is_file():
        return {'batch_id': batch.name, 'status': 'pending', 'issues': None}
    review, request = load(review_path), load(request_path)
    plan = load(batch / 'plan.json')
    if (not review.get('imported_at') or review.get('batch_id') != batch.name
            or not review.get('reviewer') or not review.get('model')
            or review.get('model') == plan['model_config']['model']):
        raise ValueError('独立审核导入或身份依据缺失: ' + batch.name)
    files, expected = review.get('files', []), request.get('files', [])
    failed = request.get('failed_files', [])
    expected_ids = [f['id'] for f in expected + failed]
    if (not expected or len(set(expected_ids)) != len(expected_ids)
            or len(files) != len(expected_ids) or {f['id'] for f in files} != set(expected_ids)):
        raise ValueError('独立审核缺少完整文件集合')
    for failure in failed:
        if next(f for f in files if f['id'] == failure['id']) != failure or failure.get('status') != 'failed':
            raise ValueError('前阶段失败记录与审核请求不一致')
    for required in expected:
        current = next((f for f in files if f['id'] == required['id']), {})
        if current.get('artifacts') != required.get('artifacts'):
            raise ValueError('独立审核产物集合已变更')
        for artifact in required['artifacts']:
            if sha(artifact['path']) != artifact['sha256']:
                raise ValueError('审核产物 SHA 校验失败')
        if current.get('verdict') not in ('pass', 'needs_revision') or not isinstance(current.get('issues'), list):
            raise ValueError('独立审核结论无效')
        if current['verdict'] == 'pass':
            if current['issues']:
                raise ValueError('审核通过却仍有未解决问题')
            for flag in required.get('quality_flags', []):
                if not any(r.get('flag') == flag and isinstance(r.get('evidence'), str) and r['evidence'].strip()
                           for r in current.get('resolved_quality_flags', [])):
                    raise ValueError('审核通过缺少质量标记解决证据')
            repaired = next((r for r in plan.get('repair', {}).get('files', []) if r['id'] == current['id']), None)
            if repaired:
                required_ids = {c['issue_id'] for c in repaired['changes']}
                closures = current.get('resolved_issues', [])
                if ({c.get('issue_id') for c in closures} != required_ids or len(closures) != len(required_ids)
                        or any(c.get('status') != 'resolved' or not c.get('evidence') for c in closures)):
                    raise ValueError('修订通过缺少全部原问题的独立闭环证据')
    return {'batch_id': batch.name, 'status': 'approved' if not failed and all(f['verdict'] == 'pass' for f in files) else 'needs_revision',
            'issues': sum(len(f.get('issues', [])) for f in files), 'failed_files': failed,
            'reviewer': review['reviewer'], 'model': review['model'],
            'duration_seconds': review.get('duration_seconds'), 'cost_usd': review.get('cost_usd'),
            'files': files, 'review_path': str(review_path), 'review_sha256': sha(review_path),
            'closed_issues': sum(len(f.get('resolved_issues', [])) for f in files),
            'translation_style': plan.get('translation_style'),
            'editing': {k: plan['repair'].get(k) for k in ('reviewer_or_editor', 'model', 'duration_seconds', 'cost_usd', 'duration_note', 'timing_evidence')} if 'repair' in plan else None}


def build(pipeline, archives, processing, reviews, jurisdiction=None):
    archive_rows, unique, snapshots_to_save = [], set(), []
    def snapshot(path):
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        snapshots_to_save.append({'sha256': digest, 'source_path': str(path), 'text': data.decode('utf-8')})
        return json.loads(data), digest
    for name in archives:
        directory = pipeline / 'bulk' / safe_id(name)
        state, state_sha = snapshot(directory / 'state.json')
        plan, plan_sha = snapshot(directory / 'plan.json')
        files = [f for d in state['documents'].values() for f in d.get('files', {}).values()]
        detail_gaps = [{k: d.get(k) for k in ('detail_url', 'platform', 'status', 'reason')}
                       for d in state['documents'].values() if d.get('status') in ('failed', 'incomplete')]
        verified = [f for f in files if valid_receipt(f)]
        unique.update(f['sha256'] for f in verified)
        archive_rows.append({'batch_id': name, 'catalogs_exhausted': sum(bool(s.get('complete')) for s in state['platforms'].values() if not s.get('alias_of')),
            'details_discovered': sum(len(s.get('details', [])) for s in state['platforms'].values()),
            'details_attempted': len(state['documents']), 'file_references': len(files), 'verified_file_references': len(verified),
            'failed_file_references': len(files) - len(verified), 'unique_originals': len({f['sha256'] for f in verified}),
            'failed_details': sum(d['status'] == 'failed' for d in detail_gaps),
            'incomplete_details': sum(d['status'] == 'incomplete' for d in detail_gaps), 'detail_gaps': detail_gaps,
            'network_capture_seconds': sum(e.get('duration_seconds', 0) for e in state['events']),
            'coverage': state['platforms'], 'state_sha256': state_sha, 'plan_sha256': plan_sha,
            'report': str(directory / 'report.html')})
    jobs, model_batches, processing_rows = [], set(), []
    for directory in processing:
        directory = Path(directory).resolve()
        summary = {'directory': str(directory), 'planned_files': None, 'recorded_jobs': 0, 'statuses': {}}
        if (directory / 'plan.json').is_file():
            process_plan, _ = snapshot(directory / 'plan.json')
            summary['planned_files'] = len(process_plan.get('inputs', []))
        if (directory / 'await-export-state.json').is_file():
            summary['await_export'], _ = snapshot(directory / 'await-export-state.json')
        processing_rows.append(summary)
        if not (directory / 'jobs').is_dir():
            continue
        for path in sorted((directory / 'jobs').glob('*.json')):
            row, row_sha = snapshot(path)
            jobs.append({'directory': str(directory), 'state_sha256': row_sha, **row})
            model_batches.add(row['pipeline_batch'])
            status = row.get('status', 'unknown')
            summary['statuses'][status] = summary['statuses'].get(status, 0) + 1
            summary['recorded_jobs'] += 1
    snapshots = [review_snapshot(pipeline / 'batches' / safe_id(name)) for name in reviews]
    for review in snapshots:
        for file in review.get('files', []):
            unique.update(a['sha256'] for a in file.get('artifacts', []) if a['path'].lower().endswith('.pdf'))
    events = []
    for name in sorted(model_batches):
        for path in sorted((pipeline / 'batches' / safe_id(name)).glob('attempts/*/events/model-*.json')):
            event, event_sha = snapshot(path)
            events.append({'path': str(path), 'sha256': event_sha, 'batch_id': name,
                **{k: event.get(k) for k in ('status', 'duration_seconds', 'usage', 'document', 'chunk')}})
    completed = [e for e in events if e['status'] == 'success']
    return {'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'national_complete': False, 'jurisdiction': jurisdiction,
        'completion_reason': '全量来源覆盖与全部独立复核仍需核实；本报告是持续任务当前快照，不是全量完成声明。',
        'independent_review_mode': 'current_session_independent_agents_in_batches',
        'archives': archive_rows, 'processing_outputs': processing_rows, 'processing_jobs': jobs, 'review_snapshots': snapshots,
        'verified_unique_originals_in_this_report': len(unique),
        'model_usage': {'event_count': len(events), 'completed_calls': len(completed),
            'failed_calls': sum(e['status'] == 'failed' for e in events),
            'unfinished_calls': sum(e['status'] not in ('success', 'failed') for e in events),
            'known_model_seconds': sum(e['duration_seconds'] or 0 for e in events),
            'input_tokens': sum((e['usage'] or {}).get('input_tokens') or 0 for e in completed),
            'output_tokens': sum((e['usage'] or {}).get('output_tokens') or 0 for e in completed),
            'partial_usage_unknown': any(e['status'] != 'success' or any((e['usage'] or {}).get(k) is None for k in ('input_tokens', 'output_tokens')) for e in events)},
        'model_events': events, '_snapshots': snapshots_to_save,
        'cost_usd': {'local_ollama_api': 0, 'session_subscription_attribution': None, 'hardware_electricity': None, 'total': None}}


def emit(report, output):
    output.mkdir(parents=True, exist_ok=True)
    evidence = output / 'snapshots'
    evidence.mkdir(exist_ok=True)
    report['evidence_snapshots'] = []
    for item in report.pop('_snapshots', []):
        target = evidence / (item['sha256'] + '.json')
        data = item['text'].encode('utf-8')
        if target.exists() and target.read_bytes() != data:
            raise ValueError('已有报告证据快照被修改')
        if not target.exists():
            target.write_bytes(data)
        report['evidence_snapshots'].append({'source_path': item['source_path'], 'path': str(target), 'sha256': item['sha256']})
    write_json(output / 'execution-report.json', report)
    title = (report.get('jurisdiction') or '所选法域') + '批量归档、三语处理与独立复核'
    md = ['# ' + title, '', report['completion_reason'], '',
          '快照时间（UTC）：' + report['generated_at_utc'], '',
          '本报告已校验的不同原件：' + str(report['verified_unique_originals_in_this_report']), '',
          '|归档批次|穷尽目录|发现详情|已尝试详情|哈希核验原件|失败附件引用|失败详情|不完整详情|', '|---|---:|---:|---:|---:|---:|---:|---:|']
    md += [f"|{a['batch_id']}|{a['catalogs_exhausted']}|{a['details_discovered']}|{a['details_attempted']}|{a['unique_originals']}|{a['failed_file_references']}|{a['failed_details']}|{a['incomplete_details']}|" for a in report['archives']]
    value = lambda v: '未知' if v is None else str(v)
    md += ['', '## 本地处理队列', '', '|输出目录|冻结文件数|已有逐件状态数|当前状态分布|', '|---|---:|---:|---|']
    md += [f"|{r['directory']}|{value(r['planned_files'])}|{r['recorded_jobs']}|{json.dumps(r['statuses'], ensure_ascii=False)}|" for r in report['processing_outputs']]
    md += ['', '## 独立复核记录', '', '|批次|状态|未解决问题|已闭环问题|复核秒数|修订秒数|复核费用USD|', '|---|---|---:|---:|---:|---:|---:|']
    md += [f"|{r['batch_id']}|{r['status']}|{value(r['issues'])}|{value(r.get('closed_issues'))}|{value(r.get('duration_seconds'))}|{value((r.get('editing') or {}).get('duration_seconds'))}|{value(r.get('cost_usd'))}|" for r in report['review_snapshots']]
    notes = [
        '逐件状态是持久化断点，不单独证明进程仍在运行；静态报告只对应本次快照。目录总量、附件引用和不同原件不是同一计数。',
        '保留官方原始文种和法律制度身份，不混入个案判决；印尼PERMA/SEMA不自动等同中国司法解释。',
        '采用中国大陆中文、英国英文法律文体，保留原文及非官方译文。语义、原件身份、效力和全量覆盖分别核验。',
        '初稿审核失败与修订通过记录分别列示。修订通过只批准对应哈希，未批准旧OCR或其他待复核文件。',
        '组织图隔离页保留原OCR，并不代表层级关系已经恢复；强制质量标记未解决前不能批准。',
        '本地Ollama API费0；会话订阅归属、硬件电力和总费用未知。模型事件、原始归档和审核耗时分别记录，不相加作为端到端工时。',
        '独立复核和修订按用户选择在当前会话中分批执行；当前没有无人值守的强模型API审核服务。',
        '完整逐件状态、模型事件、token、耗时、风险及证据路径见execution-report.json；后台模型产物仍需逐件独立复核。']
    md += ['', '## 质量、耗时与成本', '', '```json', json.dumps(report['model_usage'], ensure_ascii=False, indent=2), '```', ''] + notes
    (output / 'execution-report.md').write_text('\n'.join(md) + '\n', encoding='utf-8')
    e = html.escape
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+e(title)+'</title><style>body{font:16px/1.7 system-ui;max-width:1200px;margin:32px auto;padding:20px;color:#1d2e42}table{border-collapse:collapse;width:100%}td,th{border:1px solid #d4dce5;padding:10px;overflow-wrap:anywhere}th{background:#edf2f7}.notice{background:#fff3d6;padding:20px}pre{white-space:pre-wrap}a{color:#155ca0}</style><h1>'+e(title)+'</h1><p class="notice">' + e(report['completion_reason']) + '</p><p>快照时间（UTC）：' + e(report['generated_at_utc']) + '</p><h2>归档范围</h2><table><tr><th>批次</th><th>穷尽目录</th><th>发现详情</th><th>已尝试</th><th>不同原件</th><th>失败附件引用</th></tr>'
    page = page.replace('<th>失败附件引用</th>', '<th>失败附件引用</th><th>失败详情</th><th>不完整详情</th>')
    page += ''.join('<tr><td><a href="'+Path(a['report']).as_uri()+'">'+e(a['batch_id'])+'</a></td>'+''.join('<td>'+str(a[k])+'</td>' for k in ('catalogs_exhausted','details_discovered','details_attempted','unique_originals','failed_file_references','failed_details','incomplete_details'))+'</tr>' for a in report['archives'])
    page += '</table><h2>本地处理队列</h2><table><tr><th>输出目录</th><th>冻结文件数</th><th>已有逐件状态数</th><th>当前状态分布</th></tr>'
    for row in report['processing_outputs']:
        cells = [row['directory'], row['planned_files'], row['recorded_jobs'], json.dumps(row['statuses'], ensure_ascii=False)]
        page += '<tr>'+''.join('<td>'+e(value(v))+'</td>' for v in cells)+'</tr>'
    page += '</table><h2>独立复核记录</h2><table><tr><th>批次</th><th>结论</th><th>未解决</th><th>已闭环</th><th>复核秒数</th><th>修订秒数</th><th>复核费用USD</th></tr>'
    for r in report['review_snapshots']:
        label = e(r['batch_id'])
        if r.get('review_path'):
            label = '<a href="'+Path(r['review_path']).as_uri()+'">'+label+'</a>'
        cells = [r['status'], r['issues'], r.get('closed_issues'), r.get('duration_seconds'),
                 (r.get('editing') or {}).get('duration_seconds'), r.get('cost_usd')]
        page += '<tr><td>'+label+'</td>'+''.join('<td>'+e(value(v))+'</td>' for v in cells)+'</tr>'
    page += '</table><h2>计量与范围说明</h2>'+''.join('<p>'+e(n)+'</p>' for n in notes)+'<pre>'+e(json.dumps(report['model_usage'],ensure_ascii=False,indent=2))+'</pre><p><a href="execution-report.json">逐件状态、计时与证据JSON</a> · <a href="execution-report.md">Markdown报告</a></p></html>'
    (output / 'execution-report.html').write_text(page, encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--jurisdiction', required=True)
    parser.add_argument('--config')
    parser.add_argument('--archive-batch', action='append', default=[])
    parser.add_argument('--process-output', action='append', default=[], type=Path)
    parser.add_argument('--review-batch', action='append', default=[])
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        root = Path(args.root).resolve()
        selected = resolve_jurisdiction(root, args.jurisdiction)
        pipeline = country_download_dir(configuration_path(root, args.config), root, selected.index, selected.name) / 'pipeline'
        report = build(pipeline, args.archive_batch, args.process_output, args.review_batch, selected.name)
        emit(report, args.output.resolve())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc))
        return 2
    print(json.dumps({'national_complete': False, 'verified_originals': report['verified_unique_originals_in_this_report'],
                      'report': str(args.output.resolve() / 'execution-report.html')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
