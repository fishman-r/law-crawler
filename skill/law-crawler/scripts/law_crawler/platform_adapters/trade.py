"""贸易部正式法规发现；详情身份和正文附件须独立核实。"""
from copy import deepcopy
import hashlib
import math
import re
from urllib.parse import parse_qsl, unquote, urldefrag, urlencode, urljoin, urlsplit, urlunsplit
from law_crawler.regulation_metadata import _Document, _pick, _table_pairs, extract_metadata
from .base import Adapter, Document, Links, document_url, field, text_html
from .census_minor import reject_access_error, missing_original_evidence


_FORMAL_KIND = r'(?:Peraturan\s+(?:Menteri|Pemerintah|Presiden)\b|Undang[- ]Undang\b)'
_SUPPLEMENT = re.compile(r'(?:^|[\W_])(?:abstrak|abstract|faq|rancangan|draft)(?:$|[\W_])', re.I)


def classify_file_roles(html, detail_url, urls):
    """按详情DOM逐URL声明角色；PDF隐藏文字不用于确定草案身份。"""
    root = _Document(html).root
    primary, annex = set(), set()
    for node in root.walk():
        target = primary if node.attrs.get('id') == 'peraturan-pane' else (annex if node.attrs.get('id') == 'lampiran-pane' else None)
        if target is None:
            continue
        for child in node.walk():
            attr = {'a': 'href', 'iframe': 'src', 'embed': 'src', 'object': 'data'}.get(child.tag)
            if attr and child.attrs.get(attr):
                target.add(urldefrag(urljoin(detail_url, child.attrs[attr]))[0])
    result = {}
    for url in urls:
        path = unquote(urlsplit(url).path).casefold()
        if re.fullmatch(r'/peraturan/download/[0-9a-f-]{36}/file_peraturan_en', path):
            role, context = 'official_translation', '官方 Peraturan [EN] 下载入口；不代替印尼语正文'
        elif '/pembentukan-puu/' in path:
            role, context = 'related_formation_document_pending', '立法形成资料入口；文件身份待核验，不能仅按路径断言草案'
        elif url in primary:
            role, context = 'regulation_body', '#peraturan-pane'
        elif url in annex:
            role, context = 'regulation_annex', '#lampiran-pane'
        else:
            role, context = 'related_or_unconfirmed_document_pending', '未在本条正文或附件面板确认；不得继承详情正式身份'
        result[url] = {'role': role, 'status': 'html_context_observed' if role in ('regulation_body', 'regulation_annex', 'official_translation') else 'pending_identity_review',
                       'detail_url': detail_url, 'html_context': context}
    return result


