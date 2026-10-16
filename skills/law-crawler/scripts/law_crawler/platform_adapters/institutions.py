"""MK、MA、DPR 的正式法规入口；不把裁决、草案、摘要或目录卡片当作正文。

MA 的 Kepaniteraan 公开 PERMA 栏目作为同机关备用来源。MK、DPR 主入口若被
403/挑战页阻断，会如实失败，不使用固定 PDF 或搜索结果冒充列表发现结果。
"""
import re
from urllib.parse import parse_qs, quote, urlencode, urldefrag, urljoin, urlsplit

from law_crawler.regulation_metadata import _Document, _Node, _normalize_date, extract_metadata
from .base import document_url, Adapter, Document, Links, field, text_html


def _nodes(html):
    return list(_Document(html).root.walk())


def _pairs(nodes):
    pairs = {}
    for node in nodes:
        if node.tag != 'tr':
            continue
        cells = [c for c in node.children if isinstance(c, _Node) and c.tag in ('td', 'th')]
        if len(cells) == 3 and cells[1].text().strip() == ':':
            cells.pop(1)
        if len(cells) == 2:
            pairs.setdefault(cells[0].text().strip(' :'), cells[1].text())
    return pairs


def _pick(pairs, *labels):
    for label in labels:
        if pairs.get(label):
            return label, pairs[label]
    return None, None


def _url(base, value):
    """保留原有百分号与查询参数；仅编码网页 href 中不能直接请求的空格。"""
    return quote(urldefrag(urljoin(base, value))[0], safe=':/?#[]@!$&\'()*+,;=%')


def _pdf_links(nodes, final, hosts):
    result = []
    for node in nodes:
        attr = {'a': 'href', 'iframe': 'src', 'embed': 'src', 'object': 'data'}.get(node.tag)
        candidate = node.attrs.get(attr) if attr else None
        if not candidate:
            continue
        url = _url(final, candidate)
        parts = urlsplit(url)
        # 目录卡片和摘要即使是 PDF 也不是正文。只排除附件本身的标签/文件名，
        # 不因正式法规标题讨论 Rancangan 或 Putusan 而误判文档种类。
        label = node.text().strip().lower()
        filename = parts.path.rsplit('/', 1)[-1].lower()
        if (parts.scheme not in ('http', 'https') or parts.hostname not in hosts
                or not document_url(url)
                or re.search(r'^(?:unduh\s+)?(?:abstrak|abstract|katalog|rancangan|draft)\b', label)
                or re.search(r'(?:abstrak|abstract|katalog|rancangan|draft|\dabsper)', filename)):
            continue
        if url not in result:
            result.append(url)
    return result


def _identity(metadata, url, kind, issuer, title, pairs=None):
    pairs = pairs or {}
    number_label, number = _pick(pairs, 'Nomor', 'Nomor Peraturan')
    year_label, year = _pick(pairs, 'Tahun', 'Tahun Peraturan')
    # 编号只能来自标题开头的本法身份，不从 Tentang 后的被修改旧法中提取。
    match = re.match(r'^(?:PMK|PERMA|Peraturan\s+.+?)\s+(?:(?:Nomor|No\.?)\s+)?(\d+)\s+Tahun\s+(\d{4})\b', title, re.I)
    if match:
        number, year = number or match.group(1), year or match.group(2)
    value = dict(jurisdiction='ID', issuer=issuer, type=kind, number=number, year=year)
    metadata['legal_identity'] = field(
        value, dict(title=title, source_fields=pairs), '官方法规类别、标题、详情表与机关域名', url,
        '法规身份；所属国家 ID 不表示全国适用，现行效力尚未核实。',
        'observed' if all(value.values()) else 'partial')
    metadata['document_type'] = field('regulation', kind, '官方法规类别', url,
                                      '法规资料；未将司法裁决、草案、摘要或目录卡片视为法规正文。')
    metadata['legal_status'] = 'not_verified'
    return metadata


