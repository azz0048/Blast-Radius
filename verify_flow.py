"""Generate independent Boolean mutations and audit every newly excluded test.

Planning uses baseline maps. Test execution runs in a separate process that
doesn't hold those maps. No plan or mutation answers enter the runtime image.
"""
import argparse
import ast
import copy
import difflib
import gzip
import json
from pathlib import Path
import time


def plan(project,destination,limit=6):
    from branch_flow import shape,simulate,boolean_root,site
    from selector import schedule
    from verify_predicates import load_index
    from verify_generalization import replace_node
    tests=json.loads((project/'sample/tests.json').read_text())['tests']
    before,after=load_index(project,tests,False),load_index(project,tests)
    data=after.flow.data
    by_number={t['number']:t for t in tests};durations={t['number']:round(t['seconds']*1000) for t in tests}
    def cost(selected):
        workers=schedule(selected,durations)
        return (max((sum(durations[n] for n in w) for w in workers),default=0)+2000*len(workers))/1000
    candidates=[];attempts=0
    for path,source in after.sources.items():
        if not path.startswith('sqlglot/'):continue
        root=ast.parse(source);parents={id(child):parent for parent in ast.walk(root) for child in ast.iter_child_nodes(parent)}
        count=0
        for node in ast.walk(root):
            if not boolean_root(node):continue
            token=site(path,node)
            contexts=data['sites'].get(token)
            if not contexts:continue
            line=node.lineno
            if any(m['path']==path and m['start']<=line<=m['end'] for m in data['accessor_dependencies']):continue
            modified_node=copy.deepcopy(node)
            boolean=next((n for n in ast.walk(modified_node) if isinstance(n,ast.BoolOp)),None)
            if boolean is None:continue
            boolean.op=ast.Or() if isinstance(boolean.op,ast.And) else ast.And()
            original_shape,modified_shape=shape(node,[]),shape(modified_node,[])
            safe=False
            for context,profile in contexts.items():
                if int(context)<0 or profile['unsafe']:continue
                patterns=[data['patterns'][n] for n in profile['patterns']]
                if patterns and all(simulate(original_shape,p) is not None and simulate(original_shape,p)==simulate(modified_shape,p) for p in patterns):
                    safe=True;break
            if not safe:continue
            attempts+=1;count+=1
            modified_source=replace_node(source,node,ast.unparse(modified_node))
            text=''.join(difflib.unified_diff(source.splitlines(keepends=True),modified_source.splitlines(keepends=True),fromfile='a/'+path,tofile='b/'+path))
            old_selection,new_selection=before.select(text),after.select(text)
            removed=old_selection-new_selection
            if removed:
                modes={data['patterns'][n][0] for p in contexts.values() for n in p['patterns']}
                parent=parents.get(id(node))
                candidates.append({'path':path,'line':line,'parent':type(parent).__name__,
                    'mode':sorted(modes),'source':modified_source,
                    'selected_before':len(old_selection),'selected_after':len(new_selection),
                    'simulated_saving':cost(old_selection)-cost(new_selection),
                    'test_ids':[by_number[n]['id'] for n in sorted(removed)]})
            if count>=3:break
        if attempts>=100:break
    candidates.sort(key=lambda c:(-c['simulated_saving'],-len(c['test_ids']),c['path'],c['line']))
    chosen=[];files=set()
    for mode in ('truth','return'):
        for c in candidates:
            if mode in c['mode'] and c['path'] not in files:
                chosen.append(c);files.add(c['path']);break
    for c in candidates:
        if len(chosen)>=limit:break
        if c['path'] not in files:chosen.append(c);files.add(c['path'])
    destination.write_text(json.dumps({'cases':chosen,'provenance':'Fresh operator flips chosen by baseline reachability and predicted savings; no public-sample or hidden failure labels.'}))
    print(json.dumps({'planned':len(chosen),'candidates':len(candidates),'newly_removed_tests':sum(len(c['test_ids']) for c in chosen),'potential_simulated_saving':sum(c['simulated_saving'] for c in chosen)}),flush=True)
    for c in chosen:print(json.dumps({k:v for k,v in c.items() if k not in {'source','test_ids'}}|{'tests_to_audit':len(c['test_ids'])}),flush=True)
    return int(not chosen)


def run_plan(checkout,plan_path,output,offset,count):
    from probe_runner import Prober
    cases=json.loads(plan_path.read_text())['cases'][offset:offset+count]
    prober=Prober(checkout);prober.prepare()
    import duckdb
    connect=duckdb.connect
    def audit_connect(*a,**kw):
        config=dict(kw.pop('config',{}) or {});config.update(threads='1',memory_limit='6GB')
        return connect(*a,config=config,**kw)
    duckdb.connect=audit_connect
    reports=[]
    for c in cases:
        started=time.monotonic()
        result=prober.run({c['path']:c['source'].encode()},c['test_ids'],seconds=165,case_timeout=140,max_rss_mb=3072)
        report={k:v for k,v in c.items() if k not in {'source','test_ids'}}
        report.update({'newly_removed_tests':len(c['test_ids']),'checked':len(result.get('checked',())),
            'complete':result.get('complete',False),'removed_failures':result.get('failed',[]),
            'import_errors':result.get('import_errors',[]),'setup_errors':result.get('setup_errors',[]),
            'error':result.get('error'),'wall_seconds':time.monotonic()-started})
        reports.append(report);print(json.dumps(report),flush=True)
        output.write_text(json.dumps({'cases':reports,'note':'Fresh local mutations. Every test newly excluded by V6 is actually executed, including expensive cases.'},indent=2)+'\n')
    return int(not reports or any(not r['complete'] or r['removed_failures'] or r['import_errors'] or r['setup_errors'] for r in reports))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('checkout',type=Path)
    parser.add_argument('--plan',type=Path,default=Path('/tmp/blast-flow-plan.json'))
    parser.add_argument('--plan-only',action='store_true');parser.add_argument('--run-plan',action='store_true')
    parser.add_argument('--offset',type=int,default=0);parser.add_argument('--count',type=int,default=6)
    parser.add_argument('--output',type=Path,default=Path('flow-validation.json'))
    args=parser.parse_args();project=Path(__file__).resolve().parent
    if not args.run_plan:
        result=plan(project,args.plan,args.count)
        if result or args.plan_only:return result
    return run_plan(args.checkout,args.plan,args.output,args.offset,args.count)


if __name__=='__main__':raise SystemExit(main())
