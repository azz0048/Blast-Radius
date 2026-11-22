# Blast Radius V5

A baseline coverage selector with bounded execution of the current patch. It
prints the required worker schedules for independent changes to SQLGlot at
`eb11aec4be8cb2122afe713a4816662c3f0fb2fa`.

## What changed

V5 adds coverage for individual Boolean expressions, comparisons, binary
operations, and calls with literal arguments. A long return statement can be
executed without reaching its changed subexpression. The new map distinguishes
those cases instead of selecting everyone who reached the surrounding line.
Expression reachability can also be combined across files.

A second map records primitive operand ranges for literal comparisons. At run
time, it can prove that a changed boundary or literal gives the same Boolean
result for a particular test. Unknown types and uncertain ranges remain selected.

The new maps contain **only facts collected from the unchanged pinned source**.
They do not contain patches, mutation answers, sample commit IDs, or a table
mapping edit kinds to failures. Patch comparisons and selection happen at runtime.

## Local evidence

- Both new collectors passed all 1,621 baseline tests and 25,454 subtests.
- All 91 project regression tests passed.
- The 30 public sample commits passed with zero misses.
- The final read-only native check measured 96.757 simulated seconds plus 11.276
  CPU seconds: **108.033 seconds total**. An earlier check measured 106.367
  seconds. See `readonly-validation.json` and `readonly-validation-earlier.json`.
- Five fresh local mutations were audited: all **3,243 newly excluded test
  executions** completed and passed. See `predicate-validation.json`.

The public sample's simulated cost is unchanged from V4. Its smaller candidate
sets did not reduce the slowest remaining worker on this sample. These numbers
do not establish an improvement on hidden commits or a top-ten ranking.

The native check used Python 3.12.14, UID/GID 65534, two CPU affinity, a read-only
root, and a 64 MiB `/tmp`. It was not a Docker build, cgroup memory-limit check,
or network-isolated run. The submitted image uses the same pinned Python 3.13
base and runtime requirements as V4; no new runtime package is required.

`verify_predicates.py` generates fresh local mutations and executes every test
newly removed by the added metadata. It is an offline diagnostic, excluded from
the image. Incomplete checks are reported explicitly and make that script fail.

## Submission

1. Upload this folder's contents to your public GitHub repository, replacing the
   existing files. `Dockerfile` and `selector.py` must be at the repository root.
2. Commit the uploaded changes.
3. Submit the repository URL. Leave the commit field empty to score the newest
   default-branch commit, or copy the full 40-character commit ID from GitHub.
4. Keep the previous best entry selected until the new entry is scored. A local
   commit or ZIP checksum is not a substitute for a pushed GitHub commit ID.

## Run locally with Docker

```sh
make sample
```

This builds the linux/amd64 image and uses the supplied scorer, with two CPUs,
4 GiB memory, no network, a read-only filesystem, and UID 65534. `make sample`
reports simulated seconds; the competition also charges CPU time.

For a native environment with the runtime requirements installed:

```sh
python -m unittest discover -p 'test_*.py'
SQLGLOT_SOURCE=/path/to/pinned/sqlglot BR_PROBE=1 python verify.py
SQLGLOT_SOURCE=/path/to/pinned/sqlglot BR_PROBE=1 python verify_readonly.py
python verify_predicates.py /path/to/pinned/sqlglot --limit 5 --candidate-limit 120
```

`verify_readonly.py` needs Linux and bubblewrap. The larger memory and per-test
time allowances in the offline mutation audit do not change production limits.

## Runtime behavior

The selector reads `/data/tests.json` and `/data/commits/*.patch`, maps stable test
IDs to the supplied numbers, and prints one JSON line per commit. Diagnostics go
to stderr. It evaluates schedules with one through eight workers, including the
two-second worker fee.

Unsupported edits, initialization failures, unknown catalogue entries, source
readers, and relevant introspection retain conservative selections. A live
probe can remove a test only after that exact test completed and passed. A
passing representative never substitutes for an unexecuted expensive test.
Each trial starts from a fresh fork with the current patch applied in memory.
Deadlines and resident-memory checks bound online work.

The image fetches only the allowed pinned SQLGlot revision. Maps are committed
as regular files and are not rebuilt during Docker builds. The Dockerfile's
explicit COPY list excludes sample answers, audit scripts, and validation
reports. No answer or result cache persists across container runs.

## New map reproduction

With the pinned checkout and `map-requirements.txt` installed:

```sh
python build_expressions.py /path/to/pinned/sqlglot
python build_values.py /path/to/pinned/sqlglot
```

Each collector runs the unchanged full suite and atomically publishes its map
only when every baseline test passes and the collection matches. Other
`build_*.py` scripts reproduce the existing coverage, usage, dictionary,
attribute, and type metadata. These are offline tools and never inspect sample
or hidden mutation answers. SQLGlot's license is in `LICENSE.sqlglot`.
