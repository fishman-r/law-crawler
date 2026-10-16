"""工程兼容导入桥；全部运行实现直接加载skill内的唯一源码。"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

_runtime = Path(__file__).resolve().parents[1] / 'skills/law-crawler/scripts/law_crawler'
_spec = spec_from_file_location(__name__, _runtime / '__init__.py',
                                submodule_search_locations=[str(_runtime)])
if _spec is None or _spec.loader is None:
    raise ImportError('缺少完整skills/law-crawler运行源码')
_module = module_from_spec(_spec)
sys.modules[__name__] = _module
_spec.loader.exec_module(_module)
