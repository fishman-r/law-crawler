"""国家级三平台的目录普查：只归档目录响应和可见元数据，不请求法规原件。"""
import argparse
import asyncio
from collections import deque
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from urllib.parse import urljoin, urlsplit, urldefrag
import xml.etree.ElementTree as ET

from law_crawler.catalog_census_common import Census, atomic, now

CONFIG = {
    'peraturan.go.id': dict(hosts=['peraturan.go.id','www.peraturan.go.id'],
        entries=['https://peraturan.go.id/','https://www.peraturan.go.id/','https://peraturan.go.id/peraturan'],
        details=r'^/id/[^/]+/?$'),
    'bpk.go.id': dict(hosts=['peraturan.bpk.go.id'],
        entries=['https://peraturan.bpk.go.id/','https://peraturan.bpk.go.id/Search'],
        details=r'^/Details/\d+(?:/[^/]+)?/?$'),
    'jdihn.go.id': dict(hosts=['jdihn.go.id','www.jdihn.go.id','dev-jdihn.bphn.go.id'],
        entries=['https://jdihn.go.id/pencarian','https://jdihn.go.id/','https://www.jdihn.go.id/pencarian','https://dev-jdihn.bphn.go.id/'],
        details=r'^/(?:index\.php/)?pencarian/detail/\d+(?:/[^/]+)?/?$'),
}
PARSER_VERSION='national-20261022-5'
FILES = re.compile(r'\.(pdf|docx?|xlsx?|pptx?|zip|rar|jpe?g|png|tiff?|mp4|mp3|bin)(?:$|[?#])',re.I)


def clean(text):
    return re.sub(r'\s+',' ',text).strip()


class Node:
    def __init__(self,tag='',attrs=(),parent=None):
        self.tag,self.attrs,self.parent,self.children=tag,dict(attrs),parent,[]
    def text(self):
        return clean(' '.join(c if isinstance(c,str) else c.text() for c in self.children))
    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child,Node):yield from child.walk()


class Page(HTMLParser):
    VOID={'meta','link','input','img','hr','br','source','area','base','embed','wbr'}
    def __init__(self,body):
        super().__init__(convert_charrefs=True)
        self.root=Node();self.stack=[self.root];self.feed(body)
    def handle_starttag(self,tag,attrs):
        n=Node(tag,attrs,self.stack[-1]);self.stack[-1].children.append(n)
        if tag not in self.VOID:self.stack.append(n)
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,0,-1):
            if self.stack[i].tag==tag:self.stack=self.stack[:i];break
    def handle_data(self,data):self.stack[-1].children.append(data)


def allowed(c,url):
    u=urlsplit(url)
    return u.scheme in ('http','https') and u.hostname in c.hosts and not u.username and not u.password and u.port in (None,80,443) and not FILES.search(url)


