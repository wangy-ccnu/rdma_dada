# Multi-server development task ledger

Coordinator: 多服务器同步开发 (`01a090fd-55d8-7b83-9ea1-1a53eaa1b952`).
Branch: `codex/multi-server-observation`. Updated: 2026-10-15.
Procedure: [delegated development](../agents/delegated-development.md).

## Current acceptance: MS-07b/c and I1

**ACCEPTED_BUILD_AND_NO_DATAFLOW_GATES**, confirmed by the user on 2026-10-15.
Base `2b746a4`; implementation is integrated in this feature checkpoint.
Independent specification, standards and simplicity reviews are
closed. Coordinator portable CTest: 61 selected tests passed, plus one separately
executed local loopback test. These local results are development evidence.

[Server acceptance](evidence/ms07bc-build-gates-20261015.json) records the tested
source manifest, build/binary identities and the accepted three-repeat gates.
The [tested manifest](manifests/ms07bc-tested-source-20261015.sha256) preserves
that source identity; subsequent documentation changes belong to the current
checkpoint manifest and do not change the tested code.
HF/qths1 source verification passed 309/309 entries. Fresh CUDA/RDMA build:
`/home/user/wy/build-multiserver/ms07bc-20261015`. Compiler, receiver, unpack,
GPU worker and read-only PSRDADA ABI probe built successfully; 13 selected
registered tests met the three-consecutive-pass requirement. Host-test acceptance
uses the isolated three-pass sequence. Remote logs were not changed.

This accepts compilation, controller/host/process tests, compiler integration and
read-only ABI checks. It does **not** accept real packet reception, GPU data flow,
multi-instance data closure, runtime fault isolation or throughput. No task
dataflow resources or temporary capabilities were created. Functional acceptance
remains pending; temporary CAP_NET_RAW, if needed, still requires authorization.
Future tests send no preparation traffic; see [testing policy](../agents/testing.md).

### Retained implementation tasks

All five use gpt-6.1-sol/high from base `2b746a4`. Paths below are relative to
`/Users/ywang/.codex/worktrees/`; all checkouts remain retained. Shared integration,
CMake and maintained guides belong to the coordinator, not to child carry-in files.

| Task title | Task ID | Checkout | Owned implementation |
|---|---|---|---|
| MS-07b B1 主机资源归属与持久记录 | `01a13e05-f037-7fb2-8b7d-6969b0bdb370` | `5eaa/rdma_dada` | `ownership.py`, ownership tests |
| MS-07b B2 进程身份与后台监管 | `01a13e1d-cfa8-7cb0-96f3-64ad8acb2726` | `b910/rdma_dada` | `processes.py`, process tests |
| MS-07b B3 主机命令与常驻监管入口 | `01a13e2a-02f6-7582-8a36-187e03fcef87` | `dca7/rdma_dada` | `host_agent.py`, host CLI/tests |
| MS-07c C1 实际流水线生命周期接入 | `01a13eab-ef59-7992-82ce-e813c3872c50` | `5cf9/rdma_dada` | runtime/compiler integration, optional app hooks, shared lifecycle publisher and tests |
| MS-07 I1 统一功能验收入口 | `01a13edf-bd5e-7691-8e0b-642805af5477` | `04ed/rdma_dada` | acceptance runner/tests and fixed peer fixture |

Implementation deliveries and the accepted no-dataflow stage are closed;
all five conversations are ARCHIVED on 2026-10-15, independently confirmed in
the app's archived-thread listing. Remaining real functional acceptance is
explicitly transferred to the coordinator and existing server-test task, not
marked complete. No implementation correction is currently assigned to these
children. Archive conversations only; retain all source checkouts and evidence.
Some child callbacks were denied; the coordinator retrieved their original frozen
deliveries through thread read/wait before review and integration.

### Evidence still needed for the next stage

- Use the existing versioned acceptance runner and immutable suite/deployments:
  single instance, two GPUs with equal/+1 channels, all-ready gating, rollback,
  finite EOD, graceful/forced stop, local failure with healthy peer continuing,
  and disconnect/reconnect; three clean repetitions per functional case.
- The [Phase0 refresh](evidence/ms07b-phase0-20261015.json) supplies observed tools,
  topology and permissions. The [disconnect probe](evidence/ms07b-disconnect-probe-20261015.json)
  proves ordinary SSH-request exit survival and natural child exit only, not
  reboot recovery, all-session logout survival or full pipeline operation.
- Dataflow permissions, exact fixture inputs and available receiving hosts must
  be resolved before launch. One receiver host with two GPUs is not cross-host
  receiver acceptance. No new saturation campaign is included.
