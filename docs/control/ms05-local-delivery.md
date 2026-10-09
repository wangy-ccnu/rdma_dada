# MS-05 local implementation delivery

Freeze date: 2026-10-08 (Asia/Shanghai). Development evidence, pending independent
coordinator review and server acceptance. Keep this worktree and evidence open.

```text
TASK_ID: 01a11b7e-d6d1-7942-b378-5e520e48d2c3
TASK_TITLE: MS-05 现有编译器兼容性验收
COORDINATOR: 多服务器同步开发 / 01a090fd-55d8-7b83-9ea1-1a53eaa1b952
MODEL_POLICY: gpt-6.1-sol / high (dispatched setting; not independently introspected)
BASE_REVISION: 5851eaed6cff78179d9cee763c6d4713c1095b08
BRANCH: detached HEAD, app-managed worktree derived from codex/multi-server-observation
WORKTREE: /Users/ywang/.codex/worktrees/179b/rdma_dada
IMPLEMENTATION_RESULT: COMPLETE
ACCEPTANCE: REVIEW_PENDING / SERVER_TEST_PENDING
COMMIT_PUSH_MERGE: NOT_PERFORMED
RESULT_NOTIFICATION: FAIL
ARCHIVE_STATUS: KEEP_OPEN_PENDING_ACCEPTANCE
```

Authoritative procedure:
`/Users/ywang/WorkFile/code/rdma_dada/.worktrees/multi-server-observation/docs/agents/delegated-development.md`.
Scope: that worktree's `ms05-development-handoff.md` and implementation plan Task 5.

## Dependency and source ownership

Imported exactly the sixteen paths in coordinator
`docs/control/manifests/ms04b-reviewed-files.sha256` from
`/Users/ywang/.codex/worktrees/1209/rdma_dada`, after verifying all source hashes.
Used apply_patch, then verified all sixteen imported byte hashes. Baseline:
115/115 pure tests PASS before MS-05 implementation.

MS-05 adds/changes only:

- `scripts/multiserver/compiler.py`: explicit existing compiler adapter, report /
  artifact checks, identity evidence, non-clobbering publication and retained
  unsuccessful diagnostics.
- `scripts/multiserver_compile.py`: new required-argument `resolve` CLI;
  existing `compile` behavior retained. Structured failure JSON includes compiler
  argv, exit status and stdout/stderr where invocation occurred.
- `tests/multiserver_compile_test.py`: 18 pure rejection tests and 12 real compiler
  tests, with mandatory binary argument for integration. Negative tests cover
  unavailable binary, invocation failure, false/malformed reports, missing or
  tampered artifacts, I/O failures, output ownership and CLI diagnostics.
- `CMakeLists.txt`: six feature registrations: four existing pure suites,
  adapter unit tests and explicit-target real compiler integration.
- `docs/control/configuration.md`: resolve usage, evidence/failure semantics,
  actual int16/CI8 restriction and local/server verification.
- This delivery record.

Shared fixtures need no new production behavior. The reviewed bundle test's
`resolve` invocation has no required arguments and still correctly requires
`CLI_ARGUMENT`; no obsolete assertion needed updating. Final imported-file check
shows only CLI and configuration documentation differ from the reviewed sixteen
files. The source dependency worktree remains byte-identical to its supplied SHA
snapshot. No C++ compiler, Observation schema, algorithm, paper configuration or
accepted-results index was changed. No coordinator or paper worktree was edited.

## Behavior and recorded decisions

The adapter invokes `[BINARY, --config, INSTANCE/observation.json, --output-dir,
STAGING/artifacts]` with subprocess argument arrays, no shell or default binary.
The input path is the installed instance path. A new exclusive sibling workspace
lets it validate compiler output before atomic no-replace publication to OUTPUT;
the original compiler manifest/artifact bytes remain intact, and separate
`resolution.json` records binary/input/resource/artifact identities. Runtime
readiness remains false. Existing destinations (including empty directories),
paths overlapping the portable bundle and symlink paths are refused. The
adapter retains unsuccessful staging diagnostics and never overwrites a foreign
destination or writes through a replaced staging workspace.

Implementation decisions retained from the execution ledger:

1. Coordinator owns independent review and server handoff; generic skill
   commit/reviewer-dispatch steps do not override the delegated workflow. Cost
   if unsuitable: coordinator can request another review.
2. Full feature source manifest is deferred to coordinator review/formal-handoff
   freeze per the explicit MS-05 handoff. This delivery retains a separate local
   twenty-file identity record. Cost: full server manifest must be generated
   before synchronization/formal testing.
3. Staging artifact output is passed to the compiler and atomically published to
   the requested destination after verification; recorded argv retains that
   actual staging path. Cost: argv's historical output path differs from the
   final artifact path, both explicitly recorded.
4. Int16 full Observation resolution is an existing CI8 incompatibility, not a
   compiler change. Coordinator explicitly confirmed this disposition in this
   conversation on 2026-10-08. Cost: CI16/int16 Full compatibility needs a later
   separately scoped input-contract change.

