# Multi-server Configuration Implementation Plan — Phase 1

> **For agentic workers:** The coordinator dispatches bounded work to persistent Luna conversations under `docs/agents/delegated-development.md`. Each child uses superpowers:executing-plans for its approved scope and runs red/green checks task by task. Do not automatically commit/push or modify the paper branch.

**Goal:** Compile user frequency assignments and a full weight matrix into deterministic per-GPU bundles compatible with the existing Observation compiler.

**Architecture:** A pure Python validation layer accepts the global observation plus explicit deployment profiles. A byte-preserving weight slicer and bundle generator produce existing single-instance Observation JSON and local resource files. An adapter invokes the existing C++ compiler to validate/resolve a bundle at its actual local path; runtime ownership and remote transport are outside this phase.

**Tech Stack:** Python 3.8-compatible standard library; existing C++11 `observation_config_compile`, JSON schema documentation and unittest/CTest. No NumPy, web framework, SSH, GPU or RDMA dependency for the pure tests.

**Spec:** `docs/superpowers/specs/2026-09-12-multi-server-observation-design.md`.

## Global Constraints

- Work only in `/Users/ywang/WorkFile/code/rdma_dada/.worktrees/multi-server-observation`, branch `codex/multi-server-observation`, starting at `febdc0ee4587d5c8bd3b9f8466bbc954e1204389`.
- Preserve the parent paper branch and its uncommitted numerical-suite work. Do not merge the GPU-only branch.
- Do not edit current Observation v1 schema, existing fixture JSON, algorithm code or accepted paper indexes in this phase.
- One pipeline per `server_id + gpu_id`; common S/T/d/K; user-specified equal/one-extra-channel allocation. Derived byte quantities are not separate user input.
- All configured Stations are required and retain their ordered A-axis mapping. All instances use the same global observation/run time grid.
- Weights remain unchanged during an observation. Support the currently accepted NPY v1/v2 C-contiguous `|i1` and `<i2`, shape `[F,P,A,B,2]`; no dtype conversion or invented float-weight support.
- Local checks are development evidence only. Completed functionality is handed off to `GPU服务器代码测试`; remote design/execution depends on the Phase 0 gate in the roadmap.
- Source schema identifiers in this new feature must not rename or rewrite existing paper configuration IDs.

## Files and interfaces

All paths below are relative to this feature worktree.

| File | Responsibility |
|---|---|
| `config/global-observation-v1.schema.json` | Global science/time/allocation input contract |
| `config/multiserver-deployment-v1.schema.json` | Per-instance resource profiles, no passwords/private keys |
| `config/global-observation.example.json` | Seven-channel/four-Station, 4+3 assignment example |
| `config/deployments/multiserver.example.json` | Explicit non-runnable example profiles with documentation/test-network addresses |
| `scripts/multiserver/__init__.py` | Package marker |
| `scripts/multiserver/model.py` | Validate global shape, time and channel mapping |
| `scripts/multiserver/deployment.py` | Validate deployment constraints and resource collisions |
| `scripts/multiserver/weights.py` | Bounded NPY header parsing and byte-exact F-axis slicing |
| `scripts/multiserver/bundle.py` | Produce portable files plus immutable manifest |
| `scripts/multiserver/compiler.py` | Explicit-argv existing compiler adapter, no remote execution |
| `scripts/multiserver_compile.py` | Local `compile`/`resolve` command interface |
| `tests/multiserver_fixture.py` | Tiny deterministic global/profile/weights/inventory fixture builders |
| `tests/multiserver_model_test.py` | Model and geometry input rejection |
| `tests/multiserver_deployment_test.py` | CPU/GPU/ring/flow/resource conflicts |
| `tests/multiserver_weights_test.py` | Shape, exact byte order and unsafe format rejection |
| `tests/multiserver_bundle_test.py` | Deterministic portable bundles, identity and path safety |
| `tests/multiserver_compile_test.py` | Real existing compiler compatibility; binary path is mandatory |
| `CMakeLists.txt` | Register new tests/explicit compiler argument only; preserve existing targets |

