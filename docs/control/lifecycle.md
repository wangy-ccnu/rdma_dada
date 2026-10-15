# Observation control lifecycle

MS-06 implements observation-wide decisions with an injected host adapter and
clocks. MS-07a supplies SSH transport; the MS-07b/c implementation below adds a
host supervisor and actual pipeline adapter. The latter is integrated but still
awaits batch review and server acceptance. None of these lifecycle interfaces
supplies live data-rate or sample-time telemetry (P4).

## Public interface

```python
ObservationController(db_path, host_adapter, *, utc_now, monotonic_ns,
                      heartbeat_timeout_ns, drain_timeout_ns)
controller.register_run(run_id, bundle, global_document)
controller.execute(request)
controller.status(run_id)
controller.reconcile(run_id)
controller.close()
```

`utc_now()` returns a timezone-aware `datetime.datetime` with UTC offset zero.
`monotonic_ns()` returns a nonnegative built-in integer and cannot move backwards
within one controller process. Both timeout arguments are explicit positive
integers, excluding booleans. No wall-clock sleeps or implicit timeout defaults
are used. UTC admits the configured start; it never supplies data progress or
normal-completion evidence. Start at the exact configured UTC is permitted;
start after it is refused.

One serialized controller owns dispatch, on SQLite's owning thread. A stop may
be received at an adapter/clock dispatch boundary through the same public API;
the next dispatch reloads durable intent. This is not an API for concurrent
command workers, multi-controller HA or distributed exactly-once execution.

`register_run` accepts the MS-04 bundle object and its canonical global document.
It reuses `validate_global`, checks the bundle's SHA256 identity excluding
`bundle_id`, and checks `inputs['global-observation.json']` against the validated
document's sorted, ASCII-escaped, compact JSON plus trailing newline. It binds
the observation ID, time grid, monitoring policy, participant assignments and
local observation hashes. Duplicate instance keys, mismatched assignments,
changed bindings and boolean GPU IDs are rejected. Identical registration is
idempotent and returns current status. Initial registration always returns NEW;
a bundle's static status never supplies runtime READY.

Registration does not read bundle paths or execute the compiler. Actual artifact
byte verification and installed-host readiness belong to the later host adapter.
This controller relies on matching, fresh host reports for runtime readiness.

`execute` accepts the shape in `config/multiserver-command-v1.schema.json`:

```json
{"schema_version":1,"request_id":"prepare-1","run_id":"run-a","operation":"prepare"}
```

`operation` is `prepare`, `start`, or `stop`. Stop alone requires `mode` equal
to `graceful` or `force`. Extra fields, caller-provided targets/commands, empty
identifiers, boolean schema versions, and surrogate-containing identifiers are
rejected. Request IDs are scoped to their run. An identical retry returns the
same operation ID and current outcome with no further dispatch. Reusing that
ID with different content fails. A different request ID cannot replay an
already-requested prepare/start; resolve uncertainty or explicitly stop instead.

The result contains `operation_id`, `request_id`, `outcome` and current `status`.
Outcome is APPLIED, UNKNOWN, REJECTED or CANCELLED. APPLIED acknowledges the
command's recorded effect; it is not normal-completion proof. A stop on an
already confirmed finished run is a recorded no-op and preserves its historical
result. A stop on an unprepared run needs no host call but suppresses later
prepare/start and records interruption.

`status` reads the stored snapshot and recomputes freshness locally. It never
sends a command, writes the database, refreshes report age or advances a deadline.
`reconcile` queries every registered participant, validates identity and action
outcomes, and may advance already-authorized human stop, startup rollback or
failed-instance cleanup. It never resubmits prepare/start. `close` closes SQLite
only, and can be repeated; healthy pipelines are not tied to its lifetime.

Input/protocol/state errors raise `ControllerError`, a `ValueError` with a stable
`code` and contextual detail. Missing runs, closed stores, revision conflicts and
SQLite failures retain the reviewed RunStore typed exceptions. Transport
`TimeoutError`/`ConnectionError`/`OSError` leaves action outcome UNKNOWN. Other
adapter exceptions propagate with durable uncertainty still recorded, so the
caller must reconcile rather than retry a launch.

