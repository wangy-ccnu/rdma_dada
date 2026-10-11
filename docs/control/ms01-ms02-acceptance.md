# MS-01/02 validator acceptance — 2026-09-30

Status: ACCEPTED_PURE_PYTHON_ONLY. Static global/deployment validation and
Linux/Python compatibility only; no live deployment, compiler budget or
performance claim. Both implementation tasks are archived; identities and
retained worktrees are in the [task ledger](task-ledger.md).

## Tested identity and evidence

The combined MS-02 checkout `/Users/ywang/.codex/worktrees/ae1b/rdma_dada`
included the reviewed MS-01 dependencies; base920ccbb. The earlier standalone
MS-01 snapshot was not separately executed on the server.
[Combined frozen source](manifests/ms02-source.sha256):294 entries, SHA256
`4069ef814e726fde2958567a261bfe4e71fbe9fb47b4cb747958c6323778cb50`.
HF/qths full verification passed. Original MS-01 manifest is retained as
dependency history, not a separate accepted run.

The user explicitly authorized one complete pass of these deterministic
validators, replacing earlier separate triple-run requests for this stage only:

```sh
/usr/bin/python3 -B -m unittest discover -s tests -p 'multiserver_model_test.py' -v
/usr/bin/python3 -B -m unittest discover -s tests -p 'multiserver_deployment_test.py' -v
```

qths1 Python3.8.10: model24/24 in0.027s and deployment36/36 in0.102s, both exit0.
No CMake, CUDA, NumPy/jsonschema installation, qtp hosts or dataflow was needed.
CLEANUP=PASS; no task processes/rings/capabilities created. All reviewed source
was subsequently integrated; the independent local integration check passed60
tests. Existing algorithms and paper performance results were unchanged.

Authoritative retained
[original command outputs](evidence/ms01-ms02-server-command-output-20260930.json)
include cases, results, exits and source-thread provenance; SHA256
`b160b673c93382d189b6e43d7354b3d0e53ba37e695210d0489e49f21ab9d853`.
Outputs are combined: empty stderr cannot independently be claimed.
No rerun was used to recover these original records.

HF/qths result directory:
`/home/user/wy/task8c-multiserver-results/ms01-ms02-combined-20260930T141850Z/`.
Result file: `ms01-ms02-combined-20260930T141850Z-result.json`, SHA256
`e6cd639af6d54f0e4b27bed200b21f6074754222989c7f179f96eabd64a0ba40`.
Remote logs are compact summaries, not full original unittest output.

## Review conclusions retained for future regressions

All blocking findings were corrected before acceptance: exact time-product
bounds; supported schema patterns/types/bounds; control/data SMT conflicts;
physical-host and NIC-port aliases; same-host path containment and cross-host
path reuse; unsupported wire/IP/protocol rejection; direct/1 CUDA consistency.
WR upper bound uses (T/S)*A, not F: A4/F4 and A4/F3 both accept128/reject129;
A2 rejects65. Tests preserve requested CPU/NUMA/resource policy rather than
silently replacing it. Static claims keep runtime_ready=false.

The September13 no-route-to-host blocker was resolved before this run;
it is not a product failure or an outstanding test. No project runtime was
started during environment diagnosis. Later MS-03–05 and MS-06 acceptances
exercise expanded model/deployment suites; they do not rewrite these counts.

Current interfaces and validation limits are maintained in
[configuration.md](configuration.md). Historical manifests are immutable
snapshot records, not today's source manifest.
