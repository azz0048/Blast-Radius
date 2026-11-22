"""Class-construction dependencies collected ONLY on unmodified pinned sqlglot."""
from __future__ import annotations
import argparse
import ast
import concurrent.futures
import functools
import gzip
import json
import os
from pathlib import Path
import pickle
import sys
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('checkout',type=Path)
    parser.add_argument('--baseline',type=Path,default=Path('analysis.json.gz'))
    parser.add_argument('--output',type=Path,default=Path('usage.json.gz'))
    args=parser.parse_args()
    root=args.checkout.resolve();dest=args.output.resolve()
    with gzip.open(args.baseline.resolve(),'rt') as f:baseline=json.load(f)
    os.chdir(root);sys.path.insert(0,str(root))
    os.environ['SKIP_INTEGRATION']='0'
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    import pytest
    from sqlglot.expressions.core import Expr
    from sqlglot.dialects.dialect import Dialect
    from sqlglot.generator import Generator
    from sqlglot.parser import Parser
    from sqlglot.tokens import Tokenizer
    original_pool=concurrent.futures.ProcessPoolExecutor
    class TracedPool(concurrent.futures.ThreadPoolExecutor):
        def submit(self,fn,/,*args,**kwargs):
            fn,args,kwargs=pickle.loads(pickle.dumps((fn,args,kwargs)))
            return super().submit(lambda:pickle.loads(pickle.dumps(fn(*args,**kwargs))))
    def pool(*args,**kwargs):
        kwargs.pop('mp_context',None);kwargs.pop('max_tasks_per_child',None)
        kwargs['max_workers']=1
        return TracedPool(**kwargs)
    concurrent.futures.ProcessPoolExecutor=pool
    active=[''];phase=[''];seen=set();classes={};outcomes={};collected=[]
    class_cache={};originals=[]
    def keys(cls):
        if cls not in class_cache:
            answer=[]
            for parent in type.__getattribute__(cls,'__mro__'):
                mod=type.__getattribute__(parent,'__module__')
                if mod.startswith('sqlglot'):
                    q=type.__getattribute__(parent,'__qualname__')
                    answer.append(mod.replace('.','/')+'.py::'+q)
            class_cache[cls]=answer
        return class_cache[cls]
    def wrap(original):
        @functools.wraps(original)
        def traced(self,*args,**kwargs):
            cls=type(self)
            if active[0] and cls not in seen:
                seen.add(cls)
                context=active[0]+'|'+phase[0]
                for key in keys(cls):classes.setdefault(key,set()).add(context)
            return original(self,*args,**kwargs)
        return traced
    for cls in [Expr,Dialect,Generator,Parser,Tokenizer]:
        original=cls.__init__;originals.append((cls,original));cls.__init__=wrap(original)
    class Plugin:
        def pytest_collection_modifyitems(self,items):collected.extend(i.nodeid for i in items)
        def pytest_runtest_setup(self,item):
            active[0]=item.nodeid;phase[0]='setup';seen.clear()
        def pytest_runtest_call(self,item):
            active[0]=item.nodeid;phase[0]='call';seen.clear()
        def pytest_runtest_teardown(self,item):phase[0]='teardown';seen.clear()
        def pytest_runtest_logreport(self,report):
            if report.failed:outcomes[report.nodeid]='failed'
            elif report.when=='call':outcomes.setdefault(report.nodeid,report.outcome)
    start=time.monotonic()
    try:status=pytest.main(['tests','-q','--tb=short','-p','pytest_timeout','--timeout=180'],plugins=[Plugin()])
    finally:
        for cls,original in originals:cls.__init__=original
        concurrent.futures.ProcessPoolExecutor=original_pool
    names=baseline['collection'];number={name:i for i,name in enumerate(names)}
    groups={}
    for name in names:groups.setdefault(name.rsplit('::',1)[0],set()).add(name)
    result={}
    for key,contexts in classes.items():
        used=set()
        for context in contexts:
            name,_,stage=context.partition('|')
            if name not in number:continue
            used.update(groups[name.rsplit('::',1)[0]] if stage=='setup' else [name])
        result[key]=sorted(number[name] for name in used)
    # Explicit class references can bypass constructors. Derive these from the
    # unchanged source and previously measured per-line test contexts.
    symbols={}
    for filename,source in baseline['sources'].items():
        coverage=baseline['lines'].get(filename,{})
        if not coverage:continue
        try:tree=ast.parse(source)
        except SyntaxError:continue
        for node in ast.walk(tree):
            symbol=node.id if isinstance(node,ast.Name) else node.attr if isinstance(node,ast.Attribute) else None
            if symbol:
                ids=coverage.get(str(node.lineno),())
                if ids:symbols.setdefault(symbol,set()).update(ids)
    payload={'pinned_commit':baseline['pinned_commit'],'collection':names,
             'classes':result,'symbols':{k:sorted(v) for k,v in symbols.items()},
             'baseline_outcomes':outcomes,'elapsed_seconds':time.monotonic()-start}
    with gzip.open(dest,'wt',compresslevel=9) as f:json.dump(payload,f,separators=(',',':'))
    print('Class usage map:',len(result),'classes;',dest.stat().st_size,'bytes; exit',status,file=sys.stderr)
    print('Baseline failures:',[k for k,v in outcomes.items() if v=='failed'],file=sys.stderr)
    return int(status)

if __name__=='__main__':raise SystemExit(main())
