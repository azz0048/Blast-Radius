"""Conservative source-derived supplements to measured attribute reads."""
import ast
from bisect import bisect_left

def collect(base,fields):
    result={}
    for filename,source in base['sources'].items():
        coverage={int(k):v for k,v in base['lines'].get(filename,{}).items()}
        if not coverage:continue
        keys=sorted(coverage)
        try:tree=ast.parse(source)
        except SyntaxError:continue
        statements=None
        for node in ast.walk(tree):
            field=(node.attr if isinstance(node,ast.Attribute) and isinstance(node.ctx,ast.Load)
                   else node.value if isinstance(node,ast.Constant) and isinstance(node.value,str) else None)
            if field not in fields:continue
            ids=coverage.get(node.lineno)
            if ids is None:
                if statements is None:statements=[n for n in ast.walk(tree) if isinstance(n,ast.stmt)]
                parents=[n for n in statements if n.lineno<=node.lineno<=getattr(n,'end_lineno',n.lineno)]
                if parents:
                    parent=min(parents,key=lambda n:getattr(n,'end_lineno',n.lineno)-n.lineno)
                    i=bisect_left(keys,parent.lineno)
                    if i<len(keys) and keys[i]<=getattr(parent,'end_lineno',parent.lineno):ids=coverage[keys[i]]
            if ids:result.setdefault(field,set()).update(ids)
    return {field:sorted(ids) for field,ids in result.items()}
