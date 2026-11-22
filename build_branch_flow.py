"""Collect baseline Boolean inputs and original effect order; never mutations."""
import argparse
import ast
import concurrent.futures
import gzip
import importlib.abc
import importlib.machinery
import json
import os
from pathlib import Path
import pickle
import sys
import time
from branch_flow import START,LEAF,END,InstrumentFlow
from branch_collect import Recorder
from pure_inputs import ACCESSOR_DEPENDENCIES


def main():
    parser=argparse.ArgumentParser();parser.add_argument('checkout',type=Path)
    parser.add_argument('--output',type=Path,default=Path('branch_flow.json.gz'))
    args=parser.parse_args();root=args.checkout.resolve();destination=args.output.resolve()
    with gzip.open(Path(__file__).with_name('analysis.json.gz'),'rt') as f:base=json.load(f)
    names=base['collection'];numbers={name:i for i,name in enumerate(names)};groups={}
    for name in names:groups.setdefault(name.rsplit('::',1)[0],set()).add(numbers[name])
    active=[''];phase=[''];registry={};sites={};patterns=[];pattern_ids={};outcomes={};collected=[]
    def emit(token,context,pattern):
        name,stage=context
        contexts=groups[name.rsplit('::',1)[0]] if stage=='setup' and name in numbers else [numbers.get(name,-1)]
        number=None
        if pattern is not None:
            text=json.dumps(pattern,separators=(',',':'))
            if text not in pattern_ids:pattern_ids[text]=len(patterns);patterns.append(pattern)
            number=pattern_ids[text]
        for i in contexts:
            profile=sites.setdefault(token,{}).setdefault(i,{'unsafe':False,'patterns':set()})
            if pattern is None:profile['unsafe']=True
            else:profile['patterns'].add(number)
    recorder=Recorder(registry,lambda:(active[0],phase[0]),emit,cap=256)
    class Loader(importlib.machinery.SourceFileLoader):
        def get_code(self,fullname):
            source=self.get_data(self.path)
            if any(n.encode() in source for n in (START,LEAF,END)):raise ValueError('Instrumentation name collision')
            tree=InstrumentFlow(Path(self.path).relative_to(root).as_posix(),registry).visit(ast.parse(source,filename=self.path,type_comments=True))
            ast.fix_missing_locations(tree);return compile(tree,self.path,'exec',dont_inherit=True)
        def exec_module(self,module):
            module.__dict__.update({START:recorder.start,LEAF:recorder.leaf,END:recorder.end})
            super().exec_module(module)
    class Finder(importlib.abc.MetaPathFinder):
        def find_spec(self,fullname,path=None,target=None):
            if not (fullname=='sqlglot' or fullname.startswith(('sqlglot.','tests.')) or fullname=='tests'):return None
            spec=importlib.machinery.PathFinder.find_spec(fullname,path)
            if spec and spec.origin and spec.origin.endswith('.py') and Path(spec.origin).is_relative_to(root):spec.loader=Loader(fullname,spec.origin)
            return spec
    class Pool(concurrent.futures.ThreadPoolExecutor):
        def submit(self,fn,/,*args,**kw):
            fn,args,kw=pickle.loads(pickle.dumps((fn,args,kw)))
            return super().submit(lambda:pickle.loads(pickle.dumps(fn(*args,**kw))))
    def pool(*args,**kw):
        kw.pop('mp_context',None);kw.pop('max_tasks_per_child',None);kw['max_workers']=1;return Pool(**kw)
    concurrent.futures.ProcessPoolExecutor=pool
    os.environ['SKIP_INTEGRATION']='0';os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    os.chdir(root);sys.path.insert(0,str(root));sys.meta_path.insert(0,Finder())
    import pytest
    class Plugin:
        def pytest_collection_modifyitems(self,items):collected.extend(i.nodeid for i in items)
        def pytest_runtest_setup(self,item):recorder.flush();active[0]=item.nodeid;phase[0]='setup'
        def pytest_runtest_call(self,item):recorder.flush();active[0]=item.nodeid;phase[0]='call'
        def pytest_runtest_teardown(self,item):recorder.flush();phase[0]='teardown'
        def pytest_runtest_logreport(self,report):
            if report.failed:outcomes[report.nodeid]='failed'
            elif report.when=='call':outcomes.setdefault(report.nodeid,report.outcome)
    start=time.monotonic()
    status=pytest.main(['tests','-q','--tb=short','-p','pytest_timeout','--timeout=600'],plugins=[Plugin()])
    recorder.flush()
    payload={'pinned_commit':base['pinned_commit'],'flow_format':1,
        'provenance':'Only original unchanged baseline Boolean inputs, pure read snapshots and original call order. No patches, alternative operators or failure answers.',
        'collection':names,'complete_baseline':status==0 and set(collected)==set(names),
        'baseline_outcomes':outcomes,'elapsed_seconds':time.monotonic()-start,'observation_cap':256,
        'patterns':patterns,
        'accessor_dependencies':[{'path':Path(p).relative_to(root).as_posix(),'start':lo,'end':hi}
            for p,lo,hi in sorted(ACCESSOR_DEPENDENCIES) if Path(p).is_relative_to(root)],
        'sites':{token:{str(i):{'unsafe':profile['unsafe'],'patterns':sorted(profile['patterns'])}
            for i,profile in sites.get(token,{}).items()} for token in registry}}
    temporary=destination.with_name(destination.name+'.new')
    with gzip.open(temporary,'wt',compresslevel=9) as f:json.dump(payload,f,separators=(',',':'))
    temporary.replace(destination if payload['complete_baseline'] else destination.with_name(destination.name+'.failed'))
    print(f'Baseline flow: {len(registry)} sites, {len(patterns)} input patterns, complete={payload["complete_baseline"]}',file=sys.stderr)
    return int(status) or int(not payload['complete_baseline'])


if __name__=='__main__':raise SystemExit(main())
