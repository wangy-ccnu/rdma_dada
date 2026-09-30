# MS-02 — Deployment declarations and same-host isolation

Coordinator: `多服务器同步开发`, `01a090fd-55d8-7b83-9ea1-1a53eaa1b952`.
Model: `gpt-5.6-luna`, high reasoning. User authorized continuation while qths1
is unreachable; remote acceptance is deferred until the user reports recovery.

## Isolation and exact dependency

Create an app-managed independent worktree starting from
`codex/multi-server-observation` at
`920ccbb458845badb1246743d95cebd174a4d01d`. Verify actual HEAD before changes.
The old plan's coordinator-worktree-only wording/base febdc0e is superseded by
this explicit child-checkout handoff. Do not change either the coordinator
worktree or the paper root.

MS-01 is locally reviewed, not server-accepted or committed. Its frozen source
is `/Users/ywang/.codex/worktrees/60bd/rdma_dada`. Explicitly import these six
new files into the new checkout, only after verifying source hashes and that
destination paths do not already contain unrelated changes. Use apply_patch
for local file additions and verify byte-for-byte hashes after import. Do not
assume the app copied dirty changes, create an implicit commit or import the
paper worktree. Keep MS-01's source untouched.

```text
207ec374830e87d49734cc3eef252b24f4a1fe65229f55468a8c2b249ea85ce8 config/global-observation-v1.schema.json
a6f77463de03f40b9bd05b078c85bc97d50ee988c63b971ed8ab61fb1be68a89 config/global-observation.example.json
15d7c6c906c1815879d404138e65bfdeba285a885efe2dd5cc9e4e3986b04eb7 scripts/multiserver/__init__.py
902b5fc47aae6469dfed444a7832de280ab9fc090581ec35d1649503408c6d7a scripts/multiserver/model.py
d62ece557bd0669db9844a9ef08c8c085e5735a5560ed24fc63317a87cfb492d tests/multiserver_fixture.py
b8f7d1337c254acd4e019ac284a657efdbf599d7fe92ff2ff78041a6224c5749 tests/multiserver_model_test.py
```

Run the 24 model tests and existing manifest/profile regressions before editing
MS-02. Preserve five imported files unchanged. In the fixture, preserve
`global_example()` semantics and extend only for this task's deployment and
fake-inventory helpers. Record that fixture's final hash separately.

## Required reading

- Root rules: `/Users/ywang/WorkFile/code/rdma_dada/AGENTS.md`.
- Authoritative delegation rules:
  `/Users/ywang/WorkFile/code/rdma_dada/.worktrees/multi-server-observation/docs/agents/delegated-development.md`.
- This handoff, then the child's full spec
  `docs/superpowers/specs/2026-09-12-multi-server-observation-design.md` and
  Phase 1 implementation plan
  `docs/superpowers/plans/2026-09-12-multi-server-config-implementation.md`.
- Follow the TDD and executing-plans skills for Task 2 only. Keep Python 3.8
  compatibility and standard-library-only dependencies.

## Owned changes

New files:

- `config/multiserver-deployment-v1.schema.json`
- `config/deployments/multiserver.example.json`
- `scripts/multiserver/deployment.py`
- `tests/multiserver_deployment_test.py`

Extend `tests/multiserver_fixture.py` with `deployment_example()` and
`inventory_example()` only as needed. Do not edit global model/schema/tests,
old Observation files, CMake, manifest, algorithm code, paper catalogs, or
implement weights/bundles/compiler adapters, SSH, live discovery or launching.

## Deliverable and acceptance

Implement `validate_deployment(global_plan: dict, profiles: dict,
inventory: dict) -> dict` as a pure validation boundary. Inputs remain unchanged;
return an independent checked declaration or `ValueError` with stable code and
offending host/instance/field. Unknown fields, booleans as integers, invalid
enums/ranges and NaN/Inf must fail. Do not silently allocate or adjust resources.

1. Every global assignment resolves to an explicit profile. Validate the
   server_id/gpu_id mapping against declared host inventory, GPU UUID uniqueness
   per physical host, and host aliases that map to the same physical host.
   GPU0 or identical ring keys on different physical hosts are allowed.
