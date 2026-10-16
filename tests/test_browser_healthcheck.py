"""真实 loopback HTTP 与子进程验证；所有内容均为合成诊断 fixture。"""
import contextlib
import hashlib
import http.client
import importlib.util
import io
import json
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'skills/law-crawler/scripts/browser_healthcheck.py'
from law_crawler import browser_healthcheck as app


class BrowserHealthcheckHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.log = self.directory / 'requests.jsonl'
        self.log.write_text('')
        self.server = app.HealthcheckServer(0, self.log)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path, method='GET', headers=None):
        with contextlib.closing(http.client.HTTPConnection(*self.server.server_address, timeout=2)) as connection:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()

    def records(self, count):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            rows = [json.loads(line) for line in self.log.read_text().splitlines()]
            if len(rows) >= count:
                return rows
            time.sleep(0.01)
        self.fail(f'expected {count} request log entries')

    def test_loopback_page_has_unique_marker_and_two_distinct_links(self):
        self.assertEqual(self.server.server_address[0], '127.0.0.1')
        self.assertRegex(self.server.marker, r'^BROWSER-HEALTH-[a-f0-9]{24}$')
        status, headers, body = self.request('/')
        self.assertEqual(status, 200)
        text = body.decode('utf-8')
        self.assertIn(self.server.marker, text)
        self.assertIn('NOT regulations', text)
        self.assertEqual(re.findall(r'href="([^"]+)"', text), ['/inline.txt', '/attachment'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['Content-Type'], 'text/html; charset=utf-8')
        status, _, marker = self.request('/marker')
        self.assertEqual(status, 200)
        self.assertEqual(marker.decode(), self.server.marker + '\n')
        other = app.HealthcheckServer(0, self.log)
        self.addCleanup(other.server_close)
        self.assertNotEqual(other.marker, self.server.marker)

    def test_attachment_bytes_hash_filename_and_inline_headers(self):
        fixture = self.server.fixture(self.directory, 180)
        status, headers, attachment = self.request('/attachment')
        self.assertEqual(status, 200)
        expected = fixture['attachment']
        self.assertEqual(len(attachment), expected['expected_bytes'])
        self.assertEqual(headers['Content-Length'], str(expected['expected_bytes']))
        self.assertEqual(hashlib.sha256(attachment).hexdigest(), expected['sha256'])
        self.assertEqual(headers['Content-Disposition'], f'attachment; filename="{expected["filename"]}"')
        self.assertEqual(headers['Content-Type'], expected['content_type'])
        self.assertIn(b'NOT a regulation', attachment)
        status, headers, inline = self.request('/inline.txt')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Disposition'], 'inline')
        self.assertEqual(inline, attachment)
        rows = self.records(2)
        self.assertEqual([row['route'] for row in rows], ['/attachment', '/inline.txt'])
        self.assertTrue(all(row['response_completed'] for row in rows))

    def test_unknown_and_traversal_routes_never_read_files(self):
        secret = self.directory / 'secret.txt'
        secret.write_text('PRIVATE-DISK-CONTENT')
        paths = [str(secret), '/secret.txt', '/../secret.txt', '/%2e%2e/secret.txt',
                 '/fixture.json', '/requests.jsonl', '/attachment/../secret.txt',
                 '/unknown?private=QUERY-SECRET', 'http://example.test/attachment']
        for path in paths:
            with self.subTest(path=path):
                status, _, body = self.request(path)
                self.assertEqual(status, 404)
                self.assertEqual(body, b'Not found\n')
        rows = self.records(len(paths))
        self.assertTrue(all(row['route'] == 'unknown' and row['status'] == 404 for row in rows))
        self.assertNotIn('secret.txt', self.log.read_text())
        self.assertNotIn('QUERY-SECRET', self.log.read_text())

    def test_request_evidence_does_not_collect_private_inputs(self):
        status, _, _ = self.request('/attachment?token=QUERY-SECRET',
                                    headers={'Cookie': 'COOKIE-SECRET', 'User-Agent': 'AGENT-SECRET',
                                             'Referer': 'https://private.example/PATH-SECRET'})
        self.assertEqual(status, 200)
        row = self.records(1)[0]
        self.assertEqual(set(row), {'at_utc', 'method', 'route', 'status', 'body_bytes', 'response_completed'})
        self.assertEqual(row['route'], '/attachment')
        for private in ('QUERY-SECRET', 'COOKIE-SECRET', 'AGENT-SECRET', 'PATH-SECRET', '127.0.0.1'):
            self.assertNotIn(private, self.log.read_text())

    def test_head_does_not_claim_a_download_body_and_post_is_rejected(self):
        status, headers, body = self.request('/attachment', method='HEAD')
        self.assertEqual(status, 200)
        self.assertEqual(body, b'')
        self.assertEqual(int(headers['Content-Length']), len(self.server.text))
        status, headers, _ = self.request('/attachment', method='POST')
        self.assertEqual(status, 405)
        self.assertEqual(headers['Allow'], 'GET, HEAD')
        rows = self.records(2)
        self.assertEqual(rows[0]['method'], 'HEAD')
        self.assertEqual(rows[0]['body_bytes'], 0)
        self.assertEqual(rows[1]['method'], 'unsupported')


class BrowserHealthcheckCLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def launch(self, duration):
        process = subprocess.Popen([sys.executable, str(SCRIPT), '--duration', str(duration),
                                    '--output-dir', str(self.directory)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.clean_process, process)
        ready = json.loads(process.stdout.readline())
        self.assertEqual(ready['event'], 'ready')
        self.assertEqual(ready['host'], '127.0.0.1')
        self.assertGreater(ready['port'], 0)
        fixture = json.loads((self.directory / 'fixture.json').read_text())
        self.assertEqual({key: value for key, value in ready.items() if key != 'event'}, fixture)
        self.assertTrue((self.directory / 'requests.jsonl').exists())
        return process, ready

    @staticmethod
    def clean_process(process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=3)

    def assert_socket_closed(self, port):
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', port), timeout=0.5)

    def test_bounded_duration_prints_ready_and_closes_slow_connection(self):
        process, ready = self.launch(0.4)
        slow = socket.create_connection(('127.0.0.1', ready['port']), timeout=1)
        self.addCleanup(slow.close)
        slow.sendall(b'GET / HTTP/1.1\r\n')  # 不完整请求不能延长服务期限。
        stdout, stderr = process.communicate(timeout=3)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(stdout, '')
        self.assertEqual(stderr, '')
        self.assert_socket_closed(ready['port'])
        self.assertEqual(set(path.name for path in self.directory.iterdir()), {'fixture.json', 'requests.jsonl'})

    def test_keyboard_interrupt_closes_listener(self):
        process, ready = self.launch(180)
        process.send_signal(signal.SIGINT)
        _, stderr = process.communicate(timeout=3)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(stderr, '')
        self.assert_socket_closed(ready['port'])

    def test_invalid_parameter_ranges_are_rejected_before_evidence(self):
        invalid = [('--port', '-1'), ('--port', '65536'), ('--port', 'abc'),
                   ('--duration', '0'), ('--duration', '-1'), ('--duration', 'nan'),
                   ('--duration', 'inf'), ('--duration', '3601'), ('--duration', 'abc'),
                   ('--host', '0.0.0.0')]
        for option, value in invalid:
            with self.subTest(option=option, value=value):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                    app.main([option, value, '--output-dir', str(self.directory / 'evidence')])
                self.assertEqual(caught.exception.code, 2)
                self.assertFalse((self.directory / 'evidence').exists())

    def test_existing_evidence_is_not_overwritten(self):
        previous = self.directory / 'fixture.json'
        previous.write_text('previous evidence')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            app.main(['--output-dir', str(self.directory), '--duration', '0.1'])
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(previous.read_text(), 'previous evidence')
        self.assertFalse((self.directory / 'requests.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
