"""Find parser/tokenizer maps used before normal baseline key tracing begins."""
import importlib
import os
from pathlib import Path
import pkgutil
import sys


def import_baseline(root):
    observed=set();root=Path(root).resolve()
    def profile(frame,event,arg):
        if event!='call' or frame.f_code.co_name!='__init__':return
        filename=frame.f_code.co_filename
        if filename==str(root/'sqlglot/tokens.py'):field='KEYWORDS'
        elif filename==str(root/'sqlglot/parser.py'):field='FUNCTIONS'
        else:return
        value=frame.f_locals.get('self')
        if value is None:return
        for cls in type(value).__mro__:
            if field in vars(cls):
                if cls.__module__.startswith('sqlglot'):
                    observed.add(cls.__module__.replace('.','/')+'.py::'+cls.__qualname__+'::'+field)
                break
    sys.path.insert(0,str(root));os.environ['SKIP_INTEGRATION']='0'
    previous=sys.getprofile();sys.setprofile(profile)
    try:
        import pytest
        import sqlglot
        for item in pkgutil.walk_packages(sqlglot.__path__,sqlglot.__name__+'.'):
            if item.name.endswith('.__main__'):continue
            try:importlib.import_module(item.name)
            except ImportError:pass
    finally:sys.setprofile(previous)
    return observed


def protect_origins(data,observed):
    def ancestors(key):
        path,qualname,field=key.split('::')
        module_name=path[:-3].replace('/','.')
        if module_name.endswith('.__init__'):module_name=module_name[:-9]
        obj=sys.modules.get(module_name)
        for piece in qualname.split('.'):
            obj=getattr(obj,piece,None)
            if obj is None:return []
        return [cls.__module__.replace('.','/')+'.py::'+cls.__qualname__+'::'+field
                for cls in obj.__mro__ if field in vars(cls) and cls.__module__.startswith('sqlglot')]
    todo=list(observed);protected=set()
    while todo:
        key=todo.pop()
        if key in protected:continue
        protected.add(key);todo.extend(ancestors(key))
        todo.extend(edge[0] for edge in data.get('copy_edges',{}).get(key,()))
    data['unsafe_fields']=sorted(set(data['unsafe_fields'])|protected)
    data['cold_import_guards']=sorted(observed)
    data['cold_import_guards_checked']=True
    return data


def main():
    import argparse,gzip,json
    ap=argparse.ArgumentParser();ap.add_argument('checkout',type=Path)
    ap.add_argument('--map',type=Path,default=Path(__file__).with_name('key_flow.json.gz'));args=ap.parse_args()
    observed=import_baseline(args.checkout)
    with gzip.open(args.map,'rt') as f:data=json.load(f)
    data=protect_origins(data,observed)
    with gzip.open(args.map,'wt',compresslevel=9) as f:json.dump(data,f,separators=(',',':'))
    print('Import-time maps:',len(observed),'protected fields:',len(data['unsafe_fields']))
    for field in sorted(observed):print(field)

if __name__=='__main__':main()
