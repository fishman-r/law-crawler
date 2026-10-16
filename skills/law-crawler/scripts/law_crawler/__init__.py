"""官方法规采集运行时；本目录是完整skill内的唯一运行源码。"""

import sys

if sys.version_info < (3, 10):
    print("错误: 需要完整Python 3.10+，请先检查实际解释器版本。", file=sys.stderr)
    raise SystemExit(2)
