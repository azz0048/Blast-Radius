"""Baseline-only configuration-key dependencies; never reads runtime patches."""
from __future__ import annotations
import argparse,ast,concurrent.futures,gzip,importlib,json,os,pickle,pkgutil,sys,time
from pathlib import Path


def key_name(key):
    if isinstance(key,str):return 's:'+key
    if isinstance(key,(int,float,bool,type(None))):return 'v:'+repr(key)
    if isinstance(key,type):return 'c:'+key.__name__
    return None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('checkout',type=Path);ap.add_argument('--output',type=Path,default=Path('dictionary_reads.json.gz'));a=ap.parse_args()
    dest=a.output.resolve()
    with gzip.open('analysis.json.gz','rt') as f:base=json.load(f)
    root=a.checkout.resolve();fields=set();unsafe=set()
    # Class-definition copies and value-dependent metaclass caches need broad
    # coverage. Membership-only cache reads are safe for value-only changes.
    for filename,source in base['sources'].items():
        if not filename.startswith('sqlglot/'):continue
        try:tree=ast.parse(source)
        except SyntaxError:continue
        for cls in (n for n in ast.walk(tree) if isinstance(n,ast.ClassDef)):
            for statement in cls.body:
                if isinstance(statement,(ast.Assign,ast.AnnAssign)):
                    value=statement.value
                    if isinstance(value,ast.Dict):
                        for target in (statement.targets if isinstance(statement,ast.Assign) else [statement.target]):
                            if isinstance(target,ast.Name):fields.add(target.id)
                    if value:
                        unsafe.update(n.attr for n in ast.walk(value) if isinstance(n,ast.Attribute))
        for fn in (n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in {'__new__','__init_subclass__'}):
            parents={child:node for node in ast.walk(fn) for child in ast.iter_child_nodes(node)}
            aliases={}
            for node in ast.walk(fn):
                if isinstance(node,ast.Assign) and isinstance(node.value,ast.Attribute):
                    for target in node.targets:
                        if isinstance(target,ast.Name):aliases[target.id]=node.value.attr
            for node in ast.walk(fn):
                if not isinstance(node,ast.Attribute):continue
                name=node.attr;parent=parents.get(node)
                if isinstance(parent,ast.Assign) and parent.value is node:
                    alias_names=[t.id for t in parent.targets if isinstance(t,ast.Name)]
                    safe=True
                    for candidate in ast.walk(fn):
                        if isinstance(candidate,ast.Name) and isinstance(candidate.ctx,ast.Load) and candidate.id in alias_names:
                            use=parents.get(candidate)
                            if not (isinstance(use,ast.Compare) and all(isinstance(op,(ast.In,ast.NotIn)) for op in use.ops)):
                                safe=False;break
                    if not safe:unsafe.add(name)
                elif isinstance(parent,ast.Compare) and all(isinstance(op,(ast.In,ast.NotIn)) for op in parent.ops):pass
                else:unsafe.add(name)
    safe_fields=fields-unsafe
    os.chdir(root);sys.path.insert(0,str(root));os.environ['SKIP_INTEGRATION']='0';os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    import pytest,sqlglot
    # Resolve the unchanged class hierarchy before wrapping its dictionaries.
    for item in pkgutil.walk_packages(sqlglot.__path__,sqlglot.__name__+'.'):
        if item.name.endswith('.__main__'):continue
        try:importlib.import_module(item.name)
        except ImportError:pass
    active=[''];phase=[''];seen=set();reads={};wildcards={};unsafe_keys=set();unsafe_runtime_fields=set();outcomes={};resolve_cache={}
    def owner_keys(owner,field):
        cache_key=(owner,field)
        if cache_key not in resolve_cache:
            path,qual=owner.split('::',1);module=path[:-3].replace('/','.')
            if module.endswith('.__init__'):module=module[:-9]
            obj=sys.modules.get(module)
            try:
                for piece in qual.split('.'):obj=getattr(obj,piece)
                result=[]
                for cls in obj.__mro__:
                    if field in cls.__dict__ and cls.__module__.startswith('sqlglot'):
                        result.append(cls.__module__.replace('.','/')+'.py::'+cls.__qualname__+'::'+field)
            except (AttributeError,TypeError):result=[owner+'::'+field]
            resolve_cache[cache_key]=result
        return resolve_cache[cache_key]
    def record(owner,field,key=None):
        for prefix in owner_keys(owner,field):
            token=(prefix,key)
            if not active[0]:
                if key is None:unsafe_runtime_fields.add(prefix)
                else:unsafe_keys.add(prefix+'::'+key)
                continue
            if token in seen:continue
            seen.add(token);context=active[0]+'|'+phase[0]
            if key is None:wildcards.setdefault(prefix,set()).add(context)
            else:reads.setdefault(prefix+'::'+key,set()).add(context)
    class TrackedDict(dict):
        def __init__(self,value,owner,field):super().__init__(value);self.owner=owner;self.field=field
        def __getitem__(self,key):
            encoded=key_name(key)
            if encoded is not None:record(self.owner,self.field,encoded)
            else:record(self.owner,self.field)
            return super().__getitem__(key)
        def get(self,key,default=None):
            encoded=key_name(key)
            if encoded is not None:record(self.owner,self.field,encoded)
            else:record(self.owner,self.field)
            return super().get(key,default)
        def items(self):record(self.owner,self.field);return super().items()
        def values(self):record(self.owner,self.field);return super().values()
        def copy(self):record(self.owner,self.field);return super().copy()
        # Generic mapping copies then invoke our item accesses, rather than
        # CPython's fast raw-dictionary copying path.
        def __iter__(self):return super().__iter__()
    wrapped=[];visited=set()
    def visit(obj):
        if not isinstance(obj,type) or obj in visited or not getattr(obj,'__module__','').startswith('sqlglot'):return
        visited.add(obj)
        for field,value in list(vars(obj).items()):
            if isinstance(value,type) and getattr(value,'__qualname__','').startswith(obj.__qualname__+'.'):
                visit(value)
            if field not in safe_fields or not isinstance(value,dict) or isinstance(value,TrackedDict):continue
            owner=obj.__module__.replace('.','/')+'.py::'+obj.__qualname__
            wrapped.append((obj,field,value));setattr(obj,field,TrackedDict(value,owner,field))
    for module_name,module in list(sys.modules.items()):
        if module_name.startswith('sqlglot') and module is not None:
            for obj in list(vars(module).values()):visit(obj)
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
        for cls,field,value in reversed(wrapped):setattr(cls,field,value)
        concurrent.futures.ProcessPoolExecutor=original_pool
    names=base['collection'];number={name:i for i,name in enumerate(names)};groups={}
    for name in names:groups.setdefault(name.rsplit('::',1)[0],set()).add(name)
    def compact(mapping):
        result={}
        for key,contexts in mapping.items():
            expanded=set()
            for context in contexts:
                name,_,stage=context.partition('|')
                if name in number:expanded.update(groups[name.rsplit('::',1)[0]] if stage=='setup' else [name])
            result[key]=sorted(number[name] for name in expanded)
        return result
    payload={'pinned_commit':base['pinned_commit'],'collection':names,'reads':compact(reads),'wildcards':compact(wildcards),
             'safe_fields':sorted(safe_fields),'wrapped':[cls.__module__.replace('.','/')+'.py::'+cls.__qualname__+'::'+field for cls,field,value in wrapped],
             'unsafe_keys':sorted(unsafe_keys),'unsafe_fields':sorted(unsafe_runtime_fields),'baseline_outcomes':outcomes,'elapsed':time.monotonic()-start}
    with gzip.open(dest,'wt',compresslevel=9) as f:json.dump(payload,f,separators=(',',':'))
    print('Safe fields:',sorted(safe_fields),file=sys.stderr)
    print('Dictionary map:',len(wrapped),'dictionaries',len(reads),'keys; exit',status,'bytes',dest.stat().st_size,file=sys.stderr)
    print('Failures:',[n for n,v in outcomes.items() if v=='failed'],file=sys.stderr)
    return int(status)

if __name__=='__main__':raise SystemExit(main())
