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

## 4. Phase 0: evidence gate before remote adapter/test-runner design

**Files:** evidence only under a feature-owned location; no product or remote script changes yet.

**Interfaces:** an observed host inventory provides exact tools/versions, CPU `(socket,core,logical_cpu)` map, GPU UUID map, NIC/MTU/flow information, installed library resolution, available capacities and permitted execution paths. Absence is explicit, not guessed.

- [x] Hand off a read-only inventory to existing `GPU服务器代码测试`, naming this feature branch/worktree and proposed isolated roots. Dispatched 2026-10-11 from base `78b909e`, targeting HF/qths1 only. No sync/build/rings/data flow authorized by this inventory request; sender-host and Web checks are deferred to their dependent stages.
- [ ] Verify control host -> processing/sender host connectivity, host-key handling, Python and available libraries, fixed CMake/CTest pair `/home/user/wy/tools/cmake-3.31.12/bin/{cmake,ctest}`, CUDA/PSRDADA/RDMA, permissions, disk/RAM, GPU/CPU topology, NIC/link/MTU and clock state.
- [ ] Determine whether a persistent host process survives control SSH disconnect under the observed environment. Do not invent `systemd`, `setsid` or passwordless privilege availability.
- [ ] Record safe control Web exposure: loopback behind an authenticated channel for v1; no unauthenticated externally bound command server. Check time synchronization for scheduled launch separately from data time alignment.
- [ ] Verify feature namespace isolation and record shared hardware scheduling constraints with paper testing.
- [ ] Return facts to the development task. If a facility is missing, propose the smallest environment decision; do not install it silently or revert to the paper source/build paths.

Offline configuration/control Phase0 and acceptance are recorded in the stage results. They do not establish live-host supervision, disconnect survival, NIC/clock performance or Web access. Refresh those specific prerequisites before designing the remote adapter and its runner; the unchecked items above concern that upcoming boundary.

2026-10-11 read-only P3 findings are retained in
[Phase0](../../control/evidence/ms07-phase0-20261011.json) and its
[supplement](../../control/evidence/ms07-phase0-supplement-20261011.json).
HF→qths1, Python3.8.10/OpenSSH8.2p1, readable process start identity and CPU/GPU
mapping are observed. Feature source/results roots and their writable parent
exist; `multiserver-runs` does not and was not created. qths1 has no task-owned
dataflow resources. Its user service manager is available with `Linger=no`;
effective logind session cleanup and disconnect survival are UNKNOWN. Available
setsid/nohup/systemd-run tools do not resolve those unknowns. Historical binaries
were inspected for dependencies only, not adopted as feature executables.
These facts support transport implementation planning; actual stdin protocol,
process survival, readiness, signals and resource cleanup still need their
versioned, scoped tests. No service/SSH/NIC settings were changed.

## 5. Implementation work packages

### P1 — Global configuration and portable bundles

Accepted MS-01–05, committed through82ac66f. See
[configuration interfaces and reproduction](../../control/configuration.md),
[MS-01/02 acceptance](../../control/ms01-ms02-acceptance.md) and
[MS-03–05 acceptance](../../control/ms03-ms05-acceptance.md).
Portable per-GPU bundles resolve through the unchanged single-instance compiler.
Static validation is not live resource readiness; no remote execution is implemented.

### P2 — Control protocol and lifecycle, using fake hosts first

Accepted MS-06 offline control on2026-10-11; six Linux suites x3 PASS, no actual
host/dataflow claim. See [lifecycle API and host contract](../../control/lifecycle.md)
and [MS-06 acceptance](../../control/ms06-acceptance.md).
Store/controller/schema and fake-host tests are implemented. Public requests
target the observation; durable intents and reconciliation guard uncertain actions.
No production SSH adapter, automatic restart, resume-start API or HA guarantee.

### P3 — Isolated host supervisor and transport

