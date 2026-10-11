# Multi-server development task ledger

Coordinator: 多服务器同步开发 (`01a090fd-55d8-7b83-9ea1-1a53eaa1b952`).
Procedure: [delegated development](../agents/delegated-development.md).
Branch: `codex/multi-server-observation`. Updated: 2026-10-11.

## Current status and reading guide

| Need | Authoritative document |
|---|---|
| Current project status | [PROJECT_STATUS](../PROJECT_STATUS.md) |
| Inputs, bundle generation, compiler resolution and configuration tests | [configuration](configuration.md) |
| Controller/store API, host contract, stop/restart policy and offline tests | [lifecycle](lifecycle.md) |
| Agreed system requirements | [design](../superpowers/specs/2026-09-12-multi-server-observation-design.md) |
| Remaining work and branch/server isolation | [roadmap](../superpowers/plans/2026-09-12-multi-server-control-roadmap.md) |
| MS-01/02 validator acceptance | [MS-01/02 acceptance](ms01-ms02-acceptance.md) |
| MS-03/04/05 configuration acceptance | [MS-03–05 acceptance](ms03-ms05-acceptance.md) |
| MS-06 offline lifecycle acceptance | [MS-06 acceptance](ms06-acceptance.md) |

MS-01–05 are committed and accepted for offline configuration. MS-06 is
reviewed/server-accepted for offline control and committed as `d1580f6`.
All six child conversations are confirmed archived. No live host supervisor,
distributed process startup, telemetry or Web/CLI acceptance is implied.
Next work is roadmap P3, followed by P4–P6; it requires its own bounded handoff.

P3/MS-07 read-only environment investigation is reviewed (2026-10-11), performed by
`GPU服务器代码测试` (`019fbb68-ae6f-77f0-bfdd-30bc30bd6c45`) from committed
base `78b909e`. Scope: read-only HF/qths1 supervision, transport, ownership and
readiness prerequisites. Original [evidence](evidence/ms07-phase0-20261011.json)
and [supplement](evidence/ms07-phase0-supplement-20261011.json) were retrieved and
reviewed; both callbacks were delivered. No source synchronization, build, CTest
or data flow occurred. Facts support the MS-07a transport plan, not live-host
acceptance: JSON round-trip/disconnect survival and effective logind session
cleanup remained unproven at Phase0. MS-07a later verified the JSON round-trip;
disconnect survival and logind behavior remain future gates. MS-07a was approved on2026-10-11. Its persistent
gpt-6.1-sol/high worktree task was requested from feature base78b909e;
task ID `01a129ce-5383-79a3-abe1-1f6712304f9a` is ACCEPTED_TRANSPORT_ONLY as
**MS-07a SSH命令传输**, checkout
`/Users/ywang/.codex/worktrees/14f7/rdma_dada`.
Scope is transport/tests/CMake/lifecycle guide only.
The child froze its implementation; spec and standards review passed after one
timeout-range correction. Command timeouts now have an explicit one-hour maximum,
rejected before spawn when out of range. Coordinator independently reran the
final transport 22/22 and verified the reviewed five-file delivery was integrated
byte-for-byte into this feature checkout. Store/controller/transport registered
CTest passed 3/3 locally. Linux registered tests met the three-repeat gate and
HF→qths1 JSON-only echo/exit7 probe passed 3/3. No data flow was started.
The child's callback was denied by the messaging tool; the coordinator retrieved
the full original delivery using the thread read/wait interface and continued
review. Its corrected delivery was later successfully received after explicit
human authorization. Notification failure is not an implementation or test failure.
The server callback was delivered successfully. See the original
[acceptance](evidence/ms07a-acceptance-20261011.json) and
[coordinator audit](evidence/ms07a-acceptance-audit-20261011.json): all actual
extra repetitions are retained (store6/controller9/transport9, 834 case
executions), and the notification-only evidence hash transition is explained.
The tested source identity is frozen in
[MS-07a manifest](manifests/ms07a-source-20261011.sha256), SHA256
`3ed01d0f76a147f75b41d7f86c7a2c5ee91b30de0c389353e44d5afd4e31824b`.
Post-test status-document edits do not rewrite that tested snapshot. No automatic
commit/push/merge. After the acceptance notice, the child conversation was
confirmed archived through the app's archived-thread listing; its source
worktree, reviewed implementation and test evidence remain retained.
Coordinator-owned Phase0 evidence, roadmap and this ledger are read through
their existing absolute paths, not assumed to exist in the fresh checkout.

MS-07a preflight plan check: the transport produces the existing MS-06
`host_adapter.send` contract without changing controller/store behavior;
MS-07b will supply the production host entrypoint. The independent MS-07a
fixture is JSON-only, not a substitute supervisor. There are no concurrent
implementation owners of transport/tests/CMake/lifecycle. Test obligations
match the byte limits, timeouts, strict JSON and uncertainty behavior described
in the roadmap; no plan conflict identified.

## Task identity and retained source

Observation-only interface cleanup is ACCEPTED (user-approved 2026-10-11), ahead
of MS-07b. Task **Observation旧配置接口清理**
(`01a12abc-4498-7883-8d45-0b7966a70533`, gpt-6.1-sol/high) uses isolated
checkout `/Users/ywang/.codex/worktrees/20b8/rdma_dada`, base `78b909e` plus
the five reviewed MS-07a carry-in files. It owns removal of standalone legacy
pipeline/unpack/worker configuration loaders, examples and conversion/inspect
tools; migration of useful regression coverage to Observation/resolved plans;
and affected configuration/test guides and CMake registrations. Runtime structs,
geometry helpers and algorithms remain; sender schemas, original evidence and
paper branches are out of scope. Coordinator owns integration, independent
review and server handoff. No automatic commit/push or new performance campaign.

