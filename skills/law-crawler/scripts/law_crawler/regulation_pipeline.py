"""面向 skill 的显式法域、目录优先和可重跑批次执行器。"""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

from law_crawler.batch_runtime import BatchRuntime, emit_report, retry_fetch, safe_id
from law_crawler.crawl_store import CrawlStore, canonical_url, now, write_json
from law_crawler.jurisdiction_registry import resolve_jurisdiction
from law_crawler.platform_adapters import ADAPTERS
from law_crawler.regulation_selection import select_latest, identity_key, published_date

SCOPES = {'eur-lex.europa.eu': 'navigation_only',
          'rechtsprechung-im-internet.de': 'out_of_scope_case_law',
          'uitspraken.rechtspraak.nl': 'out_of_scope_case_law',
          'decisions.scc-csc.ca': 'out_of_scope_case_law',
          'a2aj.ca': 'out_of_scope_academic',
          'www.scc-csc.gc.ca':'out_of_scope_case_law',
          'scc-csc.gc.ca':'out_of_scope_case_law',
          'scc-csc.lexum.com':'out_of_scope_case_law',
          'decisions.fct-cf.ca':'out_of_scope_case_law',
          'arxiv.org':'out_of_scope_academic','github.com':'reference_only'}
OPTIONS = ('limit', 'per_platform_limit', 'max_details', 'timeout', 'delay', 'refresh',
           'max_attempts', 'backoff_base', 'experimental_sources')


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _directory(app, args, country):
    return app.country_download_dir(args.config, args.root, Path('index.md'), country)



def _validate_options(args):
    for name in ('limit', 'max_details', 'max_attempts'):
        value=getattr(args,name,None)
        if not isinstance(value,int) or isinstance(value,bool) or value<1:
            raise ValueError(f'{name} 必须为正整数')
    if args.per_platform_limit is not None and (not isinstance(args.per_platform_limit,int) or isinstance(args.per_platform_limit,bool) or args.per_platform_limit<1):
        raise ValueError('per_platform_limit 必须为正整数')
    import math
    for name in ('timeout','delay','backoff_base'):
        value=getattr(args,name,None)
        if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or value<0 or (name=='timeout' and value==0):
            raise ValueError(f'{name} 必须为有限且有效的非负数')


def _load_plan(app, args):
    _validate_options(args)
    root = app.download_dir(args.config, args.root) / '.batches'
    if root.is_symlink():
        raise ValueError('批次根目录不能使用符号链接')
    if getattr(args, 'rerun_batch', None):
        identifier = safe_id(args.rerun_batch)
        batch_dir = root / identifier
        if batch_dir.is_symlink() or (batch_dir/'plan.json').is_symlink():
            raise ValueError('批次目录不能使用符号链接')
        old = json.loads((batch_dir/'plan.json').read_text(encoding='utf-8'))
        if old.get('schema_version') != 1 or not old.get('jurisdiction') or old.get('mode') not in ('metadata', 'content'):
            raise ValueError('批次计划缺少显式法域或采集模式')
        if args.jurisdiction and resolve_jurisdiction(args.root, args.jurisdiction).name != old['jurisdiction']:
            raise ValueError('重跑输入法域与原批次不一致')
        if args.mode and args.mode != old['mode']:
            raise ValueError('重跑不得改变原批次采集模式；请创建新批次')
        if old.get('download_base') and old['download_base']!=str(app.download_dir(args.config,args.root)):
            raise ValueError('重跑不能改变原批次下载基础目录')
        selection = resolve_jurisdiction(args.root, old['jurisdiction'], args.index)
        args.jurisdiction, args.mode, args.index = selection.name, old['mode'], selection.index
        if not set(old['platforms']).issubset(selection.platforms):
            raise ValueError('原批次平台已不在当前法域索引中')
        if set(old.get('options', {})) != set(OPTIONS):
            raise ValueError('原批次运行参数不完整或含未知字段')
        for key, value in old['options'].items():
            setattr(args, key, value)
        _validate_options(args)
        return root, old, selection
    selection = resolve_jurisdiction(args.root, args.jurisdiction, args.index)
    args.jurisdiction, args.index = selection.name, selection.index
    if args.mode not in ('metadata', 'content'):
        raise ValueError('必须确认仅元数据目录或法规原文：--mode metadata|content')
    platforms = list(selection.platforms)
    if args.platform != 'all':
        if args.platform not in platforms:
            raise ValueError('指定平台不在该国家/法域的工程索引中')
        platforms = [args.platform]
    plan = dict(schema_version=1, jurisdiction=selection.name, mode=args.mode,
                index=str(selection.index.relative_to(args.root.resolve())),
                index_sha256=digest(selection.index.read_text(encoding='utf-8')), platforms=platforms,
                countries=list(selection.countries), options={key: getattr(args, key) for key in OPTIONS},
                created_at=now(), download_base=str(app.download_dir(args.config,args.root)),
                selection_policy='latest_official_published_at_with_unknowns_flagged',
                steps=['读取工程法域索引并验证范围', '有界发现并生成逐法规元数据',
                       '按法规身份/公布日期生成候选快照',
                       '仅元数据或下载选中原件；文本转Markdown',
                       '记录逐文件状态并输出JSON/Markdown/HTML报告'])
    return root, plan, selection


