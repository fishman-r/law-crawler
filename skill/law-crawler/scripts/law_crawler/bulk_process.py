"""将批量原件清单逐件交给现有流水线；复核仍由独立审核者执行。"""
import argparse
import hashlib
import html
import json
import math
from urllib.parse import unquote, urlsplit
from pathlib import Path
import shutil
import time
from types import SimpleNamespace

from law_crawler.crawl_store import write_json
from law_crawler.download_regulations import country_download_dir
from law_crawler.file_lock import FileLock
from law_crawler.jurisdiction_registry import resolve_jurisdiction
from law_crawler.resources import configuration_path
from law_crawler import staged_pipeline


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def manifest_inputs(directory):
    """读取当前导出的活跃清单，保留历史快照但不重复处理历史元数据版本。"""
    directory = directory.resolve()
    index = directory.parent / 'active-manifests.json'
    if not index.exists():
        return [{'path': str(p.resolve()), 'sha256': sha(p)} for p in sorted(directory.glob('*.json'))]
    value = load(index)
    rows = value.get('manifests') if isinstance(value, dict) else value
    if not isinstance(rows, list):
        raise ValueError('活跃清单索引必须包含manifests列表')
    result, raw_seen = [], set()
    for row in rows:
        path = Path(row['path']).resolve()
        if path.parent != directory or sha(path) != row['sha256']:
            raise ValueError('活跃清单文件路径或SHA不匹配')
        raw_sha = row.get('raw_sha256')
        if not raw_sha or raw_sha in raw_seen:
            raise ValueError('活跃清单缺少或重复原件SHA')
        raw_seen.add(raw_sha)
        result.append({'path': str(path), 'sha256': row['sha256']})
    return sorted(result, key=lambda r: r['path'])


def await_export(directory, manifests, jurisdiction, model_config, model_source, profile_source,
                 timeout_seconds, processing_lock_wait_seconds):
    """持有输出目录锁等待生产者活跃索引；仅索引就绪后冻结正常处理输入。"""
    if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
        raise ValueError('await_export_seconds 必须为有限非负数')
    manifests = manifests.resolve()
    producer = manifests.parent / 'plan.json'
    index = manifests.parent / 'active-manifests.json'
    status_path = directory / 'await-export-state.json'
    started = time.monotonic()
    state = {'status': 'pending_export', 'reason': '等待原始归档生产者导出活跃处理清单',
             'elapsed_seconds': 0, 'timed_out': False, 'active_index': str(index)}
    try:
        producer_plan = load(producer)
        if (producer_plan.get('jurisdiction') != jurisdiction
                or producer_plan.get('batch_id') != manifests.parent.name):
            raise ValueError('生产者计划法域或批次目录不匹配')
        signature = producer.parent / 'plan.sha256'
        if signature.exists() and signature.read_text().strip() != sha(producer):
            raise ValueError('生产者冻结计划SHA校验失败')
        frozen = {'version': 1, 'jurisdiction': jurisdiction, 'manifest_directory': str(manifests),
                  'producer_plan': {'path': str(producer), 'sha256': sha(producer)},
                  'model_config': model_config, 'translation_style': load(profile_source),
                  'model_config_source': {'path': str(model_source.resolve()), 'sha256': sha(model_source)},
                  'translation_style_source': {'path': str(profile_source.resolve()), 'sha256': sha(profile_source)},
                  'frozen_model_snapshot': {'path': str(directory / 'model-config.json'), 'sha256': sha(directory / 'model-config.json')},
                  'frozen_style_snapshot': {'path': str(directory / 'translation-style.json'), 'sha256': sha(directory / 'translation-style.json')},
                  'timeout_seconds': timeout_seconds, 'poll_seconds': 5,
                  'processing_lock_wait_seconds': processing_lock_wait_seconds}
        plan_path = directory / 'await-export-plan.json'
        if plan_path.exists() and load(plan_path) != frozen:
            raise ValueError('等待生产者计划或模型、文体、等待策略已变化；使用新output目录')
        if not plan_path.exists():
            write_json(plan_path, frozen)
        while True:
            for field in ('producer_plan', 'model_config_source', 'translation_style_source', 'frozen_model_snapshot', 'frozen_style_snapshot'):
                item = frozen[field]
                if sha(item['path']) != item['sha256']:
                    raise ValueError('等待期间冻结输入发生变化: ' + field)
            if signature.exists() and signature.read_text().strip() != frozen['producer_plan']['sha256']:
                raise ValueError('等待期间生产者计划SHA签名发生变化')
            state['elapsed_seconds'] = time.monotonic() - started
            if index.exists():
                index_sha = sha(index)
                inputs = manifest_inputs(manifests)
                if sha(index) != index_sha:
                    raise ValueError('活跃导出索引在冻结输入期间变化；保留待处理状态并重跑')
                if not inputs:
                    raise ValueError('生产者导出空活跃清单，不能称处理完成')
                state.update(status='ready', reason='活跃导出清单已通过路径及SHA校验',
                             active_index_sha256=index_sha, input_count=len(inputs))
                write_json(status_path, state)
                return inputs
            if state['elapsed_seconds'] >= timeout_seconds:
                state.update(timed_out=True, reason='等待活跃导出清单达到上限；仍待原件归档导出，未运行OCR或模型')
                write_json(status_path, state)
                raise TimeoutError(state['reason'])
            write_json(status_path, state)
            print(json.dumps({'await_export': state}, ensure_ascii=False), flush=True)
            time.sleep(min(frozen['poll_seconds'], timeout_seconds - state['elapsed_seconds']))
    except TimeoutError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        state.update(status='failed', reason=str(exc), elapsed_seconds=time.monotonic() - started)
        write_json(status_path, state)
        raise


