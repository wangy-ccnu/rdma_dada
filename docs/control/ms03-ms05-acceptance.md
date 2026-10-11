# MS-03–05 configuration acceptance — 2026-10-08

Status: ACCEPTED_OFFLINE_CONFIGURATION. Runtime/data-flow acceptance is outside
this phase. Coordinator `01a090fd-55d8-7b83-9ea1-1a53eaa1b952` independently
reviewed the implementation and retrieved server command outputs after callback
delivery was refused.

## Accepted scope

- MS-03: deterministic, byte-preserving F-axis NPY weight slices.
- MS-04: explicit inventory/resource validation and portable per-GPU bundles;
  common sample time grid with channel-dependent byte geometry, CA metadata,
  fixed test weights and disabled storage.
- MS-05: explicit existing compiler invocation from installed instance paths,
  artifact/report/identity verification, exclusive publication and structured
  cleanup-failure evidence. Existing C++ compiler and algorithms are unchanged.

Current CI8 input accepts matching int8 weights. Int16 slicing/metadata is
supported, but full CI8 compiler resolution must reject int16 without conversion.
Examples are non-runnable fixtures, not actual server resource assignments.

## Evidence

Frozen source: `/Users/ywang/.codex/worktrees/179b/rdma_dada`, feature base
`5851eaed6cff78179d9cee763c6d4713c1095b08`.
Source manifest312 entries, SHA256
`fa0483687947d874146dc276afe69eadf4d2ec241b4cd372ca90a27d09ca7553`.
Historical tested manifest: `manifests/ms03-ms05-tested-source.sha256`; it records
the frozen child tree, not later coordinator documentation edits.

Local independent checks:133 pure cases,12 actual compiler integration cases
and11 registered suites PASS. Discovery reports145 with12 skips because those
integration cases run separately with an explicit binary. Post-integration
feature-worktree check:20 delivered files byte-identical to the tested child,
133 pure cases PASS, git diff --check PASS.

qths1 fresh build: `/home/user/wy/build-multiserver-config-20261008`, Release,
GCC9.4.0, Python3.8.10, fixed CMake/CTest3.31.12; CUDA/RDMA disabled.
HF/qths source `/home/user/wy/rdma_dada-multiserver`; both independently verified
manifest identity and all312 entries with exit0. Compiler binary SHA:
`2e4307fa3e9ab9adbdf6bc9b6902623738123539633e5d5fd56c8a68a243a7ac`.
Compiler dynamic dependencies resolved. All11 required CTest suites passed
three consecutive repetitions, including actual compiler integration.

Remote result namespace on HF/qths:
`/home/user/wy/task8c-multiserver-results/ms03-ms05-combined-20261008T131716Z`.
Original result SHA:
`0d892d7206975bf48a7a429496bf5917f7d2f1d4f89f9eb958429d66c1bc8d5a`.

Local durable interfaces:

- `evidence/ms03-ms05-20261008-server-result.json`: exact executor result copy.
- `evidence/ms03-ms05-20261008-ctest.json`: all11 original command outputs,
  command IDs and retrieval provenance, independently checked by coordinator.
- [Configuration/API and local commands](configuration.md).
- Environment, corrected findings and server reproduction selection below.

Remote LastTest.log was overwritten by separate CTest invocations and proves
only its final selection. For all11 suites use the preserved per-command JSON.
No test was rerun to repair that retention gap. Notification FAIL is separate
from tests; coordinator retrieval completed the acceptance.

No rings, sender, receiver, GPU or network data flow was started. No task
process/IPC remained; unrelated gdm segments were preserved. An initial complex
SSH regex listing error was corrected by selecting plain test names separately;
no product change was required. Synchronization used no deletion after dry-run
found no stale deletion targets.

Final HF→qths complete-tree dry-run after synchronization returned exit0 with
no differences or proposed deletion. The complete per-command CTest evidence
was additionally retained remotely as `ctest-complete-output.json`, SHA256
`3a98a9ed6881ecc89d7976da1c92829fc6885777a651ab5b5e953c508d07a1c2`.
No tests were repeated and original result.json was not edited.