def _metadata_path(directory, candidate):
    catalog = directory/'catalog'
    catalog.mkdir(exist_ok=True)
    return catalog/(candidate['id']+'.json')


def _save_metadata(directory, candidate):
    path = _metadata_path(directory, candidate)
    write_json(path, dict(schema_version=2, document=candidate['metadata'],
        source=dict(platform=candidate['platform'], detail_url=candidate['detail_url'],
                    file_urls=candidate['file_urls']), country=candidate['country'],
        candidate_id=candidate['id'], selection=candidate.get('selection'),
        retrieval=candidate.get('retrieval', dict(status='metadata_only', reason='仅目录元数据，未请求法规原件')),
        collected_at=candidate.get('collected_at')))
    return str(path)


def _discover(args, batch, report, app, stores, targets=None, detail_targets=None):
    candidates = []
    for platform in (targets if targets is not None else batch.plan['platforms']):
        scope = SCOPES.get(platform)
        adapter = ADAPTERS.get(platform)
        if scope or adapter is None:
            status = scope or 'unsupported'
            report['platforms'].append(dict(platform=platform, status=status))
            batch.record('platform:'+platform, 'skip', status, platform=platform)
            continue
        country = adapter.country
        if args.experimental_sources and hasattr(adapter, 'with_experimental_sources'):
            adapter = adapter.with_experimental_sources()
        state = dict(platform=platform, country=country, status='discovering', detail_pages=0, errors=[])
        report['platforms'].append(state)
        def fetch_page(url):
            return retry_fetch(lambda u: app.official_fetch(u, args.timeout, adapter.allowed_hosts), url,
                               max_attempts=args.max_attempts, base_delay=args.backoff_base)
        try:
            details = (detail_targets or {}).get(platform) or adapter.discover(fetch_page, args.max_details)
            seen = set()
            for detail in details[:args.max_details]:
                normalized = canonical_url(detail)
                if normalized in seen:
                    continue
                seen.add(normalized)
                state['detail_pages'] += 1
                time.sleep(args.delay)
                try:
                    reader = getattr(adapter, 'read_catalog_entry', adapter.read_document)
                    doc = reader(detail, fetch_page)
                    if doc.metadata.get('document_type', {}).get('value') != 'regulation':
                        raise ValueError('详情未明确确认正式法规资料类型')
                    for url in doc.file_urls:
                        if urlsplit(url).hostname not in adapter.allowed_hosts:
                            raise ValueError('原件链接不在平台官方白名单中')
                    candidate_identity=identity_key(doc.metadata,country,doc.detail_url,platform)[0]
                    candidate_version=published_date(doc.metadata) or 'publication-unknown'
                    text_version=doc.metadata.get('text_version',{}).get('value')
                    candidate = dict(id=digest(json.dumps([country,platform,canonical_url(doc.detail_url),candidate_identity,candidate_version,text_version],ensure_ascii=False)),
                                     country=country, platform=platform, detail_url=doc.detail_url,
                                     file_urls=doc.file_urls, metadata=doc.metadata, collected_at=now())
                    candidates.append(candidate)
                    _save_metadata(stores[country].directory, candidate)
                    batch.record('catalog:'+candidate['id'], 'success', '法规目录元数据已保存，尚未请求原文',
                                 platform=platform, country=country, detail_url=doc.detail_url)
                except Exception as exc:
                    error=dict(platform=platform, url=detail, error=str(exc))
                    report['errors'].append(error); state['errors'].append(error)
                    batch.record('detail:'+digest(detail), 'fail', str(exc), attempts=getattr(exc, 'retry_attempts', 1), **error)
            state['status'] = 'metadata_collected' if any(c['platform']==platform for c in candidates) else 'no_metadata'
        except Exception as exc:
            state['status']='failed'
            error=dict(platform=platform, url=adapter.entry_urls[0], error=str(exc))
            report['errors'].append(error); state['errors'].append(error)
            batch.record('platform:'+platform, 'fail', str(exc), attempts=getattr(exc,'retry_attempts',1), **error)
        if getattr(adapter, 'production_discovery', None):
            state['production_discovery'] = adapter.production_discovery
    select_latest(candidates)
    for c in candidates:
        _save_metadata(stores[c['country']].directory, c)
    return candidates