class TradeAdapter(Adapter):
    platform = 'kemendag.go.id'
    entry_urls = ('https://jdih.kemendag.go.id/',)
    allowed_hosts = ('jdih.kemendag.go.id',)

    def discover(self, fetch, limit):
        body, final = fetch(self.entry_urls[0])
        return [url for url in Links(text_html(body), final).urls
                if urlsplit(url).hostname in self.allowed_hosts
                and re.match(r'^/peraturan/peraturan-[^/]+$', urlsplit(url).path)][:limit]

    bulk_catalog_url = 'https://jdih.kemendag.go.id/peraturan'

    def discover_bulk(self, fetch, max_pages=1000, checkpoint=None, on_page=None):
        """逐个数字页归档混合正式文献目录，不把有缺口的 Next 链当完整分页。"""
        if type(max_pages) is not int or max_pages < 1:
            raise ValueError('max_pages 必须为正整数')
        base = urlsplit(self.bulk_catalog_url)
        filters = [(k, v) for k, v in parse_qsl(base.query, keep_blank_values=True) if k != 'page']
        if (base.scheme != 'https' or base.hostname not in self.allowed_hosts or base.path != '/peraturan'
                or base.username or base.password or base.port not in (None, 443)
                or set(k for k, _ in filters) - {'search', 'jenisreg', 'tahunreg', 'tematik', 'status', 'order'}):
            raise ValueError('非已登记的贸易部官方目录筛选')

        def page_url(number):
            return urlunsplit((base.scheme, base.netloc, base.path, urlencode(filters + [('page', number)]), ''))

        def page_number(url):
            parsed = urlsplit(url)
            query = parse_qsl(parsed.query, keep_blank_values=True)
            if (parsed.scheme != 'https' or parsed.hostname != base.hostname or parsed.path != base.path
                    or parsed.username or parsed.password or parsed.port not in (None, 443)
                    or sorted((k, v) for k, v in query if k != 'page') != sorted(filters)):
                return None
            pages = [v for k, v in query if k == 'page']
            return int(pages[0]) if len(pages) == 1 and re.fullmatch(r'[1-9]\d*', pages[0]) else (1 if not pages else None)

        state = deepcopy(checkpoint) if checkpoint is not None else {
            'version': 1, 'platform': self.platform, 'details': [], 'excluded_details': [], 'pages': [],
            'catalog_url': self.bulk_catalog_url, 'observed_total': None, 'last_page': None,
            'streams': {'peraturan': {'pending_urls': [page_url(1)], 'visited_urls': [],
                                     'fingerprints': [], 'stop_reasons': []}}}
        if (state.get('version') != 1 or state.get('platform') != self.platform
                or state.get('catalog_url') != self.bulk_catalog_url or set(state.get('streams', {})) != {'peraturan'}):
            raise ValueError('贸易部 bulk checkpoint 类型或筛选不符')
        stream = state['streams']['peraturan']
        if any(page_number(u) is None for u in stream['pending_urls'] + stream['visited_urls']):
            raise ValueError('贸易部 checkpoint 含非官方或筛选变化的分页地址')
        stream['stop_reasons'] = [r for r in stream['stop_reasons'] if r != 'fetch_failed']
        requests = 0
        while stream['pending_urls'] and requests < max_pages:
            url = stream['pending_urls'][0]
            number = page_number(url)
            row = {'url': url, 'page': number, 'status': 'running'}
            requests += 1
            try:
                body, final = fetch(url)
                if page_number(final) != number:
                    raise ValueError('贸易部分页跳转改变页码或筛选排序')
                page = _Document(text_html(body))
                text = page.root.text()
                order_select = next((n for n in page.root.walk() if n.tag == 'select' and n.attrs.get('name') == 'order'), None)
                options = [n for n in order_select.walk() if n.tag == 'option'] if order_select else []
                observed_order = next((n.attrs.get('value') for n in options if 'selected' in n.attrs), options[0].attrs.get('value') if options else None)
                observed_date = next((n.attrs.get('content') for n in page.root.walk() if n.tag == 'meta'
                    and (n.attrs.get('property') or n.attrs.get('name')) in ('article:modified_time', 'dateModified')), None)
                if 'observed_order' not in state:
                    state.update(observed_order=observed_order, observed_catalog_date=observed_date)
                elif (state['observed_order'] != observed_order or state['observed_catalog_date'] != observed_date):
                    stream['stop_reasons'].append('catalog_changed')
                    raise ValueError('目录排序或官方更新时间发生变化；本批次保持未穷尽')
                count = re.search(r'Menampilkan\s+([\d,.]+)\s+dari\s+([\d,.]+)\s+peraturan', text, re.I)
                if not count:
                    raise ValueError('未发现官方目录数量，不能判定穷尽')
                shown, total = [int(re.sub(r'[,\.]', '', n)) for n in count.groups()]
                links = Links(text_html(body), final).urls
                numbers = [n for u in links if (n := page_number(u)) is not None]
                observed_last = max(numbers + [number])
                if state['observed_total'] is None:
                    state['observed_total'], state['last_page'] = total, observed_last
                    if shown < 1 or observed_last != math.ceil(total / shown):
                        raise ValueError('总量与显式末页不一致；分页结构待确认')
                    stream['pending_urls'] = [page_url(n) for n in range(1, observed_last + 1)]
                elif total != state['observed_total'] or observed_last != state['last_page']:
                    stream['stop_reasons'].append('catalog_changed')
                    raise ValueError('目录总量或末页发生变化；本批次保持未穷尽')
                cards = [node for node in page.root.walk() if 'peraturan-card' in node.attrs.get('class', '').split()]
                details, excluded = [], []
                for card in cards:
                    card_links = [urldefrag(urljoin(final, n.attrs['href']))[0] for n in card.walk()
                                  if n.tag == 'a' and n.attrs.get('href')]
                    selected = [u for u in card_links if urlsplit(u).hostname in self.allowed_hosts
                                and urlsplit(u).scheme == 'https' and not urlsplit(u).query
                                and re.fullmatch(r'/peraturan/[^/]+/?', urlsplit(u).path)]
                    for detail in dict.fromkeys(selected):
                        category = ' '.join(n.text() for n in card.walk() if 'badge' in n.attrs.get('class', '').split())
                        target = excluded if re.search(r'\b(?:draft|rancangan)\b', category, re.I) or re.search(r'/(?:draft|rancangan)[-/]', detail, re.I) else details
                        if detail not in target:
                            target.append(detail)
                if len(details) + len(excluded) != shown:
                    raise ValueError('可识别详情数量与官方本页展示数量不一致')
                fingerprint = hashlib.sha256('\n'.join(sorted(details + excluded)).encode()).hexdigest()
                row.update(final_url=final, sha256=hashlib.sha256(body).hexdigest(), detail_urls=details,
                    excluded_detail_urls=excluded, shown=shown, displayed_total=total,
                    observed_last_page=observed_last, fingerprint=fingerprint)
                if fingerprint in stream['fingerprints'] or set(details) & set(state['details']):
                    stream['stop_reasons'].append('repeated_or_overlapping_page')
                    raise ValueError('分页重复首屏或跨页详情重叠；不能视为穷尽')
                state['details'].extend(details)
                state['excluded_details'].extend(excluded)
                stream['fingerprints'].append(fingerprint)
                stream['visited_urls'].append(url)
                stream['pending_urls'].pop(0)
                row['status'] = 'success'
            except Exception as exc:
                row.update(status='failed', reason=str(exc))
                stream['stop_reasons'].append('fetch_failed')
                state['pages'].append(row)
                break
            finally:
                if row['status'] == 'success':
                    state['pages'].append(row)
                stream['stop_reasons'] = list(dict.fromkeys(stream['stop_reasons']))
                state['pending_urls'] = list(stream['pending_urls'])
                state['complete'] = not state['pending_urls'] and not stream['stop_reasons']
                if on_page:
                    on_page(deepcopy(state))
        state['requests_this_run'] = requests
        state['pending_urls'] = list(stream['pending_urls'])
        reconciled = len(state['details']) + len(state['excluded_details']) == state['observed_total']
        state['complete'] = not state['pending_urls'] and not stream['stop_reasons'] and reconciled
        state['limit_reached'] = bool(state['pending_urls'] and requests >= max_pages)
        state['coverage_note'] = '混合目录：法规、行政决定、通函等；草案排除另计；仅本目录快照，不证明全国全集或最新效力。'
        return state

    def read_document(self, url, fetch):
        return self._read_document(url, fetch, strict=True)

    def read_document_bulk(self, url, fetch):
        return self._read_document(url, fetch, strict=False)

    def _read_document(self, url, fetch, strict):
        body, final = fetch(url)
        if urlsplit(final).hostname not in self.allowed_hosts:
            raise ValueError('贸易部详情跳转到未允许的官方主机')
        html = text_html(body)
        reject_access_error(html)
        nodes = list(_Document(html).root.walk())
        pairs = _table_pairs(nodes)
        metadata = extract_metadata(html, final, self.platform)
        title = metadata['title']['value'] or ''
        # 只读取标题开头的本法规身份，避免把正文引用的旧法或误标详情视为原件。
        formal_title = bool(re.match(r'^' + _FORMAL_KIND + r'.*?\s+Nomor\s+[\w./-]+\s+Tahun\s+\d{4}\b', title, re.I))
        if strict and not formal_title:
            raise ValueError('贸易部详情未确认正式法规标题与编号，拒绝草案、裁决、FAQ 或目录')
        _, doc_type = _pick(pairs, ['Tipe Dokumen', 'Tipe'])
        kind_label, kind = _pick(pairs, ['Jenis / Bentuk Peraturan', 'Jenis Peraturan', 'Jenis'])
        if strict and doc_type and not re.match(r'^Peraturan(?:\s|$)', doc_type, re.I):
            raise ValueError('贸易部详情文献类型不是正式法规')
        if strict and kind and (not re.match(r'^' + _FORMAL_KIND, kind, re.I)
                     or re.search(r'\b(?:rancangan|draft|putusan|keputusan|faq|abstrak)\b', kind, re.I)):
            raise ValueError('贸易部详情法规类型不是正式法规')
        formal_kind = (not kind or bool(re.match(r'^' + _FORMAL_KIND, kind, re.I))) and not re.search(r'\b(?:rancangan|draft|putusan|keputusan|faq|abstrak)\b', kind or '', re.I)
        accepted = formal_title and formal_kind and (not doc_type or bool(re.match(r'^Peraturan(?:\s|$)', doc_type, re.I)))
        if not strict:
            metadata['processing_gate'] = 'eligible' if accepted else 'pending_document_type_review'
        metadata['document_type'] = field(
            'regulation' if accepted else ('administrative_decision' if re.search(r'\bKeputusan\b', title, re.I) else 'unclassified_official_document'), kind or title, kind_label or metadata['title']['source_label'],
            final, '官方详情的正式法规类型和标题身份；尚未核实现行效力。')
        if not strict and (re.match(r'^(?:rancangan|draft)\b', title, re.I) or re.search(r'\b(?:rancangan|draft)\b', kind or '', re.I)):
            metadata['processing_gate'] = 'excluded_draft'
            return Document(final, [], metadata)
        files = []
        for node in nodes:
            attr = {'a': 'href', 'iframe': 'src', 'embed': 'src', 'object': 'data'}.get(node.tag)
            if not attr or not node.attrs.get(attr):
                continue
            candidate = urldefrag(urljoin(final, node.attrs[attr]))[0]
            path = unquote(urlsplit(candidate).path)
            filename = path.rsplit('/', 1)[-1]
            explicit_download = (bool(re.search(r'\b(?:unduh|download|dokumen|naskah|lampiran)\b',
                node.text() + ' ' + node.attrs.get('title', ''), re.I)) or 'download' in node.attrs
                or node.tag in ('iframe', 'embed', 'object'))
            if re.fullmatch(r'/peraturan/download/[0-9a-f-]{36}/(?:file_peraturan(?:_en)?|file_lampiran)', path, re.I):
                explicit_download = True
            archive_file = document_url(candidate) or (not strict and explicit_download and (
                path.lower().endswith(('.txt', '.xml', '.json', '.html', '.htm', '.csv', '.doc', '.xls',
                    '.ppt', '.wpd', '.tif', '.tiff', '.jpg', '.jpeg', '.png', '.bin'))
                or '/download/' in path.lower()))
            if (urlsplit(candidate).hostname in self.allowed_hosts and archive_file
                    # 官网页脚的前端应用 ZIP 不是本条法规正文或附件。
                    and not path.casefold().startswith('/frontend-new/')
                    and not any(_SUPPLEMENT.search(value) for value in
                                (node.text(), node.attrs.get('title', ''), filename))
                    and candidate not in files):
                files.append(candidate)
        if not strict:
            metadata['file_roles'] = classify_file_roles(html, final, files)
            evidence = missing_original_evidence(self.platform, html)
            if evidence:
                metadata['primary_original_missing'] = True
                metadata['missing_original_evidence'] = evidence
            for role in metadata['file_roles'].values():
                role['detail_sha256'] = hashlib.sha256(body).hexdigest()
        return Document(final, files, metadata)


ADAPTERS = {'kemendag.go.id': TradeAdapter()}
