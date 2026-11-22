# Blast Radius v2

An updated submission for RWX's Blast Radius track. The Dockerfile is at the repository root.

## Local results

| Check | Result |
| --- | --- |
| Public sample patches | 30 |
| Missed sample commits | 0 |
| Simulated cost, original scorer | 94.790 seconds |
| CPU, including probe children | 18.977 seconds |
| Total in this local run | 113.767 seconds |
| Earlier ZIP on the same sample | About 337.3 seconds total |
| Selector and isolation checks | 27 passed |
| Unmodified baseline collection | 1,621 tests passed |
| Direct runner on inexpensive baseline cases | 1,604 passed |
| Peak process RSS reported locally | About 604 MiB |

These are **public-sample results**, not a hidden leaderboard score. Your existing 480.0-second submission remains a separate entry. Sample performance cannot establish top-ten placement.

The supplied `score.py` is unchanged. Native checks used Python 3.12.14 and two-CPU affinity. The image uses pinned Python 3.13. Docker was unavailable here, so verify the build and actual container restrictions with `make sample`. Native checks report resident memory and do not impose a virtual-address-space cap on DuckDB; this is not an equivalent substitute for the scorer's physical-memory limit.

## Update your submission

1. Unzip this archive.
2. Open https://github.com/azz0048/Blast-Radius and choose **Add file → Upload files**.
3. Upload the **contents** of the extracted folder to the repository root. Replace the old files, including **Dockerfile** and **selector.py**. Include every new Python module and all four `.json.gz` maps.
4. Commit with a message such as `Improve selection and validate current patches`.
5. Confirm **Dockerfile** appears on the repository's main file list. Its contents should install `runtime-requirements.txt` and fetch pinned sqlglot.
6. If Docker and make are available, run `make sample` before spending an attempt.
7. On the Blast Radius competition page, enter your public repository URL and the new full commit SHA. A blank SHA selects the current default-branch commit.
8. Submit, then check the score and missed-commit count. The competition keeps your best entry unless you explicitly choose a different final entry.

Uploading only the ZIP to GitHub will not work: the grader needs the extracted Dockerfile and project files.

## Selection approach

The baseline maps describe only the unchanged pinned revision:

```text
eb11aec4be8cb2122afe713a4816662c3f0fb2fa
```

- Line coverage and fixture reads identify candidate tests.
- Constructor use, scalar-field reads, and selected dictionary-key reads narrow class settings. Import-time cache checks restrict when the narrower maps apply.
- The **current runtime patch** is applied in memory in a fresh isolated child. Inexpensive candidate tests execute against it. Passing tests can be removed; observed failures remain. A partial probe retains the conservative selection.
- Expensive tests remain selected unless their touched dependencies have passing inexpensive representatives. This is a prediction: different inputs can expose failures that a representative did not.
- Syntax errors, unsupported changes, missing modules, import errors, and class/module setup errors use conservative fallbacks.
- The scheduler tries one through eight workers, including the two-second fee for every occupied worker.

Online checks consume real CPU, included in the reported total. A total probe budget, per-trial deadlines, an RSS guard, and bounded DuckDB memory/threads limit work.

Each probe starts from an unchanged interpreter snapshot. There are no saved outputs across container runs. The process-pool adapter preserves argument/result pickle round trips while keeping computations in the patched interpreter. The unchanged baseline and inexpensive direct runner were validated separately.

No map contains patch-to-failure answers. Collectors run only the unchanged baseline. Runtime code reads the supplied patches and test catalogue, never `sample/answers.json`. The sample, answers, collectors, and local reports are excluded from the runtime image.

## Verify locally

With Docker and make:

```sh
make sample
```

Without Docker, install the runtime packages and use a separate checkout of the exact pinned sqlglot revision:

```sh
python3 -m pip install -r runtime-requirements.txt
python3 -m unittest -v test_selector test_probes
SQLGLOT_SOURCE=/absolute/path/to/sqlglot-source BR_PROBE=1 python3 verify.py
```

The environment-variable command uses POSIX shell syntax. On Windows, use WSL or set the variables in PowerShell first.

`verify.py` writes `output.jsonl` and `validation.json`. Only the verification process reads sample answers after the selector exits. Without a pinned source checkout, the selector uses static selection. Set `BR_PROBE=0` to test static selection explicitly.

## Regenerate the maps

The maps are included, so the Docker build does not run the baseline suite. To regenerate them, install `map-requirements.txt`, use an unchanged external checkout, and run from this project directory:

```sh
python3 build_map.py /absolute/path/to/sqlglot-source --output analysis.json.gz
python3 build_usage.py /absolute/path/to/sqlglot-source --output usage.json.gz
python3 build_attributes.py /absolute/path/to/sqlglot-source --output attributes.json.gz
python3 build_dictionary_reads.py /absolute/path/to/sqlglot-source --output dictionary_reads.json.gz
```

Each collector executes baseline tests; together these commands can take several minutes. Keep the source checkout outside the submission repository. `requirements.lock` records the larger baseline development environment; the image installs the smaller `runtime-requirements.txt`.

The image downloads dependencies from PyPI and the exact public sqlglot revision at build time, precompiles Python bytecode, then runs without network access as UID/GID 65534. Answer JSON goes to stdout; diagnostics go to stderr. The source index is covered by `LICENSE.sqlglot`.
