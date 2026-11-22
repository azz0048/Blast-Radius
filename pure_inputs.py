"""Baseline inputs to pure boolean predicates, not patch/failure lookup tables."""
import ast
import builtins
import inspect
import operator
import types
import textwrap
from functools import lru_cache

FUNCTION_NAME = '__br_baseline_predicate_inputs__'
UNKNOWN = object()
PRIMITIVES = (int, float, complex, str, bytes, bool, type(None))
CONTAINERS = (list, tuple, dict, set, frozenset, range)
ACCESSOR_DEPENDENCIES = set()
_TRUSTED_LEN = len
_TRUSTED_ISINSTANCE = isinstance

def kind_in(cls, classes):
    return any(cls is expected for expected in classes)


def key(path, node):
    return f'{path}::{node.lineno}:{node.col_offset}:{node.end_lineno}:{node.end_col_offset}'


def formula(node, leaves):
    if isinstance(node, ast.BoolOp):
        return [type(node.op).__name__, *[formula(n, leaves) for n in node.values]]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return ['Not', formula(node.operand, leaves)]
    leaves.append(node)
    return len(leaves)-1


def evaluate(tree, mask):
    if isinstance(tree, int): return bool(mask & (1 << tree))
    if tree[0] == 'And': return all(evaluate(t, mask) for t in tree[1:])
    if tree[0] == 'Or': return any(evaluate(t, mask) for t in tree[1:])
    return not evaluate(tree[1], mask)


def pure_truth(value):
    if value is UNKNOWN: return UNKNOWN
    cls = type(value)
    if kind_in(cls, PRIMITIVES + CONTAINERS): return bool(value)
    # Default object truth has no user code and cannot raise or mutate state.
    if inspect.getattr_static(cls, '__bool__', UNKNOWN) is UNKNOWN and inspect.getattr_static(cls, '__len__', UNKNOWN) is UNKNOWN:
        return True
    return UNKNOWN


@lru_cache(maxsize=2048)
def function_body(fn, code):
    try:
        if type(fn) is not types.FunctionType or not fn.__module__.startswith('sqlglot.'):
            return None
        if '__wrapped__' in fn.__dict__:
            return None
        lines,start=inspect.getsourcelines(fn)
        ACCESSOR_DEPENDENCIES.add((fn.__code__.co_filename,start,start+len(lines)-1))
        tree=ast.parse(textwrap.dedent(''.join(lines)))
        defs=[n for n in tree.body if isinstance(n,ast.FunctionDef)]
        if len(defs)!=1:return None
        node=defs[0]
        if node.args.vararg or node.args.kwarg or node.args.kwonlyargs:return None
        return node
    except (OSError,TypeError,SyntaxError):return None


def pure_function(fn, args, depth):
    if depth>5:return UNKNOWN
    node=function_body(fn,fn.__code__)
    if node is None or fn.__builtins__ is not vars(builtins):return UNKNOWN
    params=node.args.posonlyargs+node.args.args
    defaults=fn.__defaults__ or ()
    if not len(params)-len(defaults)<=len(args)<=len(params):return UNKNOWN
    values=list(args)+list(defaults[len(defaults)-(len(params)-len(args)):]) if len(args)<len(params) else list(args)
    local={name:UNKNOWN for name in fn.__code__.co_varnames+fn.__code__.co_cellvars+fn.__code__.co_freevars}
    local.update({p.arg:v for p,v in zip(params,values)})
    def block(statements):
        for statement in statements:
            if isinstance(statement,ast.Expr) and isinstance(statement.value,ast.Constant) and isinstance(statement.value.value,str):continue
            if isinstance(statement,ast.Return):
                return True,pure_value(statement.value,local,fn.__globals__,depth+1) if statement.value else None
            if isinstance(statement,ast.Assign) and len(statement.targets)==1 and isinstance(statement.targets[0],ast.Name):
                value=pure_value(statement.value,local,fn.__globals__,depth+1)
                if value is UNKNOWN:return True,UNKNOWN
                local[statement.targets[0].id]=value
            elif isinstance(statement,ast.If):
                truth=pure_truth(pure_value(statement.test,local,fn.__globals__,depth+1))
                if truth is UNKNOWN:return True,UNKNOWN
                returned,value=block(statement.body if truth else statement.orelse)
                if returned:return True,value
            else:return True,UNKNOWN
        return False,None
    return block(node.body)[1]


