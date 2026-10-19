import json
import ssl
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError

from law_crawler.batch_runtime import BatchRuntime, emit_report, retry_fetch


class BatchRuntimeTests(unittest.TestCase):
    def test_plan_precedes_execution_and_rerun_preserves_original(self):
        plan = {'country': 'Canada', 'mode': 'metadata', 'parameters': {'limit': 2},
                'candidates': [{'key': 'a', 'url': 'https://example.gov/a'}, {'key': 'b'}]}
        with tempfile.TemporaryDirectory() as root:
            first = BatchRuntime(root, plan)
            original = (first.batch_dir / 'plan.json').read_bytes()
            self.assertEqual(first.plan, plan)
            plan['country'] = 'Germany'
            snapshot = first.plan
            snapshot['country'] = 'France'
            self.assertEqual(first.plan['country'], 'Canada')
            first.record('a', 'success', '元数据已保存', attempts=1)
            files = first.finish({'counts': {'success': 1}, 'files': [{'file': 'a.json'}]})
            self.assertTrue(all(path.exists() for path in files.values()))
            self.assertEqual(first.states['b']['status'], 'fail')
            second = BatchRuntime(root, rerun_from=first.batch_id)
            self.assertEqual(second.attempt_number, 2)
            self.assertNotEqual(first.attempt_dir, second.attempt_dir)
            self.assertEqual(second.plan['country'], 'Canada')
            self.assertEqual((second.batch_dir / 'plan.json').read_bytes(), original)
            self.assertEqual(json.loads((first.attempt_dir / 'states.json').read_text())['states']['a']['status'], 'success')
            self.assertEqual(second.states['a']['status'], 'pending')
            with self.assertRaises(ValueError):
                BatchRuntime(root, {'different': True}, rerun_from=first.batch_id)
            with self.assertRaises(ValueError):
                first.record('a', 'fail', '不能更改完成的报告')

    def test_abort_still_emits_reports_and_failure_reason(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(RuntimeError, 'source unavailable'):
                with BatchRuntime(root, {'candidates': [{'key': 'a'}]}) as batch:
                    batch.record('a', 'running', '开始下载', attempts=1)
                    raise RuntimeError('source unavailable')
            report = json.loads((batch.attempt_dir / 'report.json').read_text())
            self.assertEqual(report['states']['a']['status'], 'fail')
            self.assertIn('source unavailable', report['states']['a']['reason'])
            self.assertTrue((batch.attempt_dir / 'report.md').exists())
            self.assertTrue((batch.attempt_dir / 'report.html').exists())

    def test_rejects_escape_duplicate_and_symlink(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as external:
            for value in ('../outside', '.', '/absolute', 'name/path', '', '名字'):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    BatchRuntime(root, {}, batch_id=value)
            batch = BatchRuntime(root, {}, batch_id='batch-safe')
            with self.assertRaises(ValueError):
                BatchRuntime(root, {}, batch_id=batch.batch_id)
            (Path(root) / 'linked').symlink_to(external, target_is_directory=True)
            with self.assertRaises(ValueError):
                BatchRuntime(root, rerun_from='linked')

    def test_report_escapes_all_source_values_and_unsafe_links(self):
        attack = '<script>alert("x")</script>'
        with tempfile.TemporaryDirectory() as root:
            paths = emit_report({'title': attack, 'country': attack, 'counts': {attack: 1},
                                 'files': [{'file': attack, 'url': 'javascript:alert(1)'},
                                           {'url': 'https://example.gov/a?x=" onmouseover="attack'}],
                                 'errors': [{'reason': attack}], 'states': {'a': {'status': 'fail', 'reason': attack}}}, root)
            page = paths['html'].read_text()
            self.assertNotIn('<script>', page)
            self.assertNotIn('href="javascript:', page)
            self.assertIn('&lt;script&gt;', page)
            self.assertIn('href="https://example.gov/a?x=&quot; onmouseover=&quot;attack"', page)
            self.assertIn('Content-Security-Policy', page)
            self.assertEqual(json.loads(paths['json'].read_text())['title'], attack)
            with self.assertRaises(ValueError):
                emit_report({}, root, '../escape')


class RetryFetchTests(unittest.TestCase):
    def test_default_three_requests_with_exponential_backoff(self):
        calls, delays, events = [], [], []
        def fetcher(url):
            calls.append(url)
            raise URLError(TimeoutError('temporary timeout'))
        with self.assertRaises(URLError) as raised:
            retry_fetch(fetcher, 'https://example.gov/a', sleep=delays.append, event_callback=events.append)
        self.assertEqual(len(calls), 3)
        self.assertEqual(delays, [1, 2])
        self.assertEqual([e['status'] for e in events], ['retrying', 'retrying', 'failed'])
        self.assertEqual(raised.exception.retry_attempts, 3)
        self.assertEqual(events[-1]['attempts'], 3)

    def test_transient_http_errors_and_eventual_success(self):
        responses = [HTTPError('https://example.gov', 429, 'limited', {}, None),
                     HTTPError('https://example.gov', 503, 'unavailable', {}, None), (b'PDF', 'https://example.gov')]
        events, delays = [], []
        def fetcher(url):
            value = responses.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        result = retry_fetch(fetcher, 'https://example.gov', base_delay=0.5, sleep=delays.append, event_callback=events.append)
        self.assertEqual(result[0], b'PDF')
        self.assertEqual(delays, [0.5, 1])
        self.assertEqual(events[-1]['status'], 'succeeded')
        self.assertEqual(events[-1]['attempt'], 3)

    def test_permanent_failures_do_not_retry(self):
        for exc in (HTTPError('https://example.gov', 403, 'forbidden', {}, None),
                    ValueError('schema invalid'), ssl.SSLCertVerificationError('invalid certificate'),
                    URLError(ssl.SSLCertVerificationError('invalid certificate'))):
            events, calls = [], []
            def fetcher(url):
                calls.append(url)
                raise exc
            with self.subTest(exc=type(exc).__name__), self.assertRaises(type(exc)):
                retry_fetch(fetcher, 'https://example.gov', sleep=lambda delay: self.fail('不得退避'), event_callback=events.append)
            self.assertEqual(len(calls), 1)
            self.assertEqual(events[0]['backoff_seconds'], 0)
            self.assertTrue(events[0]['reason'])

    def test_invalid_retry_settings(self):
        for value in (0, -1, 1.5, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                retry_fetch(lambda url: b'data', 'https://example.gov', max_attempts=value)
        with self.assertRaises(ValueError):
            retry_fetch(lambda url: b'data', 'https://example.gov', base_delay=-1)

    def test_long_custom_retry_delays_are_capped(self):
        delays = []
        def fetcher(url):
            raise TimeoutError('timeout')
        with self.assertRaises(TimeoutError):
            retry_fetch(fetcher, 'https://example.gov', max_attempts=4, base_delay=20, sleep=delays.append)
        self.assertEqual(delays, [20, 30, 30])


if __name__ == '__main__':
    unittest.main()
