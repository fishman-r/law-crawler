"""Read-only capability and coverage evidence matrix. No network or queue writes."""
import argparse
from collections import Counter
from datetime import datetime, timezone, date
import hashlib
import html
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from .capability_registry import describe_platform, canonical_platform
from .platform_adapters import ADAPTERS
from .catalog_census_report import report_sources, cutoff_bucket
from .jurisdiction_registry import resolve_jurisdiction
from .resources import skill_directory


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(path):
    raw=Path(path).read_bytes()
    return json.loads(raw), dict(path=str(Path(path).resolve()),sha256=sha(raw))


def unknown(reason, numerator=None, denominator=None, unit=None):
    return dict(numerator=numerator,denominator=denominator,unit=unit,ratio=None,status='unknown',reason=reason)


def read_plan(root):
    value,evidence=read_json(Path(root)/'plan.json')
    signature=Path(root)/'plan.sha256'
    if signature.exists() and signature.read_text().strip()!=evidence['sha256']:
        raise ValueError('Frozen plan SHA mismatch')
    evidence['signature_verified']=signature.exists()
    return value,evidence


def scope_plan(plan,selection,label):
    if plan.get('jurisdiction')!=selection.name:
        raise ValueError(label+' jurisdiction differs from explicitly selected jurisdiction')


def index_binding(plan,index_sha):
    old=plan.get('registry_index_sha256',plan.get('index_sha256'))
    return dict(frozen_sha256=old,current_sha256=index_sha,drift=None if old is None else old!=index_sha,
                status='not_bound' if old is None else 'changed' if old!=index_sha else 'matched')


def ratio_from_evidence(status,actual,expected_scope,folder):
    """Only accept explicitly comparable counters with locally retained evidence.

    observed_total is intentionally NOT a denominator: it may count cards, not URLs.
    """
    coverage=status.get('coverage_evidence')
    block=coverage.get('catalogue') if isinstance(coverage,dict) else None
    fallback=unknown('缺少同范围、同计数单位且可校验的目录分母证据',actual,unit='distinct_detail_urls')
    if not expected_scope.get('versions'):return unknown('冻结范围未声明版本口径',actual,unit='distinct_detail_urls')
    if not isinstance(block,dict):return fallback
    if block.get('review_status')!='independently_verified' or not block.get('reviewer') or not block.get('review_note'):return fallback
    num,den=block.get('numerator',{}),block.get('denominator',{})
    if not isinstance(num,dict) or not isinstance(den,dict):return fallback
    if (den.get('basis')!='official_explicit_total' or num.get('scope')!=expected_scope or den.get('scope')!=expected_scope or
        num.get('unit')!='distinct_detail_urls' or den.get('unit')!='distinct_detail_urls' or
        type(num.get('value')) is not int or type(den.get('value')) is not int or
        num['value']!=actual or den['value']<actual or actual<0):
        return unknown('目录分子分母范围、单位或实际计数不一致',actual,unit='distinct_detail_urls')
    evidence=block.get('evidence')
    if not isinstance(evidence,list) or not evidence:return fallback
    verified=[]
    for item in evidence:
        if not isinstance(item,dict):return fallback
        path=Path(item.get('path',''))
        path=path if path.is_absolute() else folder/path
        if not path.resolve().is_relative_to(folder.resolve()) or path.is_symlink() or not path.is_file():return fallback
        if sha(path.read_bytes())!=item.get('sha256'):return unknown('目录分母证据哈希不一致',actual,unit='distinct_detail_urls')
        u=urlsplit(item.get('source_url',''))
        adapter=ADAPTERS.get(canonical_platform(expected_scope['platform']))
        allowed=set(getattr(adapter,'allowed_hosts',()))
        if u.scheme not in ('http','https') or u.hostname not in allowed or u.username or u.password:return fallback
        verified.append(dict(path=str(path.resolve()),sha256=item['sha256'],source_url=item['source_url']))
    if den['value']==0:
        return dict(**unknown('已观察空范围；零分母不计算百分比',0,0,'distinct_detail_urls'),evidence=verified)
    return dict(numerator=actual,denominator=den['value'],unit='distinct_detail_urls',ratio=actual/den['value'],
                status='review_bound',reviewer=block['reviewer'],review_note=block['review_note'],reason='相同冻结范围与distinct_detail_urls单位；仅表示此来源目录范围',evidence=verified)


