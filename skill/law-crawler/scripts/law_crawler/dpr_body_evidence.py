"""从原件第一页生成可复核的正文身份证据，不执行任务合并。"""
import re
from .platform_adapters.census_dpr import normalized_title

KINDS = {'INSTRUKSI PRESIDEN': 'inpres', 'KEPUTUSAN PRESIDEN': 'keppres',
         'PENETAPAN PRESIDEN': 'penpres',
         'PERATURAN PRESIDEN': 'perpres', 'PERATURAN PEMERINTAH': 'pp',
         'UNDANG-UNDANG': 'uu'}


def first_page_identity(text):
    # 限定首页抬头，不把正文的被引用法规身份误认为本法规。
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'^(?:SALINAN\s+)?PRESIDEN REPUBLIK INDONESIA\s+(?=(?:INSTRUKSI|KEPUTUSAN|PENETAPAN|PERATURAN|UNDANG-UNDANG)\b)', '', text, flags=re.I)
    header = re.match(r'^(INSTRUKSI PRESIDEN|KEPUTUSAN PRESIDEN|PENETAPAN PRESIDEN|PERATURAN PRESIDEN|'
                      r'PERATURAN PEMERINTAH|UNDANG-UNDANG)\s+'
                      r'(?:REPUBLIK INDONESIA\s*)?(?:\([A-Z]+\)\s*)?'
                      r'NOMOR\s+(\d+)\s+TAHUN\s+(\d{4})\s*'
                      r'(?:\([^)]{1,30}\)\s*)?TENTANG\s+(.+?)\s+'
                      r'PRESIDEN REPUBLIK INDONESIA\b', text, re.I)
    if not header:
        # 老法规排版显式列出 Bentuk / Oleh / Nomor / Tentang / Indeks；
        # 只接受页首字段序列，标题在 Indeks 前终止，避免引用法规污染。
        header = re.match(r'^Bentuk\s*:\s*(INSTRUKSI PRESIDEN|KEPUTUSAN PRESIDEN|PENETAPAN PRESIDEN|'
                          r'PERATURAN PRESIDEN|PERATURAN PEMERINTAH|UNDANG-UNDANG)'
                          r'\s*(?:\([A-Z]+\)\s*)?Oleh\s*:\s*PRESIDEN REPUBLIK INDONESIA\s*'
                          r'Nomor\s*:\s*(\d+)\s+TAHUN\s+(\d{4})\s*'
                          r'(?:\([^)]{1,30}\)\s*)?Tanggal\s*:[^:]{1,120}'
                          r'Sumber\s*:[^:]{0,180}Tentang\s*:\s*(.+?)\s+Indeks\s*:', text, re.I)
    if not header:
        return None
    kind, number, year, title = header.groups()
    title = re.sub(r'\s+DENGAN RAHMAT TUHAN YANG MAHA ESA\s*$', '', title, flags=re.I)
    return dict(identity=[KINDS[kind.upper()], str(int(number)), year], title=title,
                first_page_header=header.group(0))


def official_html_text(html):
    from law_crawler.regulation_metadata import _Document
    def content(node):
        if isinstance(node, str):
            return node
        if node.tag in ('head', 'style', 'script', 'title'):
            return ''
        return ' '.join(content(child) for child in node.children)
    return content(_Document(html).root).strip()


def compare_body(evidence, text):
    body = first_page_identity(text)
    target_title = evidence.get('fields', {}).get('judul')
    exact = bool(body and tuple(body['identity']) == tuple(evidence.get('identity') or ())
                 and len(normalized_title(target_title)) >= 8
                 and normalized_title(target_title) == normalized_title(body['title']))
    reason = ('full_header_identity_and_subject_confirmed' if exact else
              'first_page_header_not_recognized_or_scanned' if not body else
              'body_identity_conflicts_with_catalogue' if tuple(body['identity']) != tuple(evidence.get('identity') or ()) else
              'complete_subject_title_differs')
    return dict(status='exact_body_identity_and_title' if exact else 'independent_review_required', reason=reason,
                body=body, target_identity=evidence.get('identity'), target_title=target_title,
                match_policy='no_fuzzy_matching_no_numeric_correction')


