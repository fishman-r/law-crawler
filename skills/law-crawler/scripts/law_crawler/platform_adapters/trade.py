"""贸易部正式法规发现；详情身份和正文附件须独立核实。"""
import re
from urllib.parse import unquote, urldefrag, urljoin, urlsplit
from law_crawler.regulation_metadata import _Document, _pick, _table_pairs, extract_metadata
from .base import Adapter, Document, Links, document_url, field, text_html


_FORMAL_KIND = r'(?:Peraturan\s+(?:Menteri|Pemerintah|Presiden)\b|Undang[- ]Undang\b)'
_SUPPLEMENT = re.compile(r'(?:^|[\W_])(?:abstrak|abstract|faq|rancangan|draft)(?:$|[\W_])', re.I)


class TradeAdapter(Adapter):
    platform = 'kemendag.go.id'
    entry_urls = ('https://jdih.kemendag.go.id/',)
    allowed_hosts = ('jdih.kemendag.go.id',)

    def discover(self, fetch, limit):
        body, final = fetch(self.entry_urls[0])
        return [url for url in Links(text_html(body), final).urls
                if urlsplit(url).hostname in self.allowed_hosts
                and re.match(r'^/peraturan/peraturan-[^/]+$', urlsplit(url).path)][:limit]

    def read_document(self, url, fetch):
        body, final = fetch(url)
        if urlsplit(final).hostname not in self.allowed_hosts:
            raise ValueError('贸易部详情跳转到未允许的官方主机')
        html = text_html(body)
        nodes = list(_Document(html).root.walk())
        pairs = _table_pairs(nodes)
        metadata = extract_metadata(html, final, self.platform)
        title = metadata['title']['value'] or ''
        # 只读取标题开头的本法规身份，避免把正文引用的旧法或误标详情视为原件。
        if not re.match(r'^' + _FORMAL_KIND + r'.*?\s+Nomor\s+[\w./-]+\s+Tahun\s+\d{4}\b', title, re.I):
            raise ValueError('贸易部详情未确认正式法规标题与编号，拒绝草案、裁决、FAQ 或目录')
        _, doc_type = _pick(pairs, ['Tipe Dokumen', 'Tipe'])
        kind_label, kind = _pick(pairs, ['Jenis / Bentuk Peraturan', 'Jenis Peraturan', 'Jenis'])
        if doc_type and not re.match(r'^Peraturan(?:\s|$)', doc_type, re.I):
            raise ValueError('贸易部详情文献类型不是正式法规')
        if kind and (not re.match(r'^' + _FORMAL_KIND, kind, re.I)
                     or re.search(r'\b(?:rancangan|draft|putusan|keputusan|faq|abstrak)\b', kind, re.I)):
            raise ValueError('贸易部详情法规类型不是正式法规')
        metadata['document_type'] = field(
            'regulation', kind or title, kind_label or metadata['title']['source_label'],
            final, '官方详情的正式法规类型和标题身份；尚未核实现行效力。')
        files = []
        for node in nodes:
            attr = {'a': 'href', 'iframe': 'src', 'embed': 'src', 'object': 'data'}.get(node.tag)
            if not attr or not node.attrs.get(attr):
                continue
            candidate = urldefrag(urljoin(final, node.attrs[attr]))[0]
            path = unquote(urlsplit(candidate).path)
            filename = path.rsplit('/', 1)[-1]
            if (urlsplit(candidate).hostname in self.allowed_hosts and document_url(candidate)
                    # 官网页脚的前端应用 ZIP 不是本条法规正文或附件。
                    and not path.casefold().startswith('/frontend-new/')
                    and not any(_SUPPLEMENT.search(value) for value in
                                (node.text(), node.attrs.get('title', ''), filename))
                    and candidate not in files):
                files.append(candidate)
        return Document(final, files, metadata)


ADAPTERS = {'kemendag.go.id': TradeAdapter()}
