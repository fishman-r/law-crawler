"""德国联邦法律网：有界字母目录发现，优先官方 XML 压缩包。"""
import re
from urllib.parse import urlsplit

from crawl_store import unknown_metadata
from regulation_metadata import _Document
from .base import Adapter, Document, Links, field

HOSTS = ('www.gesetze-im-internet.de', 'gesetze-im-internet.de')
DETAIL = re.compile(r'/([a-z0-9_]+)/index\.html$', re.I)
CATALOG = re.compile(r'/Teilliste_[A-Z1-9]\.html$')


def _official(url):
    p = urlsplit(url)
    return (p.scheme == 'https' and p.hostname in HOSTS and not p.username
            and not p.password and p.port in (None, 443))


def _html(body):
    # 德国站仍明确声明 ISO-8859-1；不能沿用默认 UTF-8 而损坏德语标题。
    match = re.search(br'charset\s*=\s*["\']?([a-zA-Z0-9_-]+)', body[:4096], re.I)
    codec = match[1].decode('ascii').lower() if match else 'utf-8'
    if codec not in ('utf-8', 'iso-8859-1', 'windows-1252'):
        raise ValueError('德国官方页面使用未支持的字符编码')
    return body.decode(codec, errors='strict')


class GermanyAdapter(Adapter):
    platform = 'gesetze-im-internet.de'
    country = 'Germany'
    allowed_hosts = HOSTS
    entry_urls = ('https://www.gesetze-im-internet.de/aktuell.html',)

    def discover(self, fetch, limit):
        if limit <= 0:
            return []
        body, final = fetch(self.entry_urls[0])
        if not _official(final) or urlsplit(final).path != '/aktuell.html':
            raise ValueError('德国法规目录跳转到未确认来源')
        catalogs = [u for u in Links(_html(body), final).urls
                    if _official(u) and CATALOG.fullmatch(urlsplit(u).path)]
        found = []
        # 每次仅有界验证，不将整站目录/司法判决作为下载对象。
        for catalog in catalogs[:3]:
            body, final = fetch(catalog)
            if not _official(final) or not CATALOG.fullmatch(urlsplit(final).path):
                raise ValueError('德国字母目录跳转到未确认来源')
            for url in Links(_html(body), final).urls:
                if (_official(url) and DETAIL.fullmatch(urlsplit(url).path)
                        and not urlsplit(url).path.startswith('/englisch_') and url not in found):
                    found.append(url)
                    if len(found) >= limit:
                        return found
        return found

    def read_document(self, url, fetch):
        if not _official(url) or not DETAIL.fullmatch(urlsplit(url).path):
            raise ValueError('不是德国官方法规详情地址')
        body, final = fetch(url)
        if not _official(final) or not DETAIL.fullmatch(urlsplit(final).path):
            raise ValueError('德国法规详情跳转到未确认来源')
        html = _html(body)
        nodes = list(_Document(html).root.walk())
        title = next((n.text() for n in nodes if n.tag == 'h1'
                      and 'headline' in n.attrs.get('class', '').split()), None)
        directory = urlsplit(final).path.rsplit('/', 1)[0] + '/'
        links = [u for u in Links(html, final).urls if _official(u)
                 and urlsplit(u).path.rsplit('/', 1)[0] + '/' == directory]
        archives = [u for u in links if urlsplit(u).path == directory + 'xml.zip']
        pdfs = [u for u in links if urlsplit(u).path.lower().endswith('.pdf')]
        complete = [u for u in links if re.fullmatch(r'BJNR[a-zA-Z0-9]+\.html', urlsplit(u).path.split('/')[-1])]
        if not title or not complete or not (archives or pdfs):
            raise ValueError('德国详情缺少法规标题、官方 Gesamtausgabe 身份或原件链接')
        metadata = unknown_metadata(final)
        metadata['title'] = field(title, title, 'h1.headline', final, '官方法规完整标题，按页面声明字符编码读取。')
        metadata['document_type'] = field('regulation', 'Gesetze / Verordnungen', '官方法规目录', self.entry_urls[0], '联邦成文法规资料；不含司法判决，不代表现行法律效力核验。')
        identity = urlsplit(complete[0]).path.rsplit('/', 1)[-1][:-5]
        metadata['legal_identity'] = field(dict(jurisdiction='DE', issuer=None, type=None, number=identity, year=None), complete[0], 'Gesamtausgabe BJNR', final, '官方整部法规文献标识；不从网站主机推定适用地区。', 'partial')
        metadata['text_version'] = field('consolidated', 'Gesamtausgabe der Norm', 'h2.headline', final, '法律网整部法规汇编文本；具体修订截止日及原始公布日未在详情确认，保留未知。')
        metadata['available_formats'] = field(','.join(['xml.zip'] * bool(archives) + ['pdf'] * bool(pdfs)), archives + pdfs, '官方整部法规下载链接', final, '优先保留官方 XML ZIP 原件，PDF 为备选；ZIP 未改写或重新打包。')
        return Document(final, archives + pdfs, metadata)


ADAPTERS = {GermanyAdapter.platform: GermanyAdapter()}
