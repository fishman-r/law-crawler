"""小平台无原件诊断；仅接受官方字段列明的精确存储服务。"""
import re
from urllib.parse import urlsplit


BKPM_DOCUMENT_STORAGE_HOST = 'minio-jb.globalpiranti.co.id'


def bkpm_storage_document(raw):
    """仅用于 BKPM 官方 document_file_url 字段，不授权一般外链。"""
    if not isinstance(raw, str):
        return None
    try:
        p = urlsplit(raw)
        if (p.scheme == 'https' and p.hostname == BKPM_DOCUMENT_STORAGE_HOST
                and p.port in (None, 443) and not p.username and not p.password
                and p.path.startswith('/jdih/jdih/') and p.path.lower().endswith('.pdf')
                and not p.query and not p.fragment):
            return raw
    except ValueError:
        pass
    return None


def reject_access_error(html):
    """HTTP 200 的拦截页是访问失败，不能推论官方缺少原件。"""
    if re.search(r'<title>\s*Request Rejected\s*</title>', html, re.I) or (
            'The requested URL was rejected' in html and 'support ID' in html):
        raise ValueError('access_blocked: HTTP200 Request Rejected，无法核验官方原件')


def missing_original_evidence(platform, html, record=None):
    """保存来源明确无文件、缺失链接和访问失败的不同证据。"""
    reject_access_error(html)
    if platform == 'kemendag.go.id' and 'Dokumen peraturan tidak tersedia' in html:
        return {'code': 'official_primary_document_unavailable',
                'source_text': 'Dokumen peraturan tidak tersedia.'}
    if platform == 'ojk.go.id-jdih-supplement' and 'Dokumen Belum Tersedia' in html:
        return {'code': 'official_document_not_yet_available',
                'source_text': 'Dokumen Belum Tersedia'}
    if platform == 'bkpm.go.id' and record is not None:
        row = record.get('official_metadata') or {}
        if 'document_file_url' in row and not row.get('document_file_url') and not row.get('document_file_url_en'):
            return {'code': 'official_catalogue_file_fields_empty',
                    'fields': ['document_file_url', 'document_file_url_en'],
                    'document_type': row.get('document_type')}
    return None