def parse_page(c,body,base):
    text=body.decode('utf-8','replace');page=Page(text);rows={};nexts=[];catalogues=[];sitemaps=[]
    pattern=CONFIG[c.platform]['details']
    for node in page.root.walk():
        href=node.attrs.get('href') if node.tag in ('a','link') else None
        if not href:continue
        url=urldefrag(urljoin(base,href))[0]
        if not allowed(c,url):continue
        label=node.text();path=urlsplit(url).path
        if re.fullmatch(pattern,path,re.I) or (c.platform=='jdihn.go.id' and urlsplit(url).hostname=='dev-jdihn.bphn.go.id' and re.fullmatch(r'/doc/\d+',path)):
            context=node.parent.text() if node.parent else label
            if len(context)<60 and node.parent and node.parent.parent:context=node.parent.parent.text()
            title=label if len(label)>12 and not re.fullmatch(r'(?:Lihat|Detail|Selengkapnya|Baca|Lihat Detail).*',label,re.I) else None
            row=dict(url=url,title=title or '',list_context=context[:4000],catalogue_url=base,
                     published_at=None,version_id=None,version_status='unknown',cutoff_status='date_not_visible',
                     source_route='experimental_official_fallback' if urlsplit(url).hostname=='dev-jdihn.bphn.go.id' else 'production_official',
                     source_environment='development' if urlsplit(url).hostname=='dev-jdihn.bphn.go.id' else 'production')
            for target in (label,context):
                found=re.search(r'\b(?:Nomor|No\.?)\s*([\w./-]+)\s+(?:Tahun|TAHUN)\s+(\d{4})',target,re.I)
                if found:
                    row.update(number=found[1],year=found[2]);break
            if row.get('year') and int(row['year'])>int(c.cutoff[:4]):row['cutoff_status']='year_after_cutoff'
            kind=re.match(r'^(Peraturan\s+(?:Menteri|Presiden|Pemerintah|Daerah|Badan)[^\d]*?|Undang[- ]Undang|Peraturan Pemerintah Pengganti Undang[- ]Undang)\s+(?:Nomor|No)',label,re.I)
            if kind:row['type']=clean(kind[1])
            rows[url]=row
        elif (node.attrs.get('rel')=='next' or re.fullmatch(r'(?:next|selanjutnya|berikutnya|›|»|→)',label,re.I)
              or re.search(r'(?:[?&](?:page|p|offset)=\d+|/page/\d+)',url) and label.isdigit()):
            if urlsplit(url).path==urlsplit(base).path or 'page' in urlsplit(url).path:nexts.append(url)
        if path.endswith('.xml') and ('sitemap' in path or node.tag=='link'):sitemaps.append(url)
        # Only follow catalogue links actually published by the official UI.
        if (c.platform=='peraturan.go.id' and re.fullmatch(r'/(?:peraturan|uu|uudp|perppu|perpu|pp|perpres|permen|perda|peraturan-pemerintah|undang-undang|peraturan-presiden|peraturan-menteri|peraturan-daerah)/?',path)):
            catalogues.append(url)
        if c.platform=='bpk.go.id' and path.lower() in ('/search','/search/'):
            catalogues.append(url)
        if c.platform=='jdihn.go.id' and urlsplit(url).hostname=='dev-jdihn.bphn.go.id' and path in ('/search','/search/'):
            catalogues.append(url)
    observed=[]
    # Inspect public server-rendered JSON as data only; never evaluate website scripts.
    if c.platform=='jdihn.go.id' and urlsplit(base).hostname=='dev-jdihn.bphn.go.id':
        decoder=json.JSONDecoder()
        for node in page.root.walk():
            if node.tag!='script':continue
            script=''.join(child for child in node.children if isinstance(child,str)).strip()
            matched=re.fullmatch(r'self\.__next_f\.push\((.*)\);?',script,re.S)
            if not matched:continue
            try:
                chunk=json.loads(matched[1])
                payload=chunk[1] if isinstance(chunk,list) and len(chunk)==2 and isinstance(chunk[1],str) else ''
            except (ValueError,TypeError):continue
            for marker in re.finditer(r'"initialData":',payload):
                try:initial,_=decoder.raw_decode(payload,marker.end())
                except ValueError:continue
                result=initial.get('results') if isinstance(initial,dict) else None
                if not isinstance(result,dict) or not isinstance(result.get('items'),list):continue
                evidence=dict(source_url=base,source_environment='development',
                    source_field='HTML initialData.results.total',reported_total=result.get('total'),
                    scope='开发环境混合文献目录；包括法规、图书、文章及可见测试数据，不能作为生产法规总数',
                    reported_by_document_type=(result.get('aggs') or {}).get('by_document_type',[]))
                if evidence not in observed:observed.append(evidence)
                for item in result['items']:
                    if not isinstance(item,dict) or not str(item.get('id','')).isdigit():continue
                    url=urljoin(base,'/doc/'+str(item['id']))
                    year=item.get('tahunTerbit')
                    valid_year=isinstance(year,int) and 1600<=year<=int(c.cutoff[:4])
                    rows[url]=dict(url=url,title=item.get('title') or item.get('subtitle') or '',
                        type=item.get('regulationType'),number=item.get('documentNumber'),year=year,
                        issuer=item.get('instansi'),document_category=item.get('category'),
                        published_at=None,version_id=None,version_status='unknown',
                        source_environment='development',source_route='experimental_official_fallback',
                        year_validation='plausible_year_only' if valid_year else 'invalid_or_after_cutoff_year',
                        cutoff_status='date_not_visible' if valid_year else 'unverified_invalid_or_future_year',
                        catalogue_url=base,discovery_method='official_development_html_initialData')
    plain=page.root.text()
    for m in re.finditer(r'(?:total|jumlah|dari|of)\s*[:=]?\s*([\d.,]+)\s*(?:peraturan|dokumen|records?|data|hasil)',plain,re.I):
        observed.append(dict(label=m.group(0),count=int(re.sub(r'\D','',m[1])),source_url=base))
    return list(rows.values()),list(dict.fromkeys(nexts+catalogues+sitemaps)),observed,plain