**Create after Phase 0:** `scripts/multiserver/transport.py`, `host_agent.py`, `ownership.py`, `scripts/multiserver_host.py`; `tests/multiserver_host_test.py`, `tests/multiserver_transport_test.py`.

**Potential product edits, only if required by verified lifecycle behavior:** `apps/rdma2dada/main.cpp`, `apps/vdif_unpack_worker/main.cpp`, `apps/pipeline_worker/main.cpp`; dedicated tests under `tests/` and feature-only CMake changes. Do not claim CLI flags already exist.

**Interface:** host command protocol from P2, checked bundle directory from P1, observed inventory from Phase 0. Persist exact owned PIDs plus process start identity/process group, IPC keys and path identities per run. A reused PID is not proof of ownership.

Local interface inspection at `78b909e` (2026-10-11) identifies these adapter
constraints, not live-host acceptance:

- `vdif_unpack_worker --ready-file` publishes preparation evidence before the
  input read lock/header arrives; this is not first-block or output evidence.
- `rdma2dada` exposes startup log messages but no machine-readable ready-file
  argument. Its SIGINT/SIGTERM path stops the receiver and then emits raw EOD.
- `pipeline_worker` exposes resolved-plan and final-metrics arguments, but no
  explicit pre-header readiness snapshot. CUDA ring-registration log output
  alone does not prove algorithm initialization, which occurs in transfer open.
- Unpack/GPU signal handlers set a stop flag checked around the read loop;
  bounded exit from a blocked PSRDADA call is unproven. Normal drain must stop
  ingress first; force cleanup needs independently verified resource ownership.

Keep readiness distinct from transfer-open/completed evidence. Decide the
smallest optional runtime hooks after Phase 0; do not transplant the performance
runner's sleep/PID/log checks as a production readiness contract.

- [ ] Generate process argv from explicit binary/profile inputs; no shell interpolation or arbitrary user command strings. Host-local compiler resolves bundles from their final path before ring allocation.
- [ ] Use an exact-key resource claim before create; refuse foreign IPC instead of deleting it. Validate CPU logical and physical-core overlap and available GPU ownership at runtime, not only offline.
- [ ] Prepare in reverse-consumer dependency order (rings, output consumer, GPU, unpack, receiver) using observed readiness, not sleep-only success. Mark ready only when required processes and interfaces are verified, even before a data header has arrived.
- [ ] Implement run-scoped drain and forced cleanup. Stop ingress first and allow downstream EOD to finish; do not broadcast SIGTERM to every stage at once and discard buffered data. Normal finite finish, input silence and missing Station behavior must remain truthful.
- [ ] Prove the host supervisor remains alive when the control connection drops and reports/reconciles state on reconnect. Test timeouts without touching an unknown run.
- [ ] Run fake-process ownership/error tests, then an authorized small functional server gate. Any product stop/readiness changes require affected existing correctness/Full regression on the feature build; they are not automatically backported to the paper branch.

#### MS-07a — First independently testable slice: SSH command transport

Plan approved and transport-only acceptance completed on 2026-10-11.
See [server evidence](../../control/evidence/ms07a-acceptance-20261011.json) and
[coordinator audit](../../control/evidence/ms07a-acceptance-audit-20261011.json).
Preserve the
approved execution method: one persistent gpt-6.1-sol/high task, coordinator
review, then the existing GPU-server test owner. No automatic Git operations.

**Files:** create `scripts/multiserver/transport.py` and
`tests/multiserver_transport_test.py`; register the test in `CMakeLists.txt`;
extend `docs/control/lifecycle.md` with the accepted transport contract.
Do not edit controller/store behavior, processing binaries or the paper runner.

