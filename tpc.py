"""Exact checks for the slow TPC executor tests, run in the patched child.

For every fixture query the parts of the computation each test depends on are hashed
(DuckDB SQL, parsed/pickled and optimized trees, plan, generated Python) and compared with
the pinned commit's hashes, recorded while the image builds. Queries whose hash is unchanged
behave exactly as on the pinned commit, where the tests pass. Only the changed queries are
then run for real, through the test module's own code (mp_execute, subtestHelper,
cached_execute) on the fixture data, which the parent loads once with the test's own
setUpClass. A test is dropped only if every changed query passes.
"""
import hashlib
import json
import os
from pathlib import Path
import pickle
import re
import sys

_ADDR = re.compile(r"\(\d+\)")
BASELINE = Path(__file__).with_name('tpc_baseline.json')
EXEC_TPCDS = 'tests/test_executor.py::TestExecutor::test_execute_tpcds'
EXEC_TPCH = 'tests/test_executor.py::TestExecutor::test_execute_tpch'
OPT_TPCH = 'tests/test_executor.py::TestExecutor::test_optimized_tpch'
MAX_NODES = 'tests/test_parser.py::TestParser::test_max_nodes'
# what each test needs: (kind, part)
NEEDS = {EXEC_TPCDS: ('tpcds', 'ex'), EXEC_TPCH: ('tpch', 'ex'), OPT_TPCH: ('tpch', 'tr')}
GATED = (
    'sqlglot/executor/', 'sqlglot/planner.py', 'sqlglot/generators/python.py',
    'sqlglot/helper.py', 'sqlglot/schema.py',
    'tests/test_executor.py', 'tests/helpers.py', 'tests/__init__.py',
    'tests/fixtures/optimizer/tpc-h/', 'tests/fixtures/optimizer/tpc-ds/',
)
MAX_NODES_GATED = ('tests/test_parser.py', 'tests/helpers.py', 'tests/__init__.py')
MAX_RERUN = {'ex': 10, 'tr': 22}   # changed queries we are willing to run for real


def _h(parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode('utf-8', 'surrogatepass'))
        h.update(b'\x00')
    return h.hexdigest()[:32]


def _exprs(value, out):
    from sqlglot import exp
    if isinstance(value, exp.Expr):
        out.append(value)
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            _exprs(v, out)
    elif isinstance(value, dict):
        for k in sorted(value, key=repr):
            _exprs(value[k], out)


def _canon(step, gen, memo):
    key = id(step)
    if key in memo:
        return memo[key]
    lines = [step.type_name, repr(step.name), repr(step.limit), repr(step.offset)]
    lines += [x.sql() for x in step.projections]
    lines.append(step.condition.sql() if step.condition else '')
    lines += [_ADDR.sub('', x) for x in step._to_s('')]
    found = []
    for k in sorted(vars(step)):
        if k in ('dependencies', 'dependents'):
            continue
        lines.append(k)
        _exprs(getattr(step, k), found)
    for x in found:
        try:
            lines.append(gen.generate(x))
        except Exception as err:
            lines.append('ERR ' + type(err).__name__)
    deps = sorted(_canon(d, gen, memo) for d in step.dependencies)
    text = '\n'.join(lines) + '\n[' + '\n,'.join(deps) + ']'
    memo[key] = text
    return text


def _schema_path(kind):
    from tests.helpers import TPCH_SCHEMA, TPCDS_SCHEMA
    if kind == 'tpch':
        return TPCH_SCHEMA, 'optimizer/tpc-h/tpc-h.sql'
    return TPCDS_SCHEMA, 'optimizer/tpc-ds/tpc-ds.sql'


def query_hashes(kind, parts, only=None, base=None):
    """Per fixture query: {'ex': hash or None (not executed), 'tr': hash} for the requested parts.
    With only (a set of indices), 'ex' is computed for those queries and copied from base for the rest."""
    from sqlglot import find_tables, parse_one, transpile
    from sqlglot.optimizer import optimize
    from sqlglot.planner import Plan
    from sqlglot.executor.python import PythonExecutor
    from tests.helpers import load_sql_fixture_pairs
    schema, path = _schema_path(kind)
    gen = PythonExecutor().generator
    out = []
    for qi, (meta, sql, optimized) in enumerate(load_sql_fixture_pairs(path)):
        a = transpile(sql, write='duckdb')[0]
        row = {}
        if 'ex' in parts and only is not None and qi not in only and base is not None and qi < len(base):
            row['ex'] = base[qi].get('ex')
            if 'tr' in parts:
                row['tr'] = _h([repr(sorted(meta.items())), sql, optimized, a, transpile(optimized, write='duckdb')[0]])
            out.append(row)
            continue
        if 'tr' in parts:
            row['tr'] = _h([repr(sorted(meta.items())), sql, optimized, a, transpile(optimized, write='duckdb')[0]])
        if 'ex' in parts:
            if meta.get('execute'):
                e = pickle.loads(pickle.dumps(parse_one(sql)))
                o = optimize(e, schema, leave_tables_isolated=True)
                row['ex'] = _h([repr(sorted(meta.items())), sql, a, repr(sorted(t.name for t in find_tables(e))),
                                repr(o), o.sql(), _canon(Plan(o).root, gen, {})])
            else:
                row['ex'] = None
        out.append(row)
    return out


