"""DPR、最高法院和宪法法院的可恢复目录普查；只请求官方目录，不下载正文。"""
import argparse
from collections import Counter
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from urllib.parse import urljoin, urlsplit, parse_qs, urlencode, urlunsplit
import xml.etree.ElementTree as ET

from .catalog_census_common import Census, atomic

class ThrottledCensus(Census):
    last_request_finished = 0.0
    def fetch(self, *args, **kwargs):
        delay = .25 - (time.monotonic() - ThrottledCensus.last_request_finished)
        if delay > 0: time.sleep(delay)
        try: return super().fetch(*args, **kwargs)
        finally: ThrottledCensus.last_request_finished = time.monotonic()


DPR_BASE = 'https://jdih.dpr.go.id'
DPR_SUFFIX = '-BAGIAN-PERTIMBANGAN-DAN-DOKUMENTASI-INFORMASI-HUKUM'
DPR_CATEGORIES = {
    'UUD-1945': 'constitution', 'Undang-undang': 'law',
    'Peraturan-Pemerintah-Pengganti-Undang-undang': 'government_regulation_in_lieu_of_law',
    'Peraturan-Pemerintah': 'government_regulation', 'Peraturan-Presiden': 'presidential_regulation',
    'Keputusan-Presiden': 'presidential_decision', 'Instruksi-Presiden': 'presidential_instruction',
    'Peraturan-DPR': 'parliament_regulation', 'Peraturan-Bersama': 'joint_regulation',
    'Peraturan-Sekjen-DPR': 'secretary_general_regulation',
    'Prolegset-Persekjen': 'legislative_plan', 'Rancangan-Undang-Undang': 'draft',
}


class Document(HTMLParser):
    """仅保留目录行、锚文本、表头和页面可见文本；不执行网页脚本。"""
    def __init__(self, text, base):
        super().__init__(convert_charrefs=True)
        self.base, self.links, self.rows, self.headers, self.texts = base, [], [], [], []
        self.row, self.cell, self.anchor, self.skip = None, None, None, 0
        self.feed(text)
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style'): self.skip += 1
        if tag == 'tr': self.row = {'cells': [], 'links': []}
        if tag in ('td', 'th') and self.row is not None: self.cell = []
        if tag == 'a' and attrs.get('href'):
            self.anchor = {'url': urljoin(self.base, attrs['href']), 'text': [], 'rel': attrs.get('rel', '')}
    def handle_data(self, text):
        if self.skip: return
        if text.strip(): self.texts.append(text.strip())
        if self.cell is not None: self.cell.append(text)
        if self.anchor is not None: self.anchor['text'].append(text)
    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.skip: self.skip -= 1
        if tag in ('td', 'th') and self.row is not None and self.cell is not None:
            value = ' '.join(' '.join(self.cell).split())
            self.row['cells'].append(value)
            if tag == 'th': self.headers.append(value)
            self.cell = None
        if tag == 'a' and self.anchor is not None:
            item = {**self.anchor, 'text': ' '.join(' '.join(self.anchor['text']).split())}
            self.links.append(item)
            if self.row is not None: self.row['links'].append(item)
            self.anchor = None
        if tag == 'tr' and self.row is not None:
            self.rows.append(self.row); self.row = None; self.cell = None


def decode(raw): return raw.decode('utf-8', errors='replace')
def clean_url(url):
    split = urlsplit(url)
    return urlunsplit((split.scheme, split.netloc, split.path, split.query, ''))
def date_scope(year, cutoff):
    if year and re.fullmatch(r'\d{4}', str(year)):
        if int(year) < int(cutoff[:4]): return 'year_before_cutoff'
        if int(year) > int(cutoff[:4]): return 'after_cutoff_year'
    return 'exact_date_unknown'

def load_progress(c):
    path = c.root / 'traversal.json'
    return json.loads(path.read_text()) if path.exists() else {'visited': [], 'pages': [], 'categories': {}}

def save_progress(c, state, **extra):
    atomic(c.root / 'traversal.json', state)
    extra.setdefault('observed_total', c.count())
    return c.checkpoint(pages=state['pages'], categories=state['categories'],
        all_versions_complete=False, cutoff_catalogue_complete=False,
        cutoff_note='当前公开目录的历史截点不可由仅有年份推定；未声称完整还原截止日目录。', **extra)