def _previous_states(batch):
    if batch.attempt_number <= 1:
        return {}
    prior = batch.batch_dir/'attempts'/f'{batch.attempt_number-1:04d}'/'states.json'
    return json.loads(prior.read_text(encoding='utf-8')).get('states', {}) if prior.exists() else {}


def _existing_latest(store, candidate):
    """已收集历史目录/原件若已存在更新日期，不把本轮较旧条目设为当前。"""
    key = candidate.get('identity_key') or identity_key(candidate['metadata'], candidate['country'],candidate['detail_url'],candidate['platform'])[0]
    dates=[]
    for path in (store.directory/'catalog').glob('*.json'):
        prior=json.loads(path.read_text(encoding='utf-8'))
        source=prior.get('source',{})
        md=prior.get('document',{})
        if source.get('detail_url') and identity_key(md,candidate['country'],source['detail_url'],source.get('platform',''))[0]==key:
            known=published_date(md)
            if known: dates.append(known)
    for record in store.records():
        for source in record.get('sources',[]):
            if not source.get('detail_url'):
                continue
            md=source.get('metadata', {})
            oldkey=identity_key(md,candidate['country'],source['detail_url'],source.get('platform',''))[0]
            if oldkey==key and published_date(md):
                dates.append(published_date(md))
    current=candidate.get('publication_date')
    return max(dates) if dates and (not current or max(dates)>current) else None


def _replay_cache(store, candidate, previous):
    """只把前轮成功且本地原件/派生文件完整的项作为可复用结果。"""
    completed = {candidate['id']+':'+digest(canonical_url(u)): previous.get(candidate['id']+':'+digest(canonical_url(u)), {}) for u in candidate['file_urls']}
    cached_by_key = {}
    for url in candidate['file_urls']:
        key = candidate['id']+':'+digest(canonical_url(url))
        if completed[key].get('status') not in ('success', 'succeeded'):
            continue
        row = store.db.execute("SELECT sha256 FROM requests WHERE canonical_url=? AND status='succeeded'", (canonical_url(url),)).fetchone()
        cached = store.by_hash(row['sha256']) if row else None
        if cached:
            try:
                store.upgrade_conversion(cached)
            except (OSError, ValueError):
                pass
        if not cached or not store.valid(cached):
            completed[key] = dict(status='pending', reason='前轮原件或派生文件缺失/损坏，需要修复')
        else:
            cached_by_key[key] = cached
    return completed, cached_by_key