## Integration and next boundary

Reviewed files were committed as `82ac66f` on
`codex/multi-server-observation`, following the user's2026-10-09 instruction.
The paper worktree remains untouched; no push or Git merge was performed.
MS-03/04/05 conversations were archived on2026-10-09 at the user's request;
the coordinator verified all three IDs in the app's archived-thread listing.
Their source worktrees and server artifacts remain retained.

This establishes configuration preparation, not centralized remote start/stop,
live progress/rate monitoring, FPGA control or deployment readiness. Those are
later scoped work; no control-system runtime is claimed complete.

Commit preparation, 2026-10-09: removed one trailing blank line from
`config/deployments/multiserver-inventory.example.json` to pass staged whitespace
checks. JSON content is unchanged; the tested-source manifest above remains a
historical snapshot and is not rewritten to conceal this formatting-only delta.

## Retained review decisions and reproduction

All blocking review findings were corrected before acceptance:

- MS-03 rejects duplicate NPY keys, accepts legacy 16-byte-aligned v2 input,
  checks bounded multi-chunk I/O, closes the source before publication and
  reports owned-file cleanup failures. A nonblocking duplicate-close-handler
  simplification was advisory, not an unresolved correctness failure.
- MS-04 validates supported verbs ports only for selected NICs; unused positive
  port numbers remain valid inventory. Tail-channel centers and immutable
  axis order are covered. Bundle review found no remaining defects.
- MS-05 preserves successful publication on staging-cleanup failure and records
  the warning; failure to persist it is a structured error with
  `published=true`, not a false rollback. Real-compiler regressions cover both.
  See configuration.md for the exact API/error contract and CI8/int16 limit.

Historical Phase0 (2026-10-08): HF=qtrelay0 and qths1 reachable as user uid1000,
Python3.8.10 on both; qths CMake/CTest3.31.12, GCC/G++9.4.0, ld2.34/glibc2.31.
Feature roots were user-owned. Python/compiler dependencies resolved. NIC MTU
was9000 on ens2np0 and1500 on enp4s0; exact link speed and clock synchronization
were not established or needed for this offline gate. No runtime-readiness
claim follows; recheck relevant environment facts before future remote work.

Historical build targets:
`observation_config_compile observation_config_test resolved_observation_plan_test observation_artifacts_test beamform_weight_metadata_test`.
Configure Release with BUILD_TESTING=ON, BUILD_RDMA_PIPELINE=OFF, USE_CUDA=OFF.
Use the fixed qths CMake/CTest3.31.12 pair and a fresh recorded build directory;
never overwrite the accepted build. The equivalent combined reproduction filter is:

```sh
/home/user/wy/tools/cmake-3.31.12/bin/ctest --test-dir BUILD \
  --repeat until-fail:3 --no-tests=error -V --output-on-failure \
  -R '^(multiserver_model_test|multiserver_deployment_test|multiserver_weights_test|multiserver_bundle_test|multiserver_compiler_unit_test|multiserver_compile_test|observation_config_test|resolved_observation_plan_test|observation_artifacts_test|beamform_weight_metadata_test|observation_config_compile_test)$'
```

BUILD is a verified build-path placeholder, not a default. Original execution
selected the11 suites separately; preserved JSON records those actual commands.
Five pure suites contain26/43/21/25/18 cases; integration has12 actual compiler
cases, no skips. Current test counts may evolve; use the frozen source for
historical reproduction. Server execution remains owned by the testing task.

Local implementation evidence is retained under
`/Users/ywang/.codex/worktrees/179b/rdma_dada/build-ms05/evidence`
(including `review-p2/`, the delivered-file manifest, patch, real CLI output
and resolved F4/F3 artifacts). That child's extra full portable CTest run
reported53/54 with a sandbox-denied localhost loopback bind; it is not part
of the11-suite configuration acceptance and was not claimed PASS.
