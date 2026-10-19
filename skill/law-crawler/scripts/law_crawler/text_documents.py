"""将已确认的官方法规文本原件转换为 Markdown，原始字节仍须单独保留。

这是格式转换器，不负责判断法域、来源域名或法规效力。通用格式必须由
平台 adapter 显式确认正文，并给出正文容器证据；HTTP 类型不能代替确认。
"""
import json
import re
from copy import deepcopy
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

from law_crawler.document_formats import _canadian_law_xml

MAX_TEXT_BYTES = 40 * 1024 * 1024
MAX_DEPTH = 180
CONVERSION_METHOD = 'structured_text_v2.4'
SKIP_HTML = {'script', 'style', 'noscript', 'template', 'nav', 'form'}
ERROR_TITLES = re.compile(r'^(?:access\s*denied|forbidden|unauthori[sz]ed|error|404|403|just a moment|verify (?:you are|that you)|security verification|please enable javascript)\b', re.I)


def _value(metadata, name):
    value = (metadata or {}).get(name)
    return value.get('value') if isinstance(value, dict) else value


def _decode(data):
    if not isinstance(data, bytes) or not data or len(data) > MAX_TEXT_BYTES:
        raise ValueError('文本原件为空、不是字节或超过 40 MiB')
    encoding = ('utf-32' if data.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')) else
                'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')
    try:
        raw = data.decode(encoding)
    except UnicodeError as exc:
        raise ValueError('文本原件必须为有效 UTF 编码；不猜测编码或替换丢失字符') from exc
    if any(ord(c) < 32 and c not in '\t\n\r' for c in raw):
        raise ValueError('文本原件含空字符或不支持的控制字符')
    return raw


def _plain(text):
    return re.sub(r'\s+', ' ', text or '').strip()


def _escape(text):
    # 避免法规文字被解释为活动 HTML 或 Markdown 结构。
    return re.sub(r'([\\`*_\[\]<>])', r'\\\1', text)


def _code(text):
    """源标签、路径、属性用代码文本显示，不能成为活动 HTML。"""
    text = _plain(str(text))
    fence = '`' * (max((len(m.group()) for m in re.finditer(r'`+', text)), default=0) + 1)
    return fence + ' ' + text + ' ' + fence


def _table_grid(rows):
    """共享的安全 Markdown 网格；合并格只存一次文字，续格指明原坐标。"""
    occupied = {}
    for ri, (header, cells) in enumerate(rows):
        column = 0
        for content, colspan, rowspan, start in cells:
            if start is not None:
                if start < column or (ri, start) in occupied:
                    raise ValueError('表格显式列位置重叠，不能无损转换')
                column = start
            while (ri, column) in occupied:
                column += 1
            if column < 0 or not (1 <= colspan <= 1000 and 1 <= rowspan <= 1000) or (ri + rowspan) * (column + colspan) > 100000:
                raise ValueError('表格跨度过大或无效，不能安全转换')
            origin = f'R{ri + 1}C{column + 1}'
            for dr in range(rowspan):
                for dc in range(colspan):
                    position = (ri + dr, column + dc)
                    if position in occupied:
                        raise ValueError('表格合并单元格重叠，不能无损转换')
                    occupied[position] = content if dr == dc == 0 else f'↳ {origin}'
            column += colspan
    if not occupied:
        return ''
    width = max(c for _, c in occupied) + 1
    height = max(r for r, _ in occupied) + 1
    def line(values):
        return '| ' + ' | '.join(re.sub(r'\s*\n\s*', ' / ', value).replace('|', '\\|') for value in values) + ' |'
    rendered = [line([occupied.get((r, c), '') for c in range(width)]) for r in range(height)]
    if not rows or not rows[0][0]:
        rendered.insert(0, line([f'Column {i + 1}' for i in range(width)]))
    rendered.insert(1, '| ' + ' | '.join(['---'] * width) + ' |')
    return '\n\n' + '\n'.join(rendered) + '\n\n'


def _span(value, default=1):
    if value is None:
        return default
    if not re.fullmatch(r'\d+', str(value)):
        raise ValueError('表格跨度不是非负整数')
    return int(value)