def dpr_rows(doc, kind, category, cutoff):
    records = []
    for row in doc.rows:
        cells = row['cells']
        details = [link for link in row['links'] if '/setjen/detail-dokumen/' in urlsplit(link['url']).path
                   or kind == 'draft' and '/setjen/view/' in urlsplit(link['url']).path
                   or kind == 'legislative_plan' and re.search(r'\.(?:pdf|docx?)$', urlsplit(link['url']).path, re.I)]
        if not details or len(cells) < 2: continue
        headers = [value.upper().strip() for value in doc.headers]
        def field(label):
            return cells[headers.index(label)] if label in headers and headers.index(label)<len(cells) else None
        title = field('TENTANG') or field('JUDUL') or max(cells,key=len)
        year = field('TAHUN')
        year_match=re.search(r'/year/(\d{4})$',urlsplit(doc.base).path)
        year_source='visible_year_column' if year and re.fullmatch(r'\d{4}',year) else None
        if not year_source and year_match:
            year=year_match.group(1);year_source='official_catalogue_year_filter'
        if not year_source:
            match=re.match(r'^(?:Peraturan|Keputusan|Instruksi|Undang[- ]Undang).*?\bNomor\s+\S+\s+Tahun\s+(\d{4})\b',title,re.I)
            year=match.group(1) if match else None;year_source='legal_identity_in_visible_title' if match else None
        number = field('NOMOR')
        if not number:
            match=re.search(r'\bNOMOR\s+([^\s]+)',title,re.I);number=match.group(1) if match else None
        for link in details:
            records.append(dict(url=clean_url(link['url']), title=title, type=kind,
                classification='draft' if kind == 'draft' else 'planning' if kind == 'legislative_plan' else 'pending' if kind=='presidential_decision' or re.match(r'^Keputusan\b',title,re.I) else 'regulation',
                category=category, year=year, year_source=year_source, number=number,
                published_at=None, updated_at=None, applicable_region='Indonesia',
                status=field('STATUS'), visible_fields=cells, source_catalogue_url=doc.base,
                cutoff_status=date_scope(year, cutoff),
                version_status='all_linked_entries_preserved_versions_not_proven'))
    if not records and kind == 'constitution':
        for link in doc.links:
            if re.search(r'(?:/jdih/.*\.(?:pdf|docx?)$|/setjen/detail-dokumen/)', urlsplit(link['url']).path, re.I):
                records.append(dict(url=clean_url(link['url']),title=link['text'] or 'UUD 1945（目录标题未提供）',
                    type=kind,classification='regulation',category=category,year=None,number=None,
                    published_at=None,applicable_region='Indonesia',source_catalogue_url=doc.base,
                    cutoff_status='exact_date_unknown',version_status='unknown'))
    return records


