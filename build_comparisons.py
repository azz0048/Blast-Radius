"""Collect only original operand types on the unchanged pinned baseline."""
from __future__ import annotations
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

from comparison_profiles import FUNCTION_NAME,InstrumentComparisons,OPERATORS,type_trait,operand_trait


def main():
    parser=argparse.ArgumentParser();parser.add_argument('checkout',type=Path)
    parser.add_argument('--output',type=Path,default=Path('comparisons.json.gz'))
    args=parser.parse_args();root=args.checkout.resolve();dest=args.output.resolve()
    with gzip.open(Path(__file__).with_name('analysis.json.gz'),'rt') as f:base=json.load(f)
    names=base['collection'];numbers={name:i for i,name in enumerate(names)}
    groups={}
    for name in names:groups.setdefault(name.rsplit('::',1)[0],set()).add(name)
    active=[''];phase=[''];seen=set();sites={};traits={};patterns={};outcomes={};collected=[];canonical_ids={}
    ops={cls.__name__:fn for cls,fn in OPERATORS.items()}
    def observed(site,operation,left,right):
        types=(type(left),type(right));simple_token=(site,*types)
        if simple_token in seen:return ops[operation](left,right)
        pair=[]
        for cls,value in zip(types,(left,right)):
            if cls not in traits:
                trait=type_trait(cls)
                if trait.startswith(('enum-str:','enum-int:')):
                    trait+='@'+str(len(traits))
                    canonical_ids[cls]={id(v) for v in type.__getattribute__(cls,'_member_map_').values()}
                traits[cls]=trait
            trait=operand_trait(value,traits[cls],canonical_ids.get(cls))
            pair.append(trait)
        pair=tuple(pair)
        token=(site,*types,*pair) if any(cls in canonical_ids for cls in types) else simple_token
        if token not in seen:
            seen.add(token)
            if pair not in patterns:patterns[pair]=len(patterns)
            key=patterns[pair]
            contexts=sites.setdefault(site,{}).setdefault(key,set())
            name=active[0]
            if name in numbers:
                group_members=groups[name.rsplit('::',1)[0]] if phase[0]=='setup' else [name]
                contexts.update(numbers[member] for member in group_members)
            else:contexts.add(-1)
        return ops[operation](left,right)
    class Loader(importlib.machinery.SourceFileLoader):
        def get_code(self,fullname):
            source=self.get_data(self.path)
            tree=ast.parse(source,filename=self.path,type_comments=True)
            if FUNCTION_NAME.encode() in source:raise ValueError('Instrumentation name collision')
            tree=InstrumentComparisons(Path(self.path).relative_to(root).as_posix()).visit(tree)
            ast.fix_missing_locations(tree)
            return compile(tree,self.path,'exec',dont_inherit=True)
        def exec_module(self,module):
            module.__dict__[FUNCTION_NAME]=observed
            super().exec_module(module)
    class Finder(importlib.abc.MetaPathFinder):
        def find_spec(self,fullname,path=None,target=None):
            if not (fullname=='sqlglot' or fullname.startswith(('sqlglot.','tests.')) or fullname=='tests'):return None
            spec=importlib.machinery.PathFinder.find_spec(fullname,path)
            if spec and spec.origin and spec.origin.endswith('.py') and Path(spec.origin).is_relative_to(root):
                spec.loader=Loader(fullname,spec.origin)
            return spec
    original_pool=concurrent.futures.ProcessPoolExecutor
    class Pool(concurrent.futures.ThreadPoolExecutor):
        def submit(self,fn,/,*args,**kwargs):
            fn,args,kwargs=pickle.loads(pickle.dumps((fn,args,kwargs)))
            return super().submit(lambda:pickle.loads(pickle.dumps(fn(*args,**kwargs))))
    def pool(*args,**kwargs):
        kwargs.pop('mp_context',None);kwargs.pop('max_tasks_per_child',None);kwargs['max_workers']=1
        return Pool(**kwargs)
    concurrent.futures.ProcessPoolExecutor=pool
    os.environ['SKIP_INTEGRATION']='0';os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    os.chdir(root);sys.path.insert(0,str(root));finder=Finder();sys.meta_path.insert(0,finder)
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
    try:status=pytest.main(['tests','-q','--tb=short','-p','pytest_timeout','--timeout=600'],plugins=[Plugin()])
    finally:
        sys.meta_path.remove(finder);concurrent.futures.ProcessPoolExecutor=original_pool
    payload={'pinned_commit':base['pinned_commit'],'provenance':'Original operand types only. No patches, alternate operations, or failure answers.',
             'collection':names,'complete_baseline':status==0 and set(collected)==set(names),'canonical_enum_profiles':True,
             'patterns':[list(pair) for pair in patterns],
             'sites':{site:{str(pair):sorted(ids) for pair,ids in values.items()} for site,values in sites.items()},
             'baseline_outcomes':outcomes,'elapsed_seconds':time.monotonic()-start}
    temporary=dest.with_name(dest.name+'.new')
    with gzip.open(temporary,'wt',compresslevel=9) as f:json.dump(payload,f,separators=(',',':'))
    if payload['complete_baseline']:temporary.replace(dest)
    else:temporary.replace(dest.with_name(dest.name+'.failed'))
    print('Operand profile:',len(sites),'sites; complete',payload['complete_baseline'],file=sys.stderr)
    return int(status) or (0 if payload['complete_baseline'] else 1)

if __name__=='__main__':raise SystemExit(main())
