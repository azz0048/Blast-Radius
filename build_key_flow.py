"""Baseline key dependencies, including copied tokenizer/parser dictionaries.

No patches or answer files are used. Copies with unknown import-time effects
are excluded. Copies that may cache values at runtime are excluded as well.
"""
from __future__ import annotations
import argparse,ast,concurrent.futures,enum,gzip,importlib,inspect,json,os,pickle,pkgutil,sys,time,types
from pathlib import Path

FIELDS={'KEYWORDS','FUNCTIONS'}


def prefix(cls,field):return cls.__module__.replace('.','/')+'.py::'+cls.__qualname__+'::'+field


def resolve(node,namespace):
    if isinstance(node,ast.Name):return namespace.get(node.id)
    if isinstance(node,ast.Attribute):
        obj=resolve(node.value,namespace)
        if isinstance(obj,types.ModuleType):return vars(obj).get(node.attr)
        if isinstance(obj,type):return inspect.getattr_static(obj,node.attr,None)
    return None


def main():
    ap=argparse.ArgumentParser();ap.add_argument('checkout',type=Path)
    ap.add_argument('--output',type=Path,default=Path('key_flow.json.gz'));a=ap.parse_args()
    project=Path(__file__).resolve().parent;dest=a.output.resolve();root=a.checkout.resolve()
    with gzip.open(project/'analysis.json.gz','rt') as f:base=json.load(f)
    os.chdir(root);sys.path.insert(0,str(root));os.environ['SKIP_INTEGRATION']='0';os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    from import_key_guards import import_baseline,protect_origins
    import_time_fields=import_baseline(root)
    import pytest,sqlglot
    classes={};parents={};copy_edges={};unsafe=set();pure_refs=set();trees={}
    def module_for(path):
        name=path[:-3].replace('/','.')
        if name.endswith('.__init__'):name=name[:-9]
        return sys.modules.get(name)
    def class_origins(cls,field):
        return [prefix(c,field) for c in cls.__mro__ if field in vars(c) and c.__module__.startswith('sqlglot')]
    def key_only(node,parent):
        if isinstance(parent,ast.Starred) and parent.value is node:return True
        if isinstance(parent,(ast.For,ast.comprehension)) and parent.iter is node:return True
        return isinstance(parent,ast.Compare) and len(parent.ops)==1 and isinstance(parent.ops[0],(ast.In,ast.NotIn)) and parent.comparators[0] is node
    for path,source in base['sources'].items():
        if not path.startswith('sqlglot/'):continue
        module=module_for(path)
        if module is None:continue
        namespace=vars(module);tree=ast.parse(source);trees[path]=tree
        parent_map={child:node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if isinstance(node,ast.Attribute):
                obj=resolve(node,namespace)
                if isinstance(obj,(type,types.FunctionType,types.BuiltinFunctionType,types.MethodType,enum.Enum,classmethod,staticmethod)):
                    pure_refs.add(ast.dump(node,include_attributes=False))
        class Visitor(ast.NodeVisitor):
            def __init__(self):self.stack=[]
            def visit_ClassDef(self,node):
                self.stack.append(node.name);obj=namespace.get(self.stack[0])
                for piece in self.stack[1:]:obj=inspect.getattr_static(obj,piece,None) if isinstance(obj,type) else None
                if isinstance(obj,type):
                    for field in FIELDS:
                        if field in vars(obj) and isinstance(vars(obj)[field],dict):
                            key=prefix(obj,field);classes[key]=(obj,field,vars(obj)[field]);parents[key]=class_origins(obj,field)
                    for statement in node.body:
                        if not isinstance(statement,(ast.Assign,ast.AnnAssign)):continue
                        value=statement.value
                        if value is None:continue
                        targets=statement.targets if isinstance(statement,ast.Assign) else [statement.target]
                        target_names={t.id for t in targets if isinstance(t,ast.Name)}
                        refs=[n for n in ast.walk(value) if isinstance(n,ast.Attribute) and n.attr in FIELDS]
                        for ref in refs:
                            owner=resolve(ref.value,namespace)
                            origins=class_origins(owner,ref.attr) if isinstance(owner,type) else []
                            if not origins:
                                unsafe.update(k for k in classes if k.endswith('::'+ref.attr));continue
                            target=next(iter(target_names)) if len(target_names)==1 else None
                            allowed=target in FIELDS and isinstance(value,ast.Dict)
                            if isinstance(value,ast.Call) and isinstance(value.func,ast.Attribute) and value.func.attr=='copy' and value.func.value is ref:
                                allowed=target in FIELDS and not value.args and not value.keywords
                            # Calls in a dictionary comprehension can transform
                            # values or mutate import-time state; keep broad plans.
                            current=parent_map.get(ref)
                            while current is not None and current is not statement:
                                if isinstance(current,ast.Call):
                                    if not (isinstance(current.func,ast.Attribute) and current.func.attr in {'items','copy'} and current.func.value is ref):allowed=False
                                current=parent_map.get(current)
                            if not allowed:unsafe.update(origins);continue
                            destination=prefix(obj,target)
                            direct=isinstance(value,ast.Dict) and any(k is None and v is ref for k,v in zip(value.keys,value.values))
                            for origin in origins:copy_edges.setdefault(destination,set()).add((origin,not direct))
                self.generic_visit(node);self.stack.pop()
        Visitor().visit(tree)
        for fn in [n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name in {'__new__','__init_subclass__'}]:
            for ref in ast.walk(fn):
                if isinstance(ref,ast.Attribute) and ref.attr in FIELDS and not key_only(ref,parent_map.get(ref)):
                    # A metaclass cache read of values requires wider coverage.
                    unsafe.add('*::'+ref.attr)
    for key in classes:
        if '*::'+key.rsplit('::',1)[-1] in unsafe:unsafe.add(key)
    closure_cache={}
    def origins(key):
        if key in closure_cache:return closure_cache[key]
        answer={key:False};todo=[(key,False)]
        while todo:
            item,wild=todo.pop()
            next_edges=[(p,False) for p in parents.get(item,[]) if p!=item]+list(copy_edges.get(item,()))
            for parent,edge_wild in next_edges:
                inherited=wild or edge_wild
                if parent not in answer or inherited and not answer[parent]:
                    answer[parent]=inherited;todo.append((parent,inherited))
        closure_cache[key]=answer;return answer
    active=[''];phase=[''];seen=set();reads={};wildcards={};unsafe_keys=set();outcomes={};collected=[]
    def record(owner,key=None,cache=False):
        for source,wild in origins(owner).items():
            token=(source,None if wild else key)
            if cache:unsafe.add(source);continue
            if not active[0]:
                if key is None or wild:unsafe.add(source)
                else:unsafe_keys.add(source+'::s:'+str(key))
                continue
            if token in seen:continue
            seen.add(token);context=active[0]+'|'+phase[0]
            if key is None or wild:wildcards.setdefault(source,set()).add(context)
            elif isinstance(key,str):reads.setdefault(source+'::s:'+key,set()).add(context)
            else:unsafe.add(source)
    class Tracked(dict):
        def __init__(self,value,owner):super().__init__(value);self.owner=owner
        def __getitem__(self,key):record(self.owner,key);return super().__getitem__(key)
        def get(self,key,default=None):record(self.owner,key);return super().get(key,default)
        def keys(self):record(self.owner,cache=True);return super().keys()
        def items(self):record(self.owner,cache=True);return super().items()
        def values(self):record(self.owner,cache=True);return super().values()
        def copy(self):record(self.owner,cache=True);return super().copy()
        def __iter__(self):return super().__iter__()
    for key,(cls,field,value) in classes.items():setattr(cls,field,Tracked(value,key))
    original_pool=concurrent.futures.ProcessPoolExecutor
    class Pool(concurrent.futures.ThreadPoolExecutor):
        def submit(self,fn,/,*args,**kwargs):
            fn,args,kwargs=pickle.loads(pickle.dumps((fn,args,kwargs)))
            return super().submit(lambda:pickle.loads(pickle.dumps(fn(*args,**kwargs))))
    def pool(*args,**kwargs):
        kwargs.pop('mp_context',None);kwargs.pop('max_tasks_per_child',None);kwargs['max_workers']=1;return Pool(**kwargs)
    concurrent.futures.ProcessPoolExecutor=pool
    class Plugin:
        def pytest_collection_modifyitems(self,items):collected.extend(i.nodeid for i in items)
        def pytest_runtest_setup(self,item):active[0]=item.nodeid;phase[0]='setup';seen.clear()
        def pytest_runtest_call(self,item):active[0]=item.nodeid;phase[0]='call';seen.clear()
        def pytest_runtest_teardown(self,item):phase[0]='teardown';seen.clear()
        def pytest_runtest_logreport(self,report):
            if report.failed:outcomes[report.nodeid]='failed'
            elif report.when=='call':outcomes.setdefault(report.nodeid,report.outcome)
    started=time.monotonic()
    try:status=pytest.main(['tests','-q','--tb=short','-p','pytest_timeout','--timeout=180'],plugins=[Plugin()])
    finally:
        active[0]=''
        for key,(cls,field,value) in classes.items():setattr(cls,field,value)
        concurrent.futures.ProcessPoolExecutor=original_pool
    names=base['collection'];number={name:i for i,name in enumerate(names)};groups={}
    for name in names:groups.setdefault(name.rsplit('::',1)[0],set()).add(name)
    def compact(mapping):
        result={}
        for key,contexts in mapping.items():
            values=set()
            for context in contexts:
                name,_,stage=context.partition('|')
                if name in number:values.update(groups[name.rsplit('::',1)[0]] if stage=='setup' else [name])
            result[key]=sorted(number[name] for name in values)
        return result
    payload={'pinned_commit':base['pinned_commit'],'collection':names,'complete_baseline':status==0 and set(collected)==set(names),
             'wrapped':sorted(classes),'reads':compact(reads),'wildcards':compact(wildcards),
             'unsafe_fields':sorted(k for k in unsafe if not k.startswith('*::')),'unsafe_keys':sorted(unsafe_keys),
             'pure_references':sorted(pure_refs),'copy_edges':{k:sorted(v) for k,v in copy_edges.items()},
             'baseline_outcomes':outcomes,'elapsed_seconds':time.monotonic()-started}
    payload=protect_origins(payload,import_time_fields)
    with gzip.open(dest,'wt',compresslevel=9) as f:json.dump(payload,f,separators=(',',':'))
    print('Key flow:',len(classes),'dictionaries;',len(reads),'keys;',dest.stat().st_size,'bytes; complete',payload['complete_baseline'],file=sys.stderr)
    print('Unsupported/cached fields:',len(unsafe),file=sys.stderr)
    return int(status) or (0 if payload['complete_baseline'] else 1)

if __name__=='__main__':raise SystemExit(main())
