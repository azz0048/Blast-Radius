"""Run catalogued unittest cases directly, preserving fixtures and subtest failures."""
import importlib
import signal
import unittest


def run_tests(test_ids, patched_paths):
    cases=[];names={}
    for nodeid in test_ids:
        parts=nodeid.split('::')
        if len(parts)!=3 or '[' in parts[-1]:return None
        module=importlib.import_module(parts[0][:-3].replace('/','.'))
        cls=getattr(module,parts[1],None)
        if not isinstance(cls,type) or not issubclass(cls,unittest.TestCase):return None
        case=cls(parts[2]);cases.append(case);names[id(case)]=nodeid
    outcomes={};import_errors=[];details=[];setup_errors=[]
    def timed_out(*unused):raise TimeoutError('Bounded online test exceeded its deadline')
    signal.signal(signal.SIGALRM,timed_out)
    class Result(unittest.TestResult):
        failfast=True
        def __init__(self):
            super().__init__();self.failfast=True
        def startTest(self,test):
            super().startTest(test)
            signal.setitimer(signal.ITIMER_REAL,4)
        def stopTest(self,test):
            signal.setitimer(signal.ITIMER_REAL,0)
            super().stopTest(test)
        def record_failure(self,test,error):
            nodeid=names.get(id(test))
            if nodeid is None:
                setup_errors.append(test.id())
            else:outcomes[nodeid]='failed'
            if len(details)<2:details.append(error[0].__name__)
            tb=error[2]
            while tb:
                if tb.tb_frame.f_code.co_name=='<module>' and tb.tb_frame.f_code.co_filename in patched_paths:
                    import_errors.append(tb.tb_frame.f_code.co_filename)
                tb=tb.tb_next
            self.stop()
        def addSuccess(self,test):
            outcomes.setdefault(names[id(test)],'passed')
            super().addSuccess(test)
        def addSkip(self,test,reason):
            if id(test) in names:outcomes.setdefault(names[id(test)],'skipped')
            super().addSkip(test,reason)
        def addError(self,test,error):
            self.record_failure(test,error)
            super().addError(test,error)
        def addFailure(self,test,error):
            self.record_failure(test,error)
            super().addFailure(test,error)
        def addSubTest(self,test,subtest,error):
            if error is not None:self.record_failure(test,error)
            super().addSubTest(test,subtest,error)
    result=Result()
    try:unittest.TestSuite(cases).run(result)
    finally:signal.setitimer(signal.ITIMER_REAL,0)
    return {'complete':all(nodeid in outcomes for nodeid in test_ids),
            'failed':sorted(n for n,outcome in outcomes.items() if outcome=='failed'),
            'checked':sorted(outcomes),'import_errors':import_errors,
            'setup_errors':setup_errors,'details':details,'exit':0 if result.wasSuccessful() else 1}