def _execute_candidates(args, batch, report, app, stores, candidates):
    previous = _previous_states(batch)
    per_counts={}; unique=set(); reported=set()
    replay = {}; admitted = set()
    if args.mode == 'content' and args.rerun_batch and not args.rerun_all:
        # 先为所有可复用成功项预留额度，再处理失败/待执行项。
        # 仅在遍历到成功项时计数，会让排在它之前的失败项抢占额度。
        for c in candidates:
            store = stores[c['country']]
            if not c['selection']['selected'] or _existing_latest(store, c):
                continue
            completed, cached = _replay_cache(store, c, previous)
            replay[c['id']] = (completed, cached)
            if not cached:
                continue
            record = next(iter(cached.values()))
            identity = (c['country'], record['file']['sha256'])
            if identity not in unique:
                if len(unique) >= args.limit or per_counts.get(c['platform'], 0) >= (args.per_platform_limit or args.limit):
                    continue
                unique.add(identity)
                per_counts[c['platform']] = per_counts.get(c['platform'], 0) + 1
            admitted.add(c['id'])
    for c in candidates:
        store=stores[c['country']]
        newer=_existing_latest(store,c)
        if newer:
            c['selection']=dict(selected=False,reason='superseded_by_stored_publication',
                                latest_unverified=False,latest_publication=newer,selected_id=None)
        md_path=_save_metadata(store.directory,c)
        report['catalog'].append(dict(candidate_id=c['id'], country=c['country'], file=md_path,
                                      title=c['metadata'].get('title',{}).get('value'), selection=c['selection']))
        if args.mode == 'metadata':
            batch.record(c['id'], 'success', '仅元数据模式：目录已保存，未请求法规原件', metadata_file=md_path)
            continue
        if not c['selection']['selected']:
            batch.record(c['id'],'skip',c['selection']['reason'],metadata_file=md_path)
            for url in c['file_urls']:
                batch.record(c['id']+':'+digest(canonical_url(url)),'skip',c['selection']['reason'],url=url,country=c['country'])
            continue
        newer=_existing_latest(store,c)
        if newer:
            batch.record(c['id'],'skip',f'本地同法规已有更晚公布日期 {newer}',metadata_file=md_path)
            continue
        completed, cached_by_key = replay[c['id']] if c['id'] in replay else _replay_cache(store, c, previous)
        if args.rerun_batch and not args.rerun_all and any(s.get('status') in ('success','succeeded') for s in completed.values()):
            if c['id'] not in admitted:
                for url in c['file_urls']:
                    batch.record(c['id']+':'+digest(canonical_url(url)), 'skip', '达到本次总限量或平台限量', country=c['country'], url=url)
                continue
            successful_key = next(k for k,s in completed.items() if s.get('status') in ('success','succeeded'))
            successful = completed[successful_key]
            cached = cached_by_key[successful_key]
            store.persist(cached)
            body_review=cached.get('body_review',dict(status='not_reviewed'))
            cached_file = str(store.directory/cached['file']['path'])
            identity = (c['country'], cached['file']['sha256'])
            if identity not in reported:
                report['files'].append(dict(file=cached_file,sha256=cached['file']['sha256'],country=c['country'],platform=c['platform'],
                    result='previous_success',metadata_file=str(Path(cached_file).with_suffix('.json')),
                    selection=c['selection'],body_review=body_review))
                report['counts']['previous_success']=report['counts'].get('previous_success',0)+1
                reported.add(identity)
            for key, old in completed.items():
                batch.record(key, 'success' if old.get('status') in ('success','succeeded') else 'skip',
                    '该法规原件已在先前轮次完成，本轮复用完成状态',
                    previous_status=old.get('status'), file=cached_file if key in cached_by_key else old.get('file'), country=c['country'],body_review=body_review)
            continue
        for url in c['file_urls']:
            key=c['id']+':'+digest(canonical_url(url))
            if len(unique)>=args.limit or per_counts.get(c['platform'],0)>=(args.per_platform_limit or args.limit):
                batch.record(key,'skip','达到本次总限量或平台限量',country=c['country'],url=url); continue
            events=[]
            batch.record(key,'running','开始请求选中的法规原件',country=c['country'],url=url)
            def on_event(event):
                events.append(event)
                batch.record(key,'running',event['reason'],attempts=event['attempt'],
                             country=c['country'],url=url,request_events=list(events))
            def fetch_file(target):
                return retry_fetch(lambda u: app.official_fetch(u,args.timeout,ADAPTERS[c['platform']].allowed_hosts),
                    target,max_attempts=args.max_attempts,base_delay=args.backoff_base,event_callback=on_event)
            try:
                result,record=store.save(url,c['detail_url'],c['platform'],fetch_file,c['metadata'],
                                         refresh=args.refresh,convert_text=True)
                # 同一官方资源的其他入口已读到新元数据时，更新已验证过的来源证据。
                # 不为未实际请求过的别名捏造成功请求或重定向终点。
                for alias in candidates:
                    if alias['id']!=c['id'] and alias['selection'].get('selected_id')==c['id'] and alias['selection']['reason']=='duplicate_official_resource':
                        for source in list(record['sources']):
                            if source.get('platform')==alias['platform'] and source.get('detail_url')==alias['detail_url'] and source['download_url'] in alias['file_urls']:
                                store.add_source(record,source['download_url'],source['detail_url'],source['platform'],source['final_url'],alias['metadata'])
                store.persist(record)
                sha=record['file']['sha256']; identity=(c['country'],sha)
                if identity not in unique:
                    unique.add(identity); per_counts[c['platform']]=per_counts.get(c['platform'],0)+1
                    report['files'].append(dict(file=str(store.directory/record['file']['path']),sha256=sha,
                        country=c['country'],platform=c['platform'],result=result,
                        metadata_file=str((store.directory/record['file']['path']).with_suffix('.json')),
                        selection=c['selection'],body_review=record.get('body_review',dict(status='not_reviewed'))))
                    reported.add(identity)
                report['counts'][result]=report['counts'].get(result,0)+1
                c['retrieval']=dict(status=result,reason='官方原件已保存或本地有效缓存已复用',
                                    file=record['file'],batch_id=batch.batch_id)
                _save_metadata(store.directory,c)
                batch.record(key,'success',result,attempts=events[-1]['attempt'] if events else 0,
                             url=url,country=c['country'],file=str(store.directory/record['file']['path']),
                             request_events=events,body_review=record.get('body_review',dict(status='not_reviewed')))
                # 同一详情其余格式是备选，不需要重复下载同一法规。
                for alternative in c['file_urls'][c['file_urls'].index(url)+1:]:
                    batch.record(c['id']+':'+digest(canonical_url(alternative)),'skip',
                                 '已取得该法规首个可用格式，跳过备选格式',country=c['country'],url=alternative)
                break
            except Exception as exc:
                error=dict(platform=c['platform'],country=c['country'],url=url,error=str(exc))
                report['errors'].append(error)
                c['retrieval']=dict(status='failed',reason=str(exc),batch_id=batch.batch_id)
                _save_metadata(store.directory,c)
                batch.record(key,'fail',str(exc),attempts=events[-1]['attempt'] if events else getattr(exc,'retry_attempts',0),
                             request_events=events, **error)
        if not c['file_urls']:
            batch.record(c['id'],'fail','详情没有已适配的官方正文下载链接',metadata_file=md_path)
            report['errors'].append(dict(platform=c['platform'],url=c['detail_url'],error='详情没有已适配的正文下载链接'))