No deferred minor review findings. Author checks do not replace coordinator
independent correctness/standards review.

## Initial-freeze tests and exact local commands

Local toolchain: CMake/CTest 4.3.2; Python 3.9.6, macOS AppleClang portable build.

```sh
python3 -B -m unittest discover -s tests -p 'multiserver_*_test.py' -v
cmake -S . -B build-ms05 -DBUILD_TESTING=ON -DBUILD_RDMA_PIPELINE=OFF -DUSE_CUDA=OFF
cmake --build build-ms05 --target observation_config_compile observation_config_test resolved_observation_plan_test observation_artifacts_test beamform_weight_metadata_test -j 4
cmake --build build-ms05 -j 4
python3 -B tests/multiserver_compile_test.py --unit
python3 -B tests/multiserver_compile_test.py build-ms05/observation_config_compile
ctest --test-dir build-ms05 -R 'multiserver|observation_config_test|resolved_observation_plan_test|observation_artifacts_test|beamform_weight_metadata_test|observation_config_compile_test' --output-on-failure
ctest --test-dir build-ms05 --output-on-failure
git diff --check
```

- Baseline: 115/115 PASS.
- Initial pure discovery: 142 discovered; 133 executed PASS (115 dependency + 18
  adapter); 9 real compiler cases explicitly skipped. Skips are not counted as
  compiler acceptance.
- Direct adapter: 18/18 PASS. Direct actual compiler integration: 9/9 PASS.
- Registered feature + relevant original regression CTest: 11/11 PASS.
- Full portable build: PASS. Final broader CTest: 53/54 PASS, one environmental
  failure `fpga_sender_sim_loopback_test`: `receiver.bind(("127.0.0.1", 0))`
  raises `PermissionError: [Errno 1] Operation not permitted` in the sandbox.
  That unchanged test remains outside MS-05 configuration acceptance. An earlier
  full-suite run overlapped an intentional adapter RED test and had 52/54;
  retain that intermediate log separately from the final 53/54 result.
- Missing direct integration binary invocation: exit 1 with explicit usage;
  no binary fallback. `git diff --check`: PASS.

Real integration verifies both F4/F3 geometries, common 16384/512/128 sample
grid, 32 groups and 16.384 ms blocks; literal compute/raw/output byte counts;
Station order `[1003,1001,1000,1002]`; raw/unpacked/converted/beamformed/output
header order; local IDs and first channel; exact signed weight payload slices
and NPY versions; installed source/resource paths; explicit P2 coherency weights,
four products `AA,BB,AB_REAL,AB_IMAG`; same-host GPU0/GPU1; 33-group valid partial
tail with output transfer bytes 4224/3168; incompatible duration rejection;
publication collision preserving foreign output; inputs changing during
compilation rejected. No fake compiler success is counted.

## Known compatibility restriction

The unchanged metadata reader supports `|i1` and `<i2` in NPY v1/v2. Full
Observation currently uses CI8 input and rejects int16 weights at
`src/config/resolved_observation_plan.cpp:137-139`, after reading metadata and
comparing complex component widths. Actual stderr:

```text
observation preflight failed: beamform weight dtype must match antenna input width
```

Real v1/v2 int16 tests require that rejection for both local instances. Existing
`beamform_weight_metadata_test` verifies int16 metadata positively. Int16 slice
support remains unchanged; no conversion, coercion or Full-compatible PASS claim.

## Initial-freeze retained evidence and identities

Evidence root (ignored but retained):
`/Users/ywang/.codex/worktrees/179b/rdma_dada/build-ms05/evidence`.
Includes full logs, dependency SHA manifest, `ms05-delivered-files.sha256`,
`ms05-only.patch` against the reviewed dependency snapshot, generated inputs,
portable bundle, real CLI output JSON and `resolved-node-a`/`resolved-node-b`.
Each resolved directory retains the C++ manifest, nine original artifact files
including manifest, and the adapter evidence file. Actual argv, stdout/stderr,
resource/input/artifact SHA256 and local IDs are recorded in `resolution.json`.

Binary:
`/Users/ywang/.codex/worktrees/179b/rdma_dada/build-ms05/observation_config_compile`.
SHA256: `881ca3533d86461c5238fa9af60fddc22086fb463ebee8bf25bd19cb58ac93bf`.

Retained real CLI commands (destinations now exist; use new outputs for retries):

```sh
python3 -B scripts/multiserver_compile.py compile --global build-ms05/evidence/inputs/global.json --deployments build-ms05/evidence/inputs/deployments.json --inventory build-ms05/evidence/inputs/inventory.json --output build-ms05/evidence/bundle
python3 -B scripts/multiserver_compile.py resolve --instance-dir build-ms05/evidence/bundle/instances/node-a/gpu-0 --compiler build-ms05/observation_config_compile --output build-ms05/evidence/resolved-node-a
python3 -B scripts/multiserver_compile.py resolve --instance-dir build-ms05/evidence/bundle/instances/node-b/gpu-0 --compiler build-ms05/observation_config_compile --output build-ms05/evidence/resolved-node-b
```