class _Node:
    def __init__(self, tag='', attrs=None):
        self.tag, self.attrs, self.children = tag, dict(attrs or []), []

    def text(self):
        if self.tag in SKIP_HTML:
            return ''
        return ''.join(c.text() if isinstance(c, _Node) else c for c in self.children)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.walk()


class _HTML(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, raw):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.stack = [self.root]
        self.feed(raw)
        self.close()

    def handle_starttag(self, tag, attrs):
        if len(self.stack) > MAX_DEPTH:
            raise ValueError('HTML 嵌套层次过深')
        node = _Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _html_body(raw, metadata):
    tree = _HTML(raw)
    for node in tree.root.walk():
        if node.tag == 'title' and ERROR_TITLES.match(_plain(node.text())):
            raise ValueError('HTML 是拒绝访问、错误或验证页面')
    selector = _value(metadata, 'text_body_selector')
    if isinstance(selector, str) and selector.startswith('#') and re.fullmatch(r'#[A-Za-z][\w:.-]*', selector):
        matches = [n for n in tree.root.walk() if n.attrs.get('id') == selector[1:]]
    elif selector in ('article', 'main'):
        matches = [n for n in tree.root.walk() if n.tag == selector]
    else:
        raise ValueError('HTML 正文需给出明确的 #id 或唯一 article/main 容器')
    if len(matches) != 1 or matches[0].tag in SKIP_HTML:
        raise ValueError('HTML 正文容器缺失、重复或不是可见正文')
    body = matches[0]
    visible = ' '.join(n.text() for n in body.walk() if n.tag in ('p', 'td', 'pre', 'li'))
    if not _plain(visible) or ERROR_TITLES.match(_plain(visible)):
        raise ValueError('HTML 缺少条段正文或内容是错误消息')
    return body


def _xml_root(raw):
    if '<!DOCTYPE' in raw.upper() or '<!ENTITY' in raw.upper():
        raise ValueError('XML 不允许 DTD 或实体声明')
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError) as exc:
        raise ValueError('XML 原件结构无效') from exc
    def check_depth(node, depth=0):
        if depth > MAX_DEPTH:
            raise ValueError('XML 嵌套层次过深')
        for child in node:
            check_depth(child, depth + 1)
    check_depth(root)
    return root


def _local(tag):
    return tag.rsplit('}', 1)[-1]


def _json_value(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('JSON 原件包含重复字段，无法无损转换')
            value[key] = item
        return value
    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda x: (_ for _ in ()).throw(ValueError('JSON 不允许非有限数值')))
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('JSON 原件结构无效或嵌套过深') from exc


def _inspect(data, metadata):
    if _value(metadata, 'document_type') != 'regulation':
        return None
    if data.startswith((b'%PDF-', b'PK\x03\x04', b'\x89PNG', b'\xff\xd8', b'{\\rtf')):
        return None
    raw = _decode(data)
    stripped = raw.lstrip()
    # 加拿大 LIMS 沿用已有的正文结构白名单，目录 XML 不符合此门槛。
    if stripped.startswith('<'):
        try:
            _canadian_law_xml(data)
        except ValueError:
            pass
        else:
            return {'extension': 'xml', 'media_type': 'application/xml'}, _xml_root(raw)
    if _value(metadata, 'text_body_confirmed') is not True:
        return None
    if re.match(r'(?:<!doctype\s+html\b|<html\b)', stripped, re.I):
        return {'extension': 'html', 'media_type': 'text/html'}, _html_body(raw, metadata)
    if stripped.startswith('<'):
        root = _xml_root(raw)
        name = _local(root.tag)
        if name.lower() in ('html', 'error', 'errors', 'accessdenied', 'response', 'catalog', 'catalogue', 'directory'):
            raise ValueError('XML 是页面、错误或目录，不能作为法规正文')
        if name != _value(metadata, 'text_body_root') or not _plain(''.join(root.itertext())):
            raise ValueError('XML 缺少已确认的正文根节点或正文为空')
        return {'extension': 'xml', 'media_type': 'application/xml'}, root
    if stripped.startswith(('{', '[')):
        value = _json_value(raw)
        key = _value(metadata, 'text_body_root')
        if not isinstance(value, dict) or not isinstance(key, str) or key not in value:
            raise ValueError('JSON 缺少已确认的顶级正文节点')
        if any(str(k).lower() in ('error', 'errors', 'accessdenied', 'captcha', 'challenge') for k in value):
            raise ValueError('JSON 是错误或验证响应')
        if not value[key]:
            raise ValueError('JSON 法规正文为空')
        strings = []
        def body_text(item, depth=0):
            if depth > MAX_DEPTH:
                raise ValueError('JSON 嵌套层次过深')
            if isinstance(item, dict):
                for child in item.values():
                    body_text(child, depth + 1)
            elif isinstance(item, list):
                for child in item:
                    body_text(child, depth + 1)
            elif isinstance(item, str) and item.strip():
                strings.append(item)
        body_text(value[key])
        if not strings or ERROR_TITLES.match(_plain(' '.join(strings))):
            raise ValueError('JSON 缺少法条文字或内容是错误消息')
        return {'extension': 'json', 'media_type': 'application/json'}, value
    return None