def verify_item(item, boundary):
    path = Path(item['path']).resolve()
    if not path.is_relative_to(boundary.resolve()) or sha(path) != item['sha256']:
        raise ValueError('归档或处理产物 SHA-256 校验失败')


def verify_completed(row, pipeline):
    """每次复用、报告、审核前重新检查原件、输入、请求及全部处理产物。"""
    for key, hash_key in [('manifest', 'manifest_sha256'), ('prepared_manifest', 'prepared_manifest_sha256'),
                           ('review_request', 'review_request_sha256')]:
        if not row.get(key) or sha(row[key]) != row.get(hash_key):
            raise ValueError('完成状态证据缺失或已变更: ' + key)
    if sha(row['raw_path']) != row['raw_sha256']:
        raise ValueError('原件 SHA-256 校验失败')
    request = load(row['review_request'])
    if request.get('batch_id') != row['pipeline_batch'] or not request.get('files') or request.get('failed_files'):
        raise ValueError('独立复核请求未包含完整成功产物')
    for file in request['files']:
        if not file.get('artifacts'):
            raise ValueError('处理产物缺少 SHA-256 证据')
        for item in file['artifacts']:
            verify_item(item, pipeline)
    manifest = load(row['manifest'])
    for document in manifest.get('documents', []):
        decision = file_role_decision(document.get('document', {}), pipeline, row['raw_sha256'])
        if not decision['eligible']:
            raise FileIdentityPending(decision)
    return request


