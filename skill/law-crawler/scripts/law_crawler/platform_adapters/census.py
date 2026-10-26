"""冻结目录原件发现：保留混合材料身份，不把目录数量等同于法规数量。

仅返回官方目录/详情明确公布的文件链接；传输、重试、字节校验与 OSS
持久化由调用方负责。目录直链优先，附件穷尽程度始终单独记录。
"""
import re
from copy import deepcopy
from urllib.parse import parse_qs, quote, unquote, urldefrag, urljoin, urlsplit

from law_crawler.regulation_metadata import _Document, extract_metadata
from .base import Document, document_url, field, text_html
from . import ADAPTERS
from .census_finance import source_availability
from .census_minor import BKPM_DOCUMENT_STORAGE_HOST, bkpm_storage_document, reject_access_error, missing_original_evidence
from .census_dpr import extract_dpr_evidence


def allowed_hosts(platform):
    if platform == 'ojk.go.id-jdih-supplement':
        return ('jdih.ojk.go.id', 'www.ojk.go.id', 'ojk.go.id')
    platform = {'dpr': 'dpr.go.id'}.get(platform, platform)
    adapter = ADAPTERS.get(platform)
    if adapter is None:
        raise ValueError('未登记的目录平台: ' + platform)
    return tuple(adapter.allowed_hosts) + ((BKPM_DOCUMENT_STORAGE_HOST,) if platform == 'bkpm.go.id' else ())


def _url(base, raw, hosts):
    if not isinstance(raw, str) or raw.strip() in ('', '-'):
        return None
    target = quote(urldefrag(urljoin(base, raw.strip()))[0], safe=":/?[]@!$&'()*+,;=%")
    p = urlsplit(target)
    return target if p.scheme in ('https', 'http') and p.hostname in hosts and not p.username and not p.password else None


def _metadata(record, html=None):
    url = record['url']
    result = extract_metadata(html, url, record.get('platform', '')) if html else {}
    result.update(deepcopy(record.get('metadata') or {}))
    for key in ('title', 'published_at', 'promulgated_at', 'enacted_at', 'updated_at'):
        if record.get(key) is not None and not (isinstance(result.get(key), dict) and result[key].get('value')):
            result[key] = field(record[key], record[key], 'official_catalogue.' + key, url,
                                '冻结目录中的官方字段；日期语义和截止核验状态见 catalogue_record。')
    identity = record.get('identity') or dict(jurisdiction='ID', issuer=record.get('issuer'),
        type=record.get('type'), number=record.get('number'), year=record.get('year'))
    result.setdefault('legal_identity', field(identity, identity, 'official_catalogue', url,
        '目录身份线索，尚未独立复核。', 'partial'))
    result.setdefault('document_type', field(None, record.get('type'), 'official_catalogue.type', url,
        '混合目录材料；下载不等于确认正式法规身份。', 'pending_review'))
    result['processing_gate'] = 'pending_document_type_review'
    result['catalogue_record'] = deepcopy(record)
    result['all_attachments_verified'] = False
    return result


def read_catalog_document(platform, record, fetch):
    """fetch(url) -> (bytes, final_url); 返回 Document，允许没有可下载文件。"""
    hosts = allowed_hosts(platform)
    base = record['url']
    if not _url(base, base, hosts):
        raise ValueError('目录详情域名未登记')
    files, roles = [], {}

    def add(raw, role, context, source=base):
        target = _url(source, raw, hosts)
        if target:
            if target not in files:
                files.append(target)
            roles.setdefault(target, dict(role=role, status='official_link_observed', detail_url=base,
                                          html_context=context))

    # 目录 API 已提供正式文件字段；避免为了同一文件再次抓取 HTML 壳页面。
    official = [record.get('official_metadata') or {}] + (record.get('catalogue_variants') or [])
    for row in official:
        if platform == 'bkpm.go.id':
            for key in ('document_file_url', 'document_file_url_en'):
                raw = row.get(key)
                if isinstance(raw, str) and urlsplit(raw).hostname == BKPM_DOCUMENT_STORAGE_HOST and not bkpm_storage_document(raw):
                    continue
                add(raw, 'regulation_body' if key == 'document_file_url' else 'official_translation', key)
        elif platform == 'kemenkeu.go.id':
            add(row.get('full_text_pdf'), 'regulation_body', 'full_text_pdf')
    if files:
        metadata = _metadata(record)
        metadata['file_roles'] = roles
        metadata['file_discovery_basis'] = 'official_catalogue_explicit_file_fields'
        return Document(base, files, metadata)

    if platform == 'kemendag.go.id':
        return ADAPTERS[platform].read_document_bulk(base, fetch)
    body, final = fetch(base)
    if not _url(base, final, hosts):
        raise ValueError('详情重定向至未登记主机')
    html = text_html(body)
    reject_access_error(html)
    metadata = _metadata(record, html)
    for node in _Document(html).root.walk():
        attr = {'a': 'href', 'iframe': 'src', 'embed': 'src', 'object': 'data'}.get(node.tag)
        raw = node.attrs.get(attr) if attr else None
        if not raw:
            continue
        target = _url(final, raw, hosts)
        if not target:
            continue
        if urlsplit(target).path.endswith('/viewer.html'):
            target = _url(final, parse_qs(urlsplit(target).query).get('file', [''])[0], hosts)
        if not target:
            continue
        path = unquote(urlsplit(target).path)
        label = node.text().strip()
        # JDIH OJK 的无后缀下载入口，排除 landasan 法律依据链接。
        ojk_download = platform == 'ojk.go.id-jdih-supplement' and re.fullmatch(
            r'/Web/ViewPeraturan/DownloadDokumen/[0-9a-f-]+', path, re.I)
        legacy_document = path.lower().endswith(('.doc', '.xls', '.ppt', '.csv', '.txt', '.xml'))
        if not (document_url(target) or legacy_document or ojk_download or
                (platform == 'kemenkeu.go.id' and path.startswith('/api/download/fulltext/'))):
            continue
        descriptor = label + ' ' + path.rsplit('/', 1)[-1]
        own_faq = bool(re.search(r'\bfaq\b', str(record.get('title', '')), re.I))
        if re.search(r'abstrak|abstract|faq', descriptor, re.I) and not (own_faq and re.search(r'\bfaq\b', descriptor, re.I)):
            # 附属摘要、问答不替代正文；目录本身若是草案仍归档并保留类别。
            metadata.setdefault('excluded_supplement_links', []).append(target)
            continue
        if platform in ('dpr', 'dpr.go.id') and label.upper() != 'UNDUH BERKAS':
            continue
        role = 'official_non_regulatory_material' if own_faq else 'regulation_body' if ojk_download or label.upper() == 'UNDUH BERKAS' else 'related_or_unconfirmed_document_pending'
        add(target, role, label or node.tag, final)
    metadata['file_roles'] = roles
    metadata['file_discovery_basis'] = 'official_detail_explicit_links'
    evidence = missing_original_evidence(platform, html, record)
    if evidence:
        metadata['source_availability'] = evidence
        if not files:metadata['no_original_reason'] = evidence['code']
    if platform == 'kemenkeu.go.id':
        metadata.update(source_availability(record, html, files))
    if platform in ('dpr', 'dpr.go.id'):
        metadata['source_availability'] = extract_dpr_evidence(record, html)
        if not files:metadata['no_original_reason'] = metadata['source_availability']['reason']
    return Document(final, files, metadata)
