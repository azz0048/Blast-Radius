"""Local held-out mutation audit, excluded from the runtime Docker image.

Generate changes from baseline source and rerun every test removed by the new
metadata. This diagnostic never supplies answers to the runtime selector.
"""
import argparse
import ast
import difflib
import copy
import gzip
import json
import os
from pathlib import Path
import resource
import time
from probe_runner import Prober
from selector import ImpactIndex, schedule
from dictionary_impact import merge_maps
from value_profiles import comparison
from verify_generalization import replace_node


def load_index(project, tests, new=True):
    def read(name):
        with gzip.open(project/name,'rt') as handle:return json.load(handle)
    base=read('analysis.json.gz')
    comparison_data=read('comparisons.json.gz')
    traits=json.loads((project/'none_traits.json').read_text())
    comparison_data.update({k:traits[k] for k in ('safe_eq_none_types','safe_ne_none_types','protected_methods')})
    return ImpactIndex(base,tests,read('usage.json.gz'),read('attributes.json.gz'),
                       merge_maps(read('dictionary_reads.json.gz'),read('key_flow.json.gz')),
                       comparison_data,read('values.json.gz'),read('expressions.json.gz'),
                       read('branch_flow.json.gz') if new else None)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('checkout',type=Path)
    parser.add_argument('--limit',type=int,default=12)
    parser.add_argument('--candidate-limit',type=int,default=180)
    parser.add_argument('--output',type=Path,default=Path('predicate-validation.json'))
    args=parser.parse_args();project=Path(__file__).resolve().parent
    tests=json.loads((project/'sample/tests.json').read_text())['tests']
    old,new=load_index(project,tests,False),load_index(project,tests)
    by_number={t['number']:t for t in tests}
    duration={t['number']:round(t['seconds']*1000) for t in tests}
    def cost(selected):
        workers=schedule(selected,duration)
        return max((sum(duration[n] for n in w) for w in workers),default=0)+2000*len(workers)
    candidates=[];seen=set();attempts=0
    for path,source in new.sources.items():
        if attempts>=args.candidate_limit:break
        if not path.startswith('sqlglot/'):continue
        tree=ast.parse(source)
        file_counts={};functions=[n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))]
        for node in ast.walk(tree):
            if attempts>=args.candidate_limit:break
            if not any(n.lineno<getattr(node,'lineno',-1)<=n.end_lineno for n in functions):continue
            options=[]
            if isinstance(node,ast.BoolOp):
                replacement=ast.BoolOp(op=ast.Or() if isinstance(node.op,ast.And) else ast.And(),values=node.values)
                options.append(('boolean',replacement))
            if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult)):
                op=ast.Sub() if isinstance(node.op,ast.Add) else ast.Add()
                options.append(('binary',ast.BinOp(left=node.left,op=op,right=node.right)))
            if isinstance(node,ast.Call):
                choices=[(False,i,a) for i,a in enumerate(node.args) if isinstance(a,ast.Constant) and type(a.value) in (bool,int)]
                choices += [(True,i,k.value) for i,k in enumerate(node.keywords) if isinstance(k.value,ast.Constant) and type(k.value.value) in (bool,int)]
                if choices:
                    keyword,i,constant=choices[0];replacement=copy.deepcopy(node)
                    value=ast.Constant(not constant.value if type(constant.value) is bool else constant.value+1)
                    if keyword:replacement.keywords[i].value=value
                    else:replacement.args[i]=value
                    options.append(('call_literal',replacement))
            shape=comparison(node)
            if shape is not None:
                swap={ast.Lt:ast.LtE,ast.LtE:ast.Lt,ast.Gt:ast.GtE,ast.GtE:ast.Gt}
                if type(node.ops[0]) in swap:
                    options.append(('boundary',ast.Compare(left=node.left,ops=[swap[type(node.ops[0])]()],comparators=node.comparators)))
                if type(shape[1]) is int:
                    literal=ast.Constant(shape[1]+1)
                    options.append(('constant',ast.Compare(left=literal if shape[0]==0 else node.left,
                        ops=node.ops,comparators=[literal if shape[0]==1 else node.comparators[0]])))
            for family,replacement in options:
                if attempts>=args.candidate_limit:break
                if file_counts.get(family,0)>=2:continue
                file_counts[family]=file_counts.get(family,0)+1
                attempts+=1
                modified=replace_node(source,node,ast.unparse(replacement))
                text=''.join(difflib.unified_diff(source.splitlines(keepends=True),modified.splitlines(keepends=True),fromfile='a/'+path,tofile='b/'+path))
                before,after=old.select(text),new.select(text)
                removed=before-after
                if not removed:continue
                token=(family,path,node.lineno)
                if token in seen:continue
                seen.add(token)
                candidates.append((cost(before)-cost(after),family,path,node.lineno,modified,before,after,removed))
    # Exercise the largest cost reductions, while covering separate files and
    # all supported edit families. No failure labels influence this selection.
    candidates.sort(key=lambda c:(-c[0],c[2],c[3]))
    chosen=[];files=set();families=set()
    for family in ('boolean','boundary','constant','binary','call_literal'):
        for c in candidates:
            if c[1]==family:
                chosen.append(c);files.add(c[2]);families.add(family);break
    for c in candidates:
        if len(chosen)>=args.limit:break
        if c[2] not in files:chosen.append(c);files.add(c[2])
    prober=Prober(args.checkout);prober.prepare()
    # This is an offline correctness audit. Avoid false DuckDB OOM errors from
    # the deliberately tighter online probing budget.
    import duckdb
    connect=duckdb.connect
    def audit_connect(*a,**kw):
        config=dict(kw.pop('config',{}) or {});config.update(threads='1',memory_limit='6GB')
        return connect(*a,config=config,**kw)
    duckdb.connect=audit_connect
    reports=[];started=time.monotonic()
    for saving,family,path,line,modified,before,after,removed in chosen:
        ids=[by_number[n]['id'] for n in sorted(removed)]
        start=time.monotonic()
        trial=prober.run({path:modified.encode()},ids,seconds=160,case_timeout=120,max_rss_mb=3072)
        failures=set(trial.get('failed',()))
        report={'family':family,'path':path,'line':line,'selected_before':len(before),'selected_after':len(after),
                'newly_removed_tests':len(removed),'checked':len(trial.get('checked',())),
                'complete':trial.get('complete',False),'removed_failures':sorted(failures),
                'import_errors':trial.get('import_errors',[]),'setup_errors':trial.get('setup_errors',[]),
                'potential_simulated_saving':saving/1000,'wall_seconds':time.monotonic()-start}
        reports.append(report);print(json.dumps(report),flush=True)
        args.output.write_text(json.dumps({'cases':reports,'elapsed_seconds':time.monotonic()-started,
            'note':'Fresh local mutations, not the public sample or hidden commits. Every newly removed test is executed.'},indent=2)+'\n')
    return int(not reports or any(not r['complete'] or r['removed_failures'] or r['import_errors'] or r['setup_errors'] for r in reports))


if __name__=='__main__':raise SystemExit(main())
