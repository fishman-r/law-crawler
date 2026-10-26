"""Code-backed capability descriptions, never a claim of live access or coverage.

Evidence paths are relative to scripts/law_crawler. A method or census route is
implementation evidence only; successful traversal requires a separate run audit.
"""
import ast
import hashlib
import inspect
from pathlib import Path
from urllib.parse import urlsplit
from .platform_adapters import ADAPTERS
from .platform_adapters.base import Adapter

SOURCE_ALIASES = {'dpr': 'dpr.go.id', 'ma': 'mahkamahagung.go.id', 'mk': 'mkri.id',
                  'laws-lois.justice.gc.ca': 'laws.justice.gc.ca',
                  'ojk.go.id-jdih-supplement': 'ojk.go.id'}
_HOST_ROUTES = {'jdih.ojk.go.id': 'ojk.go.id-jdih-supplement'}
DIMENSIONS = ('discovery', 'pagination', 'originals', 'attachments', 'versions', 'cutoff')
_ROOT = Path(__file__).resolve().parent
# These are adapter routing rules, not configured batch scope or live success.
_CENSUS_ROUTES = {
    'peraturan.go.id': ('catalog_census_national.py', 'crawl', 'HTTP目录链接遍历；上限、访问阻断和未发现分支仍需运行证据'),
    'bpk.go.id': ('catalog_census_national.py', 'crawl', 'HTTP目录链接遍历；不能把受阻或首屏结果认作目录穷尽'),
    'jdihn.go.id': ('catalog_census_national.py', 'crawl', 'HTTP目录链接遍历与可选浏览器探测；实验入口不计作正式目录覆盖'),
    'dpr.go.id': ('catalog_census_judiciary.py', 'dpr', '按文种和年份分支遍历；保留无详情链接行'),
    'mahkamahagung.go.id': ('catalog_census_judiciary.py', 'courts', '司法规范目录分页和站点地图；普通浏览器补充需单独执行'),
    'mkri.id': ('catalog_census_judiciary.py', 'courts', '司法规范目录分页和站点地图；普通浏览器补充需单独执行'),
    'kemendag.go.id': ('catalog_census_trade.py', 'run', '复用贸易部数字分页与旧详情元数据；不能证明全部历史版本'),
    'kemkes.go.id': ('catalog_census_ministries.py', 'kemkes', '平台专用目录分页；需运行页数与总量对账'),
    'kemenkeu.go.id': ('catalog_census_ministries.py', 'kemenkeu', '按目录类型分组遍历；需运行分组与分页对账'),
    'bkpm.go.id': ('catalog_census_ministries.py', 'bkpm', '公开目录API分页；需运行总量和分支对账'),
    'ojk.go.id': ('catalog_census_ministries.py', 'ojk', 'SharePoint表单分页；中央列表不等于JDIH补充入口'),
    'ojk.go.id-jdih-supplement': ('catalog_census_ministries.py', 'ojk_jdih', 'JDIH补充目录独立遍历；需单独冻结范围与计数'),
}


def _host(value):
    if not isinstance(value, str) or not value.strip():
        return ''
    value = value.strip().lower()
    return (urlsplit(value).hostname or '') if '://' in value else value


def canonical_platform(platform):
    name = _host(platform).removeprefix('www.')
    if name in _HOST_ROUTES:
        return SOURCE_ALIASES[_HOST_ROUTES[name]]
    if name in SOURCE_ALIASES:
        return SOURCE_ALIASES[name]
    if name in ADAPTERS:
        return name
    candidates = {key for key, adapter in ADAPTERS.items()
                  if name.removeprefix('www.') in {h.lower().removeprefix('www.') for h in adapter.allowed_hosts}}
    # Aggregators allow member sites for linked documents; this does not make
    # those members their own catalogue entry. Prefer only a uniquely declared
    # entry host, never the first match or a domain-suffix guess.
    owners = {key for key in candidates if name.removeprefix('www.') in {
        (urlsplit(url).hostname or '').removeprefix('www.')
        for url in getattr(ADAPTERS[key], 'entry_urls', ())}}
    if len(owners) == 1:
        return next(iter(owners))
    return name


