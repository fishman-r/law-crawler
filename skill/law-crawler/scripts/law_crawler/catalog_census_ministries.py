"""OJK、卫生部、财政部及投资部官方目录普查；只取目录，不请求正文文件。"""
import argparse
import hashlib
import html
import json
import math
import re
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit, parse_qs

from .catalog_census_common import Census, atomic


class Node:
    def __init__(self, tag='', attrs=(), parent=None):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []
    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()
    def text(self):
        return ' '.join(' '.join(child.text() if isinstance(child, Node) else child for child in self.children).split())


class DOM(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.root = self.current = Node()
        self.feed(body)
    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.current); self.current.children.append(n)
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'):
            self.current = n
    def handle_startendtag(self, tag, attrs):
        self.current.children.append(Node(tag, attrs, self.current))
    def handle_endtag(self, tag):
        n = self.current
        while n.parent and n.tag != tag:
            n = n.parent
        if n.parent:
            self.current = n.parent
    def handle_data(self, data):
        self.current.children.append(data)


MONTHS = {'jan':1,'januari':1,'feb':2,'februari':2,'mar':3,'maret':3,'apr':4,'april':4,'mei':5,'jun':6,'juni':6,'jul':7,'juli':7,'agu':8,'agt':8,'ags':8,'agustus':8,'sep':9,'september':9,'okt':10,'oktober':10,'nov':11,'november':11,'des':12,'desember':12}


def date_value(value):
    value = str(value or '').strip()
    m = re.match(r'((?:19|20)\d{2}-\d{2}-\d{2})', value)
    if m:
        return m.group(1)
    m = re.search(r'\b(\d{1,2})\s+([A-Za-z]+)\s+((?:19|20)\d{2})\b', value)
    if m and m.group(2).lower() in MONTHS:
        return f'{m[3]}-{MONTHS[m[2].lower()]:02}-{int(m[1]):02}'
    return None


def classify(title, kind=''):
    # 文种由官网目录的独立字段优先决定；不能因标题引用草案/决定而排除一部规定。
    kind=str(kind or '').strip().lower();primary=kind if kind not in ('','peraturan','dokumen') else re.split(r'\btentang\b',title,flags=re.I)[0].lower()
    if re.search(r'^rancangan\b|naskah akademik|program penyusunan', primary):
        return 'draft', 'excluded_draft'
    if re.search(r'^putusan pengadilan|^putusan mahkamah|^putusan\b', primary):
        return 'court_case', 'excluded_individual_judgment'
    if re.search(r'\bmonografi\b|\bartikel\b|\bberita\b|kajian|penelitian', kind):
        return 'non_regulatory_material', 'excluded_non_regulatory_material'
    if re.search(r'^keputusan\b|^penetapan\b', primary):
        return 'decision', 'pending_normative_or_administrative'
    if re.search(r'peraturan|undang.undang|surat edaran|instruksi|tap mpr|\bpojk\b|\bseojk\b|\bpadk\b', primary):
        return 'regulation_or_guidance', 'candidate'
    return 'unknown', 'pending_type_verification'


