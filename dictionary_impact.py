"""Resolve value-only dictionary edits against measured baseline key reads."""
import ast

def literal_key(node):
    if not isinstance(node,ast.Constant):return None
    value=node.value
    if isinstance(value,str):return 's:'+value
    if isinstance(value,(int,float,bool,type(None))):return 'v:'+repr(value)
    return None

def choose(index,path,qualname,declaration,new_tree):
    if not index.dictionary_data or not isinstance(declaration.value,ast.Dict):return None
    targets=declaration.targets if isinstance(declaration,ast.Assign) else [declaration.target]
    if len(targets)!=1 or not isinstance(targets[0],ast.Name):return None
    field=targets[0].id;prefix=path+'::'+qualname+'::'+field
    if prefix not in index.dictionary_wrapped or prefix in index.dictionary_unsafe_fields:return None
    candidates=[]
    class Visitor(ast.NodeVisitor):
        def __init__(self):self.stack=[]
        def visit_ClassDef(self,node):
            self.stack.append(node.name)
            if '.'.join(self.stack)==qualname:candidates.append(node)
            self.generic_visit(node);self.stack.pop()
    Visitor().visit(new_tree)
    if len(candidates)!=1:return None
    matches=[]
    for statement in candidates[0].body:
        if isinstance(statement,(ast.Assign,ast.AnnAssign)):
            targets=statement.targets if isinstance(statement,ast.Assign) else [statement.target]
            if any(isinstance(t,ast.Name) and t.id==field for t in targets):matches.append(statement)
    if len(matches)!=1 or not isinstance(matches[0].value,ast.Dict):return None
    old=declaration.value;new=matches[0].value
    oldkeys=[literal_key(k) for k in old.keys];newkeys=[literal_key(k) for k in new.keys]
    # Membership and ordering changes can invalidate import-time caches.
    if None in oldkeys or None in newkeys or oldkeys!=newkeys or len(set(oldkeys))!=len(oldkeys):return None
    changed=[k for k,a,b in zip(oldkeys,old.values,new.values) if ast.dump(a,include_attributes=False)!=ast.dump(b,include_attributes=False)]
    if not changed:return None
    if any(prefix+'::'+key in index.dictionary_unsafe_keys for key in changed):return None
    selected=set(index.dictionary_wildcards.get(prefix,()))
    for key in changed:selected.update(index.dictionary_reads.get(prefix+'::'+key,()))
    return selected
