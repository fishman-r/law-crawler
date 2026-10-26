"""财政部书目来源可用性诊断；不把封面、详情壳或缺失全文标成下载成功。"""
from urllib.parse import urlsplit


def source_availability(record, html='', file_urls=()):
    """在通用发现完成后调用；只在官方字段完整支持书目无全文时提供说明。

    返回元数据增量。保留任务与原始目录，不排除原授权范围，也不声称书籍
    在其他馆藏没有电子版。file_urls 非空时不得添加 no_original_reason。
    """
    official = record.get('official_metadata') or {}
    variants = [official] + (record.get('catalogue_variants') or [])
    if (not official or official.get('bentuk') != 'Monografi Hukum'
            or any(not isinstance(row, dict) or row.get('bentuk') != 'Monografi Hukum'
                   or row.get('full_text_pdf') or row.get('blocks') or row.get('jumlah_pasal')
                   for row in variants)):
        return {}
    image = official.get('image')
    cover = (isinstance(image, str) and urlsplit(image).hostname == 'perpustakaan.kemenkeu.go.id'
             and urlsplit(image).path.startswith('/img/FileCover/'))
    detail_missing = ('NEXT_HTTP_ERROR_FALLBACK;404' in html or
                      ('not-found.' in html and str(official.get('judul') or '') not in html))
    state = {
        'status': 'file_links_observed' if file_urls else 'full_text_not_provided_in_catalogue',
        'material_type': 'Monografi Hukum',
        'source_url': record['url'],
        'catalogue_url': record.get('catalogue_url'),
        'evidence': {
            'full_text_pdf': official.get('full_text_pdf'),
            'blocks': official.get('blocks'),
            'jumlah_pasal': official.get('jumlah_pasal'),
            'catalogue_variants_checked': len(variants),
            'cover_url': image if cover else None,
            'cover_is_original': False,
            'detail_not_found_shell_observed': detail_missing,
        },
        'scope_policy': 'retain_catalogue_task_and_metadata; do_not_count_as_original_downloaded',
        'other_library_full_text_availability': 'unverified',
    }
    result = {'source_availability': state}
    if not file_urls:
        result['no_original_reason'] = ('财政部法律书目未提供全文：官方全文字段为空，封面不属于原件'
            + ('；详情入口返回无正文页面' if detail_missing else ''))
    return result