def enrich(row, cutoff, catalogue_url):
    title = str(row.get('title') or '').strip(); row['title'] = title
    year = row.get('year')
    if not year or str(year) == '0':
        m = re.search(r'\bTahun\s+((?:18|19|20)\d{2})\b', title, re.I)
        year = m.group(1) if m else None
    row['year'] = str(year) if year else None
    row['date'] = date_value(row.get('date'))
    row.setdefault('published_at', row['date'] if 'list_display' not in row.get('date_meaning','') else None)
    row.setdefault('promulgated_at', None)
    row.setdefault('enacted_at', None)
    row.setdefault('issuer', None)
    row['cutoff_date_basis'] = row.get('date_meaning','unknown')
    row['cutoff_confidence'] = 'source_day_field' if row['date'] and row.get('published_at') else 'display_date_semantics_unverified' if row['date'] else 'year_only' if year else 'unknown'
    row['date_precision'] = 'day' if row['date'] else 'year' if year else 'unknown'
    row['cutoff_status'] = ('after_cutoff' if row['date'] > cutoff else 'within_cutoff') if row['date'] else ('year_before_cutoff_date_unknown' if str(year or '') and int(year) < int(cutoff[:4]) else 'after_cutoff_year' if str(year or '') and int(year) > int(cutoff[:4]) else 'cutoff_unverified')
    if row['date'] and 'list_display' in row.get('date_meaning',''):
        row['cutoff_status'] = 'year_before_cutoff_date_unknown' if year and int(year)<int(cutoff[:4]) else 'cutoff_unverified_display_date'
    row['material_kind'], row['scope_status'] = classify(title, row.get('type', ''))
    row['catalogue_url'] = catalogue_url
    row['all_versions_policy'] = 'retain_every_distinct_official_detail_url_and_visible_version'
    return row


def save(c, rows, source):
    prepared=[]
    for row in rows:
        row=enrich(row,c.cutoff,source)
        # 同一详情URL若在目录出现多个版本/数据库ID，保留全部可见元数据，不按法规身份折叠。
        if row.get('official_metadata'):
            key=hashlib.sha256(row['url'].encode()).hexdigest();old=c.db.execute('SELECT data FROM records WHERE id=?',(key,)).fetchone()
            variants=[]
            if old:
                previous=json.loads(old[0]);variants=previous.get('catalogue_variants') or ([previous['official_metadata']] if previous.get('official_metadata') else [])
            current=next((record for record in reversed(prepared) if record['url']==row['url']),None)
            if current:variants=current.get('catalogue_variants',variants)
            if row['official_metadata'] not in variants:variants.append(row['official_metadata'])
            row['catalogue_variants']=variants
        prepared.append(row)
    c.records(prepared)


def summary(c):
    kinds, cutoff, scope = Counter(), Counter(), Counter()
    for (raw,) in c.db.execute('SELECT data FROM records'):
        row = json.loads(raw); kinds[row.get('material_kind','unknown')] += 1; cutoff[row.get('cutoff_status','unknown')] += 1; scope[row.get('scope_status','unknown')] += 1
    return {'material_kind_counts':dict(kinds),'cutoff_counts':dict(cutoff),'scope_counts':dict(scope),'regulation_unique_count':None,'count_unit':'distinct_official_detail_urls_not_unique_regulations','all_versions_complete':False}


def page_fingerprint(rows):
    return hashlib.sha256('\n'.join(sorted(row['url'] for row in rows)).encode()).hexdigest()


def rsc_objects(body):
    """解码官网 Next.js SSR 已公开给浏览器的目录数据，不访问内部服务。"""
    chunks = []
    for m in re.finditer(r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)', body):
        try: chunks.append(json.loads(m.group(1)))
        except ValueError: pass
    payload = ''.join(chunks); decoder = json.JSONDecoder(); found = []
    for m in re.finditer(r'"data":\s*(?=\{)', payload):
        try:
            row, _ = decoder.raw_decode(payload[m.end():])
            if isinstance(row, dict) and row.get('produk_hukum_id'):
                found.append(row)
        except ValueError: pass
    return found


