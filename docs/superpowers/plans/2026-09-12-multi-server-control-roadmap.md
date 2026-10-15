# Multi-server Control Implementation Plan — Roadmap and Version Isolation

> **For agentic workers:** The coordinator assigns bounded implementation to persistent tasks using the current model/effort policy in `docs/agents/delegated-development.md`; each implementation task uses superpowers:executing-plans for its assigned scope. No automatic commit, push or merge. Checklist entries describe future work unless explicitly marked complete.

**Goal:** Implement centralized configuration, start/stop and business monitoring for one processing pipeline per GPU without changing the paper maintenance branch or its evidence.

**Architecture:** A global compiler produces immutable per-GPU bundles. A control plane owns run/operation state and talks to host-local supervisors; Web and CLI share that interface. Existing processing binaries gain only the explicitly tested readiness, stop and progress hooks needed by this interface.

**Tech Stack:** Existing C++11/CMake processing code; proposed Python 3.8-compatible control modules with standard-library JSON, subprocess and SQLite; plain HTML/JavaScript functional UI. Remote executable paths and transport details require Phase 0 verification; no new remote package installation is assumed.

**Spec:** [agreed requirements](../specs/2026-09-12-multi-server-observation-design.md). Implemented interfaces: [configuration](../../control/configuration.md) and [lifecycle](../../control/lifecycle.md). Current acceptance and task identities: [ledger](../../control/task-ledger.md).

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

## 1. Branch baseline

Feature `codex/multi-server-observation` was created from committed paper
revision `febdc0ee4587d5c8bd3b9f8466bbc954e1204389`, not its dirty worktree or
an older remote ref. Feature checkout:
`/Users/ywang/WorkFile/code/rdma_dada/.worktrees/multi-server-observation`.
Paper checkout: `/Users/ywang/WorkFile/code/rdma_dada`.
Existing `codex/gpu-only-pressure` and `codex/rdma-staged-copy-ablation`
remain independent. See the ledger for subsequent accepted implementation bases;
initial planning checks do not replace a stage's recorded test identity.

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
| 0 | `docs(control): define multi-server isolation and implementation plan` | Agreed design, roadmap and delegation rules; local ignored AGENTS entry is not force-added |
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

Feature namespaces (source/results used in offline acceptance; runtime roots still require Phase 0):

| Purpose | Reserved proposal |
|---|---|
| Sole feature source mirror on control/test hosts | `/home/user/wy/rdma_dada-multiserver` |
| Feature build collection | `/home/user/wy/build-multiserver/` (selected build identity recorded; never substitute a paper build) |
| Feature configurations/owned run state | `/home/user/wy/multiserver-runs/<run_id>/` |
| Feature functional evidence | `/home/user/wy/task8c-multiserver-results/` |

Source/result roots were verified during offline acceptance; runtime and fresh build roots must be checked before use. Keep ONE source mirror per branch purpose, not archive/r1/r2 source copies. Full source mirroring and per-file SHA verification still apply within the feature namespace; preserve results/data, and scope stale-source removal only to the verified feature source root. Never mirror this feature into the paper canonical source path.

Build/runtime disk namespaces do not isolate NIC, CPU, GPU, RAM or shared-memory keys. Before any feature data flow, the testing task checks for paper runs/owned resources, conflicts and aggregate capacity. Defer the feature run if shared hardware is occupied; do not kill or clean another task. Paper supplementary testing has priority. Source sync and runtime resource ownership have separate ledgers.

## 4. Environment prerequisites

Observed host facts are retained in [MS-07b Phase0](../../control/evidence/ms07b-phase0-20261015.json).
The bounded [disconnect probe](../../control/evidence/ms07b-disconnect-probe-20261015.json)
established ordinary SSH-request exit survival on qths1; it is not reboot/HA
acceptance. Tool availability alone does not authorize capability or service changes.
Refresh only facts required by a changed environment or the next test boundary.

Before real dataflow, the server owner checks source/binary identity, resource
availability, receiver/sender connectivity and the installed PSRDADA ABI. The
registered read-only ABI gate has passed in the no-dataflow stage. Any temporary
CAP_NET_RAW grant still needs permission. Web access remains a later prerequisite:
loopback behind an authenticated channel, not an exposed command service.

## 5. Implementation work packages

### P1 — Global configuration and portable bundles

MS-01–05 accepted. Contracts and reproduction are maintained in
[configuration](../../control/configuration.md), with
[MS-01/02](../../control/ms01-ms02-acceptance.md) and
[MS-03–05](../../control/ms03-ms05-acceptance.md) evidence.
This accepts static configuration, not live resource readiness.

