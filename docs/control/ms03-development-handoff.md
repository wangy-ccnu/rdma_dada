# MS-03 — Byte-preserving weight slices

Coordinator: `多服务器同步开发`, `01a090fd-55d8-7b83-9ea1-1a53eaa1b952`.
User authorized MS-03, MS-04 and MS-05 on 2026-09-30. Implement MS-03 only;
MS-04/05 consume its reviewed outputs and are dispatched in dependency order.

## Authority and baseline

Read the complete authoritative procedure at
`/Users/ywang/WorkFile/code/rdma_dada/.worktrees/multi-server-observation/docs/agents/delegated-development.md`.
Its uncommitted identity-checkpoint update is authoritative: SHA manifests are
refreshed at stage/test freezes, not every edit or local test. This is not
permission to commit that rule or other coordinator files from the child.

Use a native isolated task worktree based on `codex/multi-server-observation`
commit `5851eaed6cff78179d9cee763c6d4713c1095b08`. Check cwd, HEAD and status
before editing. MS-01/MS-02 are committed in this base. The coordinator path is
`/Users/ywang/WorkFile/code/rdma_dada/.worktrees/multi-server-observation`;
do not edit it or the root paper checkout from the child.

Read Task 3 and global constraints in
`docs/superpowers/plans/2026-09-12-multi-server-config-implementation.md`, the
complete observation spec, and the existing weight-reader implementations in
`src/config/beamform_weight_metadata.cpp` and
`modules/beamform/beamform_module.cpp` before implementation.
The old plan's initial base revision is historical; the explicit base above
is the authorized implementation baseline.

## Scope and interface

Allowed edits:

- Add `scripts/multiserver/weights.py`.
- Add `tests/multiserver_weights_test.py`.
- Extend `tests/multiserver_fixture.py` with an independent deterministic
  `write_weight_fixture(path, shape, dtype)` helper returning raw payload bytes.

Implement `slice_weights(source: Path, destination: Path, first_f: int,
nchan: int) -> dict`, returning shape, dtype, source_sha256, local_sha256,
first_f and nchan. This API's artifact hashes are part of its function, not
the development-source manifest refresh policy.

Support NPY v1/v2, C-contiguous five-dimensional `[F,P,A,B,2]`, dtype `|i1`
or `<i2`. Validate magic/version/header bounds (1 MiB), dictionary structure,
positive integer dimensions excluding booleans, last axis=2, exact payload
length and bounded slice indices. Reject unsupported layouts/dtypes, malformed
literal syntax, truncation and trailing bytes with stable ValueError codes.
Use bounded standard-library parsing, never eval or NumPy. Chunk-copy raw
payload; no full matrix expansion, conversion, normalization or weight scaling.
Preserve all P/A/B order and negative values. Emit deterministic valid padding.

Keep source unchanged and refuse overwriting an existing destination, including
source aliases. Preserve pre-existing files on failure; clean only new owned
partial output. Filesystem write errors must not leave a purported complete
slice. Shape expectations supplied by a future bundle are checked in MS-04;
MS-03 validates the NPY structural contract, not unrelated observation inputs.

## Tests and workflow

Use the plan's red/green workflow. First run the 60 existing multiserver tests
as baseline; then prove a new failing slicing test before implementing it.
Pure test command: `python3 -B -m unittest discover -s tests -p
'multiserver_weights_test.py' -v`. Combined regression uses
`python3 -B -m unittest discover -s tests -p 'multiserver_*test.py' -q`.

Cover both NPY versions and dtypes; F7 split4+3 and reconstruction; nonzero
slice offsets; signed int16 values; unchanged source; deterministic output;
malformed headers, unsupported format, invalid/bool slice dimensions,
payload truncation/trailing bytes, safe destination failures, and bounded I/O.
Expected bytes must come from independent fixture logic, not the slicer.
Python3.8 standard library only; no package installs. Real C++ loader acceptance
is MS-05, not a reason to change existing readers now.

Do not change algorithms, existing Observation schema, CMake, CLI, bundle or
compiler modules, accepted-results indexes or manifests. No remote commands,
source integration, commit/push/merge, or worktree deletion. Report a required
public-contract change before implementing it.

## Delivery and acceptance

Send startup cwd/base identity and final report to the coordinator task ID via
send_message_to_thread; verify delivery, retry once if necessary and report
notification failure independently of test outcome. Startup handshakes do not
end the implementation turn. Final report: changed files, tests/counts,
limitations, exact paths and server pure-test requirements. Freeze the completed
files for review; source identity is captured at that stage, not every edit.

Coordinator owns independent review. Existing `GPU服务器代码测试` owns any remote
acceptance. Local PASS is development evidence only. Keep the task open until
review/server acceptance, then decide archive under the authoritative procedure.
