import ast
import unittest
import difflib
from selector import ImpactIndex
from expression_profiles import FUNCTION_NAME, InstrumentEntries, ExpressionIndex, changed_sites, site


class EntryTests(unittest.TestCase):
    def test_multi_file_entry_sets_are_unioned(self):
        sources={p:'def f(x,y):\n return x and y\n' for p in ('sqlglot/a.py','sqlglot/b.py')}
        names=['a','b','neither']
        tests=[{'id':name,'number':i,'file':'tests/test_'+name+'.py'} for i,name in enumerate(names)]
        data={'collection':names,'sources':sources,'lines':{p:{'2':[0,1,2]} for p in sources},'modules':{},'reads':{}}
        sites={site(p,ast.parse(source).body[0].body[0].value,'bool'):[i] for i,(p,source) in enumerate(sources.items())}
        expressions={'collection':names,'complete_baseline':True,'expression_coverage':1,'sites':sites}
        index=ImpactIndex(data,tests,expression_data=expressions)
        patches=[]
        for p,source in sources.items():
            modified=source.replace('x and y','x or y')
            patches.append(f'diff --git a/{p} b/{p}\n')
            patches.extend(difflib.unified_diff(source.splitlines(keepends=True),modified.splitlines(keepends=True),fromfile='a/'+p,tofile='b/'+p))
        self.assertEqual(index.select(''.join(patches)),{0,1})
    def run_instrumented(self,source,namespace):
        registered=set();seen=[]
        tree=InstrumentEntries('x.py',registered).visit(ast.parse(source));ast.fix_missing_locations(tree)
        namespace[FUNCTION_NAME]=lambda token,value:(seen.append(token),value)[1]
        exec(compile(tree,'x.py','exec'),namespace)
        return registered,set(seen)
    def test_nested_short_circuit_truth_calls_are_preserved(self):
        for expression in ('a and b','(a and b) and c','(a and b) or c','a or (b and c)','not (a and b) or c'):
            for truth in (False,True):
                for context in ('result = '+expression,'result = 0\nif '+expression+': result = 1'):
                    events=[]
                    class C:
                        def __bool__(self):events.append('bool');return truth
                    args={'a':C(),'b':C(),'c':C()}
                    normal=dict(args);exec(context,normal);expected=list(events);events.clear()
                    observed=dict(args);self.run_instrumented(context,observed)
                    self.assertEqual(events,expected,(expression,truth,context))
                    self.assertIs(observed['result'],normal['result'])
    def test_chained_comparison_truth_calls_are_preserved(self):
        events=[]
        class Truth:
            def __bool__(self):events.append('truth');return False
        class C:
            def __lt__(self,other):events.append('lt');return Truth()
        code='result = 0\nif a < b < c: result = 1'
        ns={'a':C(),'b':C(),'c':C()};exec(code,dict(ns));expected=list(events);events.clear()
        self.run_instrumented(code,dict(ns));self.assertEqual(events,expected)
    def test_return_subexpression_entry_excludes_short_circuited_case(self):
        source='def f(a,b,c):\n return a and (b or c)\nx=f(False,True,False)\n'
        registered,seen=self.run_instrumented(source,{})
        node=ast.parse(source).body[0].body[0].value.values[1]
        token=site('x.py',node,'bool')
        self.assertIn(token,registered);self.assertNotIn(token,seen)
    def test_annotations_and_defaults_are_not_instrumented(self):
        source='from __future__ import annotations\ndef f(x: int | None = 1 + 2) -> str | None:\n return x + 2\n'
        ns={};self.run_instrumented(source,ns)
        self.assertEqual(ns['f'].__annotations__,{'x':'int | None','return':'str | None'})
        self.assertEqual(ns['f'].__defaults__,(3,))
    def test_reassociation_selects_the_changed_subexpression(self):
        old=ast.parse('def f(a,b,c,d):\n return a and (b and c and d)')
        new=ast.parse('def f(a,b,c,d):\n return a and (b and c or d)')
        inner=old.body[0].body[0].value.values[1]
        self.assertEqual(changed_sites('x.py',old,new),[site('x.py',inner,'bool')])
    def test_unrecognized_simultaneous_edit_rejects_filter(self):
        a=ast.parse('return a and f(1)');b=ast.parse('return a or f(2)')
        self.assertIsNone(changed_sites('x.py',a,b))
    def test_unknown_tests_and_source_readers_are_retained(self):
        a,b=ast.parse('return a and b'),ast.parse('return a or b')
        token=site('x.py',a.body[0].value,'bool')
        data={'expression_coverage':1,'complete_baseline':True,'collection':['skip','enter'],'sites':{token:[1]}}
        index=ExpressionIndex(data,{'skip':0,'enter':1,'unknown':2})
        self.assertEqual(index.filter('x.py',a,b,{0,1,2}),{1,2})
        self.assertEqual(index.filter('x.py',a,b,{0,1,2},{0}),{0,1,2})
    def test_import_time_execution_keeps_conservative_plan(self):
        a,b=ast.parse('return a and b'),ast.parse('return a or b')
        token=site('x.py',a.body[0].value,'bool')
        data={'expression_coverage':1,'complete_baseline':True,'collection':['test'],'sites':{token:[-1]}}
        self.assertIsNone(ExpressionIndex(data,{'test':0}).filter('x.py',a,b,{0}))


if __name__=='__main__':unittest.main()
