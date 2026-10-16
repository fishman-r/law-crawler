"""国家级法规入口：仅有限发现官方详情，不绕过 TLS、403 或访问控制。

这些入口在 2026-10-13 的本机网络测试中受 TLS/403 阻断；离线解析测试不代表
真实下载已通过。每次执行仍从官方列表发现链接，不使用固定法规或 PDF 兜底。
"""
import re
import json
from copy import copy
from html import escape
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urldefrag, urljoin, urlsplit

from .base import document_url, Adapter, Document, Links, extract_metadata, field, text_html


def _clean(value):
    return re.sub(r'\s+', ' ', value).strip(' \t\r\n:')


class _Node:
    def __init__(self, tag='', attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def text(self):
        return _clean(' '.join(c if isinstance(c, str) else c.text() for c in self.children))

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.walk()


class _Page(HTMLParser):
    """读取表格、定义列表及 BPK 标签/值布局；不执行脚本。"""
    _void = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
             'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in self._void:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def pairs(self):
        """原样保留标签，同时支持 JDIHN 的 label / ':' / value 三列表格。"""
        result = []
        for node in self.root.walk():
            if node.tag == 'tr':
                cells = [c for c in node.children if isinstance(c, _Node) and c.tag in {'th', 'td'}]
                if len(cells) == 2:
                    result.append((cells[0].text(), cells[1]))
                elif len(cells) == 3 and not cells[1].text():
                    result.append((cells[0].text(), cells[2]))
            if node.tag not in {'div', 'dl', 'li'}:
                continue
            children = [c for c in node.children if isinstance(c, _Node) and c.text()]
            if len(children) == 2 and children[0].text().casefold() in _LABELS:
                result.append((children[0].text(), children[1]))
            elif node.tag == 'dl':
                for label, value in zip(children, children[1:]):
                    if label.tag == 'dt' and value.tag == 'dd':
                        result.append((label.text(), value))
        return result


_LABELS = {x.casefold() for x in (
    'Judul', 'Judul Peraturan', 'Jenis', 'Jenis Peraturan', 'Jenis Dokumen', 'Tipe Dokumen',
    'Bentuk', 'Nomor', 'Nomor Peraturan', 'Tahun', 'Tahun Peraturan', 'T.E.U', 'T.E.U.',
    'T.E.U. Badan/Pengarang', 'Pemrakarsa', 'Instansi', 'Tanggal Penetapan',
    'Tanggal Pengundangan', 'Tanggal Publikasi', 'Tanggal Unggah', 'Tanggal Upload',
    'Tanggal Update', 'Terakhir Diperbarui', 'Wilayah Berlaku', 'Wilayah Penerapan',
    'Cakupan Wilayah', 'Daerah Berlaku', 'Unduhan', 'Unduhan-alternatif', 'Detil-dokumen')}


def _metadata(html, final, platform, page):
    pairs = page.pairs()
    # 转为同一解析器可读取的二列表格，不重命名原始日期标签。
    table = '<table>' + ''.join(
        '<tr><td>' + escape(label) + '</td><td>' + escape(node.text()) + '</td></tr>'
        for label, node in pairs) + '</table>'
    result = extract_metadata(table + html, final, platform)
    lookup = {label.casefold(): (label, node.text()) for label, node in pairs}
    identity = dict(result['legal_identity']['value'] or {})
    raw = dict(result['legal_identity']['raw'] or {})
    for key, labels in [('type', ('jenis peraturan', 'jenis', 'bentuk', 'jenis dokumen')),
                        ('issuer', ('t.e.u. badan/pengarang', 't.e.u.', 't.e.u', 'pemrakarsa', 'instansi'))]:
        for label in labels:
            if label in lookup:
                original, value = lookup[label]
                if value and value not in {'-', '–', '—'}:
                    identity[key] = value
                    raw[original] = value
                    break
    identity['jurisdiction'] = 'ID'
    raw['source_host'] = urlsplit(final).hostname
    result['legal_identity'] = field(
        identity, raw, '官方详情表、标题及来源域名', final,
        '法规身份字段；jurisdiction 是文献国家归属，不表示全国适用或现行有效。',
        'observed' if all(identity.get(key) for key in ('jurisdiction', 'issuer', 'type', 'number', 'year')) else 'partial')
    kind = identity.get('type') or ''
    title = result['title']['value'] or ''
    if re.search(r'\b(rancangan|putusan|keputusan|artikel|monografi)\b', kind, re.I) or re.match(
            r'^(rancangan|putusan|keputusan)\b', title, re.I):
        raise ValueError('详情属于草案、裁决或其他非本适配器法规类型')
    regulation_kind = r'^(?:peraturan\b|undang[- ]undang\b|perppu\b|perpu\b|uu\b|pp\b|perpres\b|permen\b|pmk\b)'
    if not any(re.match(regulation_kind, value, re.I) for value in (kind, title)):
        raise ValueError('官方详情未提供可识别的法规标题或类型')
    result['document_type'] = field('regulation', kind or '官方法规详情栏目',
                                    '资料分类', final, '正式法规资料；尚未核实现行效力。')
    return result


def _allowed(url, hosts):
    parts = urlsplit(url)
    return (parts.scheme in {'https', 'http'} and parts.hostname in hosts
            and not parts.username and not parts.password and parts.port in {None, 80, 443})


def _supplement(value):
    return bool(re.search(r'(?:^|[\W_])(?:abstrak|abstract|faq)(?:$|[\W_])|摘要', value, re.I))


def _original_links(page, final):
    """筛除页面明确标识的摘要/FAQ；附件 Lampiran 仍可能是法规组成部分。"""
    excluded_nodes = set()
    for label, node in page.pairs():
        if _supplement(label):
            excluded_nodes.update(id(child) for child in node.walk())
    result = []
    for node in page.root.walk():
        attribute = {'a': 'href', 'iframe': 'src', 'embed': 'src', 'object': 'data'}.get(node.tag)
        if not attribute or not node.attrs.get(attribute) or id(node) in excluded_nodes:
            continue
        link = urldefrag(urljoin(final, node.attrs[attribute]))[0]
        filename = unquote(urlsplit(link).path.rsplit('/', 1)[-1])
        if _supplement(node.text()) or _supplement(node.attrs.get('title', '')) or _supplement(filename):
            continue
        if link not in result:
            result.append(link)
    return result


class _NationalAdapter(Adapter):
    detail_pattern = ''

    def discover(self, fetch, limit):
        if limit <= 0:
            return []
        errors = []
        had_response = False
        for entry in self.entry_urls:
            try:
                body, final = fetch(entry)
                if not _allowed(final, self.allowed_hosts):
                    raise ValueError('官方入口跳转到未允许的主机')
                had_response = True
                details = [url for url in Links(text_html(body), final).urls
                           if _allowed(url, getattr(self, 'discovery_hosts', self.allowed_hosts))
                           and re.fullmatch(self.detail_pattern, urlsplit(url).path, re.I)]
                if details:
                    return details[:limit]
            except (OSError, ValueError) as exc:
                errors.append(f'{entry}: {exc}')
                if isinstance(exc, HTTPError):
                    exc.close()
        if not had_response and errors:
            raise OSError('官方入口访问失败；' + '；'.join(errors))
        return []

    def _read(self, url, fetch):
        if not _allowed(url, self.allowed_hosts):
            raise ValueError('法规详情 URL 不在允许的官方主机范围')
        body, final = fetch(url)
        if not _allowed(final, self.allowed_hosts):
            raise ValueError('法规详情跳转到未允许的主机')
        html = text_html(body)
        page = _Page(html)
        metadata = _metadata(html, final, self.platform, page)
        return final, html, page, metadata

    def read_document(self, url, fetch):
        final, _, page, metadata = self._read(url, fetch)
        pdfs = [u for u in _original_links(page, final) if _allowed(u, self.allowed_hosts)
                and document_url(u)]
        return Document(final, pdfs, metadata)


class PeraturanAdapter(_NationalAdapter):
    platform = 'peraturan.go.id'
    entry_urls = ('https://peraturan.go.id/', 'https://www.peraturan.go.id/')
    allowed_hosts = ('peraturan.go.id', 'www.peraturan.go.id')
    detail_pattern = r'/id/[^/]+'


class BPKAdapter(_NationalAdapter):
    platform = 'bpk.go.id'
    entry_urls = ('https://peraturan.bpk.go.id/',)
    allowed_hosts = ('peraturan.bpk.go.id',)
    detail_pattern = r'/Details/\d+(?:/[^/]+)?/?'

    def read_document(self, url, fetch):
        final, _, page, metadata = self._read(url, fetch)
        # BPK Read 是 HTML 预览，Download 才是原件；不把预览页当作 PDF。
        pdfs = [u for u in _original_links(page, final) if _allowed(u, self.allowed_hosts)
                and re.match(r'^/Download/\d+/', urlsplit(u).path, re.I)
                and document_url(u)]
        return Document(final, pdfs, metadata)


class JDIHNAdapter(_NationalAdapter):
    platform = 'jdihn.go.id'
    entry_urls = ('https://jdihn.go.id/pencarian', 'https://jdihn.go.id/')
    # 仅允许从 JDIHN 详情公开链接可确认的成员原件主机；不接受通配 *.go.id。
    member_hosts = ('jdih.kemendag.go.id', 'jdih.dephub.go.id', 'jdih.jatengprov.go.id')
    index_hosts = ('jdihn.go.id', 'www.jdihn.go.id')
    experimental_host = 'dev-jdihn.bphn.go.id'
    experimental_member_hosts = ('jdih.setneg.go.id',)
    # 允许读取已保存的实验来源元数据；自动发现仍必须显式启用。
    allowed_hosts = index_hosts + member_hosts + (experimental_host,) + experimental_member_hosts
    experimental_sources = False
    discovery_hosts = index_hosts
    detail_pattern = r'/(?:index\.php/)?pencarian/detail/\d+(?:/[^/]+)?/?'

    def with_experimental_sources(self):
        adapter = copy(self)
        adapter.experimental_sources = True
        adapter.production_discovery = None
        return adapter

    def discover(self, fetch, limit):
        if limit <= 0:
            return []
        try:
            details = super().discover(fetch, limit)
            if details or not self.experimental_sources:
                return details
            self.production_discovery = dict(status='no_details', entries=list(self.entry_urls))
        except OSError as exc:
            if not self.experimental_sources:
                raise
            self.production_discovery = dict(status='failed', entries=list(self.entry_urls), error=str(exc))
        entry = 'https://' + self.experimental_host + '/'
        body, final = fetch(entry)
        if not _allowed(final, (self.experimental_host,)):
            raise ValueError('实验 JDIHN 入口跳转到未允许的主机')
        return [u for u in Links(text_html(body), final).urls
                if _allowed(u, (self.experimental_host,))
                and re.fullmatch(r'/doc/\d+', urlsplit(u).path)][:limit]

    def _read_experimental(self, url, fetch):
        if not re.fullmatch(r'/doc/\d+', urlsplit(url).path):
            raise ValueError('实验 JDIHN 详情路由必须为 /doc/数字')
        body, final = fetch(url)
        if (not _allowed(final, (self.experimental_host,))
                or urlsplit(final).path != urlsplit(url).path):
            raise ValueError('实验 JDIHN 详情跳转到其他来源')
        # Next.js 公开 HTML 中的 JSON 字符串；严格 json.loads，不执行任何 JS。
        page = _Page(text_html(body))
        documents = []
        decoder = json.JSONDecoder()
        for node in page.root.walk():
            if node.tag != 'script':
                continue
            script = ''.join(c for c in node.children if isinstance(c, str)).strip()
            match = re.fullmatch(r'self\.__next_f\.push\((.*)\);?', script, re.S)
            if not match:
                continue
            try:
                payload = json.loads(match.group(1))
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, list) or len(payload) != 2 or not isinstance(payload[1], str):
                continue
            for found in re.finditer(r'"document":', payload[1]):
                try:
                    item, _ = decoder.raw_decode(payload[1], found.end())
                except ValueError:
                    continue
                if isinstance(item, dict) and str(item.get('id')) == urlsplit(final).path.rsplit('/', 1)[-1]:
                    if item not in documents:
                        documents.append(item)
        if len(documents) != 1:
            raise ValueError('实验 JDIHN 缺失或冲突的文献元数据')
        item = documents[0]
        if item.get('umbrellaType') != 'peraturan':
            raise ValueError('实验 JDIHN 文献不是法规类别')
        title = item.get('canonicalTitle') or item.get('documentNumber')
        kind = item.get('type')
        if not isinstance(title, str) or not isinstance(kind, str):
            raise ValueError('实验 JDIHN 缺失法规标题或类型')
        if any(re.search(r'\b(rancangan|draft|putusan|keputusan|artikel|monografi)\b', value, re.I)
               for value in (title, kind)):
            raise ValueError('实验 JDIHN 不接受草案或其他非正式法规')
        pairs = [('Judul', title), ('Jenis Dokumen', kind)]
        for key, label in [('enactedDate', 'Tanggal Penetapan'), ('promulgationDate', 'Tanggal Pengundangan')]:
            if isinstance(item.get(key), str) and item[key]:
                pairs.append((label, item[key]))
        html = '<table>' + ''.join('<tr><td>' + escape(k) + '</td><td>' + escape(v) + '</td></tr>'
                                  for k, v in pairs) + '</table>'
        metadata = _metadata(html, final, self.platform, _Page(html))
        # 表格只是复用解析器的内部表示；证据标签必须回到真实 JSON 字段。
        for name, key in [('title', 'canonicalTitle' if item.get('canonicalTitle') else 'documentNumber'),
                          ('enacted_at', 'enactedDate'), ('promulgated_at', 'promulgationDate'),
                          ('published_at', 'promulgationDate')]:
            if item.get(key):
                metadata[name]['source_label'] = '公开 HTML document.' + key
        metadata['document_type']['source_label'] = '公开 HTML document.umbrellaType/type'
        metadata['legal_identity']['source_label'] = '公开 HTML document 标题/type 及来源域名'
        metadata['source_route'] = field('experimental_official_fallback', self.experimental_host,
            'JDIHN 实验官方备用入口', final,
            'BPHN 的 dev 部署公开目录；正式 jdihn.go.id 未因此恢复。附件来自明确允许的官方成员。')
        metadata['experimental_source_document'] = field(json.dumps(item, ensure_ascii=False), item, '公开 HTML document JSON', final,
            '仅保存公开响应中的证据，不执行脚本；状态未经独立法律效力核验。')
        link = item.get('attachmentUrl')
        if not isinstance(link, str) or not _allowed(link, self.experimental_member_hosts):
            raise ValueError('实验 JDIHN 原件不属于已确认的官方成员')
        parts = urlsplit(link)
        query = parse_qs(parts.query)
        if (parts.scheme != 'https' or parts.path != '/api/hukumproduk/pdf'
                or query.get('l') != ['uploads'] or not query.get('fl')
                or len(query.get('f', [])) != 1 or not query['f'][0].lower().endswith('.pdf')):
            raise ValueError('实验 JDIHN 官方成员原件路由不匹配')
        return Document(final, [link], metadata)

    def read_document(self, url, fetch):
        if _allowed(url, (self.experimental_host,)):
            return self._read_experimental(url, fetch)
        if not _allowed(url, self.index_hosts):
            raise ValueError('JDIHN 文献来源必须是本平台索引详情')
        final, _, page, metadata = self._read(url, fetch)
        if not _allowed(final, self.index_hosts):
            raise ValueError('JDIHN 索引详情不能以成员页面替代')
        pdfs = []
        originals = set(_original_links(page, final))
        for label, node in page.pairs():
            if label.casefold() == 'detil-dokumen':
                for child in node.walk():
                    if child.tag == 'a' and child.attrs.get('href'):
                        member = urldefrag(urljoin(final, child.attrs['href']))[0]
                        if _allowed(member, self.member_hosts):
                            metadata['member_detail_url'] = field(member, child.attrs['href'], label,
                                                                 final, 'JDIHN 明确链接的原始成员详情页。')
                            break
            if label.casefold() not in {'unduhan', 'unduhan-alternatif'}:
                continue
            for child in node.walk():
                if child.tag != 'a' or not child.attrs.get('href'):
                    continue
                link = urldefrag(urljoin(final, child.attrs['href']))[0]
                if link not in originals or not _allowed(link, self.allowed_hosts):
                    continue
                parts = urlsplit(link)
                query = parse_qs(parts.query)
                download_route = (parts.hostname in self.index_hosts and
                                  re.fullmatch(r'/(?:index\.php/)?(?:file|pencarian)/download', parts.path)
                                  and query.get('id_anggota') and query.get('id_dokumen'))
                if (document_url(link) or download_route) and link not in pdfs:
                    pdfs.append(link)
        return Document(final, pdfs, metadata)


ADAPTERS = {adapter.platform: adapter for adapter in
            (PeraturanAdapter(), BPKAdapter(), JDIHNAdapter())}
