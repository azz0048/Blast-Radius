import ast
import itertools
import operator
import unittest
from value_profiles import ValueIndex, range_preserves, changed_comparisons, site_key


class ValueTests(unittest.TestCase):
    def test_no_integer_range_proof_can_skip_a_disagreement(self):
        ops={'Eq':operator.eq,'NotEq':operator.ne,'Lt':operator.lt,'LtE':operator.le,'Gt':operator.gt,'GtE':operator.ge}
        for low,high,op,side,c,truth in itertools.product(range(-3,4),range(-3,4),ops,range(2),range(-4,5),[False,True]):
            if low>high:continue
            if range_preserves('int',low,high,truth,op,side,c):
                self.assertTrue(all((ops[op](c,x) if side==0 else ops[op](x,c))==truth for x in range(low,high+1)))
    def test_equality_cannot_hide_an_interior_match(self):
        self.assertFalse(range_preserves('int',0,10,False,'Eq',1,5))
        self.assertFalse(range_preserves('str','a','z',True,'NotEq',1,'m'))
    def test_incompatible_ordering_is_conservative(self):
        self.assertFalse(range_preserves('str','a','z',True,'Lt',1,1))
    def test_noncomparison_change_rejects_proof(self):
        old=ast.parse('if x < 5:\n print(x)')
        new=ast.parse('if x <= 5:\n print(y)')
        self.assertIsNone(changed_comparisons('x.py',old,new))
    def test_contexts_unknown_tests_and_source_readers_stay_selected(self):
        old,new=ast.parse('if x < 5: pass'),ast.parse('if x <= 5: pass')
        site=site_key('x.py',old.body[0].test)
        data={'collection':['safe','unsafe'],'complete_baseline':True,'sites':{site:{
            '0':{'unsafe':False,'ranges':[['int',1,0,4]]},
            '1':{'unsafe':False,'ranges':[['int',0,5,9]]}}}}
        index=ValueIndex(data,{'safe':0,'unsafe':1,'unknown':2})
        self.assertEqual(index.filter('x.py',old,new,{0,1,2}),{1,2})
        self.assertEqual(index.filter('x.py',old,new,{0,1,2},{0}),{0,1,2})


if __name__=='__main__':unittest.main()
