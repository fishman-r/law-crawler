#!/usr/bin/env python3
"""兼容原命令；实际实现直接维护在skill/law-crawler/scripts/law_crawler中。"""
from law_crawler.download_regulations import main

if __name__ == "__main__":
    raise SystemExit(main())
