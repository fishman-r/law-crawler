"""加拿大联邦合并法规：官方目录发现，优先 XML；不采案例或学术汇编。"""
import re
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree as ET

from crawl_store import unknown_metadata
from regulation_metadata import _Document
from .base import Adapter, Document, Links, field, text_html

JUSTICE_HOSTS = ('laws.justice.gc.ca', 'laws-lois.justice.gc.ca')
DETAIL = re.compile(r'/eng/(acts|regulations)/([^/]+)/index\.html$', re.I)


def _official(url, hosts):
    p = urlsplit(url)
    return p.scheme in ('https', 'http') and p.hostname in hosts and not p.username and not p.password and p.port in (None,80,443)


def _alternate(items, limit):
    acts = [u for u in items if '/acts/' in u]
    regs = [u for u in items if '/regulations/' in u]
    ordered = []
    for i in range(max(len(acts),len(regs))):
        for group in (acts,regs):
            if i < len(group) and group[i] not in ordered:
                ordered.append(group[i])
                if len(ordered) >= limit:
                    return ordered
    return ordered


class JusticeCanadaAdapter(Adapter):
    platform = 'laws.justice.gc.ca'
    country = 'Canada'
    entry_urls = ('https://laws.justice.gc.ca/eng/acts/', 'https://laws.justice.gc.ca/eng/regulations/')
    allowed_hosts = JUSTICE_HOSTS

    def discover(self, fetch, limit):
        if limit <= 0:
            return []
        found = []
        for entry in self.entry_urls:
            body, final = fetch(entry)
            if not _official(final,self.allowed_hosts):
                raise ValueError('加拿大目录跳转到未确认来源')
            found.extend(u for u in Links(text_html(body), final).urls if _official(u,JUSTICE_HOSTS) and DETAIL.fullmatch(urlsplit(u).path))
        return _alternate(found,limit)

    def read_document(self, url, fetch):
        if not _official(url,JUSTICE_HOSTS) or not DETAIL.fullmatch(urlsplit(url).path):
            raise ValueError('加拿大法规来源不是官方合并文本目录页')
        body, final = fetch(url)
        if not _official(final,JUSTICE_HOSTS) or not DETAIL.fullmatch(urlsplit(final).path):
            raise ValueError('加拿大法规详情跳转到未确认页面')
        html=text_html(body)
        nodes=list(_Document(html).root.walk())
        title=next((n.text() for n in nodes if n.tag=='h1' and 'HeadTitle' in n.attrs.get('class','').split()),None)
        match=DETAIL.fullmatch(urlsplit(final).path)
        kind='Act' if match[1].lower()=='acts' else 'Regulation'
        if not title:
            raise ValueError('官方详情缺少法规标题，可能是错误页')
        links=Links(html,final).urls
        expected='/eng/XML/'+match[2]+'.xml'
        xmls=[u for u in links if _official(u,JUSTICE_HOSTS) and urlsplit(u).path==expected]
        if not xmls:
            raise ValueError('官方详情未提供与法规编号匹配的 XML 正文链接')
        metadata=unknown_metadata(final)
        metadata['title']=field(title,title,'HeadTitle',final,'官方目录页完整法规标题及引注。')
        metadata['document_type']=field('regulation',kind,'官方 acts/regulations 栏目',final,'联邦成文法资料（含 Act 与 Regulation）；不代表已核实现行效力。')
        metadata['legal_identity']=field(dict(jurisdiction='CA',issuer=None,type=kind,number=match[2],year=None),{'path':urlsplit(final).path,'title':title},'官方路径及标题',final,'加拿大法律文献身份，路径编号保留原样；不据此推断地域适用范围。','partial')
        metadata['official_resource_uri']=field('https://laws-lois.justice.gc.ca'+expected, xmls[0], '司法部双主机同路径的当前合并正文资源', final, '仅司法部已核实的两官方主机别名共用此资源标识；不表示公布日期已知。')
        metadata['text_version']=field('consolidated','Full Document','官方合并法规目录',final,'合并文本，不是原始颁布版。')
        for n in nodes:
            if n.tag=='meta' and n.attrs.get('name')=='dcterms.modified':
                date=n.attrs.get('content','')
                if re.fullmatch(r'\d{4}-\d{2}-\d{2}',date):
                    metadata['updated_at']=field(date,date,'dcterms.modified',final,'官方页面声明的修改日期，非下载时间或法规公布日期。')
        status=next((n.text() for n in nodes if n.attrs.get('id')=='assentedDate'),'')
        for name,pattern,label,meaning in [
            ('consolidated_to',r'current to (\d{4}-\d{2}-\d{2})','current to','页面声明的合并文本截至日期，不等于公布日期或效力结论。'),
            ('last_amended_at',r'last amended.*?on (\d{4}-\d{2}-\d{2})','last amended on','法规最近修订日期，与网页更新时间分别保留。')]:
            m=re.search(pattern,status,re.I)
            metadata[name]=field(m[1] if m else None,status or None,label if status else None,final,meaning)
        return Document(final,xmls,metadata)


class OpenCanadaAdapter(JusticeCanadaAdapter):
    platform='open.canada.ca'
    entry_urls=('https://open.canada.ca/data/en/dataset/eb0dee21-9123-4d0d-b11d-0763fa1fb403',)
    allowed_hosts=('open.canada.ca',)+JUSTICE_HOSTS

    def discover(self, fetch, limit):
        if limit<=0:
            return []
        body,final=fetch(self.entry_urls[0])
        if not _official(final,('open.canada.ca',)):
            raise ValueError('开放数据门户跳转到未知来源')
        catalogs=[u for u in Links(text_html(body),final).urls if _official(u,JUSTICE_HOSTS) and urlsplit(u).path=='/eng/XML/Legis.xml']
        if not catalogs:
            raise ValueError('门户未提供已识别的官方 XML 目录资源')
        data,final=fetch(catalogs[0])
        if not _official(final,JUSTICE_HOSTS) or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
            raise ValueError('不接受未知来源或带 DTD 的法规目录')
        root=ET.fromstring(data)
        if root.tag!='ActsRegsList':
            raise ValueError('不是官方法规 XML 目录；目录不会作为原件入库')
        found=[]
        for node in root.iter():
            if node.findtext('Language')!='eng':
                continue
            link=node.findtext('LinkToHTMLToC') or ''
            if _official(link,JUSTICE_HOSTS) and DETAIL.fullmatch(urlsplit(link).path):
                # 目录历史链接使用 http；同官方主机改用 HTTPS 保持传输校验。
                p=urlsplit(link);found.append(urlunsplit(('https',p.netloc,p.path,p.query,p.fragment)))
        return _alternate(found,limit)

    def read_document(self,url,fetch):
        doc=super().read_document(url,fetch)
        doc.metadata['catalogue_url']=field(self.entry_urls[0],self.entry_urls[0],'Open Government dataset',self.entry_urls[0],'发现法规索引的官方开放数据集；实际正文由司法部发布。')
        return doc


ADAPTERS={a.platform:a for a in (JusticeCanadaAdapter(),OpenCanadaAdapter())}