def dpr(root, cutoff):
    c = ThrottledCensus(root, 'dpr', ['jdih.dpr.go.id','berkas.dpr.go.id','statik.dpr.go.id','dpr.go.id','www.dpr.go.id'], cutoff)
    cutoff = c.cutoff
    state = load_progress(c)
    c.checkpoint(status='running', catalogue_complete=False)
    # Inspect the official DataTables initializer: client-side pagination is not server-side pagination.
    js = ''
    try:
        body, _ = c.fetch('https://statik.dpr.go.id/plugin-jdih/js/app.js')
        js = decode(body)
    except Exception: pass
    static_tables = bool(re.search(r'\.[dD]ata[Tt]able\s*\(', js)) and not re.search(r'(?:serverSide|bServerSide)\s*:\s*true', js)
    for category, kind in DPR_CATEGORIES.items():
        entry = DPR_BASE + '/setjen/index/id/' + category + DPR_SUFFIX
        prior = state['categories'].get(category, {})
        if prior.get('directory_exhausted'): continue
        queue = [entry]; seen = set(); row_count = 0; success = True
        while queue:
            url = queue.pop(0)
            if url in seen: continue
            seen.add(url)
            try:
                raw, final = c.fetch(url); html = decode(raw); doc = Document(html, final)
                rows = dpr_rows(doc, kind, category, cutoff); c.records(rows); row_count += len(rows)
                # Only same-category official page links; never request a detail or original file here.
                pages = []
                for link in doc.links:
                    target = clean_url(link['url']); parts = urlsplit(target)
                    if parts.hostname != 'jdih.dpr.go.id': continue
                    if re.fullmatch(re.escape(urlsplit(entry).path) + r'/year/\d{4}',parts.path):
                        pages.append(target);continue
                    if parts.path != urlsplit(entry).path: continue
                    if parts.query and any(key in parse_qs(parts.query) for key in ('page','start','offset','limitstart')):
                        pages.append(target)
                pages=list(dict.fromkeys(pages))
                queue.extend(page for page in pages if page not in seen and page not in queue)
                indices = [int(row['cells'][0]) for row in doc.rows if row['cells'] and re.fullmatch(r'\d+',row['cells'][0]) and any('/setjen/detail-dokumen/' in a['url'] or '/setjen/view/' in a['url'] for a in row['links'])]
                contiguous = bool(indices) and set(indices) == set(range(1,max(indices)+1))
                inline = static_tables and contiguous and bool(rows) and not pages
                # Year branches can be empty in the latest year, but older linked years still require traversal.
                if not rows and not pages: success = False
                info = dict(url=url, final_url=final, response_sha256=hashlib.sha256(raw).hexdigest(),
                    row_count=len(rows), next_pages=pages, inline_row_indices_contiguous=contiguous,
                    inline_client_pagination=inline, status='parsed' if rows else 'no_verified_rows')
                state['pages'].append(info)
                state['categories'][category] = dict(type=kind, observed_rows=row_count,
                    directory_exhausted=bool(success and not queue and static_tables), pagination='all_rows_embedded_client_DataTables' if inline else 'follow_explicit_links_or_unknown')
                save_progress(c,state,status='running',catalogue_complete=False)
            except Exception as exc:
                success=False; state['pages'].append(dict(url=url,status='failed',error=str(exc)))
                state['categories'][category]=dict(type=kind,directory_exhausted=False,observed_rows=row_count)
                save_progress(c,state,status='running',catalogue_complete=False)
        state['visited'] = sorted(set(state['visited']) | seen)
    complete=all(state['categories'].get(key,{}).get('directory_exhausted') for key in DPR_CATEGORIES)
    save_progress(c,state,status='catalogue_traversed_cutoff_unverified' if complete else 'partial',
                  catalogue_complete=complete,
                  limitations=['文种间URL重复不等于同一法规版本；全部历史版本完整性未知。',
                               '草案和立法规划独立分类，个案裁判未纳入本次法规目录。'])
    return finalize_dpr(c)