def detect_text_document(data, metadata=None):
    """已确认的文本原件返回原始 extension/media_type，二进制或未确认返回 None。

    确认后发现错误响应、无效编码/结构时抛 ValueError，调用方须记录失败原因。
    metadata 使用项目的 {value, raw, source_url, ...} 包装字段。
    """
    found = _inspect(data, metadata)
    return found[0] if found else None


def _html_markdown(node, source_url):
    if isinstance(node, str):
        return _escape(re.sub(r'\s+', ' ', node))
    if node.tag in SKIP_HTML:
        return ''
    if node.tag == 'table':
        rows = []
        def collect(parent, header=False):
            for child in parent.children:
                if not isinstance(child, _Node):
                    continue
                if child.tag == 'table':
                    continue
                if child.tag == 'tr':
                    cells = []
                    for cell in child.children:
                        if not isinstance(cell, _Node) or cell.tag not in ('td', 'th'):
                            continue
                        text = ''.join(_html_markdown(c, source_url) for c in cell.children).strip()
                        spans = ', '.join(f'{key}={cell.attrs[key]}' for key in ('rowspan', 'colspan') if key in cell.attrs)
                        if spans:
                            text += ' (' + _escape(spans) + ')'
                        cells.append((text, _span(cell.attrs.get('colspan')), _span(cell.attrs.get('rowspan')), None))
                    rows.append((header or any(isinstance(c, _Node) and c.tag == 'th' for c in child.children), cells))
                else:
                    collect(child, header or child.tag == 'thead')
        collect(node)
        captions = [c for c in node.children if isinstance(c, _Node) and c.tag == 'caption']
        caption = '\n\n'.join(_html_markdown(c, source_url).strip() for c in captions)
        return '\n\n' + caption + _table_grid(rows)
    content = ''.join(_html_markdown(c, source_url) for c in node.children)
    tag = node.tag
    if re.fullmatch(r'h[1-6]', tag):
        return '\n\n' + '#' * int(tag[1]) + ' ' + content.strip() + '\n\n'
    if tag == 'br':
        return '  \n'
    if tag in ('p', 'div', 'section', 'article', 'main', 'blockquote'):
        return '\n\n' + content.strip() + '\n\n'
    if tag == 'li':
        return '\n- ' + content.strip() + '\n'
    if tag == 'ol':
        start = node.attrs.get('start', '1')
        number = int(start) if re.fullmatch(r'-?\d+', start) else 1
        lines = []
        for child in node.children:
            if isinstance(child, _Node) and child.tag == 'li':
                explicit = child.attrs.get('value', '')
                if re.fullmatch(r'-?\d+', explicit):
                    number = int(explicit)
                item = ''.join(_html_markdown(c, source_url) for c in child.children).strip()
                lines.append(f'{number}. ' + item.replace('\n', '\n    '))
                number += 1
            elif isinstance(child, _Node):
                lines.append(_html_markdown(child, source_url))
        return '\n\n' + '\n'.join(lines) + '\n\n'
    if tag == 'ul':
        return '\n\n' + content + '\n\n'
    if tag == 'dl':
        # HTML allows several terms followed by several definitions. Preserve
        # the sequence and nesting without inventing one-to-one field pairs.
        content = re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n', content).strip()
        quoted = '\n'.join('> ' + line for line in content.splitlines())
        return '\n\n> HTML dl — definition list\n>\n' + quoted + '\n>\n> End HTML dl\n\n'
    if tag in ('dt', 'dd'):
        role = 'Term' if tag == 'dt' else 'Definition'
        content = content.strip()
        separator = '\n\n' if re.match(r'^(?:>|#{1,6}\s|\d+\.\s|[-*+]\s)', content) else ' '
        return '\n\n**' + role + ' (' + tag + '):**' + separator + content + '\n\n'
    if tag in ('sup', 'sub'):
        # Do not turn source superscript/subscript into ordinary adjacent text,
        # and do not assume that a superscript denotes a resolvable footnote.
        return ' ' + _escape('[' + tag + ': ') + content.strip() + _escape(']') + ' '
    if tag == 'time':
        attrs = ''
        if 'datetime' in node.attrs:
            attrs = ' ' + _code('[time @datetime=' + json.dumps(node.attrs['datetime'], ensure_ascii=False) + ']')
        return ' ' + content.strip() + attrs + ' '
    if tag == 'img':
        # A source reference is evidence, not an instruction to fetch/embed it.
        fields = ['@' + key + '=' + json.dumps(node.attrs[key], ensure_ascii=False)
                  for key in ('alt', 'src') if key in node.attrs]
        return ' ' + _code('[img' + (' ' + '; '.join(fields) if fields else '') + ']') + ' '
    if tag in ('strong', 'b'):
        return '**' + content.strip() + '**'
    if tag in ('em', 'i'):
        return '*' + content.strip() + '*'
    if tag == 'a':
        target = urljoin(source_url, node.attrs.get('href', ''))
        if urlsplit(target).scheme in ('http', 'https'):
            return '[' + content.strip() + '](' + target.replace(' ', '%20').replace(')', '%29') + ')'
    if tag in ('td', 'th'):
        return content.strip() + ' | '
    if tag == 'tr':
        return '\n' + content.rstrip(' |') + '\n'
    if tag == 'pre':
        return '\n\n' + '\n'.join('    ' + line for line in node.text().splitlines()) + '\n\n'
    return content