def review_state(row, pipeline):
    request = verify_completed(row, pipeline)
    path = pipeline / 'batches' / row['pipeline_batch'] / 'review.json'
    if not path.is_file():
        return {'status': 'pending', 'reason': '尚无独立审核记录；不等同通过'}
    review = load(path)
    plan = load(pipeline / 'batches' / row['pipeline_batch'] / 'plan.json')
    if (review.get('batch_id') != row['pipeline_batch'] or not review.get('imported_at')
            or not review.get('reviewer') or not review.get('model')
            or review['model'] == plan['model_config']['model']):
        raise ValueError('独立复核身份、模型或正式导入证据不完整')
    duration = review.get('duration_seconds')
    cost = review.get('cost_usd')
    if type(duration) not in (float, int) or not math.isfinite(duration) or duration < 0:
        raise ValueError('独立复核计时无效')
    if cost is not None and (type(cost) not in (float, int) or not math.isfinite(cost) or cost < 0):
        raise ValueError('独立复核成本无效')
    files = review.get('files', [])
    expected = request['files']
    if len(files) != len(expected) or len({f['id'] for f in files}) != len(files):
        raise ValueError('独立复核文件集合不完整')
    for required in expected:
        file = next((f for f in files if f['id'] == required['id']), {})
        if file.get('artifacts') != required['artifacts'] or file.get('verdict') not in ('pass', 'needs_revision'):
            raise ValueError('独立复核产物绑定或结论无效')
        if not isinstance(file.get('issues'), list) or (file['verdict'] == 'pass' and file['issues']):
            raise ValueError('独立复核问题未闭环')
        if file['verdict'] == 'pass':
            for flag in required.get('quality_flags', []):
                if not any(r.get('flag') == flag and isinstance(r.get('evidence'), str) and r['evidence'].strip()
                           for r in file.get('resolved_quality_flags', []) if isinstance(r, dict)):
                    raise ValueError('独立复核未逐项解决质量标记')
    return {'status': 'approved' if all(f['verdict'] == 'pass' for f in files) else 'needs_revision',
            'path': str(path), 'sha256': sha(path), 'files': files, 'cost_usd': cost, 'duration_seconds': duration}


def preparation_directory(base):
    """保留失败OCR现场；仅复用完整收据，后续有限尝试使用新目录。"""
    current, attempt = base, 1
    while current.exists():
        receipt = current / 'receipt.json'
        if receipt.is_file() and load(receipt).get('status') in ('completed', 'completed_needs_review'):
            return current
        attempt += 1
        current = base.with_name(base.name + '-attempt-' + str(attempt))
    return current


class FileIdentityPending(ValueError):
    def __init__(self, decision):
        self.decision = decision
        super().__init__('文件角色待身份审核；原始归档保留，未运行OCR或模型')


def archived_response(url, pipeline):
    """从本地响应索引验证URL→原始字节绑定，不在处理阶段访问网络。"""
    from law_crawler.raw_capture import _safe, _url
    canonical = _url(url)
    raw = pipeline.resolve() / 'raw'
    receipt = load(_safe(raw / 'index' / (hashlib.sha256(canonical.encode()).hexdigest() + '.json')))
    path = _safe(Path(receipt['path']))
    if (path.parent != raw / 'objects' or not receipt.get('complete')
            or not 200 <= receipt.get('status_code', 0) < 300
            or _url(receipt['url']) != canonical
            or urlsplit(receipt.get('final_url') or canonical).hostname != urlsplit(canonical).hostname
            or sha(path) != receipt['sha256']
            or path.stat().st_size != receipt['size_bytes']):
        raise ValueError('角色证据原始响应绑定无效')
    return receipt, path


