"""Measure scalar configuration reads on the unchanged pinned baseline."""
from __future__ import annotations
import argparse,ast,concurrent.futures,functools,gzip,json,os,pickle,sys,time,types
from pathlib import Path


def main():
    ap=argparse.ArgumentParser();ap.add_argument('checkout',type=Path);ap.add_argument('--output',type=Path,default=Path('attributes.json.gz'));a=ap.parse_args()
    dest=a.output.resolve();root=a.checkout.resolve();basepath=Path('analysis.json.gz').resolve()
    with gzip.open(basepath,'rt') as f:base=json.load(f)
    names=base['collection'];numbers={n:i for i,n in enumerate(names)}
    scalar=set();unsafe=set()
    for source in base['sources'].values():
        try:tree=ast.parse(source)
        except SyntaxError:continue
        for node in ast.walk(tree):
            if isinstance(node,ast.ClassDef):
                for statement in node.body:
                    value=getattr(statement,'value',None)
                    if isinstance(value,ast.UnaryOp):value=value.operand
                    if isinstance(value,ast.Constant):
                        for target in (statement.targets if isinstance(statement,ast.Assign) else [getattr(statement,'target',None)]):
                            if isinstance(target,ast.Name) and not target.id.startswith('__'):scalar.add(target.id)
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in {'__new__','__init_subclass__'}:
                unsafe.update(x.attr for x in ast.walk(node) if isinstance(x,ast.Attribute))
    os.chdir(root);sys.path.insert(0,str(root));os.environ['SKIP_INTEGRATION']='0';os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    import pytest
    from sqlglot.expressions.core import Expr
    from sqlglot.dialects.dialect import Dialect
    from sqlglot.generator import Generator
    from sqlglot.parser import Parser
    from sqlglot.tokens import Tokenizer
    active=[''];phase=[''];seen=set();reads={};class_cache={};outcomes={}
    def owners(cls,name):
        key=(cls,name)
        if key not in class_cache:
            result=[]
            for parent in type.__getattribute__(cls,'__mro__'):
                namespace=type.__getattribute__(parent,'__dict__')
                module=type.__getattribute__(parent,'__module__')
                if name in namespace and module.startswith('sqlglot'):
                    result.append(module.replace('.','/')+'.py::'+type.__getattribute__(parent,'__qualname__')+'::'+name)
            class_cache[key]=result
        return class_cache[key]
    def wrap(original,meta=False):
        @functools.wraps(original)
        def get(self,name):
            if active[0] and name in scalar:
                cls=self if meta else type(self);token=(cls,name)
                if token not in seen:
                    seen.add(token)
                    for key in owners(cls,name):reads.setdefault(key,set()).add(active[0]+'|'+phase[0])
            return original(self,name)
        return get
    saved=[];metas=set()
    for cls in [Expr,Dialect,Generator,Parser,Tokenizer]:
        original=cls.__getattribute__;saved.append((cls,original));cls.__getattribute__=wrap(original)
        meta=type(cls)
        if meta is not type and meta not in metas:
            metas.add(meta);original=meta.__getattribute__;saved.append((meta,original));meta.__getattribute__=wrap(original,True)
    original_pool=concurrent.futures.ProcessPoolExecutor
    class Pool(concurrent.futures.ThreadPoolExecutor):
        def submit(self,fn,/,*args,**kwargs):
            fn,args,kwargs=pickle.loads(pickle.dumps((fn,args,kwargs)))
            return super().submit(lambda:pickle.loads(pickle.dumps(fn(*args,**kwargs))))
    def pool(*args,**kwargs):kwargs.pop('mp_context',None);kwargs['max_workers']=1;return Pool(**kwargs)
    concurrent.futures.ProcessPoolExecutor=pool
    class Plugin:
        def pytest_runtest_setup(self,item):active[0]=item.nodeid;phase[0]='setup';seen.clear()
        def pytest_runtest_call(self,item):active[0]=item.nodeid;phase[0]='call';seen.clear()
        def pytest_runtest_teardown(self,item):phase[0]='teardown';seen.clear()
        def pytest_runtest_logreport(self,report):
            if report.failed:outcomes[report.nodeid]='failed'
            elif report.when=='call':outcomes.setdefault(report.nodeid,report.outcome)
    start=time.monotonic()
    try:status=pytest.main(['tests','-q','--tb=short','-p','pytest_timeout','--timeout=180'],plugins=[Plugin()])
    finally:
        active[0]=''
        for cls,original in reversed(saved):cls.__getattribute__=original
        concurrent.futures.ProcessPoolExecutor=original_pool
    groups={}
    for name in names:groups.setdefault(name.rsplit('::',1)[0],set()).add(name)
    compact={}
    for key,contexts in reads.items():
        expanded=set()
        for context in contexts:
            name,_,stage=context.partition('|')
            if name in numbers:expanded.update(groups[name.rsplit('::',1)[0]] if stage=='setup' else [name])
        compact[key]=sorted(numbers[n] for n in expanded)
    data={'pinned_commit':base['pinned_commit'],'collection':names,'reads':compact,'unsafe_fields':sorted(unsafe),'baseline_outcomes':outcomes,'elapsed':time.monotonic()-start}
    from attribute_references import collect
    data['references']=collect(base,{key.rsplit('::',1)[-1] for key in compact})
    with gzip.open(dest,'wt',compresslevel=9) as f:json.dump(data,f,separators=(',',':'))
    print('Attributes:',len(compact),'bytes',dest.stat().st_size,'exit',status,file=sys.stderr)
    print('Failures:',[n for n,v in outcomes.items() if v=='failed'],file=sys.stderr)
    return int(status)

if __name__=='__main__':raise SystemExit(main())
