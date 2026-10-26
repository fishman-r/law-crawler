"""上传成功归档原件及来源元数据；凭据从工程外受限文件读取。"""
import argparse,concurrent.futures,hashlib,json,os,threading,time
from pathlib import Path
from datetime import datetime,timezone
import oss2
from law_crawler.storage_config import validate_storage_config

def sha_file(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for chunk in iter(lambda:f.read(256*1024),b''):h.update(chunk)
 return h.hexdigest()
def now():return datetime.now(timezone.utc).isoformat()
def atomic(path,data):
 p=Path(path);t=p.with_suffix('.tmp');t.write_text(json.dumps(data,ensure_ascii=False,indent=2));t.replace(p)
def main():
 p=argparse.ArgumentParser();p.add_argument('--credentials',required=True);p.add_argument('--metadata',required=True);p.add_argument('--output',required=True);p.add_argument('--bucket',required=True);p.add_argument('--endpoint',required=True);p.add_argument('--region',required=True);p.add_argument('--prefix',required=True);p.add_argument('--workers',type=int,default=5);p.add_argument('--sample',action='store_true');a=p.parse_args()
 destination=validate_storage_config(vars(a));a.prefix=destination['prefix']
 out=Path(a.output);out.mkdir(parents=True,exist_ok=True);credentials=Path(a.credentials)
 if credentials.stat().st_mode&0o077:raise ValueError('凭据文件权限须为600或更严格')
 secret=json.loads(credentials.read_text());local=threading.local()
 def bucket():
  if not hasattr(local,'bucket'):local.bucket=oss2.Bucket(oss2.AuthV4(secret['access_key_id'],secret['access_key_secret']),a.endpoint,a.bucket,region=a.region,connect_timeout=30)
  return local.bucket
 source=Path(a.metadata);data=json.loads(source.read_text());files={};excluded=0
 raw_root=source.parents[2]/'raw'/'objects'
 for doc in data['documents']:
  for record in doc.get('files',{}).values():
   if record.get('capture_status') not in ('saved','reused') or not record.get('complete') or not 200<=int(record.get('status_code') or 0)<300:
    excluded+=1;continue
   path=Path(record['path']).resolve()
   if path.parent!=raw_root.resolve():raise ValueError('原件路径超出批次原始归档目录')
   digest=record['sha256'];entry=files.setdefault(digest,dict(sha256=digest,path=str(path),size_bytes=record['size_bytes'],key=a.prefix.rstrip('/')+'/raw/'+path.name,sources=[]))
   entry['sources'].append(dict(detail_url=doc.get('detail_url'),platform=doc.get('platform'),metadata=doc.get('metadata'),capture=record,identity_verification=doc.get('identity_verification')))
 plan={'created_at':now(),'bucket':a.bucket,'endpoint':a.endpoint,'prefix':a.prefix,'source_metadata_sha256':sha_file(source),'files':[{k:v for k,v in r.items() if k!='sources'} for r in files.values()],'excluded_failed_references':excluded,'workers':a.workers}
 if (out/'plan.json').exists():
  old=json.loads((out/'plan.json').read_text())
  for key in ('bucket','endpoint','prefix','source_metadata_sha256','files'):
   if old[key]!=plan[key]:raise ValueError('上传计划变化，另建批次')
  plan=old
 else:atomic(out/'plan.json',plan)
 results={};start=time.monotonic();batch=out.name
 def verify(key,digest,size):
  b=bucket();head=b.head_object(key)
  if head.content_length!=size or head.headers.get('x-oss-meta-sha256')!=digest:raise ValueError('现有对象大小或SHA元数据冲突')
  remote=b.get_object(key);h=hashlib.sha256()
  try:
   for chunk in iter(lambda:remote.read(256*1024),b''):h.update(chunk)
  finally:remote.close()
  if h.hexdigest()!=digest:raise ValueError('OSS回读SHA256不符')
 def put(key,path=None,content=None):
  if path: digest=sha_file(path);size=Path(path).stat().st_size
  else:digest=hashlib.sha256(content).hexdigest();size=len(content)
  b=bucket();exists=False
  try:b.head_object(key);exists=True
  except oss2.exceptions.NoSuchKey:pass
  if not exists:
   headers={'x-oss-meta-sha256':digest,'x-oss-forbid-overwrite':'true'}
   if path:b.put_object_from_file(key,path,headers=headers)
   else:b.put_object(key,content,headers=headers)
  verify(key,digest,size);return 'reused_verified' if exists else 'uploaded_verified'
 def upload(entry):
  began=time.monotonic();record={'key':entry['key'],'sha256':entry['sha256'],'size_bytes':entry['size_bytes']}
  for attempt in range(1,4):
   try:
    if sha_file(entry['path'])!=entry['sha256']:raise ValueError('本地原件SHA256不符')
    status=put(entry['key'],path=entry['path'])
    metadata=json.dumps(entry,ensure_ascii=False,sort_keys=True).encode()
    mk=a.prefix.rstrip('/')+'/metadata/'+source.parent.name+'/'+entry['sha256']+'.json';put(mk,content=metadata)
    return {**record,'status':status,'metadata_key':mk,'attempts':attempt,'reason':'原件及元数据均完成OSS回读SHA256核验','duration_seconds':time.monotonic()-began}
   except Exception as e:
    # 不输出异常完整请求，避免凭据或签名进入日志。
    reason=type(e).__name__+':'+str(getattr(e,'code',''))
    if isinstance(e,ValueError):reason=str(e)
    if isinstance(e,ValueError) or getattr(e,'status',None) in (401,403) or attempt==3:return {**record,'status':'failed','attempts':attempt,'reason':reason,'duration_seconds':time.monotonic()-began}
    time.sleep(2**(attempt-1))
 items=list(files.values());selected=items[:1] if a.sample else items
 def snapshot(status):
  ok=sum(r['status']!='failed' for r in results.values());failed=len(results)-ok
  result={'status':status,'updated_at':now(),'started_at':plan['created_at'],'total_files':len(items),'attempted':len(results),'success':ok,'failed':failed,'total_bytes':sum(r['size_bytes'] for r in items),'verified_bytes':sum(r['size_bytes'] for r in results.values() if r['status']!='failed'),'workers':a.workers,'duration_seconds':time.monotonic()-start,'results':list(results.values())};atomic(out/'status.json',result);return result
 snapshot('sample_running' if a.sample else 'running')
 with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
  futures=[pool.submit(upload,r) for r in selected]
  for f in concurrent.futures.as_completed(futures):
   r=f.result();results[r['key']]=r;snapshot('running')
   if len(results)%100==0 or r['status']=='failed':print(json.dumps({'attempted':len(results),'last_status':r['status']},ensure_ascii=False),flush=True)
 result=snapshot('sample_verified' if a.sample and all(r['status']!='failed' for r in results.values()) else 'finished_with_gaps' if any(r['status']=='failed' for r in results.values()) else 'completed')
 if not a.sample:
  for name in ('plan.json','status.json'):
   put(a.prefix.rstrip('/')+'/batches/'+batch+'/'+name,path=str(out/name))
 print(json.dumps({k:v for k,v in result.items() if k!='results'},ensure_ascii=False),flush=True)
 if result['failed']:raise SystemExit(1)
if __name__=='__main__':main()
