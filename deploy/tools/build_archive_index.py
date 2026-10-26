#!/usr/bin/env python3
"""在线核验已归档原件并原子构建法规查询索引，不执行下载或模型处理。"""
import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download-base', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--title-translations', type=Path, help='可选中文参考译名 JSON；默认读取下载目录内的译名文件')
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'web'))
    from observer.archive_index import build_archive_index
    print(json.dumps(build_archive_index(args.download_base,args.state,args.output,title_translations_path=args.title_translations),ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
