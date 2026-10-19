#!/usr/bin/env python3
"""标准库本机浏览器诊断；合成附件不是法规，不写入法规资料库。"""
import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import secrets
import socket
import sys
import tempfile
import threading
import time


HOST = '127.0.0.1'
MAX_DURATION = 3600


def port_number(value):
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError('port must be an integer from 0 to 65535')
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError('port must be from 0 to 65535 (0 chooses a free port)')
    return port


def duration_seconds(value):
    try:
        duration = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError('duration must be a finite number of seconds')
    if not math.isfinite(duration) or not 0 < duration <= MAX_DURATION:
        raise argparse.ArgumentTypeError(f'duration must be greater than 0 and at most {MAX_DURATION} seconds')
    return duration


class HealthcheckServer(ThreadingHTTPServer):
    """只提供内存中固定内容；不使用 SimpleHTTPRequestHandler 或文件路由。"""
    daemon_threads = True
    block_on_close = False

    def __init__(self, port, request_log):
        self.marker = 'BROWSER-HEALTH-' + secrets.token_hex(12)
        self.filename = f'browser-healthcheck-{self.marker}.txt'
        self.text = (
            'BROWSER HEALTHCHECK FIXTURE\n'
            'This is synthetic diagnostic data, NOT a regulation.\n'
            f'marker={self.marker}\n'
        ).encode('utf-8')
        self.request_log = request_log
        self.log_lock = threading.Lock()
        self.connections = set()
        self.connection_lock = threading.Lock()
        super().__init__((HOST, port), HealthcheckHandler)
        self.timeout = 0.25
        self.routes = {
            '/': ('text/html; charset=utf-8', self.page(), None),
            '/marker': ('text/plain; charset=utf-8', (self.marker + '\n').encode('utf-8'), None),
            '/inline.txt': ('text/plain; charset=utf-8', self.text, 'inline'),
            '/attachment': ('text/plain; charset=utf-8', self.text,
                            f'attachment; filename="{self.filename}"'),
        }

    def page(self):
        # marker 仅由安全的固定前缀和随机十六进制字符组成，无用户输入。
        return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Browser healthcheck</title>
<style>body{{font:20px sans-serif;max-width:900px;margin:48px auto;padding:24px;background:#f5f8fc;color:#162a40}}
code{{display:block;padding:24px;background:#daf0df;overflow-wrap:anywhere}}
li{{margin:24px 0}}a{{color:#124c9a}}</style></head>
<body><h1>Browser healthcheck</h1><p>Visible health marker:</p>
<code id="health-marker">{self.marker}</code>
<p>Synthetic diagnostic data only. This page and its attachment are NOT regulations.</p>
<ul><li><a id="inline-link" href="/inline.txt">Open plain text diagnostic fixture</a></li>
<li><a id="attachment-link" href="/attachment">Download diagnostic TXT attachment</a></li></ul>
</body></html>'''.encode('utf-8')

    def fixture(self, output_dir, duration):
        base = f'http://{HOST}:{self.server_address[1]}'
        return {
            'kind': 'browser_healthcheck_fixture',
            'synthetic': True,
            'is_regulation': False,
            'marker': self.marker,
            'host': HOST,
            'port': self.server_address[1],
            'duration_seconds': duration,
            'urls': {'page': base + '/', 'marker': base + '/marker',
                     'inline': base + '/inline.txt', 'attachment': base + '/attachment'},
            'attachment': {'filename': self.filename, 'expected_bytes': len(self.text),
                           'sha256': hashlib.sha256(self.text).hexdigest(),
                           'content_type': 'text/plain; charset=utf-8'},
            'output_dir': str(output_dir),
            'fixture_path': str(output_dir / 'fixture.json'),
            'requests_path': str(output_dir / 'requests.jsonl'),
        }

    def record(self, method, route, status, body_bytes, complete):
        # 不保存 IP、请求头、query、任意未知路径或未经约束的方法名称。
        entry = {'at_utc': datetime.now(timezone.utc).isoformat(),
                 'method': method if method in ('GET', 'HEAD') else 'unsupported',
                 'route': route, 'status': status, 'body_bytes': body_bytes,
                 'response_completed': complete}
        with self.log_lock:
            with self.request_log.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(entry) + '\n')

    def process_request(self, request, client_address):
        with self.connection_lock:
            self.connections.add(request)
        super().process_request(request, client_address)

    def shutdown_request(self, request):
        try:
            super().shutdown_request(request)
        finally:
            with self.connection_lock:
                self.connections.discard(request)

    def server_close(self):
        # 期限到达时同时关闭慢连接，不让请求延长诊断服务生命周期。
        with self.connection_lock:
            for connection in self.connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
        super().server_close()


class HealthcheckHandler(BaseHTTPRequestHandler):
    timeout = 2

    def log_message(self, format, *args):
        pass  # BaseHTTPRequestHandler 默认日志含 IP/原始请求，诊断不收集这些内容。

    def do_GET(self):
        self.respond()

    def do_HEAD(self):
        self.respond(head=True)

    def do_POST(self):
        self.respond(unsupported=True)

    do_PUT = do_POST
    do_DELETE = do_POST
    do_PATCH = do_POST
    do_OPTIONS = do_POST
    do_TRACE = do_POST

    def respond(self, head=False, unsupported=False):
        path = self.path.split('?', 1)[0]
        route = path if path in self.server.routes else 'unknown'
        if route == 'unknown':
            status, content_type, body, disposition = 404, 'text/plain; charset=utf-8', b'Not found\n', None
        elif unsupported:
            status, content_type, body, disposition = 405, 'text/plain; charset=utf-8', b'Method not allowed\n', None
        else:
            status = 200
            content_type, body, disposition = self.server.routes[route]
        body_bytes = 0
        complete = False
        try:
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'")
            self.send_header('Connection', 'close')
            if disposition:
                self.send_header('Content-Disposition', disposition)
            if status == 405:
                self.send_header('Allow', 'GET, HEAD')
            self.end_headers()
            if not head:
                self.wfile.write(body)
                self.wfile.flush()
                body_bytes = len(body)
            complete = True
        except OSError:
            pass
        finally:
            self.close_connection = True
            self.server.record(self.command, route, status, body_bytes, complete)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=port_number, default=0, help='loopback port; default 0 chooses a free port')
    parser.add_argument('--duration', type=duration_seconds, default=180,
                        help=f'stop after this many seconds (default 180; maximum {MAX_DURATION})')
    parser.add_argument('--output-dir', type=Path,
                        help='diagnostic evidence directory outside the regulation store; default: a new temporary directory')
    args = parser.parse_args(argv)
    output_dir = (args.output_dir or Path(tempfile.mkdtemp(prefix='browser-healthcheck-'))).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    request_log = output_dir / 'requests.jsonl'
    fixture_path = output_dir / 'fixture.json'
    if request_log.exists() or fixture_path.exists():
        parser.error('output directory already contains healthcheck evidence; choose a new directory')
    with HealthcheckServer(args.port, request_log) as server:
        # x 模式也拒绝并发运行覆盖或预先创建的证据文件/符号链接。
        with request_log.open('x', encoding='utf-8'):
            pass
        fixture = server.fixture(output_dir, args.duration)
        with fixture_path.open('x', encoding='utf-8') as stream:
            json.dump(fixture, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        deadline = time.monotonic() + args.duration
        print(json.dumps({'event': 'ready', **fixture}), flush=True)
        try:
            while time.monotonic() < deadline:
                server.timeout = min(0.25, max(0, deadline - time.monotonic()))
                server.handle_request()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
