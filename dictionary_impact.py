"""Resolve value-only dictionary edits against measured baseline key reads."""
import ast

def merge_maps(base,extra):
    if not extra.get('complete_baseline') or not extra.get('cold_import_guards_checked') or extra.get('collection')!=base.get('collection'):
        return base
    result=dict(base)
    for name in ('wrapped','unsafe_fields','unsafe_keys','pure_references'):
        result[name]=sorted(set(base.get(name,()))|set(extra.get(name,())))
    for name in ('reads','wildcards'):
        merged=dict(base.get(name,{}))
        for key,ids in extra.get(name,{}).items():merged[key]=sorted(set(merged.get(key,()))|set(ids))
        result[name]=merged
    return result

def pure_value(node,references):
    if isinstance(node,(ast.Constant,ast.Name)):return True
    if isinstance(node,ast.Attribute):return ast.dump(node,include_attributes=False) in references
    if isinstance(node,(ast.Tuple,ast.List)):return all(pure_value(v,references) for v in node.elts)
    if isinstance(node,ast.UnaryOp):return isinstance(node.operand,ast.Constant)
    if isinstance(node,ast.BinOp):
        def literal(n):
            if isinstance(n,ast.Constant):return True
            if isinstance(n,ast.UnaryOp):return literal(n.operand)
            return isinstance(n,ast.BinOp) and literal(n.left) and literal(n.right)
        return literal(node)
    if isinstance(node,ast.Lambda):
        return all(pure_value(v,references) for v in [*node.args.defaults,*node.args.kw_defaults] if v is not None)
    return False

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
    # Preserve every key, unpack expression, and its order. Unchanged dictionary
    # copies are covered by the baseline copy-dependency graph.
    if len(old.keys)!=len(new.keys):return None
    changed=[];references=getattr(index,'dictionary_pure_refs',set())
    for oldkey,newkey,a,b in zip(old.keys,new.keys,old.values,new.values):
        if (ast.dump(oldkey) if oldkey is not None else None)!=(ast.dump(newkey) if newkey is not None else None):return None
        if ast.dump(a,include_attributes=False)==ast.dump(b,include_attributes=False):continue
        if oldkey is None:return None
        key=literal_key(oldkey)
        if key is None or not pure_value(a,references) or not pure_value(b,references):return None
        changed.append(key)
    if not changed:return None
    if any(prefix+'::'+key in index.dictionary_unsafe_keys for key in changed):return None
    selected=set(index.dictionary_wildcards.get(prefix,()))
    for key in changed:selected.update(index.dictionary_reads.get(prefix+'::'+key,()))
    return selected
