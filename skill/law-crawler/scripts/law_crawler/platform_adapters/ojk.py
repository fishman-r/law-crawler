"""OJK 公开法规列表；区分 POJK 原文与摘要、FAQ 等附属资料。"""
import re
from urllib.parse import unquote, urlsplit

from law_crawler.regulation_metadata import _Document, extract_metadata
from .base import document_url, Adapter, Document, Links, field, text_html


class OJKAdapter(Adapter):
    platform = 'ojk.go.id'
    entry_urls = ('https://www.ojk.go.id/id/regulasi/Default.aspx',)
    allowed_hosts = ('www.ojk.go.id', 'ojk.go.id')

    def discover(self, fetch, limit):
        body, final = fetch(self.entry_urls[0])
        return [u for u in Links(text_html(body), final).urls
                if urlsplit(u).hostname in self.allowed_hosts
                and re.match(r'^/id/regulasi/Pages/POJK-[^/]+\.aspx$', urlsplit(u).path, re.I)][:limit]

    def read_document(self, url, fetch):
        body, final = fetch(url)
        html = text_html(body)
        metadata = extract_metadata(html, final, self.platform)
        nodes = list(_Document(html).root.walk())
        def by_class(name):
            return next((n.text() for n in nodes if name in n.attrs.get('class', '').split()), None)
        kind = by_class('jenis-regulasi-display')
        if kind != 'Peraturan OJK':
            raise ValueError(f'OJK 详情未确认正式 Peraturan OJK 类型: {kind!r}')
        number = by_class('nomor-regulasi-display')
        match = re.fullmatch(r'(\d+)\s+Tahun\s+(\d{4})', number or '')
        identity = dict(jurisdiction='ID', issuer='Otoritas Jasa Keuangan', type=kind,
                        number=match.group(1) if match else number,
                        year=match.group(2) if match else None)
        metadata['legal_identity'] = field(identity, {'Jenis Regulasi': kind, 'Nomor Regulasi': number},
                                           'Jenis Regulasi / Nomor Regulasi', final, 'OJK 官方详情身份，未核实现行效力。',
                                           'observed' if match else 'partial')
        metadata['document_type'] = field('regulation', kind, 'Jenis Regulasi', final, '正式法规资料。')
        # 日期显示由官网 JavaScript 格式化；保留原文，避免猜测斜线日期的月日顺序。
        effective = by_class('tanggal-2')
        metadata['effective_at'] = field(None, effective, 'Tanggal Berlaku', final,
                                         '生效日期，不是公布日期；官网斜线日期未明确顺序，保留原文。',
                                         'unparsed' if effective else 'not_provided')
        pdfs = [u for u in Links(html, final).urls
                if urlsplit(u).hostname in self.allowed_hosts
                and document_url(u)
                and re.match(r'POJK\b', unquote(urlsplit(u).path.rsplit('/', 1)[-1]), re.I)]
        return Document(final, pdfs, metadata)


ADAPTERS = {'ojk.go.id': OJKAdapter()}