_LIMS_NAMESPACE = 'http://justice.gc.ca/lims'
# These are source tracking/version attributes, not independent legal conclusions.
_LIMS_TRACKING = {'id', 'fid', 'pit-date', 'lastAmendedDate', 'current-date',
                  'inforce-start-date', 'inforce-end-date', 'enacted-date'}
_XML_INLINE = {'Emphasis', 'XRefExternal', 'XRefInternal', 'DefinitionRef',
               'DefinedTermEn', 'DefinedTermFr', 'Sup', 'Sub', 'Ins', 'Del',
               'BilingualItemEn', 'BilingualItemFr', 'FootnoteRef', 'LineBreak',
               'em', 'strong', 'b', 'i', 'sup', 'sub', 'a'}
_XML_BILINGUAL_LABELS = {'BilingualItemEn': 'English', 'BilingualItemFr': 'Français'}
_XML_TEXT = {'Text', 'text', 'p', 'P', 'para', 'Para'}
_XML_STRUCTURES = {'Section', 'Article', 'Part', 'Chapter', 'Subsection', 'Division',
                   'Schedule', 'Paragraph', 'Subparagraph', 'Clause', 'Subclause',
                   'Item', 'Provision', 'Definition', 'ContinuedDefinition',
                   'ContinuedSectionSubsection', 'ContinuedParagraph'}
_XML_STRUCTURE_NAMES = {name.lower() for name in _XML_STRUCTURES}
_XML_FIELD_LABELS = {
    'RegistrationDate': '登记日期（RegistrationDate）',
    'ConsolidationDate': 'XML合并日期（ConsolidationDate）',
    'InstrumentNumber': '法规编号（InstrumentNumber）',
    'ConsolidatedNumber': '合并文本编号（ConsolidatedNumber）',
    'LongTitle': '完整标题（LongTitle）',
    'ShortTitle': '简短标题（ShortTitle）',
    'EnablingAuthority': '依据法律（EnablingAuthority）',
    'RegulationMaker': '制令者（RegulationMaker）',
    'OrderNumber': '命令编号（OrderNumber）',
}