def _detail_metadata(html, final, platform, nodes, allowed_types):
    metadata = extract_metadata(html, final, platform)
    pairs = _pairs(nodes)
    type_label, kind = _pick(pairs, 'Jenis', 'Jenis Peraturan', 'Bentuk Peraturan')
    if not kind or kind.casefold() not in {t.casefold() for t in allowed_types}:
        raise ValueError('官方详情未确认正式法规类型，拒绝草案、裁决、摘要或文章')
    label, title = _pick(pairs, 'Judul', 'Judul Peraturan')
    if not title:
        raise ValueError('法规详情缺少可确认的标题')
    metadata['title'] = field(title, title, label, final, '官方详情页法规标题。')
    for name, labels in [('enacted_at', ('Tanggal Penetapan', 'Tanggal Ditetapkan')),
                         ('promulgated_at', ('Tanggal Pengundangan', 'Tanggal Diundangkan'))]:
        label, raw = _pick(pairs, *labels)
        if raw:
            value = _normalize_date(raw)
            metadata[name] = field(value, raw, label, final,
                                   '法规制定日期。' if name == 'enacted_at' else '法规公布日期（Pengundangan）。',
                                   'observed' if value else ('not_provided' if raw in ('-', '—') else 'unparsed'))
    metadata['published_at'] = dict(metadata['promulgated_at'])
    metadata['published_at']['meaning'] = '按法规公布日期记录；不是网页发布日或下载时间。'
    _, issuer = _pick(pairs, 'Pemrakarsa', 'T.E.U.', 'Tajuk Entri Utama (T.E.U)',
                      'Tajuk Entri Utama (T.E.U.)', 'T.E.U. Badan/Pengarang')
    return _identity(metadata, final, kind, issuer, title, pairs)


class MKAdapter(Adapter):
    platform = 'mkri.id'
    entry_urls = ('https://www.mkri.id/peraturan/peraturan-mk',)
    allowed_hosts = ('mkri.id', 'www.mkri.id', 's.mkri.id', 'jdih.mkri.id', 'arsipds.mkri.id')

    def _rows(self, html, final):
        rows = []
        for row in _nodes(html):
            if row.tag != 'tr':
                continue
            cells = [c for c in row.children if isinstance(c, _Node) and c.tag == 'td']
            if len(cells) < 2:
                continue
            title = cells[0].text()
            if not re.match(r'^PMK\s+(?:Nomor|No\.?)\s+\d+\s+Tahun\s+\d{4}\b', title, re.I):
                continue
            pdfs = [u for u in _pdf_links(list(row.walk()), final, self.allowed_hosts)
                    if '/public/content/pmk/' in urlsplit(u).path.lower()]
            if pdfs:
                rows.append((title, cells[1].text(), pdfs))
        return rows

    def discover(self, fetch, limit):
        if limit <= 0:
            return []
        body, final = fetch(self.entry_urls[0])
        # 官网列表没有逐条详情路由，使用其已公开的 search 参数锁定本行，
        # 将可重新访问的官方筛选页作为详情证据，而不是制造本地 URL。
        return list(dict.fromkeys(self.entry_urls[0] + '?' + urlencode({'search': title})
                                  for title, _, _ in self._rows(text_html(body), final)))[:limit]

    def read_document(self, url, fetch):
        search = parse_qs(urlsplit(url).query).get('search', [None])[0]
        if not search:
            raise ValueError('MK 详情须为官网按法规标题筛选的列表页')
        body, final = fetch(url)
        matches = [row for row in self._rows(text_html(body), final) if row[0] == search]
        if len(matches) != 1:
            raise ValueError('MK 列表未返回唯一匹配的 PMK 正式法规')
        short_title, about, pdfs = matches[0]
        title = short_title + (' — ' + about if about else '')
        metadata = extract_metadata('', final, self.platform)
        metadata['title'] = field(title, dict(Judul=short_title, Tentang=about),
                                  '官方 PMK 列表的 Judul / Tentang', final, '官方法规列表标题与主题。')
        return Document(final, pdfs, _identity(metadata, final, 'Peraturan Mahkamah Konstitusi',
                                               'Mahkamah Konstitusi', title))


