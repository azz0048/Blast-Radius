"""Observed baseline literal-comparison operands, never mutant answers."""
import ast
import math
import operator

FUNCTION_NAME = '__br_baseline_literal_values__'
OPS = {c.__name__: f for c, f in [(ast.Eq, operator.eq), (ast.NotEq, operator.ne),
       (ast.Lt, operator.lt), (ast.LtE, operator.le), (ast.Gt, operator.gt), (ast.GtE, operator.ge)]}
TYPES = {int:'int', float:'float', str:'str', bytes:'bytes', bool:'bool', type(None):'none',tuple:'tuple-int'}


def literal(node):
    if isinstance(node, (ast.Constant,ast.Tuple)) or (isinstance(node, ast.UnaryOp) and
            isinstance(node.op, (ast.UAdd, ast.USub)) and isinstance(node.operand, ast.Constant)):
        try:
            value = ast.literal_eval(node)
            if encode(value) is not None:
                return True, value
        except (ValueError, TypeError):
            pass
    return False, None


def comparison(node):
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or type(node.ops[0]).__name__ not in OPS:
        return None
    left, right = literal(node.left), literal(node.comparators[0])
    if left[0] == right[0]:
        return None
    return (0, left[1], node.comparators[0]) if left[0] else (1, right[1], node.left)


def site_key(path, node):
    return f'{path}::{node.lineno}:{node.col_offset}:{node.end_lineno}:{node.end_col_offset}'


def encode(value):
    kind = TYPES.get(type(value))
    if kind is None or (kind == 'float' and not math.isfinite(value)):
        return None
    if kind == 'int' and value.bit_length() > 512:
        return None
    if kind in {'str', 'bytes'} and len(value) > 256:
        return None
    if kind == 'tuple-int' and (len(value)>8 or not all(type(v) is int and v.bit_length()<=512 for v in value)):
        return None
    return kind, value.hex() if kind == 'bytes' else value


def decode(kind, value):
    if kind=='tuple-int':return tuple(value)
    return bytes.fromhex(value) if kind == 'bytes' else value


class InstrumentValues(ast.NodeTransformer):
    def __init__(self, path): self.path = path
    def visit_Compare(self, node):
        eligible = comparison(node)
        self.generic_visit(node)
        if eligible is None:
            return node
        call = ast.Call(func=ast.Name(id=FUNCTION_NAME, ctx=ast.Load()), args=[
            ast.Constant(site_key(self.path, node)), ast.Constant(type(node.ops[0]).__name__),
            ast.Constant(eligible[0]), node.left, node.comparators[0]], keywords=[])
        return ast.copy_location(call, node)


def changed_comparisons(path, old, new):
    edits = []
    def same(a, b):
        x, y = comparison(a), comparison(b)
        if x is not None and y is not None and x[0] == y[0] and ast.dump(x[2]) == ast.dump(y[2]):
            if type(a.ops[0]) != type(b.ops[0]) or type(x[1]) != type(y[1]) or x[1] != y[1]:
                edits.append((site_key(path, a), type(b.ops[0]).__name__, y[0], y[1]))
                return True
        if type(a) is not type(b): return False
        if isinstance(a, ast.AST): return all(same(getattr(a,k), getattr(b,k)) for k in a._fields)
        if isinstance(a, list): return len(a) == len(b) and all(same(x,y) for x,y in zip(a,b))
        return a == b
    return edits if same(old, new) and edits else None


def range_preserves(kind, low, high, expected, op, side, constant):
    low, high = decode(kind, low), decode(kind, high)
    fn = OPS[op]
    try:
        outcomes = [fn(constant, v) if side == 0 else fn(v, constant) for v in (low, high)]
        if any(type(v) is not bool or v != expected for v in outcomes): return False
        # Equality can differ at an interior value even if both endpoints pass.
        if (op == 'Eq' and not expected) or (op == 'NotEq' and expected):
            if low is None or high is None:
                return constant is not None
            try:
                if low <= constant <= high: return False
            except TypeError:
                # Different exact builtin kinds have no possible equality,
                # except bool/int/float, whose ordering is defined above.
                numeric = {'int', 'float', 'bool'}
                if kind in numeric and type(constant) in (int,float,bool): return False
        return True
    except (TypeError, ValueError, OverflowError):
        return False


class ValueIndex:
    def __init__(self, data, numbers):
        self.data, self.numbers = data, numbers
        self.valid = data.get('complete_baseline', False)
        self.collection = data['collection']
        self.known = set(self.collection)
    def filter(self, path, old, new, selected, protected=()):
        if not self.valid: return None
        edits = changed_comparisons(path, old, new)
        if not edits or any(site not in self.data['sites'] for site, *_ in edits): return None
        unsafe = set(protected)
        for site, op, side, constant in edits:
            for context, profile in self.data['sites'][site].items():
                valid = not profile['unsafe'] and all(range_preserves(
                    kind, low, high, bool(truth), op, side, constant)
                    for kind, truth, low, high in profile['ranges'])
                if valid: continue
                if int(context) == -1: return None
                name = self.collection[int(context)]
                if name in self.numbers: unsafe.add(self.numbers[name])
        unknown = {n for name,n in self.numbers.items() if name not in self.known}
        return selected & (unsafe | unknown)
