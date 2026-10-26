"""DPR 空文件诊断及跨官方来源的严格身份匹配；不猜测文件地址。"""
import re
import unicodedata
from urllib.parse import urlsplit

from law_crawler.regulation_metadata import _Document

TYPES = {
    'presidential_instruction': 'inpres', 'instruksi presiden': 'inpres',
    'presidential_decision': 'keppres', 'keputusan presiden': 'keppres',
    'penetapan presiden': 'penpres',
    'presidential_regulation': 'perpres', 'peraturan presiden': 'perpres',
    'government_regulation': 'pp', 'peraturan pemerintah': 'pp',
    'law': 'uu', 'undang-undang': 'uu',
}


def normalized_title(value):
    value = unicodedata.normalize('NFKC', str(value or '')).casefold()
    # 官方完整标题的文种编号前缀不属于事项标题；不删改事项中的年份/数字。
    value = re.sub(r'^(?:instruksi presiden|keputusan presiden|penetapan presiden|peraturan presiden|'
                   r'peraturan pemerintah|undang[ -]undang)(?: republik indonesia)?\s+'
                   r'(?:(?:nomor|no\.?)\s+)?\d+\s+tahun\s+\d{4}\s+tentang\s*', '', value)
    return re.sub(r'[^\w]+', '', value)


def identity(record, number=None):
    kind = TYPES.get(str(record.get('type', '')).casefold())
    official = record.get('official_metadata') or {}
    # 部分目录把68/M误存为68；不能因此与一般编号68合并。
    for title in (record.get('title'), official.get('judul')):
        # An explicit formal title can contradict a misclassified catalogue type.
        # Reject this candidate instead of turning a valid PENPRES into a KEPPRES.
        formal = re.match(r'^(instruksi presiden|keputusan presiden|penetapan presiden|'
                          r'peraturan presiden|peraturan pemerintah|undang-undang)'
                          r'(?: republik indonesia)?\s+(?:(?:nomor|no\.?)\s+)?\d+\b',
                          str(title or '').strip(), re.I)
        if formal and TYPES.get(formal.group(1).casefold()) != kind:
            return None
        if re.match(r'^(?:instruksi presiden|keputusan presiden|peraturan presiden|'
                    r'penetapan presiden|peraturan pemerintah|undang[ -]undang)(?: republik indonesia)?\s+'
                    r'(?:(?:nomor|no\.?)\s+)?\d+\s*[/\-]\s*[A-Za-z]+\b',
                    str(title or '').strip(), re.I):
            return None
    number = number if number is not None else official.get('no', record.get('number'))
    year = str(record.get('year') or '')
    number = str(number or '').strip()
    if not kind or not re.fullmatch(r'\d+', number) or not re.fullmatch(r'(?:18|19|20)\d{2}', year):
        return None
    return kind, str(int(number)), year


def extract_dpr_evidence(record, html):
    root = _Document(html).root
    fields = {}
    placeholders, concrete = [], []
    for node in root.walk():
        if node.tag == 'tr':
            cells = [n for n in node.children if getattr(n, 'tag', '') in ('td', 'th')]
            if len(cells) == 2:
                fields[cells[0].text().strip().casefold()] = cells[1].text().strip()
        if node.tag == 'a' and 'UNDUH BERKAS' in node.text().upper():
            url = node.attrs.get('href', '')
            p = urlsplit(url)
            if p.hostname != 'berkas.dpr.go.id' or p.scheme != 'https' or p.username or p.password:
                continue
            if p.path.endswith('/') or p.path.rsplit('/', 1)[-1] in ('-', ''):
                placeholders.append(url)
            else:
                concrete.append(url)
    return dict(identity=identity(record, fields.get('nomor')), fields=fields,
                placeholder_links=placeholders, concrete_links=concrete,
                reason='official_download_filename_empty' if placeholders and not concrete else
                       'official_link_observed' if concrete else 'official_download_link_absent')


def strict_match(record, evidence, candidate):
    """仅匹配全国文种+编号+年份+规范化事项标题；文种本身确定同一制定机关体系。"""
    left = evidence.get('identity')
    right = identity(candidate)
    if not left or not right or tuple(left) != right:
        return False
    title = evidence.get('fields', {}).get('judul') or record.get('title')
    other = (candidate.get('official_metadata') or {}).get('judul') or candidate.get('title')
    return len(normalized_title(title)) >= 8 and normalized_title(title) == normalized_title(other)
