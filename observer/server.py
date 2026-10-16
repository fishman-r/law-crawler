"""以 Python 标准库提供只读状态 API 和静态看板，默认仅监听本机。"""
import argparse
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from observer.events import hooks_installed, session_detail, session_list
from observer.snapshot import SnapshotReader, host_metrics


def _safe_string(value, limit=120):
    return str(value or '')[:limit]


class Observer:
    def __init__(self, workspace, download_base, runtime_dir, settings_path=None):
        self.workspace = Path(workspace).resolve()
        self.download_base = Path(download_base).resolve()
        self.runtime_dir = Path(runtime_dir).resolve()
        self.settings_path = Path(settings_path or Path.home()/'.claude/settings.json')
        self.reader = SnapshotReader(self.workspace, self.download_base)

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
        skill = self.settings_path.parent/'skills/download-official-regulations'
        return dict(claude_version=_safe_string(metadata.get('claude_version')) or None,
                    skill_installed=all((skill/part).exists() for part in ('SKILL.md','scripts','references','assets')),
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

    def _send(self, code, data, content_type='application/json; charset=utf-8'):
        if isinstance(data, (dict,list)):
            data = json.dumps(data, ensure_ascii=False).encode('utf-8')
        elif isinstance(data, str):
            data = data.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(data)

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
        target = unquote(urlsplit(self.path).path)
        observer = self.server.observer
        try:
            if target == '/healthz':
                return self._send(200, {'status':'ok','read_only':True})
            if target == '/api/overview':
                return self._send(200, observer.overview())
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
                      '/static/style.css':('style.css','text/css; charset=utf-8')}
            if target in static:
                filename, content_type = static[target]
                path = Path(__file__).parent/'static'/filename
                return self._send(200, path.read_bytes(), content_type)
            return self._send(404, {'error':'未找到'})
        except FileNotFoundError:
            return self._send(404, {'error':'记录不存在'})
        except (ValueError, TypeError, KeyError):
            return self._send(400, {'error':'无效记录或参数'})
        except Exception:
            # 错误细节可能带路径或凭据，不回传原始 exception。
            return self._send(503, {'error':'暂时无法读取状态，请稍后刷新'})

    def _read_only(self):
        self._send(405, {'error':'看板只提供 GET 查询'})

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
