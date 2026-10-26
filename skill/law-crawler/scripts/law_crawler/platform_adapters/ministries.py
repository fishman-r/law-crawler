"""卫生部、财政部、投资部的公开 JDIH 列表与正文适配器。"""
import re
from urllib.parse import parse_qs, urldefrag, urljoin, urlsplit

from law_crawler.regulation_metadata import _Document, _Node, _normalize_date, extract_metadata
from .base import document_url, Adapter, Document, Links, field, text_html


def _pairs(nodes):
    """兼容表格、带 div 的 dl，以及财政部的 dok-item 字段行。"""
    result = {}
    for node in nodes:
        children = [c for c in node.children if isinstance(c, _Node)]
        if node.tag == 'tr':
            cells = [c for c in children if c.tag in ('td', 'th')]
        elif 'dok-item' in node.attrs.get('class', '').split():
            cells = children
        else:
            cells = [c for c in children if c.tag in ('dt', 'dd')]
        if len(cells) == 2 and cells[0].text():
            result.setdefault(cells[0].text(), cells[1].text())
    return result


def _pick(pairs, *labels):
    for label in labels:
        if label in pairs:
            return label, pairs[label]
    return None, None


def _date(raw):
    if not raw:
        return None
    # 两个 JDIH 页面使用印尼语月份缩写；原文仍由 provenance.raw 保留。
    months = {'jan': 'Januari', 'feb': 'Februari', 'mar': 'Maret',
              'apr': 'April', 'mei': 'Mei', 'jun': 'Juni', 'jul': 'Juli',
              'agu': 'Agustus', 'agt': 'Agustus', 'ags': 'Agustus',
              'sep': 'September', 'okt': 'Oktober', 'nov': 'November', 'des': 'Desember'}
    parts = raw.split()
    if len(parts) == 3:
        parts[1] = months.get(parts[1].lower().rstrip('.'), parts[1])
        return _normalize_date(' '.join(parts))
    return _normalize_date(raw)


def _metadata(html, url, platform, nodes):
    result = extract_metadata(html, url, platform)
    pairs = _pairs(nodes)
    type_label, doc_type = _pick(pairs, 'Tipe Dokumen', 'Tipe')
    kind_label, kind = _pick(pairs, 'Jenis/Bentuk Peraturan', 'Jenis Peraturan', 'Jenis Dokumen')
    if not (doc_type and doc_type.lower().startswith('peraturan')
            and kind and kind.lower().startswith(('peraturan', 'undang-undang'))):
        raise ValueError('详情页未提供可确认的正式法规类型，拒绝混入草案、决定或文章')
    result['document_type'] = field('regulation', doc_type, type_label, url,
                                    '官方详情明确列为法规资料；不表示已核实现行效力。')
    title_label, title = _pick(pairs, 'Judul', 'Judul Dokumen')
    if title:
        result['title'] = field(title, title, title_label, url, '官方详情页法规标题。')
    for name, labels, meaning in [
            ('enacted_at', ('Tgl. Penetapan', 'Tanggal Penetapan'), '法规制定或签署日期；不是公布日期。'),
            ('promulgated_at', ('Tgl. Pengundangan', 'Tanggal Pengundangan'),
             '法规公布日期（Pengundangan）；不是网页发布日或下载时间。')]:
        label, raw = _pick(pairs, *labels)
        if label:
            value = _date(raw)
            status = 'observed' if value else ('not_provided' if raw in ('', '-', '—') else 'unparsed')
            result[name] = field(value, raw, label, url, meaning, status)
    result['published_at'] = dict(result['promulgated_at'])
    result['published_at']['meaning'] = '法规发布时间按官方公布日期（Pengundangan）记录；不是网页发布日或下载时间。'
    label, issuer = _pick(pairs, 'T.E.U Badan/Pengarang', 'T.E.U. (Tajuk Entri Utama)', 'Tajuk Entri Utama')
    number_label, number = _pick(pairs, 'No. Peraturan', 'Nomor', 'Nomor Peraturan')
    year_label, year = _pick(pairs, 'Tahun')
    year_raw = year
    if not year:
        match = re.search(r'\bTahun\s+(\d{4})\b', title or '', re.I)
        if match:
            year_label, year, year_raw = title_label or 'Judul', match.group(1), title
    identity = dict(jurisdiction='ID', issuer=issuer, type=kind, number=number, year=year)
    raw_identity = {k: v for k, v in [(kind_label, kind), (label, issuer),
                                     (number_label, number), (year_label, year_raw)] if k}
    result['legal_identity'] = field(identity, raw_identity, '官方详情元数据及来源域名', url,
                                     '法规身份；所属国家 ID 不等同于全国适用，效力尚未核实。',
                                     'observed' if all(identity.values()) else 'partial')
    return result


