"""Expression-entry coverage from the unchanged baseline, not mutant answers."""
import ast

FUNCTION_NAME='__br_expression_entry__'
SPECIAL_CALLS={'super','eval','exec','locals','globals','vars','__import__'}


def site(path,node,kind,step=None):
    location=f'{path}::{node.lineno}:{node.col_offset}:{node.end_lineno}:{node.end_col_offset}:{kind}'
    return location if step is None else location+':'+str(step)


def dump(node):return ast.dump(node,include_attributes=False)


def leaves(node):
    if isinstance(node,ast.BoolOp):return [x for value in node.values for x in leaves(value)]
    return [dump(node)]


def primitive_literal(node):
    try:
        value=ast.literal_eval(node)
        return type(value) in (str,bytes,int,float,complex,bool,type(None))
    except (ValueError,TypeError,SyntaxError):return False


class InstrumentEntries(ast.NodeTransformer):
    def __init__(self,path,registered):self.path,self.registered=path,registered
    def wrap(self,node,token):
        self.registered.add(token)
        return ast.copy_location(ast.Call(func=ast.Name(id=FUNCTION_NAME,ctx=ast.Load()),
                    args=[ast.Constant(token),node],keywords=[]),node)
    def visit_FunctionDef(self,node):
        node.body=[self.visit(n) for n in node.body];return node
    visit_AsyncFunctionDef=visit_FunctionDef
    def visit_Lambda(self,node):node.body=self.visit(node.body);return node
    def visit_AnnAssign(self,node):
        if node.value is not None:node.value=self.visit(node.value)
        return node
    def visit_ClassDef(self,node):
        node.body=[self.visit(n) for n in node.body];return node
    def visit_BoolOp(self,node):
        # Put the marker on the first leaf, leaving the Boolean AST intact.
        # Wrapping the whole expression would change short-circuit __bool__
        # call counts when Python compiles it in a truth context.
        parent=node;first=node.values[0]
        while isinstance(first,ast.BoolOp):parent=first;first=first.values[0]
        unsafe=any(isinstance(n,(ast.BoolOp,ast.IfExp,ast.Yield,ast.YieldFrom,ast.Await)) or
                   (isinstance(n,ast.Compare) and len(n.ops)>1) for n in ast.walk(first))
        if not unsafe:parent.values[0]=self.wrap(first,site(self.path,node,'bool'))
        self.generic_visit(node);return node
    def visit_Compare(self,node):
        self.generic_visit(node)
        for i,value in enumerate(node.comparators):
            node.comparators[i]=self.wrap(value,site(self.path,node,'compare',i))
        return node
    def visit_BinOp(self,node):
        dynamic=any(isinstance(n,(ast.Name,ast.Call,ast.Attribute,ast.Subscript)) for n in ast.walk(node))
        self.generic_visit(node)
        if dynamic:node.right=self.wrap(node.right,site(self.path,node,'binary'))
        return node
    def visit_Call(self,node):
        # Literal argument changes keep the function expression unchanged.
        eligible=(any(primitive_literal(n) for n in node.args) or any(primitive_literal(k.value) for k in node.keywords))
        if isinstance(node.func,ast.Name) and node.func.id in SPECIAL_CALLS:eligible=False
        self.generic_visit(node)
        if eligible:node.func=self.wrap(node.func,site(self.path,node,'call'))
        return node


def changed_sites(path,old,new):
    edits=[]
    def same(a,b):
        if type(a) is not type(b):return False
        if isinstance(a,ast.BoolOp):
            if type(a.op) is not type(b.op) or len(a.values)!=len(b.values):
                if leaves(a)!=leaves(b):return False
                edits.append(site(path,a,'bool'));return True
        if isinstance(a,ast.Compare) and len(a.ops)==len(b.ops):
            if dump(a.left)==dump(b.left) and [dump(n) for n in a.comparators]==[dump(n) for n in b.comparators]:
                for i,(x,y) in enumerate(zip(a.ops,b.ops)):
                    if type(x) is not type(y):edits.append(site(path,a,'compare',i))
                return True
        if isinstance(a,ast.BinOp) and type(a.op) is not type(b.op):
            if dump(a.left)!=dump(b.left) or dump(a.right)!=dump(b.right):return False
            edits.append(site(path,a,'binary'));return True
        if isinstance(a,ast.Call) and dump(a.func)==dump(b.func) and len(a.args)==len(b.args) and len(a.keywords)==len(b.keywords):
            pairs=list(zip(a.args,b.args))
            if [k.arg for k in a.keywords]!=[k.arg for k in b.keywords]:return False
            pairs+=list(zip([k.value for k in a.keywords],[k.value for k in b.keywords]))
            differences=[(x,y) for x,y in pairs if dump(x)!=dump(y)]
            if differences and all(primitive_literal(x) and primitive_literal(y) for x,y in differences):
                edits.append(site(path,a,'call'));return True
        if isinstance(a,ast.AST):return all(same(getattr(a,k),getattr(b,k)) for k in a._fields)
        if isinstance(a,list):return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
        return a==b
    return edits if same(old,new) and edits else None


class ExpressionIndex:
    def __init__(self,data,numbers):
        self.data,self.numbers=data,numbers
        self.valid=data.get('complete_baseline',False) and data.get('expression_coverage')==1
        self.collection=data['collection'];self.known=set(self.collection)
    def filter(self,path,old,new,selected,protected=()):
        if not self.valid:return None
        edits=changed_sites(path,old,new)
        if not edits or any(token not in self.data['sites'] for token in edits):return None
        unsafe=set(protected)
        for token in edits:
            contexts=self.data['sites'][token]
            if -1 in contexts:return None
            unsafe.update(self.numbers[self.collection[i]] for i in contexts if self.collection[i] in self.numbers)
        unknown={n for name,n in self.numbers.items() if name not in self.known}
        return selected & (unsafe | unknown)