class Data:
    """The fixture tables as the test's own setUpClass builds them, as plain rows."""
    def __init__(self, root):
        self.root = Path(root)
        self.rows = None

    def load(self):
        if self.rows is not None:
            return self.rows
        os.environ['SKIP_INTEGRATION'] = '0'
        sys.path.insert(0, str(self.root))
        cwd = os.getcwd()
        try:
            os.chdir(self.root)
            import tests.test_executor as te
            te.TestExecutor.setUpClass()
            self.rows = {
                'tpch': {n: (list(t.columns), t.rows) for n, t in te.TestExecutor.tpch_tables.items()},
                'tpcds': {n: (list(t.columns), t.rows) for n, t in te.TestExecutor.tpcds_tables.items()},
            }
            te.TestExecutor.tearDownClass()
        finally:
            os.chdir(cwd)
            for name in list(sys.modules):
                if name == 'sqlglot' or name.startswith('sqlglot.') or name == 'tests' or name.startswith('tests.'):
                    del sys.modules[name]
            sys.path.remove(str(self.root))
        return self.rows


def _test_case(te, kind, rows):
    import duckdb
    from sqlglot.executor.table import Table
    from tests.helpers import load_sql_fixture_pairs
    schema, path = _schema_path(kind)
    cls = te.TestExecutor
    conn = duckdb.connect()
    directory = te.DIR_TPCH if kind == 'tpch' else te.DIR_TPCDS
    for table, columns in schema.items():
        file_name = f"{directory}{table}.csv.gz"
        conn.execute(
            f"""
                CREATE VIEW {table} AS
                SELECT *
                FROM READ_CSV('{file_name}', delim='|', header=True, columns={columns})
                """
        )
    tables = {n: Table(columns=c, rows=r) for n, (c, r) in rows[kind].items()}
    setattr(cls, f'{kind}_conn', conn)
    setattr(cls, f'{kind}_tables', tables)
    setattr(cls, f'{kind}_sqls', list(load_sql_fixture_pairs(path)))
    cls.cache = {}
    return cls(EXEC_TPCDS.split('::')[-1] if kind == 'tpcds' else EXEC_TPCH.split('::')[-1]), schema, tables


def check(wanted, base, rows, tpcds_only=None):
    """Child side. wanted: test ids. Returns {test id: 'pass' | 'keep'}."""
    import importlib
    os.environ['SKIP_INTEGRATION'] = '0'
    te = importlib.import_module('tests.test_executor')  # the module itself must still import
    from sqlglot import parse_one, transpile
    from pandas.testing import assert_frame_equal
    need = {}
    for tid in wanted:
        kind, part = NEEDS[tid]
        need.setdefault(kind, set()).add(part)
    hashes = {k: query_hashes(k, parts, only=(tpcds_only if k == 'tpcds' else None), base=base.get(k)) for k, parts in need.items()}
    result = {}
    for tid in wanted:
        kind, part = NEEDS[tid]
        now = hashes[kind]
        was = base.get(kind, [])
        if len(now) != len(was):
            result[tid] = 'keep'
            continue
        changed = [i for i, (n, w) in enumerate(zip(now, was)) if n.get(part) != w.get(part)]
        if not changed:
            result[tid] = 'pass'
            continue
        if part == 'ex':
            changed = [i for i in changed if now[i]['ex'] is not None or was[i].get('ex') is not None]
            if any(now[i]['ex'] is None or was[i].get('ex') is None for i in changed) or len(changed) > MAX_RERUN['ex']:
                result[tid] = 'keep'
                continue
        elif len(changed) > MAX_RERUN['tr']:
            result[tid] = 'keep'
            continue
        try:
            tc, schema, tables = _test_case(te, kind, rows)
            sqls = getattr(te.TestExecutor, f'{kind}_sqls')
            if part == 'ex':
                te.initializer(schema, tables)
                for i in changed:
                    meta, sql, _ = sqls[i]
                    table = te.mp_execute(pickle.loads(pickle.dumps(parse_one(sql))), meta)
                    table = pickle.loads(pickle.dumps(table))
                    if table is not None:
                        tc.subtestHelper(i, table, tpch=(kind == 'tpch'))
            else:
                conn = getattr(te.TestExecutor, f'{kind}_conn')
                for i in changed:
                    _, sql, optimized = sqls[i]
                    a = tc.cached_execute(sql, tpch=True)
                    b = conn.execute(transpile(optimized, write='duckdb')[0]).fetchdf()
                    tc.rename_anonymous(b, a)
                    assert_frame_equal(a, b)
            result[tid] = 'pass'
        except BaseException:
            result[tid] = 'keep'
    return result