def pure_value(node, local, global_, depth=0):
    if depth>7:return UNKNOWN
    if isinstance(node, ast.Constant): return node.value
    if isinstance(node, ast.Name):
        return local.get(node.id, global_.get(node.id, vars(builtins).get(node.id, UNKNOWN)))
    if isinstance(node, ast.Attribute):
        base = pure_value(node.value, local, global_,depth)
        if base is UNKNOWN: return UNKNOWN
        cls = type(base)
        if inspect.getattr_static(cls, '__getattribute__', None) is not object.__getattribute__:
            if isinstance(base,type) and inspect.getattr_static(cls,'__getattribute__',None) is type.__getattribute__:
                try:
                    value=inspect.getattr_static(base,node.attr)
                    if inspect.getattr_static(type(value),'__get__',None) is None:return value
                except AttributeError:pass
            return UNKNOWN
        try:
            value = inspect.getattr_static(base, node.attr)
            if type(value) is types.MemberDescriptorType:
                return value.__get__(base, cls)
            if type(value) is property and value.fget is not None:
                return pure_function(value.fget,[base],depth+1)
            if inspect.getattr_static(type(value), '__get__', None) is not None: return UNKNOWN
            return value
        except (AttributeError, TypeError): return UNKNOWN
    if isinstance(node, ast.Call) and not node.keywords:
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'get' and len(node.args) in (1,2):
            base = pure_value(node.func.value, local, global_,depth)
            args = [pure_value(n, local, global_,depth) for n in node.args]
            if type(base) is dict and all(kind_in(type(a), PRIMITIVES) for a in args) and all(kind_in(type(k), PRIMITIVES) for k in base):
                return dict.get(base, *args)
        if isinstance(node.func, ast.Name) and node.func.id == 'len' and len(node.args) == 1:
            fn = pure_value(node.func, local, global_,depth)
            value = pure_value(node.args[0], local, global_,depth)
            if fn is _TRUSTED_LEN and kind_in(type(value), PRIMITIVES + CONTAINERS) and value is not None:
                try: return _TRUSTED_LEN(value)
                except TypeError: pass
        if isinstance(node.func,ast.Name) and node.func.id=='isinstance' and len(node.args)==2:
            fn=pure_value(node.func,local,global_,depth)
            value,classes=[pure_value(n,local,global_,depth) for n in node.args]
            classes_tuple=classes if type(classes) is tuple else (classes,)
            plain_instance=(kind_in(type(value), PRIMITIVES) or
                (inspect.getattr_static(type(value),'__getattribute__',None) is object.__getattribute__ and
                 inspect.getattr_static(type(value),'__class__',None) is object.__dict__['__class__']))
            if fn is _TRUSTED_ISINSTANCE and value is not UNKNOWN and plain_instance and all(type(c) is type for c in classes_tuple):
                # Exact metaclass type uses Python's builtin instance check.
                return _TRUSTED_ISINSTANCE(value,classes)
        if isinstance(node.func,ast.Attribute):
            base=pure_value(node.func.value,local,global_,depth)
            args=[pure_value(n,local,global_,depth) for n in node.args]
            if base is UNKNOWN or any(a is UNKNOWN for a in args):return UNKNOWN
            if type(base) is str and node.func.attr in {'upper','lower','casefold'} and not args:
                return getattr(str,node.func.attr)(base)
            if inspect.getattr_static(type(base),'__getattribute__',None) is object.__getattribute__:
                method=inspect.getattr_static(base,node.func.attr,None)
                if type(method) is types.FunctionType:
                    return pure_function(method,[base,*args],depth+1)
        return UNKNOWN
    if isinstance(node,(ast.Tuple,ast.List)):
        values=[pure_value(n,local,global_,depth) for n in node.elts]
        if any(v is UNKNOWN for v in values):return UNKNOWN
        return tuple(values) if isinstance(node,ast.Tuple) else values
    if isinstance(node,ast.Subscript):
        base,index=pure_value(node.value,local,global_,depth),pure_value(node.slice,local,global_,depth)
        if type(base) is dict and kind_in(type(index), PRIMITIVES) and all(kind_in(type(k), PRIMITIVES) for k in base):
            try:return base[index]
            except (KeyError,TypeError):return UNKNOWN
        if kind_in(type(base), (list,tuple,str,bytes)) and type(index) is int:
            try:return base[index]
            except IndexError:return UNKNOWN
        return UNKNOWN
    if isinstance(node,ast.BoolOp):
        for value_node in node.values:
            value=pure_value(value_node,local,global_,depth)
            truth=pure_truth(value)
            if truth is UNKNOWN:return UNKNOWN
            if (isinstance(node.op,ast.And) and not truth) or (isinstance(node.op,ast.Or) and truth):return value
        return value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        truth = pure_truth(pure_value(node.operand, local, global_,depth))
        return UNKNOWN if truth is UNKNOWN else not truth
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        a, b = pure_value(node.left, local, global_,depth), pure_value(node.comparators[0], local, global_,depth)
        if a is UNKNOWN or b is UNKNOWN: return UNKNOWN
        op = node.ops[0]
        if isinstance(op, ast.Is): return a is b
        if isinstance(op, ast.IsNot): return a is not b
        if isinstance(op,(ast.In,ast.NotIn)) and kind_in(type(a), PRIMITIVES) and kind_in(type(b), (tuple,list,str,bytes)):
            if kind_in(type(b), (tuple,list)) and not all(kind_in(type(v), PRIMITIVES) for v in b):return UNKNOWN
            try:return (a in b) if isinstance(op,ast.In) else (a not in b)
            except TypeError:return UNKNOWN
        if not kind_in(type(a), PRIMITIVES) or not kind_in(type(b), PRIMITIVES):
            cls=type(a)
            if cls is not type(b):return UNKNOWN
            eq,ne=inspect.getattr_static(cls,'__eq__',None),inspect.getattr_static(cls,'__ne__',None)
            if isinstance(op,(ast.Eq,ast.NotEq)):
                if eq is object.__eq__ and ne is object.__ne__:return (a is b) if isinstance(op,ast.Eq) else (a is not b)
                if (eq is str.__eq__ and ne is str.__ne__) or (eq is int.__eq__ and ne is int.__ne__):
                    return operator.eq(a,b) if isinstance(op,ast.Eq) else operator.ne(a,b)
            return UNKNOWN
        operations = {ast.Eq:operator.eq, ast.NotEq:operator.ne, ast.Lt:operator.lt,
                      ast.LtE:operator.le, ast.Gt:operator.gt, ast.GtE:operator.ge}
        if type(op) not in operations: return UNKNOWN
        try: return operations[type(op)](a,b)
        except (TypeError, ValueError): return UNKNOWN
    return UNKNOWN

