# MS-05 — Existing compiler compatibility

Coordinator: `01a090fd-55d8-7b83-9ea1-1a53eaa1b952`.
Dispatch prerequisite: coordinator MS-04b review PASS. This file alone does not
start a task. Use a new persistent task with `gpt-6.1-sol` / `high`.

Read the complete authoritative procedure at
`/Users/ywang/WorkFile/code/rdma_dada/.worktrees/multi-server-observation/docs/agents/delegated-development.md`.
Read Task 5 of `docs/superpowers/plans/2026-09-12-multi-server-config-implementation.md`
in that coordinator worktree and its linked design spec. The coordinator owns
review and server handoff; Git decisions remain with the user.

## Checkout and dependencies

Start an isolated worktree from `codex/multi-server-observation`, expected HEAD
`5851eaed6cff78179d9cee763c6d4713c1095b08`. Verify it before editing. The base
contains MS-01/02 only. Reviewed MS-03/04 dependencies are uncommitted in
`/Users/ywang/.codex/worktrees/1209/rdma_dada`; import only the paths in the
coordinator's `docs/control/manifests/ms04b-reviewed-files.sha256` after checking
their hashes. Use apply_patch and preserve source worktrees. Verify imported
bytes, then run the 115-test pure baseline before new implementation.
Do not copy build directories, paper changes or unrelated docs. This is explicit
local dependency transfer, not authorization for remote per-file synchronization.

## Allowed implementation

- Add `scripts/multiserver/compiler.py` with
  `resolve_instance(instance_directory: Path, compiler: Path, output: Path) -> dict`.
- Extend `scripts/multiserver_compile.py` with `resolve --instance-dir DIR
  --compiler BINARY --output DIR`; retain compile behavior.
- Add `tests/multiserver_compile_test.py`, extend shared fixture narrowly,
  register pure tests and real compiler integration in `CMakeLists.txt`, update
  `docs/control/configuration.md`.
- Keep MS-03/04 behavior frozen except concrete integration corrections reported
  to the coordinator. Existing Observation schema, C++ compiler/algorithms,
  paper tests and accepted-results indexes remain unchanged.

Invoke the explicit existing binary with argument array:
`BINARY --config INSTANCE/observation.json --output-dir OUTPUT`.
Preserve argv, exit code and stderr on failure; reject invalid/false validation
reports. Record actual compiler binary identity and input/resource/artifact
hashes. Preserve bundle files. Output must be new, task-owned and non-clobbering;
make failed outputs clearly unsuccessful and retain actionable diagnostics.
Do not rewrite headers or fabricate resolved IDs. Resolve from the actual
installed instance path: compiler artifacts are path-dependent, unlike the
portable bundle. No runtime-readiness claim from compiler success.

Use existing helpers/contracts where appropriate; avoid a second config parser
or geometry calculator. Inject subprocess results only for negative-path unit
tests. Real integration requires the explicit compiler argument supplied by
CTest as `$<TARGET_FILE:observation_config_compile>`, with no default binary
fallback or fake compiler counted as acceptance. Keep pure test discovery usable
without pretending the integration test ran.

## Verification

Red/green tests cover missing binary, nonzero exit, invalid JSON/report, missing
artifacts, ownership/no-overwrite and structured CLI failure. Real compiler tests
build the deterministic bundle then compile both F4/F3 instances:

- Common T=16384, S=512, groups=32, K=128, interval=1,000,000 ps,
  block duration=16.384 ms; A4/P1/B2.
- Compute bytes 524288/393216; raw record bytes 4128/3104;
  raw block bytes 528384/397312; Power output bytes 4096/3072.
- Verify local channel/weight/header ordering, local IDs and common time grid;
  same-host GPU0/GPU1 remains supported without runtime launches.
- P2 coherency with explicit P2 weights and four products; valid partial tail
  and incompatible duration; NPY v1/v2 with int8/int16 read by existing C++.
- Invalid input does not become silently rounded, reconfigured or accepted.

Check available local CMake before a portable feature-owned build with
`BUILD_TESTING=ON`, `BUILD_RDMA_PIPELINE=OFF`, `USE_CUDA=OFF`. Build the existing
compiler and relevant observation/artifact/weight tests; run new pure tests,
real compiler integration and relevant existing regressions via registered
CTest. Record exact commands/counts and `git diff --check`.

Prepare server requirements only: Linux pure tests plus existing compiler
build/artifact checks in isolated multiserver roots, using the validated server
CMake/CTest 3.31.12 pair. GPU-server test task performs Phase 0/sync/execution.
No SSH, RDMA/GPU flow, rings, sender or remote controller implementation here.
Full feature manifest freezes after coordinator review; do not refresh the
paper manifest or update hashes after every edit.

## Delivery

Return implementation scope, exact files, tests, limitations and server commands
to the coordinator. If callback is denied, record `RESULT_NOTIFICATION=FAIL`
and keep the complete final report available for coordinator retrieval; do not
let notification failure replace implementation. Freeze after delivery, remain
open pending independent review and server acceptance. No commit/push/merge or
archive. The coordinator actively waits, reviews and handles correction rounds.
