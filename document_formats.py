"""按原始字节识别官方文件；结构检查不是完整渲染或恶意代码扫描。

不解包到磁盘、不转码。未知/无法可靠识别的格式拒绝保存。
"""
import binascii
import io
import posixpath
import struct
import zipfile
import zlib
from xml.etree import ElementTree as ET
from urllib.parse import unquote, urlsplit

MAX_EXPANDED = 200 * 1024 * 1024
MAX_XML = 8 * 1024 * 1024
MAX_LAW_XML = 40 * 1024 * 1024


def _canadian_law_xml(data):
    """仅接受司法部 LIMS 的 Statute/Regulation 正文，不接受泛 XML。

    XML 结构用于格式识别，官方来源仍由下载器的域名白名单核验。
    已核实的司法部原件为 UTF-8；其他编码先拒绝，避免 DTD 字节检查失效。
    """
    if len(data) > MAX_LAW_XML:
        raise ValueError('加拿大法规 XML 超过 40 MiB 验证上限')
    try:
        raw = data.decode('utf-8-sig')
    except UnicodeError as exc:
        raise ValueError('加拿大法规 XML 必须使用 UTF-8 编码') from exc
    if '\x00' in raw or '<!DOCTYPE' in raw.upper() or '<!ENTITY' in raw.upper():
        raise ValueError('加拿大法规 XML 不允许 DTD、实体声明或空字符')
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError) as exc:
        raise ValueError('加拿大法规 XML 结构无效') from exc
    if root.tag not in ('Statute', 'Regulation') or not root.get('{http://justice.gc.ca/lims}id', '').strip():
        raise ValueError('不是加拿大司法部 Statute/Regulation 正文 XML')
    identities, bodies = root.findall('Identification'), root.findall('Body')
    if len(identities) != 1 or len(bodies) != 1:
        raise ValueError('加拿大法规 XML 身份或正文节点缺失/重复')

    def has_text(node):
        return node is not None and bool(''.join(node.itertext()).strip())

    identity = identities[0]
    title = any(has_text(identity.find(tag)) for tag in ('ShortTitle', 'LongTitle'))
    number = identity.find('Chapter/ConsolidatedNumber') if root.tag == 'Statute' else identity.find('InstrumentNumber')
    if not title or not has_text(number):
        raise ValueError('加拿大法规 XML 缺少标题或法律编号')
    if not any(has_text(node) for node in bodies[0].findall('.//Section//Text')):
        raise ValueError('加拿大法规 XML 缺少条文正文')


def validate_pdf(data):
    # 保持历史 PDF 校验及旧记录兼容；不声称验证了所有 PDF 对象。
    if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-4096:]:
        raise ValueError('响应不是完整 PDF（文件头或结束标记缺失）')


def _xml(archive, name):
    if archive.getinfo(name).file_size > MAX_XML:
        raise ValueError('文档 XML 超过结构验证上限')
    raw = archive.read(name)
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('文档 XML 不允许外部实体或 DTD')
    return ET.fromstring(raw)


def _part_name(value):
    parts = urlsplit(value)
    if not value or parts.scheme or parts.netloc or parts.query or parts.fragment:
        raise ValueError('OOXML 主文档必须是包内文件路径')
    path = unquote(parts.path)
    if '\\' in path or any(ord(c) < 32 for c in path):
        raise ValueError('OOXML 包内路径含无效字符')
    path = posixpath.normpath(path.lstrip('/'))
    if path in ('.', '..') or path.startswith('../'):
        raise ValueError('OOXML 包内路径越界')
    return path


