"""Run the selector and the official public-sample scorer without Docker."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

try:
    import resource
except ImportError:
    resource = None

import score


def child_limits():
    # A cgroup memory limit counts resident memory, not virtual reservations.
    # DuckDB's allocator reserves address space without committing that RAM.
    if os.environ.get('BR_PROBE') != '1':
        resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))
    if hasattr(os, 'sched_getaffinity'):
        os.sched_setaffinity(0, set(sorted(os.sched_getaffinity(0))[:2]))


def main():
    root = Path(__file__).resolve().parent
    before = resource.getrusage(resource.RUSAGE_CHILDREN) if resource else None
    started = time.monotonic()
    output = root / 'output.jsonl'
    with output.open('wb') as destination:
        process = subprocess.run(
            [sys.executable, str(root / 'selector.py'), '--data', str(root / 'sample')],
            stdout=destination, stderr=subprocess.PIPE, timeout=180,
            **({'preexec_fn': child_limits} if resource and os.name == 'posix' else {}),
        )
    wall = time.monotonic() - started
    after = resource.getrusage(resource.RUSAGE_CHILDREN) if resource else None
    cpu = ((after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime)) if resource else None
    tests = json.loads((root / 'sample/tests.json').read_text())['tests']
    answers = json.loads((root / 'sample/answers.json').read_text())['commits']
    result = score.evaluate_run(output.read_bytes(), tests, answers)
    report = {
        'exit_code': process.returncode,
        'cpu_seconds': cpu,
        'wall_seconds': wall,
        'peak_rss_kib': after.ru_maxrss if after else None,
        'evaluation': result,
        'output_bytes': output.stat().st_size,
        'environment': f'Native Python {sys.version.split()[0]}; not a Docker run',
    }
    (root / 'validation.json').write_text(json.dumps(report, indent=2) + '\n')
    print(process.stderr.decode(), file=sys.stderr, end='')
    print(json.dumps(report, indent=2))
    return 0 if process.returncode == 0 and result['ok'] and not result['missed_commits'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