## Store API

`scripts/multiserver/store.py` exposes:

```python
store = RunStore(path)
store.create(run_id, snapshot)
store.load(run_id)
store.save(run_id, expected_revision, snapshot)
store.close()
```

Create/load/save return `{run_id, revision, snapshot}` with independent snapshot
data; create begins at revision1 and each successful save increments it.
Snapshots must be finite JSON objects with Unicode scalar strings, no duplicate
keys on decode, cycles, coerced keys or unsupported types. Expected revision is
a positive integer, not bool. Atomic transactions preserve prior state on failure.
Existing empty, corrupt or unknown-version databases are refused, not reset.

Typed errors distinguish validation, duplicate/missing run, revision conflict,
closed store, database failure, unsupported version and corruption
(`StoreValidationError`, `RunExistsError`, `RunNotFoundError`,
`RevisionConflictError`, `StoreClosedError`, `StoreDatabaseError`,
`UnsupportedStoreVersionError`, `StoreCorruptionError`). Revisions prevent
stale writes; they do not establish distributed locking or multi-controller HA.

## Durable actions and host contract

The injected adapter implements:

```python
host_adapter.send(instance_key: Tuple[str, int], command: dict) -> dict
```

The key is `(server_id, gpu_id)`. Commands contain `run_id`, `bundle_id`,
`server_id`, `gpu_id`, `observation_sha256`, `expected_generation`, `action_id`
and `operation`. Start carries the original `utc_start` and `duration_seconds`;
it arms that schedule, not a replacement start time. Stop adds `mode`.
Status/stop also carry `pending_action_ids` for the same participant so the host
can report authoritative outcomes of uncertain actions, including an in-flight
prepare stopped before its response arrived. Status queries have fresh query
IDs and no process side effects. All prepare/start/stop action IDs are durable.

Before a mutating adapter call, the controller saves its request, action intent,
UNKNOWN dispatch outcome and possible resource ownership. It reloads stop
intent, fresh readiness and UTC after persistence, before start dispatch.
It saves the returned outcome against a newly loaded record, preserving a stop
that arrived during the call. A process failure on either side of the adapter
call leaves explicit uncertainty rather than pretending no action happened.

The host must deduplicate mutating action IDs and bind cleanup to this run,
bundle, observation hash and expected generation. When generation is initially
unknown, it must verify the recorded run/action ownership; it must not stop an
unrelated process or resource. These are requirements on the future real-host
adapter, not properties proved by the fake.

Every response has exactly these fields:

| Field | Meaning |
| --- | --- |
| `run_id`, `bundle_id`, `server_id`, `gpu_id`, `observation_sha256`, `action_id` | Must match the queried command binding; GPU ID is an integer, not bool |
| `outcome` | APPLIED, REJECTED, UNKNOWN or authoritative NOT_APPLIED |
| `state` | NEW, PREPARING, READY, STARTING, RUNNING, DRAINING, COMPLETED, INTERRUPTED or FAILED |
| `generation` | Nonempty Unicode scalar string for an active generation; null only for NEW/FAILED/INTERRUPTED with no established generation |
| `sequence` | Positive built-in integer, increasing for fresh observations |
| `cleanup` | PENDING, CONFIRMED or UNKNOWN; separate from lifecycle |
| `evidence` | Exactly `target_data_complete`, `output_published`, `eod_complete`, each a boolean |
| `error` | Null or nonempty Unicode scalar string |
| `action_outcomes` | Outcomes for requested pending IDs only, for this participant |

The first matching prepare result establishes generation, including a result
with an explicitly uncertain action outcome; it does not turn UNKNOWN into
APPLIED. Reconciliation/stop can establish it from an authoritative APPLIED
pending prepare outcome. Later generation changes require explicit resolution;
there is no automatic adoption or restart.

Older sequences supply no state, action, progress or cleanup facts. An identical
observation at the same sequence does not renew freshness. A contradictory
observation at that sequence is rejected. The observation comparison excludes
query/action envelopes, so a query may resolve a pending action without claiming
a fresh lifecycle observation. Terminal lifecycle states cannot regress.
STARTING/RUNNING/COMPLETED reports require a dispatched, authorized start intent;
they cannot upgrade a merely prepared run into a running one.