def kemkes(c, max_pages):
    base = 'https://jdih.kemkes.go.id/documents'; seen = set(); total = None; processed = 0; entries_seen = 0
    for page in range(1, max_pages + 1):
        url = base if page == 1 else base + '?' + urlencode({'page':page})
        body, final = c.fetch(url); source = body.decode('utf-8','replace'); doc = DOM(source); nodes = list(doc.root.walk())
        m = re.search(r'dari\s*<span[^>]*>\s*([\d.,]+)\s*</span>\s*dokumen', source, re.I)
        if m: total = int(re.sub(r'\D','',m[1]))
        rows = []
        for n in nodes:
            if n.tag != 'a' or not n.parent or n.parent.tag != 'h3' or '/documents/' not in n.attrs.get('href','') or not n.text(): continue
            target = urljoin(final,n.attrs['href'])
            if urlsplit(target).hostname != 'jdih.kemkes.go.id': continue
            card = n
            for _ in range(3): card = card.parent or card
            texts = [x.text() for x in card.walk() if x.tag == 'p']
            title = max([n.text(),*texts],key=len)
            raw_date = next((x.text() for x in card.walk() if x.tag == 'span' and date_value(x.text())),None)
            number = re.search(r'\bNomor\s+(.+?)(?:\s+Tahun\s+\d{4}|\s+tentang|$)',n.text(),re.I)
            kind = re.split(r'\s+Nomor\b',n.text(),flags=re.I)[0]
            rows.append({'url':target,'title':title,'date':raw_date,'date_raw':raw_date,'date_meaning':'official_list_display_date_semantics_unverified','type':kind,'number':number.group(1) if number else None,'issuer':'Kementerian Kesehatan','official_metadata':{'title':title,'date_display':raw_date,'type':kind,'list_page':page,'detail_url':target}})
        values = rows; fingerprint = page_fingerprint(values)
        if not values or fingerprint in seen:
            c.checkpoint(status='incomplete',catalogue_complete=False,observed_total=total,pages=processed,reason='empty_or_repeated_page_before_verified_terminal',**summary(c)); return
        seen.add(fingerprint);save(c,values,final);processed=page;entries_seen+=len(values)
        next_button = any(n.tag == 'button' and 'nextPage' in n.attrs.get('wire:click','') and 'disabled' not in n.attrs for n in nodes)
        c.checkpoint(status='running',catalogue_complete=False,observed_total=total,pages=page,next_page=page+1,page_size_observed=len(values),observed_catalogue_rows=entries_seen,unlinked_catalogue_rows=0,observed_total_basis='official_documents_list_pagination_total_including_repeated_detail_urls',**summary(c))
        if not next_button:
            complete = total is not None and entries_seen == total
            c.checkpoint(status='completed' if complete else 'incomplete',catalogue_complete=complete,observed_total=total,pages=page,terminal='official_next_button_absent',catalogue_entries_seen=entries_seen,observed_catalogue_rows=entries_seen,unlinked_catalogue_rows=0,observed_total_basis='official_documents_list_pagination_total_including_repeated_detail_urls',distinct_detail_urls=c.count(),reason=None if complete else 'terminal_count_mismatch',**summary(c)); return
    c.checkpoint(status='incomplete',catalogue_complete=False,observed_total=total,pages=processed,reason='operator_page_cap',**summary(c))


