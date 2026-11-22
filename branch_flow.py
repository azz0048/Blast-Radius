"""Runtime reasoning over baseline Boolean inputs and observed effect order.

The data contains only executions of the unmodified pinned revision. New
operators are interpreted here, on the current patch, never during collection.
"""
import ast
import copy

START='__br_flow_start__'
LEAF='__br_flow_leaf__'
END='__br_flow_end__'


def site(path,node):
    return f'{path}::{node.lineno}:{node.col_offset}:{node.end_lineno}:{node.end_col_offset}'


def shape(node,leaves):
    if isinstance(node,ast.BoolOp):return [type(node.op).__name__,*[shape(n,leaves) for n in node.values]]
    if isinstance(node,ast.UnaryOp) and isinstance(node.op,ast.Not):return ['Not',shape(node.operand,leaves)]
    leaves.append(node);return len(leaves)-1


def location(node):return node.lineno,node.col_offset,node.end_lineno,node.end_col_offset


def boolean_root(node):
    return isinstance(node,ast.BoolOp) or (isinstance(node,ast.UnaryOp) and isinstance(node.op,ast.Not) and boolean_root(node.operand))


class InstrumentFlow(ast.NodeTransformer):
    def __init__(self,path,registry):self.path,self.registry=path,registry
    def visit_FunctionDef(self,node):
        node.body=[self.visit(n) for n in node.body];return node
    visit_AsyncFunctionDef=visit_FunctionDef
    def visit_Lambda(self,node):node.body=self.visit(node.body);return node
    def visit_ClassDef(self,node):
        node.body=[self.visit(n) for n in node.body];return node
    def visit_AnnAssign(self,node):
        if node.value is not None:
            raw=copy.deepcopy(node.value);node.value=self.visit(node.value)
            node.value=self.wrap(raw,node.value,'return')
        return node
    def wrap(self,raw,actual,mode):
        if not boolean_root(raw):return actual
        leaves=[];formula=shape(raw,leaves)
        if len(leaves)>8:return actual
        # These shapes can change compiler truth-conversion behavior if wrapped
        # as values. Keep them on the ordinary coverage path.
        if any(isinstance(n,(ast.IfExp,ast.NamedExpr,ast.Yield,ast.YieldFrom,ast.Await)) or
               (isinstance(n,ast.Compare) and len(n.ops)>1) for n in leaves):return actual
        token=site(self.path,raw)
        self.registry[token]={'mode':mode,'formula':formula,'leaves':leaves}
        numbers={location(n):i for i,n in enumerate(leaves)}
        class LeafWrapper(ast.NodeTransformer):
            def visit(self,node):
                if isinstance(node,ast.expr) and location(node) in numbers:
                    return ast.copy_location(ast.Call(func=ast.Name(id=LEAF,ctx=ast.Load()),
                        args=[ast.Constant(token),ast.Constant(numbers[location(node)]),node],keywords=[]),node)
                return super().visit(node)
        expression=LeafWrapper().visit(actual)
        if mode=='truth':expression=ast.IfExp(test=expression,body=ast.Constant(True),orelse=ast.Constant(False))
        begin=ast.Call(func=ast.Name(id=START,ctx=ast.Load()),args=[ast.Constant(token)],keywords=[])
        finish=ast.Call(func=ast.Name(id=END,ctx=ast.Load()),args=[ast.Constant(token),expression],keywords=[])
        return ast.copy_location(ast.Subscript(value=ast.Tuple(elts=[begin,finish],ctx=ast.Load()),
                                               slice=ast.Constant(1),ctx=ast.Load()),actual)
    def visit_If(self,node):
        raw=copy.deepcopy(node.test);self.generic_visit(node)
        node.test=self.wrap(raw,node.test,'truth');return node
    visit_While=visit_If
    visit_Assert=visit_If
    visit_IfExp=visit_If
    def value_fields(self,node,fields):
        raw={name:copy.deepcopy(getattr(node,name)) for name in fields}
        self.generic_visit(node)
        for name in fields:
            before,after=raw[name],getattr(node,name)
            if isinstance(before,list):
                setattr(node,name,[self.wrap(x,y,'return') if x is not None else y for x,y in zip(before,after)])
            elif before is not None:setattr(node,name,self.wrap(before,after,'return'))
        return node
    def visit_Return(self,node):return self.value_fields(node,('value',))
    def visit_Assign(self,node):return self.value_fields(node,('value',))
    def visit_Call(self,node):
        raw_args=copy.deepcopy(node.args);raw_keywords=[copy.deepcopy(k.value) for k in node.keywords]
        self.generic_visit(node)
        node.args=[self.wrap(a,b,'return') for a,b in zip(raw_args,node.args)]
        for raw,k in zip(raw_keywords,node.keywords):k.value=self.wrap(raw,k.value,'return')
        return node
    def visit_Compare(self,node):return self.value_fields(node,('left','comparators'))
    def visit_BinOp(self,node):return self.value_fields(node,('left','right'))
    def visit_Attribute(self,node):return self.value_fields(node,('value',))
    def visit_Subscript(self,node):return self.value_fields(node,('value','slice'))
    def visit_List(self,node):return self.value_fields(node,('elts',))
    visit_Tuple=visit_List
    visit_Set=visit_List
    def visit_Dict(self,node):return self.value_fields(node,('keys','values'))