The planned pure function signatures are:

```text
validate_global(document: dict) -> dict
validate_deployment(global_plan: dict, profiles: dict, inventory: dict) -> dict
slice_weights(source: Path, destination: Path, first_f: int, nchan: int) -> dict
build_bundle(global_path: Path, deployment_path: Path, output: Path) -> dict
resolve_instance(instance_directory: Path, compiler: Path, output: Path) -> dict
```

Invalid input raises `ValueError` containing a stable error code and offending instance/field, not a partially valid plan. CLI converts this to structured failure JSON and nonzero exit. Unknown fields and contradictory independently entered derived byte sizes are rejected.

Fixture contract in `tests/multiserver_fixture.py`:

- `global_example()` returns a fresh dict for A4/F7/P1/B2, Stations 1000–1003, channels 100–106, d=1,000,000 ps, S=512, T=16,384, K=128 MEAN, duration `1.048576`, allocations node-a/gpu0 F4 and node-b/gpu0 F3.
- `deployment_example()` returns independent receiver/worker/GPU/sink CPU lists, CUDA mode/slots, rings/counts, output consumer and receiver endpoint values; examples are not authorized remote launch configs.
- `inventory_example()` returns fake CPU socket/core/logical maps, GPU UUIDs and capacity/MTU constraints for tests. Real inventory is a later Phase 0 input, not a hardcoded test map used in production.
- `write_example_inputs(root)` writes the example JSON resources and NPY and returns `(global_path, deployment_path)`.
- `write_weight_fixture(path, shape, dtype)` writes NPY with channel-distinct signed integer values; it returns the full raw payload bytes for independent byte-slice assertions. Do not reuse the production slicer to generate expected sliced bytes.

The checked-in example becomes a precise expanded form of the spec's conceptual resource references: a weight object contains relative `path`, `order=FPAB2`, `weights_id`, `weights_scale` and `compute_mode`; the common processing section retains the explicit conversion/product/integration settings. The deployment document supplies metadata, storage, ring and execution fields required by current Observation v1. Missing required profile fields fail, not silently inherit a test-runner default.

## Task 1 — Validate global observation and time/frequency assignment

**Files:** model/schema/example and `multiserver_fixture.py`, `multiserver_model_test.py` from the table.

**Consumes:** a JSON dictionary with explicit global and allocation fields. **Produces:** canonical validated input, preserving Station order, with assignments identified by `(server_id,gpu_id)`; no runtime IDs or actual throughput.

- [ ] Write the fixture builder and initial red unittest (run with repo root on `sys.path`):

```python
import copy
import unittest
from scripts.multiserver.model import validate_global
from multiserver_fixture import global_example

class GlobalPlanTest(unittest.TestCase):
    def test_station_order_and_common_time_are_preserved(self):
        source = global_example()
        before = copy.deepcopy(source)
        plan = validate_global(source)
        self.assertEqual(source, before)
        self.assertEqual(plan['observation']['station_ids'], [1000,1001,1002,1003])
        self.assertEqual(plan['time_grid']['samples_per_block'], 16384)
        self.assertEqual([a['nchan'] for a in plan['assignments']], [4,3])

    def test_overlapping_assignment_is_rejected(self):
        source = global_example()
        source['assignments'][1]['first_channel_id'] = 103
        with self.assertRaisesRegex(ValueError, 'CHANNEL_OVERLAP'):
            validate_global(source)
```

