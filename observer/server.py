"""以 Python 标准库提供只读状态 API 和静态看板，默认仅监听本机。"""
import argparse
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
from urllib.parse import parse_qs, unquote, urlsplit

from observer.events import hooks_installed, session_detail, session_list
from observer.library import (CHUNK_BYTES, LibraryError, LibraryReader,
                              content_disposition, parse_range)
from observer.snapshot import SnapshotReader, host_metrics


def _safe_string(value, limit=120):
    return str(value or '')[:limit]


class Observer:
    def __init__(self, workspace, download_base, runtime_dir, settings_path=None):
        self.workspace = Path(workspace).resolve()
        self.download_base = Path(download_base).absolute()
        self.runtime_dir = Path(runtime_dir).resolve()
        self.settings_path = Path(settings_path or Path.home()/'.claude/settings.json')
        self.reader = SnapshotReader(self.workspace, self.download_base)
        self.library = LibraryReader(self.download_base)

    def setup(self):
        """只返回必要配置和布尔值；永不返回配置文件的 env 或凭据。"""
        settings = {}
        try:
            if self.settings_path.is_file() and self.settings_path.stat().st_size < 1024*1024:
                settings = json.loads(self.settings_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        env = settings.get('env', {}) if isinstance(settings, dict) else {}
        if not isinstance(env, dict):
            env = {}
        try:
            host = urlsplit(str(env.get('ANTHROPIC_BASE_URL', ''))).hostname or ''
        except ValueError:
            host = ''
        provider = 'DeepSeek' if host == 'api.deepseek.com' else ('未配置' if not host else '其他服务')
        metadata = {}
        metadata_path = self.runtime_dir/'deployment.json'
        try:
            if metadata_path.is_file() and metadata_path.stat().st_size < 16384:
                metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        if not isinstance(metadata, dict):
            metadata = {}
        skill = self.settings_path.parent/'skills/law-crawler'
        return dict(claude_version=_safe_string(metadata.get('claude_version')) or None,
                    skill_installed=all((skill/part).exists() for part in ('SKILL.md','scripts/law_crawler','references','countries')),
                    provider=provider, model=_safe_string(env.get('ANTHROPIC_MODEL')) or None,
                    api_key_configured=bool(env.get('ANTHROPIC_AUTH_TOKEN') or env.get('ANTHROPIC_API_KEY')),
                    hooks_installed=hooks_installed(self.settings_path))

    def overview(self):
        batches = self.reader.batches()
        sessions = session_list(self.runtime_dir)
        host = host_metrics(self.workspace)
        warnings = []
        total = host.get('memory_total_mb')
        if total is not None and total < 3500:
            warnings.append('实例物理内存低于 Claude Code 官方 4 GB 要求；当前仅建议单会话试运行，以实际内存和运行结果为准。')
        return dict(generated_at=datetime.now(timezone.utc).isoformat(),
                    workspace_name=self.workspace.name, host=host, setup=self.setup(),
                    counts=dict(sessions=len(sessions),
                                active_sessions=sum(s.get('status') in ('running','waiting') for s in sessions),
                                batches=len(batches),
                                finished_batches=sum(b.get('status') in ('completed','completed_with_errors','planned_only') for b in batches)),
                    sessions=sessions, batches=batches, warnings=warnings)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, observer):
        self.observer = observer
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = 'LawCrawlerObserver/1'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def _send(self, code, data, content_type='application/json; charset=utf-8', headers=None):
        if isinstance(data, (dict,list)):
            data = json.dumps(data, ensure_ascii=False).encode('utf-8')
        elif isinstance(data, str):
            data = data.encode('utf-8')
        self._headers(code, len(data), content_type, headers)
        if self.command != 'HEAD':
            self.wfile.write(data)

    def _headers(self, code, length, content_type, headers=None, inline=False):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(length))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        if inline:
            # Chromium 的内置 PDF 查看器使用插件对象；只允许已验证原文的同源对象。
            policy = "default-src 'none'; object-src 'self'; frame-src 'self'; base-uri 'none'; frame-ancestors 'self'; form-action 'none'"
        else:
            policy = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
        self.send_header('Content-Security-Policy', policy)
        for key, value in (headers or {}).items():
            self.send_header(key, str(value))
        self.end_headers()

    def _stream(self, prepared, inline=False, allow_range=False):
        headers = {'Content-Disposition': content_disposition(prepared.name, inline)}
        start, end, code = 0, prepared.size - 1, 200
        if allow_range:
            headers['Accept-Ranges'] = 'bytes'
            values = self.headers.get_all('Range', [])
            try:
                requested = parse_range(values[0] if len(values) == 1 else (',' if values else None), prepared.size)
            except LibraryError as exc:
                return self._send(exc.status, {'error': exc.message, 'code': exc.code},
                                  headers={'Content-Range': f'bytes */{prepared.size}', 'Accept-Ranges': 'bytes'})
            if requested is not None:
                start, end = requested
                code = 206
                headers['Content-Range'] = f'bytes {start}-{end}/{prepared.size}'
        length = max(0, end - start + 1)
        media_type = prepared.media_type if inline or prepared.media_type == 'application/zip' else 'application/octet-stream'
        self._headers(code, length, media_type, headers, inline=inline)
        if self.command == 'HEAD':
            return
        prepared.stream.seek(start)
        remaining = length
        while remaining:
            chunk = prepared.stream.read(min(CHUNK_BYTES, remaining))
            if not chunk:
                break
            self.wfile.write(chunk)
            remaining -= len(chunk)

    def _library_query(self, query):
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True, max_num_fields=8)
        if any(key not in ('country', 'q', 'offset', 'limit') or len(value) != 1
               for key, value in values.items()):
            raise LibraryError(400, '资源查询参数无效', 'invalid_query')
        offset, limit = values.get('offset', ['0'])[0], values.get('limit', ['50'])[0]
        if not re.fullmatch(r'[0-9]{1,7}', offset) or not re.fullmatch(r'[0-9]{1,3}', limit):
            raise LibraryError(400, '分页参数无效', 'invalid_query')
        return dict(country=values.get('country', [''])[0] or None,
                    q=values.get('q', [''])[0], offset=int(offset), limit=int(limit))

    def do_GET(self):
        """固定路由与本机 Host 校验，拒绝任意文件读取及 DNS rebinding。"""
        try:
            host = urlsplit('//'+self.headers.get('Host','')).hostname
        except ValueError:
            host = None
        if host not in ('127.0.0.1','localhost','::1'):
            return self._send(403, {'error':'仅支持本机或 SSH 隧道访问'})
        origin = self.headers.get('Origin')
        if origin and origin != f'http://{self.headers.get("Host")}' and origin != f'https://{self.headers.get("Host")}':
            return self._send(403, {'error':'不允许跨站请求'})
        observer = self.server.observer
        try:
            split = urlsplit(self.path)
            target = unquote(split.path, errors='strict')
            if target == '/healthz':
                return self._send(200, {'status':'ok','read_only':True})
            if target == '/api/overview':
                return self._send(200, observer.overview())
            if target == '/api/library':
                return self._send(200, observer.library.listing(**self._library_query(split.query)))
            match = re.fullmatch(r'/api/library/files/([A-Za-z0-9_-]{1,1024})/(download|preview|metadata)', target)
            if match:
                identifier, action = match.groups()
                if action == 'metadata':
                    raw, name = observer.library.metadata(identifier)
                    return self._send(200, raw, headers={'Content-Disposition': content_disposition(name)})
                with observer.library.prepare(identifier) as prepared:
                    if action == 'download':
                        return self._stream(prepared)
                    if prepared.preview_kind == 'text':
                        return self._send(200, observer.library.text_preview(prepared))
                    if prepared.preview_kind in ('pdf', 'image'):
                        return self._stream(prepared, inline=True, allow_range=True)
                    raise LibraryError(415, '此文件格式仅支持下载', 'preview_unavailable')
            match = re.fullmatch(r'/api/batches/([A-Za-z0-9][A-Za-z0-9_-]{0,119})/(files|archive\.zip)', target)
            if match:
                identifier, action = match.groups()
                if action == 'files':
                    return self._send(200, observer.library.batch_files(identifier))
                with observer.library.archive(identifier) as prepared:
                    return self._stream(prepared)
            match = re.fullmatch(r'/api/(sessions|batches)/([A-Za-z0-9][A-Za-z0-9_.-]{0,127})', target)
            if match:
                if match[1] == 'sessions':
                    result = session_detail(observer.runtime_dir, match[2])
                else:
                    result = observer.reader.batch(match[2])
                if result is None:
                    raise FileNotFoundError()
                return self._send(200, result)
            static = {'/':('index.html','text/html; charset=utf-8'),
                      '/index.html':('index.html','text/html; charset=utf-8'),
                      '/app.js':('app.js','text/javascript; charset=utf-8'),
                      '/style.css':('style.css','text/css; charset=utf-8'),
                      '/static/app.js':('app.js','text/javascript; charset=utf-8'),
                      '/static/style.css':('style.css','text/css; charset=utf-8'),
                      '/static/library.js':('library.js','text/javascript; charset=utf-8'),
                      '/static/library.css':('library.css','text/css; charset=utf-8')}
            if target in static:
                filename, content_type = static[target]
                path = Path(__file__).parent/'static'/filename
                return self._send(200, path.read_bytes(), content_type)
            return self._send(404, {'error':'未找到'})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            # 客户端断开时 context manager 已关闭临时文件与原件 FD。
            return
        except LibraryError as exc:
            return self._send(exc.status, {'error': exc.message, 'code': exc.code})
        except FileNotFoundError:
            return self._send(404, {'error':'记录不存在'})
        except (ValueError, TypeError, KeyError):
            return self._send(400, {'error':'无效记录或参数'})
        except Exception:
            # 错误细节可能带路径或凭据，不回传原始 exception。
            return self._send(503, {'error':'暂时无法读取状态，请稍后刷新'})

    def _read_only(self):
        self._send(405, {'error':'看板只提供 GET / HEAD 查询'})

    do_HEAD = do_GET
    do_POST = do_PUT = do_DELETE = do_PATCH = _read_only

    def log_message(self, format, *args):
        # 原始 URL 可能含查询凭据，不将请求参数写入日志。
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--download-base', type=Path, required=True)
    parser.add_argument('--runtime-dir', type=Path, required=True)
    parser.add_argument('--settings-path', type=Path)
    parser.add_argument('--host', choices=('127.0.0.1',), default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if not args.workspace.is_dir():
        parser.error('workspace 必须为已有目录')
    app = Observer(args.workspace, args.download_base, args.runtime_dir, args.settings_path)
    server = Server((args.host,args.port), app)
    print(f'只读看板监听 http://{args.host}:{args.port}；远程使用 SSH 本地端口转发。', flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