def bkpm(c, max_pages):
    base='https://jdih.bkpm.go.id';seen=set();total=None;rows_seen=0;unlinked_rows=0
    # 此接口及参数直接来自官方 search 页面加载的 Next.js 客户端脚本。
    entry,_=c.fetch(base+'/id/search?keyword=&sort=desc_nulls_last&status=all')
    match=re.search(r'(/_next/static/chunks/app/(?:%5Blang%5D|\[lang\])/search/page-[A-Za-z0-9]+\.js)',entry.decode('utf8','replace'))
    if not match:raise ValueError('BKPM官方搜索脚本入口未找到')
    c.fetch(base+match[1])
    for page in range(1,max_pages+1):
        url=base+'/api/document-search?'+urlencode({'page':page,'limit':50,'keyword':'','sort':'desc_nulls_last','status':'all'})
        raw,final=c.fetch(url); data=json.loads(raw); items=data.get('allDocuments')
        if not isinstance(items,list):raise ValueError('BKPM API 缺少 allDocuments')
        total=data.get('v_peraturan_prior_aggregate',{}).get('aggregate',{}).get('count')
        rows=[]
        for item in items:
            if not item.get('slug'):continue
            rows.append({'url':base+'/id/document/'+item['slug'],'title':item.get('title'),'date':item.get('publication_date'),'date_raw':item.get('publication_date'),'date_meaning':'publication_date_official_catalogue_not_independently_promulgation_verified','enacted_at':item.get('establishment_date'),'type':item.get('regulation_sub_type') or item.get('regulation_type') or item.get('document_type'),'document_type':item.get('document_type'),'number':item.get('regulation_number'),'year':item.get('regulation_year') or None,'source_id':item.get('id'),'issuer':item.get('main_entry') or item.get('institution'),'official_metadata':item})
        fingerprint=page_fingerprint(rows)
        if fingerprint in seen:raise ValueError('BKPM 分页重复，不能声明完整')
        seen.add(fingerprint);save(c,rows,final);rows_seen+=len(items);unlinked_rows+=len(items)-len(rows)
        c.checkpoint(status='running',catalogue_complete=False,observed_total=total,pages=page,next_page=page+1,page_size_requested=50,observed_catalogue_rows=rows_seen,unlinked_catalogue_rows=unlinked_rows,observed_total_basis='official_document_search_aggregate_count_all_material_types',**summary(c))
        if len(items)<50 or isinstance(total,int) and page*50>=total:
            complete=isinstance(total,int) and rows_seen==total
            c.checkpoint(status='completed' if complete else 'incomplete',catalogue_complete=complete,observed_total=total,pages=page,terminal='official_aggregate_last_page',catalogue_entries_seen=rows_seen,observed_catalogue_rows=rows_seen,unlinked_catalogue_rows=unlinked_rows,observed_total_basis='official_document_search_aggregate_count_all_material_types',distinct_detail_urls=c.count(),reason=None if complete else 'count_mismatch',**summary(c));return
    c.checkpoint(status='incomplete',catalogue_complete=False,observed_total=total,pages=max_pages,reason='operator_page_cap',**summary(c))


def ojk(c,max_pages):
    url='https://www.ojk.go.id/id/regulasi/Default.aspx';payload=None;seen=set();total=None;items_total=0
    for page in range(1,max_pages+1):
        raw,final=c.fetch(url,data=payload,headers={'Content-Type':'application/x-www-form-urlencoded'} if payload else None)
        source=raw.decode('utf8','replace');nodes=list(DOM(source).root.walk());rows=[]
        for tr in (n for n in nodes if n.tag=='tr'):
            links=[n for n in tr.walk() if n.tag=='a' and re.match(r'/id/regulasi/Pages/[^/]+\.aspx$',n.attrs.get('href',''),re.I)]
            if not links:continue
            a=links[0];cells=[n for n in tr.children if isinstance(n,Node) and n.tag=='td'];number=cells[0].text() if cells else None
            caption=next((n.text() for n in tr.walk() if 'caption' in n.attrs.get('class','').split()),'');kind=caption.split('•')[0].strip()
            ym=re.search(r'(?:Tahun\s+|/)((?:19|20)\d{2})\s*$',number or '',re.I)
            rows.append({'url':urljoin(final,a.attrs['href']),'title':a.text(),'type':kind,'issuer':'Otoritas Jasa Keuangan','number':number,'year':ym[1] if ym else None,'date':None,'date_meaning':'no_publication_date_on_list','official_caption_raw':caption,'caption_year_not_used_as_publication_date':True})
        fingerprint=page_fingerprint(rows)
        if not rows or fingerprint in seen:raise ValueError('OJK 目录为空或分页重复，不能声明完整')
        seen.add(fingerprint);save(c,rows,final);items_total+=len(rows)
        targets=[]
        for n in nodes:
            if n.tag!='a' or 'pagingButton' not in n.attrs.get('class','').split():continue
            match=re.search(r"__doPostBack\('([^']*)','([^']*)'\)",n.attrs.get('href',''))
            if match:targets.append((n.text(),n.attrs.get('class',''),match[1],match[2]))
        next_target=next((n for n in targets if n[0]==str(page+1)),None) or next((n for n in targets if 'fa-arrow-right' in n[1]),None)
        c.checkpoint(status='running',catalogue_complete=False,observed_total=None,pages=page,next_page=page+1,records_seen_across_pages=items_total,observed_catalogue_rows=items_total,unlinked_catalogue_rows=0,**summary(c))
        if not next_target:
            total=items_total;c.checkpoint(status='completed',catalogue_complete=True,observed_total=total,observed_total_basis='sum_of_all_official_form_pages_after_verified_terminal',observed_catalogue_rows=items_total,unlinked_catalogue_rows=0,pages=page,terminal='official_next_postback_absent',**summary(c));return
        fields={}
        for n in nodes:
            if n.tag=='input' and n.attrs.get('name') and n.attrs.get('type','').lower() in ('hidden','text','search'):
                fields[n.attrs['name']]=n.attrs.get('value','')
            elif n.tag=='select' and n.attrs.get('name'):
                options=[o for o in n.walk() if o.tag=='option'];selected=next((o for o in options if 'selected' in o.attrs),options[0] if options else None)
                if selected:fields[n.attrs['name']]=selected.attrs.get('value',selected.text())
        fields['__EVENTTARGET']=next_target[2];fields['__EVENTARGUMENT']=next_target[3];payload=urlencode(fields).encode()
    c.checkpoint(status='incomplete',catalogue_complete=False,observed_total=None,pages=max_pages,reason='operator_page_cap',**summary(c))