- [ ] Run `python3 -B -m unittest discover -s tests -p 'multiserver_model_test.py' -v`; initial failure must be the missing new module/function, not a dependency/environment failure.
- [ ] Implement validation: integer fields excluding booleans; duplicate Stations/instance IDs; channel coverage/overlap; contiguous ranges and current VDIF per-instance F<=255; same d/S/T/K; T%S and T%K; positive exact duration and whole group count. Use Decimal or integer decimal parsing, never binary float time arithmetic.
- [ ] Validate physical channel metadata independently of sampling interval. Local band center derives from first center plus `(F-1)*spacing/2`; reject a nonintegral-Hz result that cannot be represented by the current single-instance metadata rather than silently rounding. First version describes contiguous equal-width channels; do not invent nonuniform frequency metadata.
- [ ] Add negative tests for missing coverage, two assignments to one GPU, F=0/256, ID overflow, unequal-by-more-than-one allocations, K=127, T not divisible by S, sub-group duration and unknown keys. Do not silently clamp inputs.
- [ ] Run the full model tests green and verify the new schema/example encode the same accepted fields; model validation is the authoritative runtime check, not a claim that a JSON-schema package is installed.
- [ ] After combined Task 2 server unit acceptance, request approval for commit group 1; no automatic commit.

## Task 2 — Validate deployment and same-host isolation

**Files:** deployment/schema/profile example and `multiserver_deployment_test.py`; extend only the new test fixture helper.

**Consumes:** validated global config, explicit profiles and observed inventory. **Produces:** validated claims by host, retaining requested CPU/NUMA/GPU/flow values. Compiler-only operation may check static declarations; runtime readiness additionally requires fresh observed inventory.

- [ ] Write red tests for distinct numeric GPU IDs on different hosts, duplicate UUID on the same host, ring-key conflicts across all stage keys, shared physical cores through SMT siblings and aggregate memory overflow.

```python
import unittest
from scripts.multiserver.model import validate_global
from scripts.multiserver.deployment import validate_deployment
from multiserver_fixture import global_example, deployment_example, inventory_example

class DeploymentTest(unittest.TestCase):
    def test_gpu_zero_on_different_hosts_is_not_a_collision(self):
        checked = validate_deployment(validate_global(global_example()),
                                      deployment_example(), inventory_example())
        self.assertEqual(len(checked['instances']), 2)
```

- [ ] Run `python3 -B -m unittest discover -s tests -p 'multiserver_deployment_test.py' -v` red before implementation.
- [ ] Implement claims by physical host and GPU UUID. CPU identity uses `(socket_id, core_id)` from inventory; same logical or physical core across pipeline instances fails. Control/reporting CPU budgets are separate from reserved data-path cores.
- [ ] Check receive-flow overlap using the actual matching fields, not just string inequality of profile names. The global compiler must not invent NIC flow-rule support; unsupported or unknown mapping stays unready until host verification.
- [ ] Validate NUMA membership, per-GPU VRAM and host aggregate RAM against declared/observed limits. Packet byte length includes current 32-byte Project header and protocol/IP/UDP overhead for MTU checks. Unknown transport overhead is an unverified prerequisite, not guessed zero.
- [ ] Ensure all claimed runtime paths are within a configured feature run root and do not overlap paper paths. Identical SysV keys on different physical hosts are allowed; collisions within a host are rejected.
- [ ] Run model+deployment tests green, then hand off pure tests for server verification. No rings or network flows are required to accept these validators.

## Task 3 — Slice weights without changing their values

**Files:** `weights.py`, `multiserver_weights_test.py`, deterministic fixture helper.

**Consumes:** full NPY and zero-based F-axis slice. **Produces:** local NPY plus `{shape,dtype,source_sha256,local_sha256,first_f,nchan}`. Assignment first_f is derived from global channel IDs, not the host index.

- [ ] Write a red byte-order test:

```python
import pathlib
import tempfile
import unittest
from scripts.multiserver.weights import slice_weights
from multiserver_fixture import write_weight_fixture

class WeightSliceTest(unittest.TestCase):
    def test_tail_three_channels_preserve_signed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source, target = root/'global.npy', root/'local.npy'
            payload = write_weight_fixture(source, (7,1,4,2,2), '|i1')
            evidence = slice_weights(source, target, 4, 3)
            self.assertEqual(evidence['shape'], [3,1,4,2,2])
            self.assertEqual(evidence['dtype'], '|i1')
            expected = payload[4*1*4*2*2:7*1*4*2*2]
            self.assertEqual(target.read_bytes()[-len(expected):], expected)
```

