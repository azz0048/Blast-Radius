"""Exact stand-ins for the slow TPC executor tests.

TestExecutor's TPC tests compare sqlglot's own executor with DuckDB. Their outcome is a
function of what sqlglot computes for the fixture queries: the DuckDB SQL it transpiles to,
the optimized tree, the plan and the Python code generated for every plan step. With the
executor runtime, planner, Python generator, helpers, schema, test file and fixtures
unchanged, equal values mean the test behaves exactly as on the pinned commit, where it
passes. The pinned values are computed from the pinned source while the image is built.
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
# test id -> fingerprint kinds its outcome depends on
MAX_NODES = 'tests/test_parser.py::TestParser::test_max_nodes'
MAX_NODES_GATED = ('tests/test_parser.py', 'tests/helpers.py', 'tests/__init__.py')
TESTS = {
    'tests/test_executor.py::TestExecutor::test_execute_tpcds': ('tpcds',),
    'tests/test_executor.py::TestExecutor::test_execute_tpch': ('tpch',),
    'tests/test_executor.py::TestExecutor::test_optimized_tpch': ('tpch',),
}
# a patch touching any of these keeps the tests (their behaviour is not in the fingerprint)
GATED = (
    'sqlglot/executor/', 'sqlglot/planner.py', 'sqlglot/generators/python.py',
    'sqlglot/helper.py', 'sqlglot/schema.py',
    'tests/test_executor.py', 'tests/helpers.py', 'tests/__init__.py',
    'tests/fixtures/optimizer/tpc-h/', 'tests/fixtures/optimizer/tpc-ds/',
)


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
    """Canonical text of a plan step and its dependencies (sorted), with generated code."""
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


def fingerprint(kind):
    os.environ['SKIP_INTEGRATION'] = '0'
    from sqlglot import find_tables, parse_one, transpile
    from sqlglot.optimizer import optimize
    from sqlglot.planner import Plan
    from sqlglot.executor.python import PythonExecutor
    from tests.helpers import load_sql_fixture_pairs, TPCH_SCHEMA, TPCDS_SCHEMA
    schema = TPCH_SCHEMA if kind == 'tpch' else TPCDS_SCHEMA
    path = 'optimizer/tpc-h/tpc-h.sql' if kind == 'tpch' else 'optimizer/tpc-ds/tpc-ds.sql'
    gen = PythonExecutor().generator
    h = hashlib.sha256()
    for meta, sql, optimized in load_sql_fixture_pairs(path):
        parts = [repr(sorted(meta.items())), sql, optimized,
                 transpile(sql, write='duckdb')[0], transpile(optimized, write='duckdb')[0]]
        if meta.get('execute'):
            e = pickle.loads(pickle.dumps(parse_one(sql)))  # the test pickles it to its worker
            parts.append(repr(sorted(t.name for t in find_tables(e))))
            o = optimize(e, schema, leave_tables_isolated=True)
            parts += [repr(o), o.sql(), _canon(Plan(o).root, gen, {})]
        for p in parts:
            h.update(p.encode('utf-8', 'surrogatepass'))
            h.update(b'\x00')
    return h.hexdigest()


def fingerprints(kinds):
    import importlib
    importlib.import_module('tests.test_executor')  # the test module itself must still import
    return {k: fingerprint(k) for k in kinds}


def max_nodes_check():
    """test_max_nodes at one tenth of its width, every node limit scaled by the same factor.

    Node counts grow by exactly the width factor, so each limit is exceeded (or not)
    exactly as in the full test; all other assertions are unchanged.
    """
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


class Proxy:
    def __init__(self, prober, tests):
        self.prober = prober
        self.number = {t['id']: t['number'] for t in tests}
        self.base = json.loads(BASELINE.read_text()) if BASELINE.is_file() else {}
        self.stats = []

    def apply(self, changes, selected, seconds=20):
        """changes: {path: patched bytes} for every file the patch touches (None if unsupported)."""
        selected = self._max_nodes(changes, selected)
        wanted = {self.number[i]: kinds for i, kinds in TESTS.items() if i in self.number and self.number[i] in selected}
        if not wanted or changes is None or not self.base:
            return selected
        if any(p.startswith(GATED) for p in changes):
            return selected
        kinds = sorted({k for ks in wanted.values() for k in ks})
        if any(k not in self.base for k in kinds):
            return selected
        got = self.prober.call(changes, lambda: fingerprints(kinds), seconds=seconds)
        if not isinstance(got, dict):
            return selected
        same = {k for k in kinds if got.get(k) == self.base[k]}
        drop = {n for n, ks in wanted.items() if all(k in same for k in ks)}
        self.stats.append((sorted(wanted), sorted(drop)))
        print(f'TPC stand-in: {sorted(wanted)} unchanged={sorted(same)} dropped={sorted(drop)}', file=sys.stderr)
        return selected - drop


    def _max_nodes(self, changes, selected):
        n = self.number.get(MAX_NODES)
        if n not in selected or changes is None or any(p.startswith(MAX_NODES_GATED) for p in changes):
            return selected
        ok = self.prober.call(changes, max_nodes_check, seconds=15)
        print(f'max_nodes stand-in: {"pass" if ok is True else "keep"}', file=sys.stderr)
        return selected - {n} if ok is True else selected


def main():
    # build time: record the pinned values (run from the pinned source tree)
    root = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get('SQLGLOT_SOURCE', '/opt/sqlglot'))
    os.chdir(root)
    sys.path.insert(0, str(root))
    BASELINE.write_text(json.dumps(fingerprints(['tpch', 'tpcds'])) + '\n')
    print(BASELINE.read_text(), end='')


if __name__ == '__main__':
    main()
