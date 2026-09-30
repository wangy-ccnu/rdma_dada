# MS-01 — Global input validation server acceptance

Historical handoff: superseded on 2026-09-30 by
`ms02-server-test-handoff.md`, which authorizes one combined 24+36 test pass.
Retain this file for the original snapshot and isolation protocol; do not run
its old three-round command group as an additional acceptance.

Coordinator: `多服务器同步开发`, `01a090fd-55d8-7b83-9ea1-1a53eaa1b952`.
Implementation task: `MS-01 全局观测配置校验`,
`01a099b5-6f39-7400-8dba-deb0f8ad1ef7`.
Procedure: `docs/agents/delegated-development.md`.

## Frozen source and scope

- Source: `/Users/ywang/.codex/worktrees/60bd/rdma_dada`.
- Detached base: `920ccbb458845badb1246743d95cebd174a4d01d`, derived from
  `codex/multi-server-observation`, plus the six reviewed untracked additions.
- Full source manifest: `docs/control/manifests/ms01-source.sha256` in the
  coordinator feature worktree, not in the child source tree.
- Manifest SHA256: `a3897987c5d113c1496a439a71c930a7364578cbe1523e60937b4bd90c0915e1`.
- Manifest entries: 290; all verified locally. This is the authority for this
  acceptance, not the older paper manifest that is included as a source file.
- Model SHA256: `902b5fc47aae6469dfed444a7832de280ab9fc090581ec35d1649503408c6d7a`.
- Tests SHA256: `b8f7d1337c254acd4e019ac284a657efdbf599d7fe92ff2ff78041a6224c5749`.

Only global schema/example, `scripts/multiserver/{__init__,model}.py` and
`tests/multiserver_{fixture,model_test}.py` were added. No deployment validator,
compiler adapter, weights slicer, algorithm, CMake registration or runtime
control implementation is included. Coordinator review is PASS; local tests
24/24, 2/2 and 12/12 passed. Server acceptance remains pending.

## Test-host work

1. Read-only Phase 0 on HF and qths1: confirm Python executable/version, import
   behavior, permissions, source/evidence namespaces, capacity and existing task
   ownership. Record relevant tool/library, topology and clock facts without
   changing environment. This task requires Python 3.8+ standard library only;
   unavailable CUDA tools do not turn a pure Python gate into a GPU dependency.
2. Verify the proposed feature source root `/home/user/wy/rdma_dada-multiserver`
   is unused or explicitly owned by this feature. Never overwrite the paper
   source `/home/user/wy/rdma_dada`, its builds or results. Record ownership for
   newly created directories. On collision report before modifying anything.
3. Incrementally mirror the complete frozen worktree local -> HF -> qths1, with
   precise runtime exclusions. Preserve versioned `tests/data`, `docs/results`
   and all 290 manifest entries. Keep the manifest alongside the evidence and
   verify all entries from the correct source root. No archive/rN source trees,
   ad-hoc selected-code copies, Git operations or qtp access are required.
4. On qths1, from the verified source root, use the observed Python path for
   each command below, repeating the complete pure-test group three times.

```text
python3 -B -m unittest discover -s tests -p 'multiserver_model_test.py' -v
python3 -B tests/generate_source_manifest_test.py -q
python3 -B tests/task8c_profiles_test.py -q
```

Expect 24, 2 and 12 tests respectively, exit 0 on every repetition. The model
test includes recursive schema/example fields, exact time arithmetic, T*d
overflow rejection, allocation coverage/overlap, duplicate instance IDs,
positive COHERENCY/P2/SUM/TF32 and input immutability. No installed jsonschema
package or actual weight file is required. The checked-in example is a model
fixture, not a remotely runnable observation.

## Evidence and acceptance

Keep compact command results and stdout/stderr under a fresh run-specific child
of `/home/user/wy/task8c-multiserver-results/`; identify this task, coordinator,
purpose and retention in its ledger. Retain manifest, source base and file SHAs,
host/Python identity, per-repetition argv/exit/test count, first failure and
cleanup status in a machine-readable result. No temporary script replaces the
three versioned repository test runners as the authority.

CMake/CTest registration belongs to a later task: do not require nonexistent
multiserver CTest targets, build unrelated binaries or edit source to add them.
No source changes, installations, rings, capabilities, GPU workloads, sender,
receiver, unpack or network data flow are authorized by this acceptance.
Post-check no test-owned resources were created; preserve unknown resources.

Report each phase as PASS/FAIL/NOT_APPLICABLE. Failures in environment/sync or
test invocation remain separate from product behavior. Send the complete
report to coordinator `01a090fd-55d8-7b83-9ea1-1a53eaa1b952` and verify delivery;
do not default to the older module-development task. Retain original evidence.
