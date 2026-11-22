"""Select tests from coverage of the unmodified pinned source, never sample answers."""
from __future__ import annotations
import argparse
import ast
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
import gzip
import json
from pathlib import Path
import re
import sys
import os
import time

HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')

@dataclass
class Change:
    old: str = ''
    new: str = ''
    hunks: list = field(default_factory=list)
    removed: set = field(default_factory=set)
    inserted_at: set = field(default_factory=set)

def clean_path(text):
    text = text.split('\t', 1)[0].strip()
    if text.startswith('"'):
        text = json.loads(text)
    return text[2:] if text.startswith(('a/', 'b/')) else text

def parse_patch(text):
    changes, current, hunk, oldline, headers = [], None, None, 0, True
    edit_anchor = None
    for raw in text.splitlines(keepends=True):
        if raw.startswith('diff --git '):
            current = Change()
            changes.append(current)
            hunk, headers = None, True
            edit_anchor = None
        elif headers and raw.startswith('--- '):
            if current is None:
                current = Change()
                changes.append(current)
            current.old = clean_path(raw[4:])
        elif headers and raw.startswith('+++ '):
            current.new = clean_path(raw[4:])
        elif raw.startswith('@@ '):
            match = HUNK.match(raw)
            if match is None or current is None:
                raise ValueError('Malformed unified hunk')
            start, count, _, _ = match.groups()
            hunk = [int(start), int(count) if count is not None else 1, []]
            current.hunks.append(hunk)
            oldline, headers = int(start) + (hunk[1] == 0), False
            edit_anchor = None
        elif hunk is not None and raw.startswith((' ', '+', '-')):
            hunk[2].append(raw)
            if raw[0] == '-':
                if edit_anchor is None:
                    edit_anchor = oldline
                current.removed.add(oldline)
                oldline += 1
            elif raw[0] == '+':
                current.inserted_at.add(max(1, edit_anchor if edit_anchor is not None else oldline))
            else:
                oldline += 1
                edit_anchor = None
        elif hunk is not None and raw.startswith('\\ No newline') and hunk[2]:
            hunk[2][-1] = hunk[2][-1].rstrip('\r\n')
    if not changes or any(not (c.old or c.new) for c in changes):
        raise ValueError('No supported textual diff')
    return changes

def apply_change(source, change):
    original, result, cursor = source.splitlines(keepends=True), [], 0
    for start, count, operations in change.hunks:
        first = start if count == 0 else max(0, start - 1)
        if first < cursor or first > len(original):
            raise ValueError('Overlapping hunk')
        result.extend(original[cursor:first])
        cursor, consumed = first, 0
        for operation in operations:
            sign, payload = operation[0], operation[1:]
            if sign in ' -':
                if cursor >= len(original) or original[cursor].rstrip('\r\n') != payload.rstrip('\r\n'):
                    raise ValueError('Patch context differs from pinned source')
                if sign == ' ':
                    result.append(original[cursor])
                cursor += 1
                consumed += 1
            else:
                result.append(payload)
        if consumed != count:
            raise ValueError('Hunk length mismatch')
    result.extend(original[cursor:])
    return ''.join(result)

