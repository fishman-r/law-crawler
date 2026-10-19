"""欧盟成员国有界发现；页面可达不等同于原件下载成功。"""
import re
from datetime import date
from html import unescape
from urllib.parse import parse_qs, urlsplit

from law_crawler.crawl_store import unknown_metadata
from law_crawler.regulation_metadata import _Document
from .base import Adapter, Document, Links, field, text_html


def _official(url, host):
    p = urlsplit(url)
    return p.scheme == 'https' and p.hostname == host and not p.username and not p.password and p.port in (None, 443)


def _page(url, fetch, host):
    if not _official(url, host):
        raise ValueError('未经确认的官方入口')
    body, final = fetch(url)
    if not _official(final, host):
        raise ValueError('官方页面跳转到未确认来源')
    return text_html(body), final


class LegifranceAdapter(Adapter):
    platform = 'legifrance.gouv.fr'
    country = 'France'
    allowed_hosts = ('www.legifrance.gouv.fr',)
    entry_urls = ('https://www.legifrance.gouv.fr/jorf/jo',)

    def discover(self, fetch, limit):
        if limit <= 0:
            return []
        html, final = _page(self.entry_urls[0], fetch, self.allowed_hosts[0])
        found = [u for u in Links(html, final).urls if _official(u, self.allowed_hosts[0]) and re.fullmatch(r'/jorf/id/JORFTEXT\d+', urlsplit(u).path)]
        if not found:
            raise ValueError('法国官方公报目录未返回已识别法规详情；不把挑战页作为发现成功')
        return found[:limit]

    def read_document(self, url, fetch):
        _page(url, fetch, self.allowed_hosts[0])
        raise ValueError('法国目录当前受阻，尚未验收法规类型与原件链路')


class NormattivaAdapter(Adapter):
    platform = 'normattiva.it'
    country = 'Italy'
    allowed_hosts = ('www.normattiva.it',)
    entry_urls = ('https://www.normattiva.it/',)
    types = ('legge', 'decreto.legge', 'decreto.legislativo')

    def discover(self, fetch, limit):
        if limit <= 0:
            return []
        html, final = _page(self.entry_urls[0], fetch, self.allowed_hosts[0])
        found = []
        for u in Links(html, final).urls:
            p = urlsplit(u)
            if _official(u, self.allowed_hosts[0]) and p.path == '/uri-res/N2Ls' and any(p.query.startswith('urn:nir:stato:' + kind + ':') for kind in self.types):
                found.append(u)
        if not found:
            raise ValueError('意大利首页未发现正式法规 URN')
        return found[:limit]

    def read_document(self, url, fetch):
        html, final = _page(url, fetch, self.allowed_hosts[0])
        nodes = list(_Document(html).root.walk())
        props = {n.attrs.get('property'): n.attrs for n in nodes if n.tag == 'meta'}
        title = unescape(props.get('eli:title', {}).get('content', '')).strip()
        code = props.get('eli:id_local', {}).get('content')
        kind = props.get('eli:type_document', {}).get('resource', '').split('#')[-1]
        if not title or not code or kind not in ('LEGGE', 'DECRETO-LEGGE', 'DECRETO LEGISLATIVO', 'DECRETO-LEGISLATIVO'):
            raise ValueError('意大利页面缺少正式法规 ELI 标题、编号或类型')
        publication = props.get('eli:date_publication', {}).get('content')
        candidates = []
        for u in Links(html, final).urls:
            p = urlsplit(u)
            q = parse_qs(p.query)
            if _official(u, self.allowed_hosts[0]) and p.path == '/do/atto/caricaAKN' and q.get('codiceRedaz') == [code] and publication and q.get('dataGU') == [publication.replace('-', '')]:
                candidates.append(u)
        if not candidates:
            raise ValueError('意大利详情未给出与 ELI 身份一致的原件导出链接')
        md = unknown_metadata(final)
        md['title'] = field(title, title, 'eli:title', final, '官方 ELI 法规标题。')
        md['document_type'] = field('regulation', kind, 'eli:type_document', final, '正式法规文献类型，不核定效力。')
        md['legal_identity'] = field(dict(jurisdiction='IT', issuer=None, type=kind, number=code, year=None), code, 'eli:id_local', final, '官方 ELI 本地标识；非自行推定法令编号。', 'partial')
        if publication:
            date.fromisoformat(publication)
            md['published_at'] = field(publication, publication, 'eli:date_publication', final, '官方明确的法规公布日期。')
        return Document(final, candidates, md)


