"""Import caches must protect parent and copied dictionary origins."""
import sys
import types
import unittest
from unittest.mock import patch
from dictionary_impact import merge_maps
from import_key_guards import protect_origins

class ImportGuardTests(unittest.TestCase):
    def test_cached_child_protects_ancestors_and_cross_dialect_copy(self):
        module=types.ModuleType('sqlglot_guard_example')
        parent=type('Parent',(),{'__module__':module.__name__,'KEYWORDS':{'A':1}})
        child=type('Child',(parent,),{'__module__':module.__name__,'KEYWORDS':{'B':2}})
        other=type('Other',(),{'__module__':module.__name__,'KEYWORDS':{'C':3}})
        module.Parent=parent;module.Child=child;module.Other=other
        def key(name):return module.__name__+'.py::'+name+'::KEYWORDS'
        data={'unsafe_fields':[],'copy_edges':{key('Child'):[(key('Other'),False)]}}
        with patch.dict(sys.modules,{module.__name__:module}):
            result=protect_origins(data,{key('Child')})
        self.assertEqual(set(result['unsafe_fields']),{key('Parent'),key('Child'),key('Other')})
        self.assertTrue(result['cold_import_guards_checked'])

    def test_map_without_cold_import_check_is_ignored(self):
        original={'collection':['a'],'wrapped':[],'reads':{},'wildcards':{}}
        unchecked={'collection':['a'],'complete_baseline':True,'wrapped':['new-field']}
        self.assertIs(merge_maps(original,unchecked),original)

if __name__=='__main__':unittest.main()
