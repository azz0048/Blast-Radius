"""Semantic boundaries for the operand-type analysis and original-operation tracer."""
import ast
import enum
import unittest

from comparison_profiles import (ComparisonIndex,FUNCTION_NAME,InstrumentComparisons,
    changed_identity_comparisons,equality_is_identity,site_key,type_trait,operand_trait)


class ComparisonTests(unittest.TestCase):
    def trees(self,old='is',new='=='):
        return ast.parse(f'def f(x):\n    return x {old} None\n'),ast.parse(f'def f(x):\n    return x {new} None\n')
    def index(self,patterns=None,contexts=None,complete=True):
        old,new=self.trees();node=old.body[0].body[0].value;key=site_key('module.py',node)
        data={'collection':['a','b'],'complete_baseline':complete,
              'patterns':patterns or [['identity:module.Box','none']],
              'sites':{key:contexts or {'0':[0,1]}}}
        return old,new,ComparisonIndex(data,{'a':9,'b':4,'new-test':52})

    def test_none_comparison_with_plain_object_is_equivalent(self):
        class Box:pass
        self.assertTrue(equality_is_identity(type_trait(Box),'none'))

    def test_custom_equality_is_not_assumed_equivalent(self):
        class Box:
            def __eq__(self,other):return True
        self.assertFalse(equality_is_identity(type_trait(Box),'none'))

    def test_equal_string_values_do_not_imply_identity(self):
        self.assertFalse(equality_is_identity(type_trait(str),type_trait(str)))

    def test_equality_only_none_proof_does_not_cover_inequality(self):
        self.assertTrue(equality_is_identity('eq-none-safe:Box','none'))
        self.assertFalse(equality_is_identity('eq-none-safe:Box','none',inequality=True))

    def test_changed_equality_implementation_invalidates_its_type_property(self):
        old,new,_=self.index()
        node=old.body[0].body[0].value;key=site_key('module.py',node)
        data={'collection':['a','b'],'complete_baseline':True,
              'patterns':[['unknown:Box','none']],'sites':{key:{'0':[0,1]}},
              'safe_eq_none_types':['Box'],
              'protected_methods':[{'path':'module.py','start':1,'end':2}]}
        index=ComparisonIndex(data,{'a':9,'b':4})
        self.assertIsNone(index.filter('module.py',old,new,{4,9}))

    def test_enum_members_of_one_standard_class_use_unique_values(self):
        class Choice(str,enum.Enum):A='a';B='b'
        trait=type_trait(Choice)
        self.assertTrue(trait.startswith('enum-str:'))
        self.assertTrue(equality_is_identity(trait,trait))

    def test_distinct_enum_classes_are_not_equivalent_by_value(self):
        self.assertFalse(equality_is_identity('enum-str:A@1','enum-str:A@2'))

    def test_noncanonical_enum_instance_does_not_support_identity_equivalence(self):
        class Choice(str,enum.Enum):A='a';B='b'
        ghost=str.__new__(Choice,'a')
        self.assertEqual(ghost,Choice.A);self.assertIsNot(ghost,Choice.A)
        canonical={id(member) for member in Choice.__members__.values()}
        known=operand_trait(Choice.A,type_trait(Choice),canonical)
        unknown=operand_trait(ghost,type_trait(Choice),canonical)
        self.assertFalse(equality_is_identity(known,unknown))

    def test_custom_enum_member_factory_is_not_assumed_injective(self):
        class Choice(str,enum.Enum):
            def __new__(cls,value):
                member=str.__new__(cls,'same');member._value_=value;return member
            A=1;B=2
        self.assertTrue(Choice.A==Choice.B)
        self.assertIsNot(Choice.A,Choice.B)
        self.assertTrue(type_trait(Choice).startswith('unknown:'))

    def test_proof_keeps_unknown_test_identities(self):
        old,new,index=self.index()
        self.assertEqual(index.filter('module.py',old,new,{4,9,52}),{52})

    def test_unsafe_observation_retains_its_exact_test_identity(self):
        old,new,index=self.index([['identity:X','none'],['unknown:Y','none']],{'0':[0],'1':[1]})
        self.assertEqual(index.filter('module.py',old,new,{4,9}),{4})

    def test_source_and_bytecode_consumers_are_protected(self):
        old,new,index=self.index()
        self.assertEqual(index.filter('module.py',old,new,{4,9},protected={9}),{9})

    def test_import_time_unsafe_types_disable_pruning(self):
        old,new,index=self.index([['unknown:X','none']],{'0':[-1]})
        self.assertIsNone(index.filter('module.py',old,new,{4,9}))

    def test_incomplete_baseline_never_supports_a_proof(self):
        old,new,index=self.index(complete=False)
        self.assertIsNone(index.filter('module.py',old,new,{4,9}))

    def test_missing_site_metadata_is_conservative(self):
        old,new,index=self.index();index.profiles={}
        self.assertIsNone(index.filter('module.py',old,new,{4,9}))

    def test_mixed_operator_and_operand_edits_are_rejected(self):
        old,new=self.trees();new.body[0].body[0].value.left=ast.Constant(3)
        self.assertIsNone(changed_identity_comparisons(old,new,'module.py'))

    def test_inequality_pair_preserves_its_polarity(self):
        old,new=self.trees('is not','!=')
        self.assertEqual(len(changed_identity_comparisons(old,new,'module.py')),1)
        old,new=self.trees('is','!=')
        self.assertIsNone(changed_identity_comparisons(old,new,'module.py'))

    def test_chained_comparisons_are_not_proven(self):
        old=ast.parse('result = x is y is z');new=ast.parse('result = x == y is z')
        self.assertIsNone(changed_identity_comparisons(old,new,'module.py'))

    def test_tracer_evaluates_each_operand_once_and_keeps_original_result(self):
        calls=[];marker=object()
        class Box:
            def __eq__(self,other):calls.append('eq');return marker
        def left():calls.append('left');return Box()
        def right():calls.append('right');return None
        def observer(site,op,a,b):
            calls.append(op)
            return a==b
        tree=InstrumentComparisons('module.py').visit(ast.parse('answer = left() == right()'))
        ast.fix_missing_locations(tree)
        namespace={FUNCTION_NAME:observer,'left':left,'right':right}
        exec(compile(tree,'module.py','exec'),namespace)
        self.assertIs(namespace['answer'],marker)
        self.assertEqual(calls,['left','right','Eq','eq'])

if __name__=='__main__':unittest.main()