def finance_rows(source,base):
    rows=[]
    visible_paths={n.attrs.get('href') for n in DOM(source).root.walk() if n.tag=='a'}
    for item in rsc_objects(source):
        path='/dok/'+(item.get('slug') or item['produk_hukum_id'])
        if path not in visible_paths:raise ValueError('Kemenkeu 详情URL未见于实际目录链接')
        rows.append({'url':base+path,'title':(item.get('nomor') or '')+' '+(item.get('judul') or ''),'date':item.get('tanggal_pengundangan') or item.get('tanggal_terbit'),'date_raw':item.get('tanggal_pengundangan') or item.get('tanggal_terbit'),'date_meaning':'promulgation_date_if_present_otherwise_catalogue_publication_date','enacted_at':item.get('tanggal_penetapan'),'type':item.get('bentuk'),'number':item.get('nomor'),'year':item.get('tahun'),'source_id':item.get('produk_hukum_id'),'promulgated_at':date_value(item.get('tanggal_pengundangan')),'published_at':date_value(item.get('tanggal_pengundangan') or item.get('tanggal_terbit')),'issuer':item.get('teu'),'official_metadata':item})
    for row in rows:
        if str(row.get('date') or '').startswith('1900-01-01') and row.get('type') in ('Monografi Hukum','Artikel Hukum') and not row.get('year'):
            row.update(date=None,published_at=None,promulgated_at=None,date_meaning='source_1900_placeholder_unverified')
    return rows


