import ast
import itertools
import unittest
import functools
from branch_flow import START,LEAF,END,InstrumentFlow,simulate,changed_roots,FlowIndex,site
from branch_collect import Recorder
from pure_inputs import UNKNOWN,pure_truth,function_body


def observe(expression,values,mode='return',helpers='',cap=256):
    result=[];registry={}
    body='return '+expression if mode=='return' else 'if '+expression+':\n  return True\n return False'
    source=helpers+'\ndef f():\n '+body+'\nresult=f()\n'
    tree=InstrumentFlow('toy.py',registry).visit(ast.parse(source));ast.fix_missing_locations(tree)
    recorder=Recorder(registry,lambda:('test','call'),lambda token,context,pattern:result.append((token,pattern)),cap=cap)
    namespace=dict(values);namespace.update({START:recorder.start,LEAF:recorder.leaf,END:recorder.end})
    exec(compile(tree,'toy.py','exec'),namespace);recorder.flush()
    return source,registry,result,namespace


class BranchTests(unittest.TestCase):
    def test_boolean_and_none_results_are_distinct(self):
        _,registry,records,_=observe('a and b',{'a':None,'b':False})
        pattern=records[-1][1]
        self.assertNotEqual(simulate(['And',0,1],pattern),simulate(['Or',0,1],pattern))
    def test_truth_context_can_ignore_none_vs_false(self):
        _,registry,records,_=observe('a and b',{'a':None,'b':False},mode='truth')
        pattern=records[-1][1]
        self.assertEqual(simulate(['And',0,1],pattern),simulate(['Or',0,1],pattern))
    def test_returned_objects_require_same_origin(self):
        _,_,records,_=observe('a and b',{'a':object(),'b':object()})
        pattern=records[-1][1]
        self.assertNotEqual(simulate(['And',0,1],pattern),simulate(['Or',0,1],pattern))
    def test_effectful_function_cannot_be_skipped(self):
        helpers='def first():\n events.append(1)\n return True\ndef second():\n events.append(2)\n return True\n'
        _,_,records,ns=observe('first() and second()',{'events':[]},helpers=helpers)
        pattern=records[-1][1]
        self.assertEqual(ns['events'],[1,2])
        self.assertIsNotNone(simulate(['And',0,1],pattern))
        self.assertIsNone(simulate(['Or',0,1],pattern))
    def test_unobserved_effectful_function_cannot_be_added(self):
        _,_,records,_=observe('a and unknown()',{'a':False,'unknown':lambda:False})
        self.assertIsNone(simulate(['Or',0,1],records[-1][1]))
    def test_values_after_an_effect_use_the_updated_state(self):
        helpers='def first():\n state["x"] = False\n return True\n'
        _,_,records,_=observe('first() and state.get("x")',{'state':{'x':True}},helpers=helpers)
        p=records[-1][1]
        self.assertEqual(simulate(['And',0,1],p),(0,False,None))
        self.assertEqual(simulate(['Or',0,1],p),(0,True,None))
    def test_same_effect_prefix_allows_skipping_pure_reads(self):
        helpers='def first():\n events.append(1)\n return True\n'
        _,_,records,_=observe('first() and (a or b)',{'events':[],'a':False,'b':False},helpers=helpers)
        p=records[-1][1]
        self.assertEqual(simulate(['And',0,['Or',1,2]],p),simulate(['And',0,['And',1,2]],p))
    def test_caught_exceptions_are_explicitly_unsafe(self):
        registry={};events=[]
        tree=InstrumentFlow('toy.py',registry).visit(ast.parse('try:\n if True and fail(): pass\nexcept ValueError: pass\n'))
        ast.fix_missing_locations(tree)
        rec=Recorder(registry,lambda:('x','call'),lambda t,c,p:events.append(p))
        def fail():raise ValueError()
        exec(compile(tree,'toy.py','exec'),{START:rec.start,LEAF:rec.leaf,END:rec.end,'fail':fail})
        rec.flush();self.assertIn(None,events)
    def test_instrumentation_preserves_custom_truth_call_counts(self):
        for expression in ('a and b','(a and b) and c','(a and b) or c','a or (b and c)','not (a and b) or c'):
            for truth in (False,True):
                for mode in ('truth','return'):
                    events=[]
                    class C:
                        def __bool__(self):events.append(1);return truth
                    values={'a':C(),'b':C(),'c':C()}
                    source,registry,records,ns=observe(expression,values,mode)
                    actual=list(events);events.clear()
                    normal=dict(values);exec(source,normal)
                    self.assertEqual(actual,events,(expression,truth,mode))
                    self.assertIs(ns['result'],normal['result'])
    def test_none_special_truth_method_is_unknown(self):
        class C:__bool__=None
        self.assertIs(pure_truth(C()),UNKNOWN)
    def test_exhaustive_pure_inputs_preserve_return_identity(self):
        objects=[False,True,None,object(),object()]
        old=['And',0,['Or',1,2]];new=['Or',0,['And',1,2]]
        for a,b,c in itertools.product(objects,repeat=3):
            _,_,records,_=observe('a and (b or c)',{'a':a,'b':b,'c':c})
            p=records[-1][1];x,y=simulate(old,p),simulate(new,p)
            if x is not None and y is not None and x==y:
                self.assertIs(a and (b or c),a or (b and c))
    def test_signatures_and_future_annotations_are_not_changed(self):
        source='from __future__ import annotations\ndef f(x: (int if True and False else str)):\n return x\n'
        registry={};tree=InstrumentFlow('toy.py',registry).visit(ast.parse(source));ast.fix_missing_locations(tree)
        ns={};exec(compile(tree,'toy.py','exec'),ns)
        expected={};exec(source,expected)
        self.assertEqual(ns['f'].__annotations__,expected['f'].__annotations__)
        self.assertFalse(registry)
    def test_other_edits_reject_flow_proof(self):
        self.assertIsNone(changed_roots('x.py',ast.parse('return a and b'),ast.parse('return a or c')))
    def test_wrapped_functions_are_not_treated_as_original_getters(self):
        def original(value):return value
        @functools.wraps(original)
        def wrapped(value):return not original(value)
        wrapped.__module__='sqlglot.fake'
        self.assertIsNone(function_body(wrapped,wrapped.__code__))
    def test_unknown_tests_and_source_observers_stay_selected(self):
        source,registry,records,_=observe('a and b',{'a':False,'b':False})
        token,pattern=records[-1]
        data={'collection':['safe','uncertain'],'complete_baseline':True,'flow_format':1,'patterns':[pattern],
              'sites':{token:{'0':{'unsafe':False,'patterns':[0]},'1':{'unsafe':True,'patterns':[]}}}}
        index=FlowIndex(data,{'safe':0,'uncertain':1,'new':2})
        old,new=ast.parse(source),ast.parse(source.replace('a and b','a or b'))
        self.assertEqual(index.filter('toy.py',old,new,{0,1,2}),{1,2})
        self.assertEqual(index.filter('toy.py',old,new,{0,1,2},{0}),{0,1,2})
    def test_changed_snapshot_accessor_disables_proof(self):
        source,registry,records,_=observe('a and b',{'a':False,'b':False})
        token,pattern=records[-1]
        data={'collection':['x'],'complete_baseline':True,'flow_format':1,'patterns':[pattern],
              'accessor_dependencies':[{'path':'toy.py','start':1,'end':100}],
              'sites':{token:{'0':{'unsafe':False,'patterns':[0]}}}}
        index=FlowIndex(data,{'x':0})
        self.assertIsNone(index.filter('toy.py',ast.parse(source),ast.parse(source.replace('a and b','a or b')),{0}))
    def test_observation_cap_reports_uncertainty(self):
        _,_,records,_=observe('a and b',{'a':False,'b':False},cap=0)
        self.assertEqual([p for _,p in records],[None])
    def test_de_morgan_equivalence_preserves_effect_order(self):
        helpers='def first():\n events.append(1)\n return a\ndef second():\n events.append(2)\n return b\n'
        for a,b in itertools.product([False,True],repeat=2):
            _,_,records,_=observe('not (first() and second())',{'events':[],'a':a,'b':b},helpers=helpers)
            p=records[-1][1]
            self.assertEqual(simulate(['Not',['And',0,1]],p),simulate(['Or',['Not',0],['Not',1]],p))
    def test_call_argument_boolean_value_is_profiled(self):
        source='def f(a,b):\n return capture(a or b)\nresult=f(None,None)\n'
        registry={};records=[]
        tree=InstrumentFlow('toy.py',registry).visit(ast.parse(source));ast.fix_missing_locations(tree)
        rec=Recorder(registry,lambda:('x','call'),lambda t,c,p:records.append((t,p)))
        ns={START:rec.start,LEAF:rec.leaf,END:rec.end,'capture':lambda value:value}
        exec(compile(tree,'toy.py','exec'),ns);rec.flush()
        self.assertIsNone(ns['result'])
        changes=changed_roots('toy.py',ast.parse(source),ast.parse(source.replace('a or b','a and b')))
        self.assertEqual(len(changes),1)
        token,old,new=changes[0]
        patterns=[p for t,p in records if t==token]
        self.assertEqual(len(patterns),1)
        self.assertEqual(simulate(old,patterns[0]),simulate(new,patterns[0]))
    def test_value_contexts_preserve_custom_truth_call_counts(self):
        for expression in ('a and b','(a and b) and c','not (a and b)'):
            for template in ('result = capture(EXPR)','result = [EXPR][0]','result = (EXPR) is a','result = {"x": EXPR}["x"]'):
                for truth in (False,True):
                    events=[]
                    class C:
                        def __bool__(self):events.append(1);return truth
                    original={'a':C(),'b':C(),'c':C(),'capture':lambda x:x}
                    source=template.replace('EXPR',expression)
                    normal=dict(original);exec(source,normal);expected=list(events);events.clear()
                    registry={};tree=InstrumentFlow('toy.py',registry).visit(ast.parse(source));ast.fix_missing_locations(tree)
                    rec=Recorder(registry,lambda:('x','call'),lambda *args:None)
                    ns=dict(original);ns.update({START:rec.start,LEAF:rec.leaf,END:rec.end})
                    exec(compile(tree,'toy.py','exec'),ns);rec.flush()
                    self.assertEqual(events,expected,(expression,template,truth))
                    self.assertIs(ns['result'],normal['result'])


if __name__=='__main__':unittest.main()
