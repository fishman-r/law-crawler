"""冻结目录 -> 有界原件下载 -> OSS 核验；SQLite断点，五路临时缓冲。"""
import argparse,concurrent.futures,hashlib,html,json,math,shutil,signal,sqlite3,stat,threading,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
from law_crawler.raw_capture import capture,file_sha256,_url
from law_crawler.bulk_download_fast import HostPace,file_reference_key
from law_crawler.file_lock import FileLock
from law_crawler.platform_adapters.census import read_catalog_document,allowed_hosts
from law_crawler.oss_sink import OSSSink
from law_crawler.storage_config import validate_storage_config
from law_crawler.jurisdiction_registry import resolve_jurisdiction
from law_crawler.resources import skill_directory
from law_crawler.platform_adapters import ADAPTERS

SOURCE_ALIASES={'dpr':'dpr.go.id','ma':'mahkamahagung.go.id','mk':'mkri.id','ojk.go.id-jdih-supplement':'ojk.go.id'}
def now():return datetime.now(timezone.utc).isoformat()
def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def atomic(path,value):
 p=Path(path);t=p.with_suffix('.tmp');t.write_text(json.dumps(value,ensure_ascii=False,indent=2));t.replace(p)
def urlkey(url):return json.dumps(file_reference_key(_url(url)),ensure_ascii=False)
def valid(receipt):return receipt.get('capture_status') in ('saved','reused') and receipt.get('complete') and 200<=int(receipt.get('status_code') or 0)<300

def connect(path):
 c=sqlite3.connect(path,timeout=60);c.execute('pragma journal_mode=WAL');return c

def prepare_retry(db,out,status,run_id,platform=None):
 """冻结指定失败分区并保留旧结果；服务重启不能再次重置已尝试任务。"""
 import re
 if status not in ('no_original','incomplete','failed') or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}',run_id or ''):
  raise ValueError('定向重跑需要合法状态及唯一retry-run-id')
 db.execute('CREATE TABLE IF NOT EXISTS retry_runs(id TEXT PRIMARY KEY,manifest_sha TEXT,created_at TEXT)');db.commit()
 folder=out/'retries'/run_id;folder.mkdir(parents=True,exist_ok=True)
 manifest=folder/'plan.json'
 prior=db.execute('select manifest_sha from retry_runs where id=?',(run_id,)).fetchone()
 if prior:
  if not manifest.exists() or file_sha256(manifest)!=prior[0]:raise ValueError('重跑计划校验失败')
  plan=json.loads(manifest.read_text())
  if plan['status']!=status or plan['platform']!=platform:raise ValueError('重跑参数不能改变')
  return plan
 if manifest.exists():
  plan=json.loads(manifest.read_text())
  if plan['status']!=status or plan['platform']!=platform:raise ValueError('重跑参数不能改变')
 else:
  rows=db.execute('select id,status,reason,updated_at from jobs where status=?'+(' and platform=?' if platform else ''),[status]+([platform] if platform else [])).fetchall()
  plan=dict(run_id=run_id,status=status,platform=platform,created_at=now(),jobs=rows,source_sha=file_sha256(__file__))
  atomic(manifest,plan)
 history=folder/'outcomes';history.mkdir(exist_ok=True)
 for key,*unused in plan['jobs']:
  source=out/'outcomes'/(key+'.json');target=history/source.name
  if source.exists() and not target.exists():shutil.copy2(source,target)
 with db:
  db.executemany('update jobs set status="pending" where id=? and status=?',[(row[0],status) for row in plan['jobs']])
  db.execute('insert into retry_runs values(?,?,?)',(run_id,file_sha256(manifest),now()))
 return plan

def _arg(args,name,default=None):
 return getattr(args,name,default)