- [ ] Run `python3 -B -m unittest discover -s tests -p 'multiserver_weights_test.py' -v` red.
- [ ] Implement bounded standard-library parsing (`ast.literal_eval`, never `eval`), validating NPY magic/version, header length <=1 MiB, dictionary fields, C-order, positive five-axis shape, dtype and exact payload length. Match the existing C++ reader's accepted formats; reject object dtype, Fortran layout, unsupported version, trailing/truncated bytes and out-of-range slices.
- [ ] F is the outermost contiguous axis: derive byte offset/count and copy bounded chunks without expanding the full matrix into Python objects. Emit deterministic NPY header/padding and preserve raw payload bytes; no normalization or scale application during slicing.
- [ ] Add int16 tests, channel-distinct patterns, unchanged-source-SHA assertions and shape-mismatch cases. Recombining local payload slices in global channel order must equal original payload, including negative values.
- [ ] Re-read local weights with the existing compiler in Task 5, so pure Python validation does not become a second untested weight contract.

## Task 4 — Generate portable per-instance bundles and identities

**Files:** `bundle.py`, `multiserver_compile.py`, `multiserver_bundle_test.py`; document new CLI in `docs/control/configuration.md`.

**Consumes:** global/profile paths and an empty/new output directory. **Produces:** `bundle.json`, normalized global/profile copies, and `instances/<server_id>/gpu-<gpu_id>/observation.json`, `weights.npy`, `wire-profile.json`; one immutable SHA256 manifest. No program is started.

- [ ] Write red deterministic/path tests around `write_example_inputs(root)`:

```python
import pathlib
import tempfile
import unittest
from scripts.multiserver.bundle import build_bundle
from multiserver_fixture import write_example_inputs

class BundleTest(unittest.TestCase):
    def test_geometry_inputs_not_user_byte_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            global_path, profiles = write_example_inputs(root)
            result = build_bundle(global_path, profiles, root/'bundle')
            self.assertEqual(len(result['instances']), 2)
            self.assertEqual(result['time_grid']['samples_per_block'], 16384)
            self.assertTrue((root/'bundle'/'MANIFEST.sha256').is_file())
```

- [ ] Run `python3 -B -m unittest discover -s tests -p 'multiserver_bundle_test.py' -v` red.
- [ ] Generate existing Observation v1 fields, including metadata/storage/receiver/processing/rings, from explicit global/profile inputs. Map T/S to groups_per_block. Keep weights and wire references relative to the local Observation file. Do not inject unknown multi-server fields into old Observation JSON.
- [ ] Store controller identity/deployment policy separately. Bundle ID is based on canonical input/resource contents and compiler/generator version identity, excluding incidental output-directory names, wall timestamps and the manifest's own bytes. Canonical JSON uses sorted object keys and rejects NaN/Inf; do not sort Station or weight axes.
- [ ] Write through a new task-owned staging directory and publish only after all files/hash checks succeed. Refuse an existing nonempty destination; no force-overwrite of old accepted artifacts. Reject symlink escapes, traversal IDs, duplicate instance paths and unexpected resources.
- [ ] Test deterministic builds in two different temporary output roots, changed-source/weights/profile identity differences, failure on second-instance weights without publishing a partial bundle, and same-host GPU0/GPU1 layout without file overwrite.
- [ ] Record global weight/full F mapping and per-instance slice evidence so the receiver configuration, local weight F order and frequency ownership remain traceable.

## Task 5 — Existing compiler compatibility and test registration

**Files:** `compiler.py`, `multiserver_compile_test.py`, feature-only `CMakeLists.txt`; complete `docs/control/configuration.md`.

**Consumes:** bundle instance directory, an explicit existing compiler binary, and a new artifact output directory. **Produces:** verified compiler report and resolved artifacts, or a failure preserving argv/rc/stderr. The adapter never patches generated headers.

