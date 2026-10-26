"""MK、MA、DPR 的正式法规入口；不把裁决、草案、摘要或目录卡片当作正文。

MA 的 Kepaniteraan 公开 PERMA 栏目作为同机关备用来源。MK、DPR 主入口若被
403/挑战页阻断，会如实失败，不使用固定 PDF 或搜索结果冒充列表发现结果。
"""
import re
import hashlib
from copy import deepcopy
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

    # 同一官方目录分别筛选文种；SEMA 是司法指导文件，不标成中国法的司法解释。
    bulk_kinds = {
        'peraturan-mahkamah-agung': ('perma', 'Peraturan Mahkamah Agung'),
        'surat-edaran-mahkamah-agung': ('sema', 'Surat Edaran Mahkamah Agung'),
    }

    def discover_bulk(self, fetch, max_pages=1000, checkpoint=None, on_page=None):
        """遍历官网已公开的分页链接；返回可持久化断点，绝不据页数推算总量。

        max_pages 是本次请求上限；失败、重复页、无分页证据均保留为未穷尽。
        on_page 接收整个 JSON 可序列化断点快照，可在每次请求后原子保存。
        旧 discover 仍是有限首屏发现，不用于声称全量。
        """
        if type(max_pages) is not int or max_pages < 1:
            raise ValueError('max_pages 必须为正整数')
        base = 'https://jdih.mahkamahagung.go.id'
        state = deepcopy(checkpoint) if checkpoint is not None else {
            'version': 1, 'platform': self.platform, 'details': [], 'pages': [],
            'streams': {kind: {'pending_urls': [base + '/dokumen?' + urlencode({'jenis[]': kind})],
                               'visited_urls': [], 'fingerprints': [], 'stop_reasons': []}
                        for kind in self.bulk_kinds}}
        if (state.get('version') != 1 or state.get('platform') != self.platform
                or set(state.get('streams', {})) != set(self.bulk_kinds)):
            raise ValueError('MA bulk checkpoint 类型或版本不符')

        def page_key(url, kind):
            parts = urlsplit(url)
            query = parse_qs(parts.query)
            types = [v for key, values in query.items() if re.fullmatch(r'jenis\[(?:\d+)?\]', key) for v in values]
            if (parts.scheme != 'https' or parts.hostname != 'jdih.mahkamahagung.go.id'
                    or parts.path != '/dokumen' or types != [kind]
                    or set(query) - {'jenis[]', 'jenis[0]', 'page'}
                    or parts.username or parts.password or parts.port not in (None, 443)):
                return None
            number = query.get('page', ['1'])
            if len(number) != 1 or not re.fullmatch(r'[1-9]\d*', number[0]):
                return None
            return base + '/dokumen?' + urlencode({'jenis[0]': kind, 'page': int(number[0])})

        requests = 0
        for kind, stream in state['streams'].items():
            # 仅网络失败可在断点重跑时重试；结构歧义需人工处理，不能悄悄清除。
            stream['stop_reasons'] = [r for r in stream['stop_reasons'] if r != 'fetch_failed']
            pending = stream['pending_urls']
            stream['visited_urls'] = list(dict.fromkeys(page_key(u, kind) for u in stream['visited_urls']))
            if None in stream['visited_urls']:
                raise ValueError('MA checkpoint 含非官方分页地址')
            pending[:] = list(dict.fromkeys(page_key(u, kind) for u in pending))
            if None in pending:
                raise ValueError('MA checkpoint 含非官方待访问地址')
            while pending and requests < max_pages:
                url = pending[0]
                requests += 1
                row = {'url': url, 'kind': kind, 'status': 'running'}
                try:
                    body, final = fetch(url)
                    if page_key(final, kind) != url:
                        raise ValueError('MA 官方分页跳转或筛选条件改变')
                    html = text_html(body)
                    links = Links(html, final).urls
                    prefix = self.bulk_kinds[kind][0]
                    details = list(dict.fromkeys(u for u in links
                        if urlsplit(u).scheme == 'https' and urlsplit(u).hostname == 'jdih.mahkamahagung.go.id'
                        and re.fullmatch(r'/legal-product/' + prefix + r'-[^/]+/detail', urlsplit(u).path)
                        and not urlsplit(u).query and not urlsplit(u).username and not urlsplit(u).password))
                    pages = list(dict.fromkeys(key for u in links if (key := page_key(u, kind))))
                    fingerprint = hashlib.sha256('\n'.join(sorted(details)).encode()).hexdigest()
                    empty = bool(re.search(r'(?:Tidak\s+(?:ada|ditemukan)\s+(?:dokumen|data)|data\s+tidak\s+ditemukan)', html, re.I))
                    row.update(final_url=final, sha256=hashlib.sha256(body).hexdigest(),
                               detail_urls=details, pagination_urls=pages, fingerprint=fingerprint)
                    if not details and not empty:
                        raise ValueError('未确认目录结果或官方空态，不能判定穷尽')
                    if details and fingerprint in stream['fingerprints']:
                        row['status'] = 'repeated_page'
                        stream['stop_reasons'].append('repeated_page')
                        pending.pop(0)
                        stream['visited_urls'].append(url)
                    else:
                        row['status'] = 'success'
                        pending.pop(0)
                        stream['visited_urls'].append(url)
                        stream['fingerprints'].append(fingerprint)
                        for detail in details:
                            if detail not in state['details']:
                                state['details'].append(detail)
                        for page in pages:
                            if page not in stream['visited_urls'] and page not in pending:
                                pending.append(page)
                        if details and not pages:
                            stream['stop_reasons'].append('pagination_unverified')
                    state['pages'].append(row)
                except Exception as exc:
                    row.update(status='failed', reason=str(exc))
                    state['pages'].append(row)
                    stream['stop_reasons'].append('fetch_failed')
                    break
                finally:
                    stream['stop_reasons'] = list(dict.fromkeys(stream['stop_reasons']))
                    state['complete'] = all(not s['pending_urls'] and not s['stop_reasons'] for s in state['streams'].values())
                    state['pending_urls'] = [u for s in state['streams'].values() for u in s['pending_urls']]
                    if on_page:
                        on_page(deepcopy(state))
        state['requests_this_run'] = requests
        state['complete'] = all(not s['pending_urls'] and not s['stop_reasons'] for s in state['streams'].values())
        state['pending_urls'] = [u for s in state['streams'].values() for u in s['pending_urls']]
        state['limit_reached'] = bool(state['pending_urls'] and requests >= max_pages)
        return state


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
            metadata = _detail_metadata(html, final, self.platform, nodes,
                                        ('Peraturan Mahkamah Agung', 'Surat Edaran Mahkamah Agung'))
            kind = metadata['legal_identity']['value']['type']
            if kind == 'Peraturan Mahkamah Agung' and not re.fullmatch(
                    r'/legal-product/(?:perma-|peraturan-mahkamah-agung-)[^/]+/detail', urlsplit(final).path):
                raise ValueError('PERMA 类型与官方详情路径不一致')
            if kind == 'Surat Edaran Mahkamah Agung':
                if not re.fullmatch(r'/legal-product/sema-[^/]+/detail', urlsplit(final).path):
                    raise ValueError('SEMA 类型与官方详情路径不一致')
                metadata['document_type'] = field('judicial_guidance', kind, '官方司法指导文种', final,
                    '印度尼西亚最高法院通函 SEMA；不等同于中国法司法解释，效力尚未核实。')
                title = metadata['title']['value']
                match = re.match(r'^(?:SEMA|Surat Edaran(?: Mahkamah Agung)?)\s+(?:Nomor\s+)?(\d+)\s+Tahun\s+(\d{4})\b', title, re.I)
                if match:
                    identity = metadata['legal_identity']['value']
                    identity['number'], identity['year'] = match.groups()
                    if all(identity.values()):
                        metadata['legal_identity']['status'] = 'observed'
            body_path = urlsplit(final).path.removesuffix('/detail') + '/download/dokumen'
            pdfs = [u for u in Links(html, final).urls
                    if urlsplit(u).hostname == host
                    and urlsplit(u).path == body_path
                    and urlsplit(u).scheme == 'https' and not urlsplit(u).query]
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