class MinistryAdapter(Adapter):
    def __init__(self, platform, entry, detail_pattern, hosts):
        self.platform = platform
        self.entry_urls = (entry,)
        self.allowed_hosts = tuple(hosts)
        self._detail_host = urlsplit(entry).hostname
        self._detail_pattern = re.compile(detail_pattern, re.I)

    def discover(self, fetch, limit):
        if limit <= 0:
            return []
        body, final = fetch(self.entry_urls[0])
        return [url for url in Links(text_html(body), final).urls
                if urlsplit(url).hostname == self._detail_host
                and self._detail_pattern.fullmatch(urlsplit(url).path)][:limit]

    def read_document(self, url, fetch):
        body, final = fetch(url)
        html = text_html(body)
        nodes = list(_Document(html).root.walk())
        metadata = _metadata(html, final, self.platform, nodes)
        pdfs = []
        for node in nodes:
            candidate = None
            if node.tag == 'a' and not re.search(r'\babstrak\b|\babstract\b', node.text(), re.I):
                candidate = node.attrs.get('href')
            elif node.tag in ('iframe', 'embed'):
                candidate = node.attrs.get('src')
                if candidate and urlsplit(candidate).path.endswith('/viewer.html'):
                    candidate = parse_qs(urlsplit(candidate).query).get('file', [None])[0]
            if not candidate:
                continue
            candidate = urldefrag(urljoin(final, candidate))[0]
            parsed = urlsplit(candidate)
            # 财政部旧法规正文可以是 HTML；只接受详情明确给出的 fulltext
            # 下载入口，不把导航页、关联文章或猜出的文件名当作原件。
            fulltext = (self.platform == 'kemenkeu.go.id'
                        and parsed.path.startswith('/api/download/fulltext/')
                        and parsed.path.lower().endswith(('.htm', '.html', '.txt', '.xml', '.json', '.pdf')))
            if (parsed.scheme in ('https', 'http') and parsed.hostname in self.allowed_hosts
                    and (document_url(candidate) or fulltext) and candidate not in pdfs):
                pdfs.append(candidate)
        if not pdfs:
            raise ValueError('官方法规详情未提供可验证的正文文件链接')
        return Document(final, pdfs, metadata)


ADAPTERS = {
    'kemkes.go.id': MinistryAdapter(
        'kemkes.go.id', 'https://jdih.kemkes.go.id/',
        r'/documents/(?:peraturan|undang-undang)-[^/]+/?', ('jdih.kemkes.go.id',)),
    'kemenkeu.go.id': MinistryAdapter(
        'kemenkeu.go.id', 'https://jdih.kemenkeu.go.id/home',
        r'/dok/(?:pmk|pp|perpres|uu|perppu)-[^/]+/?', ('jdih.kemenkeu.go.id',)),
    'bkpm.go.id': MinistryAdapter(
        'bkpm.go.id', 'https://jdih.bkpm.go.id/id',
        r'/id/document/(?:peraturan|undang-undang)-[^/]+/?',
        ('jdih.bkpm.go.id', 'jdih-storage.bkpm.go.id')),
}