- [ ] Unit-test the adapter with an injected subprocess result for failure/invalid JSON paths; do not classify a fake compiler PASS as integration acceptance.
- [ ] Implement the existing argv interface exactly:

```python
import subprocess

argv = [str(compiler), '--config', str(instance_directory/'observation.json'),
        '--output-dir', str(output)]
completed = subprocess.run(argv, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, check=False)
```

- [ ] The local generated bundle is portable; resolved plans are path-dependent artifacts. In later deployment, run this adapter on each host from its final installed instance path and record the resulting local IDs/hashes. Never ship a preview resolved plan referring to the developer's filesystem and call it the runtime plan.
- [ ] Register the pure tests with CTest using the discovered Python interpreter. Register `multiserver_compile_test.py` with `$<TARGET_FILE:observation_config_compile>` as its required argument; the test must fail clearly when no binary was supplied, not fall back to a default build directory.
- [ ] In the integration test create inputs/bundle in a temporary directory, compile both instances and assert: common T=16384/groups=32/time=16.384 ms, F4/F3, compute bytes 524288/393216, raw record bytes 4128/3104, raw block bytes 528384/397312, Power output bytes 4096/3072; local header shapes/IDs and weights are accepted.
- [ ] Add P2 coherency fixtures with explicit P=2 weights and check four-product output/time alignment; do not just rename the Power header. Add valid partial-tail and incompatible duration cases; use existing compiler acceptance rules, not silent duration rounding.
- [ ] Run the selected local pure tests and real compiler integration via a feature-owned portable build. Before configuring, check available local CMake; do not assume the server absolute path exists on macOS. On qths use only the validated fixed CMake/CTest 3.31.12 pair.
- [ ] Run relevant existing observation/artifact/weight tests without changing their fixtures. This phase should not require receiver/GPU source edits; if compatibility exposes such a requirement, stop and explicitly revise scope before editing those paths.
- [ ] Prepare a complete feature source manifest with the existing generator and an explicit feature output path `config/testing/multiserver-source-manifest.sha256`. Because the generator includes its source tree, this records new schemas/scripts/tests/docs; do not regenerate the parent paper manifest.
- [ ] Hand off the exact feature worktree/source identity, isolated remote roots, targets, fixture generation and expected tests to `GPU服务器代码测试`. Require server build and registered/direct test repetitions per repository rules; no remote data-flow test is needed for this configuration-only acceptance.
- [ ] After returned PASS, propose commit group 2 with explicit new-file/CMake paths and recorded test identity. Do not automatically commit, push, tag or merge.

## Expected local command interface after implementation

```text
python3 -B scripts/multiserver_compile.py compile --global FILE --deployments FILE --output DIR
python3 -B scripts/multiserver_compile.py resolve --instance-dir DIR --compiler BINARY --output DIR
python3 -B -m unittest discover -s tests -p 'multiserver_model_test.py' -v
python3 -B -m unittest discover -s tests -p 'multiserver_deployment_test.py' -v
python3 -B -m unittest discover -s tests -p 'multiserver_weights_test.py' -v
python3 -B -m unittest discover -s tests -p 'multiserver_bundle_test.py' -v
```

`FILE`, `DIR` and `BINARY` are CLI metavariables, not runnable remote paths. Tests allocate their own temporary inputs/output and receive the compiled binary from CTest. No command above exists as a completed new implementation yet.

## Completion criteria

- All new pure and real compiler compatibility cases pass locally and on the server with recorded identities.
- Seven-channel example is reproducible; production-sized shape checks do not require allocating a live GPU/ring or pretending the tiny fixture proves throughput.
- Same-host conflicts fail before artifact activation; per-instance profiles remain exactly as requested.
- Weight slices are bit-preserving; global observation identity is shared and local geometry identity is independently recorded.
- Parent branch, dirty files, old test configs and accepted-results indexes remain unchanged. No remote canonical paper source/build/results are overwritten.
- Phase 1 produces validated configuration bundles only. Centralized runtime start/stop and live monitoring are subsequent work packages, not claimed by this acceptance.
