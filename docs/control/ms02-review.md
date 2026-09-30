# MS-02 independent review — 2026-09-13

Coordinator: `01a090fd-55d8-7b83-9ea1-1a53eaa1b952`.
Implementation task: `01a09ad1-e803-7bd1-bbb2-891073f5bd0a`,
MS-02 部署配置与资源冲突校验.
Procedure: `/Users/ywang/WorkFile/code/rdma_dada/.worktrees/multi-server-observation/docs/agents/delegated-development.md`.
Scope: `ms02-development-handoff.md`; target checkout
`/Users/ywang/.codex/worktrees/ae1b/rdma_dada`, base `920ccbb`.
The five owned files are uncommitted additions/fixture extension. Their initial
hashes are in `task-ledger.md`; no tracked-diff-only review was used.

## Standards

Independent reviewer reported three documented-standard violations:

- P1, deployment.py:360: control/data overlap checks only logical IDs. The
  example accepts unpack CPU1 and control CPU7 on physical core (0,1), violating
  plan line131's separate control/data-path core reservation.
- P2, deployment.py:242: CPU elements are hashed before type validation.
  `cpu.receiver=[{}]` raises TypeError, violating the stable-code ValueError
  boundary with offending field promised by the handoff.
- P1, deployment.py:297: DADA_DBDISK destination is only a nonempty string.
  `/outside/feature/root` passes, violating plan line134's path containment.

No additional actionable baseline smell was reported. Python3.8 syntax,
standard-library dependencies, input independence and owned-file scope appear
preserved. All22 existing deployment tests pass but miss these cases.

Standards summary: 3 findings; worst within this axis is physical-core or
runtime-path isolation falsely passing (P1).

## Spec

Independent reviewer reported seven findings:

- P1, deployment.py:414: identical flows on two interface aliases of the same
  physical port pass because the conflict key includes interface spelling.
  Handoff83–86 requires actual physical receive-resource overlap checks.
- P1, deployment.py:360–362: control/data SMT collision in the default fixture
  is accepted; physical checks apply only against earlier instances. Handoff79–80
  requires control CPUs not to consume reserved data-path cores.
- P2, deployment.py:293–297: disk destination `/etc/escape` passes. Handoff87–91
  and plan134 require claimed runtime paths to stay below the feature root.
- P2, deployment.py:394–400: path collisions omit host identity, rejecting
  identical paths on independent hosts. Spec57 scopes collisions by host.
- P2, deployment.py:420–423: arbitrary `made-up/frontend.example-v1.json`
  receives known32-byte header geometry based on suffix alone. Handoff99–104
  requires unsupported/unverified wire contracts to remain explicit.
- P2, deployment.py:242: `cpu.workers=[{}]` raises TypeError, not the handoff68–70
  stable-code ValueError.
- P2, schema lines10,107–108: decoded path patterns have unterminated character
  classes. Existing field-name tests do not exercise the schema patterns.

Spec summary: 7 findings; worst within this axis is physical-port/core isolation
falsely passing (P1). These overlap Standards findings; do not count as ten
distinct defects.

## Coordinator additional checks

Independent local runs: deployment22/22, global24/24, manifest2/2, profiles12/12
PASS. The coordinator reproduced schema, SMT, host-path, interface-alias and
TypeError findings directly without any resources or remote operations.

Additional invalid declarations accepted: destination_ip=`not-an-ip`,
protocol=`TCPP`, and SYNCHRONOUS_DIRECT/cuda_slots=3. The existing C++ contract
requires direct/1 (src/config/observation_config.cpp:694–700). Validate these
known contracts now rather than forwarding malformed values to a later phase.
Schema/validator agreement must extend through $refs and profile map entries;
the current schema/example test stops before them.

The explicit profile still lacks poll batch and unpack window/queue declarations
required to preserve user runtime choices. Resolve the minimal existing-field
mapping from repository contracts in this bounded task; report genuinely
compiler-only mappings as pending, without inventing runtime defaults or
changing product schemas. BUSY/WAIT is not a substitute for poll batch.

During correction, the coordinator sent these same-scope clarifications through
the existing task (delivery confirmed): keep one WR-depth field rather than
independent work_requests/receiver_wr_num inputs; an unsupported polling mode
must not imply runtime capability; include disk destinations in same-host path
claims; distinguish GPU hardware NUMA locality from user-selected CPU/memory
placement. Deliberate cross-NUMA placement is not an invalid topology and must
not be rejected merely because the GPU is attached to another node. Record
unverified runtime/performance aspects separately.

## Correction handoff

Return corrections to the same Luna task, not a new checkout. Apply the original
ownership boundary: four MS-02 new files and its fixture extension only; preserve
five MS-01 dependencies and global_example() semantics. Fix the findings with
red/green regressions. Keep derived reservation/MTU and actual host readiness
distinct. Add explicit filesystem-realpath/ownership prerequisite; pure lexical
checks do not inspect symlinks. Scope path and flow claims by physical host;
normalize supported flow values before comparing rules and reject unsupported
forms instead of guessing. Validate malformed CPU members before hashing.

For schema verification use the existing standard library (compile supported
patterns, walk local $refs/maps, exercise matching examples and negative cases);
do not introduce a package dependency. Retain false runtime_ready for all static
outputs. A known wire-contract alias is an explicit declaration, not proof of
arbitrary file contents; unmatched paths must not receive guessed geometry.

Run original local commands plus the new regressions. Report all five final
SHA256 values from one frozen snapshot and changed behavior. qths1 remains
deferred; no remote retries, source integration, commit/push/merge/archive or
Task3 implementation. Deliver the report through the existing app callback;
if rejected after the permitted retry, keep notification FAIL and leave the
report in the task. The coordinator actively waits on the known actual task ID;
no alternate delivery bypass is authorized.

## Correction review 1

Coordinator and independent reviewers confirmed all original Standards3 and
Spec7 findings fixed. Local deployment33/global24/manifest2/profiles12 passed.
Two introduced P2 issues were returned to the same task before acceptance:

- Spec: the new example sets WR1024 but known raw records/block is
  (T16384/S512)*A4=128. Existing receive_policy.cpp requires WR<=records/block.
  Use compatible example settings and test the exact upper boundary/rejection.
- Standards/test evidence: the small schema assertion helper treats type:null
  as unconstrained, oneOf as anyOf and omits numeric bounds, so malformed IP and
  port70000 pass the test helper. Correct only supported assertions and add
  negative checks; no new package or general schema framework is required.

Both requests were successfully delivered via send_message_to_thread to the
existing implementation task. Review remains CHANGES_REQUIRED pending reruns.

Correction review2: schema-helper null/oneOf/bounds fixed and local59 combined
tests passed. The WR implementation mistakenly used local F instead of A;
the boundary test only covered node-a where F=A=4. Coordinator rechecked C++
resolved_observation_plan.cpp's groups_per_block*nant formula and returned this
error to the same task. Required regression: both F4/F3 instances with A4 accept
WR128/reject129; with A2, both reject65. This is correction of the original
known static formula, not new scope. Acceptance still pending.

## Final coordinator disposition

Local review PASS for the final snapshot recorded in task-ledger.md. Formula
now uses len(station_ids); both F4/F3 branches and A2 negative cases verified
independently. Supported schema null/bounds/oneOf assertions verified with
negative probes. Combined60/60 tests, manifest2/2, profiles12/12 passed.
Original Standards3/Spec7 findings and correction findings are resolved.
Server acceptance is DEFERRED by user; no runtime-readiness claim, source
integration, Git write or archive follows from local review.
