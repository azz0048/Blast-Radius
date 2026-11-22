"""Reject effects, recursion, custom dispatch, and unproved inequality semantics."""
import unittest
from none_semantics import none_equality_safe

class PureStructuralEquality:
    def __eq__(self,other):
        return self is other or (type(self) is type(other) and hash(self)==hash(other))
    def __ne__(self,other):return not self.__eq__(other)

class SlottedStructuralEquality:
    __slots__=()
    def __eq__(self,other):
        return self is other or (type(self) is type(other) and self.value==other.value)
    def __ne__(self,other):return not self.__eq__(other)

class EffectfulEquality:
    def __eq__(self,other):
        self.calls+=1
        return False

class NoneEqual:
    def __eq__(self,other):return other is None

class IncorrectInequality:
    def __eq__(self,other):return False
    def __ne__(self,other):return False

class RecursiveEquality:
    def __eq__(self,other):return self==other

class FalseEquality:
    def __eq__(self,other):return False

GLOBAL_FLAG=False
class FlagDependentEquality:
    def __eq__(self,other):return GLOBAL_FLAG

class NoneSemanticTests(unittest.TestCase):
    def test_pure_type_guard_proves_equality_without_hashing(self):
        self.assertTrue(none_equality_safe(PureStructuralEquality,mode='eq'))
    def test_instance_dictionary_prevents_manual_inequality_proof(self):
        self.assertFalse(none_equality_safe(PureStructuralEquality,mode='ne'))
    def test_slotted_manual_inequality_can_be_proven(self):
        self.assertTrue(none_equality_safe(SlottedStructuralEquality,mode='ne'))
    def test_side_effect_before_false_return_is_rejected(self):
        self.assertFalse(none_equality_safe(EffectfulEquality,mode='eq'))
    def test_none_can_be_equal_to_a_custom_object(self):
        self.assertFalse(none_equality_safe(NoneEqual,mode='eq'))
    def test_inequality_is_proven_independently(self):
        self.assertTrue(none_equality_safe(IncorrectInequality,mode='eq'))
        self.assertFalse(none_equality_safe(IncorrectInequality,mode='ne'))
    def test_recursive_equality_is_not_executed_or_proven(self):
        self.assertFalse(none_equality_safe(RecursiveEquality,mode='eq'))
    def test_object_inequality_delegates_to_pure_equality(self):
        self.assertTrue(none_equality_safe(FalseEquality,mode='ne'))
    def test_none_type_is_not_a_never_equal_type(self):
        self.assertFalse(none_equality_safe(type(None),mode='eq'))
    def test_current_value_of_a_mutable_global_is_not_a_type_property(self):
        self.assertFalse(none_equality_safe(FlagDependentEquality,mode='eq'))

if __name__=='__main__':unittest.main()
