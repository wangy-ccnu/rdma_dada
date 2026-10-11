# MS-06 offline lifecycle acceptance — 2026-10-11

Status: ACCEPTED_OFFLINE_CONTROL. Coordinator:
`01a090fd-55d8-7b83-9ea1-1a53eaa1b952` (多服务器同步开发).
This accepts the control contract with simulated hosts, not actual distributed
startup, process ownership, telemetry, Web/CLI or pipeline performance.

## Delivered behavior

- SQLite run snapshots, strict JSON, immutable identity, revision conflicts and
  transactional persistence; unknown/corrupt databases are never silently reset.
- Observation-wide prepare/start/stop; fresh all-participant READY admission;
  persisted action intent and uncertainty before host calls; idempotent requests.
- Runtime-local failure isolation while healthy frequency shards continue;
  human stop across the observation with bounded graceful-to-force escalation.
- Restart reconciliation without replaying prepare/start; stale/disconnected
  reports remain unknown, distinct from failure and proven cleanup.
- Normal completion requires completed processing, output publication, EOD and
  cleanup evidence. No wall-clock-derived data progress or synthetic rates.

Public interface and limitations: [lifecycle.md](lifecycle.md).
Implementation: new `scripts/multiserver/store.py`, `controller.py`, command
schema and two test modules; CMake only extends the existing Python test list.
No Observation-v1/compiler/RDMA/unpack/GPU algorithms were changed.

## Independent review

Store checkpoint: corrected Unicode surrogate round-trip coercion;28 tests PASS.
Final whole-MS06 review: Standards and minimality PASS. Two P1 findings were
fixed in the original implementation task, then independently re-reviewed:

1. Unexpected local interruption could yield a false normal PASS. It now retains
   failure/frequency/error facts, keeps healthy peers running and cannot be PASS.
2. Equal-sequence contradictory start outcomes could force-stop healthy peers.
   They now fail validation before persistence/cleanup; valid action resolution
   still works without refreshing liveness.

Both findings ADDRESSED; six added regressions independently PASS. No pending
review findings. Reports/diffs remain under the feature-local ignored
`.superpowers/sdd/2026-10-11-ms06-control-lifecycle-implementation/` workspace
and `/private/tmp/ms06b-01a128d3-evidence/`.

## Source and local evidence

At the acceptance freeze: feature branch `codex/multi-server-observation`,
base `82ac66f` plus uncommitted MS-06 additions. Reviewed child checkout:
`/Users/ywang/.codex/worktrees/bec6/rdma_dada`. All seven delivered files were
copied byte-identically into the feature worktree before the Linux source freeze;
no Git merge was performed. Prior unrelated documentation edits were preserved.

Frozen source manifest339 entries, SHA256:
`f306f26a428440894d3d824d64e7b923fda37cb0a69825c105fd34c8479b4cdd`.
Historical immutable copy: [ms06-tested-source.sha256](manifests/ms06-tested-source.sha256).
It describes the tested snapshot, not subsequent acceptance/ledger edits. Do not
rewrite the tested identity when updating these records; regenerate current
source identity at the next formal handoff.

Local child controller52/store28 tests and two registered CTests PASS.
Coordinator ran all six registered tests before the final contained correction;
the correction's two affected suites and six new regressions were reverified.
After exact integration, broad local discovery reported225 cases,12 compiler-
dependent skips,213 executed PASS; skipped integration is not claimed passed.
The exact six-suite Linux selection below has no skips.

## Linux offline acceptance

Executor: `GPU服务器代码测试`, `019fbb68-ae6f-77f0-bfdd-30bc30bd6c45`.
HF/qths source namespace `/home/user/wy/rdma_dada-multiserver`;
both full339-entry checks PASS. Fresh configure:
`/home/user/wy/build-ms06-offline-20261011`, CMake/CTest3.31.12,
Python3.8.10, SQLite3.31.1, Release, BUILD_TESTING=ON,
BUILD_RDMA_PIPELINE=OFF, USE_CUDA=OFF, BUILD_SSD_BENCHMARK=OFF.
No C++/CUDA target build was required for these registered Python suites.

One CTest invocation selected exactly six suites with
`--repeat until-fail:3 --no-tests=error -V --output-on-failure`:

| Suite | Cases per repetition | Repetitions |
|---|---:|---|
| multiserver_model_test |26|PASS / PASS / PASS|
| multiserver_deployment_test |43|PASS / PASS / PASS|
| multiserver_weights_test |21|PASS / PASS / PASS|
| multiserver_bundle_test |25|PASS / PASS / PASS|
| multiserver_store_test |28|PASS / PASS / PASS|
| multiserver_controller_test |52|PASS / PASS / PASS|