An authoritative NOT_APPLIED is distinct from a timeout. For prepare/start it
causes startup rollback, unless a human stop already cancelled the action; that
cancellation is not a fabricated instance failure. UNKNOWN or NOT_APPLIED cleanup
may be retried with the same action ID. Prepare/start is never retried that way.
A report that simultaneously claims an active/completed state and a rejected or
unapplied start is a protocol conflict; it cannot trigger rollback of healthy peers.
This check also applies when an identical lifecycle sequence resolves pending
action outcomes. Valid APPLIED resolution at that sequence does not renew
freshness; older sequences supply no action facts at all.

## State and failure policy

All participants need fresh READY reports before the first start. Before each
remaining dispatch, still-unarmed participants must remain fresh READY, and
already-acknowledged peers may be STARTING or RUNNING. A STARTING acknowledgement
is valid arming evidence; it is not proof that processing began atomically.
A partially armed run remains STARTING while another required participant is
READY/STARTING. If a reply is lost, unsent starts are suppressed and reconciliation
does not launch the remaining peers automatically. The explicit recovery path is
inspect/reconcile and human stop; v1 has no resume-start API.

Preparation/start rejection causes rollback of all possibly-owned resources.
Unreachable rollback targets remain unknown and keep the run unfinished.
After running is observed, a runtime-local failure cleans only that participant;
healthy peers continue. Its original frequency assignment and error history are
retained. Communication loss alone sends no stop and does not mark processing
failed. Failed and unknown participant lists remain separate even when health
is DEGRADED, so one summary flag cannot hide both conditions.
An INTERRUPTED report without human-stop or startup-rollback intent is likewise
a local failure, even if cleanup is already confirmed. Its host error is retained
(or an explicit unexpected-interruption error is recorded), along with the missing
frequency assignment. Only its pending cleanup is stopped; healthy peers continue.
With completed peers the final result is PARTIAL_FAILED; all unexpectedly
interrupted participants finish FAILED. Cleanup alone cannot make interruption PASS.

Human stop records observation-wide intent first and prevents later starts.
Graceful stop asks every possibly-active participant to drain. After the explicit
drain timeout, only still-owned, unclean participants receive force stop.
Explicit force persists as force and a later graceful request cannot downgrade
it. Confirmed clean terminal participants receive no further stop. Unknown
ownership/cleanup stays explicit; there is no elapsed-time cleanup-PASS inference.

Normal completion requires terminal COMPLETED plus target-data processing,
ordered output publication, EOD and confirmed cleanup. Acknowledgement or the
scheduled wall-clock end is insufficient. Active manual stop finishes as
INTERRUPTED even after a clean drain, retaining earlier failures. Missing
normal target-data evidence does not block a confirmed clean manual interruption.
Prepare/start rollback finishes FAILED. A failed shard with healthy completed
peers finishes PARTIAL_FAILED; all-failed runs finish FAILED. A not-yet-dispatched
NEW participant needs no cleanup under stop/rollback, and remains visibly NEW
instead of claiming a host ran or exited it.

Run phases are NEW, PREPARING, READY, STARTING, RUNNING, DRAINING and FINISHED.
Health is OK, DEGRADED or UNKNOWN. Result is null until terminal/cleanup facts
resolve the run, then PASS, PARTIAL_FAILED, FAILED or INTERRUPTED. A historical
terminal result is distinct from current communication freshness.

For example, a running run with one failed shard exposes:

```json
{
  "run_id": "run-a",
  "phase": "RUNNING",
  "health": "DEGRADED",
  "result": null,
  "failed_instances": [{"server_id": "node-a", "gpu_id": 0}],
  "unknown_instances": [],
  "missing_frequency": [{
    "server_id": "node-a", "gpu_id": 0,
    "assignment": {"server_id": "node-a", "gpu_id": 0, "first_channel_id": 100,
                   "nchan": 4, "deployment_profile": "example-node-a-gpu0"},
    "data_gap_start_sample": null,
    "data_gap_end_sample": null
  }]
}
```

