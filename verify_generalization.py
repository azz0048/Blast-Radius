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
from comparison_profiles import site_key,equality_is_identity
from dictionary_impact import merge_maps,pure_value


def replace_node(source,node,value):
    lines=source.encode().splitlines(keepends=True)
    start=sum(map(len,lines[:node.lineno-1]))+node.col_offset
    end=sum(map(len,lines[:node.end_lineno-1]))+node.end_col_offset
    return (b''.join(lines)[:start]+value.encode()+b''.join(lines)[end:]).decode()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('checkout',type=Path)
    parser.add_argument('--output',type=Path,default=Path('generalization-validation.json'))
    parser.add_argument('--only-none',action='store_true')
    args=parser.parse_args();project=Path(__file__).resolve().parent
    if hasattr(os,'sched_getaffinity'):
        os.sched_setaffinity(0,set(sorted(os.sched_getaffinity(0))[:2]))
    def read(name):
        with gzip.open(project/name,'rt') as f:return json.load(f)
    base=read('analysis.json.gz')
    tests=json.loads((project/'sample/tests.json').read_text())['tests']
    dictionary=merge_maps(read('dictionary_reads.json.gz'),read('key_flow.json.gz'))
    comparisons=read('comparisons.json.gz')
    traits=json.loads((project/'none_traits.json').read_text())
    comparisons.update({k:traits[k] for k in ('safe_eq_none_types','safe_ne_none_types','protected_methods')})
    index=ImpactIndex(base,tests,read('usage.json.gz'),read('attributes.json.gz'),dictionary,comparisons,
                     read('values.json.gz'),read('expressions.json.gz'),read('branch_flow.json.gz'))
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
    # New comparison cases include both proven type categories and unsafe types.
    swaps={ast.Is:ast.Eq,ast.IsNot:ast.NotEq,ast.Eq:ast.Is,ast.NotEq:ast.IsNot}
    for path,want_safe in [('sqlglot/optimizer/scope.py',True),
                           ('sqlglot/expressions/core.py',True),
                           ('sqlglot/optimizer/normalize_identifiers.py',False)]:
        source=base['sources'][path];choices=[]
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node,ast.Compare) or len(node.ops)!=1 or type(node.ops[0]) not in swaps:continue
            rows=comparisons['sites'].get(site_key(path,node),{})
            if not rows:continue
            safe=all(equality_is_identity(*comparisons['patterns'][int(i)]) for i in rows)
            if safe==want_safe:choices.append(node)
        if choices:
            node=min(choices,key=lambda n:n.lineno)
            replacement=ast.Compare(left=node.left,ops=[swaps[type(node.ops[0])]()],comparators=node.comparators)
            cases.append((path,'comparison at line '+str(node.lineno),replace_node(source,node,ast.unparse(replacement))))
    references=set(dictionary.get('pure_references',()))
    for path,field in [('sqlglot/dialects/postgres.py','KEYWORDS'),
                       ('sqlglot/dialects/trino.py','KEYWORDS'),
                       ('sqlglot/parser.py','FUNCTIONS'),
                       ('sqlglot/parsers/postgres.py','FUNCTIONS')]:
        source=base['sources'][path];choices=[]
        for cls in [n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.ClassDef)]:
            for declaration in cls.body:
                if not isinstance(declaration,(ast.Assign,ast.AnnAssign)) or not isinstance(declaration.value,ast.Dict):continue
                targets=declaration.targets if isinstance(declaration,ast.Assign) else [declaration.target]
                if not any(isinstance(t,ast.Name) and t.id==field for t in targets):continue
                for key,value in zip(declaration.value.keys,declaration.value.values):
                    if not isinstance(key,ast.Constant) or not isinstance(key.value,str) or not pure_value(value,references):continue
                    if field=='KEYWORDS':
                        if not isinstance(value,ast.Attribute) or value.attr=='SELECT':continue
                        replacement=ast.unparse(ast.Attribute(value=value.value,attr='SELECT',ctx=ast.Load()))
                    else:replacement='lambda *args, **kwargs: None'
                    choices.append((value,replacement))
        if choices:
            node,replacement=min(choices,key=lambda pair:pair[0].lineno)
            cases.append((path,field+' value at line '+str(node.lineno),replace_node(source,node,replacement)))
    refined=index.comparisons
    eq_types=set(traits['safe_eq_none_types'])
    for path in ['sqlglot/optimizer/qualify_columns.py','sqlglot/optimizer/scope.py']:
        source=base['sources'][path];choices=[]
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node,ast.Compare) or len(node.ops)!=1 or not isinstance(node.ops[0],(ast.Is,ast.Eq)):continue
            if not any(isinstance(x,ast.Constant) and x.value is None for x in [node.left,*node.comparators]):continue
            rows=comparisons['sites'].get(site_key(path,node),{})
            if not rows:continue
            new_proof=any(tag.startswith('unknown:') and tag[len('unknown:'):] in eq_types for row in rows for tag in comparisons['patterns'][int(row)])
            if new_proof and all(equality_is_identity(*refined.patterns[int(row)]) for row in rows):choices.append(node)
        if choices:
            node=min(choices,key=lambda n:n.lineno)
            replacement=ast.Compare(left=node.left,ops=[swaps[type(node.ops[0])]()],comparators=node.comparators)
            cases.append((path,'None proof at line '+str(node.lineno),replace_node(source,node,ast.unparse(replacement))))
    path='sqlglot/expressions/core.py';source=base['sources'][path]
    methods=[n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.FunctionDef) and n.name=='__eq__']
    for fn in methods[:1]:
        choices=[n for n in ast.walk(fn) if isinstance(n,ast.Compare) and len(n.ops)==1 and isinstance(n.ops[0],ast.Is) and isinstance(n.left,ast.Name) and isinstance(n.comparators[0],ast.Name)]
        if choices:
            node=choices[0];replacement=ast.Compare(left=node.left,ops=[ast.Eq()],comparators=node.comparators)
            cases.append((path,'None proof dependency changed at line '+str(node.lineno),replace_node(source,node,ast.unparse(replacement))))
    if args.only_none:cases=[case for case in cases if case[1].startswith('None proof')]
    reports=[];started=time.monotonic()
    planned=[]
    for path,label,modified in cases:
        original=base['sources'][path]
        text=''.join(difflib.unified_diff(original.splitlines(keepends=True),modified.splitlines(keepends=True),
                                        fromfile='a/'+path,tofile='b/'+path))
        selected=index.select(text);selected=refiner.refine(text,selected)
        planned.append((path,label,modified,selected))
    for path,label,modified,selected in planned:
        trial=prober.run({path:modified.encode()},audit_ids,seconds=24)
        observed={by_id[n] for n in trial.get('failed',[]) if n in by_id}
        missing=observed-selected
        # Collection and fixture errors are not successful case executions.
        # Keep an audit explicitly inconclusive rather than silently calling it
        # zero-miss when a broad error prevented the test methods from running.
        broad_error=bool(trial.get('import_errors') or trial.get('setup_errors'))
        inconclusive=broad_error and selected!=index.all
        report={'path':path,'change':label,'selected_count':len(selected),
                'audit_checked':len(trial.get('checked',[])),'audit_complete':trial.get('complete',False),
                'observed_failures':len(observed),'observed_missing':sorted(missing),
                'import_errors':trial.get('import_errors',[]),'setup_errors':trial.get('setup_errors',[]),
                'inconclusive_collection':inconclusive}
        reports.append(report);print(json.dumps(report),flush=True)
        args.output.write_text(json.dumps({'cases':reports,'elapsed_seconds':time.monotonic()-started,
             'note':'Bounded local mutations; excludes expensive tests and cannot establish hidden accuracy.'},indent=2)+'\n')
    return 1 if any(r['observed_missing'] or r['inconclusive_collection'] for r in reports) else 0

if __name__=='__main__':raise SystemExit(main())
