"""Derive None-comparison type properties from unchanged baseline method source."""
import argparse,gzip,importlib,inspect,json,os,sys
from pathlib import Path
from none_semantics import none_equality_safe

def main():
    ap=argparse.ArgumentParser();ap.add_argument('checkout',type=Path)
    ap.add_argument('--output',type=Path,default=Path('none_traits.json'));a=ap.parse_args()
    root=a.checkout.resolve();sys.path.insert(0,str(root));os.environ['SKIP_INTEGRATION']='0'
    import pytest
    with gzip.open(Path(__file__).with_name('comparisons.json.gz'),'rt') as f:profile=json.load(f)
    names={trait[len('unknown:'):] for pair in profile['patterns'] for trait in pair if trait.startswith('unknown:sqlglot.')}
    cache={};eq_types=[];ne_types=[];methods={};protected={}
    def remember(fn):
        if not inspect.isfunction(fn):return
        key=fn.__module__+'.'+fn.__qualname__
        source,start=inspect.getsourcelines(fn)
        methods[key]=''.join(source)
        protected[key]={'path':Path(inspect.getsourcefile(fn)).relative_to(root).as_posix(),
                        'start':start,'end':start+len(source)-1,'method':key}
    for name in sorted(names):
        parts=name.split('.');obj=None
        for i in range(len(parts),0,-1):
            try:obj=importlib.import_module('.'.join(parts[:i]));tail=parts[i:];break
            except ImportError:pass
        if obj is None:continue
        for piece in tail:
            obj=inspect.getattr_static(obj,piece,None)
            if obj is None:break
        if not isinstance(obj,type) or obj.__module__+'.'+obj.__qualname__!=name:continue
        if none_equality_safe(obj,cache,'eq'):
            eq_types.append(name)
            if none_equality_safe(obj,cache,'ne'):
                ne_types.append(name);remember(inspect.getattr_static(obj,'__ne__'))
            eq=inspect.getattr_static(obj,'__eq__')
            remember(eq)
    data={'pinned_commit':profile['pinned_commit'],'safe_eq_none_types':eq_types,'safe_ne_none_types':ne_types,
          'method_sources':methods,'protected_methods':list(protected.values()),
          'provenance':'Static proof of unchanged type methods. No patches, test values, alternate operations, or failure answers.'}
    a.output.write_text(json.dumps(data,separators=(',',':'))+'\n')
    print('Proved equality-safe types:',len(eq_types),'inequality-safe types:',len(ne_types),'method implementations:',len(methods),'bytes:',a.output.stat().st_size)

if __name__=='__main__':main()