Total195 cases per set,585 case executions,18 suite executions,0 skips/failures.
No duplicate direct triple loop or performance run. Coordinator independently
parsed all18 sections of the original76,360-byte CTest log and verified its SHA:
`1cea8fbbafd7c34c7b80db3ad9e7835c6cd5c6e21fe9cb3186ccc4c5b463d5ad`.
Unlike separate overwriting invocations, this single repeated CTest command's
LastTest.log contains all18 executions; it was preserved before further tests.

Durable interfaces:

- [Environment evidence](evidence/ms06-phase0-20261011.json).
- [Executor result](evidence/ms06-server-20261011.json).
- [Independent per-repetition verification](evidence/ms06-ctest-20261011.json).
- Exact test argv is retained in the independent verification JSON; reproduction below.

HF/qths result root:
`/home/user/wy/task8c-multiserver-results/ms06-offline-20261011`.
Raw CTest log: `ctest-lasttest.log`; local retained copy
`/private/tmp/ms06-ctest-lasttest.log`. Evidence-read SSH quoting errors and the
initial attempt to fetch a not-yet-copied HF log were collection-only issues;
fixed-path transfer recovered the original log without rerunning tests.

CLEANUP_RESULT=PASS. No real host adapter, pipeline process, ring, capability,
GPU or network dataflow was started. Residue checks found only unrelated existing
gdm shared memory, untouched. Callback authorization failure is independent of
test PASS: coordinator retrieved the files and original tool execution directly.

## Decisions and remaining boundary

RunStore returns `{run_id, revision, snapshot}` so store metadata cannot collide
with application fields. If a later caller needs a different API, change it with
contract tests; this is not a distributed exactly-once/HA abstraction.
Repository policy keeps the persistent implementation task and all review/test
evidence, rather than auto-committing or deleting the skill workspace. Final
two-axis review covered the whole seven-file MS06 change once; the contained
fix received scoped re-review instead of duplicating an unchanged full review.

V1 is single-controller/serialized. A restart invalidates liveness and grants
an in-progress graceful drain a fresh bounded window; repeated crashes can
extend it. Lost start replies require reconciliation; there is no resume-start
API and no automatic reassignment/restart. These are explicit limitations, not
claims of live-host supervision.

Next scope is the isolated host supervisor/transport (roadmapP3), followed by
live business telemetry and Web/CLI. They need their own verified environment,
ownership tests and authorized functional runs. Existing paper branch and
accepted performance evidence remain untouched. Acceptance itself performed no
Git commit/push/merge; subsequent integration is recorded in the task ledger.

Child `MS-06 观测控制生命周期` (`01a128d3-928e-75d0-9a64-22ff35d0cad7`)
decided to archive after this acceptance. The coordinator confirmed its exact
ID in the app archived-task listing on2026-10-11. Only chat visibility changed;
the native source worktree and all review/test evidence remain retained.

## Reproduction and isolation

From a verified feature-source mirror, configure a fresh absent BUILD directory
with the fixed qths CMake/CTest3.31.12 pair:

```sh
/home/user/wy/tools/cmake-3.31.12/bin/cmake -S /home/user/wy/rdma_dada-multiserver -B BUILD \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=ON -DBUILD_RDMA_PIPELINE=OFF \
  -DUSE_CUDA=OFF -DBUILD_SSD_BENCHMARK=OFF -DPython3_EXECUTABLE=/usr/bin/python3
/home/user/wy/tools/cmake-3.31.12/bin/ctest --test-dir BUILD \
  --repeat until-fail:3 --no-tests=error -V --output-on-failure \
  -R '^multiserver_(store|controller|model|deployment|weights|bundle)_test$'
```

BUILD is a fresh, explicitly verified path placeholder. Historical execution
used the build named above; exact argv, cases and18 outcomes are in evidence.
No target compilation or duplicate direct triple loop is required. Preserve
the complete log before any later CTest invocation can overwrite it.

The existing server-testing task owns synchronization and execution. Use only
the isolated feature source/result namespaces; preserve paper canonical source,
builds and results. Refresh current source identity at the next approved test
handoff, verify all manifest paths and record Python/SQLite/toolchain readiness.
No qtp, rings, signals, capabilities, NIC changes or actual transport are part
of this gate. This reproduction note is not authorization to rerun old tests.
