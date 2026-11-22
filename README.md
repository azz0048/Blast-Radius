# Blast Radius V6

Select tests for independent changes against SQLGlot revision
`eb11aec4be8cb2122afe713a4816662c3f0fb2fa`, then schedule them across up to eight
workers with the required worker fees.

## V6 change

V6 adds input and call-order analysis for Boolean expressions. A test can reach
an edited expression even when the new operator returns the same value. For
example, both `read or dialect` and `read and dialect` return `None` when both
inputs are `None`.

The baseline collector records primitive truth information, read-only input
snapshots, and the original order of calls it cannot prove read-only. At runtime,
the selector interprets the current edit against those observations. It removes
a test only when every observation preserves the expression's result and all
potentially effectful evaluations. This applies to conditions, return values,
function arguments, assignments, and other supported value contexts.

`None`, Boolean values, and opaque objects are distinguished. Opaque return
values must retain their originating expression and effect prefix. Unknown truth
methods, unobserved calls, changed accessor implementations, incomplete
observations, import-time uncertainty, and observation caps retain conservative
selections. The earlier coverage, type, dictionary, and bounded live-test checks
remain in place.

## Validation

- The final collector passed all 1,621 unchanged baseline tests and 25,454
  subtests, recording 2,249 expression sites and 1,657 input patterns.
- All 111 project regression tests passed.
- All 30 public sample commits passed with zero misses.
- Six independent, freshly generated Boolean edits were audited. All 121 tests
  newly excluded relative to V5 completed and passed, including expensive
  executor tests. See `flow-validation.json`.
- The packaged native read-only check measured **96.828 simulated seconds +
  11.952 CPU seconds = 108.780 seconds**, with zero misses and about 860 MiB peak
  RSS. See `readonly-validation.json`.

The public sample total is roughly unchanged from V5. The audit's per-edit
savings compare static selections on those fresh edits; they are not an official
leaderboard score or an estimate of hidden performance. This version has not yet
been scored on hidden commits, and top-ten placement is unverified.

The native check used Python 3.12.14, two CPU affinity, UID/GID 65534, a read-only
root, and a 64 MiB `/tmp`. It was not a Docker build, cgroup memory-limit test, or
network-isolated run. The image uses the same pinned Python 3.13 base and runtime
requirements as V5, with no new runtime package.

## Submit

1. Upload the extracted ZIP's contents to the existing public repository,
   replacing the corresponding files. `Dockerfile` must be at the root.
2. Commit the changes on the default branch.
3. Submit the repository URL. Leave the commit field blank to score the latest
   default-branch commit, or copy its complete 40-character GitHub commit ID.
4. Keep the current 341.8-second best selected until a better result is scored.

## Local commands

```sh
make sample
```

The Makefile uses the supplied Docker limits and scorer. Its reported simulated
time does not include CPU time, which the official scorer adds.

With runtime requirements installed and the pinned checkout available:

```sh
python -m unittest discover -p 'test_*.py'
SQLGLOT_SOURCE=/path/to/pinned/sqlglot BR_PROBE=1 python verify.py
SQLGLOT_SOURCE=/path/to/pinned/sqlglot BR_PROBE=1 python verify_readonly.py
python verify_flow.py /path/to/pinned/sqlglot --count 6
```

`verify_readonly.py` requires Linux and bubblewrap. The mutation audit uses a
separate, larger offline test budget so it can finish expensive tests; production
probe limits are unchanged. An incomplete audit is reported explicitly and
causes a nonzero exit.

## Data provenance and runtime

All committed maps come from the unchanged pinned source, its tests, and its
fixtures. The flow map contains original input and evaluation-order facts. It
does not contain patches, mutation answers, or a patch/edit-kind-to-failure
lookup table. Changed operators are interpreted only at runtime.

The Dockerfile's explicit COPY list excludes public sample answers, audit plans,
test generators, and validation reports. The image fetches only the allowed
pinned SQLGlot source. It does not contact team-controlled endpoints, fetch new
data at runtime, or reuse outputs across container runs.

The program reads `/data/tests.json` and `/data/commits/*.patch`. It prints one
JSON schedule per commit on stdout and sends diagnostics to stderr. It uses the
supplied stable test IDs and numbers. Unsupported changes and unknown tests
retain conservative selections.

Each live trial uses a fresh fork with the current patch applied in memory. A
test can be removed by a live probe only after that exact test completed and
passed. Passing representatives never replace unexecuted tests. Deadlines and
resident-memory guards bound the work.

## Rebuild the flow map offline

With the pinned checkout and map requirements installed:

```sh
python build_branch_flow.py /path/to/pinned/sqlglot
```

The collector runs only the unchanged full suite. It publishes its map atomically
only when the full collection matches and every baseline test passes. Collection
can take more than ten minutes locally; it is an offline preparation step and
is not run during the image build. The prebuilt map is committed as a regular
file. Other `build_*.py` scripts reproduce the preceding maps.

SQLGlot's license is included in `LICENSE.sqlglot`.