def _zip(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [e.filename for e in entries]
            if not entries or len(entries) > 10000 or len(set(names)) != len(names):
                raise ValueError('ZIP 为空、条目过多或含重复路径')
            if sum(e.file_size for e in entries) > MAX_EXPANDED:
                raise ValueError('ZIP 展开大小超出验证上限')
            if any(e.flag_bits & 1 for e in entries):
                raise ValueError('不支持加密 ZIP')
            if archive.testzip() is not None:
                raise ValueError('ZIP CRC 校验失败')
            if '[Content_Types].xml' in names:
                root = _xml(archive, '[Content_Types].xml')
                ns = '{http://schemas.openxmlformats.org/package/2006/content-types}'
                if root.tag != ns + 'Types' or '_rels/.rels' not in names:
                    raise ValueError('OOXML 内容类型或包关系缺失')
                relationships = _xml(archive, '_rels/.rels')
                if relationships.tag != '{http://schemas.openxmlformats.org/package/2006/relationships}Relationships':
                    raise ValueError('OOXML 包关系根节点无效')
                rel_ns = '{http://schemas.openxmlformats.org/package/2006/relationships}'
                office_types = {'http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument', 'http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument'}
                main_links = [node for node in relationships if node.tag == rel_ns + 'Relationship' and node.get('Type') in office_types]
                if len(main_links) != 1 or main_links[0].get('TargetMode', 'Internal') != 'Internal':
                    raise ValueError('OOXML 必须有唯一的内部 officeDocument 关系')
                main = _part_name(main_links[0].get('Target', ''))
                kinds = {
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml': ('docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml': ('xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
                    'application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml': ('pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation'),
                }
                overrides = [node for node in root if node.tag == ns + 'Override' and _part_name(node.get('PartName', '')) == main]
                if len(overrides) != 1 or overrides[0].get('ContentType') not in kinds:
                    raise ValueError('不支持或不明确的 OOXML 主文档内容类型')
                ext, mime = kinds[overrides[0].get('ContentType')]
                main_root = _xml(archive, main)
                expected = {'docx': ('wordprocessingml', 'document'), 'xlsx': ('spreadsheetml', 'workbook'), 'pptx': ('presentationml', 'presentation')}[ext]
                allowed_ns = (f'http://schemas.openxmlformats.org/{expected[0]}/2006/main', f'http://purl.oclc.org/ooxml/{expected[0]}/main')
                if main_root.tag not in {f'{{{ns}}}{expected[1]}' for ns in allowed_ns}:
                    raise ValueError('OOXML 主文档根节点无效')
                return ext, mime
            if 'mimetype' in names:
                if archive.getinfo('mimetype').file_size > 256:
                    raise ValueError('ODF mimetype 过长')
                mime = archive.read('mimetype').decode('ascii').strip()
                kinds = {'application/vnd.oasis.opendocument.text': 'odt', 'application/vnd.oasis.opendocument.spreadsheet': 'ods', 'application/vnd.oasis.opendocument.presentation': 'odp'}
                if mime not in kinds:
                    raise ValueError('不支持的 ZIP mimetype')
                content = _xml(archive, 'content.xml')
                if content.tag != '{urn:oasis:names:tc:opendocument:xmlns:office:1.0}document-content':
                    raise ValueError('ODF 主文档根节点无效')
                manifest = _xml(archive, 'META-INF/manifest.xml')
                if manifest.tag != '{urn:oasis:names:tc:opendocument:xmlns:manifest:1.0}manifest':
                    raise ValueError('ODF 清单根节点无效')
                return kinds[mime], mime
            return 'zip', 'application/zip'
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, KeyError, ET.ParseError, UnicodeError, EOFError, zlib.error) as exc:
        raise ValueError(f'ZIP/办公文档结构无效: {exc}') from exc