def _xml_name(name):
    if name.startswith('{' + _LIMS_NAMESPACE + '}'):
        return 'lims:' + _local(name)
    if name.startswith('{http://www.w3.org/XML/1998/namespace}'):
        return 'xml:' + _local(name)
    return name


class _XMLMarkdown:
    """Conservative renderer for observed LIMS structures plus labelled XML fallback.

    Unknown schemas are displayed with their source names; this does not claim to
    understand their legal semantics. Original bytes remain the complete source.
    """
    def __init__(self, root):
        self.root, self.paths, self.footnotes, self.ids = root, {}, {}, {}
        self.definitions = []
        self.canadian = _local(root.tag) in ('Regulation', 'Statute')
        footnotes = []
        def index(node, path):
            self.paths[id(node)] = path
            counts, seen = {}, {}
            for child in node:
                counts[child.tag] = counts.get(child.tag, 0) + 1
            for child in node:
                seen[child.tag] = seen.get(child.tag, 0) + 1
                suffix = f'[{seen[child.tag]}]' if counts[child.tag] > 1 else ''
                index(child, path + '/' + _xml_name(child.tag) + suffix)
            if _local(node.tag) == 'Footnote':
                footnotes.append(node)
        index(root, '/' + _xml_name(root.tag))
        counts = {}
        for node in footnotes:
            counts[node.get('id')] = counts.get(node.get('id'), 0) + 1
        # Reserve source IDs before generating markers, so hostile/colliding IDs
        # cannot cause two definitions to merge or an ambiguous reference to link.
        used = {value for value in counts if value and re.fullmatch(r'[A-Za-z0-9_-]+', value)}
        for number, node in enumerate(footnotes, 1):
            source_id = node.get('id')
            marker = source_id if source_id in used and counts[source_id] == 1 else f'xml-footnote-{number}'
            if marker != source_id:
                while marker in used:
                    marker += '-generated'
                used.add(marker)
            self.footnotes[id(node)] = marker
            if source_id:
                self.ids.setdefault(source_id, []).append(marker)

    def attrs(self, node, all_attrs=False):
        values = []
        for key, value in node.attrib.items():
            if (self.canadian and not all_attrs and key.startswith('{' + _LIMS_NAMESPACE + '}')
                    and _local(key) in _LIMS_TRACKING):
                continue
            values.append('@' + _xml_name(key) + '=' + json.dumps(value, ensure_ascii=False))
        return '; '.join(values)

    def source(self, node, force=False):
        attrs = self.attrs(node, node is self.root)
        if not attrs and not force:
            return ''
        return '> XML: ' + _code(self.paths[id(node)]) + ('; ' + _code(attrs) if attrs else '')

    def inline(self, node):
        notes = []
        def walk(item):
            chunks = [_escape(re.sub(r'\s+', ' ', item.text or ''))]
            for child in item:
                name = _local(child.tag)
                if name == 'Footnote':
                    self.footnote(child)
                    content = ''
                elif name == 'FootnoteRef':
                    label = _plain(''.join(child.itertext()))
                    targets = self.ids.get(child.get('idref'), [])
                    if len(targets) == 1:
                        content = ' [^' + targets[0] + ']'
                    else:
                        detail = 'idref=' + child.get('idref') if child.get('idref') else 'idref missing'
                        content = ' ' + _escape(f'[FootnoteRef {label}; {detail}; unresolved]')
                    notes.append(self.source(child, True))
                elif name in ('LineBreak', 'br'):
                    content = ' '
                elif name in ('MathML', 'math'):
                    fragment = deepcopy(child)
                    fragment.tail = None
                    content = ' ' + _escape('[' + name + ' source structure: ') + _code(
                        ET.tostring(fragment, encoding='unicode')) + _escape(']') + ' '
                    notes.append(self.source(child, True))
                elif name in ('Sup', 'Sub', 'sup', 'sub'):
                    content = _escape('[' + name + ': ') + walk(child) + _escape(']')
                    notes.append(self.source(child, True))
                elif name in _XML_INLINE or name == 'BilingualGroup':
                    content = walk(child)
                    if self.attrs(child):
                        notes.append(self.source(child, True))
                else:
                    content = _escape('[' + _xml_name(child.tag) + ': ') + walk(child) + _escape(']')
                    notes.append(self.source(child, True))
                chunks.extend([content, _escape(re.sub(r'\s+', ' ', child.tail or ''))])
            content = ''.join(chunks)
            language = _XML_BILINGUAL_LABELS.get(_local(item.tag))
            if language:
                # 每个源语言节点保留独立边界；不猜配对，也不合并相邻语言文字。
                return ' ' + _escape('[' + language + ': ') + content.strip() + _escape(']') + ' '
            return content
        text = walk(node).strip()
        return text, list(dict.fromkeys(note for note in notes if note))

    def footnote(self, node):
        marker = self.footnotes[id(node)]
        if marker in {key for key, _ in self.definitions}:
            return
        blocks = [_escape(_plain(node.text))] if _plain(node.text) else []
        for child in node:
            if _local(child.tag) in ('Label', 'label'):
                child_source = self.source(child)
                if child_source:
                    blocks.append(child_source)
            else:
                blocks.extend(self.visit(child, 0))
            if _plain(child.tail):
                blocks.append(_escape(_plain(child.tail)))
        label = next((_plain(''.join(c.itertext())) for c in node if _local(c.tag) == 'Label'), '')
        body = ('Footnote ' + _escape(label) + ': ' if label else '') + '\n\n'.join(blocks)
        source = self.source(node, True)
        if source:
            body += '\n\n' + source
        self.definitions.append((marker, body))

    def date_value(self, node):
        if _local(node.tag) != 'Date':
            return None
        children = {_local(c.tag): c for c in node}
        if set(children) != {'YYYY', 'MM', 'DD'} or len(node) != 3:
            return None
        if _plain(node.text) or any(len(c) or _plain(c.tail) for c in node):
            return None
        values = [_plain(children[key].text) for key in ('YYYY', 'MM', 'DD')]
        if not all(re.fullmatch(r'\d+', value) for value in values):
            return None
        try:
            value = date(*(int(v) for v in values)).isoformat()
        except ValueError:
            return None
        return value, '; '.join(f'{key}={raw}' for key, raw in zip(('YYYY', 'MM', 'DD'), values))

    def table(self, node):
        rows, annotations, columns, spans = [], [], {}, {}
        def source_text(item, text, role):
            if _plain(text):
                annotations.append('**XML ' + role + '** (' + _code(self.paths[id(item)]) + '): ' + _escape(_plain(text)))
        def collect(parent, header=False):
            source_text(parent, parent.text, 'text')
            for child in parent:
                name = _local(child.tag).lower()
                if name == 'table':
                    annotations.extend(self.visit(child, 0))
                    source_text(child, child.tail, 'tail')
                    continue
                if name == 'colspec':
                    number = _span(child.get('colnum'), len(columns) + 1) - 1
                    columns[child.get('colname', str(number + 1))] = number
                elif name == 'spanspec':
                    spans[child.get('spanname')] = (child.get('namest'), child.get('nameend'))
                if name in ('row', 'tr'):
                    cells = []
                    source_text(child, child.text, 'text')
                    for cell in child:
                        if _local(cell.tag).lower() not in ('entry', 'td', 'th'):
                            annotations.extend(self.visit(cell, 0))
                            continue
                        start_name, end_name = spans.get(cell.get('spanname'), (cell.get('namest'), cell.get('nameend')))
                        start = columns.get(start_name or cell.get('colname'))
                        end = columns.get(end_name)
                        colspan = end - start + 1 if start is not None and end is not None else _span(cell.get('colspan'))
                        rowspan = _span(cell.get('morerows'), 0) + 1 if cell.get('morerows') is not None else _span(cell.get('rowspan'))
                        text, notes = self.inline(cell)
                        cell_source = self.source(cell)
                        if cell_source:
                            annotations.append(cell_source)
                        annotations.extend(notes)
                        cells.append((text, colspan, rowspan, start))
                        source_text(cell, cell.tail, 'tail')
                    rows.append((header or any(_local(c.tag).lower() == 'th' for c in child), cells))
                else:
                    if name in ('title', 'caption'):
                        annotations.extend(self.visit(child, 0))
                    elif name not in ('tgroup', 'thead', 'tbody', 'tfoot', 'colspec', 'spanspec'):
                        annotations.extend(self.visit(child, 0))
                    else:
                        collect(child, header or name == 'thead')
                source = self.source(child)
                if source and name not in ('entry', 'td', 'th'):
                    annotations.append(source)
                source_text(child, child.tail, 'tail')
        collect(node)
        return [_table_grid(rows).strip()] + list(dict.fromkeys(annotations)) if rows else None

    def visit(self, node, depth=0):
        name = _local(node.tag)
        field = _xml_name(node.tag)
        source = self.source(node)
        if name == 'Footnote':
            self.footnote(node)
            return []
        if name == 'RelatedOrNotInForce':
            pieces = ['#' * min(6, depth + 2) + ' RelatedOrNotInForce', self.source(node, True)]
            if _plain(node.text):
                pieces.append(_escape(_plain(node.text)))
            for child in node:
                pieces.extend(self.visit(child, depth + 1))
                if _plain(child.tail):
                    pieces.append(_escape(_plain(child.tail)))
            pieces.append('> End XML RelatedOrNotInForce: ' + _code(self.paths[id(node)]))
            return pieces
        if name.lower() == 'table':
            table = self.table(node)
            if table is not None:
                return ([source] if source else []) + table
        if name in _XML_BILINGUAL_LABELS:
            text, notes = self.inline(node)
            return [text] + ([source] if source else []) + notes
        if name in ('MathML', 'math'):
            # A linear string is not an adequate representation of fractions,
            # indices or limits. Keep a single inert structured source fragment.
            fragment = deepcopy(node)
            fragment.tail = None
            raw = ET.tostring(fragment, encoding='unicode').strip()
            fence = '`' * max(3, max((len(m.group()) + 1 for m in re.finditer(r'`+', raw)), default=0))
            return ['**' + _escape(field) + '** (source structure, mathematical layout not interpreted)',
                    fence + 'xml\n' + raw + '\n' + fence]
        composed_date = self.date_value(node)
        if composed_date:
            value, components = composed_date
            date_label = ('命令日期（Date）'
                          if self.paths[id(node)].endswith('/RegulationMakerOrder/Date') else '日期（Date）')
            return ['**' + date_label + '**: ' + value +
                    ' (' + _code(components) + ')' ] + [item for item in
                    (source, *(self.source(c) for c in node if c.attrib)) if item]
        # 阅读字段只显示名称和值；原始结构仍保存在原XML和必要的源属性注记中。
        if len(node) == 1 and self.date_value(node[0]):
            value, components = self.date_value(node[0])
            context = self.attrs(node)
            line = '**' + _escape(_XML_FIELD_LABELS.get(name, field)) + '**'
            if context:
                line += ' (' + _code(context) + ')'
            line += ': ' + value
            line += ' (' + _code(components) + ')'
            child_sources = [self.source(c) for c in node[0] if c.attrib]
            return [line] + [x for x in (source, self.source(node[0]), *child_sources) if x]
        if name in _XML_TEXT or (name == 'Paragraph' and not any(_local(c.tag) in ('Label', 'Num', 'Number', 'Text', 'Paragraph') for c in node)):
            text, notes = self.inline(node)
            return ([source] if source else []) + ([text] if text else []) + notes
        labels = [c for c in node if _local(c.tag).lower() in ('label', 'num', 'number')]
        structural = name.lower() in _XML_STRUCTURE_NAMES
        if structural:
            label = ' '.join(_plain(''.join(c.itertext())) for c in labels)
            text = field + (' ' + label if label else '')
            pieces = ['#' * min(6, depth + 2) + ' ' + _escape(text)]
            # Markdown只支持六级标题；深层条款用完整源路径补足父子关系。
            structural_source = self.source(node, True) if depth >= 4 else source
            if structural_source:
                pieces.append(structural_source)
            if _plain(node.text):
                pieces.append(_escape(_plain(node.text)))
            for child in node:
                if child in labels:
                    _, notes = self.inline(child)
                    label_source = self.source(child)
                    pieces.extend(([label_source] if label_source else []) + notes)
                else:
                    pieces.extend(self.visit(child, depth + 1))
                if _plain(child.tail):
                    pieces.append(_escape(_plain(child.tail)))
            return pieces
        if name in ('Heading', 'heading'):
            title_children = [c for c in node if _local(c.tag) in ('Label', 'TitleText')]
            title = ' — '.join(_plain(''.join(c.itertext())) for c in title_children)
            pieces = ['#' * min(6, depth + 2) + ' ' + _escape(title or field)]
            if source:
                pieces.append(source)
            if _plain(node.text):
                pieces.append(_escape(_plain(node.text)))
            for child in node:
                if child in title_children:
                    _, notes = self.inline(child)
                    child_source = self.source(child)
                    pieces.extend(([child_source] if child_source else []) + notes)
                else:
                    pieces.extend(self.visit(child, depth + 1))
                if _plain(child.tail):
                    pieces.append(_escape(_plain(child.tail)))
            return pieces
        if len(node) == 0 or all(_local(c.tag) in _XML_INLINE for c in node):
            text, notes = self.inline(node)
            return ['**' + _escape(_XML_FIELD_LABELS.get(name, field)) + '**: ' + text] + ([source] if source else []) + notes
        # Unknown containers and metadata retain a named boundary. They are not
        # flattened into unidentified dates, numbers or supposed legal clauses.
        transparent = name in ('Body', 'Statute', 'Regulation', 'LegalDocument')
        pieces = [] if transparent else ['**' + _escape(field) + '**']
        if source:
            pieces.append(source)
        if _plain(node.text):
            pieces.append(_escape(_plain(node.text)))
        for child in node:
            pieces.extend(self.visit(child, depth))
            if _plain(child.tail):
                pieces.append(_escape(_plain(child.tail)))
        return pieces

    def render(self):
        intro = ['> 字段名称用于区分来源语境。登记日期（RegistrationDate）不是法规公布日期（published_at）；'
                 'XML合并日期（ConsolidationDate）不是详情页的current-to日期。LIMS属性按来源原样展示，不据此推断法律效力。'
                 '重复的LIMS追踪属性完整保留在原XML中。'] if self.canadian else []
        pieces = intro + self.visit(self.root)
        for marker, body in self.definitions:
            pieces.append('[^' + marker + ']: ' + body.replace('\n', '\n    '))
        return '\n\n'.join(piece for piece in pieces if piece)