This is an excerpt; full status also supplies revision, bundle/observation IDs,
per-instance state, generation, last report, errors, ownership/cleanup, `fresh`
and local `age_ns`. Unknown gap endpoints remain null. No live data rates or
sample progress are fabricated by this stage.

## Restart policy and tests

Only JSON snapshots/revisions are durable. Local monotonic timestamps are never
persisted or subtracted across controller processes. Reopening makes prior
liveness stale until increasing, matching reports arrive. A duplicate cached
report cannot become fresh merely because a new controller read it.

A graceful drain interrupted by restart receives a new bounded reconciliation
window, starting with the new process's first cleanup reconciliation. It does
not inherit an old monotonic deadline or infer success. Repeated crashes can
extend this conservative window; v1 deliberately has no external deadline/HA
service. Explicit force remains force. Stop/rollback/failed-instance cleanup can
advance after identity queries, but no prepare/start is automatically replayed.

Direct tests from the repository root:

```bash
python3 -B -m unittest discover -s tests -p multiserver_store_test.py -v
python3 -B -m unittest discover -s tests -p multiserver_controller_test.py -v
```

Local portable CTest configuration uses the observed matching tool pair:

```bash
/opt/homebrew/bin/cmake -S . -B build-ms06-offline \
  -DBUILD_TESTING=ON -DBUILD_RDMA_PIPELINE=OFF -DUSE_CUDA=OFF \
  -DBUILD_SSD_BENCHMARK=OFF
/opt/homebrew/bin/ctest --test-dir build-ms06-offline \
  -R '^multiserver_(store|controller)_test$' --no-tests=error --output-on-failure
```

These two registered tests are pure Python and require no compiled target.
This filter does not imply unrelated C++ targets were built or tested. Existing
pure model/deployment/weights/bundle suites remain regression gates. The
controller fixture generates a real small two-shard MS-04 bundle, then uses
stateful fake hosts and clocks for positive, failure, crash and restart paths.
No actual transport, resource creation or old performance runner is exercised.

After independent review, the coordinator freezes source identity and hands the
stage to the GPU-server testing task. Linux offline acceptance checks the target
Python/sqlite/CMake environment and runs the new registered tests three clean
consecutive times; it creates no rings, uses no GPU/RDMA and generates no network
traffic. Real supervision/transport and live metrics remain later stages.

## SSH command transport (MS-07a)

`scripts/multiserver/transport.py` provides the existing adapter interface:

```python
SSHHostAdapter(endpoints, *, ssh_path, timeout_seconds)
adapter.send((server_id, gpu_id), command) -> dict
```

`endpoints` is a nonempty trusted deployment dictionary keyed by server ID.
Each entry requires `ssh_host`, `python_path`, `host_entrypoint` and
`known_hosts_file`, with optional `deployment_path` for the production host CLI.
The first is a hostname/SSH alias, optionally `user@host`;
IP literals with colons and arbitrary SSH options are not accepted. The paths
are absolute Unicode scalar strings without control characters. The explicit
known-hosts path is on the control host and additionally excludes quotes,
backslashes, percent and dollar characters to prevent SSH token/environment
expansion. `ssh_path` is an explicit absolute local executable path;
`timeout_seconds` is an explicit int/float in `(0, 3600]` seconds (one hour
maximum), excluding bool. Out-of-range values, including finite extremes such
as `1e308`, raise `ValueError` before any child is launched. This bounds both
the remaining selector wait and SSH's integer connection timeout to values
representable on the supported POSIX hosts.
Validation checks syntax, not remote file availability. Endpoint values are
copied at construction and cannot be selected or overridden by command fields.

The instance key must be a tuple of server ID and nonnegative built-in integer
GPU ID. The command must be a dict with exactly matching `server_id` and integer
`gpu_id`; unknown targets and mismatches fail before any child is launched.
Other command fields remain JSON data. Requests accept only finite JSON values,
string keys and Unicode scalar strings; cycles, coerced keys, tuples and bytes
are rejected. The wire form is sorted, compact UTF-8 JSON plus one newline,
with Unicode preserved and `allow_nan=False`. The complete request including
newline must fit 1 MiB and is validated before launch.