def changed_roots(path,old,new):
    changes=[]
    def same(a,b):
        if boolean_root(a) and boolean_root(b) and ast.dump(a)!=ast.dump(b):
            left,right=[],[];original,modified=shape(a,left),shape(b,right)
            if original!=modified and [ast.dump(n) for n in left]==[ast.dump(n) for n in right]:
                changes.append((site(path,a),original,modified));return True
        if type(a) is not type(b):return False
        field='test' if isinstance(a,(ast.If,ast.While,ast.Assert,ast.IfExp)) else 'value' if isinstance(a,ast.Return) else None
        if field is not None:
            x,y=getattr(a,field),getattr(b,field)
            if x is not None and y is not None and ast.dump(x)!=ast.dump(y):
                left,right=[],[];original,modified=shape(x,left),shape(y,right)
                if original!=modified and [ast.dump(n) for n in left]==[ast.dump(n) for n in right]:
                    changes.append((site(path,x),original,modified))
                    return all(same(getattr(a,k),getattr(b,k)) for k in a._fields if k!=field)
        if isinstance(a,ast.AST):return all(same(getattr(a,k),getattr(b,k)) for k in a._fields)
        if isinstance(a,list):return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
        return a==b
    return changes if same(old,new) and changes else None


def simulate(formula,pattern):
    mode,snapshots,effects=pattern
    state=[0]
    def leaf(index):
        prefix=state[0]
        descriptor=snapshots[prefix][index]
        if descriptor is not None:
            kind,truth=descriptor;origin=('p',index,prefix)
        else:
            if prefix>=len(effects) or effects[prefix][0]!=index:raise ValueError('Unobserved or reordered effect')
            descriptor=effects[prefix][1]
            if descriptor is None:raise ValueError('Unknown truth conversion')
            kind,truth=descriptor;origin=('e',index,prefix);state[0]+=1
        return (kind,bool(truth),origin if kind==2 else None)
    def eval_(node):
        if isinstance(node,int):return leaf(node)
        if node[0]=='Not':return (0,not eval_(node[1])[1],None)
        for child in node[1:]:
            value=eval_(child)
            if (node[0]=='And' and not value[1]) or (node[0]=='Or' and value[1]):return value
        return value
    try:
        value=eval_(formula)
        if state[0]!=len(effects):return None
        if mode=='truth':return (0,value[1],None)
        return value
    except (ValueError,IndexError,TypeError):return None


class FlowIndex:
    def __init__(self,data,numbers):
        self.data,self.numbers=data,numbers;self.collection=data['collection'];self.known=set(self.collection)
        self.valid=data.get('complete_baseline',False) and data.get('flow_format')==1
    def filter(self,path,old,new,selected,protected=()):
        if not self.valid:return None
        changes=changed_roots(path,old,new)
        if not changes or any(token not in self.data['sites'] for token,*_ in changes):return None
        # Snapshot properties are justified by their original read-only source.
        # Never assume that source is unchanged when the patch also edits it.
        for token,*_ in changes:
            line=int(token.split('::',1)[1].split(':',1)[0])
            if any(m['path']==path and m['start']<=line<=m['end'] for m in self.data.get('accessor_dependencies',())):return None
        unsafe=set(protected)
        for token,original,modified in changes:
            cache={}
            for context,profile in self.data['sites'][token].items():
                valid=not profile['unsafe']
                for number in profile['patterns']:
                    if not valid:break
                    if number not in cache:
                        pattern=self.data['patterns'][number]
                        before=simulate(original,pattern);after=simulate(modified,pattern)
                        cache[number]=before is not None and after is not None and before==after
                    valid=cache[number]
                if valid:continue
                if int(context)==-1:return None
                name=self.collection[int(context)]
                if name in self.numbers:unsafe.add(self.numbers[name])
        unknown={n for name,n in self.numbers.items() if name not in self.known}
        return selected & (unsafe|unknown)
