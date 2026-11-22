# Blast Radius — coverage-based test selection

A standalone submission for RWX's Blast Radius track. The Dockerfile is at the project root. The selector has **no runtime package dependencies** beyond Python's standard library.

## Public sample validation

| Check | Result |
| --- | --- |
| Public sample changes | 30 |
| Missed sample changes | 0 |
| Official simulated sample score | 335.485 seconds |
| Original starter on the same sample | 1210.470 seconds |
| Simulated-score reduction | 72.28% |
| Selector CPU time in the local run | About 1.79 seconds |
| Selector peak memory in the local run | About 309 MiB |
| Selector unit tests | 15 passed |

The simulated score uses the original supplied `score.py`, unmodified. It is not an official leaderboard submission. The server adds its measured CPU time and scores hidden changes. Sample success does not guarantee hidden accuracy. Native checks used Python 3.12.14 with a two-CPU affinity and a 4 GiB address-space limit. Docker was not available in the assembly workspace, so run `make sample` to verify the actual container on your machine.

## Submit this project

1. Extract the ZIP.
2. Create a new public GitHub repository, for example `Blast-Radius`.
3. Upload the contents of this project folder, including `analysis.json.gz` and `selector.py`. `Dockerfile` must appear on the repository's main file-list page.
4. Open the Blast Radius track, paste the repository URL, and optionally supply the full commit SHA. Leaving the commit blank selects the latest commit on the default branch.
5. Submit after the checks below pass. Check the live page for your team's remaining attempts.

## Run the checks

No third-party packages are needed for these commands:

```sh
python3 -m unittest -v test_selector
python3 verify.py
```

`verify.py` writes the selected tests to `output.jsonl` and results to `validation.json`. It reads sample answers only to evaluate the selector's completed output; the selector itself never reads them.

To use the supplied Docker harness and its container restrictions:

```sh
make sample
```

For a normal container run, the scorer mounts `/data/tests.json` and `/data/commits`. The image runs as UID/GID 65534, writes only answer JSON lines to stdout, and sends diagnostics to stderr. Nothing needs to be written to `/app` or installed at runtime.

## Selection approach

- A dependency index records which baseline test identities execute each code line and read each fixture.
- Incoming unified patches are compared to source from the pinned revision. The index contains no hidden patches or patch-specific answers.
- Changed statements and their entry points are considered, including multiline conditions that previously short-circuited.
- Python files are parsed as bytes, with encoding declarations and type comments respected. Executable AST comparisons identify structurally unchanged Python edits; observed source-file consumers remain selected.
- Changes to import-time declarations, syntax failures, and unsupported cases use conservative fallbacks, including the full suite when necessary.
- Fixture changes select their observed consumers. New resources use source-derived references and a conservative fallback.
- For each selection, the scheduler evaluates one through eight workers, balancing recorded test durations and the two-second cost for every occupied worker.

This favors avoiding the 300-second miss penalty. The broad import-time fallbacks are a clear area for future improvement.

## Dependency-index provenance and regeneration

Pinned sqlglot revision:

```text
eb11aec4be8cb2122afe713a4816662c3f0fb2fa
```

The committed index was built exclusively from the unmodified pinned source and its own tests. The full baseline passed 1621 tests. An additional baseline pass through the optimizer and executor suites passed 227 tests and added serialization dependencies and class-setup propagation. Neither collection pass read the sample patches or sample answers.

`build_map.py` is included. It traces code with coverage.py and records file reads. To trace worker activity in the same process, it uses a traced single-thread executor and preserves argument/result pickle round trips. It does not alter sqlglot's source files. Runtime test numbers are resolved from test identity strings, so numbering is not hardcoded.

To regenerate the entire index from the pinned checkout:

```sh
python3 -m pip install -r map-requirements.txt
git init sqlglot-source
git -C sqlglot-source fetch --depth 1 https://github.com/tobymao/sqlglot.git eb11aec4be8cb2122afe713a4816662c3f0fb2fa
git -C sqlglot-source checkout --detach FETCH_HEAD
python3 build_map.py sqlglot-source --output analysis.json.gz
```

Keep `sqlglot-source` outside the submission repository, or remove that checkout before uploading: only the small index is required for the image. The precomputed index avoids running the baseline suite during every image build.

The index includes original sqlglot source for patch comparison. Its MIT license is provided as `LICENSE.sqlglot`. The original sample files, scorer, and scoring specification are included for local verification; they are not copied into the runtime image.
