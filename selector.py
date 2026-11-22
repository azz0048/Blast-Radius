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
    def __init__(self, data, tests):
        self.all = {t['number'] for t in tests}
        self.numbers = {t['id']: t['number'] for t in tests}
        self.baseline_numbers = [self.numbers.get(nodeid) for nodeid in data.get('collection', [])]
        self.by_file = {}
        for test in tests:
            self.by_file.setdefault(test['file'], set()).add(test['number'])
        self.sources = data['sources']
        self.lines = {p: {int(n): self.translate(ids) for n, ids in m.items()} for p, m in data['lines'].items()}
        self.modules = {p: self.translate(ids) for p, ids in data['modules'].items()}
        self.reads = {p: self.translate(ids) for p, ids in data['reads'].items()}
        self.line_keys = {p: sorted(m) for p, m in self.lines.items()}
        self.trees = {}
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
        if original is None or change.new == '/dev/null':
            return self.modules.get(path, set()) | self.by_file.get(path, set()) or self.all.copy()
        modified, old_tree = apply_change(original, change), self.tree(path)
        try:
            new_tree = ast.parse(modified.encode('utf-8'), filename=change.new or path, type_comments=True)
            compile(new_tree, change.new or path, 'exec', dont_inherit=True)
        except (SyntaxError, ValueError, TypeError):
            return self.all.copy()
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
        for line in sorted(probes):
            enclosing = [n for n in functions if n.lineno < line <= n.end_lineno]
            if not enclosing:
                # Declarations execute at import time, before test call coverage.
                return self.all.copy()
            statements = [n for n in nodes if n.lineno <= line <= getattr(n, 'end_lineno', n.lineno)
                          and not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            if statements:
                statement = min(statements, key=lambda n: getattr(n, 'end_lineno', n.lineno) - n.lineno)
                # Include entry into a multiline condition, even when its changed
                # operand was short-circuited in the unmodified revision.
                keys = self.line_keys.get(path, [])
                first = bisect_left(keys, statement.lineno)
                if first < len(keys) and keys[first] <= getattr(statement, 'end_lineno', statement.lineno):
                    selected.update(mapping[keys[first]])
            hit = mapping.get(line)
            if hit:
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
            selected.update(hit)
        if unresolved:
            # Import-time declarations may fail before runtime coverage starts.
            return self.all.copy()
        if not selected and probes:
            selected.update(self.modules.get(path, ()))
        if not selected and path.startswith(('sqlglot/', 'tests/')):
            selected.update(self.all)
        return selected
    def select(self, text):
        selected = set()
        for change in parse_patch(text):
            paths = {p for p in (change.old, change.new) if p and p != '/dev/null'}
            if any(p.endswith('.py') for p in paths):
                selected.update(self.select_python(change))
                continue
            found = False
            for path in paths:
                if path in self.reads:
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
    args = parser.parse_args()
    tests = json.loads((args.data / 'tests.json').read_text(encoding='utf-8'))['tests']
    with gzip.open(args.map, 'rt', encoding='utf-8') as f:
        index = ImpactIndex(json.load(f), tests)
    durations = {t['number']: round(t['seconds'] * 1000) for t in tests}
    count = 0
    for patch in sorted((args.data / 'commits').glob('*.patch')):
        try:
            selected = index.select(patch.read_text(encoding='utf-8'))
        except Exception as error:
            print(f'Conservative fallback for {patch.name}: {type(error).__name__}', file=sys.stderr)
            selected = index.all
        print(json.dumps({'commit': patch.stem, 'workers': schedule(selected, durations)}, separators=(',', ':')))
        count += 1
    print(f'Selected tests for {count} patches using baseline dependencies', file=sys.stderr)

if __name__ == '__main__':
    main()
