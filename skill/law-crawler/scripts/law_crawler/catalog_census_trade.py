"""贸易部目录普查，复用已归档详情元数据，不请求原件。"""
import argparse,json
from pathlib import Path
from law_crawler.catalog_census_common import Census,atomic
from law_crawler.platform_adapters.trade import TradeAdapter


def run(root,previous_state,cutoff=None):
    c=Census(root,'kemendag.go.id',['jdih.kemendag.go.id'],cutoff)
    previous=json.loads(Path(previous_state).read_text())
    docs={v['detail_url']:v for v in previous['documents'].values() if v.get('platform')=='kemendag.go.id'}
    adapter=TradeAdapter();path=c.root/'pagination.json'
    checkpoint=json.loads(path.read_text()) if path.exists() else None
    def value(m,k):
        v=m.get(k);return v.get('value') if isinstance(v,dict) else v
    def save(state):
        rows=[]
        for url in state['details']:
            old=docs.get(url,{})
            m=old.get('metadata',{});identity=value(m,'legal_identity') or {}
            rows.append(dict(url=url,title=value(m,'title') or '',identity=identity,
                identity_status=m.get('legal_identity',{}).get('status'),
                type=identity.get('type'),year=identity.get('year'),number=identity.get('number'),issuer=identity.get('issuer'),
                document_type=value(m,'document_type'),published_at=value(m,'published_at'),
                promulgated_at=value(m,'promulgated_at'),enacted_at=value(m,'enacted_at'),
                metadata=m,metadata_source='previous_archived_detail' if m else 'not_fetched',
                version_status='unverified'))
        c.records(rows);atomic(path,state)
        c.checkpoint(status='complete' if state.get('complete') else 'running',catalogue_complete=bool(state.get('complete')),
            observed_total=state.get('observed_total'),pages=len(state['streams']['peraturan']['visited_urls']),
            pending_pages=len(state.get('pending_urls',[])),excluded_drafts=len(state.get('excluded_details',[])),
            scope_note='官方混合材料目录；法规类型、截止日期和全版本需分别核验。',all_versions_complete=False)
    state=adapter.discover_bulk(c.fetch,max_pages=10000,checkpoint=checkpoint,on_page=save)
    save(state)
    c.checkpoint(status='complete' if state['complete'] else 'incomplete',reason=state['streams']['peraturan'].get('stop_reasons'))
    print(json.dumps(c.checkpoint(),ensure_ascii=False),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--previous-state',required=True);p.add_argument('--cutoff',help='Explicit YYYY-MM-DD cutoff; omit only to inherit a frozen plan');a=p.parse_args();run(a.root,a.previous_state,a.cutoff)