Implementation is frozen and integrated into this checkout. Independent spec and
standards review passed; retained runtime/geometry implementations are unchanged.
The child clean portable build passed 52 registered tests (excluding unrelated
sender loopback); the coordinator independently reran nine affected tests and
confirmed compiler preflight output is byte-identical to the pre-cleanup baseline.
Local evidence remains in the child checkout's
`build-observation-only-final/local-evidence/`.
The server [read-only readiness evidence](evidence/ms08-phase0-config-cleanup-20261011.json)
confirms the fixed CUDA/CMake/PSRDADA environment. Linux/CUDA
[acceptance](evidence/ms08-observation-cleanup-acceptance-20261011.json) passed:
323/323 mirrored files on HF/qths1, fresh CUDA/RDMA build, 17 registered tests
each repeated three times (51 executions). Coordinator retrieved the original
server command output `exec-5f4cef44-52bf-4746-85cd-d8df7db2427f` from turn
`01a12acd-ecca-75d3-8280-a6eb6ec5937c`, independently counted all 51 PASS lines
and checked all 17 exit-zero records. No data flow or rings were created.
The tested [source manifest](manifests/ms08-source-20261011.sha256) preserves
SHA256 `86c5fba8e56fffc1858c2950d003a3d5d1d0429b2430c3ffcd02bcf3452be499`;
later status-document edits do not change the tested implementation.
Both server callback attempts were denied; original delivery was retrieved by
thread wait/read. This is a notification failure, not a test failure.
Remote acceptance copies retain pre-notification SHA256 `041df0dbabce8ddabe2ee68295a9898bfe85fbf6742f1b457c17e767d631ee4a`;
the local original adds notification failure metadata only, SHA256
`683a7bedb2f464b7e13ca59bbb4f14b3ea3a019f76cde344064dbf51a2cbceeb`.
After acceptance notification, the child decided to archive its conversation;
the coordinator confirmed its ID in the app's archived-thread listing on
2026-10-11. Its retained worktree and evidence remain intact.
No commit/push was performed.

All checkouts below are retained; archived means conversation visibility only.
MS-04a/04b were checkpoints in the same MS-04 task, not separate tasks.

| Task title | Task ID | Model / effort | Base | Native checkout suffix under /Users/ywang/.codex/worktrees/ | Status |
|---|---|---|---|---|---|
| MS-01 全局观测配置校验 | `01a099b5-6f39-7400-8dba-deb0f8ad1ef7` | gpt-5.6-luna / high | 920ccbb | `60bd/rdma_dada` | ACCEPTED / ARCHIVED 2026-09-30 |
| MS-02 部署配置与资源冲突校验 | `01a09ad1-e803-7bd1-bbb2-891073f5bd0a` | gpt-5.6-luna / high | 920ccbb | `ae1b/rdma_dada` | ACCEPTED / ARCHIVED 2026-09-30 |
| MS-03 权重按频道无损切分 | `01a0f2c3-08ab-79b2-897a-091bece9198f` | gpt-5.6-luna / high | 5851eaed | `e338/rdma_dada` | ACCEPTED / ARCHIVED 2026-10-09 |
| MS-04 实例配置包生成 | `01a11b57-b539-7d80-86e4-0c72b4892a29` | gpt-5.6-luna / high | 5851eaed | `1209/rdma_dada` | ACCEPTED / ARCHIVED 2026-10-09 |
| MS-05 现有编译器兼容性验收 | `01a11b7e-d6d1-7942-b378-5e520e48d2c3` | gpt-6.1-sol / high | 5851eaed | `179b/rdma_dada` | ACCEPTED / ARCHIVED 2026-10-09 |
| MS-06 观测控制生命周期 | `01a128d3-928e-75d0-9a64-22ff35d0cad7` | gpt-6.1-sol / high | 82ac66f | `bec6/rdma_dada` | ACCEPTED_OFFLINE_CONTROL / ARCHIVED 2026-10-11 |
| MS-07a SSH命令传输 | `01a129ce-5383-79a3-abe1-1f6712304f9a` | gpt-6.1-sol / high | 78b909e | `14f7/rdma_dada` | ACCEPTED_TRANSPORT_ONLY / ARCHIVED 2026-10-11; integrated locally |

New development uses gpt-6.1-sol/high; historical model settings are not rewritten.
MS-01/02 were integrated before base5851eaed; MS-03–05 were committed in82ac66f.
MS-06 implementation, tests and frozen acceptance evidence were committed in
`d1580f6` on2026-10-11 at the user's request; no merge into the paper branch.
The coordinator confirmed archived IDs independently. Several child/server
callbacks were refused; acceptance relied on retrieved original reports and
command evidence, not an assumed notification. See each acceptance record.

## Evidence and document maintenance

Keep only information a user needs to read or future development/tests need to
use. Completed handoffs, review rounds and step-by-step plans are removed once
their unique contracts, limitations, reproduction commands and final findings
are incorporated into the documents above. No second archive directory is needed.

Acceptance JSON and frozen manifests under `evidence/` and `manifests/` remain
unchanged. Historical manifests describe their tested source snapshots, including
documents subsequently consolidated; they are not checks of today's checkout.
Original snapshots remain traceable through the retained source checkouts and
recorded Git bases. Generate current feature identity at stage/test handoff
checkpoints, never rewrite historical test identities to fit later documents.
Paper/ablation branches, accepted catalogs and remote artifacts are outside this
documentation cleanup. Commit, push and merge remain user decisions.
