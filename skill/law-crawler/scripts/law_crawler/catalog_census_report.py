"""只读汇总目录普查；区分目录记录、法规身份与不可确认的全版本总数。"""
import argparse
from collections import Counter
from datetime import datetime, timezone, date
import html
import json
from pathlib import Path
import re
import sqlite3

PLATFORM_LABELS = dict([('peraturan.go.id','国家法规门户'),('bpk.go.id','BPK'),('jdihn.go.id','JDIHN'),('dpr','DPR'),('ma','最高法院'),('mk','宪法法院'),('ojk.go.id','金融监管局中央列表'),('kemendag.go.id','贸易部'),('kemkes.go.id','卫生部'),('kemenkeu.go.id','财政部'),('bkpm.go.id','投资部')])


def report_sources(root, plan):
    """Preserve planned but unobserved sources; include separately frozen supplements."""
    aliases={'dpr.go.id':'dpr','mahkamahagung.go.id':'ma','mkri.id':'mk'}
    names=[]
    declared=plan.get('sources') or plan.get('platforms') or []
    if isinstance(declared,dict):declared=list(declared)
    for item in declared:
        name=(item.get('source') or item.get('platform')) if isinstance(item,dict) else item
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+',name) or name in ('.','..'):
            raise ValueError('Invalid catalogue source name')
        name=aliases.get(name,name)
        if name not in names:names.append(name)
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.is_symlink() and ((child/'plan.json').is_file() or (not declared and (child/'records.sqlite3').is_file())):
            if child.name not in names:names.append(child.name)
    return [(name,PLATFORM_LABELS.get(name,name)) for name in names]