def _png(data):
    pos, seen_idat, seen_ihdr = 8, False, False
    compressed = bytearray()
    while pos + 12 <= len(data):
        size = struct.unpack_from('>I', data, pos)[0]
        kind = data[pos + 4:pos + 8]
        end = pos + 12 + size
        if end > len(data):
            break
        body = data[pos + 8:pos + 8 + size]
        if binascii.crc32(kind + body) & 0xffffffff != struct.unpack_from('>I', data, end - 4)[0]:
            raise ValueError('PNG CRC 校验失败')
        if not seen_ihdr:
            if kind != b'IHDR' or size != 13 or not all(struct.unpack_from('>II', body)):
                raise ValueError('PNG IHDR 无效')
            width, height = struct.unpack_from('>II', body)
            bit_depth, color, compression, filtering, interlace = body[8:]
            allowed = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            if bit_depth not in allowed.get(color, ()) or compression or filtering or interlace not in (0, 1):
                raise ValueError('PNG IHDR 编码无效')
            seen_ihdr = True
        elif kind == b'IHDR':
            raise ValueError('PNG 重复 IHDR')
        if kind == b'IDAT':
            seen_idat = True
            compressed.extend(body)
        if kind == b'IEND':
            if size == 0 and seen_idat and end == len(data):
                try:
                    decoder = zlib.decompressobj()
                    inflated = decoder.decompress(compressed, MAX_EXPANDED + 1)
                    if len(inflated) > MAX_EXPANDED or not decoder.eof or decoder.unused_data:
                        raise ValueError('PNG 压缩数据不完整或超过验证上限')
                except zlib.error as exc:
                    raise ValueError('PNG 压缩流无效') from exc
                channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
                passes = [(0, 0, 1, 1)] if not interlace else [(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
                offset = 0
                for x, y, dx, dy in passes:
                    pw, ph = max(0, (width - x + dx - 1) // dx), max(0, (height - y + dy - 1) // dy)
                    if not pw or not ph:
                        continue
                    row_size = (pw * channels * bit_depth + 7) // 8 + 1
                    if offset + row_size * ph > len(inflated):
                        raise ValueError('PNG 像素行不完整')
                    if any(inflated[offset + row_size * row] > 4 for row in range(ph)):
                        raise ValueError('PNG 像素行过滤器无效')
                    offset += row_size * ph
                if offset != len(inflated):
                    raise ValueError('PNG 像素行长度无效')
                return
            break
        pos = end
    raise ValueError('PNG 不完整')


def _jpeg(data):
    pos, frame, scan = 2, False, False
    component_ids = set()
    while pos < len(data):
        if data[pos] != 255:
            raise ValueError('JPEG 标记无效')
        while pos < len(data) and data[pos] == 255:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker == 0xd9:
            if frame and scan and pos == len(data):
                return
            break
        if marker in (0, 0xd8) or 0xd0 <= marker <= 0xd7:
            raise ValueError('JPEG 标记顺序无效')
        if pos + 2 > len(data):
            break
        length = struct.unpack_from('>H', data, pos)[0]
        if length < 2 or pos + length > len(data):
            break
        if marker in (0xc0, 0xc1, 0xc2):
            if length < 8 or not all(struct.unpack_from('>HH', data, pos + 3)):
                raise ValueError('JPEG 图像尺寸无效')
            count = data[pos + 7]
            if not 1 <= count <= 4 or length != 8 + 3 * count or data[pos + 2] not in (8, 12):
                raise ValueError('JPEG 帧分量表无效')
            component_ids = {data[pos + 8 + i * 3] for i in range(count)}
            if len(component_ids) != count:
                raise ValueError('JPEG 帧分量重复')
            for i in range(count):
                sampling, quant_table = data[pos + 9 + i * 3:pos + 11 + i * 3]
                if not 1 <= sampling >> 4 <= 4 or not 1 <= sampling & 15 <= 4 or quant_table > 3:
                    raise ValueError('JPEG 分量采样或量化表无效')
            frame = True
        if marker == 0xda:
            if not frame or length < 6:
                raise ValueError('JPEG 扫描头缺失帧或分量表')
            count = data[pos + 2]
            if not 1 <= count <= len(component_ids) or length != 6 + 2 * count:
                raise ValueError('JPEG 扫描分量表无效')
            scan_ids = [data[pos + 3 + i * 2] for i in range(count)]
            if len(set(scan_ids)) != count or not set(scan_ids) <= component_ids:
                raise ValueError('JPEG 扫描引用未知分量')
        pos += length
        if marker == 0xda:
            scan = True
            entropy_bytes = 0
            # 压缩流中 FF00 是转义，FFD0..FFD7 是重启标记。
            while pos < len(data):
                if data[pos] != 255:
                    entropy_bytes += 1
                    pos += 1
                elif pos + 1 < len(data) and (data[pos + 1] == 0 or 0xd0 <= data[pos + 1] <= 0xd7):
                    if data[pos + 1] == 0:
                        entropy_bytes += 1
                    pos += 2
                else:
                    break
            if not entropy_bytes:
                raise ValueError('JPEG 扫描数据为空')
    raise ValueError('JPEG 不完整或不支持的编码')


def _rtf(data):
    depth, pos = 0, 0
    while pos < len(data):
        char = data[pos]
        if char == 92:
            pos += 1
            if data[pos:pos + 3] == b'bin':
                raise ValueError('暂不支持带二进制段的 RTF')
            if pos < len(data) and data[pos] in b'{}\\':
                pos += 1
                continue
        elif char == 123:
            depth += 1
        elif char == 125:
            depth -= 1
            if depth < 0 or depth == 0 and data[pos + 1:].strip():
                raise ValueError('RTF 大括号结构无效')
        pos += 1
    if depth or not data.rstrip().endswith(b'}'):
        raise ValueError('RTF 不完整')


def identify_document(data):
    """返回真实格式的 extension（不含点）和 media_type；失败抛 ValueError。"""
    if not isinstance(data, bytes) or not data:
        raise ValueError('文件内容为空或不是字节')
    if data.startswith(b'%PDF-'):
        validate_pdf(data)
        kind = 'pdf', 'application/pdf'
    elif data.startswith(b'PK\x03\x04'):
        kind = _zip(data)
    elif data.startswith(b'{\\rtf1'):
        _rtf(data)
        kind = 'rtf', 'application/rtf'
    elif data.startswith(b'\x89PNG\r\n\x1a\n'):
        _png(data)
        kind = 'png', 'image/png'
    elif data.startswith(b'\xff\xd8'):
        _jpeg(data)
        kind = 'jpg', 'image/jpeg'
    elif data.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
        raise ValueError('暂不支持 OLE 旧 Office 文件；不能仅凭签名区分 DOC/XLS/PPT')
    elif data.startswith((b'II*\x00', b'MM\x00*')):
        raise ValueError('暂不支持 TIFF 结构验证，请保留原件后人工核验')
    elif data.removeprefix(b'\xef\xbb\xbf').lstrip().startswith(b'<'):
        _canadian_law_xml(data)
        kind = 'xml', 'application/xml'
    else:
        raise ValueError('不是受支持的 PDF/办公文档/图片/ZIP 原件（HTML/JSON 错误页不会作为原件保存）')
    return dict(extension=kind[0], media_type=kind[1])
