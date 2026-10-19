"""从官方详情 HTML 提取法规元数据；只使用标准库，不猜测缺失的日期或适用地区。

published_at 指法规公布日期（Pengundangan），网页发布日另存 web_published_at。
各字段携带原文和来源；未知值为 None，不能用下载时间、PDF 文件名或制定地点填补。
"""

import json
import re
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit, urldefrag


_MONTHS = {
    'januari': 1, 'january': 1, 'februari': 2, 'february': 2,
    'maret': 3, 'march': 3, 'april': 4, 'mei': 5, 'may': 5,
    'juni': 6, 'june': 6, 'juli': 7, 'july': 7,
    'agustus': 8, 'august': 8, 'september': 9,
    'oktober': 10, 'october': 10, 'november': 11,
    'desember': 12, 'december': 12,
}
_EMPTY = {'', '-', '–', '—', 'null', 'none', 'n/a', 'tidak tersedia'}


def _clean(value):
    return re.sub(r'\s+', ' ', value).strip()


def _key(value):
    return _clean(value).strip(' :').casefold()


class _Node:
    def __init__(self, tag='', attrs=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.children = []

    def text(self):
        return _clean(' '.join(c if isinstance(c, str) else c.text()
                               for c in self.children))

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.walk()


class _Document(HTMLParser):
    _VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
             'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self._VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _normalize_date(raw):
    """规范化明确日期；错误或不明确日期保持未知，并保留原文供审查。"""
    value = _clean(raw)
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return date.fromisoformat(value).isoformat()
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}.*', value):
            return datetime.fromisoformat(value.replace('Z', '+00:00')).isoformat()
        match = re.fullmatch(r'(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})', value)
        if match:
            day, month, year = match.groups()
            return date(int(year), _MONTHS[month.casefold()], int(day)).isoformat()
    except (ValueError, KeyError):
        pass
    return None


def _field(url, meaning, raw=None, label=None, value=None, is_date=False):
    raw = _clean(raw) if isinstance(raw, str) else raw
    if raw is None or (isinstance(raw, str) and _key(raw) in _EMPTY):
        return dict(value=None, raw=raw, source_label=label, source_url=url,
                    status='not_provided', meaning=meaning)
    if is_date:
        value = _normalize_date(raw)
    elif value is None:
        value = raw
    return dict(value=value, raw=raw, source_label=label, source_url=url,
                status='observed' if value is not None else 'unparsed', meaning=meaning)


def _table_pairs(nodes):
    pairs = []
    for node in nodes:
        if node.tag == 'tr':
            cells = [c for c in node.children
                     if isinstance(c, _Node) and c.tag in ('td', 'th')]
            if len(cells) == 2:
                pairs.append((cells[0].text(), cells[1].text()))
        elif node.tag == 'dl':
            label = None
            for child in node.children:
                if not isinstance(child, _Node):
                    continue
                if child.tag == 'dt':
                    label = child.text()
                elif child.tag == 'dd' and label:
                    pairs.append((label, child.text()))
                    label = None
    return pairs


def _pick(pairs, labels):
    keys = {_key(label) for label in labels}
    return next(((label, raw) for label, raw in pairs if _key(label) in keys), (None, None))


