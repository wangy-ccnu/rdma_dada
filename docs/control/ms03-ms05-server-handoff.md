# MS-03–05 offline server acceptance

Coordinator: `01a090fd-55d8-7b83-9ea1-1a53eaa1b952`.
Executor: GPU服务器代码测试, `019fbb68-ae6f-77f0-bfdd-30bc30bd6c45`.
Procedure: `/Users/ywang/WorkFile/code/rdma_dada/.worktrees/multi-server-observation/docs/agents/delegated-development.md`.

## Frozen inputs and isolation

Source: `/Users/ywang/.codex/worktrees/179b/rdma_dada`, approved feature base
`5851eaed`, reviewed uncommitted MS-03–05 delivery. Manifest:
`config/testing/multiserver-source-manifest.sha256`,312 entries, SHA256
`fa0483687947d874146dc276afe69eadf4d2ec241b4cd372ca90a27d09ca7553`.
Verify source identity before synchronizing the complete tree local→HF→qths1
into `/home/user/wy/rdma_dada-multiserver`. Preserve paper canonical source,
builds/results, qtp hosts and unrelated runtime files. Precise runtime exclusions
must not omit manifest-listed docs/results or tests/data. Never assemble sources
by per-file remote edits. Phase0 facts: `ms03-ms05-phase0.md`.

## Commands and gates

Use fixed `/home/user/wy/tools/cmake-3.31.12/bin/cmake` and sibling ctest.
Choose a fresh absent build `/home/user/wy/build-multiserver-config-20261008`
(if occupied, record a fresh distinct feature build instead of overwriting it).
Configure Release, BUILD_TESTING=ON, BUILD_RDMA_PIPELINE=OFF, USE_CUDA=OFF.
Build these targets only:
`observation_config_compile observation_config_test resolved_observation_plan_test observation_artifacts_test beamform_weight_metadata_test`.
Record compiler binary identity and ldd resolution.

Inspect CTest registration, then execute this exact bounded selection with
`--repeat until-fail:3 --output-on-failure` (no duplicate direct triple loops):

```text
^(multiserver_model_test|multiserver_deployment_test|multiserver_weights_test|multiserver_bundle_test|multiserver_compiler_unit_test|multiserver_compile_test|observation_config_test|resolved_observation_plan_test|observation_artifacts_test|beamform_weight_metadata_test|observation_config_compile_test)$
```

Require11 registered suites, each three clean passes. New Python pure suites
contain26/43/21/25/18 cases respectively; real compiler suite has12 cases.
Use registered actual compiler argv; an integration skip is not a pass.
Cover Linux atomic no-replace publication, CPU/GPU/resource conflicts,
frequency/Station ordering, NPY v1/v2 slicing, strict JSON, immutable inputs,
portable bundles, Power/coherency outputs, partial EOD, invalid contracts and
post-publication cleanup warning/evidence failures. Current CI8 compiler must
reject int16 weights; this negative test does not imply full int16 support.

## Evidence and boundaries

Use existing versioned repository tests, not a new ad-hoc acceptance runner.
Retain compact machine-readable stage/test results and relevant CTest logs in a
unique subdirectory of `/home/user/wy/task8c-multiserver-results`; record purpose,
origin task, source/build/binary identity, versions, argv, exit statuses, actual
test counts, first failure and cleanup. No remote Git or product edits.
No rings, PSRDADA, sender, RDMA, CUDA or network data flow; do not run unrelated
loopback tests. Scoped residue check must leave unrelated gdm IPC untouched.
Return results to coordinator. If callback is denied, retain full report in the
task with RESULT_NOTIFICATION=FAIL; coordinator will retrieve it, not confuse
delivery failure with test outcome. No commit/push/merge/archive authorized.
