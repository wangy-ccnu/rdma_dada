# MS-04a independent review — 2026-10-08

Coordinator: `01a090fd-55d8-7b83-9ea1-1a53eaa1b952`.
Child: `01a11b57-b539-7d80-86e4-0c72b4892a29`, title `MS-04 实例配置包生成`.
Checkout: `/Users/ywang/.codex/worktrees/1209/rdma_dada`, base5851eaed.
Scope: dirty tracked diff plus new inventory example/configuration documentation.
Child callback PASS; its report mistakenly labeled the coordinator ID as TASK_ID;
the native source thread ID above is authoritative.

## Standards

No documented-standard violations. Pure validation and scope isolation are
preserved. One optional duplicate test setup is overwritten by the existing
same-host helper; no new abstraction is needed.

## Spec

- P2: deployment.py validates supported verbs port while reading every inventory
  NIC. An unused eth1/port2 incorrectly rejects valid eth0/port1 selections.
  Keep inventory positive-integer validation and check runtime support only
  after resolving the selected NIC.
- P3: frequency test covers global F7/F4 and first assignment, but not the
  offset tail's first center1301000000/local center1302000000 Hz. Add integer
  assertions in the existing test, not a new production frequency API.

Standards: 0 blocking /1 advisory. Spec: 2 findings, worst P2.
Both reviewers used gpt-6.1-sol/high. Coordinator independently ran model26+
deployment41=67 tests and diff check: PASS. Examples match fixture builders.
These are local development checks, not server acceptance.

## ponytail-review

1. `tests/multiserver_deployment_test.py:L320`: delete: receiver setup overwritten by `use_second_gpu_on_node_a` below. Keep the helper's assignment.

net: -4 lines possible.

## Disposition

CHANGES_REQUIRED. Findings delivered to the same child; no MS-04b execution,
remote run, commit or archive yet. Optional simplification is advisory only.

## Correction re-review

Coordinator recovered the final correction through wait_threads (callback
authorization failed on follow-up). The selected-NIC check now lives after
interface lookup; unused port2 inventory passes, selecting port2 fails with
profile/interface evidence. Tail first/center frequency assertions are present.
The redundant four-line test assignment was removed by the child.

Independent rerun: model26 + deployment43 =69 tests PASS; diff check PASS.
Standards0 remaining / Spec0 remaining. ponytail-review: Lean already. Ship.
Here "Ship" means no complexity finding, not remote acceptance or Git authority.
MS-04a local review PASS / server gate pending. Continue to MS-04b using the
explicit dependency handoff; source manifest freezes at the combined test gate.