Each call makes one direct SSH dispatch from the control host to the configured
host. Local execution uses an argv list with `shell=False`. SSH still uses a
remote shell: only the trusted fixed `[python_path, '-B', host_entrypoint]`, plus
`['--deployment', deployment_path]` when configured, is quoted with `shlex.quote`
into its command string. Runtime payload is written
only to stdin. The adapter uses `-T`, `BatchMode=yes`,
`StrictHostKeyChecking=yes`, an explicit quoted `UserKnownHostsFile`,
`ConnectionAttempts=1` and an integer `ConnectTimeout` in `[1, 3600]`, rounded
up from the request timeout. It enrolls no keys and edits no SSH settings. Run the
adapter on HF for the observed HF→qths1 topology; it contains no nested SSH.

A monotonic deadline covers request encoding, child launch, stdin writes,
stdout/stderr reads, child exit and response decoding. Nonblocking POSIX pipes
and a selector prevent blocked stdin or a noisy stream from starving the other
streams. Each output stream is limited separately to 1 MiB during reading;
stderr is counted and discarded, and stdout is buffered only within its limit.
Synchronous validation/JSON work and OS child creation check the deadline at
boundaries; they are not themselves preemptible. Timeout raises `TimeoutError`.
Launch, early stdin close, connection and nonzero exit (including SSH exit255)
raise `OSError`. Output overflow and invalid protocol raise `ValueError`.
Responses must be one strict UTF-8 JSON object: duplicate keys at any depth,
nonfinite numbers (including overflowing exponents), lone surrogates, banners,
truncated JSON and trailing non-whitespace data are refused. Response identity,
state and generation validation remain with MS-06.

There is no implicit retry. On every outcome the adapter closes its pipes and
reaps its local child, killing that child on timeout/overflow if still running.
This neither confirms remote cancellation nor issues a remote cleanup command.
A remote action may already have applied. Errors omit payload, endpoint paths
and stderr text; the adapter writes no logs. MS-06 retains its recorded action
uncertainty for transport and protocol failures and reconciles by identity,
without replaying prepare/start or stopping healthy processing for connection
loss. This transport does not supply host deduplication or a production
supervisor; those are MS-07b dependencies. It makes no observation/ring/GPU
operations itself and does not establish disconnect survival.

Local development checks (no actual SSH):

```bash
python3 -B -m unittest discover -s tests -p multiserver_transport_test.py -v
python3 -B -m unittest discover -s tests -p multiserver_controller_test.py -v
python3 -B -m unittest discover -s tests -p multiserver_store_test.py -v
/opt/homebrew/bin/cmake -S . -B /tmp/rdma-ms07a-offline \
  -DBUILD_TESTING=ON -DBUILD_RDMA_PIPELINE=OFF -DUSE_CUDA=OFF \
  -DBUILD_SSD_BENCHMARK=OFF
/opt/homebrew/bin/ctest --test-dir /tmp/rdma-ms07a-offline \
  -R '^multiserver_(store|controller|transport)_test$' \
  --no-tests=error --output-on-failure
```

The tests substitute only the SSH launch boundary, exercising real local Python
children for JSON, output bounds, blocked stdin, timeout and exit. Each local
child must be reaped with closed pipes. Controller integration uses a real
SQLite store, the existing stateful host double and the real transport to lose
replies after simulated application, then reopen/reconcile without replay.
Local PASS is development evidence, not a replacement for server gates.

