# Blast Radius v4

V4 adds baseline operand-type analysis, source checks for comparisons with None, and dictionary-copy dependencies to the zero-miss v3 selector.

This is a new, unscored candidate. The current best official result is **365.0 seconds with zero missed commits**, from v3 at `59a98106f8c51509156bae1c0b9c7c02c6257481`. Keep that entry selected until a new submission scores better. A top-ten position requires an official hidden result.

## Local validation

| Check | Result |
| --- | --- |
| Public sample patches | 30 |
| Missed public sample commits | 0 |
| Simulated sample cost | 96.757 seconds |
| CPU, including probe children | 11.065 seconds |
| Total in the recorded local run | 107.822 seconds |
| V3 total on the same public sample | About 162.2 seconds |
| Focused regression tests | 77 passed |
| Complete baseline runs for the new operand and key maps | 1,621 tests passed each |
| Direct runner check, inexpensive and moderate baseline cases | 1,615 passed |
| Additional generated changes audited | 14 |

Thirteen generated changes completed checks of 1,604 inexpensive test methods each, with no observed failing test omitted. The fourteenth intentionally broke equality during test-module import; the selector retained all 1,620 catalogue tests. The mutation audits exclude 16 expensive tests. Full baseline coverage maps include those tests, but these audits are not a guarantee of hidden recall.

The direct runner check uses a longer 15-second case limit to validate execution fidelity. Production probes have shorter limits and retain a test when it cannot be confirmed as passing.

The recorded sample run used native Python 3.12.14, UID/GID 65534, two-CPU affinity, a read-only filesystem, and a 64 MiB temporary filesystem. Peak process RSS was about 727 MiB. This was not an actual Docker build, cgroup-memory check, or network-isolation check. The image uses pinned Python 3.13 and the same dependency versions as the successfully graded v3 image.

Public-sample totals cannot be compared directly to hidden scores. The earlier v2 sample result was faster but missed a hidden commit; v4 preserves untested cases unless a separate, restricted semantic analysis supports removing them.

## Submit

1. Extract the ZIP.
2. Open https://github.com/azz0048/Blast-Radius and select **Add file → Upload files**.
3. Upload **all extracted contents** to the repository root, replacing existing files. Keep Dockerfile at the top level.
4. Use a commit message such as **Upgrade to v4: type and key dependencies**.
5. With Docker and make available, run `make sample`.
6. Submit the public repository and its new commit SHA. Leaving the SHA blank selects the newest default-branch commit.
7. Inspect the official score and missed-commit count before selecting a different final entry.

Keep every new module and metadata file from this archive together; the Dockerfile references the required runtime files explicitly.

## What changed

### Restricted comparison analysis

The operand map records only types seen while running the unchanged pinned baseline. The collector evaluates the **original operator only**. It never evaluates alternate operators, reads incoming patches, or stores mutation answers.

At runtime, the comparison analysis applies only when an entire single-file AST change consists of identity/equality substitutions with unchanged operands and polarity. Plain objects with identity equality, comparisons to None for supported built-ins, and canonical members of suitable Enum classes can support equivalence. Noncanonical Enum objects and unproved custom equality remain conservative.

Additional type properties come from a small source interpreter for the unchanged `__eq__` and `__ne__` methods. It accepts only supported effect-free paths and checks equality and inequality independently. Mutable global flags, unknown calls, recursion, instance shadowing, and unsupported statements prevent a proof.

The metadata includes the source ranges of methods used by these checks. A patch touching those implementations invalidates dependent properties. Equality-method edits otherwise begin with full-suite coverage. Source readers, bytecode/traceback consumers, and test identities missing from baseline metadata remain protected.

### Dictionary dependencies

The new key map follows unchanged dictionary copies, including copies between dialect families. It supports value-only edits with identical keys, key order, and unpack expressions. Value construction must be supported and free of import-time effects; arbitrary calls and effectful lambda defaults use wider dependencies.

Cached copies and unsupported metaclass uses disable narrow key selection. A separate cold-import pass protects dictionaries used before normal test-level tracing starts, including their parent and copied origins. This protects global values that could otherwise be invisible to later key reads.

### Online checks

V3's rule remains: an unexecuted test stays selected. Only the exact completed passing test can be removed by an online trial.

Small two-test selections may also execute moderately priced cases. Larger selections use the inexpensive-case limit. Work is bounded by potential simulated savings, per-case timeouts, a total probe budget, and a child RSS guard. Partial results retain unconfirmed cases. Import and class/module fixture failures use broad fallbacks.

No output is reused across container runs.

## Metadata and provenance

All maps derive from the unchanged revision:

```text
eb11aec4be8cb2122afe713a4816662c3f0fb2fa
```

| File | Baseline information |
| --- | --- |
| analysis.json.gz | Source, line coverage, module execution, fixture reads |
| usage.json.gz | Constructors and explicit class references |
| attributes.json.gz | Scalar-field reads and source references |
| dictionary_reads.json.gz | Existing safe dictionary-key reads |
| comparisons.json.gz | Operand types, canonical Enum membership, test contexts |
| key_flow.json.gz | Key readers, dictionary-copy origins, cache/import exclusions |
| none_traits.json | Source-derived equality properties and protected method ranges |

Runtime numbers are resolved from test identities rather than fixed test numbers. The sample answer key is used only by local scorers after the selector exits. Samples, answers, mutation audit results, collectors, and validation reports are not copied into the runtime image.

The original supplied `score.py` is unchanged. The source index is distributed with `LICENSE.sqlglot`.

## Run the checks

With Docker and make:

```sh
make sample
```

Without Docker, use Python 3.12+ and a separate unchanged checkout of the pinned source:

```sh
python3 -m pip install -r runtime-requirements.txt
python3 -m unittest -v test_selector test_probes test_refine test_comparisons test_none_semantics test_import_guards
SQLGLOT_SOURCE=/absolute/path/to/sqlglot-source BR_PROBE=1 python3 verify.py
python3 verify_baseline.py /absolute/path/to/sqlglot-source
python3 verify_generalization.py /absolute/path/to/sqlglot-source
```

The environment-variable syntax above is for a POSIX shell; on Windows, use WSL or set the variables in PowerShell. Native checks do not replace checking the actual container. The full generated-change audit takes several minutes.

## Regenerate metadata

The committed maps avoid running baseline tests in every image build. To regenerate, install `map-requirements.txt` and run from this project directory, using only the unchanged pinned checkout:

```sh
python3 build_map.py /absolute/path/to/sqlglot-source --output analysis.json.gz
python3 build_usage.py /absolute/path/to/sqlglot-source --output usage.json.gz
python3 build_attributes.py /absolute/path/to/sqlglot-source --output attributes.json.gz
python3 build_dictionary_reads.py /absolute/path/to/sqlglot-source --output dictionary_reads.json.gz
python3 build_comparisons.py /absolute/path/to/sqlglot-source --output comparisons.json.gz
python3 build_key_flow.py /absolute/path/to/sqlglot-source --output key_flow.json.gz
python3 build_none_traits.py /absolute/path/to/sqlglot-source --output none_traits.json
```

These collectors can take many minutes together. Keep the source checkout outside the submission repository. `build_key_flow.py` includes the cold-import guard pass; `import_key_guards.py` can also add those guards to an existing key-flow map.

The Docker build fetches only the pinned public sqlglot source and installs pinned PyPI packages, precompiles bytecode, then runs as UID/GID 65534. It writes answer JSON to stdout and diagnostics to stderr.
