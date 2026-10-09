# MS-04 — Explicit inputs, then portable instance bundles

Coordinator: `多服务器同步开发`, `01a090fd-55d8-7b83-9ea1-1a53eaa1b952`.
Authority: user-approved MS-03/04/05 and subsequent parameter discussion.
Read the entire procedure:
`/Users/ywang/WorkFile/code/rdma_dada/.worktrees/multi-server-observation/docs/agents/delegated-development.md`.
Read the updated Phase 1 plan in that same worktree at
`docs/superpowers/plans/2026-09-12-multi-server-config-implementation.md`,
and the observation design spec. The coordinator copy is authoritative, including
uncommitted refinements; it is not inherited by a fresh child checkout.

## Checkout and current dispatch

Use a new persistent Luna task, isolated worktree from
`codex/multi-server-observation`, commit `5851eaed6cff78179d9cee763c6d4713c1095b08`.
Confirm actual path/HEAD/status. Current dispatch is **MS-04a only**; report and
freeze after its local tests. MS-04b is authorized in principle but starts only
after coordinator delivers reviewed MS-03 dependencies and reviews 4a. Never
copy an unfinished child file on your own.

## MS-04a — Input contract completion

Own only these existing feature files:

- `config/global-observation-v1.schema.json`, `config/global-observation.example.json`
- `config/multiserver-deployment-v1.schema.json`, `config/deployments/multiserver.example.json`
- `scripts/multiserver/model.py`, `scripts/multiserver/deployment.py`
- `tests/multiserver_model_test.py`, `tests/multiserver_deployment_test.py`, `tests/multiserver_fixture.py`

May add `config/deployments/multiserver-inventory.example.json` and
`docs/control/configuration.md`. Read existing Observation v1/schema/compiler
read-only for exact field semantics; do not modify them.

Implement these exact additions:

1. Global top-level `metadata={"telescope":"CA"}`; require a nonempty string,
   preserve it, and reject unknown keys as existing validators do. Telescope is
   configurable, not hardcoded to CA. Retain first-center/spacing model inputs.
   Update example/fixture first center to 1297000000 for F7, spacing1000000.
   Document full-band center formula; do not add a redundant editable center.
2. Each deployment profile gains `receiver={"device":...,"destination_mac":...}`.
   Device must be an explicit nonempty name, MAC a valid six-octet address.
   Destination IP/UDP port continue to come from `flow.match`; no duplicate IP.
3. Each inventory NIC gains `rdma_device`, `verbs_port`, `mac_address`.
   The selected interface must match profile device/MAC and physical_port;
   compare normalized MAC values. Validate integer port and reject anything
   other than 1 with an explicit unsupported-runtime diagnostic. Do not change
   verbs code or claim that a static input proves live host readiness.
4. Each profile gains `storage={"enabled":false,"blocks_per_file":0,"direct_io":false}`.
   For this bounded milestone reject disk-enabled/nonzero/direct-I/O settings
   explicitly; existing single-server storage functionality is unaffected.
5. The checked-in inventory is fake/documentation-only, generated consistently
   with current fixture resource semantics. Keep documentation IPs and locally
   administered example MACs; no qths hardware values as implicit defaults.

Keep the existing expanded profile representation. Do not introduce a generic
template merge engine, host discovery, new dependency, automatic CPU placement
or separate algorithm/byte-budget calculator. User inputs remain authoritative.

Red/green sequence: run the existing 60 model/deployment tests; add failing
tests for every new field and NIC mismatch/unknown device/port2/storage refusal;
implement minimal validators/schema/examples; rerun all model/deployment tests.
Check schema/example/fixture coherence and input immutability. Frequency tests
must cover odd F7 and even F4 at center1.3GHz, exact first centers and local band
centers without binary float rounding. Existing conflict tests remain intact.

Commands (from child root):

```text
python3 -B -m unittest discover -s tests -p 'multiserver_model_test.py' -v
python3 -B -m unittest discover -s tests -p 'multiserver_deployment_test.py' -v
git diff --check
```

## MS-04b — Dependency-gated follow-up

After explicit follow-up, add bundle.py/compile CLI/bundle tests using plan
Task4 and required keyword-only inventory_path. Reuse reviewed slice_weights;
validate full source F/P/A and nonzero B before accepting its slices, without
duplicating the NPY parser. No actual compiler claim until MS-05. Dependency
snapshot and any narrow metadata API extension are reviewed before starting.

## Delivery and boundaries

Return changed files, red/green commands/counts, limitations and server test
requirements to coordinator ID. If the app refuses callback, record
RESULT_NOTIFICATION=FAIL; coordinator will recover via read_thread/wait_threads.
No automatic commit/push/merge/archive, no remote access/tests, no source
manifest refresh during implementation, no root/paper worktree edits.
Local PASS is development evidence only. Coordinator performs correctness and
ponytail review; existing GPU服务器代码测试 owns combined remote acceptance.
