"""Read-only HTML-table translation audit. Structural pass is never semantic approval."""
from __future__ import annotations
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables=[];self.table=None;self.cell=None;self.row_open=False;self.nested=0;self.errors=[]

    def error(self,message):
        self.errors.append({'line':self.getpos()[0],'reason':message})

    def handle_starttag(self,tag,attrs):
        if tag=='table':
            if self.table is not None:
                self.error('Nested table is unsupported and may indicate chunk-split translation');self.nested+=1;return
            self.table={'rows':0,'cells':[],'slots':{}};return
        if self.nested:return
        if self.table is None:
            if tag in ('tr','td','th'):self.error('Table row/cell outside a table')
            return
        if tag=='tr':
            if self.row_open or self.cell is not None:self.error('New row before previous row/cell was closed')
            self.row_open=True;self.table['rows']+=1
        elif tag in ('td','th'):
            if not self.row_open or self.cell is not None:
                self.error('Cell outside row or previous cell was not closed');return
            values=dict(attrs)
            try:rs=int(values.get('rowspan','1'));cs=int(values.get('colspan','1'))
            except ValueError:self.error('Noninteger rowspan/colspan');rs=cs=1
            if not 1<=rs<=1000 or not 1<=cs<=1000 or rs*cs>10000:
                self.error('Invalid or excessive rowspan/colspan');rs=cs=1
            r=self.table['rows']-1;c=0
            while (r,c) in self.table['slots']:c+=1
            self.cell={'row':r,'column':c,'rowspan':rs,'colspan':cs,'text':''}
            for row in range(r,r+rs):
                for col in range(c,c+cs):
                    if (row,col) in self.table['slots']:self.error('Overlapping rowspan/colspan slots')
                    self.table['slots'][(row,col)]=len(self.table['cells'])
        elif tag=='br' and self.cell is not None:self.cell['text']+='\n'

    def handle_data(self,data):
        if self.cell is not None and not self.nested:self.cell['text']+=data

    def handle_endtag(self,tag):
        if self.nested:
            if tag=='table':self.nested-=1
            return
        if tag in ('td','th'):
            if self.cell is None:self.error('Closing cell without an open cell')
            else:self.table['cells'].append(self.cell);self.cell=None
        elif tag=='tr':
            if self.table is None or not self.row_open:self.error('Closing row without an open row')
            if self.cell is not None:self.error('Unclosed cell at row end')
            self.row_open=False
        elif tag=='table':
            if self.table is None:self.error('Closing table without an open table');return
            if self.cell is not None or self.row_open:self.error('Unclosed row/cell at table end')
            slots=self.table.pop('slots');columns=max((c for r,c in slots),default=-1)+1
            self.table['columns']=columns
            expected={(r,c) for r in range(self.table['rows']) for c in range(columns)}
            holes=expected-set(slots);excess=set(slots)-expected
            if holes:self.error('Missing cell slots: '+str(sorted(holes)[:20]))
            if excess:self.error('Rowspan extends beyond table rows: '+str(sorted(excess)[:20]))
            if not columns or not self.table['rows']:self.error('Empty table')
            self.table['missing_slots']=len(holes);self.table['excess_slots']=len(excess)
            self.tables.append(self.table);self.table=None;self.cell=None;self.row_open=False

    def finish(self):
        self.close()
        if self.table is not None or self.nested:self.error('Unclosed table at end of text')
        return {'tables':self.tables,'errors':self.errors}


def numeric_tokens(text):
    # Preserve exact punctuation: 9.450 -> 9.45 is an actual audit flag.
    # Units may be translated; attached asterisk count remains independently checked.
    return [(m.group(1),len(m.group(2))) for m in re.finditer(r'(?<![0-9A-Za-z_])([-+]?\d+(?:[.,]\d+)*)(?:/[^\s*<]+)?(\*{0,12})',text)]


