# Multi-server configuration inputs

The global input identifies the telescope explicitly with `metadata.telescope`.
It retains one editable frequency input pair: `channels.first_center_frequency_hz`
and `channels.channel_spacing_hz`. The full-band center is derived exactly in
integer Hz as

```text
full_band_center_hz = first_center_frequency_hz
                    + (nchan - 1) * channel_spacing_hz / 2
```

For the checked-in seven-channel CA example, the first channel center is
1,297,000,000 Hz and the spacing is 1,000,000 Hz, yielding a 1,300,000,000 Hz
full-band center. A local contiguous assignment uses the same formula with its
own first channel and channel count; odd and even channel counts are accepted
only when the resulting center is integral Hz.

Deployment profiles declare the receiver RDMA device and destination MAC. The
inventory binds each selected interface to its `rdma_device`, `verbs_port`, and
`mac_address`. Inventory verbs ports must be positive integers; this milestone
accepts verbs port 1 only for interfaces selected by deployment profiles. Unused
interfaces may describe other ports. Static validation does not claim live host
readiness. Storage is explicitly disabled (`enabled=false`,
`blocks_per_file=0`, `direct_io=false`) for this configuration-only slice.

## Portable bundles (MS-04b)

Build a local bundle with an explicitly supplied inventory:

```text
python3 -B scripts/multiserver_compile.py compile --global FILE --deployments FILE --inventory FILE --output DIR
python3 -B scripts/multiserver_compile.py --version
```

`FILE` and `DIR` are placeholders. The Python API is
`build_bundle(global_path, deployment_path, output, *, inventory_path)`.
The CLI reports the bundle as JSON on success, or
`{"ok":false,"error":{"code":"...","message":"..."}}` with nonzero exit
status on failure, including argument errors. Its version is
`multiserver-bundle-v1`. Compiler resolution is a subsequent milestone.

Weights and wire resources must exist relative to the global JSON directory.
Supported weights retain MS-03's C-contiguous NPY v1/v2 `|i1` or `<i2`
`[F,P,A,B,2]` contract. Full F/P/A must match the global observation and B
must be positive. B and all local slice shapes are read from the supplied
matrix; Station, polarization, beam and complex-component order are preserved.
The wire JSON must match the checked-in Project VDIF v1 contract after JSON
normalization. Its supplied bytes are copied unchanged; formatting changes
affect resource identity, and local F comes from Observation JSON.

The output contains:

```text
bundle.json
global-observation.json
deployments.json
inventory.json
MANIFEST.sha256
instances/<server_id>/gpu-<gpu_id>/observation.json
instances/<server_id>/gpu-<gpu_id>/weights.npy
instances/<server_id>/gpu-<gpu_id>/wire-profile.json
```

The normalized input copies are provenance records, not alternate runnable
global inputs: their original resource references remain recorded, while each
instance's Observation references its local `weights.npy` and `wire-profile.json`.
`bundle.json` preserves the global monitoring policy, assignments, full weight
shape/hash, local F offsets/shapes/hashes, and expanded deployment claims
(CPU roles, NUMA, flow, execution and consumer settings). CPU/flow policy in
this sidecar is not applied to a host by bundle generation.

Observation fields use the existing schema and module names: beamform followed
by power or stokes, then integrate. Common T/S determines groups per block.
Each local bandwidth is `nchan * channel_spacing_hz`; the F7 tail starts at
1,301,000,000 Hz and has center 1,302,000,000 Hz. CUDA device/mode/slots,
receiver destination, rings and disabled storage come from the selected profile.
The existing compiler uses `compute_ring_blocks` for both compute and output
rings, so differing compute/output counts fail with
`OUTPUT_RING_COUNT_UNSUPPORTED`. Concrete destination IPv4/UDP port is required;
non-wildcard source filters and TCP fail instead of disappearing during mapping.
Source wildcard declarations remain in the policy sidecar. Disabled-storage
bundles require `DADA_DBNULL`; disk consumers are refused.

Bundle IDs hash canonical inputs, raw resource hashes, generator source hashes,
and the declared compiler contract. Output locations and timestamps are excluded;
JSON object keys are sorted while lists/axes keep input order. Duplicate JSON
keys and nonfinite numbers are rejected. No compiler-binary identity or resolved
geometry/config ID is fabricated. The manifest hashes every produced file except
itself and is verified before publication.

Use canonical filesystem paths with no symlink components, including on macOS
where `/var` and `/tmp` are aliases. Resource traversal, unsafe server path
components, and nonempty/existing-file output destinations are refused. The
output's parent must already exist; a new or empty output directory is supported.
Generation uses a task-owned staging directory beside the output. Publication
uses atomic no-replace rename on macOS/Linux; unsupported platforms/filesystems
fail explicitly. A later-instance error cleans only owned staging and publishes
no partial bundle. A cleanup failure reports the retained staging path.

`static_valid` remains separate from `runtime_ready=false`; deployment's
unverified prerequisites are retained. Local pure tests verify generation and
identity, not compiler compatibility, byte budgets, live resources or throughput.
The local adapter below checks existing compiler compatibility separately.

## Resolve installed instances with the existing compiler

Supply the built compiler explicitly; there is no PATH or build-directory fallback:

```text
python3 -B scripts/multiserver_compile.py resolve --instance-dir DIR --compiler BINARY --output DIR
```

The instance directory must contain the generated `observation.json` and its
relative weight/wire resources. Resolve from the actual installed path on each
host. The embedded absolute resource/source paths in resolved plans refer to
that installation; a development preview is not a deployable runtime plan.
Do not edit compiler-generated headers or manufacture local IDs.

