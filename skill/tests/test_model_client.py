import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from law_crawler.model_client import ModelClientError, _NoRedirect, call_json, validate_config, validate_schema


SCHEMA = {'type': 'object', 'required': ['ok'], 'properties': {'ok': {'type': 'boolean'}},
          'additionalProperties': False}
MESSAGES = [{'role': 'user', 'content': 'Return JSON'}]


class ModelClientTests(unittest.TestCase):
    def config(self, provider='ollama', **extra):
        return {'provider': provider, 'base_url': 'http://localhost:11434', 'model': 'test-model', **extra}

    def invoke(self, body, config=None):
        response = MagicMock()
        response.status = 200
        response.headers = {}
        response.read.return_value = json.dumps(body).encode()
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.return_value = response
        with patch('law_crawler.model_client.build_opener', return_value=opener):
            result = call_json(config or self.config(), MESSAGES, SCHEMA)
        return result, opener

    def test_ollama_request_and_accounting(self):
        result, opener = self.invoke({'model': 'actual-model', 'done': True, 'done_reason': 'stop',
                                     'message': {'content': '{"ok":true}'},
                                     'prompt_eval_count': 100, 'eval_count': 20},
                                    self.config(input_price_per_million=1, output_price_per_million=2))
        request = opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, 'http://localhost:11434/api/chat')
        self.assertEqual(payload['format'], SCHEMA)
        self.assertEqual(payload['keep_alive'], '2m')
        self.assertFalse(payload['think'])
        self.assertFalse(payload['stream'])
        self.assertEqual(payload['options']['num_ctx'], 4096)
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 120)
        opener.open.assert_called_once()
        self.assertEqual(result['output'], {'ok': True})
        self.assertEqual(result['model'], 'actual-model')
        self.assertAlmostEqual(result['estimated_cost_usd'], .00014)

    def test_openai_request_and_key_environment(self):
        with patch.dict('os.environ', {'MODEL_TEST_KEY': 'secret-test-key'}):
            result, opener = self.invoke({'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":false}'}}]},
                                        self.config('openai-compatible', base_url='https://example.test/v1/', api_key_env='MODEL_TEST_KEY'))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://example.test/v1/chat/completions')
        self.assertEqual(request.get_header('Authorization'), 'Bearer secret-test-key')
        self.assertEqual(json.loads(request.data)['response_format'], {'type': 'json_object'})
        self.assertEqual(result['usage'], {'input_tokens': None, 'output_tokens': None})
        self.assertIsNone(result['estimated_cost_usd'])
        self.assertNotIn('secret-test-key', json.dumps(result))

    def test_rejects_invalid_or_incomplete_output(self):
        for content in ('', 'not-json', '{"ok":', '{"ok":1}', '{"ok":true,"secret":"x"}', '{"ok":NaN}'):
            with self.subTest(content=content), self.assertRaises(ModelClientError):
                self.invoke({'done': True, 'message': {'content': content}})
        for provider in ('ollama', 'openai-compatible'):
            body = ({'done': True, 'done_reason': 'length', 'message': {'content': '{"ok":true}'}}
                    if provider == 'ollama' else {'choices': [{'finish_reason': 'length', 'message': {'content': '{"ok":true}'}}]})
            with self.assertRaises(ModelClientError):
                self.invoke(body, self.config(provider))

    def test_configuration_freeze_does_not_read_secrets(self):
        with patch('law_crawler.model_client.os.environ.get', side_effect=AssertionError('must not read env')):
            cfg = validate_config(self.config(api_key_env='MODEL_TEST_KEY'))
        self.assertEqual(cfg['api_key_env'], 'MODEL_TEST_KEY')
        self.assertEqual(cfg['max_output_tokens'], 1024)
        for changes in ({'api_key': 'secret'}, {'model': ''}, {'provider': 'other'},
                        {'timeout': -1}, {'num_ctx': True}, {'temperature': float('nan')},
                        {'base_url': 'https://user:secret@example.test'}, {'base_url': 'https://example.test?key=secret'},
                        {'api_key_env': 'bad-key-name'}):
            with self.subTest(changes=changes), self.assertRaises(ModelClientError):
                validate_config(self.config(**changes))

    def test_schema_recursion(self):
        schema = {'type': 'array', 'items': {'type': 'object', 'required': ['value'],
                  'properties': {'value': {'type': ['number', 'null']}, 'mode': {'enum': ['a', 'b']}},
                  'additionalProperties': False}}
        validate_schema([{'value': 3, 'mode': 'a'}, {'value': None}], schema)
        for value in ([{'value': True}], [{'mode': 'a'}], [{'value': 1, 'mode': 'c'}], [{'value': 1, 'extra': 2}]):
            with self.assertRaises(ModelClientError):
                validate_schema(value, schema)

    def test_no_retry_and_sanitized_network_errors(self):
        for error in (RuntimeError('secret-key private request body'),
                      HTTPError('https://example.test/secret', 429, 'secret-key', {}, None)):
            opener = MagicMock()
            opener.open.side_effect = error
            with patch('law_crawler.model_client.build_opener', return_value=opener):
                with self.assertRaises(ModelClientError) as caught:
                    call_json(self.config(), MESSAGES, SCHEMA)
            opener.open.assert_called_once()
            self.assertNotIn('secret', str(caught.exception))

    def test_redirect_is_rejected(self):
        with self.assertRaises(ModelClientError):
            _NoRedirect().redirect_request(None, None, 302, 'Found', {}, 'https://other.test')

    def test_missing_key_stops_before_http(self):
        with patch.dict('os.environ', {}, clear=True), patch('law_crawler.model_client.build_opener') as opener:
            with self.assertRaises(ModelClientError):
                call_json(self.config(api_key_env='MODEL_TEST_KEY'), MESSAGES, SCHEMA)
        opener.assert_not_called()


class ModelHTTPFramingTests(unittest.TestCase):
    def test_valid_json_with_truncated_http_body_is_rejected(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                body = json.dumps({'done': True, 'message': {'content': '{"ok":true}'}}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body) + (10 if self.path.startswith('/short/') else 0)))
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = {'provider': 'ollama', 'model': 'test',
                      'base_url': f'http://127.0.0.1:{server.server_port}/short'}
            with self.assertRaisesRegex(ModelClientError, 'incomplete'):
                call_json(config, MESSAGES, SCHEMA)
            config['base_url'] = f'http://127.0.0.1:{server.server_port}/complete'
            self.assertEqual(call_json(config, MESSAGES, SCHEMA)['output'], {'ok': True})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
