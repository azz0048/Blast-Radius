"""Conservative source proof that a type's comparisons with None are pure booleans.

This examines the unchanged equality implementation. It does not run operators
against test values, mutate source, or inspect any patch.
"""
import ast
import builtins
import inspect
import operator
import textwrap
import types

UNKNOWN=object();SELF=object()
PRIMITIVES=(type(None),bool,int,float,complex,str,bytes)


def none_equality_safe(cls,cache=None,mode='both'):
    if cls is type(None):return False
    cache={} if cache is None else cache
    key=(cls,mode)
    if key in cache:return cache[key]
    cache[key]=False
    eq=inspect.getattr_static(cls,'__eq__',None)
    ne=inspect.getattr_static(cls,'__ne__',None)
    if eq is not object.__eq__:
        if not isinstance(eq,types.FunctionType):return False
        if _method_result(eq,cls,False,cache) is not False:return False
    if mode=='eq':cache[key]=True;return True
    if ne is object.__ne__:
        cache[key]=True;return True
    if isinstance(ne,types.FunctionType) and _method_result(ne,cls,True,cache) is True:
        cache[key]=True;return True
    return False


def _method_result(fn,cls,eq_proven,cache):
    try:
        tree=ast.parse(textwrap.dedent(inspect.getsource(fn)))
        method=next(n for n in tree.body if isinstance(n,ast.FunctionDef))
    except (OSError,TypeError,SyntaxError,StopIteration):return UNKNOWN
    parameters=[*method.args.posonlyargs,*method.args.args]
    if len(parameters)!=2 or method.args.vararg or method.args.kwarg:return UNKNOWN
    self_name,other_name=[p.arg for p in parameters]
    namespace=fn.__globals__
    def truth(value):return bool(value) if type(value) in PRIMITIVES else UNKNOWN
    def name_value(name):
        if name==self_name:return SELF
        if name==other_name:return None
        value=namespace.get(name,vars(builtins).get(name,UNKNOWN))
        # A mutable module flag is not a type property, even if it happens to
        # contain a constant-looking value while metadata is being built.
        return value if isinstance(value,(type,types.ModuleType)) else UNKNOWN
    def expression(node):
        if isinstance(node,ast.Constant):return node.value if type(node.value) in PRIMITIVES else UNKNOWN
        if isinstance(node,ast.Name):return name_value(node.id)
        if isinstance(node,ast.Attribute):
            owner=expression(node.value)
            if isinstance(owner,types.ModuleType):return vars(owner).get(node.attr,UNKNOWN)
            if isinstance(owner,type):return inspect.getattr_static(owner,node.attr,UNKNOWN)
            return UNKNOWN
        if isinstance(node,ast.BoolOp):
            value=UNKNOWN
            for child in node.values:
                value=expression(child);boolean=truth(value)
                if boolean is UNKNOWN:return UNKNOWN
                if isinstance(node.op,ast.And) and not boolean:return value
                if isinstance(node.op,ast.Or) and boolean:return value
            return value
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,ast.Not):
            value=truth(expression(node.operand))
            return UNKNOWN if value is UNKNOWN else not value
        if isinstance(node,ast.IfExp):
            condition=truth(expression(node.test))
            return UNKNOWN if condition is UNKNOWN else expression(node.body if condition else node.orelse)
        if isinstance(node,ast.Compare) and len(node.ops)==1:
            left,right=expression(node.left),expression(node.comparators[0]);op=node.ops[0]
            if left is UNKNOWN or right is UNKNOWN:return UNKNOWN
            if isinstance(op,(ast.Is,ast.IsNot)):
                if left is SELF or right is SELF:
                    if left is SELF and right is SELF:result=True
                    elif left is None or right is None:result=False
                    else:return UNKNOWN
                else:result=left is right
                return not result if isinstance(op,ast.IsNot) else result
            if not isinstance(op,(ast.Eq,ast.NotEq)) or left is SELF or right is SELF:return UNKNOWN
            if type(left) in PRIMITIVES and type(right) in PRIMITIVES:result=operator.eq(left,right)
            elif left is None and isinstance(right,type) or right is None and isinstance(left,type):
                target=right if left is None else left
                if not none_equality_safe(type(target),cache,'eq'):return UNKNOWN
                result=False
            elif isinstance(left,type) and isinstance(right,type):
                if inspect.getattr_static(type(left),'__eq__',None) is not object.__eq__ or inspect.getattr_static(type(right),'__eq__',None) is not object.__eq__:return UNKNOWN
                result=left is right
            else:return UNKNOWN
            return not result if isinstance(op,ast.NotEq) else result
        if isinstance(node,ast.Call) and not node.keywords:
            if isinstance(node.func,ast.Name):
                name=node.func.id
                if name=='type' and namespace.get(name,builtins.type) is builtins.type and len(node.args)==1:
                    value=expression(node.args[0])
                    return cls if value is SELF else type(value) if value is not UNKNOWN else UNKNOWN
                if name=='isinstance' and namespace.get(name,builtins.isinstance) is builtins.isinstance and len(node.args)==2:
                    value,target=map(expression,node.args)
                    if value is UNKNOWN or value is SELF or not isinstance(target,type):return UNKNOWN
                    if inspect.getattr_static(type(target),'__instancecheck__',None) is not type.__instancecheck__:return UNKNOWN
                    return isinstance(value,target)
            if eq_proven and isinstance(node.func,ast.Attribute) and node.func.attr=='__eq__' and isinstance(node.func.value,ast.Name) and node.func.value.id==self_name and len(node.args)==1:
                getter=inspect.getattr_static(cls,'__getattribute__',None)
                if getter is object.__getattribute__ and not cls.__dictoffset__ and expression(node.args[0]) is None:return False
        return UNKNOWN
    def statements(body):
        for node in body:
            if isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str):continue
            if isinstance(node,ast.Return):return True,expression(node.value) if node.value else None
            if isinstance(node,ast.If):
                condition=truth(expression(node.test))
                if condition is UNKNOWN:return True,UNKNOWN
                returned,value=statements(node.body if condition else node.orelse)
                if returned:return True,value
            else:return True,UNKNOWN
        return False,UNKNOWN
    return statements(method.body)[1]