def file_role_decision(document, pipeline, raw_sha):
    """按同SHA来源确认角色，拦截旧生产者混入的立法形成/相关文件。"""
    sources = document.get('sources', [])
    if not sources:
        return {'eligible': True, 'reason': '显式本地输入；无生产者来源角色可继承', 'sources': []}
    observations, accepted = [], False
    for source in sources:
        url = source.get('file_url', '')
        detail = source.get('detail_url', '')
        path = unquote(urlsplit(url).path).casefold()
        observation = {'file_url': url, 'detail_url': detail, 'raw_sha256': raw_sha,
                       'role': 'related_or_unconfirmed_document_pending', 'eligible': False}
        try:
            if urlsplit(url).scheme not in ('http', 'https') or not urlsplit(url).hostname:
                raise ValueError('来源缺少有效原件URL')
            if source.get('raw_sha256') not in (None, raw_sha):
                raise ValueError('来源原件SHA不匹配')
            if '/pembentukan-puu/' in path:
                observation.update(role='related_formation_document_pending', reason='立法形成入口，身份待审核；不能仅按路径认定草案')
            elif urlsplit(url).hostname == 'jdih.kemendag.go.id' or source.get('platform') == 'kemendag.go.id':
                from law_crawler.platform_adapters.trade import classify_file_roles
                receipt, detail_path = archived_response(detail, pipeline)
                download, _ = archived_response(url, pipeline)
                if download['sha256'] != raw_sha:
                    raise ValueError('详情正文URL与本原件SHA不匹配')
                role = classify_file_roles(detail_path.read_text(encoding='utf-8', errors='replace'), detail, [url])[url]
                observation.update(role, detail_sha256=receipt['sha256'])
                observation['eligible'] = role['role'] in ('regulation_body', 'regulation_annex')
                observation['reason'] = role['html_context']
            elif isinstance(source.get('file_role'), dict) and source['file_role'].get('role') not in ('regulation_body', 'regulation_annex', 'formal_regulation_body_with_integral_annex'):
                observation.update(role=source['file_role'].get('role', observation['role']), reason='逐URL角色声明为相关、历史或未确认文件；等待身份审核')
            elif any(token in path for token in ('/related/', '/history/', '/historical/', '/rancangan/', '/draft/')):
                observation['reason'] = '相关、历史或形成资料入口；正式身份待核验'
            else:
                observation.update(role='legacy_platform_body', eligible=True, reason='保持非Kemendag既有正式正文适配兼容')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            observation['reason'] = '缺少有效逐URL角色证据: ' + str(exc)
        accepted = accepted or observation['eligible']
        observations.append(observation)
    return {'eligible': accepted, 'raw_sha256': raw_sha, 'sources': observations,
            'reason': '同SHA至少一个来源确认正文或附件上下文；其他角色保留独立记录' if accepted else '全部来源角色待身份核验，禁止自动继承正式法规身份'}


def prepare_manifest(manifest, pipeline, engine):
    """为PDF生成完整逐页OCR，保留检测表格的可审核派生结构。"""
    value = load(manifest)
    documents = value.get('documents')
    if not isinstance(documents, list) or len(documents) != 1:
        raise ValueError('批处理输入必须为每件一个document的manifest')
    doc = documents[0]
    if 'path' not in doc:
        raise ValueError('仅处理已归档的本地原件，不在处理阶段下载')
    raw = Path(doc['path']).expanduser()
    if not raw.is_absolute():
        raise ValueError('批量处理原件必须为已归档的绝对路径')
    raw = raw.resolve()
    if not raw.is_relative_to(pipeline.resolve() / 'raw'):
        raise ValueError('原件必须位于国家 pipeline/raw 归档目录')
    raw_sha = sha(raw)
    decision = file_role_decision(doc.get('document', {}), pipeline, raw_sha)
    if not decision['eligible']:
        raise FileIdentityPending(decision)
    metrics = {'ocr_seconds': 0, 'automatic_tables_seconds': 0, 'ocr_reused': False,
               'file_role_gate': decision}
    if raw.read_bytes().startswith(b'%PDF-') and engine != 'none':
        from law_crawler.local_ocr import generate
        output = preparation_directory(pipeline / 'ocr' / ('bulk-' + raw_sha + '-' + engine))
        receipt = generate(raw, output, engine, ['id-ID'] if engine == 'apple-vision' else ['ind'])
        metrics.update(ocr_seconds=0 if receipt.get('reused') else receipt['duration_seconds'],
                       ocr_reused=receipt.get('reused', False), ocr_receipt=str(output / 'receipt.json'))
        doc['transcription_path'] = str(output / 'transcription.json')
        try:
            from law_crawler.automatic_tables import generate_compatible as detect
            table_key = raw_sha + '-' + sha(Path(doc['transcription_path']))
            table_dir = pipeline / 'ocr' / ('bulk-tables-' + table_key)
            detected = detect(Path(doc['transcription_path']), table_dir)
            table_dir = Path(detected.get('output_directory', table_dir))
            # Both APIs and their receipts remain auditable. No candidate is approved here.
            sidecar = table_dir / 'transcription.json'
            if sidecar.is_file():
                doc['transcription_path'] = str(sidecar)
            metrics.update(automatic_tables_seconds=0 if detected.get('reused') else detected.get('duration_seconds', 0),
                           automatic_tables_directory=str(table_dir))
        except (ImportError, OSError, ValueError, RuntimeError, TypeError) as exc:
            raise RuntimeError('自动表格结构处理失败，保留OCR证据并等待有限重试: ' + str(exc)) from exc
    elif raw.read_bytes().startswith(b'%PDF-'):
        metrics['ocr_reason'] = '未选择OCR；文字层和全部页面须由独立复核者核验'
    return value, raw_sha, metrics