2. Validate each instance's receiver/unpack/GPU/sink CPU lists and worker-count
   consistency. Across instances on the same host, logical CPUs and physical
   `(socket_id, core_id)` must not collide. Use supplied inventory to identify
   SMT siblings and NUMA membership, not numeric CPU assumptions. Control CPUs
   must not consume reserved data-path cores.
3. Check ring-key collisions across all stages/instances on the same host,
   including spelling-normalized numeric keys. Explicitly validate supported
   flow matching fields on the same interface/physical port. A broad wildcard
   flow overlaps a narrower matching flow; distinct labels or source endpoints
   do not prove destination-only receive rules are disjoint. Unknown matching
   capabilities cannot be called ready.
4. Require task-owned paths under a configured feature root, reject lexical
   traversal/outside-root paths and overlapping per-instance run directories.
   Preserve parent/child path collision semantics. Host paths are POSIX strings;
   a pure checker cannot prove remote filesystem symlink safety. Document that
   real path/resource ownership must be verified by later host readiness.
5. Validate declared RAM/VRAM reservation limits against supplied capacity;
   aggregate host RAM reservations across instances and check each GPU limit.
   These are resource reservations, NOT independently entered derived block
   sizes. Actual algorithm/ring memory demand requires weight shape and compiler
   budgets in later tasks: do not guess B, duplicate GPU budget formulas or
   assert that a reservation proves the actual pipeline fits. Make that
   unverified dependency explicit in the returned check status.
6. Validate packet bytes from the known S/F/P/CI8 plus the versioned Project
   header contract and explicit transport/IP overhead against observed/declarative
   MTU. Distinguish L3 MTU from Ethernet frame overhead. Do not guess unknown
   profile/header length or overhead as zero, change S, or modify packet layout.
   Read the existing wire profile contract; unsupported wire profiles must fail
   clearly or remain explicitly unverified, never runtime-ready.
7. The example uses documentation addresses and fake inventory. Preserve all
   requested affinity, NUMA, polling, WR, ring counts, CUDA mode/slots and consumer
   fields without paper baseline fallback. Clearly distinguish static validation
   from fresh host readiness; no output from this pure function proves that a
   real host is currently ready.

Before implementation, summarize the proposed deployment/inventory field shape
and how reservation-vs-compiler-budget and unknown inventory are represented in
the startup report. These are new input structures within this task, not a
reason to change existing product configuration contracts. If the existing
three-argument interface cannot faithfully represent a required check, report
the bounded design gap rather than fake evidence.

## Tests and reporting

Red then green tests must include same-host two-GPU success; two-host GPU0
success; duplicate UUID; logical/SMT conflict; absent CPU/NUMA; duplicate ring
keys; wildcard/narrow flow overlap; safe disjoint flows; host alias conflicts;
MTU exact boundary and oversize; unknown transport facts; aggregate RAM and
VRAM overcommit; path escapes/overlap; missing profile/inventory; input
immutability; and matching schema/example structure. Do not make a real host
query or allocate resources to generate expected values.

```text
python3 -B -m unittest discover -s tests -p 'multiserver_model_test.py' -v
python3 -B -m unittest discover -s tests -p 'multiserver_deployment_test.py' -v
python3 -B tests/generate_source_manifest_test.py -q
python3 -B tests/task8c_profiles_test.py -q
git diff --check
```

Also check new untracked file whitespace with no-index diffs; do not call an
empty tracked diff complete review evidence.

Report startup identity/task title/worktree/HEAD and dependency import results
to coordinator `01a090fd-55d8-7b83-9ea1-1a53eaa1b952` using
send_message_to_thread, verify delivery, and continue the task. On completion or
blocker, deliver scope, changed files, red/green evidence, hashes, limitations and
deferred server pure-test requirements via the same operation. No commit, push,
merge, cherry-pick, stash, worktree deletion or archive. Keep open pending local
review and deferred server acceptance. Do not start Task 3 or retry qths1.