The adapter invokes the explicit binary with an argument array:
`BINARY --config INSTANCE/observation.json --output-dir STAGING/artifacts`.
Staging is an exclusively allocated directory beside the requested destination.
The destination must be new (even an existing empty directory is refused), its
parent must exist, and it must be outside the portable bundle/instance. Symlink
paths and traversal are refused. The compiler's stdout report must declare
`valid=true`, its five checks must pass, and report/artifact identities, artifact
file set, header sizes and the compiler's SHA manifest must agree. After checking
that inputs and binary are unchanged, artifacts are published using atomic
no-replace rename. Concurrent destination creation does not authorize overwrite.

Success preserves compiler artifact bytes and `MANIFEST.sha256` and adds separate
`resolution.json` evidence with `ok=true` and `published=true`. It records actual binary path/SHA256, installed input
and resource hashes, artifact hashes, compiler config/geometry IDs, argv, exit
code, stdout and stderr. `runtime_ready=false` remains explicit: resolving does
not validate CPU/GPU/NIC inventory, create rings, launch workers or establish
throughput. Bundle files and their immutable manifest are preserved.

Before-publication failures return nonzero CLI status and structured JSON (`ok=false`, stable error
code). Compiler failures also include `compiler_failure` with original argv,
returncode, stdout/stderr and an `evidence_directory`. The owned staging directory
is retained with unsuccessful `resolution.json` and any partial compiler artifacts;
the requested success destination is not published. Pre-invocation errors such
as a missing/non-executable binary do not create staging. If staging ownership has
changed, writes/cleanup are refused and diagnostic error details remain in the
exception/CLI JSON. Failed directories are retained for diagnosis; ownership
records must be checked before any later cleanup.

After publication, failure to remove the now-empty staging workspace does not
undo successful compilation. The API and CLI return `ok=true`, `published=true`,
with a `cleanup_warning` (`RESOLVE_CLEANUP`, message and retained workspace path);
the warning is atomically persisted in the final `resolution.json`, whose
`evidence_directory` points to the published OUTPUT. CLI exit status remains 0.
If persisting the warning itself fails, a structured `RESOLVE_CLEANUP_EVIDENCE`
error returns nonzero status with `published=true`, OUTPUT, artifact hashes and
the cleanup/diagnostic errors in `compiler_failure`. Published artifacts and
their original success evidence remain intact. This is a diagnostic/cleanup
problem; do not retry compilation into that existing output or delete it as a
rollback. The adapter never removes the published artifact directory.

Compatibility tests build deterministic A4/F7/P1/B2 bundles and resolve F4/F3:
common T=16384, S=512, K=128, 32 groups/block, 1,000,000 ps sampling and
16.384 ms block duration. Compute bytes are 524288/393216, raw record bytes
4128/3104, raw block bytes 528384/397312 and Power output bytes 4096/3072.
Tests also cover explicit P2 coherency weights/four products, same-host GPU0/GPU1,
NPY v1/v2, Station/channel/header/weight ordering and valid partial tails.
Incompatible duration is rejected by the unchanged compiler without rounding.

The NPY slicer preserves both int8 and int16 values and versions. The current
Observation wire contract is CI8: **full Observation resolution accepts int8,
and rejects int16 weights**. The existing C++ metadata reader accepts `<i2`, but
`src/config/resolved_observation_plan.cpp` checks weight complex width against
antenna input width and returns `beamform weight dtype must match antenna input width`.
The real v1/v2 int16 tests require that rejection, not a Full compatibility PASS.
No input-width change, dtype conversion or algorithm change is included.

## Local and server verification

Check the available local CMake/CTest pair before configuring:

```text
cmake -S . -B build-ms05 -DBUILD_TESTING=ON -DBUILD_RDMA_PIPELINE=OFF -DUSE_CUDA=OFF
cmake --build build-ms05 --target observation_config_compile observation_config_test resolved_observation_plan_test observation_artifacts_test beamform_weight_metadata_test -j 4
ctest --test-dir build-ms05 -R 'multiserver|observation_config_test|resolved_observation_plan_test|observation_artifacts_test|beamform_weight_metadata_test|observation_config_compile_test' --output-on-failure
python3 -B -m unittest discover -s tests -p 'multiserver_*_test.py' -v
python3 -B tests/multiserver_compile_test.py --unit
python3 -B tests/multiserver_compile_test.py build-ms05/observation_config_compile
```

CTest registers model/deployment/weight/bundle pure suites, adapter unit tests,
and real compiler integration separately. It passes
`$<TARGET_FILE:observation_config_compile>` explicitly to integration. Direct
integration invocation without a binary fails with usage; unittest discovery
skips the integration class with an explicit reason. Those skips are not counted
as real compiler acceptance. Negative unit paths alone inject subprocess results.

Local evidence is development verification only. After coordinator review,
`GPU服务器代码测试` performs Phase 0, complete source-tree synchronization and
manifest verification in isolated multiserver source/build/result roots. On qths1
use `/home/user/wy/tools/cmake-3.31.12/bin/cmake` and its matching `ctest` for all
configure/build/list/run steps. Repeat Linux pure tests, registered new and related
compiler/artifact/weight tests, and direct compiler integration; record source,
binary and generated artifact identities. Generate a full feature manifest only
at the coordinator review/formal-handoff freeze with explicit output
`config/testing/multiserver-source-manifest.sha256`; preserve historical/paper
manifests. No RDMA/GPU flow or remote execution is required or performed here.
