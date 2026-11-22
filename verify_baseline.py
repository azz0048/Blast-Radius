"""Validate the direct runner on the unchanged inexpensive/moderate baseline."""
import argparse,json,os,resource,time
from pathlib import Path
from probe_runner import Prober
import probe_runner
from test_execution import run_tests

def main():
    ap=argparse.ArgumentParser();ap.add_argument('checkout',type=Path)
    ap.add_argument('--output',type=Path,default=Path('native-baseline-validation.json'));a=ap.parse_args()
    if hasattr(os,'sched_getaffinity'):os.sched_setaffinity(0,set(sorted(os.sched_getaffinity(0))[:2]))
    project=Path(__file__).resolve().parent
    # This validates runner fidelity independently of the shorter production
    # probe deadline. Production timeouts retain the affected test.
    probe_runner.run_tests=lambda *args,**kwargs:run_tests(*args,**kwargs,case_timeout=15)
    tests=json.loads((project/'sample/tests.json').read_text())['tests']
    ids=[t['id'] for t in tests if t['seconds']<=3]
    before=resource.getrusage(resource.RUSAGE_CHILDREN);started=time.monotonic()
    result=Prober(a.checkout).run({},ids,seconds=80)
    after=resource.getrusage(resource.RUSAGE_CHILDREN)
    report={'case_count':len(ids),'result':result,'wall_seconds':time.monotonic()-started,
            'child_cpu_seconds':after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,
            'peak_rss_kib':after.ru_maxrss,'case_timeout_seconds':15,
            'note':'Native baseline runner check; no mutations or answer keys. Longer case timeout than production probes.'}
    a.output.write_text(json.dumps(report,indent=2)+'\n')
    summary={k:v for k,v in report.items() if k!='result'}
    summary.update({'complete':result.get('complete'),'failed':result.get('failed',[]),
                    'checked':len(result.get('checked',[])),
                    'import_errors':result.get('import_errors',[]),'setup_errors':result.get('setup_errors',[])})
    print(json.dumps(summary,indent=2))
    return 0 if result.get('complete') and not result.get('failed') and not result.get('import_errors') and not result.get('setup_errors') else 1

if __name__=='__main__':raise SystemExit(main())
