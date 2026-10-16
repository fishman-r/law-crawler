"""每个平台独立维护发现规则，主流程统一保存和去重。"""
from .trade import ADAPTERS as TRADE
from .ojk import ADAPTERS as OJK

from .ministries import ADAPTERS as MINISTRIES

from .national import ADAPTERS as NATIONAL
from .institutions import ADAPTERS as INSTITUTIONS

from .canada import ADAPTERS as CANADA

from .germany import ADAPTERS as GERMANY

from .eu_other import ADAPTERS as EU_OTHER

ADAPTERS = {**EU_OTHER, **GERMANY, **CANADA, **TRADE, **OJK, **MINISTRIES, **NATIONAL, **INSTITUTIONS}