def configuration(args,out):
 """Resolve explicit new inputs or immutable legacy plan before creating files."""
 out=Path(out);prior=None
 if (out/'plan.json').exists():
  prior=json.loads((out/'plan.json').read_text())
  if file_sha256(out/'plan.json')!=(out/'plan.sha256').read_text().strip():raise ValueError('计划SHA不符')
 root=Path(_arg(args,'registry_root') or skill_directory()).expanduser().resolve()
 requested=_arg(args,'jurisdiction') or (prior or {}).get('jurisdiction')
 selection=resolve_jurisdiction(root,requested)
 if prior and selection.name!=prior['jurisdiction']:raise ValueError('冻结计划法域不能改变')
 args.jurisdiction=selection.name
 for key in ('bucket','endpoint','region','prefix','census_root'):
  value=_arg(args,key)
  if prior:
   if value is not None and str(value)!=str(prior[key]):raise ValueError('冻结计划参数不能改变: '+key)
   value=prior[key]
  if not isinstance(value,str) or not value.strip():raise ValueError('新批次必须显式提供 --'+key.replace('_','-'))
  setattr(args,key,value)
 storage=validate_storage_config({key:getattr(args,key) for key in ('bucket','endpoint','region','prefix')})
 if not prior:
  for key,value in storage.items():setattr(args,key,value)
  args.census_root=str(Path(args.census_root).expanduser().resolve())
 if prior:
  if _arg(args,'expected') is not None and args.expected!=prior['expected']:raise ValueError('冻结计划数量不能改变')
  args.expected=prior['expected']
  if _arg(args,'old_metadata') is not None and file_sha256(args.old_metadata)!=prior.get('old_metadata_sha256'):raise ValueError('冻结旧缓存参数不能改变')
 defaults={'workers':5,'max_attempts':3,'max_file_bytes':512*1024*1024,'min_free_bytes':5*1024**3,'detail_max_bytes':12*1024*1024,'timeout':30,'backoff_base':1,'host_delay':.25}
 for key,default in defaults.items():
  value=_arg(args,key)
  if prior and key in prior:
   if value is not None and value!=prior[key]:raise ValueError('冻结执行参数不能改变: '+key)
   value=prior[key]
  elif value is None:value=default
  if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<(0 if key in ('backoff_base','host_delay','min_free_bytes') else 1):raise ValueError('执行参数无效: '+key)
  if key in ('workers','max_attempts','max_file_bytes','min_free_bytes','detail_max_bytes') and not isinstance(value,int):raise ValueError('执行参数须整数: '+key)
  setattr(args,key,value)
 if not 1<=args.workers<=5:raise ValueError('workers须为1..5')
 if _arg(args,'expected') is not None and (type(args.expected) is not int or args.expected<1):raise ValueError('expected必须为正整数')
 if prior:
  if _arg(args,'sources') is not None and sorted(args.sources)!=sorted(s['source'] for s in prior['sources']):raise ValueError('冻结来源不能改变')
  return selection,prior,[]
 root=Path(args.census_root).expanduser().resolve()
 names=_arg(args,'sources')
 if names is None:
  top=root/'plan.json'
  if not top.is_file():raise ValueError('新批次须提供--source或冻结顶层目录plan.json的sources/platforms')
  top_plan=json.loads(top.read_text());declared=top_plan.get('sources',top_plan.get('platforms'))
  if not isinstance(declared,list) or not declared:raise ValueError('顶层目录计划缺少来源清单')
  names=[]
  for value in declared:
   name=value if isinstance(value,str) else value.get('source',value.get('platform')) if isinstance(value,dict) else None
   if not isinstance(name,str):raise ValueError('顶层来源清单格式无效')
   if not (root/name).is_dir():
    aliases=[alias for alias,platform in SOURCE_ALIASES.items() if platform==name and (root/alias).is_dir()]
    if len(aliases)==1:name=aliases[0]
   names.append(name)
 if not names or len(set(names))!=len(names):raise ValueError('目录来源为空或重复')
 sources=[];total=0;seen_urls=set()
 for name in names:
  if Path(name).name!=name or name in ('.','..'):raise ValueError('目录来源名无效')
  folder=root/name;path=folder/'records.sqlite3';plan_path=folder/'plan.json'
  if folder.is_symlink() or path.is_symlink() or not path.is_file() or not plan_path.is_file():raise ValueError('来源缺少冻结plan.json/records.sqlite3: '+name)
  source_plan=json.loads(plan_path.read_text());raw_platform=source_plan.get('platform')
  if not isinstance(raw_platform,str):raise ValueError('来源计划缺少platform')
  canonical=SOURCE_ALIASES.get(raw_platform,raw_platform)
  adapter=ADAPTERS.get(canonical)
  if adapter is None:raise ValueError('目录平台尚未适配: '+raw_platform)
  if adapter.country not in selection.countries:raise ValueError('目录来源与法域不一致: '+raw_platform)
  index_hosts=set(selection.platforms)
  if canonical not in index_hosts and not index_hosts.intersection(h.removeprefix('www.') for h in adapter.allowed_hosts):raise ValueError('目录来源未在法域索引登记: '+raw_platform)
  platform=raw_platform if raw_platform=='ojk.go.id-jdih-supplement' else canonical
  hosts=allowed_hosts(platform)
  count=0
  with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as src:
   for raw, in src.execute('select data from records'):
    r=json.loads(raw)
    if r.get('source_environment')=='development':continue
    url=r.get('url');parsed=urlsplit(url) if isinstance(url,str) else None
    if parsed is None or parsed.scheme not in ('https','http') or parsed.hostname not in hosts or parsed.username or parsed.password:raise ValueError('目录URL不是已登记官方地址')
    if url in seen_urls:raise ValueError('所选来源有重复详情URL，须先明确目录记录合并规则')
    seen_urls.add(url);count+=1
  total+=count;sources.append(dict(source=name,platform=platform,rows=count,plan_sha256=file_sha256(plan_path),records_sha256=file_sha256(path),cutoff=source_plan.get('cutoff')))
 if total==0:raise ValueError('所选目录没有可下载记录')
 if _arg(args,'expected') is not None and total!=args.expected:raise ValueError(f'队列数量{total}不等于授权断言{args.expected}')
 args.expected=total
 if _arg(args,'old_metadata') is not None:
  old=json.loads(Path(args.old_metadata).read_text())
  if not isinstance(old.get('documents'),(dict,list)):raise ValueError('旧缓存缺少documents')
 return selection,None,sources

