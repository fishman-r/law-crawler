"""按可核实法规身份分组，只以官方公布日期选本次最新候选。"""
import hashlib
import json
from datetime import date
from law_crawler.crawl_store import canonical_url


def published_date(metadata):
    field = metadata.get('published_at', {})
    value = field.get('value') if isinstance(field, dict) else None
    if not isinstance(value, str) or field.get('status') not in ('observed', 'partial'):
        return None
    try:
        parsed = date.fromisoformat(value)
        return parsed.isoformat() if len(value) == 10 else None
    except ValueError:
        return None


def identity_key(metadata, country, detail, platform):
    identity = metadata.get('legal_identity', {}).get('value')
    if isinstance(identity, dict) and identity.get('number') and identity.get('jurisdiction'):
        # 发文机关缺失时仅同平台分组，避免不同机关同编号错误合并。
        national_types = {'act', 'regulation', 'undang-undang', 'peraturan pemerintah', 'peraturan presiden'}
        kind = str(identity.get('type') or '').casefold()
        issuer = identity.get('issuer') or ('national' if kind in national_types else 'source:' + platform)
        item = [country, identity['jurisdiction'], issuer, identity.get('type'),
                identity['number'], identity.get('year')]
        return json.dumps(item, ensure_ascii=False), 'identified'
    # 未核实身份时不按标题/语义推断；同一个详情的不同格式仍为一个候选。
    return json.dumps([country, platform, canonical_url(detail)], ensure_ascii=False), 'source_only'


def _same_official_resource(group):
    # 仅接受适配器核实的正文资源 URI，且官方版本观察一致；不按标题推测。
    resources=[]; versions=[]
    for item in group:
        field=item['metadata'].get('official_resource_uri',{})
        if field.get('status')!='observed' or not field.get('value') or not item.get('file_urls'):
            return False
        resources.append(canonical_url(field['value']))
        versions.append((item['metadata'].get('text_version',{}).get('value'),
                         item['metadata'].get('consolidated_to',{}).get('value'),
                         item['metadata'].get('last_amended_at',{}).get('value')))
    return len(set(resources))==1 and len(set(versions))==1


def select_latest(candidates):
    groups = {}
    for item in candidates:
        key, confidence = identity_key(item['metadata'], item['country'], item['detail_url'], item['platform'])
        item['identity_key'] = key
        item['identity_status'] = confidence
        item['publication_date'] = published_date(item['metadata'])
        groups.setdefault(key, []).append(item)
    selected = []
    for key, group in groups.items():
        known = [item for item in group if item['publication_date']]
        if known:
            newest = max(item['publication_date'] for item in known)
            winners = [item for item in known if item['publication_date'] == newest]
            winner = sorted(winners, key=lambda item: (item['detail_url'], item['platform']))[0]
            reason = 'latest_published_at'
            uncertain = any(not item['publication_date'] for item in group) or len(winners)>1
        elif len(group) == 1:
            winner, reason, uncertain = group[0], 'single_candidate_date_unknown', True
        elif _same_official_resource(group):
            winner = sorted(group, key=lambda item: (item['detail_url'], item['platform']))[0]
            reason, uncertain = 'same_official_resource_date_unknown', True
        else:
            winner, reason, uncertain = None, 'publication_dates_unknown_selection_unresolved', True
        for item in group:
            item['selection'] = dict(selected=item is winner, reason=reason if item is winner or winner is None else
                ('duplicate_official_resource' if reason=='same_official_resource_date_unknown' else
                 'same_date_source_tie' if item['publication_date'] == winner['publication_date'] else 'superseded_source'),
                latest_unverified=uncertain, selected_id=winner['id'] if winner else None)
        if winner:
            selected.append(winner)
    return selected
