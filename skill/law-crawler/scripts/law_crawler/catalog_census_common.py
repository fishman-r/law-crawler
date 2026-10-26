"""有界目录调查：独立证据、URL去重记录、可恢复断点；不下载原件。"""
import hashlib,json,sqlite3,time,urllib.request,urllib.error,http.cookiejar,io,os,uuid
from datetime import datetime,timezone,date
from pathlib import Path
from urllib.parse import urlsplit
from .batch_runtime import retry_fetch
from .download_transport import stream_response


def now():return datetime.now(timezone.utc).isoformat()
def atomic(path,data):
    path=Path(path);temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2));temp.replace(path)


def validate_cutoff(value):
    if not isinstance(value,str):raise ValueError('New census requires explicit cutoff YYYY-MM-DD')
    try: parsed=date.fromisoformat(value)
    except ValueError:raise ValueError('Invalid census cutoff; use YYYY-MM-DD') from None
    if parsed.isoformat()!=value:raise ValueError('Invalid census cutoff; use YYYY-MM-DD')
    return value


def resolve_cutoff(root,platform,cutoff=None):
    """Resolve a declared cutoff before creating files or opening a database."""
    requested=validate_cutoff(cutoff) if cutoff is not None else None
    saved=[]
    for path in (Path(root)/'plan.json',Path(root)/platform/'plan.json'):
        if path.exists():
            plan=json.loads(path.read_text())
            if 'cutoff' in plan:saved.append(validate_cutoff(plan['cutoff']))
    if len(set(saved))>1 or (requested is not None and any(v!=requested for v in saved)):
        raise ValueError('Frozen census cutoff conflicts with requested cutoff')
    if requested is not None:return requested
    if saved:return saved[0]
    raise ValueError('New census requires explicit cutoff YYYY-MM-DD')

class Census:
    def __init__(self,root,platform,hosts,cutoff=None):
        cutoff=resolve_cutoff(root,platform,cutoff)
        self.platform=platform;self.hosts=set(hosts);self.cutoff=cutoff
        self.root=Path(root)/platform;self.root.mkdir(parents=True,exist_ok=True)
        (self.root/'responses').mkdir(exist_ok=True)
        self.db=sqlite3.connect(self.root/'records.sqlite3');self.db.execute('CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY,url TEXT,title TEXT,data TEXT)')
        self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.started=now();self.requests=0;self.failures=[]
        plan={'platform':platform,'cutoff':cutoff,'mode':'catalog_only','versions':'all_discoverable','hosts':sorted(hosts)}
        p=self.root/'plan.json'
        if p.exists():
            if json.loads(p.read_text())!=plan:raise ValueError('冻结目录计划不一致')
        else:atomic(p,plan)
    def fetch(self,url,data=None,headers=None):
        if urlsplit(url).hostname not in self.hosts:raise ValueError('未登记官方主机: '+url)
        key=hashlib.sha256(url.encode()+b'\0'+(data or b'')).hexdigest();receipt=self.root/'responses'/(key+'.json');raw=self.root/'responses'/(key+'.body')
        if receipt.exists() and raw.exists():
            r=json.loads(receipt.read_text())
            if r.get('ok'):
                b=raw.read_bytes()
                if hashlib.sha256(b).hexdigest()==r['sha256']:return b,r['final_url']
        # POST is used only by registered read-only catalogue query adapters.
        # Preserve CookieJar/session/form state; this is the sole retry owner.
        request_id=uuid.uuid4().hex
        context={}
        def once(request_url):
            self.requests+=1;context.clear()
            req=urllib.request.Request(request_url,data=data,headers={'User-Agent':'LawCrawler/1.0 official-catalog-audit','Accept':'text/html,application/json,application/xml;q=0.9',**(headers or {}),'Accept-Encoding':'identity'})
            try:
                with self.opener.open(req,timeout=20) as response:
                    context.update(final_url=response.url,status=response.status,content_type=response.headers.get('Content-Type'))
                    if urlsplit(response.url).hostname not in self.hosts:raise ValueError('重定向至未登记主机')
                    body=io.BytesIO()
                    stream_response(response,body.write,12*1024*1024)
                    b=body.getvalue()
                    context.update(sha256=hashlib.sha256(b).hexdigest(),size=len(b))
                temporary=raw.with_name(raw.name+'.'+request_id+'.tmp')
                with temporary.open('wb') as f:
                    f.write(b);f.flush();os.fsync(f.fileno())
                temporary.replace(raw)
                return b,context['final_url']
            except urllib.error.HTTPError as exc:
                context.update(status=exc.code)
                exc.close()
                raise
        def record_attempt(event):
            r={'url':url,'at':event['checked_at'],'request_id':request_id,'attempt':event['attempt'],
               'ok':event['status']=='succeeded',**context,
               'attempt_status':event['status'],'backoff_seconds':event['backoff_seconds']}
            if not r['ok']:r.update(error=event['reason'],error_type=event['error_type'])
            for field in ('retry_after_seconds','not_before'):
                if field in event:r[field]=event[field]
            attempt_receipt=receipt.with_name(key+'.'+request_id+'.'+str(event['attempt'])+'.json')
            temporary=attempt_receipt.with_suffix('.tmp')
            with temporary.open('xb') as f:
                f.write(json.dumps(r,ensure_ascii=False,indent=2).encode());f.flush();os.fsync(f.fileno())
            try:os.link(temporary,attempt_receipt)
            finally:temporary.unlink()
            atomic(receipt,r)
            if event['status'] in ('failed','deferred'):self.failures.append(r)
        result=retry_fetch(once,url,max_attempts=3,sleep=time.sleep,event_callback=record_attempt)
        time.sleep(.25)
        return result
    def records(self,rows):
        for row in rows:
            url=row['url'];key=hashlib.sha256(url.encode()).hexdigest()
            self.db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?)',(key,url,row.get('title',''),json.dumps(row,ensure_ascii=False)))
        self.db.commit()
    def count(self):return self.db.execute('SELECT count(*) FROM records').fetchone()[0]
    def checkpoint(self,**extra):
        p=self.root/'status.json';old=json.loads(p.read_text()) if p.exists() else {}
        result={**old,'platform':self.platform,'cutoff':self.cutoff,'started_at':old.get('started_at',self.started),'updated_at':now(),'discovered_records':self.count(),'requests_this_process':self.requests,'failures':self.failures,**extra}
        result.setdefault('status','running');result.setdefault('catalogue_complete',False);result.setdefault('all_versions_complete',False)
        atomic(p,result);return result
