"""Run catalogued unittest cases directly, preserving fixtures and subtest failures."""
import importlib
import signal
import unittest


def run_tests(test_ids, patched_paths, on_progress=None, case_timeout=4):
    cases=[];names={}
    for nodeid in test_ids:
        parts=nodeid.split('::')
        if len(parts)!=3 or '[' in parts[-1]:return None
        try:module=importlib.import_module(parts[0][:-3].replace('/','.'))
        except BaseException as error:
            # A changed library function may be called while an unchanged test
            # module initializes. Report this as a collection/import failure,
            # not merely an incomplete probe of the function's line coverage.
            return {'complete':False,'failed':[],'checked':[],
                    'import_errors':[parts[0]],'setup_errors':[],
                    'error':type(error).__name__}
        cls=getattr(module,parts[1],None)
        if not isinstance(cls,type) or not issubclass(cls,unittest.TestCase):return None
        case=cls(parts[2]);cases.append(case);names[id(case)]=nodeid
    outcomes={};import_errors=[];details=[];setup_errors=[];pending=[]
    def timed_out(*unused):raise TimeoutError('Bounded online test exceeded its deadline')
    signal.signal(signal.SIGALRM,timed_out)
    class Result(unittest.TestResult):
        failfast=True
        def __init__(self):
            super().__init__();self.failfast=True
        def startTest(self,test):
            self.flush_progress()
            super().startTest(test)
            signal.setitimer(signal.ITIMER_REAL,case_timeout)
        def stopTest(self,test):
            signal.setitimer(signal.ITIMER_REAL,0)
            super().stopTest(test)
            nodeid=names.get(id(test))
            if nodeid in outcomes:pending.append(nodeid)
        def flush_progress(self):
            # A previous case is reported only after its fixture cleanup has
            # completed. A timeout during class cleanup leaves its last case
            # unconfirmed, so the selector retains that test.
            if on_progress and pending:
                on_progress({'checked':list(pending),
                             'failed':[n for n in pending if outcomes[n]=='failed'],
                             'import_errors':list(import_errors),
                             'setup_errors':list(setup_errors)})
            pending.clear()
        def stop(self):
            # failfast ends the current failed subtest method, but the suite
            # continues to independently check the remaining test methods.
            if import_errors or setup_errors:super().stop()
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
    result.flush_progress()
    return {'complete':all(nodeid in outcomes for nodeid in test_ids),
            'failed':sorted(n for n,outcome in outcomes.items() if outcome=='failed'),
            'checked':sorted(outcomes),'import_errors':import_errors,
            'setup_errors':setup_errors,'details':details,'exit':0 if result.wasSuccessful() else 1}
