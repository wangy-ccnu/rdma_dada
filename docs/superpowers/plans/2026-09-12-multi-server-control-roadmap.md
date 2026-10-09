# Multi-server Control Implementation Plan — Roadmap and Version Isolation

> **For agentic workers:** The coordinator assigns bounded implementation to persistent tasks using the current model/effort policy in `docs/agents/delegated-development.md`; each implementation task uses superpowers:executing-plans for its assigned scope. No automatic commit, push or merge. Checklist entries describe future work unless explicitly marked complete.

**Goal:** Implement centralized configuration, start/stop and business monitoring for one processing pipeline per GPU without changing the paper maintenance branch or its evidence.

**Architecture:** A global compiler produces immutable per-GPU bundles. A control plane owns run/operation state and talks to host-local supervisors; Web and CLI share that interface. Existing processing binaries gain only the explicitly tested readiness, stop and progress hooks needed by this interface.

**Tech Stack:** Existing C++11/CMake processing code; proposed Python 3.8-compatible control modules with standard-library JSON, subprocess and SQLite; plain HTML/JavaScript functional UI. Remote executable paths and transport details require Phase 0 verification; no new remote package installation is assumed.

**Spec:** `docs/superpowers/specs/2026-09-12-multi-server-observation-design.md` in this worktree. Compiler detail: `docs/superpowers/plans/2026-09-12-multi-server-config-implementation.md`.

## Global Constraints

- The paper is not yet accepted. `codex/pipeline-architecture` remains available for supplementary tests and fixes.
- Do not merge `codex/multi-server-observation` or GPU/receive ablation experiments into the paper branch without explicit user approval.
- One pipeline per GPU; user-provided frequency allocation; common samples/packet, samples/block, sample interval and integration alignment.
- Same-host pipelines require distinct CPU physical-core allocations, GPU, ring keys, receive flow and run directories. Shared host/NIC/memory capacity is checked in aggregate.
- Data time comes from sample coverage, never the host wall clock. Actual data rates come from counter deltas, never planned rates.
- Start requires all participants ready; runtime failure is local and others continue. Control disconnection must not stop healthy processing.
- Normal finish drains the configured data interval; user stop is bounded graceful drain, with explicit force/timeout disposition.
- No real FPGA management, downstream-consumption responsibility, automatic balancing, automatic recovery or high-availability cluster.
- First version prioritizes function, not UI polish or new performance campaigns. Existing correctness, ownership and cleanup requirements still apply.
- GPU-server acceptance belongs to `GPU服务器代码测试`; local test PASS is not remote acceptance. Commit and push remain user decisions.

## 1. Repository facts and isolation already established

Read-only inspection on 2026-09-12 found:

| Item | Recorded value |
|---|---|
| Actual paper branch name | `codex/pipeline-architecture` (not `pipeline-archtecture`) |
| Local committed paper HEAD / new feature base | `febdc0ee4587d5c8bd3b9f8466bbc954e1204389` |
| Locally cached origin paper ref | `c96a0a390ef21c0994feb41aabcf5a00ae353d42` |
| Local-only commits | `cf7e02c`, `5fba3f8`, `febdc0e`; no fetch/push performed in this planning turn |
| Paper worktree | `/Users/ywang/WorkFile/code/rdma_dada` |
| Feature branch | `codex/multi-server-observation` |
| Feature worktree | `/Users/ywang/WorkFile/code/rdma_dada/.worktrees/multi-server-observation` |
| Existing comparison branch | `codex/gpu-only-pressure`, inspected at `209fd36`; left untouched |
| Existing receiver ablation | `codex/rdma-staged-copy-ablation`, inspected at `5e7bd7e`; left untouched, including prunable worktree metadata |

- [x] Verify `.worktrees/` is already ignored; no `.gitignore` change needed.
- [x] Create the feature branch/worktree from the exact committed paper HEAD, not the older remote ref and not the dirty working tree.
- [x] Run local `python3 -B tests/generate_source_manifest_test.py -q`: 2 tests PASS.
- [x] Run local `python3 -B tests/task8c_profiles_test.py -q`: 12 tests PASS.
- [x] Copy only the new multi-server design document into the feature worktree.