def parse_sitemap(c,body,base):
    root=ET.fromstring(body);urls=[clean(n.text or '') for n in root.iter() if n.tag.rsplit('}',1)[-1]=='loc']
    rows=[];nexts=[]
    for url in urls:
        if not allowed(c,url):continue
        if re.fullmatch(CONFIG[c.platform]['details'],urlsplit(url).path,re.I):
            rows.append(dict(url=url,title='',catalogue_url=base,published_at=None,
                version_id=None,version_status='unknown',cutoff_status='date_not_visible',discovery_method='official_sitemap'))
        elif urlsplit(url).path.endswith('.xml') and 'sitemap' in urlsplit(url).path:nexts.append(url)
    return rows,nexts,[]


def census_counts(c,observed):
    production,experimental=0,0
    for (url,) in c.db.execute('SELECT url FROM records'):
        if urlsplit(url).hostname=='dev-jdihn.bphn.go.id':experimental+=1
        else:production+=1
    return dict(discovered_records=production,production_discovered_records=production,
        all_discovered_records=production+experimental,experimental_records=experimental,
        production_catalogue_total=None,observed_total=None,observed_total_evidence=observed,
        experimental_data_quality_status='contains_mixed_types_and_testing_or_invalid_years' if experimental else None)


def crawl(c,max_pages=100000):
    cursor_path=c.root/'cursor.json'
    old=json.loads(cursor_path.read_text()) if cursor_path.exists() else {}
    primary=urlsplit(CONFIG[c.platform]['entries'][0])
    seeds=CONFIG[c.platform]['entries']+[primary.scheme+'://'+primary.netloc+'/robots.txt',primary.scheme+'://'+primary.netloc+'/sitemap.xml']
    queue=deque(old.get('queue',seeds));seen=set(old.get('seen',[]))
    if old and old.get('parser_version')!=PARSER_VERSION:
        queue=deque(dict.fromkeys(seeds+list(queue)));seen=set()
    failed=list({row['url']:row for row in old.get('failed',[])}.values())
    successful_pages=set(old.get('successful_pages',set(old.get('seen',[]))-{row['url'] for row in failed}))
    pages=len(successful_pages);totals=old.get('observed_totals',[]);leaf=False
    probes_path=c.root/'browser-probes.json'
    probes={item['url']:item for item in json.loads(probes_path.read_text())} if probes_path.exists() else {}
    while queue and pages<max_pages:
        url=queue.popleft()
        if url in seen or not allowed(c,url):continue
        seen.add(url)
        try:
            probe=probes.get(url,{})
            if probe and (probe.get('error') or probe.get('access_control') or probe.get('status') not in (200,)):
                raise ValueError('同批次普通浏览器已确认入口受阻：'+str(probe.get('error') or probe.get('status')))
            receipt=c.root/'responses'/(hashlib.sha256(url.encode()+b'\0').hexdigest()+'.json')
            previous=json.loads(receipt.read_text()) if receipt.exists() else {}
            # Keep already exhausted same-batch probes; do not repeat 403 or transport failures without end.
            if previous and not previous.get('ok') and (previous.get('attempt')==3 or previous.get('status') in (400,401,403,404,429)):
                c.failures.append(previous)
                raise ValueError('同批次已耗尽入口探测：'+str(previous.get('error')))
            body,final=c.fetch(url)
            if urlsplit(final).path=='/robots.txt':
                rows,observed,plain=[],[],body.decode('utf-8','replace')
                links=[line.split(':',1)[1].strip() for line in plain.splitlines() if re.match(r'^sitemap\s*:',line,re.I)]
            elif body.lstrip().startswith(b'<?xml') or '<urlset' in body[:500].decode('utf-8','replace') or '<sitemapindex' in body[:500].decode('utf-8','replace'):
                rows,links,observed=parse_sitemap(c,body,final);plain=''
            else:rows,links,observed,plain=parse_page(c,body,final)
            if re.search(r'captcha|verify you are human|access denied|request blocked|just a moment|attention required',plain[:20000],re.I) and not rows:
                raise ValueError('官方入口呈现访问控制或人机验证，停止该入口，不绕过')
            c.records(rows);successful_pages.add(url);pages=len(successful_pages);totals.extend(x for x in observed if x not in totals)
            if rows and not links:leaf=True
            for link in links:
                if link not in seen and link not in queue:queue.append(link)
            print(c.platform,'page',pages,'records',c.count(),'queue',len(queue),url,flush=True)
        except Exception as exc:
            failed=[row for row in failed if row['url']!=url]
            failed.append(dict(url=url,error=str(exc),at=now()))
            print(c.platform,'blocked',url,str(exc),flush=True)
        atomic(cursor_path,dict(parser_version=PARSER_VERSION,queue=list(queue),seen=sorted(seen),successful_pages=sorted(successful_pages),failed=failed,pages=pages,observed_totals=totals))
        c.checkpoint(status='running',catalogue_complete=False,pages=pages,queued_pages=len(queue),
                     entry_failures=failed,all_versions_complete=False,**census_counts(c,totals),
                     cutoff_basis='可见日期；未见发布日期的条目不纳入截至日已确认总量',version_basis='目录条目不等于版本；各版本完整性未验证')
    traversal_finished=not queue
    # A drained homepage/pagination queue is not proof of the platform's national catalogue boundary.
    complete=False
    result=c.checkpoint(status='partial' if c.count() else 'blocked',
        catalogue_complete=complete,traversal_finished=traversal_finished,pages=pages,queued_pages=len(queue),entry_failures=failed,**census_counts(c,totals),
        all_versions_complete=False,versions_observed=None,all_regulations_total=None,
        completion_basis='已遍历可见目录链；仍须独立核验平台目录边界' if complete else '官方入口失败、未识别全部分页或仍有目录待遍历')
    return result