def execute(args):
    from law_crawler import download_regulations as app
    root,plan,selection=_load_plan(app,args)
    platforms=plan['platforms']; countries=[]
    for platform in platforms:
        if platform in SCOPES or platform not in ADAPTERS:
            continue
        country=ADAPTERS[platform].country
        if selection.name!='EU' and country!=selection.name:
            raise ValueError(f'平台归属{country}，与输入法域{selection.name}不匹配')
        _directory(app,args,country)
        if country not in countries: countries.append(country)
    _directory(app,args,selection.name)
    batch=BatchRuntime(root,plan_dict=None if args.rerun_batch else plan,
                       batch_id=args.batch_id,rerun_from=args.rerun_batch)
    report=dict(schema_version=3,title='官方法规采集报告',jurisdiction=selection.name,mode=args.mode,
                batch_id=batch.batch_id,plan_file=str(batch.batch_dir/'plan.json'),
                selection_policy=plan['selection_policy'],files=[],catalog=[],counts={},platforms=[],errors=[],migration_errors=[])
    stores={}
    termination_reason='执行结束，候选文件未完成'
    try:
        if args.plan_only:
            report['result']='planned_only'
            batch.record('plan','skip','仅生成执行计划，未发起网络请求')
            return 0
        with ExitStack() as stack:
            for country in countries:
                stores[country]=stack.enter_context(CrawlStore(_directory(app,args,country)))
                report['migration_errors'].extend(stores[country].migration_errors)
            snapshot=batch.batch_dir/'candidates.json'
            if args.rerun_batch and snapshot.exists():
                candidates=json.loads(snapshot.read_text(encoding='utf-8'))
                for c in candidates:
                    if c.get('platform') not in platforms or c.get('country') not in stores:
                        raise ValueError('批次候选国家/平台与原计划不符')
                    if identity_key(c['metadata'],c['country'],c['detail_url'],c['platform'])[0]!=c['identity_key']:
                        raise ValueError('批次候选身份校验失败')
                select_latest(candidates)
                discovery_path=batch.batch_dir/'discovery.json'
                prior_discovery=json.loads(discovery_path.read_text(encoding='utf-8')) if discovery_path.exists() else []
                targets=[]; detail_targets={}
                for state in prior_discovery:
                    if state['status'] in ('failed','no_metadata'):
                        targets.append(state['platform'])
                    elif state.get('errors'):
                        targets.append(state['platform'])
                        detail_targets[state['platform']]=list(dict.fromkeys(e['url'] for e in state['errors']))
                if targets:
                    recovered=_discover(args,batch,report,app,stores,targets,detail_targets)
                    merged={c['id']:c for c in candidates}
                    merged.update({c['id']:c for c in recovered})
                    candidates=list(merged.values()); select_latest(candidates)
                    checked={s['platform']:s for s in report['platforms']}
                    for s in prior_discovery:
                        if s['platform'] not in checked: report['platforms'].append(dict(s,status='snapshot_replay'))
                    write_json(discovery_path,[checked.get(s['platform'],s) for s in prior_discovery])
                    write_json(snapshot,candidates)
                else:
                    report['platforms']=[dict(next((s for s in prior_discovery if s['platform']==p),{'platform':p}),status=SCOPES[p] if p in SCOPES else 'snapshot_replay') for p in platforms]
            else:
                candidates=_discover(args,batch,report,app,stores)
                write_json(snapshot,candidates)
                write_json(batch.batch_dir/'discovery.json',report['platforms'])
            write_json(batch.attempt_dir/'candidates.json',candidates)
            jobs=[]
            for candidate in candidates:
                if args.mode=='content' and candidate['selection']['selected']:
                    for url in candidate['file_urls']:
                        key=candidate['id']+':'+digest(canonical_url(url))
                        batch.record(key,'pending','候选快照已确认，等待下载',country=candidate['country'],url=url)
                        jobs.append(dict(key=key,url=url,country=candidate['country'],metadata=candidate['metadata']))
            write_json(batch.attempt_dir/'file-plan.json',dict(mode=args.mode,jobs=jobs))
            _execute_candidates(args,batch,report,app,stores,candidates)
            for country,store in stores.items():
                store.update_current_selection(country)
                current=[dict(candidate_id=c['id'],identity_key=c['identity_key'],selection=c['selection'],
                              metadata_file=str(_metadata_path(store.directory,c))) for c in candidates if c['country']==country]
                manifest=store.directory/'latest-selection.json'
                previous_index=json.loads(manifest.read_text(encoding='utf-8')).get('entries',[]) if manifest.exists() else []
                by_identity={entry['identity_key']:entry for entry in previous_index}
                for entry in current:
                    if entry['selection']['selected']:
                        by_identity[entry['identity_key']]=entry
                write_json(manifest,dict(batch_id=batch.batch_id,entries=list(by_identity.values())))
            for state in report['platforms']:
                platform=state['platform']
                if platform not in ADAPTERS or platform in SCOPES:
                    continue
                state['checked_at']=now()
                state['errors']=[e for e in report['errors'] if e.get('platform')==platform]
                state['file_count']=sum(f.get('platform')==platform for f in report['files'])
                if args.mode=='content':
                    state['status']=('partial_success' if state['errors'] else 'success') if state['file_count'] else ('content_failed' if state['errors'] else 'not_selected')
                    if state['file_count'] and any(c['platform']==platform and c['metadata'].get('source_route',{}).get('value')=='experimental_official_fallback' for c in candidates):
                        state['status']='experimental_success'
                elif state.get('status')=='snapshot_replay':
                    state['status']='metadata_collected'
            for country,store in stores.items():
                status_path=store.directory/'platform-status.json'
                status=json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else {}
                status.update({s['platform']:s for s in report['platforms'] if ADAPTERS.get(s['platform']) and ADAPTERS[s['platform']].country==country})
                write_json(status_path,status)
            report['result']='completed_with_errors' if report['errors'] else 'completed'
            return 0 if (report['catalog'] if args.mode=='metadata' else report['files']) else 1
    except BaseException as exc:
        termination_reason='执行中止：'+(str(exc) or type(exc).__name__)
        report['errors'].append(dict(error=termination_reason,type=type(exc).__name__))
        report['result']='interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed'
        if isinstance(exc,(KeyboardInterrupt,SystemExit)): raise
        return 1
    finally:
        report['unique_files']=len(report['files'])
        report['metadata_count']=len(report['catalog'])
        paths=batch.finish(report,reason=termination_reason)
        final_report=json.loads(paths['json'].read_text(encoding='utf-8'))
        for country in list(dict.fromkeys(countries+[selection.name])):
            directory=_directory(app,args,country)
            directory.mkdir(parents=True,exist_ok=True)
            local=deepcopy(final_report)
            if country!=selection.name:
                local['files']=[f for f in local['files'] if f['country']==country]
                local['catalog']=[c for c in local['catalog'] if c['country']==country]
            emit_report(local,directory,'last-run')
        print(f'批次 {batch.batch_id} 第{batch.attempt_number}轮；报告: {paths["html"]}')
