# MS-01/MS-02 — Combined pure-Python server acceptance

Status: AUTHORIZED on 2026-09-30 after read-only Phase0 completed.
The user approved one complete server pass of the 24 model and 36 deployment
tests on this combined source snapshot. This supersedes the previous three-round
handoffs for MS-01/MS-02 only; process/concurrency/data-flow gates are unchanged.

Coordinator: `多服务器同步开发`, `01a090fd-55d8-7b83-9ea1-1a53eaa1b952`.
Implementation: `MS-02 部署配置与资源冲突校验`,
`01a09ad1-e803-7bd1-bbb2-891073f5bd0a`.
Procedure: `/Users/ywang/WorkFile/code/rdma_dada/.worktrees/multi-server-observation/docs/agents/delegated-development.md`.

## Frozen source

- Worktree: `/Users/ywang/.codex/worktrees/ae1b/rdma_dada`.
- Detached base: `920ccbb458845badb1246743d95cebd174a4d01d`, derived from
  `codex/multi-server-observation`; no source commits/integration performed.
- Full manifest in coordinator worktree: `docs/control/manifests/ms02-source.sha256`.
- Manifest SHA256: `4069ef814e726fde2958567a261bfe4e71fbe9fb47b4cb747958c6323778cb50`.
- Entries: 294, all independently checked against this source.
- The included paper manifest is an older source file, not this handoff's
  authority. Do not regenerate or select it for the feature identity gate.

The source includes five unchanged MS-01 model/schema/test dependencies and
its fixture expanded by MS-02. The original frozen MS-01 checkout is unchanged.
Accept both layers using this combined source identity. The original MS-01
snapshot is historical and need not be separately mirrored/tested; do not
retroactively claim that earlier snapshot was executed on the server.

Affected MS-02 files and hashes:

```text
43443a4186324be658bfcb68ea70d470bb90e3b366a95c4d28839d3ad16c1834 config/multiserver-deployment-v1.schema.json
934b850e9d182fe56263248327c591cb9ea98b07f756be1a49bf48eaccc4f93d config/deployments/multiserver.example.json
94ebaca96f672f25483cf1e80543c40e1b2fadd826befea02862802b189b128e scripts/multiserver/deployment.py
edc7d7ad79c054eb7297894052e068f1b24242af7d38cdf8a5833790432607ef tests/multiserver_deployment_test.py
a539ba1d505ff75a6e49a094726a5e89522509dd9f40a7fd9704310c31112034 tests/multiserver_fixture.py
```

## Resume gate and commands

Phase0 on 2026-09-30 confirmed HF/qths1 SSH, /usr/bin/python3 3.8.10,
rsync 3.1.3, sha256sum and available capacity. Both feature directories were
absent and /home/user/wy was owned by user:user. Recheck namespace ownership
immediately before creation; reuse the completed Phase0 evidence unless an
environment change or failed command requires a targeted check.
Use the isolation/ownership/mirror protocol in `ms01-server-test-handoff.md`:
feature source `/home/user/wy/rdma_dada-multiserver`, evidence under
`/home/user/wy/task8c-multiserver-results/`. Verify ownership before use and
preserve paper canonical sources/builds/results. No qtp or data-flow activity.

Mirror the complete frozen tree with precise exclusions, preserving all294
manifest files including tests/data and docs/results. Verify the full manifest
before any test. On qths1, from the verified source root, run each command once:

```text
/usr/bin/python3 -B -m unittest discover -s tests -p 'multiserver_model_test.py' -v
/usr/bin/python3 -B -m unittest discover -s tests -p 'multiserver_deployment_test.py' -v
```

Expect 24 and 36 tests respectively and exit 0 for both. Existing manifest/profile
tests are unchanged and already locally verified; they are not rerun here.
Full source SHA256 verification remains mandatory and is not a unit test.
No installed
jsonschema, NumPy, CUDA, CMake build or nonexistent CTest target is needed.
Future test registration/compiler integration remains a separate task.

## Required behavior and evidence

Validate fake inventory only: physical-host aliases; GPU UUID and CPU/SMT
conflicts; ring-key normalization; receive-flow wildcard/physical-port alias
overlap; same-host path containment/collision including disk outputs; cross-host
path reuse; RAM/VRAM reservations; exact packet/MTU facts and unknown handling.
Direct mode requires one CUDA slot. Explicit poll/WR/window/queue choices are
retained. WR upper bound is `(T/S)*A`, not local F: A4/F4+F3 permits128 and
rejects129 for either instance; A2 rejects65. Invalid types/IP/protocol fail
with stable ValueError codes. Inputs remain unchanged.

Schema assertions cover supported local refs, patterns, null, oneOf and bounds;
they are limited test assertions, not a substitute for a general JSON-schema
implementation. Runtime validation is authoritative. Static outputs always
have runtime_ready=false; actual host readiness, filesystem realpath/ownership
and compiler/algorithm memory demand remain unverified. WAIT polling and
explicit cross-NUMA placement retain additional prerequisite markers as needed.
This layer does not generate complete runtime bundles or implement algorithms.

Retain compact JSON with source manifest, host/Python identity, repetition_count=1,
argv/test-count/exit/stdout-stderr, first failure and cleanup status. Verify no
test-owned processes/rings/capabilities were created. No remote source edits,
installs, Git operations, GPU or RDMA/network workloads. Return actual results
to coordinator ID above and check successful delivery, with notification and
test/cleanup status separate. PASS establishes deterministic validator and
Linux/Python compatibility acceptance only, not real deployment or performance.