def census_observations(root,plan):
    result=[]
    cutoff=plan.get('cutoff')
    if not isinstance(cutoff,str) or date.fromisoformat(cutoff).isoformat()!=cutoff:
        raise ValueError('Census cutoff must be explicit YYYY-MM-DD')
    for folder_name,label in report_sources(root,plan):
        folder=root/folder_name
        status={};status_evidence=None
        if (folder/'status.json').is_file():status,status_evidence=read_json(folder/'status.json')
        source_plan={};plan_evidence=None
        if (folder/'plan.json').is_file():source_plan,plan_evidence=read_plan(folder)
        if source_plan.get('cutoff') not in (None,cutoff):raise ValueError('Source cutoff differs from census cutoff')
        normalize_versions=lambda x:'all_discoverable' if x=='all_discoverable_versions' else x
        if source_plan.get('versions') is not None and normalize_versions(source_plan['versions'])!=normalize_versions(plan.get('versions')):raise ValueError('Source versions differ from census version scope')
        platform=canonical_platform(source_plan.get('platform',folder_name))
        count=0;variants=0;invalid_urls=0;dates=Counter();snapshot=hashlib.sha256();db_path=folder/'records.sqlite3';db_exists=db_path.is_file()
        if db_exists:
            if db_path.is_symlink():raise ValueError('Source database symlink is not accepted')
            with sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro',uri=True) as db:
                db.execute('BEGIN')
                urls=set()
                for raw, in db.execute('SELECT data FROM records ORDER BY data'):
                    row=json.loads(raw)
                    if row.get('source_environment')=='development':continue
                    snapshot.update(raw.encode());snapshot.update(b'\n')
                    count+=1
                    u=row.get('url');parsed=urlsplit(u) if isinstance(u,str) else None
                    if parsed and parsed.scheme in ('http','https') and parsed.hostname and not parsed.username and not parsed.password:urls.add(u)
                    else:invalid_urls+=1
                    variants+=len(row.get('catalogue_variants') or [row]);dates[cutoff_bucket(row,cutoff)]+=1
                unique=len(urls)
        else:unique=None
        metric_scope=dict(platform=folder_name,cutoff=cutoff,versions=plan.get('versions'))
        metric=ratio_from_evidence(status,unique,metric_scope,folder) if db_exists else unknown('未取得来源目录数据库',unit='distinct_detail_urls')
        if invalid_urls:metric=unknown('目录含无效或缺失详情URL，需先核对记录',unique,unit='distinct_detail_urls')
        result.append(dict(source=folder_name,canonical_platform=platform,name=label,
            status=status.get('status','not_started'),updated_at=status.get('updated_at'),database_present=db_exists,
            stored_catalogue_records=count if db_exists else None,distinct_detail_urls=unique,
            preserved_metadata_variants=variants if db_exists else None,cutoff_counts=dict(dates),
            reported_total_unverified=status.get('observed_total'),reported_total_label='来源状态记录的总数，尚未核验是否为官方分母',invalid_url_rows=invalid_urls,pages=status.get('pages'),
            catalogue_complete_reported=status.get('catalogue_complete'),all_versions_complete_reported=status.get('all_versions_complete'),
            failure_count=len(status.get('failures',[])) if isinstance(status.get('failures'),list) else None,
            blockers=[str(x) for x in (status.get('reason'),status.get('error')) if x]+list(status.get('limitations') or []),
            metric_scope=metric_scope,source_versions=source_plan.get('versions'),versions_basis='source_plan' if source_plan.get('versions') is not None else 'inherited_parent_plan',catalogue_coverage=metric,
            attachment_completeness=unknown('未取得附件总清单及逐件独立完整性验收'),
            historical_version_coverage=unknown('未取得官方历史版本全集分母和逐版核对证据'),
            records_snapshot_sha256=snapshot.hexdigest() if db_exists else None,status_evidence=status_evidence,plan_evidence=plan_evidence))
    return result


