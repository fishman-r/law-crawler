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
            'ANTHROPIC_AUTH_TOKEN':'sk-test-placeholder-not-a-real-api-key-12345',
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
        self.assertNotIn('sk-test-placeholder-not-a-real-api-key-12345',raw)
        self.assertFalse((self.root/'data').exists())
        self.assertFalse((self.root/'runtime').exists())
        self.assertEqual(body['counts']['batches'],0)

    def test_installed_skill_uses_current_complete_package_layout(self):
        self.assertFalse(self.server.observer.setup()['skill_installed'])
        import shutil
        source = self.root/'source'/'law-crawler'
        shutil.copytree(Path(__file__).resolve().parents[2]/'skill/law-crawler', source,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        installed = install('claude', destination=self.settings.parent/'skills', source=source)
        style = installed/'assets/translation-styles/zh-cn_en-gb.json'
        self.assertTrue(style.is_file())
        self.assertEqual(set(json.loads(style.read_text())['targets']), {'zh', 'en'})
        with self.get('/api/overview') as response:
            self.assertTrue(json.load(response)['setup']['skill_installed'])
        (installed/'countries').rename(installed/'missing-countries')
        self.assertFalse(self.server.observer.setup()['skill_installed'])

    def test_regulation_library_group_search_export_and_integrity(self):
        import hashlib, io, zipfile
        from observer.archive_index import build_archive_index
        base=self.root/'data'
        docs={}
        for n in range(2):
            data=b'%PDF-1.4\nfixture '+str(n).encode()+b'\n%%EOF\n'
            digest=hashlib.sha256(data).hexdigest()
            path=base/'Indonesia/pipeline/raw/objects'/(digest+'.pdf')
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            docs[str(n)]=dict(detail_url='https://official.example/'+str(n),platform='official.example',status='archived',
                metadata=dict(title={'value':'Test Law 7'},document_type={'value':'regulation'},
                    legal_identity={'status':'observed','value':dict(jurisdiction='ID',issuer='Ministry',type='Regulation',number='7',year='2025')},
                    file_roles={'https://official.example/file'+str(n):{'role':'regulation_body','status':'observed'}}),
                files={str(n):dict(url='https://official.example/file'+str(n),path=str(path),sha256=digest,size_bytes=len(data),
                                  capture_status='saved',complete=True,status_code=200,headers={'content-type':'application/pdf'})})
        source=self.root/'archive-state.json';source.write_text(json.dumps({'documents':docs}))
        build_archive_index(base,source,base/'Indonesia/pipeline/library/archive.sqlite3')
        with self.get('/api/regulations?page_size=1&q=Test&classification=regulation') as response:
            body=json.load(response)
        self.assertEqual(body['total'],1)
        self.assertEqual(len(body['items'][0]['files']),2)
        self.assertNotIn(str(self.root),json.dumps(body))
        self.assertIn('/api/regulations/archive.zip?',body['archive_url'])
        with self.get(body['archive_url']) as response:
            self.assertEqual(response.headers['Content-Type'],'application/zip')
            payload=response.read()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn('manifest.json',archive.namelist())
            self.assertEqual(len([x for x in archive.namelist() if x.startswith('originals/')]),2)
        for query in ('page=0','page_size=101','sort=DROP_TABLE','q=a&q=b','classification=invalid'):
            with self.subTest(query=query):
                with self.assertRaises(urllib.error.HTTPError) as exc:self.get('/api/regulations?'+query)
                self.assertEqual(exc.exception.code,400)
        stale=body['archive_url'].replace('sort=published_desc','sort=title_asc')
        with self.assertRaises(urllib.error.HTTPError) as exc:self.get(stale)
        self.assertEqual(exc.exception.code,409)
        file=body['items'][0]['files'][0]
        with self.get(file['download_url']) as response:self.assertTrue(response.read().startswith(b'%PDF-'))
        with self.get(file['preview_url'],{'Range':'bytes=0-3'}) as response:
            self.assertEqual(response.status,206);self.assertEqual(response.read(),b'%PDF')
        path=base/'Indonesia/pipeline/raw/objects'/(file['id']+'.pdf');path.write_bytes(b'corrupt')
        with self.assertRaises(urllib.error.HTTPError) as exc:self.get(file['download_url'])
        self.assertEqual(exc.exception.code,409)

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
