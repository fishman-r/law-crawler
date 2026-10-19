"""校验本地数据迁移记录，仅为原计划生成运行时路径视图。"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat


MAX_MANIFEST_BYTES = 1024 * 1024
KINDS = ('downloads', 'reports', 'tmp')
_BATCH_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')
_SHA256 = re.compile(r'[0-9a-f]{64}\Z')


def _path(value):
    if not isinstance(value, (str, Path)):
        raise ValueError('迁移路径必须为字符串')
    raw = str(value)
    if (not raw or '\x00' in raw
            or any(part in ('.', '..') for part in raw.replace('\\', '/').split('/'))):
        raise ValueError('迁移路径不得包含空值、控制字节或相对跳转')
    return Path(raw)


def _absolute(value):
    path = _path(value)
    if not path.is_absolute() or str(path) != str(value):
        raise ValueError('迁移清单必须登记规范的绝对路径')
    return path


def _no_symlinks(path):
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('迁移后的路径不得包含符号链接')


@dataclass(frozen=True)
class PlanRelocation:
    download_base: Path
    old_download_base: Path | None = None
    roots: tuple = ()

    def matches_download_base(self, value):
        return (value == str(self.download_base)
                or (self.old_download_base is not None and value == str(self.old_download_base)))

    def map_path(self, value):
        """只映射登记的三个旧根；外部输入路径保留原义，不能猜测替代文件。"""
        if not self.roots:
            return Path(value)
        path = _path(value)
        if path.is_absolute():
            for before, after in self.roots:
                if path.is_relative_to(before):
                    mapped = after / path.relative_to(before)
                    _no_symlinks(mapped)
                    return mapped
        return path


def _read_manifest(path):
    if path.is_symlink():
        raise ValueError('数据迁移清单不得为符号链接')
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                             | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError('无法安全读取数据迁移清单') from exc
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_MANIFEST_BYTES:
            raise ValueError('数据迁移清单不是有界常规文件')
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError('数据迁移清单超过大小上限')
    try:
        result = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise ValueError('数据迁移清单不是有效的 UTF-8 JSON') from exc
    if not isinstance(result, dict):
        raise ValueError('数据迁移清单必须为对象')
    return result


def plan_relocation(download_base, batch_id, plan_bytes):
    """旧根映射绑定批次 ID 和原 plan.json 字节 SHA；不修改任何证据。"""
    base = Path(download_base).absolute()
    manifest = _read_manifest(base / '.relocation.json')
    identity = PlanRelocation(base)
    if manifest is None:
        return identity
    _no_symlinks(base)
    if (manifest.get('schema_version') != 1 or isinstance(manifest.get('schema_version'), bool)
            or base.name != 'downloads' or base.parent.name != 'data'):
        raise ValueError('数据迁移清单版本或当前下载根目录无效')
    current = _absolute(manifest.get('download_base'))
    old = _absolute(manifest.get('old_download_base'))
    if current != base or old.name != 'downloads' or old == current:
        raise ValueError('数据迁移清单的下载根目录不匹配')
    roots = manifest.get('roots')
    if not isinstance(roots, list) or len(roots) != len(KINDS):
        raise ValueError('数据迁移清单必须且只能登记 downloads、reports、tmp')
    mappings, seen = [], set()
    for item in roots:
        if not isinstance(item, dict) or item.get('kind') not in KINDS:
            raise ValueError('数据迁移清单含未知根目录')
        kind = item['kind']
        if kind in seen:
            raise ValueError('数据迁移清单含重复根目录')
        before, after = _absolute(item.get('before')), _absolute(item.get('after'))
        if before != old.parent / kind or after != base.parent / kind:
            raise ValueError('数据迁移清单不得改变约定之外的根目录')
        _no_symlinks(after)
        seen.add(kind)
        mappings.append((before, after))
    bindings = manifest.get('batch_plans')
    if (not isinstance(bindings, dict)
            or any(not isinstance(key, str) or not _BATCH_ID.fullmatch(key)
                   or not isinstance(value, str) or not _SHA256.fullmatch(value)
                   for key, value in bindings.items())):
        raise ValueError('数据迁移清单的批次计划 SHA 登记无效')
    try:
        created = datetime.fromisoformat(manifest['created_at'].replace('Z', '+00:00'))
        if created.tzinfo is None:
            raise ValueError()
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise ValueError('数据迁移清单缺少有效的带时区时间') from exc
    if not isinstance(batch_id, str) or not _BATCH_ID.fullmatch(batch_id):
        raise ValueError('迁移批次 ID 无效')
    expected = bindings.get(batch_id)
    if expected is not None:
        if hashlib.sha256(plan_bytes).hexdigest() != expected:
            raise ValueError('原批次计划 SHA 与数据迁移登记不一致')
        return PlanRelocation(base, old, tuple(mappings))
    # 迁移后新建的批次继续按当前目录重跑；它们没有使用旧根映射的权限。
    try:
        plan = json.loads(plan_bytes.decode('utf-8'))
    except (ValueError, UnicodeError, AttributeError) as exc:
        raise ValueError('原批次计划无效') from exc
    if not isinstance(plan, dict) or plan.get('download_base') != str(base):
        raise ValueError('原批次未登记数据迁移，不能改变下载基础目录')
    return identity