def queue_snapshot(batch,plan,selection):
    scope_plan(plan,selection,'Batch')
    path=batch/'queue.sqlite3'
    if path.is_symlink():raise ValueError('Queue symlink is not accepted')
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        counts=dict(db.execute('SELECT status,count(*) FROM jobs GROUP BY status'))
        per_platform={}
        for platform,status,count in db.execute('SELECT platform,status,count(*) FROM jobs GROUP BY platform,status'):
            per_platform.setdefault(canonical_platform(platform),Counter())[status]+=count
        if not set(per_platform)<=set(canonical_platform(p) for p in selection.platforms):raise ValueError('Queue sources are outside selected jurisdiction index')
        total=sum(counts.values());digest=hashlib.sha256()
        for row in db.execute('SELECT id,platform,record FROM jobs ORDER BY id'):
            digest.update(json.dumps(row,ensure_ascii=False).encode())
        if type(plan.get('expected')) is not int or total!=plan['expected'] or digest.hexdigest()!=plan.get('queue_sha256'):
            raise ValueError('Queue count/content differs from frozen plan')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        files,size=db.execute('SELECT count(*),coalesce(sum(bytes),0) FROM objects').fetchone() if 'objects' in tables else (None,None)
    complete=counts.get('completed',0)
    return dict(snapshot_at=datetime.now(timezone.utc).isoformat(),total_tasks=total,status_counts=counts,
        queue_sha256=digest.hexdigest(),queue_hash_verified=True,
        queue_completion=dict(numerator=complete,denominator=total,unit='frozen_detail_tasks',ratio=complete/total if total else None,
            reason='冻结详情任务完成率；不是唯一法规数、正文身份核验率或全国覆盖率'),
        unique_object_count=files,object_bytes=size,object_count_scope='该队列objects登记，不是整个Bucket盘点或法规数量',
        per_platform={p:dict(c) for p,c in per_platform.items()})


def build(jurisdiction,registry_root=None,census_root=None,batch=None):
    selection=resolve_jurisdiction(registry_root or skill_directory(),jurisdiction)
    index_raw=selection.index.read_bytes();index_sha=sha(index_raw)
    scope=dict(jurisdiction=selection.name,index=dict(path=str(selection.index.resolve()),sha256=index_sha),
               registered_platforms=list(selection.platforms),cutoff=None)
    observations=[];queue=None;cp=None
    if census_root:
        census_root=Path(census_root).resolve();cp,pe=read_plan(census_root);scope_plan(cp,selection,'Census')
        scope.update(census_plan=pe,census_index_binding=index_binding(cp,index_sha),cutoff=cp.get('cutoff'),versions=cp.get('versions'))
        observations=census_observations(census_root,cp)
    if batch:
        batch=Path(batch).resolve();bp,pe=read_plan(batch);scope_plan(bp,selection,'Batch')
        if census_root and Path(bp.get('census_root','')).resolve()!=census_root:raise ValueError('Batch references a different census root')
        scope.update(batch_plan=pe,batch_index_binding=index_binding(bp,index_sha))
        if census_root:
            bindings=[]
            by_source={o['source']:o for o in observations}
            for item in bp.get('sources',[]):
                name=item.get('source') if isinstance(item,dict) else item
                if name not in by_source:raise ValueError('Batch source is absent from census scope')
                obs=by_source[name];source_dir=census_root/name
                for field,file in (('plan_sha256','plan.json'),('records_sha256','records.sqlite3')):
                    expected=item.get(field) if isinstance(item,dict) else None
                    if expected is not None and sha((source_dir/file).read_bytes())!=expected:raise ValueError('Census source changed since batch freeze: '+name)
                bindings.append(dict(source=name,hash_bound=isinstance(item,dict) and bool(item.get('plan_sha256') and item.get('records_sha256'))))
            scope['batch_census_source_bindings']=bindings
        queue=queue_snapshot(batch,bp,selection)
    rows=[];groups={}
    for platform in selection.platforms:groups.setdefault(canonical_platform(platform),[]).append(platform)
    for canonical,hosts in groups.items():
        observed=[o for o in observations if o['canonical_platform']==canonical]
        static=describe_platform(hosts[0])
        rows.append(dict(platform=canonical,canonical_platform=canonical,indexed_hosts=hosts,index_registered=True,
            static_capabilities=static,observed_sources=observed,
            queue_status_counts=queue['per_platform'].get(canonical) if queue else None,
            actual_access='recorded_snapshot_not_live_probe' if any(o['status_evidence'] or o['plan_evidence'] or o['database_present'] for o in observed) else 'not_observed',
            unresolved=[] if observed else ['本次未取得该索引入口的普查证据；不视为零部法规'],
            next_actions=(['实现并测试该索引来源适配器'] if not static.get('registered_adapter') else ['补充真实分页结束及目录对账证据'])+['取得同范围官方目录分母并独立核对','核对正文及应收附件清单','取得官方历史版本清单并逐版核对']))
    # Retain additional frozen routes without falsely promoting them into the index.
    known={r['canonical_platform'] for r in rows}
    for obs in observations:
        if obs['canonical_platform'] not in known:
            rows.append(dict(platform=obs['source'],canonical_platform=obs['canonical_platform'],indexed_hosts=[],index_registered=False,
                static_capabilities=describe_platform(obs['source']),observed_sources=[o for o in observations if o['canonical_platform']==obs['canonical_platform']],
                queue_status_counts=queue['per_platform'].get(obs['canonical_platform']) if queue else None,
                actual_access='recorded_snapshot_not_live_probe',unresolved=['来源尚未映射到所选法域索引，不据此宣称范围完整'],next_actions=['核对该补充来源与已登记法域的关系','独立建立目录、附件和历史版本分母']))
            known.add(obs['canonical_platform'])
    return dict(schema_version=1,generated_at=datetime.now(timezone.utc).isoformat(),scope=scope,
        metric_policy='能力不等于本轮验收；任务完成率不等于法规、附件或历史版本覆盖率',
        platforms=rows,queue=queue,
        overall=dict(regulation_coverage=unknown('未建立全部法规的同范围官方分母及完整身份对账'),
                     attachment_coverage=unknown('未建立全部应收附件清单及逐项完整性验收'),
                     historical_version_coverage=unknown('未建立全部历史版本清单及逐版验收')))