def max_nodes_check():
    """test_max_nodes at one tenth of its width; node counts scale exactly, so every limit
    is exceeded (or not) as in the full test. All other assertions are unchanged."""
    import importlib
    importlib.import_module('tests.test_parser')
    from sqlglot import exp, parse_one
    from sqlglot.errors import ParseError
    if not isinstance(parse_one('SELECT 1', max_nodes=1_000_000), exp.Select):
        return False
    cols = [f'col{i}' for i in range(500)]
    if not isinstance(parse_one(f"SELECT {','.join(cols)} FROM t", max_nodes=1_000_000), exp.Select):
        return False
    width, depth = 1000, 100
    expr = 'x' + '=x' * depth
    sql = f"SELECT {','.join([expr] * width)}"
    try:
        parse_one(sql, max_nodes=100_000)
        return False
    except ParseError as error:
        if 'Maximum number of AST nodes' not in str(error):
            return False
    try:
        parse_one(sql, max_nodes=5_000)
        return False
    except ParseError as error:
        if '(5000)' not in str(error):
            return False
    try:
        parse_one('SELECT ' + ','.join(f'x={i}' for i in range(100)), max_nodes=5)
        return False
    except ParseError:
        pass
    return isinstance(parse_one('SELECT 1, 2, 3'), exp.Select)


class StandIn:
    def __init__(self, prober, tests, root):
        self.prober = prober
        self.number = {t['id']: t['number'] for t in tests}
        self.base = json.loads(BASELINE.read_text()) if BASELINE.is_file() else {}
        self.data = Data(root)
        self.spans = None

    def apply(self, patch_text, changes, selected):
        if changes is None or not self.base:
            return selected
        selected = self._max_nodes(changes, selected)
        wanted = [tid for tid in NEEDS if self.number.get(tid) in selected]
        if not wanted or any(p.startswith(GATED) for p in changes):
            return selected
        try:
            rows = self.data.load()
        except Exception as error:
            print(f'TPC data unavailable: {type(error).__name__}', file=sys.stderr)
            return selected
        base = self.base
        only = None
        if 'owners' in base and EXEC_TPCDS in wanted:
            try:
                if self.spans is None:
                    stored = base.get('spans')
                    self.spans = {k: [tuple(s) for s in v] for k, v in stored.items()} if stored else _owner_index(self.data.root)
                hit = affected_queries(patch_text, self.spans, base['owners'])
                only = None if hit is None else set(hit)
            except Exception as error:
                print(f'TPC owners unavailable: {type(error).__name__}', file=sys.stderr)
                only = None
        got = self.prober.call(changes, lambda: check(wanted, base, rows, only), seconds=30)
        if not isinstance(got, dict):
            return selected
        drop = {self.number[tid] for tid, v in got.items() if v == 'pass'}
        print(f'TPC check: {got}', file=sys.stderr)
        return selected - drop

    def _max_nodes(self, changes, selected):
        n = self.number.get(MAX_NODES)
        if n not in selected or any(p.startswith(MAX_NODES_GATED) for p in changes):
            return selected
        ok = self.prober.call(changes, max_nodes_check, seconds=15)
        print(f'max_nodes check: {"pass" if ok is True else "keep"}', file=sys.stderr)
        return selected - {n} if ok is True else selected


def main():
    # build time: record the pinned per-query hashes (run from the pinned source tree)
    root = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get('SQLGLOT_SOURCE', '/opt/sqlglot'))
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ['SKIP_INTEGRATION'] = '0'
    owners = build_owners(root)  # first: needs a process that has not imported sqlglot yet
    data = {'tpch': query_hashes('tpch', {'ex', 'tr'}), 'tpcds': query_hashes('tpcds', {'ex'}), 'owners': owners,
            'spans': {k: [list(s) for s in v] for k, v in _owner_index(root).items()}}
    BASELINE.write_text(json.dumps(data) + '\n')
    print(f'TPC baseline written: {BASELINE.stat().st_size} bytes', file=sys.stderr)



# ---- which sqlglot functions each TPC-DS query runs through (baseline) -------------------

