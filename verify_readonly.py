"""Local scorer check in a read-only namespace, using bubblewrap, not Docker."""
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time
import score


def main():
    root=Path(__file__).resolve().parent
    before=resource.getrusage(resource.RUSAGE_CHILDREN);start=time.monotonic()
    command=['bwrap','--unshare-user','--uid','65534','--gid','65534',
             '--ro-bind','/','/','--proc','/proc','--dev','/dev',
             '--size','67108864','--tmpfs','/tmp','--setenv','HOME','/tmp',
             '--',sys.executable,str(root/'selector.py'),'--data',str(root/'sample')]
    def affinity():os.sched_setaffinity(0,set(sorted(os.sched_getaffinity(0))[:2]))
    output=root/'output-readonly.jsonl'
    with output.open('wb') as handle:
        process=subprocess.run(command,stdout=handle,stderr=subprocess.PIPE,timeout=180,preexec_fn=affinity)
    elapsed=time.monotonic()-start;after=resource.getrusage(resource.RUSAGE_CHILDREN)
    tests=json.loads((root/'sample/tests.json').read_text())['tests']
    answers=json.loads((root/'sample/answers.json').read_text())['commits']
    evaluation=score.evaluate_run(output.read_bytes(),tests,answers)
    report={'exit_code':process.returncode,'cpu_seconds':after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,
            'wall_seconds':elapsed,'peak_rss_kib':after.ru_maxrss,'evaluation':evaluation,
            'output_bytes':output.stat().st_size,
            'environment':f'Native Python {sys.version.split()[0]}; UID/GID 65534, read-only root, two CPU affinity, 64 MiB /tmp. No Docker, cgroup memory limit or network namespace.'}
    (root/'readonly-validation.json').write_text(json.dumps(report,indent=2)+'\n')
    print(process.stderr.decode(),file=sys.stderr,end='');print(json.dumps(report,indent=2))
    return int(process.returncode!=0 or not evaluation['ok'] or bool(evaluation.get('missed_commits')))


if __name__=='__main__':raise SystemExit(main())