def kemenkeu(c,max_pages):
    base='https://jdih.kemenkeu.go.id';count_pages=0;partitions=[]
    # 官方全局结果被封顶10,000；公开类型facet总量11,016（首次观察），按类型完整分片。
    # 不加status=有效过滤，保留废止、历史及效力未记载的所有版本候选。
    c.fetch(base+'/search?order=desc')
    raw,final=c.fetch(base+'/api/search/aggregate_filter?q=');aggregation=json.loads(raw)
    atomic(c.root/'type-aggregation.json',aggregation)
    facets=aggregation.get('bentuk')
    if not isinstance(facets,list) or not facets:
        c.checkpoint(status='incomplete',catalogue_complete=False,observed_total=None,pages=0,reason='official_type_facets_not_available_global_search_capped_10000',**summary(c));return
    observed_total=sum(int(item.get('total',0)) for item in facets)
    for facet in facets:
        kind=facet.get('value');seen=set();expected=None;partition_count=0;finished=False
        for page in range(1,max_pages+1):
            if count_pages>=max_pages:break
            url=base+'/search?'+urlencode({'order':'desc','bentuk':kind,'page':page});raw,final=c.fetch(url);source=raw.decode('utf8','replace')
            m=re.search(r'Ditemukan\s+([\d.,]+)\s+hasil',source);expected=int(re.sub(r'\D','',m[1])) if m else None
            rows=finance_rows(source,base);fp=page_fingerprint(rows)
            if rows and fp in seen:raise ValueError('Kemenkeu 类型分片分页重复')
            if not rows and expected not in (0,None):raise ValueError('Kemenkeu SSR 目录数据未提取')
            seen.add(fp);save(c,rows,final);count_pages+=1;partition_count+=len(rows)
            c.checkpoint(status='running',catalogue_complete=False,observed_total=observed_total,pages=count_pages,current_type=kind,current_page=page,partition_total=expected,partitions=partitions,observed_catalogue_rows=sum(p['rows_seen'] for p in partitions)+partition_count,unlinked_catalogue_rows=0,observed_total_basis='sum_of_official_document_type_facets_all_materials',global_search_capped=True,**summary(c))
            if expected is not None and page*10>=expected or not rows:
                finished=expected is not None and partition_count==expected and expected<10000 and expected==int(facet.get('total',0));break
        partitions.append({'type':kind,'facet_total':facet.get('total'),'observed_total':expected,'rows_seen':partition_count,'complete':finished})
        if count_pages>=max_pages:break
    facet_complete=len(partitions)==len(facets) and all(p['complete'] for p in partitions) and sum(p['rows_seen'] for p in partitions)==observed_total
    complete=False  # 官方bentuk聚合未显式提供null桶；不把已知类型分片遍历完误报为全库无遗漏。
    c.checkpoint(status='completed' if complete else 'incomplete',catalogue_complete=complete,observed_total=observed_total,pages=count_pages,partitions=partitions,catalogue_entries_seen=sum(p['rows_seen'] for p in partitions),observed_catalogue_rows=sum(p['rows_seen'] for p in partitions),unlinked_catalogue_rows=0,distinct_detail_urls=c.count(),observed_total_basis='sum_of_visible_official_document_type_facets_not_proven_global_total',facet_partition_complete=facet_complete,null_type_coverage_verified=False,top_level_observed_total=10000,top_level_total_capped=True,public_access_facet_total=sum(int(item.get('total',0)) for item in aggregation.get('akses',[]) if str(item.get('value'))=='0'),reason='known_type_facets_exhausted_but_null_type_coverage_unverified' if facet_complete else 'facet_partition_count_mismatch_or_operator_page_cap',**summary(c))