def summarize(directory, pipeline, started, error=None):
    rows = [load(p) for p in sorted((directory / 'jobs').glob('*.json'))]
    for row in rows:
        if row['status'] == 'pending_review':
            try:
                row['independent_review'] = review_state(row, pipeline)
                if row['independent_review']['status'] != 'pending':
                    row['status'] = row['independent_review']['status']
                    row['reason'] = '独立复核已通过' if row['status'] == 'approved' else '独立复核要求修订；尚未通过'
            except FileIdentityPending as exc:
                row.update(status='pending_identity', reason=str(exc), file_role_gate=exc.decision, independent_review={'status': 'identity_pending'})
            except (OSError, ValueError, KeyError, TypeError) as exc:
                row.update(status='failed', reason='产物或复核证据无效: ' + str(exc), independent_review={'status': 'invalid'})
    counts = {status: sum(r['status'] == status for r in rows)
              for status in ('pending_review', 'pending_identity', 'approved', 'needs_revision', 'failed', 'deferred', 'running')}
    history = [a for r in rows for a in r.get('attempt_history', [])]
    plan_path = directory / 'plan.json'
    expected_count = len(load(plan_path).get('inputs', [])) if plan_path.exists() else len(rows)
    counts['unstarted'] = max(0, expected_count - len(rows))
    model_calls = 0
    for row in rows:
        ledger_path = pipeline / 'batches' / row['pipeline_batch'] / 'model-calls.json'
        model_calls += load(ledger_path)['calls'] if ledger_path.is_file() else sum(
            a.get('result', {}).get('usage', {}).get('model_calls', 0) for a in row.get('attempt_history', []))
    reviews = [r['independent_review'] for r in rows if r.get('independent_review', {}).get('status') in ('approved', 'needs_revision')]
    report = {'version': 1, 'complete': False, 'completion_reason': '全量来源覆盖需另行核实；本报告仅描述冻结输入清单',
              'input_count': expected_count,
              'processing_complete': bool(rows) and len(rows) == expected_count and all(r['status'] in ('pending_review', 'approved', 'needs_revision') for r in rows),
              'review_complete': bool(rows) and len(rows) == expected_count and all(r['status'] == 'approved' for r in rows),
              'counts': counts, 'jobs': rows, 'duration_seconds': time.monotonic() - started,
              'cumulative_job_seconds': sum(a.get('duration_seconds', 0) for a in history),
              'cumulative_model_calls': model_calls,
              'processing_lock_wait_seconds': sum(a.get('result', {}).get('usage', {}).get('processing_lock_wait_seconds', 0) for a in history),
              'model_seconds': sum(a.get('result', {}).get('usage', {}).get('model_seconds', 0) for a in history),
              'independent_review_seconds': sum(r['duration_seconds'] for r in reviews),
              'independent_review_known_cost_usd': sum(r['cost_usd'] for r in reviews if r['cost_usd'] is not None),
              'independent_review_unknown_cost_count': sum(r['cost_usd'] is None for r in reviews),
              'error': error, 'cost_usd': {'local_model_api': 0, 'subscription': None, 'hardware_electricity': None, 'total': None}}
    write_json(directory / 'report.json', report)
    write_json(directory / 'review-queue.json', {'jobs': [r for r in rows if r['status'] != 'approved']})
    lines = ['# 批量低成本处理与独立复核队列', '', '全量完成：未确认。每件必须对照原件完成独立复核。', '', '|文件|状态|原因|', '|---|---|---|']
    lines += ['|' + '|'.join(str(r.get(k, '')).replace('|', '\\|').replace('\n', ' ') for k in ('id', 'status', 'reason')) + '|' for r in rows]
    (directory / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>批量处理进度</title><style>body{max-width:1100px;margin:30px auto;padding:20px;font:16px/1.7 system-ui}td,th{border:1px solid #ddd;padding:10px}table{border-collapse:collapse;overflow-wrap:anywhere}</style><h1>批量处理进度</h1><p>全量完成：未确认。原件归档、翻译处理、独立复核分别验收。</p><table><tr><th>文件</th><th>状态</th><th>原因</th></tr>'
    page += ''.join('<tr>' + ''.join('<td>' + html.escape(str(r.get(k, ''))) + '</td>' for k in ('id', 'status', 'reason')) + '</tr>' for r in rows)
    (directory / 'report.html').write_text(page + '</table><p>费用与逐件计时见report.json；未知费用保留null。</p></html>', encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--jurisdiction', required=True)
    parser.add_argument('--config')
    parser.add_argument('--manifest-dir', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--model-config', required=True, type=Path)
    parser.add_argument('--ocr-engine', choices=('apple-vision', 'tesseract', 'none'), default='apple-vision')
    parser.add_argument('--chunk-chars', type=int, default=1500)
    parser.add_argument('--max-chars', type=int, default=2000000)
    parser.add_argument('--max-calls', type=int, default=3000, help='每件冻结批次累计模型调用保护上限；达到后保留断点')
    parser.add_argument('--max-job-attempts', type=int, default=3, help='每件最多执行次数，默认3，须显式retry-failed')
    parser.add_argument('--processing-lock-wait-seconds', type=float, default=None, help='国家处理锁等待上限：新批次默认1800秒；旧冻结批次沿用0秒')
    parser.add_argument('--retry-failed', action='store_true')
    parser.add_argument('--status-only', action='store_true')
    parser.add_argument('--await-export-seconds', type=float, default=None, help='等待生产者active-manifests.json的最大秒数，默认0不等待')
    args = parser.parse_args(argv)
    started = time.monotonic()
    try:
        root = Path(args.root).resolve()
        selected = resolve_jurisdiction(root, args.jurisdiction)
        if selected.name != 'Indonesia':
            raise ValueError('当前批量OCR语言适配仅验证Indonesia')
        pipeline = country_download_dir(configuration_path(root, args.config), root, selected.index, selected.name) / 'pipeline'
        directory = args.output.resolve()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'jobs').mkdir(exist_ok=True)
        with FileLock(directory / '.process.lock'):
            if min(args.chunk_chars, args.max_chars, args.max_calls, args.max_job_attempts) <= 0:
                raise ValueError('分块、长度、调用和重试上限必须为正整数')
            model_config = staged_pipeline.validate_config(load(args.model_config))
            if model_config['provider'] != 'ollama' or urlsplit(model_config['base_url']).hostname not in ('localhost', '127.0.0.1', '::1'):
                raise ValueError('批量处理仅允许本地 Ollama；云端调用未获预算授权')
            profile = Path(__file__).resolve().parents[2] / 'assets/translation-styles/zh-cn_en-gb.json'
            plan_path = directory / 'plan.json'
            existing = load(plan_path) if plan_path.exists() else None
            lock_wait = args.processing_lock_wait_seconds if args.processing_lock_wait_seconds is not None else (existing.get('processing_lock_wait_seconds', 0) if existing else 1800)
            if not math.isfinite(lock_wait) or lock_wait < 0:
                raise ValueError('processing_lock_wait_seconds 必须为有限非负数')
            await_plan_path = directory / 'await-export-plan.json'
            await_seconds = args.await_export_seconds if args.await_export_seconds is not None else (load(await_plan_path)['timeout_seconds'] if await_plan_path.exists() else 0)
            if not math.isfinite(await_seconds) or await_seconds < 0:
                raise ValueError('await_export_seconds 必须为有限非负数')
            frozen_model = directory / 'model-config.json'
            frozen_profile = directory / 'translation-style.json'
            for target, content in ((frozen_model, model_config), (frozen_profile, load(profile))):
                if target.exists() and load(target) != content:
                    raise ValueError('冻结模型或文体配置已变更')
                if not target.exists():
                    write_json(target, content)
            if await_seconds > 0 or await_plan_path.exists():
                inputs = await_export(directory, args.manifest_dir, selected.name, model_config,
                                      args.model_config, profile, await_seconds, lock_wait)
                if load(frozen_model) != model_config or load(frozen_profile) != load(profile):
                    raise ValueError('等待期间冻结模型或文体配置已变更')
            else:
                inputs = manifest_inputs(args.manifest_dir)
            config = {'version': 1, 'jurisdiction': selected.name, 'manifest_directory': str(args.manifest_dir.resolve()),
                      'model_config': model_config, 'ocr_engine': args.ocr_engine, 'translation_style': load(profile),
                      'chunk_chars': args.chunk_chars, 'max_chars': args.max_chars, 'max_calls': args.max_calls,
                      'max_job_attempts': args.max_job_attempts,
                      'inputs': inputs}
            if not existing or 'processing_lock_wait_seconds' in existing or args.processing_lock_wait_seconds not in (None, 0):
                config['processing_lock_wait_seconds'] = lock_wait
            if plan_path.exists() and load(plan_path) != config:
                raise ValueError('执行配置已变更；使用新output目录，旧批次保持冻结')
            if not config['inputs']:
                raise ValueError('manifest-dir 无待处理文件，禁止空集合完成')
            if not plan_path.exists():
                write_json(plan_path, config)
            if args.status_only:
                summarize(directory, pipeline, started)
                return 0
            for input_row in config['inputs']:
                manifest = Path(input_row['path'])
                input_sha = sha(manifest)
                job_id = 'bulk-' + hashlib.sha256((input_sha + json.dumps(config, sort_keys=True)).encode()).hexdigest()[:24]
                job_path = directory / 'jobs' / (job_id + '.json')
                prior = load(job_path) if job_path.exists() else None
                if prior and prior['status'] == 'pending_review':
                    try:
                        verify_completed(prior, pipeline)
                        continue
                    except FileIdentityPending as exc:
                        prior.update(status='pending_identity', reason=str(exc), file_role_gate=exc.decision)
                        write_json(job_path, prior)
                    except (OSError, ValueError, KeyError, TypeError) as exc:
                        prior.update(status='failed', reason='复用前证据校验失败: ' + str(exc))
                        write_json(job_path, prior)
                if prior and ((prior['status'] == 'failed' and not args.retry_failed)
                              or prior.get('attempts', 0) >= args.max_job_attempts):
                    continue
                row = {'id': manifest.stem, 'pipeline_batch': job_id, 'status': 'running', 'reason': '准备已归档原件',
                       'manifest': str(manifest.resolve()), 'manifest_sha256': input_sha, 'attempts': (prior or {}).get('attempts', 0) + 1,
                       'attempt_history': (prior or {}).get('attempt_history', [])}
                write_json(job_path, row)
                summarize(directory, pipeline, started)
                job_started = time.monotonic()
                scheduling_deferred = False
                try:
                    if shutil.disk_usage(directory).free < 5 * 1024 ** 3:
                        raise RuntimeError('可用磁盘低于5GiB，暂停处理并保留原件和断点')
                    prepared = directory / (job_id + '-manifest.json')
                    stage_plan_path = pipeline / 'batches' / job_id / 'plan.json'
                    if prior and prepared.exists() and stage_plan_path.exists():
                        if prior.get('prepared_manifest') and (Path(prior['prepared_manifest']).resolve() != prepared
                                or sha(prepared) != prior.get('prepared_manifest_sha256')):
                            raise ValueError('冻结准备输入SHA已变化，拒绝重新生成覆盖')
                        value = load(prepared)
                        expected_plan = staged_pipeline.make_plan(root, selected.name, value, model_config,
                            args.chunk_chars, args.max_chars, args.max_calls)
                        if expected_plan != load(stage_plan_path) or len(value.get('documents', [])) != 1:
                            raise ValueError('冻结准备输入与既有staged计划绑定不匹配，拒绝自动重制')
                        raw_sha = sha(value['documents'][0]['path'])
                        if prior.get('raw_sha256') and raw_sha != prior['raw_sha256']:
                            raise ValueError('冻结原件SHA已变化')
                        decision = file_role_decision(value['documents'][0].get('document', {}), pipeline, raw_sha)
                        if not decision['eligible']:
                            raise FileIdentityPending(decision)
                        metrics = {**prior.get('preparation', {}), 'reused_frozen_preparation': True, 'file_role_gate': decision,
                                   'historical_preparation_metrics_unavailable': not bool(prior.get('preparation')),
                                   'ocr_seconds': 0, 'automatic_tables_seconds': 0, 'ocr_reused': True}
                    else:
                        value, raw_sha, metrics = prepare_manifest(manifest, pipeline, args.ocr_engine)
                        value['translation_style_path'] = str(frozen_profile)
                    if prepared.exists() and load(prepared) != value:
                        raise ValueError('准备输入已变更，须建立新处理目录')
                    if not prepared.exists():
                        write_json(prepared, value)
                    row.update(raw_path=value['documents'][0]['path'], raw_sha256=raw_sha, preparation=metrics,
                               prepared_manifest=str(prepared), prepared_manifest_sha256=sha(prepared))
                    batch_exists = (pipeline / 'batches' / job_id / 'plan.json').exists()
                    options = SimpleNamespace(root=str(root), jurisdiction=selected.name, config=args.config,
                        stage='run', manifest=str(prepared), model_config=str(frozen_model),
                        chunk_chars=args.chunk_chars, max_chars=args.max_chars, max_calls=args.max_calls,
                        batch_id=None if batch_exists else job_id, rerun_batch=job_id if batch_exists else None,
                        parent_batch=None, repair=None, review=None, processing_lock_wait_seconds=lock_wait)
                    if load(frozen_model) != config['model_config'] or load(frozen_profile) != config['translation_style']:
                        raise ValueError('执行前冻结模型或文体配置校验失败')
                    write_json(job_path, row)  # 在模型/锁等待前持久化准备绑定，保留中断恢复证据。
                    result, paths = staged_pipeline.run(options)
                    row.update(raw_sha256=raw_sha, preparation=metrics, result=result,
                               reports={k: str(v) for k, v in paths.items()})
                    if result.get('retryable_scheduling'):
                        scheduling_deferred = True
                        row.update(status='deferred', reason='国家处理锁等待达到上限；停止本轮调度，重跑可重试且不计处理次数',
                                   attempts=(prior or {}).get('attempts', 0),
                                   scheduling_attempts=(prior or {}).get('scheduling_attempts', 0) + 1)
                    elif result['counts']['failed']:
                        row.update(status='failed', reason='流水线失败，详见逐件报告；缓存及失败原因保留')
                    else:
                        row.update(status='pending_review', reason='已生成原文和中英文稿，等待独立全文及原件复核',
                                   review_request=str(pipeline / 'batches' / job_id / 'review-request.json'))
                        row['review_request_sha256'] = sha(row['review_request'])
                        verify_completed(row, pipeline)
                except FileIdentityPending as exc:
                    row.update(status='pending_identity', reason=str(exc), file_role_gate=exc.decision,
                               attempts=(prior or {}).get('attempts', 0))
                except Exception as exc:
                    row.update(status='failed', reason=str(exc))
                row['duration_seconds'] = time.monotonic() - job_started
                row['attempt_history'].append({k: row[k] for k in ('attempts', 'scheduling_attempts', 'status', 'reason', 'duration_seconds', 'preparation', 'result', 'file_role_gate') if k in row})
                write_json(job_path, row)
                summarize(directory, pipeline, started)
                print(json.dumps({'id': row['id'], 'status': row['status'], 'reason': row['reason']}, ensure_ascii=False), flush=True)
                if scheduling_deferred:
                    return 2
            summarize(directory, pipeline, started)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
