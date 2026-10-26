#!/usr/bin/env python3
"""在已部署的观测服务上核验法规库契约；不启动本地服务、不运行采集。"""
import argparse
import base64
import json
import os
import re
import urllib.parse
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True, help='已部署服务 URL，例如 http://127.0.0.1:8765')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    headers = {'Accept': 'application/json'}
    user, password = os.environ.get('OBSERVER_CHECK_USER'), os.environ.get('OBSERVER_CHECK_PASSWORD')
    if user is not None and password is not None:
        headers['Authorization'] = 'Basic ' + base64.b64encode((user + ':' + password).encode()).decode()

    def get(**filters):
        params = dict(page=1, page_size=20, classification='all', sort='published_desc')
        params.update(filters)
        request = urllib.request.Request(base + '/api/regulations?' + urllib.parse.urlencode(params), headers=headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.load(response)
        assert data.get('available') is True, '法规库未就绪'
        assert isinstance(data.get('items'), list), '缺少法规记录列表'
        assert data['page'] == params['page'] and data['page_size'] == params['page_size'], '分页契约不符'
        assert len(data['items']) <= params['page_size'], '超过当页条数'
        ids = [item['id'] for item in data['items']]
        assert len(ids) == len(set(ids)), '当页出现重复法规身份'
        for item in data['items']:
            assert re.fullmatch(r'[0-9a-f]{64}', item['id']), '法规标识不规范'
            assert item['identity_status'] in ('identified', 'pending'), '缺少身份核验状态'
            assert item['files'], '展示了没有成功归档文件的记录'
            for file in item['files']:
                assert file['role'] in ('body', 'annex', 'related', 'pending'), '文件用途未标注'
                assert file['download_url'] == '/api/regulations/files/' + file['id'] + '/download', '文件下载端点不规范'
        return data

    first = get()
    checks = ['法规记录与文件分离', '身份与用途待核验标注', '分页大小']
    if first['total_pages'] > 1:
        second = get(page=2)
        assert not ({item['id'] for item in first['items']} & {item['id'] for item in second['items']}), '相邻页重叠'
        checks.append('相邻页无重复记录')
    for facet in first.get('facets', {}).get('classification', []):
        if facet['value'] == 'all':
            continue
        data = get(classification=facet['value'])
        assert all(item['classification'] == facet['value'] for item in data['items']), '分类筛选不符'
    checks.append('分类筛选')
    for order in ('title_asc', 'title_desc', 'year_asc', 'year_desc', 'published_asc', 'published_desc'):
        get(sort=order)
    checks.append('六种排序契约')
    get(q='__law_crawler_no_match_730623__')
    checks.append('搜索空结果')
    if first['items']:
        url = urllib.parse.urlsplit(first['archive_url'])
        assert not url.scheme and not url.netloc and url.path == '/api/regulations/archive.zip', '当页导出地址不规范'
        assert urllib.parse.parse_qs(url.query).get('token'), '当页导出缺少快照令牌'
        checks.append('当页导出快照令牌')
    print(json.dumps({'passed': True, 'checks': checks, 'summary': first['summary'], 'total': first['total']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