**Interfaces:** `SSHHostAdapter(endpoints, *, ssh_path, timeout_seconds)`
accepts a per-command timeout in `(0, 3600]` seconds, not an observation duration;
implements the existing `send(instance_key, command) -> dict`. Endpoints are a
trusted deployment mapping keyed by server ID, containing `ssh_host`, absolute
`python_path`, absolute `host_entrypoint`, and absolute `known_hosts_file` on
the control host. They are not executable arguments supplied in an observation
or Web request. Validate instance/command server and GPU identity before launch.
The host entrypoint is a future dependency; tests use a dedicated fixture and
must not advertise the production supervisor as implemented.

**Transport decision:** run from HF directly to qths1; one noninteractive SSH
request, no nested SSH in the adapter, no third-party SSH package. Send the
command as finite, strict UTF-8 JSON on stdin, never as shell text. SSH still
uses a remote shell: quote only the trusted fixed Python/entrypoint argv using
`shlex.quote`, and keep all runtime payload fields off that command line.
Use `-T`, `BatchMode=yes`, `StrictHostKeyChecking=yes`, explicit known-hosts
and bounded connection/request timeouts. Do not enroll keys or change SSH config.

**Failure contract:** one dispatch only, no automatic retry. Timeout raises
`TimeoutError`; SSH/connection/nonzero-exit failures raise `OSError`, preserving
MS-06's durable UNKNOWN outcome. Malformed, duplicate-key, nonfinite or
non-object responses raise a transport protocol `ValueError`; the controller
retains its recorded uncertainty. Response identity/state validation stays with
MS-06 rather than duplicating its state machine. Bound each output stream to
1 MiB while reading, not after unbounded `communicate()` allocation. Reject an
oversized request before spawn. On local timeout/overflow, reap the SSH child;
never claim the remote action was cancelled or issue a remote cleanup command.

**Review focus:** shell metacharacters/Unicode in JSON remain data; unknown
targets cannot create an SSH process; delayed or lost replies never replay an
action; noisy/truncated output cannot exhaust memory or become a valid report;
transport failure leaves durable controller uncertainty rather than a false stop.

- [x] Add failing unittest cases for exact fixed SSH argv/stdin bytes, successful
  report round-trip, identity mismatch before spawn, invalid endpoint/path,
  strict JSON decoding, stdout/stderr limits, timeout/child reaping, exit255,
  and zero implicit retry. Include a real local fixture subprocess, not mocks
  alone, for timeout/output-bound behavior.
- [x] Implement the minimal standard-library adapter with Python3.8-compatible
  syntax. Test limits without logging full command payloads or credentials.
- [x] Add controller integration cases showing a timeout retains pending action
  identity and reconcile does not replay prepare/start. Run
  `python3 -B -m unittest discover -s tests -p multiserver_transport_test.py -v`
  and the existing `multiserver_controller_test.py`/`multiserver_store_test.py`.
  Register `multiserver_transport_test`; PASS requires all tests exit0 with no
  leftover fixture processes. This is local evidence only.
- [x] Coordinator reviews diff and failure paths; freeze source identity only
  at handoff. Test owner runs the affected registered Linux tests x3 and one
  versioned read-only HF→qths1 fixture round-trip/failure test. Its fixture
  exchanges JSON only: no host state, service, rings, GPU or observation start.
  Preserve commands/results/cleanup in the feature evidence namespace.
- [x] Record transport-only acceptance and leave commit to the user.
- [ ] Plan
  MS-07b process ownership/supervisor against the accepted adapter and observed
  logind/session behavior; keep system configuration changes separately approved.

MS-07b owns detached supervision, durable host action outcomes and resource
claims; MS-07c owns actual binary readiness/drain integration. Neither is covered
by an MS-07a PASS. Live data rates and data-time snapshots remain P4.

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

**Create after Phase 0 and P3:** `scripts/multiserver_acceptance.py`, `config/testing/multiserver-functional-suite.json`, `tests/multiserver_acceptance_test.py`. Extend existing configuration/lifecycle guides and add one final acceptance record linked by the ledger; do not duplicate them with parallel usage/status documents.

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