class WettenAdapter(Adapter):
    platform = 'wetten.overheid.nl'
    country = 'Netherlands'
    allowed_hosts = ('wetten.overheid.nl',)
    entry_urls = ('https://wetten.overheid.nl/zoeken',)

    def discover(self, fetch, limit):
        if limit <= 0:
            return []
        html, final = _page(self.entry_urls[0], fetch, self.allowed_hosts[0])
        nodes = list(_Document(html).root.walk())
        default = next((n.attrs.get('value') for n in nodes if n.tag == 'input' and n.attrs.get('name') == 'ZoekOp_Geldigheidsdatum'), None)
        if not default:
            raise ValueError('荷兰公开搜索表单缺少默认有效日期')
        stamp = date.fromisoformat(default).strftime('%d-%m-%Y')
        # 正常提交官方搜索表单后公开返回的 GET 路径；日期来自当次表单，不固定某部法规。
        results = 'https://wetten.overheid.nl/zoeken/zoekresultaat/rs/2/titelf/0/tekstf/0/d/' + stamp + '/dx/0'
        html, final = _page(results, fetch, self.allowed_hosts[0])
        found = [u for u in Links(html, final).urls if _official(u, self.allowed_hosts[0]) and re.fullmatch(r'/BWBR\d+/\d{4}-\d{2}-\d{2}/\d+', urlsplit(u).path)]
        if not found:
            raise ValueError('荷兰法规搜索未返回已识别正式法规详情')
        return found[:limit]

    def read_document(self, url, fetch):
        html, final = _page(url, fetch, self.allowed_hosts[0])
        match = re.fullmatch(r'/((?:BWBR)\d+)/(\d{4}-\d{2}-\d{2})/\d+', urlsplit(final).path)
        nodes = list(_Document(html).root.walk())
        props = {n.attrs.get('name'):n.attrs.get('content') for n in nodes if n.tag=='meta'}
        title, number, kind = props.get('dcterms:title'), props.get('dcterms:identifier'), props.get('dcterms:type')
        body = next((n for n in nodes if n.attrs.get('id')=='regeling'),None)
        if (not match or number!=match[1] or not title or not kind or body is None
                or not any('artikel' in n.attrs.get('class','').split() for n in body.walk())):
            raise ValueError('荷兰页面缺少法规身份与可确认的 regeling 条文正文；POST 导出不作为替代证据')
        md=unknown_metadata(final)
        md['title']=field(title,title,'dcterms:title',final,'官方法规标题。')
        md['document_type']=field('regulation',kind,'dcterms:type 与 BWBR 条文正文',final,'官方成文法规；效力未核验。')
        md['legal_identity']=field(dict(jurisdiction='NL',issuer=props.get('overheid:authority'),type=kind,number=number,year=None),
            number,'dcterms:identifier',final,'官方BWBR法规身份。','partial')
        modified=props.get('dcterms:modified')
        if modified:
            date.fromisoformat(modified)
            md['updated_at']=field(modified,modified,'dcterms:modified',final,'官网记录更新时间，不替代公布日期。')
        md['text_version']=field(match[2],match[2],'BWBR 版本路径',final,'官方版本路径日期，不当作公布日期。')
        md['text_body_confirmed']=field(True,'#regeling 包含 artikel 条文','官方正文结构',final,'确认HTML中包含法规正文。')
        md['text_body_selector']=field('#regeling','#regeling','正文容器',final,'仅该容器转Markdown。')
        return Document(final,[final],md)

    read_catalog_entry = read_document


ADAPTERS = {adapter.platform: adapter for adapter in (LegifranceAdapter(), NormattivaAdapter(), WettenAdapter())}