def cutoff_bucket(row, cutoff):
    for key in ('promulgated_at','published_at'):
        raw=row.get(key)
        if isinstance(raw,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',raw):
            try: day=date.fromisoformat(raw)
            except ValueError: continue
            return ('date_on_or_before_cutoff' if day<=date.fromisoformat(cutoff) else 'date_after_cutoff')
    year=str(row.get('year') or '')
    if re.fullmatch(r'\d{4}',year):
        if year<cutoff[:4]:return 'year_before_cutoff_exact_date_unknown'
        if year>cutoff[:4]:return 'year_after_cutoff'
    return 'cutoff_unknown'

def scope(row):
    if row.get('scope_status'):return row['scope_status']
    kind=row.get('classification') or row.get('document_type') or ''
    if kind=='planning':return 'excluded_legislative_plan'
    if kind in ('draft','legislative_plan','judgment'):return 'excluded_'+kind
    if kind in ('administrative_decision','decision','pending'):return 'pending_normative_or_administrative'
    if kind in ('regulation','regulation_body','judicial_guidance'):return 'candidate'
    return 'pending_type_verification'

def identity(row):
    # 仅采用详情明确给出的完整身份；跨语言/简称归一化尚未核验，不作推测合并。
    value=row.get('identity')
    if row.get('identity_status') not in ('observed','confirmed') or not isinstance(value,dict):return None
    fields=tuple(str(value.get(k) or '').strip().casefold() for k in ('jurisdiction','issuer','type','number','year'))
    return fields if all(fields) else None

def build(root):
    root=Path(root);plan=json.loads((root/'plan.json').read_text());cutoff=plan['cutoff']
    platforms=[];urls=set();identities=set();candidate_identities=set();identified_rows=0;candidate_identity_rows=0;all_dates=Counter();candidate_dates=Counter()
    sources=report_sources(root,plan)
    for folder,name in sources:
        path=root/folder;status=json.loads((path/'status.json').read_text()) if (path/'status.json').exists() else {}
        dates=Counter();kinds=Counter();record_count=0;variant_count=0
        db=path/'records.sqlite3'
        if db.exists():
            connection=sqlite3.connect(f'file:{db}?mode=ro',uri=True)
            for (raw,) in connection.execute('SELECT data FROM records'):
                row=json.loads(raw)
                if row.get('source_environment')=='development':continue
                record_count+=1;urls.add(row['url']);bucket=cutoff_bucket(row,cutoff);dates[bucket]+=1;all_dates[bucket]+=1;kinds[scope(row)]+=1
                if scope(row)=='candidate':candidate_dates[bucket]+=1
                variant_count+=len(row.get('catalogue_variants') or [row])
                key=identity(row)
                if key:
                    identities.add(key);identified_rows+=1
                    if scope(row)=='candidate':candidate_identities.add(key);candidate_identity_rows+=1
            connection.close()
        pages=status.get('pages')
        pages_attempted=len({v.get('url') for v in pages if isinstance(v,dict) and v.get('url')}) if isinstance(pages,list) else pages
        if isinstance(pages,list):
            pages=len({v.get('url') for v in pages if isinstance(v,dict) and v.get('status') in ('parsed','success','ok') and v.get('url')})
        if status.get('production_discovered_records')==0 and status.get('experimental_records'):pages=0
        unlinked=status.get('unlinked_catalogue_rows',0)
        if isinstance(unlinked,list):unlinked=len(unlinked)
        failures=Counter(str(x.get('status') or x.get('error') or 'unknown') for x in status.get('failures',[]) if isinstance(x,dict))
        platforms.append(dict(platform=folder,name=name,status=status.get('status','not_started'),catalogue_complete=status.get('catalogue_complete',False),all_versions_complete=status.get('all_versions_complete',False),observed_catalogue_total=status.get('observed_total') if isinstance(status.get('observed_total'),int) else None,observed_catalogue_rows=status.get('observed_catalogue_rows',status.get('catalogue_entries_seen')),stored_detail_urls=record_count,preserved_metadata_variants=variant_count,pages=pages,pages_attempted=pages_attempted,unlinked_rows=unlinked,cutoff_counts=dict(dates),scope_counts=dict(kinds),failure_counts=dict(failures),limitations=status.get('limitations',[]),reason=status.get('reason') or status.get('error'),updated_at=status.get('updated_at'),experimental_records=status.get('experimental_records',0),evidence=str(path)))
    return dict(jurisdiction=plan.get('jurisdiction'),source_scope='frozen_plan_and_observed_supplements' if plan.get('platforms') or plan.get('sources') else 'observed_sources_only_unconfirmed_scope',generated_at=datetime.now(timezone.utc).isoformat(),cutoff=cutoff,mode='metadata_catalog_only',raw_catalogue_counts_are_before_cutoff_filter=True,cutoff_counts=dict(all_dates),candidate_cutoff_counts=dict(candidate_dates),exact_all_platform_all_version_regulation_total=None,exact_total_reason='目录访问、材料分类、截止日和历史版本尚未全部核验；目录条目不是唯一法规。',catalogue_records=sum(p['stored_detail_urls'] for p in platforms),distinct_detail_urls=len(urls),observed_material_identity_groups=len(identities),rows_with_observed_material_identity=identified_rows,candidate_regulation_identity_groups=len(candidate_identities),candidate_regulation_identity_rows=candidate_identity_rows,unresolved_identity_rows=sum(p['stored_detail_urls'] for p in platforms)-identified_rows,platforms=platforms)

def write(root,out):
    result=build(root);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    (out/'census-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    title=f"{result.get('jurisdiction') or '法域未记录'} · {len(result['platforms'])} 个来源目录普查"
    lines=['# '+title, '',f"统计截点：{result['cutoff']}；快照生成：{result['generated_at']}", '', '**全部法规、全部版本的精确总数：仍无法确认。**', '',f"已保存截点筛选前 {result['catalogue_records']:,} 条平台目录记录；按详情URL去重后 {result['distinct_detail_urls']:,} 个地址。该数字包含截止日后、日期不明、跨平台重复和待分类材料，不是法规总量，也不是下载文件数。", '', '| 平台 | 已保存目录记录 | 官方显示总量 | 成功解析目录页 | 该入口目录核对完成 | 状态 |', '|---|---:|---:|---:|---|---|']
    for p in result['platforms']:
        lines.append(f"| {p['name']} | {p['stored_detail_urls']:,} | {p['observed_catalogue_total'] if p['observed_catalogue_total'] is not None else '未知'} | {p['pages'] if p['pages'] is not None else '未知'} | {'是' if p['catalogue_complete'] else '否'} | {p['status']} |")
    lines += ['', '## 统计口径与缺口', '', f"全部目录日期分组：`{json.dumps(result['cutoff_counts'],ensure_ascii=False)}`；法规候选日期分组：`{json.dumps(result['candidate_cutoff_counts'],ensure_ascii=False)}`。", '', '- 本轮仅获取目录与元数据，不下载原文。受阻平台的0条采集结果不代表该平台没有法规。', '- 保留废止、历史及同URL不同元数据记录；没有历史版本清单证据时，不标记全版本完成。', '- 公布/发布日与制定日、网页显示日分别保存；年份早于截点的记录仍单列日期缺失。', '- 草案、立法计划、个案裁判单列；行政决定是否具有规范性待核验。', '- 只合并有完整且明确法规身份的记录；剩余记录不根据标题相似度强行去重。', f"- 当前仅 {result['rows_with_observed_material_identity']:,} 条记录有明确完整材料身份（包含待核验决定及未分类材料），形成 {result['observed_material_identity_groups']:,} 个材料身份组；其余 {result['unresolved_identity_rows']:,} 条身份未完成对账。法规候选材料中仅 {result['candidate_regulation_identity_rows']:,} 条有完整身份，形成 {result['candidate_regulation_identity_groups']:,} 组；这也不是全库法规总量。", '']
    for p in result['platforms']:
        lines += [f"### {p['name']}",'',f"日期分组：`{json.dumps(p['cutoff_counts'],ensure_ascii=False)}`",'',f"范围分类：`{json.dumps(p['scope_counts'],ensure_ascii=False)}`",'',f"请求过的不同目录页：{p['pages_attempted']}；实际遍历目录项：{p['observed_catalogue_rows'] if p['observed_catalogue_rows'] is not None else '未单独核对'}；无详情链接目录行：{p['unlinked_rows']}；保留元数据变体：{p['preserved_metadata_variants']}", '',f"请求失败：`{json.dumps(p['failure_counts'],ensure_ascii=False)}`；完整性限制：`{json.dumps(p['reason'] or p['limitations'],ensure_ascii=False)}`",'',f"开发/实验环境另存 {p['experimental_records']} 条，不计入生产目录；其他官方入口与全部历史版本仍须独立核验。",'',f"证据目录：`{p['evidence']}`",'']
    md='\n'.join(lines)+'\n';(out/'census-report.md').write_text(md)
    table=''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in [p['name'],format(p['stored_detail_urls'],','),p['observed_catalogue_total'] if p['observed_catalogue_total'] is not None else '未知',p['pages'] if p['pages'] is not None else '未知','已核对' if p['catalogue_complete'] else '未完成',p['status']])+'</tr>' for p in result['platforms'])
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>法规目录普查</title><style>body{max-width:1100px;margin:40px auto;padding:0 24px;font:16px/1.7 system-ui;color:#253343;background:#f8fafc}table{width:100%;border-collapse:collapse;background:white}th,td{padding:12px;border-bottom:1px solid #ddd;text-align:left}.note{background:#fff3d9;padding:20px;border-left:5px solid #b67812}pre{white-space:pre-wrap;font:14px/1.7 system-ui}h1{color:#8c2028}</style><h1>法规目录普查</h1>'''
    page=page.replace("<h1>法规目录普查</h1>","<h1>"+html.escape(title)+"</h1>")
    page+=f"<p>截至 {html.escape(result['cutoff'])} · 快照 {html.escape(result['generated_at'])}</p><div class='note'><strong>全法规、全版本精确总量：未知</strong><br>已保存截点筛选前 {result['catalogue_records']:,} 条截点筛选前目录记录，含截止日后与日期不明材料。目录条数不等于唯一法规数；未完成的平台不能记作零部法规。</div><h2>平台进度</h2><table><tr><th>平台</th><th>目录记录</th><th>官方显示总量</th><th>成功解析页数</th><th>该入口完整性</th><th>任务状态</th></tr>{table}</table><h2>口径及审计明细</h2><pre>{html.escape(md.split('## 统计口径与缺口',1)[1])}</pre></html>"
    (out/'census-report.html').write_text(page)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--out',required=True);args=parser.parse_args()
    report=write(args.root,args.out);print(json.dumps({k:v for k,v in report.items() if k!='platforms'},ensure_ascii=False))