The parent has uncommitted numerical-suite, CMake, tests, manifests and documentation changes. They remain in the parent and were NOT imported. This feature base is a pinned Git commit, not a claim that all current paper evidence was generated from that commit. Evidence continues to use its recorded source/build/config identity.

## 2. Git and evidence policy

### Branch direction

```text
paper branch at febdc0e ── future paper fixes / supplementary evidence
                    └── codex/multi-server-observation ── control features

paper fix -> feature branch: optional, explicit reviewed cherry-pick
feature -> paper branch: no merge/backport unless separately approved
gpu-only-pressure / rdma-staged-copy-ablation: retain independent comparisons
```

Do not stash, reset, clean, move, commit or rewrite the parent's dirty changes. No rebasing or force-pushing the paper/experiment branches. If the paper branch advances, the feature branch does not silently rebase onto it. A necessary common fix is isolated as its own commit, reviewed and tested on each branch before an approved cherry-pick.

### Planned commit groups (user approval required per commit)

| Group | Subject | Content |
|---|---|---|
| 0 | `docs(control): define multi-server isolation and implementation plan` | Spec, two plans and `docs/agents/delegated-development.md`; local ignored AGENTS entry is not force-added |
| 1 | `feat(config): validate global observation and GPU assignments` | New global model/schema/deployment validation and tests |
| 2 | `feat(config): generate per-GPU observation and weight bundles` | Weight slicing, portable bundle generation, compiler compatibility tests |
| 3 | `feat(control): model idempotent observation lifecycle` | Pure state machine, persisted operation identity, fake-host tests |
| 4 | `feat(control): supervise isolated host pipelines` | Remote adapter/local supervisor, ownership, ready/drain behavior and server gates |
| 5 | `feat(telemetry): publish sample-based pipeline progress` | Optional snapshots and concurrency-safe progress hooks; product paths explicitly reviewed |
| 6 | `feat(control): add functional web and CLI monitoring` | Shared interface, simple pages, authentication/access limits and integration tests |
| 7 | `test(control): record multi-instance acceptance and operations guide` | Versioned functional runner, compact results and feature-local status |

Use explicit path staging, never `git add .` or `git add -A` on the parent. Worktree shares the Git object database but has its own branch/index; specify the feature working directory for every Git command.

Do not automatically create tags. When the user wants a release/checkpoint, record an immutable commit and acceptance identities; any tag naming/push is separately approved. If later pushing the feature, push only `HEAD:refs/heads/codex/multi-server-observation`, over the verified SSH remote; never `--all` or `--mirror`.

### Result separation

- Keep existing `docs/results/accepted-results.json`, `accepted-module-results.json`, paper indexes and retained suites unchanged for this feature.
- New feature documents live in `docs/control/`; feature results use a dedicated result root and manifest, not the paper accepted catalog by default.
- Record Git base/current revision, dirty source manifest where relevant, binaries, weights, local/global config IDs, run ID, instance IDs, commands, cleanup and first failure.
- After a user-approved commit, record which accepted source identity that commit contains; never rewrite an older suite's source identity to the new commit.
- Paper fixes/results remain discoverable on the paper branch. Only notify reporting tasks of genuinely relevant new evidence; control-plane functional tests are not silently promoted to paper throughput evidence.

## 3. Remote isolation: necessary even with separate Git branches

Using separate worktrees is insufficient if both are mirrored to `/home/user/wy/rdma_dada` or run on the same hardware simultaneously.

Proposed feature namespace (must be confirmed unused/available by Phase 0):

| Purpose | Reserved proposal |
|---|---|
| Sole feature source mirror on control/test hosts | `/home/user/wy/rdma_dada-multiserver` |
| Feature build collection | `/home/user/wy/build-multiserver/` (selected build identity recorded; never substitute a paper build) |
| Feature configurations/owned run state | `/home/user/wy/multiserver-runs/<run_id>/` |
| Feature functional evidence | `/home/user/wy/task8c-multiserver-results/` |