def ojk_jdih(c,max_pages):
    """补充冻结入口：OJK主站导航公开的独立JDIH，逐一核对导航分类和完整客户端表格。"""
    base='https://jdih.ojk.go.id';home,_=c.fetch(base+'/');index,_=c.fetch(base+'/Web/ViewPeraturan/Index')
    source=index.decode('utf8','replace')
    if '/Web/ViewPeraturan/ListDataPeraturan' not in source:raise ValueError('JDIH官方ajax入口未确认')
    if not re.search(r'var\s+sLanguage\s*=\s*"id"',source):raise ValueError('JDIH列表语言参数未确认')
    branches={('','')}
    for n in DOM(home.decode('utf8','replace')).root.walk():
        if n.tag!='a':continue
        href=n.attrs.get('href','');parts=urlsplit(href)
        if parts.hostname=='jdih.ojk.go.id' and parts.path.lower()=='/web/viewperaturan/index':
            q=parse_qs(parts.query,keep_blank_values=True);branches.add((q.get('sektor',[''])[0],q.get('jenisPeraturan',[''])[0]))
    ordered=[('','')]+sorted(branches-{('','')});canonical={};all_ids=set();base_ids=set();audits=[];base_total=None;unlinked=0
    for page,(sector,kind) in enumerate(ordered,1):
        if page>max_pages:break
        # DataTables在浏览器内分页；官网一次响应给出aaData全表及iTotalRecords。无serverSide启用。
        url=base+'/Web/ViewPeraturan/ListDataPeraturan?'+urlencode({'sektor':sector,'jenisPeraturan':kind,'sLanguage':'id'})
        raw,final=c.fetch(url);data=json.loads(raw);items=data.get('aaData')
        if not isinstance(items,list):raise ValueError('JDIH缺少aaData完整客户端数据')
        declared=data.get('iTotalRecords');declared=declared[0] if isinstance(declared,list) and len(declared)==1 else declared
        display=data.get('iTotalDisplayRecords');display=display[0] if isinstance(display,list) and len(display)==1 else display
        if not isinstance(declared,int) or declared!=len(items) or display!=len(items):raise ValueError('JDIH客户端表格声明总量与返回行数不符')
        rows=[];ids=set()
        for item in items:
            links=[n for n in DOM(str(item[0] if item else '')).root.walk() if n.tag=='a' and n.attrs.get('href')]
            if not links:unlinked+=1;continue
            a=links[0];original=urljoin(base,a.attrs['href']);parts=urlsplit(original)
            match=re.search(r'/ViewPeraturan/Detail/([a-f0-9-]{36})/',parts.path,re.I)
            if parts.hostname!='jdih.ojk.go.id' or not match:unlinked+=1;continue
            identifier=match[1].lower();ids.add(identifier);all_ids.add(identifier)
            # 同UUID的/行业/文种后缀只是导航上下文；保留首个官方URL，所有原始链接仍保留在元数据变体。
            canonical.setdefault(identifier,original.replace('http://','https://',1))
            title=a.text();nm=re.search(r'\b(?:Nomor|No\.?)\s+(\S+)',title,re.I);number=nm[1] if nm else item[1] if len(item)>1 else None
            year=str(item[6]) if len(item)>6 and re.fullmatch(r'(?:19|20)\d{2}',str(item[6])) else None
            if not year and number:
                ym=re.search(r'/((?:19|20)\d{2})\b',number);year=ym[1] if ym else None
            rows.append({'url':canonical[identifier],'title':title,'type':item[5] if len(item)>5 else None,'number':number,'year':year,'date':None,'issuer':'Otoritas Jasa Keuangan','date_meaning':'publication_date_not_supplied_in_catalogue','source_id':identifier,'official_metadata':{'cells':item,'source_detail_url':original,'uuid':identifier},'parent_platform':'ojk.go.id'})
        save(c,rows,final)
        if page==1:base_total=declared;base_ids=set(ids)
        audits.append({'sector':sector,'regulation_type':kind,'declared_rows':declared,'returned_rows':len(items),'distinct_source_ids':len(ids),'complete':declared==len(items)})
        c.checkpoint(status='running',catalogue_complete=False,parent_platform='ojk.go.id',pages=page,total_branches=len(ordered),observed_total=base_total,observed_catalogue_rows=len(all_ids),unlinked_catalogue_rows=unlinked,branch_audits=audits,pagination_mode='official_datatables_client_side_complete_aaData',**summary(c))
    extra_ids=all_ids-base_ids;complete=len(audits)==len(ordered) and not extra_ids and not unlinked and len(base_ids)==base_total
    c.checkpoint(status='completed' if complete else 'incomplete',catalogue_complete=complete,parent_platform='ojk.go.id',pages=len(audits),total_branches=len(ordered),observed_total=base_total if not extra_ids else None,observed_catalogue_rows=len(all_ids),unlinked_catalogue_rows=unlinked,branch_audits=audits,extra_records_outside_unfiltered_table=len(extra_ids),observed_total_basis='official_unfiltered_iTotalRecords_cross_checked_against_all_visible_sector_type_menu_branches',source_scope='OJK_independent_JDIH_regulation_catalogue_no_current_status_filter',excluded_catalogues_visible=['ViewPutusanPengadilan','ViewArtikel','ViewMonografi'],reason=None if complete else 'menu_branch_or_global_count_mismatch',**summary(c))


