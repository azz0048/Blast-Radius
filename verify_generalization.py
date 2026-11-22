"""Audit freshly generated changes; no sample answers or precomputed mutants.

This is a local diagnostic, excluded from the runtime image. Results are never
used by the selector. Expensive cases remain outside this bounded audit.
"""
import argparse
import ast
import difflib
import gzip
import json
import os
from pathlib import Path
import time

from probe_runner import Prober
from refine import Refiner
from selector import ImpactIndex


def replace_node(source,node,value):
    lines=source.encode().splitlines(keepends=True)
    start=sum(map(len,lines[:node.lineno-1]))+node.col_offset
    end=sum(map(len,lines[:node.end_lineno-1]))+node.end_col_offset
    return (b''.join(lines)[:start]+value.encode()+b''.join(lines)[end:]).decode()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('checkout',type=Path)
    parser.add_argument('--output',type=Path,default=Path('generalization-validation.json'))
    args=parser.parse_args();project=Path(__file__).resolve().parent
    if hasattr(os,'sched_getaffinity'):
        os.sched_setaffinity(0,set(sorted(os.sched_getaffinity(0))[:2]))
    def read(name):
        with gzip.open(project/name,'rt') as f:return json.load(f)
    base=read('analysis.json.gz')
    tests=json.loads((project/'sample/tests.json').read_text())['tests']
    index=ImpactIndex(base,tests,read('usage.json.gz'),read('attributes.json.gz'),read('dictionary_reads.json.gz'))
    refiner=Refiner(args.checkout,index,tests);prober=Prober(args.checkout)
    audit_ids=[t['id'] for t in tests if t['seconds']<=.75]
    by_id={t['id']:t['number'] for t in tests};cases=[]
    # Independent feature flags exercise different dialect and optimizer inputs.
    fields=[('sqlglot/dialects/mysql.py','SAFE_DIVISION'),
            ('sqlglot/dialects/bigquery.py','FORCE_EARLY_ALIAS_REF_EXPANSION'),
            ('sqlglot/dialects/duckdb.py','SUPPORTS_FIXED_SIZE_ARRAYS'),
            ('sqlglot/dialects/postgres.py','TYPED_DIVISION')]
    for path,field in fields:
        source=base['sources'][path]
        declarations=[n for n in ast.walk(ast.parse(source))
                      if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==field for t in n.targets)
                      and isinstance(n.value,ast.Constant) and isinstance(n.value.value,bool)]
        assert len(declarations)==1
        node=declarations[0].value
        cases.append((path,field,replace_node(source,node,str(not node.value))))
    # Exercise function control flow without relying on particular failure labels.
    for path in ['sqlglot/optimizer/simplify.py','sqlglot/optimizer/normalize_identifiers.py']:
        source=base['sources'][path];coverage=base['lines'].get(path,{})
        choices=[n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.If)
                 and 2<=len(coverage.get(str(n.lineno),[]))<=80]
        if choices:
            node=min(choices,key=lambda n:n.lineno).test
            cases.append((path,'condition at line '+str(node.lineno),
                          replace_node(source,node,'not ('+ast.get_source_segment(source,node)+')')))
    reports=[];started=time.monotonic()
    for path,label,modified in cases:
        original=base['sources'][path]
        text=''.join(difflib.unified_diff(original.splitlines(keepends=True),modified.splitlines(keepends=True),
                                        fromfile='a/'+path,tofile='b/'+path))
        selected=index.select(text);selected=refiner.refine(text,selected)
        trial=prober.run({path:modified.encode()},audit_ids,seconds=24)
        observed={by_id[n] for n in trial.get('failed',[]) if n in by_id}
        missing=observed-selected
        report={'path':path,'change':label,'selected_count':len(selected),
                'audit_checked':len(trial.get('checked',[])),'audit_complete':trial.get('complete',False),
                'observed_failures':len(observed),'observed_missing':sorted(missing),
                'import_errors':trial.get('import_errors',[]),'setup_errors':trial.get('setup_errors',[])}
        reports.append(report);print(json.dumps(report),flush=True)
        args.output.write_text(json.dumps({'cases':reports,'elapsed_seconds':time.monotonic()-started,
             'note':'Bounded local mutations; excludes expensive tests and cannot establish hidden accuracy.'},indent=2)+'\n')
    return 1 if any(r['observed_missing'] for r in reports) else 0

if __name__=='__main__':raise SystemExit(main())
