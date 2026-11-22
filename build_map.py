"""Collect dependency data by running ONLY the unmodified pinned revision.

No patches or answer files are read. The resulting index is reusable for new
patches and arbitrary test numbering. Requires pytest, coverage, numpy, pandas,
duckdb and pytest-timeout in the Python environment used for this build step.
"""
from __future__ import annotations

import argparse
import builtins
import concurrent.futures
import gzip
import io
import json
import os
import pickle
from pathlib import Path
import sys
import time

PIN = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--output", type=Path, default=Path("analysis.json.gz"))
    parser.add_argument("--test-path", action="append", default=[])
    args = parser.parse_args()
    root = args.checkout.resolve()
    destination = args.output.resolve()
    os.chdir(root)
    os.environ["SKIP_INTEGRATION"] = "0"
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    sys.path.insert(0, str(root))
    import coverage
    import pytest

    # TestExecutor's spawn pool otherwise escapes the parent coverage tracer.
    # A single traced worker executes the same unchanged test computations.
    original_pool = concurrent.futures.ProcessPoolExecutor

    class TracedPool(concurrent.futures.ThreadPoolExecutor):
        def submit(self, fn, /, *positional, **kwargs):
            # Preserve IPC serialization dependencies in the traced worker.
            fn, positional, kwargs = pickle.loads(pickle.dumps((fn, positional, kwargs)))
            def invoke():
                return pickle.loads(pickle.dumps(fn(*positional, **kwargs)))
            return super().submit(invoke)

    def traced_pool(*positional, **kwargs):
        kwargs.pop("mp_context", None)
        kwargs.pop("max_tasks_per_child", None)
        kwargs["max_workers"] = 1
        return TracedPool(**kwargs)

    concurrent.futures.ProcessPoolExecutor = traced_pool
    cov = coverage.Coverage(
        data_file=None,
        include=[str(root / "sqlglot" / "*"), str(root / "tests" / "*")],
        concurrency=["thread"],
    )
    active = [""]
    phase = [""]
    reads: dict[str, set[str]] = {}
    setup_reads: dict[str, set[str]] = {}
    collected: list[str] = []
    results: dict[str, str] = {}
    old_open, old_io_open = builtins.open, io.open
    root_string = str(root) + os.sep

    def record(file, mode="r"):
        if not active[0] or not isinstance(file, (str, bytes, os.PathLike)):
            return
        if isinstance(mode, str) and not ("r" in mode or "+" in mode):
            return
        try:
            path = os.path.abspath(os.fsdecode(file))
            if path.startswith(root_string):
                rel = path[len(root_string):].replace(os.sep, "/")
                if "__pycache__" in rel:
                    return
                reads.setdefault(rel, set()).add(active[0])
                if phase[0] == "setup":
                    group = active[0].rsplit("::", 1)[0]
                    setup_reads.setdefault(group, set()).add(rel)
        except (OSError, TypeError, ValueError):
            pass

    def tracked_open(file, mode="r", *a, **kw):
        record(file, mode)
        return old_open(file, mode, *a, **kw)

    def tracked_io_open(file, mode="r", *a, **kw):
        record(file, mode)
        return old_io_open(file, mode, *a, **kw)

    class Plugin:
        def pytest_collection_modifyitems(self, items):
            collected.extend(item.nodeid for item in items)

        def pytest_runtest_setup(self, item):
            active[0] = item.nodeid
            phase[0] = "setup"
            cov.switch_context(item.nodeid + "|setup")

        def pytest_runtest_call(self, item):
            active[0] = item.nodeid
            phase[0] = "call"
            cov.switch_context(item.nodeid + "|call")

        def pytest_runtest_teardown(self, item):
            phase[0] = "teardown"
            cov.switch_context(item.nodeid + "|teardown")

        def pytest_runtest_logreport(self, report):
            if report.failed:
                results[report.nodeid] = "failed"
            elif report.when == "call":
                results.setdefault(report.nodeid, report.outcome)

    cov.start()
    builtins.open, io.open = tracked_open, tracked_io_open
    start = time.monotonic()
    try:
        status = pytest.main(
            [*(args.test_path or ["tests"]), "-q", "--tb=short", "-p", "pytest_timeout", "--timeout=180"],
            plugins=[Plugin()],
        )
    finally:
        active[0] = ""
        builtins.open, io.open = old_open, old_io_open
        concurrent.futures.ProcessPoolExecutor = original_pool
        cov.stop()
    print(f"Coverage test run: exit={status}, elapsed={time.monotonic()-start:.1f}s", file=sys.stderr)
    data = cov.get_data()
    known = set(collected)
    groups = {}
    for nodeid in collected:
        groups.setdefault(nodeid.rsplit("::", 1)[0], set()).add(nodeid)
    sources = {}
    for base in (root / "sqlglot", root / "tests"):
        for path in base.rglob("*.py"):
            if "__pycache__" not in path.parts:
                sources[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    line_map = {}
    module_map = {}
    for filename in sorted(data.measured_files()):
        if not filename.startswith(root_string):
            continue
        rel = filename[len(root_string):].replace(os.sep, "/")
        lines = {}
        union = set()
        for line, contexts in data.contexts_by_lineno(filename).items():
            names = set()
            for context in contexts:
                nodeid, _, context_phase = context.partition("|")
                if nodeid in known:
                    if context_phase == "setup":
                        names.update(groups[nodeid.rsplit("::", 1)[0]])
                    else:
                        names.add(nodeid)
            names = sorted(names)
            if names:
                lines[str(line)] = names
                union.update(names)
        if lines:
            line_map[rel] = lines
            module_map[rel] = sorted(union)
    for nodeid in collected:
        group = nodeid.rsplit("::", 1)[0]
        for rel in setup_reads.get(group, ()):
            reads.setdefault(rel, set()).add(nodeid)
    collection = sorted(collected)
    number = {name: i for i, name in enumerate(collection)}
    # Integer references substantially reduce both loading CPU and memory.
    for mapping in line_map.values():
        for line, ids in mapping.items():
            mapping[line] = [number[name] for name in ids]
    module_map = {path: [number[name] for name in ids] for path, ids in module_map.items()}
    compact_reads = {path: sorted(number[name] for name in ids) for path, ids in sorted(reads.items())}
    output = {
        "format": 2,
        "pinned_commit": PIN,
        "provenance": "Only the unmodified pinned source and its own tests were executed.",
        "collection": collection,
        "baseline_outcomes": results,
        "sources": sources,
        "lines": line_map,
        "modules": module_map,
        "reads": compact_reads,
    }
    with gzip.open(destination, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(output, f, separators=(",", ":"), ensure_ascii=True)
    print(f"Wrote {destination}: {destination.stat().st_size:,} bytes, "
          f"{len(collected)} tests, {len(line_map)} code files, {len(reads)} read files", file=sys.stderr)
    failures = sorted(k for k, v in results.items() if v == "failed")
    print("Baseline failures:", failures, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