### P2 — Control protocol and lifecycle

MS-06 offline control accepted. Store/controller semantics, idempotent commands,
local-failure policy and reconciliation are maintained in
[lifecycle](../../control/lifecycle.md) and [acceptance](../../control/ms06-acceptance.md).
No automatic restart or HA guarantee is introduced.

### P3 — Host supervision, transport and pipeline integration

MS-07a SSH transport is accepted; see the [server result](../../control/evidence/ms07a-acceptance-20261011.json)
and [audit](../../control/evidence/ms07a-acceptance-audit-20261011.json).
B1–C1 and I1 implementation/review are complete. The user accepted the
[server build and no-dataflow gates](../../control/evidence/ms07bc-build-gates-20261015.json)
on 2026-10-15. This is not actual multi-instance dataflow acceptance.

#### MS-07b/c delivery batch — split first, accept together (2026-10-15)

| Package | Delivered responsibility |
|---|---|
| B1 | Host-local resource claims and action journal |
| B2 | Retained process identity, inspection, bounded wait and owned signalling |
| B3 | Durable host command entrypoint and detached supervisor |
| C1 | Compiler/runtime integration, optional readiness/result hooks and scoped cleanup |
| I1 | Versioned functional runner, fixed peer fixture and evidence validation |

Task IDs, retained checkouts and acceptance scope are maintained in the
[ledger](../../control/task-ledger.md). Internal/public interfaces and runner
invocation are maintained in [lifecycle](../../control/lifecycle.md).
Keep these contracts in one place rather than repeating implementation instructions.
No RDMA/unpack/GPU algorithm or layout change is part of this stage.

Remaining P3 functional work, through the existing GPU-server test owner:

- [ ] Resolve required dataflow permissions; preinstall trusted bundles, fixed future
  UTC schedules and immutable per-host deployments. No preparation traffic.
- [ ] Run single-instance and same-host two-GPU equal/+1-channel cases, all-ready
  gating, prepare rollback, finite EOD, graceful/forced manual stop, isolated
  runtime failure with healthy peer continuing, and disconnect/reconnect.
- [ ] Require three clean repetitions per case, actual sender/receiver/unpack/GPU/
  output/EOD accounting and exact process/IPC cleanup. Deterministic fault evidence
  must identify the intended instance; wrong-target failure is not a passing case.
- [ ] Record unavailable physical cross-host receiver coverage as a limitation;
  same-host two-GPU success cannot substitute for it.
- [ ] Coordinator reviews the real functional results, updates existing status/
  evidence and confirms final batch acceptance before child archive decisions.

No new throughput/saturation campaign, arbitrary remote command execution or
production fault-injection interface is included. Stop only dependent blocked
work; preserve unrelated paper runs and resources.

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

### P6 — Final functional acceptance and documentation

Reuse the implemented `scripts/multiserver_acceptance.py`, its fixed peer and
tests. P3 owns the pending runtime matrix above; do not build a second runner or
repeat unchanged cases just because UI/telemetry is added.

- [ ] After P4/P5, extend only affected cases to verify snapshot accuracy and
  equivalent CLI/Web behaviour, using actual resource ownership and data closure.
- [ ] Preserve one compact feature result per stage and necessary raw evidence in
  the isolated namespace. Update configuration/lifecycle guides and the ledger,
  not parallel status reports. These are not paper throughput results.
- [ ] Declare unavailable topology/permissions explicitly. After server acceptance,
  propose user-controlled commit/push; no automatic merge into the paper branch.

## 6. Acceptance gates and stopping rules

| Gate | Required evidence | Does not imply |
|---|---|---|
| Plan/worktree baseline | Recorded commit, clean isolated base, selected local tests | Full baseline rebuild or GPU server acceptance |
| Configuration | Assignment/resource validation, byte-exact weight slicing, existing compiler acceptance | Ability to run remote processes |
| Lifecycle | Idempotence, failure/rollback/timeout, ownership and reconnect tests | Distributed atomic start under arbitrary partitions |
| Monitoring | Commit-based data time, quality and actual-rate checks | Downstream received/consumed data |
| Functional server suite | Three clean repeated runs with exact fixture/source/build identities | New saturation limit or paper performance acceptance |

Unknown environment facts stop only the dependent remote stage; pure config/state work can proceed. No unsupported fallback into paper paths. No unrelated paper code changes to unblock feature tests. New shared-code defects are explained and isolated rather than silently repaired on both branches.