- Product contracts, failure/cleanup rules and runner invocation are maintained
  only in [lifecycle](lifecycle.md); remaining work and isolation are in the
  [roadmap](../superpowers/plans/2026-09-12-multi-server-control-roadmap.md).
  Live business telemetry and UI remain later work.

## Accepted earlier stages

| Scope | Evidence / tested identity |
|---|---|
| MS-01/02 configuration validation | [acceptance](ms01-ms02-acceptance.md); `manifests/ms01-source.sha256`, `ms02-source.sha256` |
| MS-03–05 weights/bundles/compiler | [acceptance](ms03-ms05-acceptance.md); `manifests/ms03-ms05-tested-source.sha256` |
| MS-06 offline lifecycle | [acceptance](ms06-acceptance.md); `manifests/ms06-tested-source.sha256`; committed `d1580f6` |
| MS-07a transport only | [server result](evidence/ms07a-acceptance-20261011.json), [audit](evidence/ms07a-acceptance-audit-20261011.json); `manifests/ms07a-source-20261011.sha256` |
| Observation-only interface cleanup | [server result](evidence/ms08-observation-cleanup-acceptance-20261011.json); `manifests/ms08-source-20261011.sha256` |

Observation cleanup task **Observation旧配置接口清理**
(`01a12abc-4498-7883-8d45-0b7966a70533`, gpt-6.1-sol/high),
checkout `20b8/rdma_dada`, base `78b909e` plus reviewed MS-07a carry-in:
ACCEPTED / ARCHIVED 2026-10-11. It removed legacy standalone configuration
interfaces without changing algorithms/geometry. Its 17 Linux/CUDA registered
tests passed three times; original command evidence was retrieved after callback
failure. The result records the notification-only metadata difference.

| Task title | Task ID | Model / effort | Base | Native checkout suffix under /Users/ywang/.codex/worktrees/ | Status |
|---|---|---|---|---|---|
| MS-01 全局观测配置校验 | `01a099b5-6f39-7400-8dba-deb0f8ad1ef7` | gpt-5.6-luna / high | 920ccbb | `60bd/rdma_dada` | ACCEPTED / ARCHIVED 2026-09-30 |
| MS-02 部署配置与资源冲突校验 | `01a09ad1-e803-7bd1-bbb2-891073f5bd0a` | gpt-5.6-luna / high | 920ccbb | `ae1b/rdma_dada` | ACCEPTED / ARCHIVED 2026-09-30 |
| MS-03 权重按频道无损切分 | `01a0f2c3-08ab-79b2-897a-091bece9198f` | gpt-5.6-luna / high | 5851eaed | `e338/rdma_dada` | ACCEPTED / ARCHIVED 2026-10-09 |
| MS-04 实例配置包生成 | `01a11b57-b539-7d80-86e4-0c72b4892a29` | gpt-5.6-luna / high | 5851eaed | `1209/rdma_dada` | ACCEPTED / ARCHIVED 2026-10-09 |
| MS-05 现有编译器兼容性验收 | `01a11b7e-d6d1-7942-b378-5e520e48d2c3` | gpt-6.1-sol / high | 5851eaed | `179b/rdma_dada` | ACCEPTED / ARCHIVED 2026-10-09 |
| MS-06 观测控制生命周期 | `01a128d3-928e-75d0-9a64-22ff35d0cad7` | gpt-6.1-sol / high | 82ac66f | `bec6/rdma_dada` | ACCEPTED_OFFLINE_CONTROL / ARCHIVED 2026-10-11 |
| MS-07a SSH命令传输 | `01a129ce-5383-79a3-abe1-1f6712304f9a` | gpt-6.1-sol / high | 78b909e | `14f7/rdma_dada` | ACCEPTED_TRANSPORT_ONLY / ARCHIVED 2026-10-11; integrated locally |


Archived means conversation visibility only; source and evidence remain retained.
MS-04a/04b were checkpoints in one task. Historical model settings are not rewritten.

## Document and JSON retention

Keep material only when a user needs to read it or later development/testing needs
to use it. Usage/contracts belong in configuration/lifecycle guides, next actions
in the roadmap, status/task identity here and in PROJECT_STATUS, machine-readable
results in evidence JSON. Do not copy per-round delivery/review transcripts into
these documents or create another archive/status report.

Keep executable schemas/examples/fixtures and authoritative evidence with their
frozen identities. A result JSON and its concise guide link are complementary,
not duplicate reports. Historical manifests describe tested snapshots, not today's
edited documentation. Refresh current identity only at the next stage freeze.
Paper/ablation branches, remote artifacts and retained child checkouts are outside
this cleanup. Commit/push/merge remain user decisions.
