# MS-04b — Portable per-instance bundle follow-up

Coordinator `01a090fd-55d8-7b83-9ea1-1a53eaa1b952`; existing child
`01a11b57-b539-7d80-86e4-0c72b4892a29`.
MS-04a corrections passed coordinator review (69 local tests), 2026-10-08.
This handoff becomes executable when delivered by the coordinator follow-up.
Read the complete authoritative procedure:
`/Users/ywang/WorkFile/code/rdma_dada/.worktrees/multi-server-observation/docs/agents/delegated-development.md`.
Plan/spec are in that coordinator worktree; implement Task4 with its approved
inventory refinement. Preserve the existing child worktree's reviewed 4a files.

## Explicit reviewed dependency transfer

MS-03 source is frozen at `/Users/ywang/.codex/worktrees/e338/rdma_dada`.
Local review PASS; remote acceptance pending, not implied by this dependency.
Verify before importing with apply_patch into your own child checkout:

```text
b02f1e0f9813c6f1b1ba007f0d4406e8631efc7bf935cb47335d2838d424ba24 scripts/multiserver/weights.py
8938621cf3e3da5c6461c31bbf23de32f34f186007a7452e3767f81049eb8608 tests/multiserver_weights_test.py
f0fe55a24f738553ee7c92f73771563c2e6a7ef0787d8e438e0da49b0595fec7 tests/multiserver_fixture.py
```

The first two files are new: import unchanged. For the fixture, import only
`import struct` and `write_weight_fixture`; preserve your 4a global/deployment/
inventory helpers. Verify imported helper equality and run all combined tests
before implementation. Keep source child/coordinator/paper worktrees untouched.

## Ownership and minimal interfaces

Add `scripts/multiserver/bundle.py`, `scripts/multiserver_compile.py`,
`tests/multiserver_bundle_test.py`; extend `tests/multiserver_fixture.py` and
`docs/control/configuration.md`. A narrow permitted dependency extension is
adding `source_shape` to slice_weights return metadata and its test; this lets
bundle code verify full F/P/A and infer B without a duplicate NPY parser.
Keep all existing slice semantics and tests. Other 4a files stay frozen unless
a concrete integration defect is reported first. No CMake/compiler adapter yet.

`build_bundle(global_path: Path, deployment_path: Path, output: Path, *,
inventory_path: Path) -> dict`; CLI `compile --global FILE --deployments FILE
--inventory FILE --output DIR`. No resolve subcommand until MS-05.
`write_example_inputs(root)` returns three input paths, writes deterministic
tiny weights and a real copied wire profile, and uses documentation-only hosts.

Output: bundle.json, normalized input JSONs (including inventory),
`instances/<server_id>/gpu-<gpu_id>/{observation.json,weights.npy,wire-profile.json}`,
and MANIFEST.sha256. Retain expanded deployment/monitoring policy separately
from old Observation JSON; do not lose CPU roles or claim they are runtime-applied.

## Compatibility and identity

- Map existing Observation fields only: metadata telescope/local center/local
  bandwidth, common time/Station/P, local channel selection, T/S groups, rings,
  disabled storage, receiver device/MAC/destination, CUDA device/mode/slots,
  conversion, beamform -> power or stokes -> integrate. Keep relative resource
  references and existing module names. No header rewriting or algorithm edits.
- Existing compiler output-ring count follows compute_ring_blocks
  (`resolved_observation_plan.cpp`). Reject differing profile output/compute
  counts explicitly; do not silently ignore the output count or change C++.
- Existing receiver is destination-only. Require concrete destination IPv4 and
  UDP port; reject non-wildcard source filters that cannot be represented in
  current runtime. Preserve wildcard declarations in sidecar, not old JSON.
- Resolve wire/weights relative to their input configuration; require supported
  readable resources. Do not infer a missing file from a paper fixture. Copy
  wire bytes unchanged; local F is conveyed by Observation, not editing wire.
- Reject full weight F/P/A mismatch and invalid B before final bundle publication.
  Record full-source hash/shape plus local F offset/shape/hash; Station/P/B order
  remains unchanged. B comes from the supplied matrix, not a hardcoded value.
- Bundle identity uses canonical inputs, resource content hashes and generator
  source identity, not output paths/timestamps or self-referential manifest hash.
  Record target compiler contract, not a fabricated compiler-binary identity;
  actual compiler identity is recorded by MS-05 resolve. Stable object key order,
  preserved axis/list order, no NaN/Inf or duplicate JSON keys.
- Publish only after complete success using task-owned staging. Refuse symlink/
  traversal identifiers and preexisting nonempty destination. Failure on a later
  instance must not expose a partial bundle or delete unrelated files.
- `static_valid` is not runtime readiness. Carry unverified prerequisites from
  deployment validation; real byte/budget/compiler compatibility is MS-05.

## Red/green and delivery

Add tests for two output roots producing identical bytes/IDs; changed weights/
wire/profile identity; F4/F3 and same-host GPU0/GPU1; full-shape mismatch;
unsupported output-count/source-filter mapping; unsafe paths and symlinks;
second-instance failure and no-clobber publication; invalid JSON and CLI errors.
Check tail center1302000000 Hz and global1.3GHz exactness in generated JSON.

Run `python3 -B -m unittest discover -s tests -p 'multiserver_*test.py' -q`,
`git diff --check`, and versioned CLI tests. Do not call a fake compiler PASS
integration acceptance. Return file list, test evidence, limitations and server
requirements to coordinator, then freeze for correctness/ponytail review.
No remote execution, commit/push/merge/archive or per-edit manifest refresh.