def finalize_dpr(c):
    """从已存目录响应生成无链接行证据和分类型计数；不发起任何网络请求。"""
    unlinked = {}; canonical={}; observations=[]
    for receipt in sorted((c.root/'responses').glob('*.json')):
        record=json.loads(receipt.read_text())
        if not record.get('ok') or '/setjen/index/id/' not in record['url']: continue
        raw=receipt.with_suffix('.body')
        if not raw.exists(): continue
        doc=Document(raw.read_text(errors='replace'),record['final_url'])
        category=next((key for key in DPR_CATEGORIES if urlsplit(record['url']).path.startswith('/setjen/index/id/'+key+DPR_SUFFIX)),None)
        if category is None:continue
        kind=DPR_CATEGORIES[category]
        for item in dpr_rows(doc,kind,category,c.cutoff):
            observations.append(item)
            old=canonical.get(item['url'])
            rank=lambda value: 3 if value.get('year_source')=='official_catalogue_year_filter' else 2 if value.get('year_source')=='visible_year_column' else 1 if value.get('year') else 0
            if old is None or rank(item)>rank(old):canonical[item['url']]=item
        headers=[label.upper().strip() for label in doc.headers]
        for row in doc.rows:
            cells=row['cells']
            official_links=[link for link in row['links'] if urlsplit(link['url']).hostname in c.hosts]
            if official_links or not cells or not re.fullmatch(r'\d+',cells[0]): continue
            title_index=headers.index('JUDUL') if 'JUDUL' in headers else headers.index('TENTANG') if 'TENTANG' in headers else None
            if title_index is None or title_index>=len(cells) or not cells[title_index]: continue
            year_match=re.search(r'/year/(\d{4})$',urlsplit(record['url']).path)
            year=cells[headers.index('TAHUN')] if 'TAHUN' in headers and headers.index('TAHUN')<len(cells) else year_match.group(1) if year_match else None
            item=dict(title=cells[title_index],year=year,type=kind,visible_fields=cells,
                      source_catalogue_url=record['final_url'],nonofficial_links=[link['url'] for link in row['links']],
                      reason='目录有元数据但无官方详情或文件链接；搜索引擎链接未访问，不制造官方URL。',
                      classification='planning' if kind=='legislative_plan' else 'draft' if kind=='draft' else 'pending' if kind=='presidential_decision' else 'regulation',
                      published_at=None,cutoff_status=date_scope(year,c.cutoff))
            key=hashlib.sha256(json.dumps([item['title'],year,kind,cells],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            unlinked.setdefault(key,item)
    # Publish the canonical URL table atomically while preserving every observed row separately.
    c.db.execute('DELETE FROM records');c.records(canonical.values())
    observations_path=c.root/'catalogue-observations.jsonl'
    temporary=observations_path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        for item in observations:stream.write(json.dumps(item,ensure_ascii=False)+'\n')
    temporary.replace(observations_path)
    output=c.root/'unlinked-catalogue-rows.json'
    atomic(output,dict(schema_version=1,rows=list(unlinked.values()),count=len(unlinked)))
    rows=[json.loads(row[0]) for row in c.db.execute('SELECT data FROM records')]
    return c.checkpoint(observed_total=len(rows)+len(unlinked),linked_catalogue_rows=len(rows),
        unlinked_catalogue_rows=len(unlinked),unlinked_catalogue_rows_path=str(output),
        catalogue_observation_rows=len(observations),catalogue_observations_path=str(observations_path),
        unique_catalogue_pages=len({page['url'] for page in json.loads((c.root/'status.json').read_text()).get('pages',[])}),
        unlinked_counts_by_classification=dict(Counter(row['classification'] for row in unlinked.values())),
        unlinked_counts_by_type=dict(Counter(row['type'] for row in unlinked.values())),
        counts_by_classification=dict(Counter(row.get('classification','unknown') for row in rows)),
        counts_by_type=dict(Counter(row.get('type','unknown') for row in rows)),
        counts_by_cutoff_status=dict(Counter(row.get('cutoff_status','unknown') for row in rows)),
        original_files_downloaded=0, individual_judgments_excluded=True)


def sitemap_catalogue(c, urls, detail_pattern, kind):
    """遍历公开sitemap索引；记录定位元数据，不把lastmod当法规公布日期。"""
    visited, found, pages = set(), [], []
    while urls and len(visited)<1000:
        url=urls.pop(0)
        if url in visited:continue
        visited.add(url)
        try:
            raw,final=c.fetch(url);tree=ET.fromstring(raw)
            locations=[node.text for node in tree.iter() if node.tag.split('}')[-1]=='loc' and node.text]
            nested=tree.tag.split('}')[-1]=='sitemapindex'
            if nested:
                urls.extend(u for u in locations if urlsplit(u).hostname in c.hosts and u not in visited)
            rows=[dict(url=u,title='标题待核验（官方sitemap仅提供定位）',type=kind,classification='regulation',
                       published_at=None,applicable_region='Indonesia',cutoff_status='exact_date_unknown',
                       version_status='unknown',source_catalogue_url=final)
                  for u in locations if re.search(detail_pattern,urlsplit(u).path,re.I)]
            c.records(rows);found.extend(u['url'] for u in rows)
            pages.append(dict(url=url,status='parsed',entries=len(locations),regulation_entries=len(rows)))
        except Exception as exc:pages.append(dict(url=url,status='failed',error=str(exc)))
        c.checkpoint(status='running',catalogue_complete=False,pages=pages)
    return pages,found


def courts(root, platform, cutoff):
    if platform=='ma':
        hosts=['jdih.mahkamahagung.go.id','kepaniteraan.mahkamahagung.go.id']
        entries=[('https://jdih.mahkamahagung.go.id/dokumen?jenis%5B%5D=peraturan-mahkamah-agung','PERMA'),
                 ('https://jdih.mahkamahagung.go.id/dokumen?jenis%5B%5D=surat-edaran-mahkamah-agung','SEMA'),
                 ('https://kepaniteraan.mahkamahagung.go.id/peraturan/peraturan-mahkamah-agung','PERMA'),
                 ('https://kepaniteraan.mahkamahagung.go.id/peraturan/surat-edaran-mahkamah-agung','SEMA'),
                 ('https://kepaniteraan.mahkamahagung.go.id/id/profil-kepaniteraan/profile-sdm/kepaniteraan-ma?id=16&view=category','judicial_norm')]
        pattern=r'/legal-product/(?:perma|sema)-[^/]+/detail$'
    else:
        hosts=['mkri.id','www.mkri.id','jdih.mkri.id']
        entries=[('https://www.mkri.id/peraturan/peraturan-mk','PMK'),
                 ('https://mkri.id/peraturan/peraturan-mk','PMK'),('https://jdih.mkri.id/','PMK')]
        pattern=r'/(?:peraturan/(?:peraturan-mk|pmk)|produk-hukum/(?:pmk|peraturan))'
    c=ThrottledCensus(root,platform,hosts,cutoff);state=load_progress(c)
    for entry,kind in entries:
        pending=[entry];visited=set()
        while pending and len(visited)<1000:
            url=pending.pop(0)
            if url in visited:continue
            visited.add(url)
            try:
                raw,final=c.fetch(url);doc=Document(decode(raw),final);rows=[]
                if platform=='ma':
                    for link in doc.links:
                        if re.search(pattern,urlsplit(link['url']).path):
                            rows.append(dict(url=clean_url(link['url']),title=link['text'] or '标题待核验',
                                type=kind,classification='regulation',published_at=None,applicable_region='Indonesia',
                                cutoff_status='exact_date_unknown',source_catalogue_url=final,version_status='unknown'))
                else:
                    for row in doc.rows:
                        cells=row['cells']
                        if not cells or not re.match(r'^PMK\s+(?:Nomor|No\.?)\s+\d+',cells[0],re.I):continue
                        title=' — '.join(cells[:2]);rows.append(dict(url=entry.split('?')[0]+'?'+urlencode({'search':cells[0]}),
                            title=title,type='Peraturan Mahkamah Konstitusi',classification='regulation',published_at=None,
                            visible_fields=cells,applicable_region='Indonesia',cutoff_status='exact_date_unknown',
                            source_catalogue_url=final,version_status='unknown'))
                c.records(rows)
                pages=[clean_url(link['url']) for link in doc.links if urlsplit(link['url']).hostname in c.hosts
                       and urlsplit(link['url']).path==urlsplit(entry).path
                       and any(key in parse_qs(urlsplit(link['url']).query) for key in ('page','start','limitstart'))]
                pending.extend(u for u in pages if u not in pending and u not in visited)
                state['pages'].append(dict(url=url,status='parsed',rows=len(rows),pagination_urls=pages))
            except Exception as exc:state['pages'].append(dict(url=url,status='failed',error=str(exc)))
            save_progress(c,state,status='running',catalogue_complete=False)
    roots=sorted({urlsplit(url).scheme+'://'+urlsplit(url).netloc for url,_ in entries})
    for base in roots:
        seeds=[base+'/sitemap.xml']
        try:
            raw,_=c.fetch(base+'/robots.txt')
            seeds.extend(re.findall(r'^Sitemap:\s*(\S+)',decode(raw),re.M|re.I))
        except Exception:pass
        pages,_=sitemap_catalogue(c,list(dict.fromkeys(seeds)),pattern,'judicial_norm' if platform=='ma' else 'PMK')
        state['pages'].extend(pages)
    return save_progress(c,state,status='access_blocked' if c.count()==0 else 'partial',catalogue_complete=False,
                         observed_total=None, limitations=['已登记官方目录及合理替代入口受阻或未证实穷尽；未绕过403、验证码或TLS。',
                         '只收录PERMA、SEMA等司法规范或PMK；不纳入个案裁判；截止日目录及历史版本全集未知。'])


async def browser_courts(root, platform, cutoff):
    """在授权移交的单个普通浏览器中读取法院目录，不处理挑战或下载原件。"""
    import asyncio
    from playwright.async_api import async_playwright
    hosts=['jdih.mahkamahagung.go.id','kepaniteraan.mahkamahagung.go.id'] if platform=='ma' else ['mkri.id','www.mkri.id','jdih.mkri.id']
    c=ThrottledCensus(root,platform,hosts,cutoff)
    previous=json.loads((c.root/'status.json').read_text()) if (c.root/'status.json').exists() else {}
    c.failures=previous.get('failures',[])
    output=c.root/'browser';output.mkdir(exist_ok=True)
    seeds=[('https://jdih.mahkamahagung.go.id/dokumen?jenis%5B%5D=peraturan-mahkamah-agung','PERMA'),
           ('https://jdih.mahkamahagung.go.id/dokumen?jenis%5B%5D=surat-edaran-mahkamah-agung','SEMA'),
           ('https://kepaniteraan.mahkamahagung.go.id/peraturan/peraturan-mahkamah-agung','PERMA'),
           ('https://kepaniteraan.mahkamahagung.go.id/peraturan/surat-edaran-mahkamah-agung','SEMA')] if platform=='ma' else [
           ('https://www.mkri.id/peraturan/peraturan-mk','PMK'),('https://jdih.mkri.id/','PMK')]
    checks=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,chromium_sandbox=True,timeout=30000)
        context=await browser.new_context(ignore_https_errors=False,accept_downloads=False,viewport={'width':1280,'height':900})
        async def route_request(route):
            if re.search(r'\.(?:pdf|docx?|xlsx?|pptx?|mp3|mp4)(?:$|[?#])',route.request.url,re.I): await route.abort()
            else: await route.continue_()
        await context.route('**/*',route_request)
        page=await context.new_page()
        for entry,kind in seeds:
            pending=[entry];seen=set()
            while pending and len(seen)<1000:
                url=pending.pop(0)
                if url in seen:continue
                seen.add(url);item=dict(url=url,kind=kind,chromium_sandbox=True,tls_verification=True)
                try:
                    response=await page.goto(url,wait_until='domcontentloaded',timeout=30000)
                    await page.wait_for_timeout(250)
                    html=await page.content();body=html.encode();digest=hashlib.sha256(body).hexdigest()
                    (output/(digest+'.html')).write_bytes(body)
                    item.update(status_code=response.status if response else None,final_url=page.url,
                                title=await page.title(),sha256=digest,size_bytes=len(body))
                    text=await page.locator('body').inner_text(timeout=5000)
                    item['excerpt']=text[:1500]
                    challenge=bool(re.search(r'captcha|verify you are human|just a moment|access denied',text,re.I))
                    if response and response.status==200 and not challenge:
                        doc=Document(html,page.url);rows=[]
                        if platform=='ma':
                            for link in doc.links:
                                if re.search(r'/legal-product/(?:perma|sema)-[^/]+/detail$',urlsplit(link['url']).path):
                                    rows.append(dict(url=link['url'],title=link['text'] or '标题待核验',type=kind,
                                        classification='regulation',published_at=None,cutoff_status='exact_date_unknown',
                                        source_catalogue_url=page.url,version_status='unknown'))
                        else:
                            for row in doc.rows:
                                cells=row['cells']
                                if cells and re.match(r'^PMK\s+(?:Nomor|No\.?)\s+\d+',cells[0],re.I):
                                    rows.append(dict(url=entry+'?'+urlencode({'search':cells[0]}),
                                        title=' — '.join(cells[:2]),type='PMK',classification='regulation',
                                        published_at=None,visible_fields=cells,cutoff_status='exact_date_unknown',
                                        source_catalogue_url=page.url,version_status='unknown'))
                        c.records(rows);item['records']=len(rows)
                        pages=[]
                        for link in doc.links:
                            target=clean_url(link['url']);parts=urlsplit(target)
                            if parts.hostname in c.hosts and parts.path==urlsplit(entry).path and 'page' in parse_qs(parts.query):
                                if platform!='ma' or parse_qs(parts.query).get('jenis[]',parse_qs(parts.query).get('jenis[0]'))==parse_qs(urlsplit(entry).query).get('jenis[]'):
                                    pages.append(target)
                        pending.extend(u for u in dict.fromkeys(pages) if u not in seen and u not in pending)
                        item['next_pages']=pages;item['status']='parsed'
                    else:item['status']='access_blocked' if challenge or response and response.status==403 else 'http_error'
                except Exception as exc:item.update(status='navigation_failed',error=str(exc))
                checks.append(item);atomic(output/'checks.json',dict(checks=checks))
                c.checkpoint(status='browser_running',catalogue_complete=False,browser_checks=checks,
                             all_versions_complete=False,cutoff_catalogue_complete=False)
        await browser.close()
    return c.checkpoint(status='access_blocked' if c.count()==0 else 'partial',catalogue_complete=False,
                        browser_checks=checks,observed_total=None,
                        all_versions_complete=False,cutoff_catalogue_complete=False)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True)
    parser.add_argument('--platform',choices=('all','dpr','ma','mk'),default='all')
    parser.add_argument('--cutoff',help='Explicit YYYY-MM-DD cutoff; omit only to inherit a frozen plan');parser.add_argument('--browser',action='store_true');args=parser.parse_args()
    for name in (('dpr','ma','mk') if args.platform=='all' else (args.platform,)):
        try:
            if args.browser:
                if name=='dpr': continue
                import asyncio
                result=asyncio.run(browser_courts(args.root,name,args.cutoff))
            else: result=dpr(args.root,args.cutoff) if name=='dpr' else courts(args.root,name,args.cutoff)
        except Exception as exc:print(json.dumps(dict(platform=name,error=str(exc)),ensure_ascii=False));raise
        print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
