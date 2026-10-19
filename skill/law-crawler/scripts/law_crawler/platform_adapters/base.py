"""官方平台适配器的最小接口；下载和持久去重仍由主流程负责。"""
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

from law_crawler.regulation_metadata import extract_metadata


@dataclass
class Document:
    detail_url: str
    pdf_urls: list[str]
    metadata: dict

    @property
    def file_urls(self):
        """通用原件链接；保留 pdf_urls 构造参数兼容已有调用者。"""
        return self.pdf_urls


def document_url(url):
    """链接发现仅筛选文档后缀，最终格式由文件字节确认。

    扫描图像可经显式浏览器 manifest 导入；不自动收集页面图片/图标。
    """
    return unquote(urlsplit(url).path).lower().endswith(
        ('.pdf', '.docx', '.xlsx', '.pptx', '.odt', '.ods', '.odp', '.rtf', '.zip'))


class Links(HTMLParser):
    def __init__(self, html, base):
        super().__init__()
        self.base, self.urls = base, []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        key = {'a': 'href', 'iframe': 'src', 'embed': 'src', 'object': 'data'}.get(tag)
        if key and attrs.get(key):
            url = urldefrag(urljoin(self.base, attrs[key]))[0]
            if urlsplit(url).scheme in ('https', 'http') and url not in self.urls:
                self.urls.append(url)


def text_html(body):
    return body.decode('utf-8', errors='replace')


def field(value, raw, label, url, meaning, status=None):
    return dict(value=value, raw=raw, source_label=label, source_url=url,
                status=status or ('observed' if value is not None else 'not_provided'), meaning=meaning)


class Adapter:
    country = 'Indonesia'
    platform = ''
    entry_urls = ()
    allowed_hosts = ()

    def discover(self, fetch, limit):
        """fetch(url)->(bytes, final_url); 返回有限条详情 URL，不下载原件。"""
        raise NotImplementedError

    def read_document(self, url, fetch):
        body, final = fetch(url)
        html = text_html(body)
        return Document(final, [u for u in Links(html, final).urls
                                if document_url(u)
                                and urlsplit(u).hostname in self.allowed_hosts],
                        extract_metadata(html, final, self.platform))