These are planning proposals, not directories created or checked remotely. Keep ONE source mirror per branch purpose, not archive/r1/r2 source copies. Full source mirroring and per-file SHA verification still apply within the feature namespace; preserve results/data, and scope stale-source removal only to the verified feature source root. Never mirror this feature into the paper canonical source path.

Build/runtime disk namespaces do not isolate NIC, CPU, GPU, RAM or shared-memory keys. Before any feature data flow, the testing task checks for paper runs/owned resources, conflicts and aggregate capacity. Defer the feature run if shared hardware is occupied; do not kill or clean another task. Paper supplementary testing has priority. Source sync and runtime resource ownership have separate ledgers.

## 4. Phase 0: evidence gate before remote adapter/test-runner design

**Files:** evidence only under a feature-owned location; no product or remote script changes yet.

**Interfaces:** an observed host inventory provides exact tools/versions, CPU `(socket,core,logical_cpu)` map, GPU UUID map, NIC/MTU/flow information, installed library resolution, available capacities and permitted execution paths. Absence is explicit, not guessed.

- [ ] Hand off a read-only inventory to existing `GPU服务器代码测试`, naming this feature branch/worktree and proposed isolated roots. No sync/build/rings/data flow authorized by the inventory request.
- [ ] Verify control host -> processing/sender host connectivity, host-key handling, Python and available libraries, fixed CMake/CTest pair `/home/user/wy/tools/cmake-3.31.12/bin/{cmake,ctest}`, CUDA/PSRDADA/RDMA, permissions, disk/RAM, GPU/CPU topology, NIC/link/MTU and clock state.
- [ ] Determine whether a persistent host process survives control SSH disconnect under the observed environment. Do not invent `systemd`, `setsid` or passwordless privilege availability.
- [ ] Record safe control Web exposure: loopback behind an authenticated channel for v1; no unauthenticated externally bound command server. Check time synchronization for scheduled launch separately from data time alignment.
- [ ] Verify feature namespace isolation and record shared hardware scheduling constraints with paper testing.
- [ ] Return facts to the development task. If a facility is missing, propose the smallest environment decision; do not install it silently or revert to the paper source/build paths.

This planning turn did not run Phase 0 or remote tests. Phase 1 below is local/pure and can be designed now. The remote execution adapter and its test runner receive their own concrete implementation plan only after these facts are available; this is an explicit subsystem gate, not a claim that those paths are ready.

## 5. Implementation work packages

### P1 — Global configuration and portable bundles

Detailed TDD tasks, interfaces and file list are in `2026-09-12-multi-server-config-implementation.md`. Expected deliverable: deterministic bundles accepted by the existing single-instance compiler without altering old observations, headers or algorithm behavior. No SSH in this package.

### P2 — Control protocol and lifecycle, using fake hosts first

**Create:** `scripts/multiserver/protocol.py`, `state.py`, `store.py`, `controller.py`; `config/multiserver-command-v1.schema.json`; `tests/multiserver_controller_test.py`.

**Interfaces:** `ControlRequest = {request_id, run_id, operation, targets, bundle_id, mode}`; operations `prepare/start/status/stop`, stop mode `graceful/force`. `RunStore.apply(request)` records idempotency and returns operation ID/current state. The host transport interface is `send(host_id, request) -> response`; unit tests inject a fake, not SSH.

- [ ] Write a failing table-driven unittest for `NEW -> PREPARING -> READY -> RUNNING -> DRAINING -> COMPLETED`, with `PARTIAL_FAILED`, `INTERRUPTED`, `FAILED`, `UNKNOWN` explicit. Per-instance state and aggregate run state are distinct.
- [ ] Assert duplicate requests with identical content return the same operation and spawn no second process; reused request IDs with different content fail.
- [ ] Implement durable SQLite state transactions and the all-ready barrier. Validation/readiness is not equivalent to successful distributed start.
- [ ] Test partial preparation rollback, failure while start commands are being delivered, runtime-local failure while peers continue, controller restart, late/stale status and a lost response after an operation actually succeeded.
- [ ] Test that startup rollback with an unreachable host reports unresolved ownership, not a false clean terminal state. Runtime stale status does not issue stop commands.
- [ ] Server-test the pure controller/store tests before proposing commit group 3. No network data flow needed for this gate.

