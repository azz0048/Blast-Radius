# Blast Radius v4

v4 adds exact stand-ins for the slowest tests, which v3 kept whenever the baseline map flagged
them (they are too slow to trial-run):

| Test | Recorded | Stand-in | Stand-in cost |
| --- | --- | --- | --- |
| `test_execute_tpcds` | 21.5 s | TPC-DS fingerprint | ~4.5 s |
| `test_execute_tpch`, `test_optimized_tpch` | 3.9 s, 3.5 s | TPC-H fingerprint | ~0.4 s |
| `test_max_nodes` | 28.8 s | same assertions at 1/10 width, limits scaled by 1/10 | ~2.7 s |

* **TPC executor tests** (`proxy.py`). Their outcome is a function of what sqlglot computes for the
  fixture queries: the DuckDB SQL they are transpiled to, the parsed (pickled) and optimized trees,
  the tables found, the plan, and the Python code generated for every plan step. In the patched
  child these are hashed and compared with the pinned commit's hash, computed from the pinned
  source while the image builds. Equal hashes remove the tests. Any patch touching the executor
  runtime, planner, Python generator, helpers, schema, the test file, `tests/helpers.py` or the
  TPC fixtures keeps them. The test module itself is imported first, so import breakage keeps them.
* **test_max_nodes**. Node counts grow exactly with the width, so every limit is exceeded or not
  exactly as in the full test; all other assertions are unchanged. Patches touching
  `tests/test_parser.py` or `tests/helpers.py` keep it.

Validation: 0 misses on the 30 sample commits (157.7 s simulated + CPU, v3: 165.2 s); the stand-ins
never dropped a failing test on the sample (2jrq, 3utz keep `test_execute_tpcds`); 25 random
mutations in optimizer/parser/generator/DuckDB code where the fingerprint was unchanged all passed
the real executor tests; 14 random parser/tokenizer mutations agreed with the real `test_max_nodes`,
and 4 crafted node-limit breakages were all kept.

# Blast Radius v3

This revision addresses two accuracy risks in v2: skipping an expensive test based on a different passing test, and failing to classify an error while importing an unchanged test module as a collection failure.

The hidden scorer reported one miss for v2 but did not identify the commit, so these fixes cannot establish that the specific hidden miss is gone. Keep the previously scored **480.0-second entry** selected until a new scored submission improves on it.

## Validation

| Check | Result |
| --- | --- |
| Public sample patches | 30 |
| Missed sample commits | 0 |
| Simulated public sample cost | 150.992 seconds |
| CPU in the read-only verification | 11.173 seconds |
| Total in that local run | 162.165 seconds |
| Focused selector, runner, and regression tests | 36 passed |
| Additional generated changes | 6 |
| Inexpensive tests checked per generated change | 1,604 |
| Observed failing tests omitted in those checks | 0 |

The additional audit excludes 16 expensive tests and is not a hidden-accuracy guarantee. It generated fresh scalar-flag and function-condition changes from baseline source. It does not read the sample answer key, and its results are never used by the runtime selector.

The read-only verification used Python 3.12.14, UID/GID 65534, two-CPU affinity, a read-only mount namespace, and a 64 MiB temporary filesystem. Peak process RSS was about 588 MiB. It was not an actual Docker run and did not enforce the scorer's cgroup memory or network limits. The Dockerfile and package pins are unchanged from the v2 image that the grader built successfully.

For context, the earlier local public-sample totals were about 337.3 seconds for v1 and 113.8 seconds for v2. V2's lower sample total did not generalize: its official hidden score was 621.6 seconds with one miss. Public-sample totals should not be compared directly with hidden scores or used to promise a leaderboard placement.

## Update and submit

1. Extract this ZIP.
2. Open https://github.com/azz0048/Blast-Radius and choose **Add file → Upload files**.
3. Upload all extracted contents at the repository root, replacing existing files. Keep Dockerfile at the top level.
4. Use a commit message such as **Upgrade to v3: preserve untested tests**.
5. With Docker and make available, run `make sample`.
6. Submit the public repository and the new full commit SHA on the competition page. A blank SHA selects the latest default-branch commit.
7. Inspect the score and miss count. Leave the 480.0-second result selected unless the new result is better.

Do not select the 621.6-second v2 entry as your final result.

## What changed

- A test is removed only when that exact test completed and passed. An unexecuted test stays selected.
- Probes continue to other test methods after a failure, while a failing subtest can end its own method early.
- Confirmed results stream from the child. A deadline or memory guard retains the unconfirmed remainder.
- The last case is not confirmed until its fixture cleanup has completed. Import, class-setup, or class-cleanup errors trigger a broad fallback.
- An exception while importing an unchanged test file is reported explicitly, including when changed library code caused it.
- Probe budgets depend on the maximum simulated cost that pruning could save. A probe is skipped when unexecuted expensive tests already determine the worker cost. Precise class-field selections still receive an initialization check.
- The total probe budget, per-case timeouts, RSS guard, and bounded DuckDB memory and threads remain enabled.

The baseline coverage, constructor, scalar-field, and dictionary-key maps are unchanged. They predict candidate tests and can still have limitations for unseen edits. Online passing results apply to the candidates actually executed; they are not a proof of complete hidden recall.

## Runtime and scoring

The pinned baseline is:

```text
eb11aec4be8cb2122afe713a4816662c3f0fb2fa
```

The Docker build installs pinned runtime requirements from PyPI, fetches that exact public sqlglot revision, and precompiles bytecode. The container runs as UID/GID 65534. Answer JSON lines go to stdout and diagnostics to stderr.

The selector uses baseline dependencies to find candidates, checks selected inexpensive cases against the **current runtime patch** in isolated in-memory trials, and balances retained tests across one through eight workers with the two-second fee per worker. Trial CPU is charged normally. No output is reused between container runs.

Collectors use only unchanged baseline source. There are no stored patch-to-failure answers. Samples, answer keys, audit results, collectors, and local validation reports are excluded from the runtime image.

## Run checks

With Docker:

```sh
make sample
```

Without Docker, use a separate checkout of the pinned baseline and Python 3.12 or newer:

```sh
python3 -m pip install -r runtime-requirements.txt
python3 -m unittest -v test_selector test_probes test_refine
SQLGLOT_SOURCE=/absolute/path/to/sqlglot-source BR_PROBE=1 python3 verify.py
```

The environment-variable command uses POSIX syntax. On Windows, use WSL or set those variables in PowerShell first. `verify.py` writes `output.jsonl` and `validation.json`; only that checker reads sample answers after the selector exits.

The extra local audit can be reproduced with:

```sh
python3 verify_generalization.py /absolute/path/to/sqlglot-source
```

It takes about two minutes here and writes `generalization-validation.json`. It is a diagnostic, not a source of runtime answers.

## Regenerate baseline maps

The included maps avoid running the entire baseline suite during each image build. Install `map-requirements.txt` and use an unchanged external checkout:

```sh
python3 build_map.py /absolute/path/to/sqlglot-source --output analysis.json.gz
python3 build_usage.py /absolute/path/to/sqlglot-source --output usage.json.gz
python3 build_attributes.py /absolute/path/to/sqlglot-source --output attributes.json.gz
python3 build_dictionary_reads.py /absolute/path/to/sqlglot-source --output dictionary_reads.json.gz
```

Each collector executes baseline tests. Keep that source checkout outside the submission repository. The unchanged full baseline previously passed 1,621 tests. `requirements.lock` records the larger development environment; the Docker build uses `runtime-requirements.txt`. The source index is covered by `LICENSE.sqlglot`.