The server test owner synchronizes the frozen source, including
`tests/fixtures/multiserver_transport_peer.py`, to the feature mirror on HF and
qths1. Run the affected registered tests three clean times using the observed
Linux CMake/CTest pair `/home/user/wy/tools/cmake-3.31.12/bin/{cmake,ctest}`.
Then run this opt-in probe **on HF**, using the existing verified known-hosts
file (adjust its absolute path if Phase 0's SSH configuration uses another):

```bash
cd /home/user/wy/rdma_dada-multiserver
/usr/bin/python3 -B tests/multiserver_transport_test.py --remote-probe \
  --ssh-path /usr/bin/ssh --ssh-host qths1 --python-path /usr/bin/python3 \
  --host-entrypoint /home/user/wy/rdma_dada-multiserver/tests/fixtures/multiserver_transport_peer.py \
  --known-hosts-file /home/user/.ssh/known_hosts --timeout-seconds 10
```

The version1 peer echoes a Unicode/metacharacter JSON object, then the probe
sends a second JSON object selecting a deterministic exit7 failure. Success
prints a JSON summary with `roundtrip=PASS` and `deterministic_exit7=PASS`.
The peer never invokes lifecycle commands, starts children, creates host state
or accesses rings, GPU, services or observation resources. It is solely a test
fixture, and cannot substitute for the future supervisor entrypoint. Preserve
commands, source identity, results and cleanup in the feature evidence namespace.
Do not infer production host behavior from this probe.

Acceptance on 2026-10-11: independent spec/standards review passed after bounding
the command timeout to one hour. The qths1 registered store/controller/transport
suites met the three-repeat gate (28/52/22 cases per suite); all three HF→qths1
echo/exit7 probes passed with no task residue. See the original
[server evidence](evidence/ms07a-acceptance-20261011.json) together with the
[coordinator audit](evidence/ms07a-acceptance-audit-20261011.json), which retains
extra repetitions and the notification-only hash change. This accepts only
command transport; host supervision, disconnected process survival, real
observation resources and business telemetry were outside that acceptance.

## Host supervision and pipeline adapter (MS-07b/c)

Build and no-dataflow gates are accepted on 2026-10-15; see the
[stage result](evidence/ms07bc-build-gates-20261015.json). Actual multi-instance
dataflow and native lifecycle functional acceptance remain pending.

The trusted host entrypoint is:

```text
python3 -B scripts/multiserver_host.py --deployment /absolute/private/host.json
```

One bounded strict JSON command arrives on stdin; one host report returns on
stdout. The deployment is an operator-owned private regular file, not a path or
executable selected by request JSON. Its root is private and canonical. It binds
the installed bundle and each run/server/GPU/observation identity to its physical
GPU UUID, CPU/core identities, expanded IPC key family, flow and run directory.
Configuration additionally supplies explicit request/stop deadlines and a fixed
`runtime.kind = "pipeline"` mapping. There is no arbitrary plugin loader.

The pipeline runtime requires SHA-bound absolute binaries for the compiler,
`numactl`, `dada_db`, `dada_dbnull`, receiver, unpack, GPU worker and GPU probe;
an explicit environment; bounded readiness/drain/poll intervals; and the observed
PSRDADA exclusive-create/max-readers/header-buffer contract. CUDA ordinal
remapping is refused. `leaf` is an assertion about an independently reviewed
no-fork executable, not proof inferred from an empty process listing. New binary
identities need that review before they can support descendant-cleanup evidence.

The CLI uses a durable mailbox and detached supervisor with redirected streams.
The supervisor retains the original child handles across ordinary SSH request
exits. One host-local SQLite journal records claims and action intent before
side effects. PID/start ticks/boot ID/UID and retained child ownership gate
signalling; no name-based, process-group or recovered-nonchild kill is allowed.
Uncertain actions are reconciled, never blindly replayed. A dead supervisor is
UNKNOWN, not permission to auto-start a replacement. Current deployment binding
is immutable for that supervisor. A batch pre-installs all run bindings and shares
one host journal; sequential runs may reuse resources only after exact claim
release. Do not create an independent root per repetition to bypass conflicts.
After the batch, the operator may invoke the same CLI with `--retire` (no stdin).
It refuses while any claimed instance is active, unclean, unresolved or while
commands are queued. Confirmed retirement closes the retained supervisor and
releases its daemon lock without signalling workers; result is `RETIRED` with
`cleanup=CONFIRMED`. `REFUSED`/`UNKNOWN` must not be relabelled successful cleanup.
The deployment cannot then auto-restart; subsequent deployment/recovery remains
an explicit operator action, not an implicit response to missing processes.

Preparation verifies bundle/compiler identities, physical resources and the full
IPC family before exclusive creation. It records exact object fingerprints and
starts output sink → GPU → unpack → receiver. READY means the configured processes
and input interfaces are prepared, not that a data-dependent GPU transfer or
calculation has completed. Optional ready/result files bind PID, configuration,
geometry and ring keys; existing algorithm/layout/header contracts are unchanged.

Start preserves the original Observation UTC and exact duration string. Each
instance has its own deadline scheduler so one slow drain does not prevent a
healthy peer's deadline. At observation end or manual stop, ingress is stopped
first; consumers drain before bounded, exact-owned forced termination. The
wall-clock deadline initiates drain but does not prove data completion. Normal
completion requires actual Station/sample extent, GPU bytes/publication, EOD and
resource cleanup. Forced or manual termination cannot become normal PASS.

Ring cleanup checks recorded SysV object IDs and ownership/size/creation facts,
and refuses attached or replaced objects. Partial allocation without sufficient
ownership evidence remains UNKNOWN; a pre-run absence check alone never
authorizes removal. Claims release only after matching process/descendant/IPC
cleanup evidence. Run records and failure evidence are retained, not treated as
disposable resources.

Before a server dataflow test, build `psrdada_ipc_layout_probe` and run the
registered `multiserver_native_ipc_layout_test`. This read-only header/ABI check
compares installed PSRDADA constants and native structure size with the ownership
footprint model. Portable tests explicitly skip this native check; a skipped check
does not authorize real IPC creation. A changed PSRDADA ABI must be resolved first.

Local development gates:

```bash
python3 -B -m unittest discover -s tests -p 'multiserver_*_test.py'
python3 -B tests/multiserver_pipeline_runtime_test.py --compiler /absolute/observation_config_compile
```

The first includes explicit Linux/compiler skips on unsupported local platforms.
It is not real-host acceptance. The opt-in `tests/pipeline_lifecycle_hooks_test.py
--deployment /absolute/host.json` creates real resources and is reserved for the
server owner's validated fixture; never enable it during ordinary local discovery.
Unified server tests must cover the unchanged control API through this actual
runtime, not just doubles or a successful dry-run. See the maintained roadmap
and task ledger for the pending matrix and frozen source identity.

### Unified functional acceptance entrypoint

On HF, the server test owner uses the versioned runner:

```text
python3 -B scripts/multiserver_acceptance.py --suite /absolute/suite.json --output /absolute/feature/results/campaign
```

The trusted suite declares source/build identity, SSH endpoints, bounded deadlines
and exactly three repetitions per case. Bundles, sender configs and host deployments
must already bind all run IDs and fixed future UTC windows. Tests send only formal
observation-window data, without preparation traffic. Startup readiness remains
required. The fixed peer fixture owns senders and reads receiver evidence; request
JSON cannot provide executable paths, arbitrary commands or signal targets.

Cases cover single and two-GPU operation (equal channels and one extra channel),
all-ready gating, preparation rollback, finite EOD, graceful/forced manual stop,
isolated runtime failure and disconnect/reconnect. Here preparation rollback means
resource initialization failure, not pre-observation data transmission. Its private
negative bundle declares one SHA-bound deviation and the exact rejected instance.
Runtime failure must match the preinstalled fault target, process identity and
authoritative GPU exit; a different failed instance cannot satisfy the test.
Healthy instances continue after a local GPU failure; authoritative input-sender
failure stops the whole test transfer.

Compiler work is also an owned retained subprocess with a readiness deadline and
bounded log reads. Unknown liveness/reaping cannot justify releasing its claim.
Temporary observation uncertainty does not by itself kill a healthy pipeline or
renew the last confirmed state. Shared optional lifecycle JSON publication uses
exclusive temporary creation and no-replace publication.

The runner records intent before effects and retains compact results, process/IPC
proof and first-failure evidence. `--resume` reconciles and stops unfinished runs;
it never replays starts or injections and marks interrupted verification incomplete.
Cleanup cannot overwrite the test outcome. A failed cleanup stops subsequent cases.
This is functional acceptance, not a throughput or real-time performance campaign.