### P3 — Isolated host supervisor and transport

**Create after Phase 0:** `scripts/multiserver/transport.py`, `host_agent.py`, `ownership.py`, `scripts/multiserver_host.py`; `tests/multiserver_host_test.py`, `tests/multiserver_transport_test.py`.

**Potential product edits, only if required by verified lifecycle behavior:** `apps/rdma2dada/main.cpp`, `apps/vdif_unpack_worker/main.cpp`, `apps/pipeline_worker/main.cpp`; dedicated tests under `tests/` and feature-only CMake changes. Do not claim CLI flags already exist.

**Interface:** host command protocol from P2, checked bundle directory from P1, observed inventory from Phase 0. Persist exact owned PIDs plus process start identity/process group, IPC keys and path identities per run. A reused PID is not proof of ownership.

- [ ] Generate process argv from explicit binary/profile inputs; no shell interpolation or arbitrary user command strings. Host-local compiler resolves bundles from their final path before ring allocation.
- [ ] Use an exact-key resource claim before create; refuse foreign IPC instead of deleting it. Validate CPU logical and physical-core overlap and available GPU ownership at runtime, not only offline.
- [ ] Prepare in reverse-consumer dependency order (rings, output consumer, GPU, unpack, receiver) using observed readiness, not sleep-only success. Mark ready only when required processes and interfaces are verified, even before a data header has arrived.
- [ ] Implement run-scoped drain and forced cleanup. Stop ingress first and allow downstream EOD to finish; do not broadcast SIGTERM to every stage at once and discard buffered data. Normal finite finish, input silence and missing Station behavior must remain truthful.
- [ ] Prove the host supervisor remains alive when the control connection drops and reports/reconciles state on reconnect. Test timeouts without touching an unknown run.
- [ ] Run fake-process ownership/error tests, then an authorized small functional server gate. Any product stop/readiness changes require affected existing correctness/Full regression on the feature build; they are not automatically backported to the paper branch.

### P4 — Live business snapshots

**Create:** `include/rdma_dada/monitoring/progress_snapshot.h`, `src/monitoring/progress_snapshot.cpp`, `scripts/multiserver/telemetry.py`, `tests/progress_snapshot_test.cpp`, `tests/multiserver_telemetry_test.py`.

**Candidate hook files:** receiver app and `src/io/rdma/` implementation selected after call-path inspection; `apps/vdif_unpack_worker/main.cpp`, `modules/vdif_unpack/vdif_unpack_engine.cpp`; `apps/pipeline_worker/main.cpp`, `src/pipeline/gpu_block_pipeline.cpp`, `src/pipeline/worker_metrics.cpp`; `CMakeLists.txt` only in feature branch.

**Interface:** opt-in snapshot contains run/instance generation, sequence, counter sample time from a local monotonic clock, accepted record bytes, published output bytes, input frontier, contiguous published sample-end and quality counters. New flags are additive and disabled by default; existing final metrics remain authoritative for old tests.

- [ ] Test input gaps, zero-fill, partial output, rejected commit and EOD before any runtime wiring. Output progress advances only after successful ordered ring commit; CUDA completion alone is insufficient.
- [ ] Establish thread-safe ownership/snapshot copying; do not read mutable worker counters concurrently without synchronization, parse debug text as live truth, or add per-packet file/network writes.
- [ ] Publish bounded periodic snapshots through a versioned interface (atomic replace/file or verified local channel), preserving generation and stale semantics. UI receives JSON through the controller, not raw process logs.
- [ ] Calculate actual rates from deltas over one local clock window; counter reset or generation change resets the estimator. Never use planned block-aligned throughput as actual throughput.
- [ ] Test input frontier vs contiguous published end, restart/out-of-order reports, all-machines-input-drop and a normal-rate but zero-filled stream. Missing/stale values are null/unknown, not false zero rates.
- [ ] Compare snapshot-enabled vs disabled correctness and bounded reporting overhead on server. Do not add SM occupancy/Tensor profiling to this functional scope.