async def browser_probe(c,urls):
    """普通浏览器读取官方目录；验证码、403 和 TLS 错误仅保存证据，不规避。"""
    from playwright.async_api import async_playwright
    previous=c.root/'browser-probes.json'
    results=json.loads(previous.read_text()) if previous.exists() else []
    done={item['url'] for item in results}
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,chromium_sandbox=True,timeout=30000)
        context=await browser.new_context(ignore_https_errors=False,accept_downloads=False,java_script_enabled=False)
        page=await context.new_page()
        async def directory_only(route):
            req=route.request
            # Census mode permits only the top-level official catalogue document.
            # Block assets, scripts, embedded originals, and all download navigation.
            if req.is_navigation_request() and req.frame==page.main_frame and allowed(c,req.url):
                await route.continue_()
            else:
                await route.abort()
        await page.route('**/*',directory_only)
        for url in urls:
            if not allowed(c,url) or url in done:continue
            item=dict(url=url,started_at=now(),runtime='server_chromium',tls_verification=True,
                      javascript_enabled=False,resource_policy='official_top_level_catalogue_only')
            try:
                response=await page.goto(url,wait_until='domcontentloaded',timeout=30000)
                item.update(status=response.status if response else None,final_url=page.url)
                body=(await page.content()).encode()
                key=hashlib.sha256(url.encode()).hexdigest()
                (c.root/'responses'/(key+'.browser.html')).write_bytes(body)
                rows,links,totals,plain=parse_page(c,body,page.url)
                item.update(title=await page.title(),records=len(rows),visible_text=plain[:2500],observed_total=totals,catalogue_links=links)
                # No browser challenge bypass. A 403/captcha page is never a regulation record.
                item['access_control']=bool((response and response.status in (401,403,429)) or re.search(r'captcha|verify you are human|access denied|just a moment|attention required',plain[:10000],re.I))
                if not item['access_control'] and response and response.status==200:c.records(rows)
                await page.screenshot(path=str(c.root/'responses'/(key+'.browser.png')),full_page=False)
            except Exception as exc:item['error']=str(exc)
            item['ended_at']=now();results.append(item);atomic(c.root/'browser-probes.json',results)
            c.checkpoint(browser_probes=results,**census_counts(c,[]))
            await asyncio.sleep(.25)
        await browser.close()
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--platform',choices=[*CONFIG,'all'],required=True)
    parser.add_argument('--cutoff',help='Explicit YYYY-MM-DD cutoff; omit only to inherit a frozen plan')
    parser.add_argument('--max-pages',type=int,default=100000)
    parser.add_argument('--browser-probe',action='store_true')
    args=parser.parse_args()
    for platform in (list(CONFIG) if args.platform=='all' else [args.platform]):
        config=CONFIG[platform]
        c=Census(args.root,platform,config['hosts'],args.cutoff)
        if args.browser_probe:
            result=asyncio.run(browser_probe(c,config['entries']))
            c.checkpoint(status='partial' if c.count() else 'blocked',catalogue_complete=False,all_versions_complete=False,
                         browser_probes=result,all_regulations_total=None,versions_observed=None,**census_counts(c,[]))
            print(json.dumps(result,ensure_ascii=False,indent=2))
        else:print(json.dumps(crawl(c,args.max_pages),ensure_ascii=False,indent=2))



if __name__=='__main__':main()
