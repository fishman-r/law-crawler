"""只读看板的接口隔离、凭据白名单和静态资源回归。"""
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from observer.server import Observer, Server
from tools.install_skill import install


class ObserverServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root/'workspace'
        self.workspace.mkdir()
        self.settings = self.root/'private/.claude/settings.json'
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(json.dumps({'env':{
            'ANTHROPIC_BASE_URL':'https://api.deepseek.com/anthropic',
            'ANTHROPIC_AUTH_TOKEN':'sk-this-is-a-secret-not-for-the-browser',
            'ANTHROPIC_MODEL':'deepseek-flash[1m]'}}))
        self.server = Server(('127.0.0.1',0),Observer(self.workspace,self.root/'data',self.root/'runtime',self.settings))
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def get(self, path, headers=None):
        return urllib.request.urlopen(urllib.request.Request(self.url+path,headers=headers or {}),timeout=2)

    def test_overview_whitelists_provider_credentials_without_writing_data(self):
        with self.get('/api/overview') as response:
            raw = response.read().decode()
            body = json.loads(raw)
        self.assertEqual(body['setup']['provider'],'DeepSeek')
        self.assertTrue(body['setup']['api_key_configured'])
        self.assertNotIn('sk-this-is-a-secret',raw)
        self.assertFalse((self.root/'data').exists())
        self.assertFalse((self.root/'runtime').exists())
        self.assertEqual(body['counts']['batches'],0)

    def test_installed_skill_uses_current_complete_package_layout(self):
        self.assertFalse(self.server.observer.setup()['skill_installed'])
        installed = install('claude', destination=self.settings.parent/'skills')
        self.assertFalse((installed/'assets').exists())
        with self.get('/api/overview') as response:
            self.assertTrue(json.load(response)['setup']['skill_installed'])
        (installed/'countries').rename(installed/'missing-countries')
        self.assertFalse(self.server.observer.setup()['skill_installed'])

    def test_rebinding_and_cross_origin_are_rejected(self):
        for headers in ({'Host':'attacker.example'},{'Origin':'https://attacker.example'}):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.get('/api/overview',headers)
            self.assertEqual(error.exception.code,403)

    def test_arbitrary_file_routes_and_mutations_are_not_available(self):
        for path in ('/../../private/.claude/settings.json','/%2e%2e/private/settings.json','/api/batches/..%2fsecret','/.claude/settings.json'):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.get(path)
            self.assertEqual(error.exception.code,404)
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(self.url+'/api/overview',data=b'{}',method='POST'))
        self.assertEqual(error.exception.code,405)

    def test_static_assets_are_self_contained_and_have_csp(self):
        for path in ('/','/static/app.js','/static/style.css'):
            with self.get(path) as response:
                self.assertEqual(response.status,200)
                self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])
                self.assertEqual(response.headers['Cache-Control'],'no-store')
                self.assertTrue(response.read())

    def test_missing_session_and_batch_return_404(self):
        for path in ('/api/batches/no-such-batch','/api/sessions/no-such-session'):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.get(path)
            self.assertEqual(error.exception.code,404)

    def test_invalid_metadata_and_endpoint_do_not_break_the_dashboard(self):
        runtime = self.root/'runtime'
        runtime.mkdir()
        (runtime/'deployment.json').write_text('[]')
        self.settings.write_text(json.dumps({'env':{'ANTHROPIC_BASE_URL':'https://[invalid'}}))
        setup = self.server.observer.setup()
        self.assertIsNone(setup['claude_version'])
        self.assertEqual(setup['provider'],'未配置')


if __name__ == '__main__':
    unittest.main()
