"""Regression checks for exact-test evidence and partial probe outcomes."""
import time
from pathlib import Path
from types import SimpleNamespace
import unittest
from refine import Refiner


PATCH='--- a/sqlglot/example.py\n+++ b/sqlglot/example.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n'

class RefinerTests(unittest.TestCase):
    def refiner(self, result):
        tests=[{'number':1,'id':'cheap','seconds':.4},
               {'number':2,'id':'expensive','seconds':20.0},
               {'number':3,'id':'other','seconds':.4}]
        ref=Refiner.__new__(Refiner)
        ref.root=Path('/nonexistent')
        ref.index=SimpleNamespace(conservative=False,import_sensitive=False,
            sources={'sqlglot/example.py':'value = 1\n'},all={1,2,3},
            witnesses=[{1,2,3}])
        ref.by_number={t['number']:t for t in tests}
        ref.by_id={t['id']:t['number'] for t in tests}
        ref.started=time.monotonic();ref.limit=95;ref.stats=[]
        ref.prober=SimpleNamespace(run=lambda *args,**kwargs:result)
        return ref

    def test_untested_expensive_case_survives_passing_representatives(self):
        ref=self.refiner({'complete':True,'checked':['cheap','other'],'failed':[]})
        self.assertEqual(ref.refine(PATCH,{1,2,3}),{2})

    def test_partial_probe_removes_only_confirmed_passes(self):
        ref=self.refiner({'complete':False,'checked':['cheap'],'failed':[]})
        self.assertEqual(ref.refine(PATCH,{1,2,3}),{2,3})

    def test_observed_failure_and_unexecuted_cases_are_retained(self):
        ref=self.refiner({'complete':False,'checked':['cheap','other'],'failed':['other']})
        self.assertEqual(ref.refine(PATCH,{1,2,3}),{2,3})

    def test_class_or_module_fixture_failure_keeps_every_candidate(self):
        ref=self.refiner({'complete':False,'checked':['cheap'],'failed':[],
                          'setup_errors':['tearDownClass']})
        self.assertEqual(ref.refine(PATCH,{1,2,3}),{1,2,3})

    def test_tiny_possible_saving_skips_probe_and_keeps_all_tests(self):
        ref=self.refiner({'complete':True,'checked':['cheap','other'],'failed':[]})
        ref.by_number[1]['seconds']=.001;ref.by_number[3]['seconds']=.001
        self.assertEqual(ref.refine(PATCH,{1,2,3}),{1,2,3})

if __name__=='__main__':unittest.main()