def _owner_index(root):
    """path -> sorted list of (start, end, body_start) for outermost function defs."""
    import ast
    idx = {}
    for f in (Path(root) / 'sqlglot').rglob('*.py'):
        rel = str(f.relative_to(root))
        try:
            tree = ast.parse(f.read_text(encoding='utf-8'))
        except (SyntaxError, UnicodeError):
            continue
        spans = []

        def visit(node, inside):
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)) and not inside:
                    body_start = ch.body[0].lineno
                    # a decorated function's code starts at its first decorator
                    first = min([ch.lineno] + [d.lineno for d in ch.decorator_list])
                    spans.append((first, ch.end_lineno, body_start))
                    visit(ch, True)
                elif isinstance(ch, ast.ClassDef) and not inside:
                    visit(ch, False)
                else:
                    visit(ch, inside)
        visit(tree, False)
        spans.sort()
        idx[rel] = spans
    return idx


def _owner_code(spans, first_line):
    """Outermost function whose span holds a code object starting at first_line."""
    for start, end, body_start in spans:
        if start <= first_line <= end:
            return start
    return None


def _owner(spans, line):
    """Outermost function containing line, as its start line, or None (module/class level, def line, decorator)."""
    for start, end, body_start in spans:
        if start <= line <= end:
            return start if line >= body_start else None
    return None


def build_owners(root):
    """Call profiles of the pinned source: functions run while importing, and per TPC-DS query
    the functions its computation runs, each query profiled in a fresh fork with cold caches."""
    root = Path(root).resolve()
    idx = _owner_index(root)
    prefix = str(root / 'sqlglot') + os.sep

    def owners_of(codes):
        out = set()
        for fn, line in codes:
            full = os.path.realpath(fn if os.path.isabs(fn) else os.path.join(root, fn))
            rel = os.path.relpath(full, root)
            if not rel.startswith('sqlglot' + os.sep):
                continue
            o = _owner_code(idx.get(rel, []), line)
            if o is not None:
                out.add(f'{rel}:{o}')
        return out

    seen = set()

    def prof(frame, event, arg):
        if event == 'call':
            c = frame.f_code
            if 'sqlglot' in c.co_filename:
                seen.add((c.co_filename, c.co_firstlineno))
    assert not any(m == 'sqlglot' or m.startswith('sqlglot.') for m in sys.modules), 'profile before importing sqlglot'
    sys.setprofile(prof)
    import sqlglot  # noqa: F401  (import phase)
    from sqlglot import find_tables, parse_one, transpile
    from sqlglot.optimizer import optimize
    from sqlglot.planner import Plan
    from sqlglot.executor.python import PythonExecutor
    from tests.helpers import load_sql_fixture_pairs, TPCDS_SCHEMA
    sys.setprofile(None)
    imported = owners_of(seen)
    pairs = list(load_sql_fixture_pairs('optimizer/tpc-ds/tpc-ds.sql'))
    per_query = []
    for meta, sql, optimized in pairs:
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(r)
            seen.clear()
            sys.setprofile(prof)
            try:
                gen = PythonExecutor().generator
                transpile(sql, write='duckdb')
                if meta.get('execute'):
                    e = pickle.loads(pickle.dumps(parse_one(sql)))
                    o = optimize(e, TPCDS_SCHEMA, leave_tables_isolated=True)
                    find_tables(e)
                    repr(o); o.sql()
                    _canon(Plan(o).root, gen, {})
            finally:
                sys.setprofile(None)
            data = json.dumps(sorted(owners_of(seen))).encode()
            while data:
                n = os.write(w, data); data = data[n:]
            os.close(w)
            os._exit(0)
        os.close(w)
        buf = b''
        while True:
            chunk = os.read(r, 1 << 16)
            if not chunk:
                break
            buf += chunk
        os.close(r)
        os.waitpid(pid, 0)
        per_query.append(json.loads(buf))
    return {'import': sorted(imported), 'query': per_query}


def affected_queries(patch_text, spans, owners):
    """Indices of TPC-DS queries whose baseline call profile includes a function the patch edits,
    or None when any edit is outside a function body (then every query must be recomputed)."""
    from selector import parse_patch
    imported = set(owners['import'])
    changed = set()
    for c in parse_patch(patch_text):
        path = c.old
        if not path.startswith('sqlglot/'):
            continue
        if not path.endswith('.py') or path not in spans:
            return None
        lines = set(c.removed) | set(c.inserted_at) | {x - 1 for x in c.inserted_at}
        for line in lines:
            o = _owner(spans[path], line)
            if o is None:
                return None
            key = f'{path}:{o}'
            if key in imported:
                return None
            changed.add(key)
    return [i for i, q in enumerate(owners['query']) if changed & set(q)]


if __name__ == '__main__':
    main()