def _xml_markdown(root):
    return _XMLMarkdown(root).render()


def _json_markdown(value):
    pieces = []
    def visit(item, depth=0, path='$'):
        if depth > MAX_DEPTH:
            raise ValueError('JSON 嵌套层次过深')
        if isinstance(item, dict):
            for key, child in item.items():
                pieces.append('#' * min(6, max(2, depth + 2)) + ' ' + _escape(str(key)))
                visit(child, depth + 1, path + '.' + str(key))
        elif isinstance(item, list):
            for index, child in enumerate(item, 1):
                if isinstance(child, (dict, list)):
                    child_path = path + f'[{index - 1}]'
                    pieces.append('#' * min(6, max(2, depth + 2)) + ' ' + _code(child_path))
                    visit(child, depth + 1, child_path)
                else:
                    pieces.append(f'{index}. ' + scalar(child))
        else:
            pieces.append(scalar(item))
    def scalar(item):
        if isinstance(item, str):
            return _escape(item)
        return json.dumps(item, ensure_ascii=False, allow_nan=False)
    visit(value)
    return '\n\n'.join(pieces)


def render_markdown(data, metadata, source_url):
    """输出 UTF-8 Markdown 字节；调用方为 raw 与 md 各生成独立元数据。"""
    found = _inspect(data, metadata)
    if not found:
        raise ValueError('响应未经法规正文确认，不能转换为 Markdown')
    kind, parsed = found
    if kind['extension'] == 'html':
        body = _html_markdown(parsed, source_url)
    elif kind['extension'] == 'xml':
        body = _xml_markdown(parsed)
    else:
        body = _json_markdown(parsed)
    title = _value(metadata, 'title')
    prefix = '# ' + _escape(_plain(title)) + '\n\n' if isinstance(title, str) and title.strip() else ''
    # 不将下载时间伪装为发布日期；原文与来源通过 sidecar 元数据关联。
    return (prefix + re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n', body).strip() + '\n').encode('utf-8')