def currencies(text):
    return re.findall(r'(?i)(?<![0-9A-Za-z_])(?:IDR|USD|EUR|GBP|CNY|RMB|Rp)(?![0-9A-Za-z_])|[$€£¥]',text)


def parse(text):
    parser=TableParser();parser.feed(text);return parser.finish()


def compare(source,targets):
    parsed={'source':parse(source),**{language:parse(text) for language,text in targets.items()}}
    flags=[]
    for language,result in parsed.items():
        flags.extend({'language':language,'kind':'invalid_table_html',**error} for error in result['errors'])
    reference=parsed['source']['tables']
    for language in targets:
        actual=parsed[language]['tables']
        if len(actual)!=len(reference):flags.append({'language':language,'kind':'table_count_mismatch','expected':len(reference),'actual':len(actual)})
        for number,(a,b) in enumerate(zip(reference,actual)):
            shape=lambda t:(t['rows'],t['columns'],[(c['row'],c['column'],c['rowspan'],c['colspan']) for c in t['cells']])
            if shape(a)!=shape(b):
                flags.append({'language':language,'table':number,'kind':'table_shape_or_span_mismatch','expected':shape(a),'actual':shape(b)})
            source_cells={(c['row'],c['column']):c for c in a['cells']}
            translated_cells={(c['row'],c['column']):c for c in b['cells']}
            for anchor in source_cells.keys() & translated_cells.keys():
                st,tt=source_cells[anchor]['text'],translated_cells[anchor]['text']
                if numeric_tokens(st)!=numeric_tokens(tt):flags.append({'language':language,'table':number,'anchor':anchor,'kind':'cell_numeric_or_asterisk_mismatch','expected':numeric_tokens(st),'actual':numeric_tokens(tt)})
                if [x.upper() for x in currencies(st)]!=[x.upper() for x in currencies(tt)]:flags.append({'language':language,'table':number,'anchor':anchor,'kind':'cell_currency_marker_mismatch','expected':currencies(st),'actual':currencies(tt)})
    if not reference:flags.append({'language':'source','kind':'no_source_html_tables','reason':'No HTML tables found; this checker does not infer tables from plain text or Markdown pipes'})
    return {'status':'requires_review' if flags else 'pass_structure_only','flags':flags,'parsed':parsed,
            'scope':'HTML table shape, spans, occupied slots, exact per-cell numeric punctuation, attached asterisks and currency markers only. Does not certify translation meaning, units, missing dash placeholders, footnote wording, source correctness or legal validity.'}


def audit(source,zh,en,output):
    started=time.monotonic();paths={key:Path(path).resolve() for key,path in [('source',source),('zh',zh),('en',en)]};output=Path(output).resolve()
    bindings={key:{'path':str(path),'sha256':sha(path)} for key,path in paths.items()}
    config={'inputs':bindings,'implementation_sha256':sha(__file__)}
    if output.exists():
        old=json.loads(output.read_text())
        if old['config']!=config:raise ValueError('Audit output exists with different inputs or implementation; choose a new output JSON')
        check={k:v for k,v in old.items() if k!='audit_sha256'}
        if hashlib.sha256(json.dumps(check,sort_keys=True,ensure_ascii=False).encode()).hexdigest()!=old['audit_sha256']:raise ValueError('Existing audit integrity mismatch')
        return {**old,'reused':True}
    result=compare(paths['source'].read_text(),{key:paths[key].read_text() for key in ('zh','en')})
    result.update(config=config,duration_seconds=time.monotonic()-started,reused=False,model_calls=0)
    result['audit_sha256']=hashlib.sha256(json.dumps(result,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for field in ('source','zh','en','output'):parser.add_argument('--'+field,required=True)
    args=parser.parse_args(argv)
    result=audit(args.source,args.zh,args.en,args.output)
    print(json.dumps({'status':result['status'],'flags':len(result['flags']),'output':str(Path(args.output).resolve()),'reused':result['reused']},ensure_ascii=False))
    return 0 if result['status']=='pass_structure_only' else 2
