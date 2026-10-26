"""只读原件前两页 OCR 身份证据；OCR 命中仍须独立视觉核验，不能直接归档。"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import time

from .dpr_body_evidence import compare_body
from .oss_sink import OSSSink


def scan(manifest, output, sink, limit=5, language='eng'):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    log_path = output / 'results.jsonl'
    seen = set()
    if log_path.exists():
        for line in log_path.read_text().splitlines():
            row = json.loads(line)
            seen.add((row['target_id'], row['source_id'], row['sha256']))
    count = 0
    with log_path.open('a') as log:
        for item in manifest:
            if not all(re.fullmatch('[0-9a-f]{64}', item.get(key, '')) for key in ('target_id', 'source_id', 'sha256')):
                raise ValueError('invalid_manifest_identity')
            if (item['target_id'], item['source_id'], item['sha256']) in seen:
                continue
            if limit and count >= limit:
                break
            started = time.monotonic()
            row = {k: item[k] for k in ('target_id', 'source_id', 'sha256', 'raw_key')}
            row.update(status='pending_independent_visual_review', pages=[], language=language,
                       engine='tesseract', dpi=180, source_evidence=item['evidence'])
            folder = output / (item['target_id'] + '-' + item['sha256'][:12])
            folder.mkdir(exist_ok=True)
            pdf = folder / 'source.pdf'
            try:
                if item['size_bytes'] > 32 * 1024**2:
                    raise ValueError('original_exceeds_32MiB_review_bound')
                last_error = None
                for attempt in range(3):
                    try:
                        response = sink._bucket().get_object(item['raw_key'])
                        digest, size = hashlib.sha256(), 0
                        try:
                            with pdf.open('wb') as f:
                                while True:
                                    chunk = response.read(256 * 1024)
                                    if not chunk:
                                        break
                                    size += len(chunk)
                                    if size > 32 * 1024**2:
                                        raise ValueError('original_exceeds_32MiB_review_bound')
                                    digest.update(chunk); f.write(chunk)
                        finally:
                            response.close()
                        if digest.hexdigest() != item['sha256'] or size != item['size_bytes']:
                            raise ValueError('original_SHA256_or_size_mismatch')
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                        if isinstance(exc, ValueError):
                            break
                        time.sleep(2**attempt)
                if last_error:
                    raise last_error
                row['original_verified'] = True
                for page in (1, 2):
                    page_result = dict(page=page)
                    row['pages'].append(page_result)
                    prefix = folder / ('page-' + str(page))
                    image = prefix.with_suffix('.png')
                    try:
                        render = subprocess.run(['pdftoppm', '-f', str(page), '-l', str(page),
                            '-r', '180', '-scale-to', '2400', '-singlefile', '-png', str(pdf), str(prefix)],
                            capture_output=True, timeout=45)
                        if render.returncode:
                            page_result['reason'] = 'page_not_available_or_render_failed'
                            continue
                        page_result['image_path'] = str(image)
                        page_result['image_sha256'] = hashlib.sha256(image.read_bytes()).hexdigest()
                        ocr = subprocess.run(['tesseract', str(image), 'stdout', '-l', language, '--psm', '3'],
                            capture_output=True, text=True, timeout=60, env=dict(os.environ, OMP_THREAD_LIMIT='1'))
                        if ocr.returncode:
                            page_result['reason'] = 'ocr_failed'
                            continue
                        page_result.update(ocr_text=ocr.stdout,
                            ocr_text_sha256=hashlib.sha256(ocr.stdout.encode()).hexdigest(),
                            comparison=compare_body(item['evidence'], ocr.stdout))
                        page_result['reason'] = 'ocr_candidate_requires_visual_identity_and_title_review'
                    except subprocess.TimeoutExpired:
                        page_result['reason'] = 'bounded_page_processing_timeout'
                row['reason'] = 'ocr_does_not_authorize_identity_merge'
            except Exception as exc:
                row['reason'] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            finally:
                pdf.unlink(missing_ok=True)
            row['duration_seconds'] = round(time.monotonic() - started, 3)
            log.write(json.dumps(row, ensure_ascii=False) + '\n'); log.flush()
            count += 1
    return count


def main():
    import fcntl
    from .storage_config import storage_from_batch
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--credentials', required=True)
    p.add_argument('--batch', required=True, help='Existing batch with explicit OSS destination in plan.json')
    p.add_argument('--limit', type=int, default=5)
    p.add_argument('--language', default='eng')
    a = p.parse_args()
    destination = storage_from_batch(a.batch)
    Path(a.output).mkdir(parents=True, exist_ok=True)
    with (Path(a.output) / '.scan.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('同一 OCR 证据目录已有运行进程；拒绝重复启动')
        sink = OSSSink(credentials=a.credentials, cache_path=Path(a.output) / 'unused-cache.json', **destination)
        print(json.dumps({'processed': scan(json.loads(Path(a.manifest).read_text()), a.output, sink, a.limit, a.language)}))


if __name__ == '__main__':
    main()