class ImpactIndex:
    def __init__(self, data, tests, usage=None, attributes=None, dictionary_data=None, comparison_data=None):
        self.all = {t['number'] for t in tests}
        self.numbers = {t['id']: t['number'] for t in tests}
        self.baseline_numbers = [self.numbers.get(nodeid) for nodeid in data.get('collection', [])]
        self.unknown_tests = ({number for name,number in self.numbers.items() if name not in set(data['collection'])}
                              if 'collection' in data else set())
        self.usage_classes = {}
        self.usage_symbols = {}
        if usage:
            catalogue = usage['collection']
            self.usage_classes = {key: {self.numbers[catalogue[i]] for i in ids if catalogue[i] in self.numbers} for key, ids in usage['classes'].items()}
            self.usage_symbols = {key: {self.numbers[catalogue[i]] for i in ids if catalogue[i] in self.numbers} for key, ids in usage['symbols'].items()}
        self.attribute_reads = {}
        self.attribute_references = {}
        self.unsafe_attributes = set()
        if attributes:
            catalogue = attributes['collection']
            self.attribute_reads = {key: {self.numbers[catalogue[i]] for i in ids if catalogue[i] in self.numbers} for key, ids in attributes['reads'].items()}
            self.attribute_references = {key: {self.numbers[catalogue[i]] for i in ids if catalogue[i] in self.numbers} for key, ids in attributes.get('references', {}).items()}
            self.unsafe_attributes = set(attributes['unsafe_fields'])
        self.dictionary_data = dictionary_data
        self.dictionary_wrapped = set()
        self.dictionary_reads = {}
        self.dictionary_wildcards = {}
        self.dictionary_unsafe_keys = set()
        self.dictionary_unsafe_fields = set()
        self.dictionary_pure_refs = set()
        if dictionary_data:
            catalogue = dictionary_data['collection']
            self.dictionary_wrapped = set(dictionary_data['wrapped'])
            self.dictionary_unsafe_keys = set(dictionary_data['unsafe_keys'])
            self.dictionary_unsafe_fields = set(dictionary_data['unsafe_fields'])
            self.dictionary_pure_refs = set(dictionary_data.get('pure_references',()))
            self.dictionary_reads = {key: {self.numbers[catalogue[i]] for i in ids if catalogue[i] in self.numbers} for key, ids in dictionary_data['reads'].items()}
            self.dictionary_wildcards = {key: {self.numbers[catalogue[i]] for i in ids if catalogue[i] in self.numbers} for key, ids in dictionary_data['wildcards'].items()}
        self.by_file = {}
        for test in tests:
            self.by_file.setdefault(test['file'], set()).add(test['number'])
        self.sources = data['sources']
        self.lines = {p: {int(n): self.translate(ids) for n, ids in m.items()} for p, m in data['lines'].items()}
        self.modules = {p: self.translate(ids) for p, ids in data['modules'].items()}
        self.reads = {p: self.translate(ids) for p, ids in data['reads'].items()}
        self.line_keys = {p: sorted(m) for p, m in self.lines.items()}
        self.trees = {}
        self.witnesses = []
        self.conservative = False
        self.import_sensitive = False
        self.comparisons = None
        if comparison_data:
            from comparison_profiles import ComparisonIndex
            self.comparisons = ComparisonIndex(comparison_data,self.numbers)
    def fallback(self):
        self.conservative = True
        return self.all.copy()
    def translate(self, ids):
        if ids and isinstance(ids[0], int):
            return {self.baseline_numbers[i] for i in ids if self.baseline_numbers[i] is not None}
        return {self.numbers[i] for i in ids if i in self.numbers}
    def tree(self, path):
        if path not in self.trees:
            self.trees[path] = ast.parse(self.sources[path].encode('utf-8'), filename=path, type_comments=True)
        return self.trees[path]
    def interval(self, path, lo, hi):
        mapping, keys, selected = self.lines.get(path, {}), self.line_keys.get(path, []), set()
        for line in keys[bisect_left(keys, lo):bisect_right(keys, hi)]:
            selected.update(mapping[line])
        return selected
    def select_python(self, change):
        path = change.old if change.old != '/dev/null' else change.new
        original = self.sources.get(path)
        if original is None or change.new == '/dev/null' or change.old != change.new:
            return self.fallback()
        modified, old_tree = apply_change(original, change), self.tree(path)
        try:
            new_tree = ast.parse(modified.encode('utf-8'), filename=change.new or path, type_comments=True)
            compile(new_tree, change.new or path, 'exec', dont_inherit=True)
        except (SyntaxError, ValueError, TypeError):
            return self.fallback()
        # Parse bytes (including encoding declarations) and type comments before
        # comparing executable structure. Resource consumers remain dependencies.
        if ast.dump(old_tree, include_attributes=False) == ast.dump(new_tree, include_attributes=False):
            return self.reads.get(path, set()).copy()
        selected = self.by_file.get(path, set()).copy()
        selected.update(self.reads.get(path, ()))
        mapping = self.lines.get(path, {})
        nodes = [n for n in ast.walk(old_tree) if isinstance(n, ast.stmt)]
        functions = [n for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        probes = set(change.removed)
        for line in change.inserted_at:
            if line not in change.removed:
                probes.update((line, max(1, line - 1)))
        unresolved = False
        measured_empty = False
        for line in sorted(probes):
            enclosing = [n for n in functions if n.lineno < line <= n.end_lineno]
            if any(n.name in {'__eq__','__ne__'} for n in enclosing):
                return self.fallback()
            if enclosing and any(line < n.body[0].lineno for n in enclosing if n.body):
                return self.fallback()
            if not enclosing:
                self.import_sensitive = True
                classes = [n for n in nodes if isinstance(n, ast.ClassDef) and n.lineno < line <= n.end_lineno]
                classes.sort(key=lambda n: n.lineno)
                if not classes or not self.usage_classes:
                    return self.fallback()
                qualname = '.'.join(n.name for n in classes)
                key = path + '::' + qualname
                constructor_users = self.usage_classes.get(key, set())
                reference_users = self.usage_symbols.get(classes[0].name, set())
                narrowed = None
                declarations = [statement for statement in classes[-1].body
                                if isinstance(statement, (ast.Assign, ast.AnnAssign))
                                and statement.lineno <= line <= statement.end_lineno]
                if declarations:
                    declaration = declarations[0]
                    if isinstance(declaration.value, ast.Dict) and self.dictionary_data:
                        from dictionary_impact import choose
                        narrowed = choose(self, path, qualname, declaration, new_tree)
                    value = declaration.value
                    if isinstance(value, ast.UnaryOp):
                        value = value.operand
                    targets = declaration.targets if isinstance(declaration, ast.Assign) else [declaration.target]
                    field_names = [target.id for target in targets if isinstance(target, ast.Name)]
                    if isinstance(value, ast.Constant) and field_names:
                        field_groups = []
                        for field_name in field_names:
                            field_key = key + '::' + field_name
                            if field_name in self.unsafe_attributes or field_key not in self.attribute_reads:
                                break
                            field_groups.append(self.attribute_reads[field_key] | self.attribute_references.get(field_name, set()))
                        else:
                            narrowed = set().union(*field_groups)
                group = narrowed if narrowed is not None else constructor_users | reference_users
                if narrowed is not None:
                    measured_empty = True
                self.witnesses.append(group)
                selected.update(group)
                if narrowed is None:
                    selected.update(mapping.get(line, ()))
                continue
            statements = [n for n in nodes if n.lineno <= line <= getattr(n, 'end_lineno', n.lineno)
                          and not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            if statements:
                statement = min(statements, key=lambda n: getattr(n, 'end_lineno', n.lineno) - n.lineno)
                # Include entry into a multiline condition, even when its changed
                # operand was short-circuited in the unmodified revision.
                keys = self.line_keys.get(path, [])
                first = bisect_left(keys, statement.lineno)
                if first < len(keys) and keys[first] <= getattr(statement, 'end_lineno', statement.lineno):
                    self.witnesses.append(mapping[keys[first]])
                    selected.update(mapping[keys[first]])
            hit = mapping.get(line)
            if hit:
                self.witnesses.append(hit)
                selected.update(hit)
                continue
            containing = [n for n in nodes if n.lineno <= line <= getattr(n, 'end_lineno', n.lineno)]
            containing.sort(key=lambda n: getattr(n, 'end_lineno', n.lineno) - n.lineno)
            hit = set()
            for node in containing:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    break
                hit.update(mapping.get(node.lineno, ()))
                if hit:
                    break
            if not hit:
                enclosing = [n for n in functions if n.lineno <= line <= n.end_lineno]
                if enclosing:
                    node = min(enclosing, key=lambda n: n.end_lineno - n.lineno)
                    hit = self.interval(path, node.lineno, node.end_lineno)
                else:
                    unresolved = True
            if hit:
                self.witnesses.append(hit)
            selected.update(hit)
        if unresolved:
            # Import-time declarations may fail before runtime coverage starts.
            return self.fallback()
        if not selected and probes and not measured_empty:
            selected.update(self.modules.get(path, ()))
        if not selected and not measured_empty and path.startswith(('sqlglot/', 'tests/')):
            selected.update(self.all)
        return selected
    def select(self, text):
        self.witnesses = []
        self.conservative = False
        self.import_sensitive = False
        selected = self.unknown_tests.copy()
        changes = parse_patch(text)
        for change in changes:
            paths = {p for p in (change.old, change.new) if p and p != '/dev/null'}
            if any(p.endswith('.py') for p in paths):
                selected.update(self.select_python(change))
                continue
            found = False
            for path in paths:
                if path in self.reads:
                    self.witnesses.append(self.reads[path])
                    selected.update(self.reads[path])
                    found = True
            if not found and any(p.startswith('tests/fixtures/') for p in paths):
                # Infer new-resource consumers from the pinned test source.
                resource_selected = set()
                for path in paths:
                    stem, relative = Path(path).stem, path.removeprefix('tests/fixtures/')
                    for filename, source in self.sources.items():
                        if filename.startswith('tests/') and (relative in source or (len(stem) >= 4 and stem in source)):
                            resource_selected.update(self.by_file.get(filename, ()))
                            resource_selected.update(self.modules.get(filename, ()))
                selected.update(resource_selected or self.all)
            elif not found and any(Path(p).suffix in {'.toml', '.ini', '.cfg'} for p in paths):
                selected.update(self.all)
        if self.comparisons and len(changes)==1:
            change=changes[0]
            if change.old==change.new and change.old.endswith('.py') and change.old in self.sources:
                path=change.old
                modified=apply_change(self.sources[path],change)
                try:
                    new_tree=ast.parse(modified.encode('utf-8'),filename=path,type_comments=True)
                    protected=set(self.reads.get(path,()))
                    introspection={'__code__','__traceback__','traceback','exc_info','_getframe',
                                   'getframeinfo','currentframe','f_lineno','getsource',
                                   'getsourcelines','get_instructions','disassemble','Bytecode'}
                    for symbol in self.usage_symbols:
                        if symbol not in introspection and not symbol.startswith('co_'):continue
                        protected.update(self.usage_symbols.get(symbol,()))
                    precise=self.comparisons.filter(path,self.tree(path),new_tree,selected,protected)
                    if precise is not None:selected=precise
                except (SyntaxError,ValueError,TypeError):pass
        return selected

def schedule(selected, durations):
    if not selected:
        return []
    ordered = sorted(selected, key=lambda n: (-durations[n], n))
    best_cost, best = None, None
    for count in range(1, min(8, len(ordered)) + 1):
        workers, loads = [[] for _ in range(count)], [0] * count
        for number in ordered:
            lightest = min(range(count), key=loads.__getitem__)
            workers[lightest].append(number)
            loads[lightest] += durations[number]
        occupied = [w for w in workers if w]
        cost = max(loads) + 2000 * len(occupied)
        if best_cost is None or cost < best_cost:
            best_cost, best = cost, occupied
    return best

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=Path('/data'))
    parser.add_argument('--map', type=Path, default=Path(__file__).with_name('analysis.json.gz'))
    parser.add_argument('--source', type=Path, default=Path(os.environ.get('SQLGLOT_SOURCE', '/opt/sqlglot')))
    parser.add_argument('--static', action='store_true')
    args = parser.parse_args()
    tests = json.loads((args.data / 'tests.json').read_text(encoding='utf-8'))['tests']
    usage_path = Path(__file__).with_name('usage.json.gz')
    usage = None
    if usage_path.is_file():
        with gzip.open(usage_path, 'rt', encoding='utf-8') as f:
            usage = json.load(f)
    attribute_path = Path(__file__).with_name('attributes.json.gz')
    attributes = None
    if attribute_path.is_file():
        with gzip.open(attribute_path, 'rt', encoding='utf-8') as f:
            attributes = json.load(f)
    dictionary_path = Path(__file__).with_name('dictionary_reads.json.gz')
    dictionary_data = None
    if dictionary_path.is_file():
        with gzip.open(dictionary_path, 'rt', encoding='utf-8') as f:
            dictionary_data = json.load(f)
    with gzip.open(args.map, 'rt', encoding='utf-8') as f:
        baseline = json.load(f)
    flow_path = Path(__file__).with_name('key_flow.json.gz')
    if dictionary_data and flow_path.is_file():
        with gzip.open(flow_path, 'rt', encoding='utf-8') as f:
            flow = json.load(f)
        if flow.get('pinned_commit') == baseline.get('pinned_commit'):
            from dictionary_impact import merge_maps
            dictionary_data = merge_maps(dictionary_data,flow)
    comparison_path = Path(__file__).with_name('comparisons.json.gz')
    comparison_data = None
    if comparison_path.is_file():
        with gzip.open(comparison_path, 'rt', encoding='utf-8') as f:
            candidate = json.load(f)
        if candidate.get('pinned_commit') == baseline.get('pinned_commit'):
            comparison_data = candidate
            none_path=Path(__file__).with_name('none_traits.json')
            if none_path.is_file():
                traits=json.loads(none_path.read_text())
                if traits.get('pinned_commit') == baseline.get('pinned_commit'):
                    comparison_data.update({k:traits[k] for k in ('safe_eq_none_types','safe_ne_none_types','protected_methods')})
    index = ImpactIndex(baseline, tests, usage, attributes, dictionary_data, comparison_data)
    durations = {t['number']: round(t['seconds'] * 1000) for t in tests}
    refiner = None
    if not args.static and os.environ.get('BR_PROBE', '1') != '0' and args.source.is_dir():
        from refine import Refiner
        refiner = Refiner(args.source, index, tests)
    count = 0
    for patch in sorted((args.data / 'commits').glob('*.patch')):
        try:
            patch_text = patch.read_text(encoding='utf-8')
            selected = index.select(patch_text)
            if refiner is not None:
                selected = refiner.refine(patch_text, selected)
        except Exception as error:
            print(f'Conservative fallback for {patch.name}: {type(error).__name__}', file=sys.stderr)
            selected = index.all
        print(json.dumps({'commit': patch.stem, 'workers': schedule(selected, durations)}, separators=(',', ':')))
        count += 1
    print(f'Selected tests for {count} patches using baseline dependencies', file=sys.stderr)

if __name__ == '__main__':
    main()