class MAAdapter(Adapter):
    platform = 'mahkamahagung.go.id'
    entry_urls = (
        'https://jdih.mahkamahagung.go.id/dokumen?jenis%5B%5D=peraturan-mahkamah-agung',
        'https://kepaniteraan.mahkamahagung.go.id/id/profil-kepaniteraan/profile-sdm/kepaniteraan-ma?id=16&view=category',
    )
    allowed_hosts = ('jdih.mahkamahagung.go.id', 'kepaniteraan.mahkamahagung.go.id')

    def discover(self, fetch, limit):
        if limit <= 0:
            return []
        errors = []
        for entry in self.entry_urls:
            try:
                body, final = fetch(entry)
                result = []
                for node in _nodes(text_html(body)):
                    if node.tag != 'a' or not node.attrs.get('href'):
                        continue
                    link = _url(final, node.attrs['href'])
                    parts = urlsplit(link)
                    if parts.hostname == self.allowed_hosts[0]:
                        accepted = bool(re.fullmatch(r'/legal-product/(?:perma-|peraturan-mahkamah-agung-)[^/]+/detail', parts.path))
                    elif parts.hostname == self.allowed_hosts[1]:
                        query = parse_qs(parts.query)
                        accepted = (query.get('view') == ['article']
                                    and query.get('catid', [''])[0].split(':')[0] == '16'
                                    and re.match(r'^PERMA\b', node.text(), re.I))
                    else:
                        accepted = False
                    if accepted and link not in result:
                        result.append(link)
                if result:
                    return result[:limit]
                errors.append(f'{entry}: 未发现 PERMA 详情')
            except Exception as exc:
                errors.append(f'{entry}: {exc}')
        raise ValueError('；'.join(errors))

    def read_document(self, url, fetch):
        body, final = fetch(url)
        html, host = text_html(body), urlsplit(final).hostname
        nodes = _nodes(html)
        if host == self.allowed_hosts[0]:
            metadata = _detail_metadata(html, final, self.platform, nodes, ('Peraturan Mahkamah Agung',))
            pdfs = [u for u in Links(html, final).urls
                    if urlsplit(u).hostname == host
                    and re.fullmatch(r'/legal-product/[^/]+/download/dokumen', urlsplit(u).path)]
        elif host == self.allowed_hosts[1]:
            title_node = next((n for n in nodes if n.tag == 'h1' and 'article-title' in n.attrs.get('class', '').split()), None)
            article = next((n for n in nodes if n.attrs.get('itemprop') == 'articleBody'), None)
            title = title_node.text() if title_node else ''
            if not article or not re.match(r'^PERMA\b', title, re.I):
                raise ValueError('Kepaniteraan 页面未确认 PERMA 正式法规正文')
            metadata = extract_metadata('', final, self.platform)
            metadata['title'] = field(title, title, 'h1.article-title', final, '最高法院书记处法规页面标题。')
            metadata = _identity(metadata, final, 'Peraturan Mahkamah Agung', 'Mahkamah Agung', title)
            # 页面显示的笼统 Tanggal 和文章发布日期不推断为法规公布日期。
            pdfs = _pdf_links(list(article.walk()), final, self.allowed_hosts)
        else:
            raise ValueError('非已适配的最高法院官方主机')
        if not pdfs:
            raise ValueError('正式法规详情未提供正文文件')
        return Document(final, list(dict.fromkeys(pdfs)), metadata)


class DPRAdapter(Adapter):
    platform = 'dpr.go.id'
    entry_urls = ('https://jdih.dpr.go.id/setjen/index/id/Peraturan-DPR-BAGIAN-PERTIMBANGAN-DAN-DOKUMENTASI-INFORMASI-HUKUM',)
    allowed_hosts = ('jdih.dpr.go.id', 'berkas.dpr.go.id', 'www.dpr.go.id', 'dpr.go.id')

    def discover(self, fetch, limit):
        if limit <= 0:
            return []
        body, final = fetch(self.entry_urls[0])
        return [u for u in Links(text_html(body), final).urls
                if urlsplit(u).hostname == 'jdih.dpr.go.id'
                and re.match(r'^/setjen/detail-dokumen/tipe/peraturan_dpr/(?:id/)?[^/]+/?$', urlsplit(u).path)][:limit]

    def read_document(self, url, fetch):
        body, final = fetch(url)
        html = text_html(body)
        nodes = _nodes(html)
        metadata = _detail_metadata(html, final, self.platform, nodes,
                                    ('Peraturan DPR', 'Peraturan DPR RI', 'Peraturan Dewan Perwakilan Rakyat'))
        # 页面也包含 UNDUH ABSTRAK / Rancangan，正文只取明确的 UNDUH BERKAS。
        primary = [n for n in nodes if n.tag == 'a' and n.text().strip().upper() == 'UNDUH BERKAS']
        pdfs = _pdf_links(primary, final, self.allowed_hosts)
        if not pdfs:
            raise ValueError('DPR 详情未提供 UNDUH BERKAS 正文文件；不以摘要或目录代替')
        return Document(final, pdfs, metadata)


ADAPTERS = {'mkri.id': MKAdapter(), 'mahkamahagung.go.id': MAAdapter(), 'dpr.go.id': DPRAdapter()}
