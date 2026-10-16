"""从工作区法规索引建立只读法域注册表；输入校验不发请求、不创建目录。"""
from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urlsplit


ALIASES = {
    'Indonesia': ('Indonesia', '印度尼西亚', '印尼', 'ID', 'IDN'),
    'Canada': ('Canada', '加拿大', 'CA', 'CAN'),
    'EU': ('EU', 'European Union', '欧盟', '欧洲联盟'),
    'Germany': ('Germany', '德国', 'DE', 'DEU'),
    'France': ('France', '法国', 'FR', 'FRA'),
    'Netherlands': ('Netherlands', '荷兰', 'NL', 'NLD'),
    'Italy': ('Italy', '意大利', 'IT', 'ITA'),
}


@dataclass(frozen=True)
class JurisdictionSelection:
    name: str
    index: Path
    platforms: tuple[str, ...]
    countries: tuple[str, ...]


def _key(value):
    return ' '.join(value.strip().casefold().split())


def _platforms(text):
    return tuple(dict.fromkeys(urlsplit(url).hostname.removeprefix('www.')
                              for url in re.findall(r'\]\((https?://[^\s)]+)\)', text)
                              if urlsplit(url).hostname))


def jurisdiction_registry(root):
    """只登记存在且包含官方链接的索引，EU成员须在实际章节中出现。"""
    registry = {}
    countries = Path(root).resolve() / 'countries'
    if countries.is_symlink():
        raise ValueError('工程索引目录不得为符号链接')
    for path in sorted(countries.glob('*/index.md')):
        if (path.is_symlink() or path.parent.is_symlink() or not path.is_file()
                or not path.resolve().is_relative_to(countries.resolve())):
            continue
        name = path.parent.name
        if not re.fullmatch(r'[\w-]+', name) or name in ('.', '..'):
            continue
        text = path.read_text(encoding='utf-8-sig')
        platforms = _platforms(text)
        if not platforms:
            continue
        registry[name] = JurisdictionSelection(name, path, platforms, (name,))
        if name != 'EU':
            continue
        members = []
        headings = list(re.finditer(r'^###\s+(.+)$', text, re.MULTILINE))
        for i, heading in enumerate(headings):
            label = heading.group(1)
            match = re.search(r'[（(]([A-Za-z][A-Za-z -]+)\s*/\s*[A-Z]{2}[）)]', label)
            if not match:
                continue
            country = match.group(1).strip()
            section = text[heading.end():headings[i + 1].start() if i + 1 < len(headings) else len(text)]
            hosts = _platforms(section.split('\n## ', 1)[0])
            if hosts:
                members.append(country)
                # 一个独立国家索引优先于EU聚合索引。
                registry.setdefault(country, JurisdictionSelection(country, path, hosts, (country,)))
        registry['EU'] = JurisdictionSelection('EU', path, platforms, tuple(members))
    return registry


def resolve_jurisdiction(root, jurisdiction, index=None):
    """显式法域及可选索引必须与注册表一致，否则抛ValueError。"""
    if not isinstance(jurisdiction, str) or not jurisdiction.strip():
        raise ValueError('必须显式提供适用国家/法域（--jurisdiction）')
    registry = jurisdiction_registry(root)
    selected = None
    for name, item in registry.items():
        if _key(jurisdiction) in {_key(alias) for alias in ALIASES.get(name, (name,))}:
            selected = item
            break
    if selected is None:
        available = ', '.join(registry) or '无可用索引'
        raise ValueError(f'国家/法域未在工程索引中登记: {jurisdiction}；可用: {available}')
    if index is not None:
        candidate = Path(index).expanduser()
        candidate = (Path(root) / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        if candidate != selected.index.resolve():
            raise ValueError(f'所选索引与法域 {selected.name} 不一致，应使用 {selected.index}')
    return selected
