"""Collect precise expression reachability on the unchanged pinned revision."""
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
from expression_profiles import FUNCTION_NAME, InstrumentEntries


def main():
    parser=argparse.ArgumentParser();parser.add_argument('checkout',type=Path)
    parser.add_argument('--output',type=Path,default=Path('expressions.json.gz'))
    args=parser.parse_args();root=args.checkout.resolve();dest=args.output.resolve()
    with gzip.open(Path(__file__).with_name('analysis.json.gz'),'rt') as f:base=json.load(f)
    names=base['collection'];numbers={name:i for i,name in enumerate(names)}
    groups={}
    for name in names:groups.setdefault(name.rsplit('::',1)[0],set()).add(numbers[name])
    active=[''];phase=[''];sites={};registered=set();outcomes={};collected=[];seen=set()
    def observed(token,value):
        if token in seen:return value
        seen.add(token)
        name=active[0]
        contexts=groups[name.rsplit('::',1)[0]] if phase[0]=='setup' and name in numbers else [numbers.get(name,-1)]
        sites.setdefault(token,set()).update(contexts)
        return value
    class Loader(importlib.machinery.SourceFileLoader):
        def get_code(self,fullname):
            source=self.get_data(self.path)
            if FUNCTION_NAME.encode() in source:raise ValueError('Instrumentation collision')
            tree=InstrumentEntries(Path(self.path).relative_to(root).as_posix(),registered).visit(ast.parse(source,filename=self.path,type_comments=True))
            ast.fix_missing_locations(tree);return compile(tree,self.path,'exec',dont_inherit=True)
        def exec_module(self,module):
            module.__dict__[FUNCTION_NAME]=observed;super().exec_module(module)
    class Finder(importlib.abc.MetaPathFinder):
        def find_spec(self,fullname,path=None,target=None):
            if not (fullname=='sqlglot' or fullname.startswith(('sqlglot.','tests.')) or fullname=='tests'):return None
            spec=importlib.machinery.PathFinder.find_spec(fullname,path)
            if spec and spec.origin and spec.origin.endswith('.py') and Path(spec.origin).is_relative_to(root):spec.loader=Loader(fullname,spec.origin)
            return spec
    class Pool(concurrent.futures.ThreadPoolExecutor):
        def submit(self,fn,/,*args,**kwargs):
            fn,args,kwargs=pickle.loads(pickle.dumps((fn,args,kwargs)))
            return super().submit(lambda:pickle.loads(pickle.dumps(fn(*args,**kwargs))))
    def pool(*args,**kwargs):
        kwargs.pop('mp_context',None);kwargs.pop('max_tasks_per_child',None);kwargs['max_workers']=1;return Pool(**kwargs)
    concurrent.futures.ProcessPoolExecutor=pool
    os.environ['SKIP_INTEGRATION']='0';os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    os.chdir(root);sys.path.insert(0,str(root));sys.meta_path.insert(0,Finder())
    import pytest
    class Plugin:
        def pytest_collection_modifyitems(self,items):collected.extend(i.nodeid for i in items)
        def pytest_runtest_setup(self,item):active[0]=item.nodeid;phase[0]='setup';seen.clear()
        def pytest_runtest_call(self,item):active[0]=item.nodeid;phase[0]='call';seen.clear()
        def pytest_runtest_teardown(self,item):phase[0]='teardown';seen.clear()
        def pytest_runtest_logreport(self,report):
            if report.failed:outcomes[report.nodeid]='failed'
            elif report.when=='call':outcomes.setdefault(report.nodeid,report.outcome)
    start=time.monotonic()
    status=pytest.main(['tests','-q','--tb=short','-p','pytest_timeout','--timeout=600'],plugins=[Plugin()])
    payload={'pinned_commit':base['pinned_commit'],'provenance':'Only original expression execution coverage on unchanged pinned source. No patch, mutation or failure-answer input.',
             'collection':names,'complete_baseline':status==0 and set(collected)==set(names),'expression_coverage':1,
             'baseline_outcomes':outcomes,'elapsed_seconds':time.monotonic()-start,
             'sites':{token:sorted(sites.get(token,())) for token in sorted(registered)}}
    temporary=dest.with_name(dest.name+'.new')
    with gzip.open(temporary,'wt',compresslevel=9) as f:json.dump(payload,f,separators=(',',':'))
    temporary.replace(dest if payload['complete_baseline'] else dest.with_name(dest.name+'.failed'))
    print(f"Expression coverage: {len(registered)} sites, complete={payload['complete_baseline']}",file=sys.stderr)
    return int(status) or int(not payload['complete_baseline'])


if __name__=='__main__':raise SystemExit(main())