def freeze(args,out):
 selection,prior,sources=configuration(args,out)
 out=Path(out);out.mkdir(parents=True,exist_ok=True)
 db=connect(out/'queue.sqlite3')
 db.executescript('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,url TEXT UNIQUE,platform TEXT,record TEXT,status TEXT DEFAULT "pending",reason TEXT,files INTEGER DEFAULT 0,bytes INTEGER DEFAULT 0,updated_at TEXT); CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status); CREATE TABLE IF NOT EXISTS cache(url TEXT PRIMARY KEY,receipt TEXT); CREATE TABLE IF NOT EXISTS objects(sha TEXT PRIMARY KEY,bytes INTEGER,key TEXT);')
 if prior:
  if db.execute('select count(*) from jobs').fetchone()[0]!=prior['expected']:raise ValueError('冻结队列数量变化')
  h=hashlib.sha256()
  for row in db.execute('select id,platform,record from jobs order by id'):h.update(json.dumps(row,ensure_ascii=False).encode())
  if h.hexdigest()!=prior['queue_sha256']:raise ValueError('冻结队列内容变化')
  return db,prior
 if db.execute('select count(*) from jobs').fetchone()[0]:raise ValueError('未完成冻结，请检查临时队列而非覆盖')
 for source in sources:
  path=Path(args.census_root)/source['source']/'records.sqlite3'
  if file_sha256(path)!=source['records_sha256'] or file_sha256(path.parent/'plan.json')!=source['plan_sha256']:raise ValueError('冻结前来源目录变化')
  with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as src:
   for raw, in src.execute('select data from records'):
    r=json.loads(raw)
    if r.get('source_environment')=='development':continue
    u=r['url'];db.execute('insert into jobs(id,url,platform,record) values(?,?,?,?)',(digest(u),u,source['platform'],json.dumps(r,ensure_ascii=False)))
 if db.execute('select count(*) from jobs').fetchone()[0]!=args.expected:raise ValueError('冻结目录数量变化')
 imported=0
 if _arg(args,'old_metadata'):
  old=json.loads(Path(args.old_metadata).read_text())
  for doc in (old['documents'].values() if isinstance(old['documents'],dict) else old['documents']):
   for receipt in doc.get('files',{}).values():
    if valid(receipt):db.execute('insert or replace into cache values(?,?)',(urlkey(receipt['url']),json.dumps(receipt,ensure_ascii=False)));imported+=1
 db.commit();h=hashlib.sha256()
 for row in db.execute('select id,platform,record from jobs order by id'):h.update(json.dumps(row,ensure_ascii=False).encode())
 plan={'created_at':now(),'batch_id':out.name,'jurisdiction':selection.name,'registry_index':str(selection.index.resolve()),'registry_index_sha256':file_sha256(selection.index),'mode':'raw_download_and_oss_only','expected':args.expected,'queue_sha256':h.hexdigest(),'census_root':str(Path(args.census_root).resolve()),'cutoff_scope':'raw_catalogue_records_before_date_filter_including_unknown_and_after_cutoff','sources':sources,'old_metadata_sha256':file_sha256(args.old_metadata) if _arg(args,'old_metadata') else None,'cached_successful_references':imported,'cleanup':'only_this_batch_staging_after_all_bytes_and_metadata_verified_in_oss; existing_raw_untouched'}
 for key in ('bucket','endpoint','region','prefix','workers','max_attempts','max_file_bytes','min_free_bytes','detail_max_bytes','timeout','backoff_base','host_delay'):plan[key]=getattr(args,key)
 atomic(out/'plan.json',plan);(out/'plan.sha256').write_text(file_sha256(out/'plan.json')+'\n');return db,plan

