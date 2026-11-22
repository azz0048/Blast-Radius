import difflib
import unittest

from selector import ImpactIndex, apply_change, parse_patch, schedule


def patch(old, new, name='sqlglot/example.py', context=3):
    return ''.join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile='a/' + name, tofile='b/' + name, n=context,
    ))


class SelectorTests(unittest.TestCase):
    def test_multi_hunk_patch_application(self):
        old = ''.join(f'line {n}\n' for n in range(25))
        new = old.replace('line 2\n', 'changed\n').replace('line 21\n', 'new\nmore\n')
        change, = parse_patch(patch(old, new))
        self.assertEqual(apply_change(old, change), new)

    def test_zero_context_insertions(self):
        old, new = 'a\nb\n', 'a\ninserted\nb\n'
        change, = parse_patch(patch(old, new, context=0))
        self.assertEqual(apply_change(old, change), new)

    def test_beginning_insertion(self):
        old, new = 'a\nb\n', 'inserted\na\nb\n'
        change, = parse_patch(patch(old, new, context=0))
        self.assertEqual(apply_change(old, change), new)

    def test_reject_wrong_base(self):
        change, = parse_patch(patch('a\nb\n', 'a\nc\n'))
        with self.assertRaises(ValueError):
            apply_change('wrong\nbase\n', change)

    def test_empty_selection_costs_nothing(self):
        self.assertEqual(schedule(set(), {}), [])

    def test_worker_fee_avoids_pointless_parallelism(self):
        self.assertEqual(len(schedule({1, 2}, {1: 100, 2: 100})), 1)

    def test_parallelism_for_two_expensive_tests(self):
        plan = schedule({1, 2}, {1: 10000, 2: 10000})
        self.assertEqual(len(plan), 2)
        self.assertEqual({n for worker in plan for n in worker}, {1, 2})

    def test_nonduplicating_eight_worker_limit(self):
        durations = {n: (n * 97) % 11000 for n in range(200)}
        plan = schedule(set(durations), durations)
        numbers = [n for worker in plan for n in worker]
        self.assertEqual(len(numbers), len(set(numbers)))
        self.assertEqual(set(numbers), set(durations))
        self.assertLessEqual(len(plan), 8)

    def make_index(self):
        data = {
            'sources': {'sqlglot/example.py': 'def f():\n    return 1\n'},
            'lines': {'sqlglot/example.py': {'2': ['test-a']}},
            'modules': {'sqlglot/example.py': ['test-a']},
            'reads': {'tests/fixtures/data.sql': ['test-b']},
        }
        tests = [
            {'id': 'test-a', 'number': 9, 'file': 'tests/test_a.py'},
            {'id': 'test-b', 'number': 4, 'file': 'tests/test_b.py'},
        ]
        return ImpactIndex(data, tests)

    def test_coverage_matches_ids_not_test_numbers(self):
        index = self.make_index()
        selected = index.select(patch('def f():\n    return 1\n', 'def f():\n    return 2\n'))
        self.assertEqual(selected, {9})

    def test_resource_dependencies(self):
        index = self.make_index()
        selected = index.select(patch('SELECT 1;\n', 'SELECT 2;\n', 'tests/fixtures/data.sql'))
        self.assertEqual(selected, {4})

    def test_syntax_breakage_is_conservative(self):
        index = self.make_index()
        selected = index.select(patch('def f():\n    return 1\n', 'def f(:\n    return 1\n'))
        self.assertEqual(selected, {4, 9})

    def test_inert_inline_comment(self):
        index = self.make_index()
        selected = index.select(patch('def f():\n    return 1\n', 'def f():\n    return 1  # comment\n'))
        self.assertEqual(selected, set())

    def test_multiline_short_circuit_change_includes_condition_entry(self):
        index = self.make_index()
        source = 'def f(a, b):\n    if (a\n        and b):\n        return 1\n    return 0\n'
        index.sources['sqlglot/example.py'] = source
        index.lines['sqlglot/example.py'] = {2: {4, 9}, 3: {9}, 4: {9}, 5: {4}}
        index.line_keys['sqlglot/example.py'] = [2, 3, 4, 5]
        selected = index.select(patch(source, source.replace('and b', 'or b')))
        self.assertEqual(selected, {4, 9})

    def test_import_time_setting_is_not_only_first_importer(self):
        index = self.make_index()
        source = 'class Settings:\n    OFFSET = -1\n'
        index.sources['sqlglot/example.py'] = source
        index.lines['sqlglot/example.py'] = {2: {9}}
        index.line_keys['sqlglot/example.py'] = [2]
        selected = index.select(patch(source, source.replace('-1', '-2')))
        self.assertEqual(selected, {4, 9})

    def test_condition_parenthesis_has_no_coverage_line(self):
        index = self.make_index()
        source = 'def f(a, b):\n    if (\n        a\n        and b\n    ):\n        return 1\n    return 0\n'
        index.sources['sqlglot/example.py'] = source
        index.lines['sqlglot/example.py'] = {3: {4, 9}, 4: {9}, 6: {9}, 7: {4}}
        index.line_keys['sqlglot/example.py'] = [3, 4, 6, 7]
        selected = index.select(patch(source, source.replace('and b', 'or b')))
        self.assertEqual(selected, {4, 9})


if __name__ == '__main__':
    unittest.main()