def scan(manifest, output, sink, limit=0, max_pages=1, max_mib=32):
    """顺序只读OSS；默认32MiB，可显式提高至128MiB，仍只提取前两页。"""
    import hashlib
    import json
    from pathlib import Path
    import subprocess
    import tempfile
    import time
    if type(max_mib) is not int or not 1 <= max_mib <= 128 or max_pages not in (1, 2):
        raise ValueError('review_limits_out_of_range')
    max_bytes = max_mib * 1024**2
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    if output.exists():
        for line in output.read_text().splitlines():
            row = json.loads(line)
            seen.add((row['target_id'], row['source_id'], row['sha256']))
    processed = 0
    with output.open('a') as log:
        for item in manifest:
            token = (item['target_id'], item['source_id'], item['sha256'])
            if token in seen:
                continue
            if limit and processed >= limit:
                break
            started = time.monotonic()
            result = {k: item[k] for k in ('target_id', 'source_id', 'sha256', 'raw_key')}
            try:
                if item['size_bytes'] > max_bytes:
                    raise ValueError(f'pdf_exceeds_review_limit_{max_mib}MiB')
                with tempfile.TemporaryDirectory(dir=output.parent) as folder:
                    pdf = Path(folder) / 'original.pdf'
                    last_error = None
                    for attempt in range(3):
                        try:
                            response = sink._bucket().get_object(item['raw_key'])
                            digest, size = hashlib.sha256(), 0
                            try:
                                with pdf.open('wb') as file:
                                    while True:
                                        chunk = response.read(256 * 1024)
                                        if not chunk:
                                            break
                                        size += len(chunk)
                                        if size > max_bytes:
                                            raise ValueError(f'pdf_exceeds_review_limit_{max_mib}MiB')
                                        digest.update(chunk); file.write(chunk)
                            finally:
                                response.close()
                            if digest.hexdigest() != item['sha256'] or size != item['size_bytes']:
                                raise ValueError('oss_sha256_or_size_mismatch')
                            last_error = None
                            break
                        except Exception as exc:
                            last_error = exc
                            if isinstance(exc, ValueError):
                                break
                            time.sleep(2**attempt)
                    if last_error:
                        raise last_error
                    if item['raw_key'].lower().endswith(('.html', '.htm')):
                        if size > 2 * 1024**2:
                            raise ValueError('html_exceeds_review_limit_2MiB')
                        pages = [official_html_text(pdf.read_text(errors='replace'))]
                        result['source_format'] = 'html'
                    else:
                        completed = subprocess.run(['pdftotext', '-f', '1', '-l', str(max_pages), str(pdf), '-'],
                                                   capture_output=True, text=True, timeout=30)
                        if completed.returncode:
                            raise ValueError('pdf_text_extraction_failed')
                        pages = completed.stdout.split('\f')[:max_pages]
                        result['source_format'] = 'pdf'
                    result.update(compare_body(item['evidence'], pages[0]))
                    result['evidence_page'] = 1
                    if not result.get('body') and len(pages) > 1:
                        second = compare_body(item['evidence'], pages[1])
                        if second.get('body'):
                            result.update(second)
                            result['evidence_page'] = 2
                    # 保留被哈希的完整提取文本；截断展示稿不能冒充可复核证据。
                    # 输入已有文件大小、HTML 2MiB 和 PDF 前两页的边界。
                    result['page_texts'] = pages
                    result['extracted_first_page_sha256'] = hashlib.sha256(pages[0].encode()).hexdigest()
                    result['first_page_text'] = pages[0]
                    result['extracted_page_sha256'] = [hashlib.sha256(page.encode()).hexdigest() for page in pages]
                    result['original_verified'] = True
                    result['attempts'] = attempt + 1
            except Exception as exc:
                result.update(status='independent_review_required', reason=type(exc).__name__ + ': ' +
                              (str(exc) if isinstance(exc, ValueError) else 'OSS_or_extraction_error'))
            result['duration_seconds'] = round(time.monotonic() - started, 3)
            result['max_review_bytes'] = max_bytes
            log.write(json.dumps(result, ensure_ascii=False) + '\n'); log.flush()
            processed += 1
    return processed


def main():
    import argparse
    import json
    from pathlib import Path
    from .oss_sink import OSSSink
    from .storage_config import storage_from_batch
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--credentials', required=True)
    p.add_argument('--batch', required=True, help='Existing batch with explicit OSS destination in plan.json')
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--max-pages', type=int, choices=(1, 2), default=1)
    p.add_argument('--max-mib', type=int, choices=range(1, 129), default=32)
    a = p.parse_args()
    sink = OSSSink(credentials=a.credentials, cache_path=Path(a.output).with_suffix('.unused-cache.json'), **storage_from_batch(a.batch))
    print(json.dumps({'processed': scan(json.loads(Path(a.manifest).read_text()), a.output, sink, a.limit, a.max_pages, a.max_mib)}))


if __name__ == '__main__':
    main()