### P5 — Web and CLI

**Create:** `scripts/multiserver_control.py`, `scripts/multiserver/web.py`, `scripts/multiserver/static/index.html`, `app.js`, `style.css`, `tests/multiserver_web_test.py`, `tests/multiserver_cli_test.py`.

**Interface:** Web and CLI call P2 controller operations and read P4 snapshots. Web must not implement a separate process launcher. Static HTML/JS and a minimal local Python server are the proposed functional implementation; external/public deployment would require a separate hardening decision.

- [ ] Add functional endpoints for configuration selection/upload, preflight, start, status and stop. Validate IDs and paths; bound upload size; prohibit traversal, arbitrary executables and client-supplied shell text.
- [ ] Require authenticated/authorized control access; disallow cross-origin state-changing requests. Keep secrets outside Observation and redact command logs. Default bind loopback and use the observed approved access channel.
- [ ] Test start twice, readiness failure, stop confirmation, forced-stop reporting, expired snapshots and unknown participant status through both CLI and HTTP.
- [ ] UI shows server/GPU/frequency, actual and expected receive/output rates, data end time, relative progress, missing/zero-filled count and status age. Use integer sample positions/time strings to avoid JavaScript 53-bit truncation of timestamps/counters.
- [ ] Polling is sufficient for v1; no custom streaming transport, frontend build framework or interactive schema editor required for the first functional deliverable.

### P6 — Functional acceptance and documentation

**Create after Phase 0 and P3:** `scripts/multiserver_acceptance.py`, `config/testing/multiserver-functional-suite.json`, `tests/multiserver_acceptance_test.py`; `docs/control/README.md`, `deployment.md`, `results.md`.

- [ ] Runner owns exact resources and records full configuration/identity, every repetition, first failure, cleanup and notifications. Do not modify the paper runner merely to add a production-control feature.
- [ ] Exercise same-host two-instance resource isolation with fake inventory locally; actual multi-GPU server run only when CPU/GPU/NIC resources permit. No fake multi-host performance claim if only one receiving host is available.
- [ ] Server functional acceptance: three consecutive clean runs for equal and one-extra-channel assignments, scheduled start/finite finish, manual stop, partial-start failure, one-instance runtime failure, control disconnect/reconnect and snapshot accuracy. Fault injection affects only explicitly owned instances.
- [ ] Simulator configs are generated per destination from the same allocation; retain samples/packet and recompute packet bytes. Protect fixed paper source ports and CPU profiles; feature endpoints are explicitly recorded.
- [ ] Preserve compact feature evidence, including failures, in the isolated result root. Document that this is functional acceptance, not a substitute for the established paper performance suites.
- [ ] Only after passed server handoff and user approval propose commit group 7. No automatic merge or push.

## 6. Acceptance gates and stopping rules

| Gate | Required evidence | Does not imply |
|---|---|---|
| Plan/worktree baseline | Recorded commit, clean isolated base, selected local tests | Full baseline rebuild or GPU server acceptance |
| Configuration | Assignment/resource validation, byte-exact weight slicing, existing compiler acceptance | Ability to run remote processes |
| Lifecycle | Idempotence, failure/rollback/timeout, ownership and reconnect tests | Distributed atomic start under arbitrary partitions |
| Monitoring | Commit-based data time, quality and actual-rate checks | Downstream received/consumed data |
| Functional server suite | Three clean repeated runs with exact fixture/source/build identities | New saturation limit or paper performance acceptance |

Unknown environment facts stop only the dependent remote stage; pure config/state work can proceed. No unsupported fallback into paper paths. No unrelated paper code changes to unblock feature tests. New shared-code defects are explained and isolated rather than silently repaired on both branches.

## 7. Planning-turn outcome

Only independent worktree setup, selected local baseline tests and documentation occurred. No remote calls, source sync, installs, new binaries, rings, data flow, commit, push, tag or merge. The parent paper branch HEAD/index/dirty contents remain unchanged. The new source manifest is deliberately not regenerated until a feature source/test handoff is actually prepared.
