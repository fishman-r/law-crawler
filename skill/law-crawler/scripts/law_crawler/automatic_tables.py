"""Conservative local ruled-table detection; no model calls or silent cross-page merges."""
from __future__ import annotations
import argparse
import copy
import hashlib
import html
import json
import re
import time
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def runs(values, minimum, gap=3):
    """Long ink runs, joining only small raster gaps."""
    import numpy as np
    indices = np.flatnonzero(values)
    if not len(indices):
        return []
    breaks = np.flatnonzero(np.diff(indices) > gap + 1)
    starts = np.r_[0, breaks + 1]
    ends = np.r_[breaks, len(indices) - 1]
    return [(int(indices[a]), int(indices[b])) for a, b in zip(starts, ends) if indices[b] - indices[a] + 1 >= minimum]


def cluster(values, tolerance=5):
    groups = []
    for value in sorted(values):
        if groups and value - groups[-1][-1] <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [round(sum(group) / len(group)) for group in groups]


def detect(image_path, threshold=170):
    import numpy as np
    from PIL import Image
    bitmap = np.asarray(Image.open(image_path).convert('L')) < threshold
    height, width = bitmap.shape
    horizontal = [(a, y, b, y) for y in range(height) for a,b in runs(bitmap[y], max(40, round(width * .12)))]
    vertical = [(x, a, x, b) for x in range(width) for a,b in runs(bitmap[:, x], max(40, round(height * .025)))]
    # Connect intersecting horizontal/vertical strokes; text-sized runs stay excluded.
    parent = list(range(len(horizontal) + len(vertical)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for hi, (left,y,right,_) in enumerate(horizontal):
        for vi,(x,top,_,bottom) in enumerate(vertical):
            if left-5 <= x <= right+5 and top-5 <= y <= bottom+5:
                a,b=find(hi),find(len(horizontal)+vi)
                parent[a]=b
    groups={}
    for i,line in enumerate(horizontal+vertical):
        groups.setdefault(find(i),[]).append((i < len(horizontal),line))
    tables=[]
    for group in groups.values():
        hs=[line for is_h,line in group if is_h]; vs=[line for is_h,line in group if not is_h]
        xs=cluster([line[0] for line in vs]); ys=cluster([line[1] for line in hs])
        if len(xs)<3 or len(ys)<3:
            continue
        # Require sufficient page area to avoid classifying stamps/boxes as tables.
        if (xs[-1]-xs[0])*(ys[-1]-ys[0]) < width*height*.01:
            continue
        tables.append({'xs':xs,'ys':ys,'horizontal':hs,'vertical':vs,'width':width,'height':height})
    return sorted(tables,key=lambda t:(t['ys'][0],t['xs'][0]))


def coverage(lines, axis, fixed, begin, end):
    # Union of aligned observed line intervals, not the bounding span of broken strokes.
    intervals=[]
    for x1,y1,x2,y2 in lines:
        if abs((x1 if axis=='v' else y1)-fixed)<=5:
            a,b=(y1,y2) if axis=='v' else (x1,x2)
            if b>=begin and a<=end:
                intervals.append((max(begin,a),min(end,b)))
    merged=[]
    for a,b in sorted(intervals):
        if merged and a<=merged[-1][1]+3:merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:merged.append((a,b))
    return sum(max(0,b-a) for a,b in merged)/max(1,end-begin)


def reconstruct(table, observations):
    xs,ys=table['xs'],table['ys']; nc,nr=len(xs)-1,len(ys)-1
    parent=list(range(nc*nr)); uncertain=[]
    def find(i):
        while parent[i]!=i:i=parent[i]
        return i
    def boundary(a,b,value,detail):
        if value<.15:parent[find(a)]=find(b)
        elif value<.75:uncertain.append({'boundary':detail,'ink_coverage':value,'action':'not_merged'})
    for r in range(nr):
        for c in range(nc-1):
            value=coverage(table['vertical'],'v',xs[c+1],ys[r],ys[r+1])
            boundary(r*nc+c,r*nc+c+1,value,{'row':r,'column_edge':c+1})
    for r in range(nr-1):
        for c in range(nc):
            value=coverage(table['horizontal'],'h',ys[r+1],xs[c],xs[c+1])
            boundary(r*nc+c,(r+1)*nc+c,value,{'row_edge':r+1,'column':c})
    groups={}
    for i in range(nr*nc):groups.setdefault(find(i),[]).append((i//nc,i%nc))
    cells=[]
    for group in groups.values():
        top,left=min(r for r,c in group),min(c for r,c in group)
        bottom,right=max(r for r,c in group)+1,max(c for r,c in group)+1
        if len(group)!=(bottom-top)*(right-left):
            uncertain.append({'reason':'non_rectangular_missing_borders','slots':group,'action':'preserve_separate_cells'})
            members=[(r,c,r+1,c+1) for r,c in group]
        else:members=[(top,left,bottom,right)]
        for r,c,b,d in members:
            cells.append({'row':r,'column':c,'rowspan':b-r,'colspan':d-c,'bbox':[xs[c]/table['width'],ys[r]/table['height'],(xs[d]-xs[c])/table['width'],(ys[b]-ys[r])/table['height']], 'observations':[]})
    assigned=set()
    for i,obs in enumerate(observations):
        x,y,w,h=obs['bbox']; cx,cy=x+w/2,y+h/2
        matches=[cell for cell in cells if cell['bbox'][0]<=cx<=cell['bbox'][0]+cell['bbox'][2] and cell['bbox'][1]<=cy<=cell['bbox'][1]+cell['bbox'][3]]
        if len(matches)==1:
            cell=matches[0]; bx,by,bw,bh=cell['bbox']
            crosses=x<bx or y<by or x+w>bx+bw or y+h>by+bh
            cell['observations'].append({'observation_index':i,**obs,'crosses_cell_boundary':crosses});assigned.add(i)
            if crosses:uncertain.append({'reason':'observation_crosses_boundary','observation_index':i,'action':'retained_in_cell'})
        elif matches:uncertain.append({'reason':'center_on_multiple_cells','observation_index':i,'action':'preserved_outside_table'})
        elif x<xs[-1]/table['width'] and x+w>xs[0]/table['width'] and y<ys[-1]/table['height'] and y+h>ys[0]/table['height']:
            uncertain.append({'reason':'bbox_intersects_table_center_outside','observation_index':i,'action':'preserved_outside_table'})
    for cell in cells:
        cell['observations'].sort(key=lambda o:(o['bbox'][1],o['bbox'][0],o['observation_index']))
        cell['text']='\n'.join(o['text'] for o in cell['observations']) or '[MISSING: visual review required]'
    cells.sort(key=lambda c:(c['row'],c['column']))
    rendered=['<table data-status="needs-independent-visual-review">']
    for r in range(nr):
        rendered.append('<tr>')
        for cell in cells:
            if cell['row']==r:
                rendered.append(f'<td rowspan="{cell["rowspan"]}" colspan="{cell["colspan"]}">'+html.escape(cell['text']).replace('\n','<br>')+'</td>')
        rendered.append('</tr>')
    rendered.append('</table>')
    return {'rows':nr,'columns':nc,'column_edges':[x/table['width'] for x in xs],'row_edges':[y/table['height'] for y in ys], 'cells':cells,'ambiguities':uncertain,'assigned_observation_indices':sorted(assigned),'html':'\n'.join(rendered),'review_status':'needs_independent_visual_review'}


def deduplicate_exact_candidates(candidates):
    """Remove only identical detected line sets, never overlapping/nested geometry."""
    kept, removed, seen = [], [], {}
    for index, table in enumerate(candidates):
        key = (tuple(table['xs']), tuple(table['ys']), table['width'], table['height'],
               tuple(sorted(tuple(line) for line in table['horizontal'])),
               tuple(sorted(tuple(line) for line in table['vertical'])))
        if key in seen:
            removed.append({'candidate_index': index, 'same_as_candidate_index': seen[key],
                            'reason': 'identical_geometry_and_observed_line_sets'})
        else:
            seen[key] = index
            kept.append(table)
    return kept, removed


def organization_heading(observations):
    """Recognise explicit Indonesian diagram headings, not prose mentioning diagrams."""
    for observation in observations:
        text = re.sub(r'\s+', ' ', observation['text']).strip()
        if text.isupper() and re.match(r'^(?:BAGAN (?:SUSUNAN |STRUKTUR )?|STRUKTUR )ORGANISASI\b', text):
            return text
    return None


def prepare_page_tables(imagepath, observations, threshold):
    """Isolate ambiguous whole pages without altering or dropping their original OCR."""
    heading = organization_heading(observations)
    if heading:
        return [], [], {'kind': 'organization_diagram_unresolved',
                        'reason': 'Official organization-diagram heading: ' + heading +
                                  '; preserve original OCR and page image; graph relationships have not been transcribed.'}, []
    candidates, duplicates = deduplicate_exact_candidates(detect(imagepath, threshold))
    tables = [reconstruct(candidate, observations) for candidate in candidates]
    all_assigned = set()
    for index, table in enumerate(tables):
        overlap = all_assigned & set(table['assigned_observation_indices'])
        geometry_overlap = any(
            max(table['column_edges'][0], previous['column_edges'][0]) < min(table['column_edges'][-1], previous['column_edges'][-1])
            and max(table['row_edges'][0], previous['row_edges'][0]) < min(table['row_edges'][-1], previous['row_edges'][-1])
            for previous in tables[:index])
        if overlap or geometry_overlap:
            rejected = [{'candidate_index': i, 'column_edges': t['column_edges'], 'row_edges': t['row_edges'],
                         'assigned_observation_indices': t['assigned_observation_indices']}
                        for i, t in enumerate(tables)]
            return [], duplicates, {'kind': 'overlapping_table_candidates_unresolved',
                'reason': 'Distinct candidate line groups have overlapping bounds or assign the same OCR observations (' +
                          ','.join(str(i) for i in sorted(overlap)) +
                          '); possible nested diagram or ambiguous borders. Original OCR retained; no table or graph recovery claimed.'}, rejected
        all_assigned.update(table['assigned_observation_indices'])
    return tables, duplicates, None, []


def continuation_candidates(pages):
    candidates=[]
    for previous,current in zip(pages,pages[1:]):
        if current['page']!=previous['page']+1:continue
        for ai,a in enumerate(previous['tables']):
            for bi,b in enumerate(current['tables']):
                if a['columns']!=b['columns']:continue
                error=max(abs(x-y) for x,y in zip(a['column_edges'],b['column_edges']))
                if error>.04:continue
                ah=' '.join(c['text'].lower() for c in a['cells'] if c['row']==0)
                bh=' '.join(c['text'].lower() for c in b['cells'] if c['row']==0)
                tokens_a,tokens_b=set(ah.split()),set(bh.split())
                similarity=len(tokens_a&tokens_b)/max(1,len(tokens_a|tokens_b))
                geometry=a['row_edges'][-1]>.65 and b['row_edges'][0]<.4
                context=' '.join(current.get('outside_text',[])).lower()
                marker=any(word in context for word in ['lanjutan','continued','continuation'])
                score=.4*max(0,1-error/.04)+.3*similarity+.2*geometry+.1*marker
                candidates.append({'from_page':previous['page'],'from_table':ai,'to_page':current['page'],'to_table':bi,'geometry_max_edge_difference':error,'header_token_similarity':similarity,'page_edge_geometry':geometry,'continuation_context_marker':marker,'heuristic_score_not_probability':score,'status':'candidate_requires_visual_review','action':'not_merged'})
    return candidates


def input_configuration(sidecar, threshold=170):
    sidecar=Path(sidecar).resolve()
    try:
        import numpy
        import PIL
    except ImportError as exc:
        raise RuntimeError('Local table detection requires installed numpy and Pillow; use the bundled Python runtime. No dependencies are downloaded.') from exc
    source=json.loads(sidecar.read_text()); prepared=[]; bindings=[]
    for page in source['pages']:
        rawpath=Path(page['raw_ocr_path']); imagepath=Path(page['image_path'])
        if not rawpath.is_absolute():rawpath=sidecar.parent/rawpath
        if not imagepath.is_absolute():imagepath=sidecar.parent/imagepath
        raw=json.loads(rawpath.read_text())
        if raw.get('pdf_sha256')!=source['pdf_sha256'] or raw.get('page')!=page['page'] or sha(imagepath)!=raw['image_sha256']:
            raise ValueError('OCR/page/image source binding mismatch')
        if raw['bbox_coordinate_system']!='normalized_top_left_xywh':raise ValueError('Normalized OCR boxes required')
        bindings.append({'page':page['page'],'raw_path':str(rawpath),'raw_sha256':sha(rawpath),'image_path':str(imagepath),'image_sha256':sha(imagepath)})
        prepared.append((page,raw,imagepath))
    config={'sidecar_sha256':sha(sidecar),'implementation_sha256':sha(__file__),'threshold':threshold,'sources':bindings,'dependencies':{'numpy':numpy.__version__,'Pillow':PIL.__version__}}
    return source,prepared,config


def generate_compatible(sidecar, base_output, threshold=170):
    """Choose a compatible immutable cache; retain failed or stale evidence unchanged."""
    from law_crawler.derived_cache import select_output_directory
    _,_,config=input_configuration(sidecar,threshold)
    output,selection=select_output_directory(base_output,{'config':config},completed_statuses=('completed_needs_review',))
    receipt=generate(sidecar,output,threshold)
    if not receipt.get('reused'):
        save(output/'cache-selection.json',selection)
    return {**receipt,'output_directory':str(output),'cache_selection':selection}


def generate(sidecar, output, threshold=170):
    start=time.monotonic();sidecar=Path(sidecar).resolve();output=Path(output).resolve()
    source,prepared,config=input_configuration(sidecar,threshold)
    if output.exists():
        receipt=json.loads((output/'receipt.json').read_text())
        if receipt.get('status')!='completed_needs_review' or receipt['config']!=config:raise ValueError('Output exists with incompatible/failed inputs; use new output directory')
        for item in receipt['artifacts']:
            if sha(output/item['path'])!=item['sha256']:raise ValueError('Cached artifact changed')
        return {**receipt,'reused':True}
    output.mkdir(parents=True)
    try:
        result=copy.deepcopy(source);audits=[]
        visual_issues=copy.deepcopy(source.get('visual_structure_issues', []))
        for page,raw,imagepath in prepared:
            tables,duplicates,issue,rejected=prepare_page_tables(imagepath,raw['observations'],threshold)
            target=next(p for p in result['pages'] if p['page']==page['page'])
            if issue:
                # Keep page text exactly as supplied by the OCR sidecar. Observations and
                # image references remain available for independent graph/table transcription.
                visual_issues.append({'page':page['page'],**issue,
                                      'raw_ocr_path':str(Path(page['raw_ocr_path']) if Path(page['raw_ocr_path']).is_absolute() else sidecar.parent/Path(page['raw_ocr_path'])),
                                      'image_path':str(imagepath)})
                target['visual_structure_status']='unresolved_original_ocr_preserved'
                target['evidence']+='; VISUAL STRUCTURE UNRESOLVED: '+issue['reason']
                audits.append({'page':page['page'],'status':'isolated_original_ocr','tables':[],
                               'issue':issue,'rejected_candidates':rejected,'exact_duplicate_candidates_removed':duplicates,
                               'outside_observation_indices':list(range(len(raw['observations']))),
                               'outside_text':[o['text'] for o in raw['observations']]})
                continue
            insertion={};all_assigned=set()
            for i,table in enumerate(tables):
                ids=set(table['assigned_observation_indices'])
                all_assigned |= ids
                if ids:insertion[min(ids)]=table['html']
            text=[]
            for index,obs in enumerate(raw['observations']):
                if index in insertion:text.extend(['[Automatically detected ruled table; merged-cell structure and OCR require independent visual review]',insertion[index]])
                if index not in all_assigned:text.append(obs['text'])
            for table in tables:
                if not table['assigned_observation_indices']:text.extend(['[Detected empty table candidate; visual review required]',table['html']])
            target['text']='\n'.join(text)
            target['evidence']+='; automatic ruled-line table detection; see table-audit.json; independent review required'
            audits.append({'page':page['page'],'status':'detected_needs_independent_review',
                           'tables':tables,'exact_duplicate_candidates_removed':duplicates,
                           'outside_observation_indices':[i for i in range(len(raw['observations'])) if i not in all_assigned],
                           'outside_text':[o['text'] for i,o in enumerate(raw['observations']) if i not in all_assigned]})
        links=continuation_candidates(audits)
        for link in links:
            for page_number in [link['from_page'],link['to_page']]:
                target=next(p for p in result['pages'] if p['page']==page_number)
                target['text'] += '\n[Cross-page table candidate: page '+str(link['from_page'])+' table '+str(link['from_table'])+' -> page '+str(link['to_page'])+' table '+str(link['to_table'])+'; NOT MERGED; independent visual review required.]'
        result['visual_structure_issues']=visual_issues
        result['table_structure_status']='automatic_ruled_detection_with_unresolved_visual_structures' if visual_issues else 'automatic_ruled_detection_needs_independent_visual_review'
        result['review_status']='needs_independent_visual_review';result['automatic_table_config']=config
        save(output/'transcription.json',result);save(output/'table-audit.json',{'pages':audits,'config':config});save(output/'cross-page-candidates.json',{'candidates':links,'policy':'Never silently merge page tables; candidates require visual confirmation'})
        receipt={'status':'completed_needs_review','config':config,'duration_seconds':time.monotonic()-start,'table_count':sum(len(p['tables']) for p in audits),'page_count':len(audits),'unresolved_visual_structure_count':len(visual_issues),'cross_page_candidate_count':len(links),'model_calls':0,'reused':False,'transcription_path':str(output/'transcription.json'),'artifacts':[{'path':name,'sha256':sha(output/name)} for name in ['transcription.json','table-audit.json','cross-page-candidates.json']]}
        save(output/'receipt.json',receipt);return receipt
    except Exception as exc:
        save(output/'receipt.json',{'status':'failed','config':config,'duration_seconds':time.monotonic()-start,'error':str(exc)})
        raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sidecar',required=True);parser.add_argument('--output',required=True);parser.add_argument('--threshold',type=int,default=170)
    args=parser.parse_args(argv)
    if not 1<=args.threshold<=254:parser.error('--threshold must be 1..254')
    print(json.dumps(generate(args.sidecar,args.output,args.threshold),ensure_ascii=False,indent=2));return 0
