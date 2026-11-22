"""Isolation and failure-detection checks for the online validator."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from probe_runner import Prober

@unittest.skipUnless(hasattr(os,'fork') and importlib.util.find_spec('pytest'),'requires Linux and runtime requirements')
class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'toy.py').write_text('def value():\n    return 1\n')
        (self.root/'data.txt').write_text('one')
        (self.root/'test_toy.py').write_text('import unittest\nfrom pathlib import Path\nfrom toy import value\nclass Case(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(value(), 1)\n    def test_resource(self):\n        self.assertEqual(Path(__file__).with_name("data.txt").read_text(), "one")\n')
        self.prober=Prober(self.root)
    def test_failing_patch_is_detected_and_baseline_is_restored(self):
        node='test_toy.py::Case::test_value'
        bad=self.prober.run({'toy.py':b'def value():\n    return 2\n'},[node])
        self.assertIn(node,bad.get('failed',[]))
        good=self.prober.run({},[node])
        self.assertTrue(good['complete']);self.assertEqual(good['failed'],[])
        self.assertIn('return 1',(self.root/'toy.py').read_text())
    def test_changed_resource_is_visible_to_path_read_text(self):
        node='test_toy.py::Case::test_resource'
        result=self.prober.run({'data.txt':b'two'},[node])
        self.assertIn(node,result.get('failed',[]))
        self.assertEqual((self.root/'data.txt').read_text(),'one')
    def test_import_error_is_reported(self):
        result=self.prober.run({'toy.py':b'raise RuntimeError("broken initialization")\n'},['test_toy.py::Case::test_value'])
        self.assertEqual(result.get('import_errors'),['toy.py'])
    def test_empty_selection_still_checks_imports(self):
        result=self.prober.run({'toy.py':b'raise RuntimeError("broken initialization")\n'},[])
        self.assertEqual(result.get('import_errors'),['toy.py'])
    def test_deadline_kills_only_the_trial(self):
        result=self.prober.run({'toy.py':b'def value():\n    while True: pass\n'},['test_toy.py::Case::test_value'],seconds=0.5)
        self.assertFalse(result['complete']);self.assertEqual(result.get('error'),'deadline')
