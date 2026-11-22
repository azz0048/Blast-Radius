"""Baseline operand-type metadata and conservative comparison equivalence.

The collector observes only the original operation. It never evaluates mutated
operators or stores mutation answers. Runtime proofs use Python type semantics.
"""
from __future__ import annotations
import ast
import enum
import inspect
import operator


FUNCTION_NAME='__br_baseline_operand_types__'
OPERATORS={ast.Eq:operator.eq,ast.NotEq:operator.ne,ast.Is:operator.is_,ast.IsNot:operator.is_not}

def operand_trait(value,trait,canonical_ids):
    if canonical_ids is not None and id(value) not in canonical_ids:
        return 'unknown:noncanonical:'+trait
    return trait


def site_key(path,node):
    return f'{path}::{node.lineno}:{node.col_offset}:{node.end_lineno}:{node.end_col_offset}'


def type_trait(cls):
    name=type.__getattribute__(cls,'__module__')+'.'+type.__getattribute__(cls,'__qualname__')
    eq=inspect.getattr_static(cls,'__eq__',None)
    ne=inspect.getattr_static(cls,'__ne__',None)
    if cls is type(None):return 'none'
    if eq is object.__eq__ and ne is object.__ne__:return 'identity:'+name
    if issubclass(cls,enum.Enum):
        factory=inspect.getattr_static(cls,'_new_member_',None)
        if eq is str.__eq__ and ne is str.__ne__ and factory is str.__new__:return 'enum-str:'+name
        if eq is int.__eq__ and ne is int.__ne__ and factory is int.__new__:return 'enum-int:'+name
    if cls in (str,int,float,complex,bool,bytes,list,tuple,dict,set,frozenset,range):
        return 'none-safe:'+name
    return 'unknown:'+name


def equality_is_identity(left,right,inequality=False):
    if left=='none':
        prefixes=('identity:','none-safe:','enum-str:','enum-int:', 'ne-none-safe:' if inequality else 'eq-none-safe:')
        return right=='none' or right.startswith(prefixes)
    if right=='none':return equality_is_identity(right,left,inequality)
    if left.startswith('identity:') and right.startswith('identity:'):return True
    return left==right and left.startswith(('enum-str:','enum-int:'))


class InstrumentComparisons(ast.NodeTransformer):
    def __init__(self,path):self.path=path
    def visit_Compare(self,node):
        self.generic_visit(node)
        if len(node.ops)!=1 or type(node.ops[0]) not in OPERATORS:return node
        call=ast.Call(func=ast.Name(id=FUNCTION_NAME,ctx=ast.Load()),
                      args=[ast.Constant(site_key(self.path,node)),ast.Constant(type(node.ops[0]).__name__),
                            node.left,node.comparators[0]],keywords=[])
        return ast.copy_location(call,node)


def changed_identity_comparisons(old,new,path):
    changed=[]
    pairs={(ast.Is,ast.Eq),(ast.Eq,ast.Is),(ast.IsNot,ast.NotEq),(ast.NotEq,ast.IsNot)}
    def same(a,b):
        if isinstance(a,ast.Compare) and isinstance(b,ast.Compare) and len(a.ops)==len(b.ops)==1:
            if (type(a.ops[0]),type(b.ops[0])) in pairs:
                if ast.dump(a.left)!=ast.dump(b.left) or ast.dump(a.comparators[0])!=ast.dump(b.comparators[0]):return False
                changed.append(site_key(path,a));return True
        if type(a) is not type(b):return False
        if isinstance(a,ast.AST):
            return all(same(getattr(a,k),getattr(b,k)) for k in a._fields)
        if isinstance(a,list):return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
        return a==b
    return changed if same(old,new) and changed else None


class ComparisonIndex:
    def __init__(self,data,numbers):
        self.profiles=data['sites'];self.numbers=numbers
        eq_types=set(data.get('safe_eq_none_types',()));ne_types=set(data.get('safe_ne_none_types',()))
        def refine(tag):
            if tag.startswith(('enum-str:','enum-int:')) and not data.get('canonical_enum_profiles'):
                return 'unknown:unverified-member:'+tag
            if not tag.startswith('unknown:'):return tag
            name=tag[len('unknown:'):]
            if name in eq_types and name in ne_types:return 'none-safe:'+name
            if name in eq_types:return 'eq-none-safe:'+name
            if name in ne_types:return 'ne-none-safe:'+name
            return tag
        self.original_patterns=[tuple('unknown:unverified-member:'+tag if tag.startswith(('enum-str:','enum-int:')) and not data.get('canonical_enum_profiles') else tag for tag in pair) for pair in data['patterns']]
        self.patterns=[tuple(refine(tag) for tag in pair) for pair in data['patterns']]
        self.protected_methods=data.get('protected_methods',[])
        self.collection=data['collection'];self.known=set(self.collection)
        self.valid=data.get('complete_baseline',False)
    def filter(self,path,old,new,selected,protected=()):
        if not self.valid:return None
        sites=changed_identity_comparisons(old,new,path)
        if not sites or any(site not in self.profiles for site in sites):return None
        unsafe=set()
        # A property's implementation must remain unchanged for a proof that
        # depends on it. In particular, never reason about a changed __eq__ by
        # assuming that its original treatment of None still holds.
        patterns=self.patterns;implementation_changed=False
        for site in sites:
            line=int(site.split('::',1)[1].split(':',1)[0])
            if any(m['path']==path and m['start']<=line<=m['end'] for m in self.protected_methods):
                patterns=self.original_patterns;implementation_changed=True;break
        modes={site_key(path,n):isinstance(n.ops[0],(ast.NotEq,ast.IsNot)) for n in ast.walk(old)
               if isinstance(n,ast.Compare) and len(n.ops)==1}
        for site in sites:
            for pattern,contexts in self.profiles[site].items():
                left,right=patterns[int(pattern)]
                if equality_is_identity(left,right,modes[site]):continue
                if implementation_changed:return None
                if -1 in contexts:return None
                unsafe.update(self.numbers[self.collection[i]] for i in contexts
                              if self.collection[i] in self.numbers)
        unknown={number for name,number in self.numbers.items() if name not in self.known}
        return selected & (unsafe|unknown|set(protected))
