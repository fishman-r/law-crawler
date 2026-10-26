"""浏览器离线导入仅新增最高法院 SEMA；来源声明不能代替真实性复核。"""
import copy
import tempfile
import unittest
from pathlib import Path

from law_crawler import import_browser_download as app
from law_crawler.crawl_store import unknown_metadata
from law_crawler.platform_adapters.base import field

DETAIL = 'https://jdih.mahkamahagung.go.id/legal-product/sema-nomor-4-tahun-2026/detail'
DOWNLOAD = DETAIL.replace('/detail', '/download/dokumen')
HOSTS = ('jdih.mahkamahagung.go.id', 'kepaniteraan.mahkamahagung.go.id')


def metadata():
    value = unknown_metadata(DETAIL)
    value['title'] = field('SEMA NOMOR 4 TAHUN 2026', 'SEMA NOMOR 4 TAHUN 2026', 'Judul', DETAIL, '官方实际标题。')
    value['document_type'] = field('judicial_guidance', 'Surat Edaran Mahkamah Agung', 'Jenis', DETAIL, 'SEMA')
    identity = {'type':'Surat Edaran Mahkamah Agung','issuer':'Mahkamah Agung','jurisdiction':'ID','number':'4','year':'2026'}
    value['legal_identity'] = field(identity, dict(identity), '官方详情身份', DETAIL, '官方法律身份。')
    return value


class BrowserJudicialGuidanceTests(unittest.TestCase):
    def test_official_sema_validated_without_mutating_original(self):
        source = metadata()
        frozen = copy.deepcopy(source)
        validated = app.validate_metadata(source, HOSTS)
        self.assertEqual(validated['document_type']['value'], 'judicial_guidance')
        self.assertIn('不等同于中国法的司法解释', validated['document_type']['meaning'])
        self.assertEqual(source, frozen)
        source['document_type']['raw'] = 'SEMA'
        self.assertEqual(app.validate_metadata(source, HOSTS)['legal_identity']['value']['number'], '4')

    def test_wrong_guidance_identity_is_rejected(self):
        for key, wrong in [('type','Putusan Mahkamah Agung'),('type','Surat Edaran Panitera'),
                           ('type','Peraturan Mahkamah Agung'),('issuer','Pengadilan Negeri'),
                           ('jurisdiction','CN'),('number',''),('number','  '),('year',None)]:
            with self.subTest(key=key,wrong=wrong):
                value = metadata()
                value['legal_identity']['value'][key] = wrong
                with self.assertRaises(ValueError):
                    app.validate_metadata(value, HOSTS)
        for status in ('partial','not_provided'):
            value = metadata()
            value['legal_identity']['status'] = status
            with self.assertRaises(ValueError):
                app.validate_metadata(value, HOSTS)
        value = metadata()
        del value['legal_identity']
        with self.assertRaises(ValueError):
            app.validate_metadata(value, HOSTS)

    def test_forged_document_kind_not_allowed(self):
        for kind in ('judgment','judicial_interpretation','article','draft'):
            value = metadata()
            value['document_type']['value'] = kind
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                app.validate_metadata(value, HOSTS)
        value = metadata()
        value['document_type']['raw'] = 'Putusan Mahkamah Agung'
        with self.assertRaises(ValueError):
            app.validate_metadata(value, HOSTS)

    def test_forged_source_and_non_ma_adapter_rejected(self):
        for name in ('title','document_type','legal_identity'):
            value = metadata()
            value[name]['source_url'] = 'https://example.invalid/fake'
            with self.subTest(name=name), self.assertRaises(ValueError):
                app.validate_metadata(value, HOSTS)
        value = metadata()
        for item in value.values():
            if isinstance(item,dict) and item.get('source_url'):
                item['source_url'] = 'https://jdih.kemendag.go.id/peraturan/sema'
        with self.assertRaises(ValueError):
            app.validate_metadata(value, ('jdih.kemendag.go.id',))
        # 即使调用方扩大白名单，也不能借其他主机放行 guidance。
        with self.assertRaises(ValueError):
            app.validate_metadata(metadata(), (*HOSTS,'example.invalid'))

    def test_prepare_offline_uses_ma_official_file_and_rejects_wrong_source(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path/'2026sema004.pdf').write_bytes(b'%PDF-1.4\nofficial fixture\n%%EOF\n')
            item = {'platform':'mahkamahagung.go.id','detail_url':DETAIL,'download_url':DOWNLOAD,
                    'local_file':'2026sema004.pdf','metadata':metadata()}
            prepared = app.prepare_item(item, ['mahkamahagung.go.id'], path, 100000)
            self.assertEqual(prepared['metadata']['document_type']['value'], 'judicial_guidance')
            self.assertFalse(prepared['final_url_verified'])
            item['download_url'] = 'https://example.invalid/fake.pdf'
            with self.assertRaises(ValueError):
                app.prepare_item(item,['mahkamahagung.go.id'],path,100000)

    def test_original_regulation_validation_preserved(self):
        value = metadata()
        value.pop('legal_identity')
        value['document_type'] = field('regulation','Peraturan Mahkamah Agung','Jenis',DETAIL,'正式法规。')
        self.assertIs(app.validate_metadata(value, HOSTS), value)
        value['document_type']['status'] = 'partial'
        with self.assertRaises(ValueError):
            app.validate_metadata(value, HOSTS)


if __name__ == '__main__':
    unittest.main()