def _pct(metric):
    return '未知' if metric.get('ratio') is None else f"{metric['ratio']*100:.2f}%"


DIMENSIONS=(('discovery','目录'),('pagination','分页'),('originals','原文'),('attachments','附件'),('versions','版本'),('cutoff','截点'))
STATUS_LABELS={'implemented':'已实现','partial':'部分','unknown':'未知','not_supported':'未支持'}


def _cap_label(static,key):
    value=static.get('capabilities',{}).get(key,{})
    return STATUS_LABELS.get(value.get('status'),'未知')


def write(jurisdiction,output,registry_root=None,census_root=None,batch=None):
    output=Path(output)
    if output.exists():raise ValueError('Use a new matrix output directory; historical reports are immutable')
    result=build(jurisdiction,registry_root,census_root,batch)
    output.mkdir(parents=True,exist_ok=False)
    (output/'matrix.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    title=result['scope']['jurisdiction']+' 覆盖率能力矩阵'
    warnings=[]
    for label,key in (('目录','census_index_binding'),('下载队列','batch_index_binding')):
        binding=result['scope'].get(key)
        if binding and binding['status']!='matched':warnings.append(label+'索引绑定：'+('当前索引已改变，不能据历史范围宣称当前索引覆盖' if binding['drift'] else '历史计划未绑定索引哈希，范围可比性未证明'))
    if any(not b['hash_bound'] for b in result['scope'].get('batch_census_source_bindings',[])):warnings.append('部分历史来源未绑定目录数据库/子计划哈希；队列自身哈希已核验，目录与队列的同期可比性未证明')
    warning_text='；'.join(warnings) or '索引哈希绑定无已知冲突；实际覆盖仍须逐维度证据。'
    q=result['queue'];qtext='未接入下载队列' if q is None else f"冻结任务 {q['total_tasks']:,}，完成 {q['status_counts'].get('completed',0):,}；任务完成率 {_pct(q['queue_completion'])}（不是法规覆盖率）"
    columns=['平台 / 索引入口']+[label for _,label in DIMENSIONS]+['实际观测（来源 / 状态 / 条数 / 时间）','目录覆盖率','缺口及下一步']
    lines=['# '+title,'',result['metric_policy'],'',qtext,'',warning_text,'','全部法规覆盖率：未知；附件完整率：未知；历史版本覆盖率：未知。','', '| '+' | '.join(columns)+' |','| '+' | '.join('---' for _ in columns)+' |']
    html_rows=[]
    for row in result['platforms']:
        sources=row['observed_sources']
        observed='；'.join(f"{o['source']} / {o['status']} / {o['stored_catalogue_records'] if o['stored_catalogue_records'] is not None else '未知'}条 / {o['updated_at'] or '时间未记录'}" for o in sources) or '无本次证据'
        rates='；'.join(o['source']+': '+_pct(o['catalogue_coverage']) for o in sources) or '未知'
        gaps=row['unresolved']+[o['catalogue_coverage'].get('reason','') for o in sources]+[str(b) for o in sources for b in o['blockers']]
        gaptext='；'.join(gaps)+'。下一步：'+'；'.join(row['next_actions'])
        hosttext=row['platform']+'（'+('、'.join(row['indexed_hosts']) if row['indexed_hosts'] else '未匹配索引')+'）'
        cells=[hosttext]+[_cap_label(row['static_capabilities'],key) for key,_ in DIMENSIONS]+[observed,rates,gaptext]
        lines.append('| '+' | '.join(str(c).replace('|','\\|').replace('\n',' ') for c in cells)+' |')
        html_rows.append('<tr>'+''.join('<td>'+html.escape(str(c))+'</td>' for c in cells)+'</tr>')
    lines+=['','## 证据与边界','', '- 同一平台的索引域名合并显示，但全部indexed_hosts和逐来源证据保留；补充来源的计数与分母不相加。','- 能力声明包含实现入口与源码哈希，不能代替当前站点可达性验收。','- 状态中的总量默认不与详情URL计数相除，只有同范围、同单位且独立审核及哈希绑定的证据才计算目录比率。','- 索引漂移、状态时间、逐来源日期分组及原始状态证据见 matrix.json。','- 队列统计来自只读事务，先核对冻结内容哈希；对象数独立于任务与法规数量。','- 本报告只读已有快照，生成时间不代表刚访问官网。','']
    (output/'matrix.md').write_text('\n'.join(lines))
    headers=''.join('<th>'+html.escape(c)+'</th>' for c in columns)
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>'+html.escape(title)+'</title><style>body{font:15px/1.6 system-ui;margin:32px;color:#233247}h1{color:#173f68}.note{background:#fff3d4;padding:16px}input{width:350px;padding:10px;margin:16px 0}.table{overflow:auto}table{border-collapse:collapse;width:100%}th,td{border:1px solid #dce2e8;padding:10px;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#edf3f8;position:sticky;top:0}td:first-child{min-width:180px}td:nth-child(n+8){min-width:240px}a{color:#2163a5}</style><h1>'+html.escape(title)+'</h1><p>'+html.escape(qtext)+'</p><div class="note">'+html.escape(warning_text)+'<br>全部法规、附件完整性、历史版本覆盖率均未知。静态能力不是线上验收；任务完成率不是法规覆盖率。本报告只读已有快照，生成时间不代表刚访问官网。</div><input id="search" aria-label="检索矩阵" placeholder="搜索平台、能力或缺口"><div class="table"><table><thead><tr>'+headers+'</tr></thead><tbody>'+''.join(html_rows)+'</tbody></table></div><p><a href="matrix.json">完整证据 JSON</a> · <a href="matrix.md">Markdown 报告</a></p><script>document.getElementById("search").addEventListener("input",function(){const q=this.value.toLowerCase();document.querySelectorAll("tbody tr").forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q));});</script></html>'
    (output/'matrix.html').write_text(page)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--jurisdiction',required=True);p.add_argument('--registry-root');p.add_argument('--census-root');p.add_argument('--batch');p.add_argument('--output',required=True)
    a=p.parse_args(argv);result=write(a.jurisdiction,a.output,a.registry_root,a.census_root,a.batch)
    print(json.dumps(dict(platform_rows=len(result['platforms']),queue=result['queue']['total_tasks'] if result['queue'] else None,report=str(Path(a.output)/'matrix.html')),ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