def _evidence(path, symbol):
    path = Path(path).resolve()
    try:
        relative = path.relative_to(_ROOT).as_posix()
    except ValueError:
        return None
    if not path.is_file():
        return None
    raw = path.read_bytes()
    tree = ast.parse(raw, filename=relative)
    if symbol.rsplit('.', 1)[-1] not in {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}:
        return None
    return {'path': relative, 'symbol': symbol, 'sha256': hashlib.sha256(raw).hexdigest(),
            'kind': 'source_code', 'proves': 'implementation_only'}


def _method_evidence(adapter, name):
    method = getattr(adapter, name, None)
    if not callable(method):
        return None
    function = getattr(method, '__func__', method)
    if name == 'discover' and function is Adapter.discover:
        return None
    path = inspect.getsourcefile(function)
    return _evidence(path, function.__qualname__) if path else None


def _cap(status, evidence, *limitations):
    return {'status': status, 'verified': False, 'evidence': [x for x in evidence if x], 'limitations': list(limitations)}


def describe_platform(platform):
    requested = _host(platform)
    key = canonical_platform(platform)
    adapter = ADAPTERS.get(key)
    result = dict(requested_platform=requested, canonical_platform=key,
                  source_alias=requested if requested != key else None,
                  registered_adapter=adapter is not None,
                  country=getattr(adapter, 'country', None), observed_access='not_assessed',
                  assessment_basis='current_source_code_not_live_verification')
    if adapter is None:
        result['capabilities'] = {name: _cap('not_supported', [], '索引入口尚无可唯一匹配的采集适配器；未发起联网验证') for name in DIMENSIONS}
        return result
    discover = _method_evidence(adapter, 'discover')
    body = _method_evidence(adapter, 'read_document')
    bulk = _method_evidence(adapter, 'discover_bulk')
    route = _CENSUS_ROUTES.get(_HOST_ROUTES.get(requested, requested), _CENSUS_ROUTES.get(key))
    route_ev = _evidence(_ROOT / route[0], route[1]) if route else None
    # An implemented route can enumerate known branches, never certify exhaustiveness.
    pagination = _cap('partial', [bulk, route_ev],
        route[2] if route and route_ev else '仅有限发现；未发现可核对全部分页的专用入口',
        '代码存在不证明本次分页穷尽；需核对截点、分支、页上限、重复页和官方总量')
    body_is_specialized = getattr(type(adapter), 'read_document', None) is not Adapter.read_document
    capabilities = {
        'discovery': _cap('implemented' if discover else 'not_supported', [discover, route_ev], '有限目录发现；不是全量或实时可达证明'),
        'pagination': pagination,
        'originals': _cap('implemented' if body and body_is_specialized else 'partial', [body], '仅提取已发现的正文候选；下载成功、正文身份及法律效力须分别核验'),
        'attachments': _cap('partial', [body], '详情链接提取不证明附件全集；需独立正文关系、角色及已知缺件审核'),
        'versions': _cap('unknown', [body], '尚无此平台全部历史版本清单及穷尽能力的证据；不得以多文件数替代版本数'),
        'cutoff': _cap('partial' if route_ev else 'unknown', [route_ev, body], '字段提取或截点分类不证明公布日期齐全；缺日期和仅年份记录须单列'),
    }
    result.update(capabilities=capabilities, entry_urls=list(adapter.entry_urls),
                  allowed_hosts=list(adapter.allowed_hosts), census_route=route_ev)
    if key in ('mahkamahagung.go.id', 'mkri.id'):
        result['browser_supplement'] = _cap('partial', [_evidence(_ROOT/'catalog_census_judiciary.py', 'browser_courts')],
            '浏览器目录补充需可用浏览器环境和单独执行；不包含自动通过挑战或原件下载')
    elif key in ('peraturan.go.id', 'bpk.go.id', 'jdihn.go.id'):
        result['browser_supplement'] = _cap('partial', [_evidence(_ROOT/'catalog_census_national.py', 'browser_probe')],
            '浏览器探测只提供可访问性证据，不证明目录或原件采集完成')
    return result