class Runner:
 def __init__(self,args,out,db,plan):
  self.args,self.out,self.db,self.plan=args,out,db,plan;self.stop=threading.Event();self.lock=threading.RLock();self.url_locks={};self.started=time.monotonic();self.pause_reason=None
  self.sink=OSSSink(credentials=args.credentials,cache_path=out/'oss-cache.json',bucket=args.bucket,endpoint=args.endpoint,region=args.region,prefix=args.prefix)
  self.pace=HostPace(args.host_delay,self.stop);self.last_report=0
 def per_url_lock(self,key):
  with self.lock:return self.url_locks.setdefault(key,threading.Lock())
 def cached(self,db,url):
  r=db.execute('select receipt from cache where url=?',(urlkey(url),)).fetchone();return json.loads(r[0]) if r else None
 def file(self,db,url,stage,hosts):
  key=urlkey(url)
  with self.per_url_lock(key):
   prior=self.cached(db,url)
   if prior and prior.get('oss'):
    result=self.sink.verify_original({**prior,'oss_raw_key':prior['oss']['raw_key']})
    if result['status']!='failed':return {**prior,'oss':result,'capture_status':'reused'}
    if self.sink.auth_failure:self.stop.set();self.pause_reason='OSS授权失败';return {**prior,'oss':result}
   if prior and Path(prior.get('path','')).is_file() and file_sha256(prior['path'])==prior.get('sha256'):receipt=prior
   else:receipt=self.request(url,stage,hosts,body=False)
   if valid(receipt):
    result=self.sink.put_original(receipt);receipt={**receipt,'oss':result}
    if result['status']!='failed':
     db.execute('insert or replace into cache values(?,?)',(key,json.dumps(receipt,ensure_ascii=False)));db.commit()
    elif self.sink.auth_failure:self.pause_reason='OSS授权失败';self.stop.set()
   return receipt
 def request(self,url,stage,hosts,body=True):
  if urlsplit(url).hostname not in hosts:raise ValueError('非登记官方主机')
  if shutil.disk_usage(self.out).free<self.plan['min_free_bytes']:
   self.pause_reason='磁盘剩余低于冻结阈值'+str(self.plan['min_free_bytes'])+'字节';self.stop.set();raise InterruptedError(self.pause_reason)
  receipt=self.pace.capture(url,stage,timeout=self.args.timeout,max_attempts=self.plan['max_attempts'],max_bytes=(self.args.detail_max_bytes if body else self.plan['max_file_bytes']),backoff_base=self.args.backoff_base)
  if urlsplit(receipt.get('final_url') or url).hostname not in hosts:receipt={**receipt,'capture_status':'failed','reason':'重定向主机未登记'}
  if not body and valid(receipt):
   path=Path(receipt['path'])
   with path.open('rb') as stream:header=stream.read(4096)
   expects_pdf=urlsplit(url).path.lower().endswith('.pdf') or 'application/pdf' in receipt.get('headers',{}).get('content-type','').lower()
   if expects_pdf and not header.startswith(b'%PDF-'):
    if header.startswith(b'--') and b'content-disposition:' in header.lower() and b'%PDF-' in header:
     receipt={**receipt,'original_format_status':'wrapped_pdf_pending_review'}
    else:receipt={**receipt,'capture_status':'failed','reason':'PDF链接返回非PDF内容，原始响应保留待核验'}
   if path.suffix=='.html':
    with path.open('rb') as stream:sample=stream.read(20000).lower()
    if any(token in sample for token in (b'access denied',b'captcha',b'403 forbidden',b'verify you are human',b'404 not found',b'just a moment',b'cloudflare',b'<title>login')):receipt={**receipt,'capture_status':'failed','reason':'HTTP200访问挑战或错误页，不能作为原件'}
  if body:
   if not valid(receipt):raise ValueError('详情请求失败:'+str(receipt.get('reason'))[:250])
   return Path(receipt['path']).read_bytes(),receipt.get('final_url') or url
  return receipt
 def process(self,row):
  key,url,platform,raw=row;db=connect(self.out/'queue.sqlite3');stage=self.out/'staging'/key;record=json.loads(raw);began=time.monotonic()
  result={'detail_key':key,'detail_url':url,'platform':platform,'catalogue_record':record,'files':[],'status':'failed','started_at':now()}
  try:
   hosts=allowed_hosts(platform)
   adapter=ADAPTERS[SOURCE_ALIASES.get(platform,platform)]
   fetch=lambda u:self.request(u,stage,hosts)
   parsed=read_catalog_document(platform,record,fetch) if adapter.country=='Indonesia' else adapter.read_document(url,fetch)
   result['metadata']=parsed.metadata
   for link in dict.fromkeys(parsed.file_urls):
    if self.stop.is_set():raise InterruptedError('任务暂停')
    try:receipt=self.file(db,link,stage,hosts)
    except Exception as exc:receipt={'url':link,'capture_status':'failed','reason':type(exc).__name__+':'+str(exc)[:250]}
    result['files'].append(receipt)
   okay=bool(result['files']) and not parsed.metadata.get('primary_original_missing') and all(valid(r) and r.get('oss',{}).get('status') in ('uploaded_verified','reused_verified') for r in result['files'])
   result.update(status='completed' if okay else 'no_original' if not result['files'] else 'incomplete',reason='已发现原件及来源元数据归档；全部版本/附件仍未独立证明' if okay else parsed.metadata.get('no_original_reason','未发现原件') if not result['files'] else '部分下载或OSS保存失败')
  except Exception as exc:result.update(status='pending' if self.stop.is_set() else 'failed',reason=type(exc).__name__+':'+str(exc)[:250])
  result['duration_seconds']=time.monotonic()-began;result['finished_at']=now()
  meta=self.sink.put_metadata(key,result,batch_id=self.out.name);result['oss_metadata']=meta
  if meta['status']=='failed':
   result['status']='pending' if self.sink.auth_failure else 'incomplete';result['reason']='OSS元数据归档失败:'+meta.get('reason','')
   if self.sink.auth_failure:self.pause_reason='OSS授权失败';self.stop.set()
  (self.out/'outcomes').mkdir(exist_ok=True);atomic(self.out/'outcomes'/(key+'.json'),result)
  # 新临时原件只在相应OSS对象和元数据均已回读核验后释放；旧raw路径不在此目录。
  db.close()
  if meta['status']!='failed':
   try:
    for r in result['files']:
     if r.get('oss',{}).get('status') not in ('uploaded_verified','reused_verified') or not isinstance(r.get('path'),str) or not r['path']:continue
     path=Path(r['path']).resolve()
     if stage.resolve() in path.parents and path.is_file():path.unlink()
    if result['status']=='completed' and stage.exists():shutil.rmtree(stage)
   except OSError:result['cleanup_warning']='已归档，临时文件清理待重试'
  return result
 def report(self,status,force=False):
  if not force and time.monotonic()-self.last_report<10:return
  counts=dict(self.db.execute('select status,count(*) from jobs group by status'));files,size=self.db.execute('select count(*),coalesce(sum(bytes),0) from objects').fetchone()
  result={'batch_id':self.out.name,'status':status,'reason':self.pause_reason,'updated_at':now(),'started_at':self.plan['created_at'],'total':self.plan['expected'],'pending':counts.get('pending',0),'downloading':counts.get('running',0),'completed':counts.get('completed',0),'failed':counts.get('failed',0),'incomplete':counts.get('incomplete',0),'no_original':counts.get('no_original',0),'attempted':sum(v for k,v in counts.items() if k not in ('pending','running')),'unique_oss_files':files,'oss_bytes':size,'workers':self.args.workers,'elapsed_this_run_seconds':time.monotonic()-self.started,'scope':'详情记录；不是唯一法规数或全部版本完成数','bucket':self.args.bucket,'prefix':self.args.prefix,'platforms':[dict(platform=p,status=s,count=n) for p,s,n in self.db.execute('select platform,status,count(*) from jobs group by platform,status')]}
  atomic(self.out/'status.json',result)
  md='# '+self.plan['jurisdiction']+' 目录原件下载与OSS归档\n\n'+ '\n'.join(f'- {k}: {v}' for k,v in result.items() if k!='platforms')+'\n';(self.out/'report.md').write_text(md)
  (self.out/'report.html').write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="30"><title>目录原件下载与OSS归档</title><style>body{font:17px/1.8 system-ui;max-width:1000px;margin:40px auto}pre{white-space:pre-wrap}</style><pre>'+html.escape(md)+'</pre>')
  self.last_report=time.monotonic();print(json.dumps({k:v for k,v in result.items() if k!='platforms'},ensure_ascii=False),flush=True)
 def run(self):
  if getattr(self.args,'retry_status',None):prepare_retry(self.db,self.out,self.args.retry_status,self.args.retry_run_id,self.args.platform)
  self.db.execute('update jobs set status="pending" where status="running"')
  if self.args.retry_failed:self.db.execute('update jobs set status="pending" where status in ("failed","incomplete","no_original")')
  self.db.commit();signal.signal(signal.SIGTERM,lambda *unused:self.stop.set());signal.signal(signal.SIGINT,lambda *unused:self.stop.set())
  processed=0;futures={};self.report('running',True)
  platforms=[r[0] for r in self.db.execute('select distinct platform from jobs where status="pending"')];turn=0
  archive_plan=self.sink.put_metadata('batch-plan',self.plan,batch_id=self.out.name)
  if archive_plan['status']=='failed':
   self.pause_reason='OSS计划归档失败';self.report('paused',True);return
  with concurrent.futures.ThreadPoolExecutor(max_workers=self.args.workers) as pool:
   while not self.stop.is_set() or futures:
    while not self.stop.is_set() and len(futures)<self.args.workers and (not self.args.limit or processed+len(futures)<self.args.limit):
     row=None
     candidates=[self.args.platform] if self.args.platform else platforms
     for unused in candidates:
      platform=candidates[turn%len(candidates)];turn+=1
      row=self.db.execute('select id,url,platform,record from jobs where status="pending" and platform=? order by rowid limit 1',(platform,)).fetchone()
      if row:break
     if not row:break
     self.db.execute('update jobs set status="running",updated_at=? where id=?',(now(),row[0]));self.db.commit();futures[pool.submit(self.process,row)]=row[0]
    if not futures:break
    done,_=concurrent.futures.wait(futures,timeout=2,return_when=concurrent.futures.FIRST_COMPLETED)
    for future in done:
     key=futures.pop(future)
     try:r=future.result()
     except Exception as exc:r={'status':'failed','reason':type(exc).__name__,'files':[]}
     self.db.execute('update jobs set status=?,reason=?,updated_at=? where id=?',(r['status'],r.get('reason'),now(),key))
     for f in r['files']:
      if f.get('oss',{}).get('status') in ('uploaded_verified','reused_verified'):self.db.execute('insert or ignore into objects values(?,?,?)',(f['sha256'],f['size_bytes'],f['oss']['raw_key']))
     self.db.commit();processed+=1
    self.report('running')
  pending=self.db.execute('select count(*) from jobs where status="pending"').fetchone()[0]
  status='paused' if self.stop.is_set() else 'sample_finished' if pending else 'finished_with_gaps' if self.db.execute('select count(*) from jobs where status!="completed"').fetchone()[0] else 'completed'
  self.report(status,True)
  self.sink.flush_cache()
  self.sink.put_metadata('batch-status',json.loads((self.out/'status.json').read_text()),batch_id=self.out.name)