def _jsonld(nodes, url):
    """只取本页的法规/页面实体，不将站点或机构的日期误记为法规日期。"""
    for node in nodes:
        if node.tag != 'script' or node.attrs.get('type', '').lower() != 'application/ld+json':
            continue
        try:
            data = json.loads(''.join(c for c in node.children if isinstance(c, str)))
        except (ValueError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            entities = item.get('@graph', [item])
            if not isinstance(entities, list):
                continue
            for entity in entities:
                if not isinstance(entity, dict):
                    continue
                types = entity.get('@type', [])
                types = [types] if isinstance(types, str) else types
                if not isinstance(types, list) or not any(
                        isinstance(kind, str) and kind in {'Legislation', 'WebPage', 'Article'}
                        for kind in types):
                    continue
                entity_url = entity.get('url') or entity.get('@id')
                if entity_url and (not isinstance(entity_url, str) or
                                   urldefrag(entity_url)[0].rstrip('/') != urldefrag(url)[0].rstrip('/')):
                    continue
                yield entity


def extract_metadata(html: str, url: str, platform: str) -> dict:
    """返回带证据的字段；不把元数据提取视为法规效力或实体适用范围审查。

    每字段为 {value, raw, source_label, source_url, status, meaning}。
    日期 value 为 ISO 日期或 ISO 日期时间；地区/标题为字符串；身份 value 为字典。
    status 为 observed、not_provided、unparsed，身份不完整时为 partial。
    """
    nodes = list(_Document(html).root.walk())
    pairs = _table_pairs(nodes)

    label, raw = _pick(pairs, ['Judul', 'Judul Peraturan'])
    if not raw:
        heading = next((n for n in nodes if n.tag == 'h1' and n.text()), None)
        if heading:
            label, raw = 'h1', heading.text()
    result = {'title': _field(url, '官方详情页的法规标题。', raw, label)}

    date_specs = {
        'enacted_at': (['Tanggal Penetapan', 'Ditetapkan Tanggal', 'Ditetapkan pada tanggal'],
                       '法规制定或签署日期（Penetapan）；不是公布日期。'),
        'promulgated_at': (['Tanggal Pengundangan', 'Diundangkan Tanggal', 'Diundangkan pada tanggal'],
                          '法规公布日期（Pengundangan）；不是网页发布日或下载时间。'),
        'web_published_at': (['Tanggal Publikasi', 'Tanggal Unggah', 'Tanggal Upload', 'Published at'],
                             '官方详情页明确提供的网页发布或上传日期；不是法规公布日。'),
        'updated_at': (['Terakhir Diperbarui', 'Terakhir Diperbaharui', 'Tanggal Pembaruan',
                        'Tanggal Pemutakhiran', 'Tanggal Update', 'Last Updated', 'Updated at'],
                       '官方详情页明确提供的记录或网页更新时间；不是抓取时间或法规修订日期。'),
    }
    for name, (labels, meaning) in date_specs.items():
        label, raw = _pick(pairs, labels)
        result[name] = _field(url, meaning, raw, label, is_date=True)

    combined_label, combined_raw = _pick(pairs, ['Tanggal Penetapan / Pengundangan'])
    if combined_raw:
        # 该官方字段按标签顺序给出两个日期，不根据日期先后或文件名推断。
        parts = re.split(r'\s*/\s*', combined_raw)
        if len(parts) == 2:
            for name, raw in zip(('enacted_at', 'promulgated_at'), parts):
                result[name] = _field(url, date_specs[name][1], raw, combined_label, is_date=True)

    meta = [(n.attrs.get('property') or n.attrs.get('name') or '', n.attrs.get('content', ''))
            for n in nodes if n.tag == 'meta']
    for name, labels in [('web_published_at', ['article:published_time', 'datePublished']),
                         ('updated_at', ['article:modified_time', 'dateModified'])]:
        if result[name]['status'] != 'not_provided':
            continue
        label, raw = _pick(meta, labels)
        result[name] = _field(url, date_specs[name][1], raw, f'meta:{label}' if label else None,
                              is_date=True)
    for entity in _jsonld(nodes, url):
        for name, prop in [('web_published_at', 'datePublished'), ('updated_at', 'dateModified')]:
            raw = entity.get(prop)
            if result[name]['status'] == 'not_provided' and isinstance(raw, str):
                result[name] = _field(url, date_specs[name][1], raw, f'JSON-LD:{prop}', is_date=True)

    result['published_at'] = dict(result['promulgated_at'])
    result['published_at']['meaning'] = '法规发布时间按官方公布日期（Pengundangan）记录；不是网页发布日或下载时间。'

    label, raw = _pick(pairs, ['Wilayah Berlaku', 'Wilayah Penerapan', 'Cakupan Wilayah',
                              'Wilayah Pemberlakuan', 'Daerah Berlaku'])
    result['effective_region'] = _field(
        url, '仅记录来源明确列出的适用地区；制定地点、保管地点和发布机关所在国不等同于适用地区。', raw, label)

    # 国家归属仅用于法律身份；不据此声称全国适用，也不从标题内引用的旧法提取编号。
    title = result['title']['value'] or ''
    type_label, legal_type = _pick(pairs, ['Jenis / Bentuk Peraturan', 'Jenis Peraturan', 'Jenis'])
    number_label, number = _pick(pairs, ['Nomor Peraturan', 'Nomor'])
    year_label, year = _pick(pairs, ['Tahun Peraturan', 'Tahun'])
    legal_type = legal_type if legal_type and _key(legal_type) not in _EMPTY else None
    number = number if number and _key(number) not in _EMPTY else None
    year = year if year and _key(year) not in _EMPTY else None
    title_identity = re.match(r'^(.+?)\s+Nomor\s+([\w./-]+)\s+Tahun\s+(\d{4})\b', title, re.I)
    if title_identity:
        if not number:
            number = title_identity.group(2)
        if not year:
            year = title_identity.group(3)
    host = (urlsplit(url).hostname or '').removeprefix('www.')
    jurisdiction = 'ID' if host in {'jdih.kemendag.go.id', 'peraturan.go.id'} else None
    issuer_label, issuer = _pick(pairs, ['T.E.U. Badan/Pengarang', 'Pemrakarsa', 'Instansi'])
    issuer = issuer if issuer and _key(issuer) not in _EMPTY else None
    identity = dict(jurisdiction=jurisdiction, issuer=issuer or None, type=legal_type or None,
                    number=number or None, year=year or None)
    identity_raw = {label: raw for label, raw in [(type_label, legal_type), (number_label, number),
                                                 (year_label, year), (issuer_label, issuer)] if label}
    if title_identity:
        identity_raw['Judul'] = title
    if jurisdiction:
        identity_raw['source_host'] = host
    result['legal_identity'] = _field(url, '法规身份字段；jurisdiction 为法律所属国家，不表示全国适用；未核实法规现行效力。',
                                      identity_raw or None, '官方详情表、标题及来源域名', identity)
    if result['legal_identity']['value'] is not None and not all(identity.values()):
        result['legal_identity']['status'] = 'partial'
    return result