def finalize_status(c):
    """只读已完成目录证据，一次性补最终统计字段；不联网、不重放全量分页。"""
    path=c.root/'status.json';old=json.loads(path.read_text())
    if old.get('status')=='running':raise ValueError('采集仍运行，不覆盖活动断点')
    fields={'requests_this_process':old.get('requests_this_process',0),'failures':old.get('failures',[])}
    if c.platform=='bkpm.go.id':
        pages={}
        for receipt in (c.root/'responses').glob('*.json'):
            meta=json.loads(receipt.read_text());parts=urlsplit(meta.get('url',''));q=parse_qs(parts.query,keep_blank_values=True)
            if not meta.get('ok') or parts.path!='/api/document-search' or q.get('limit')!=['50'] or q.get('keyword')!=[''] or q.get('status')!=['all']:continue
            page=int(q.get('page',['0'])[0])
            if not 1<=page<=int(old.get('pages',0)):continue
            raw=receipt.with_suffix('.body').read_bytes()
            if hashlib.sha256(raw).hexdigest()!=meta['sha256']:raise ValueError('目录响应SHA核验失败')
            items=json.loads(raw).get('allDocuments',[]);pages[page]=items
        observed=sum(len(items) for items in pages.values());unlinked=sum(not item.get('slug') for items in pages.values() for item in items)
        fields.update(observed_catalogue_rows=observed,unlinked_catalogue_rows=unlinked,observed_total_basis='official_document_search_aggregate_count_all_material_types',catalogue_complete=len(pages)==old.get('pages') and observed==old.get('observed_total'),distinct_detail_urls=c.count())
    elif c.platform=='kemenkeu.go.id':
        partitions=old.get('partitions',[]);observed=sum(item.get('rows_seen',0) for item in partitions);aggregation=json.loads((c.root/'type-aggregation.json').read_text());facets=aggregation.get('bentuk',[])
        valid=len(partitions)==len(facets) and all(item.get('complete') for item in partitions) and observed==old.get('observed_total')
        fields.update(status='incomplete',catalogue_complete=False,observed_catalogue_rows=observed,unlinked_catalogue_rows=0,facet_partition_complete=valid,null_type_coverage_verified=False,top_level_observed_total=10000,top_level_total_capped=True,public_access_facet_total=sum(int(item.get('total',0)) for item in aggregation.get('akses',[]) if str(item.get('value'))=='0'),observed_total_basis='sum_of_visible_official_document_type_facets_not_proven_global_total',reason='known_type_facets_exhausted_but_null_type_coverage_unverified' if valid else 'facet_partition_count_mismatch_or_operator_page_cap',distinct_detail_urls=c.count())
    c.checkpoint(**fields,**summary(c))


PLATFORMS={'kemkes.go.id':('jdih.kemkes.go.id',kemkes),'kemenkeu.go.id':('jdih.kemenkeu.go.id',kemenkeu),'bkpm.go.id':('jdih.bkpm.go.id',bkpm),'ojk.go.id':('www.ojk.go.id',ojk),'ojk.go.id-jdih-supplement':('jdih.ojk.go.id',ojk_jdih)}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True);parser.add_argument('--platform',choices=list(PLATFORMS),required=True);parser.add_argument('--cutoff',help='Explicit YYYY-MM-DD cutoff; omit only to inherit a frozen plan');parser.add_argument('--max-pages',type=int,default=10000);parser.add_argument('--finalize-status',action='store_true');args=parser.parse_args()
    host,runner=PLATFORMS[args.platform];c=Census(args.root,args.platform,[host],args.cutoff)
    if args.finalize_status:
        finalize_status(c);return
    c.checkpoint(status='running',catalogue_complete=False,observed_total=None,pages=0,error=None,reason=None,mode='catalog_only_no_document_download')
    try:runner(c,args.max_pages)
    except Exception as exc:
        c.checkpoint(status='blocked',catalogue_complete=False,error=str(exc),**summary(c));raise


if __name__=='__main__':main()