## Server requirements for coordinator handoff

`GPU服务器代码测试` owns execution. Perform Phase 0 and use isolated multiserver
source/build/result roots with ownership records. Mirror the complete frozen
source tree; preserve remote build/results/data, paper roots and historical
accepted evidence. After coordinator review generate the full manifest using:

```sh
python3 -B scripts/generate_source_manifest.py --root . --output config/testing/multiserver-source-manifest.sha256
```

On qths1 use only the validated matching 3.31.12 pair:

```sh
/home/user/wy/tools/cmake-3.31.12/bin/cmake -S SOURCE_ROOT -B BUILD_ROOT -DBUILD_TESTING=ON -DBUILD_RDMA_PIPELINE=OFF -DUSE_CUDA=OFF
/home/user/wy/tools/cmake-3.31.12/bin/cmake --build BUILD_ROOT --target observation_config_compile observation_config_test resolved_observation_plan_test observation_artifacts_test beamform_weight_metadata_test -j 4
/home/user/wy/tools/cmake-3.31.12/bin/ctest --test-dir BUILD_ROOT -N -R 'multiserver|observation_config_test|resolved_observation_plan_test|observation_artifacts_test|beamform_weight_metadata_test|observation_config_compile_test'
/home/user/wy/tools/cmake-3.31.12/bin/ctest --test-dir BUILD_ROOT -R 'multiserver|observation_config_test|resolved_observation_plan_test|observation_artifacts_test|beamform_weight_metadata_test|observation_config_compile_test' --output-on-failure
```

`SOURCE_ROOT`/`BUILD_ROOT` are metavariables for Phase-0 verified isolated roots,
not assumed server paths. Use the observed Python executable for repeated pure
discovery (133 PASS, twelve explicit skips), direct unit (18 PASS) and direct real
compiler integration (twelve PASS) with the newly built explicit binary. Verify
source manifest before configuration and record source, binary, fixture/resource
and artifact hashes. New deterministic fixtures allocate their own temporary
directories; no production inventory, hardware runtime or data-flow acceptance
is implied. Coordinator handles review, full freeze and confirmed server handoff.

## Notification and retained state

The attempted callback to the coordinator was rejected by automatic approval
review: repository paths/details were being sent to an unverified chat
destination without explicit cross-chat messaging authorization. This is a
notification failure, independent of implementation/tests. Subsequent coordinator
input explicitly acknowledged active retrieval and instructed continuing to
freeze rather than blocking on callback. Result remains available here and in
this task's final response; no bypass or indirect messaging is used.

No SSH, remote execution, commit, push, merge or archive. Implementation is frozen
pending coordinator correction/review and server acceptance.

## Review correction P2 — post-publication cleanup

Coordinator independently reproduced a post-publication `workspace.rmdir()`
PermissionError escaping without structured details. This scope-contained
correction adds three tests using the actual compiler: API cleanup failure, CLI
cleanup failure, and failure to persist the cleanup warning (API and CLI).
Initial RED reproduced the raw PermissionError in all three tests; GREEN verifies
published artifacts are preserved with accurate publication state.

The final evidence explicitly records `published=true`. After successful
publication a cleanup error returns successful compilation with a persisted
`cleanup_warning` (`RESOLVE_CLEANUP` plus directory/message). Both API evidence
and CLI JSON match the final `resolution.json`; CLI returns 0. If writing that
warning also fails, `ResolutionError` / structured CLI failure use
`RESOLVE_CLEANUP_EVIDENCE` with `published=true`, output/evidence paths, cleanup
and diagnostic errors and artifact hashes. The original success evidence remains
valid, and the adapter never deletes the published output or claims rollback.
Pre-publication errors now explicitly record `published=false`. Temporary
directory stat I/O is normalized to ValueError as well.

Only compiler adapter, integration test and these two documentation files changed
in this correction. No CLI dispatch, CMake, fixture, C++ or algorithm changes.
Final correction evidence is in `build-ms05/evidence/review-p2/`, retaining the
initial freeze unchanged. This directory contains RED/GREEN logs, final tests,
direct CLI/API actual compiler examples, corrected twenty-file SHA snapshot and
MS-05-only diff. Full server source manifest remains deferred to coordinator
review/formal handoff. The latest correction freeze and counts supersede the
initial-freeze counts above: pure discovery 145 (133 PASS, twelve explicit skips),
direct unit 18/18, actual compiler integration 12/12, required CTest 11/11; broader
portable CTest 53/54 with the same unchanged loopback sandbox PermissionError.
`git diff --check` PASS. RESULT_NOTIFICATION remains FAIL; coordinator retrieves
this task actively. KEEP_OPEN_PENDING_ACCEPTANCE; no Git/remote/archive actions.