def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--output',required=True);p.add_argument('--jurisdiction');p.add_argument('--registry-root');p.add_argument('--census-root');p.add_argument('--source',dest='sources',action='append');p.add_argument('--old-metadata');p.add_argument('--credentials')
 for name in ('bucket','endpoint','region','prefix'):p.add_argument('--'+name)
 for name in ('expected','workers','max-attempts','max-file-bytes','min-free-bytes','detail-max-bytes'):p.add_argument('--'+name,type=int)
 for name in ('timeout','backoff-base','host-delay'):p.add_argument('--'+name,type=float)
 p.add_argument('--limit',type=int,default=0);p.add_argument('--platform');p.add_argument('--retry-failed',action='store_true');p.add_argument('--freeze-only',action='store_true');p.add_argument('--retry-status',choices=('no_original','failed','incomplete'));p.add_argument('--retry-run-id');a=p.parse_args(argv)
 if a.retry_status and a.retry_failed:raise ValueError('不能混用重跑范围')
 if a.limit<0:raise ValueError('limit须非负')
 if not a.freeze_only:
  if not a.credentials:raise ValueError('执行下载须提供--credentials；freeze-only不需要凭据')
  credential_path=Path(a.credentials).expanduser().resolve()
  a.credentials=str(credential_path)
  info=credential_path.stat()
  if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:raise ValueError('OSS凭据须为权限600或更严格的普通文件')
  credential=json.loads(credential_path.read_text())
  if any(not isinstance(credential.get(k),str) or not credential[k] for k in ('access_key_id','access_key_secret')):raise ValueError('OSS凭据缺少必需字段')
 out=Path(a.output).expanduser().resolve()
 configuration(a,out)  # Fail before creating output/queue or opening any network connection.
 out.mkdir(parents=True,exist_ok=True)
 with FileLock(out/'.lock'):
  db,plan=freeze(a,out)
  try:
   if a.freeze_only:print(json.dumps({'frozen_records':plan['expected'],'sources':plan['sources']},ensure_ascii=False));return
   Runner(a,out,db,plan).run()
  finally:db.close()
if __name__=='__main__':main()
