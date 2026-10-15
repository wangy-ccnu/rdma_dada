"""Host-local durable resource claims and action intents (internal B1 API).

One host uses ONE trusted SQLite path, including across server aliases. Each
connection belongs to its creating thread; concurrent workers open independent
HostJournal objects. BEGIN IMMEDIATE serializes checks and writes. Only a newly
created file is initialized; existing empty/unknown/corrupt state is refused.
No hardware is allocated, probed, signalled or cleaned here.

Owner has exactly run_id, bundle_id, server_id, gpu_id, observation_sha256,
generation. IDs/generation are nonempty Unicode scalar strings; gpu_id is a
nonnegative built-in int; observation_sha256 is lowercase hex SHA256. Generation
is host-established, even when an initial prepare expected_generation is null.

Resources has exactly gpu_uuid (observed UUID), cpus (nonempty list of objects
with logical_cpu/socket_id/core_id nonnegative ints), ipc_keys (nonempty list
of nonzero uint32 ints), flow ({physical_port, match}), run_dir (absolute path).
CPUs/IPC are sorted; duplicate logical CPUs/IPC are rejected. Physical siblings
within ONE claim are permitted, across claims refused. Flow match supports the
deployment's IPv4 source/destination IP, source/destination port, UDP/TCP fields;
missing/null/'*' fields are wildcards. Paths resolve symlinks, and nested paths
conflict. Trusted callers must verify topology, GPU enumeration, foreign IPC and
path identities at allocation/cleanup time; this journal arbitrates declarations.

claim(owner, resources) -> record; identical active claim is idempotent.
load(owner) -> {owner, resources, released, cleanup_evidence, actions}.
actions maps action_id to {command, outcome, report}; all returns are detached.
begin_action(owner, command) -> {created: bool, action: {...}}. ONLY created=True
authorizes the caller's first execution. False with UNKNOWN requires inspection,
never replay. Commands use unchanged MS-06 mutating envelopes. Action IDs cannot
be rebound anywhere in this host journal, including after release. A null
expected_generation is allowed only on prepare/stop and still binds this exact
recorded owner; status is read-only and needs no action intent.
finish_action(owner, action_id, report) -> action. It stores a strict-JSON MS-06
report after binding/outcome validation; lifecycle/sequence policy belongs to B3.
The report may use the original action envelope or a status/stop query envelope
whose action_outcomes contains action_id; in the latter case that map supplies
the stored outcome (not the query's own outcome). B3 validates the query and its
requested IDs before calling. The exact report/sequence is retained without any
freshness timestamp or sequence increment.
UNKNOWN may be resolved by explicit, authoritative reconciliation using this
method. Resolved APPLIED/REJECTED/NOT_APPLIED reports are immutable; exact retries
return the original. Reading, reopening, closing or time passing resolves nothing.

release(owner, cleanup_evidence) -> record. Evidence has exactly owner, resources
(matching normalized claim), processes_gone, descendants_gone, resources_gone;
all three flags must be True booleans from verified host cleanup. Unresolved
actions also prevent release. This is a trusted caller attestation, not a lease
or hardware verification API. Released history is retained; only a NEW owner
(normally a new generation) can claim those resources again. Cached actions may
be read after release, but new actions and resurrection are refused. close() is
idempotent and closes SQLite only. There is no expiry or automatic recovery.

Errors: JournalValidationError(ValueError) for invalid inputs, OwnerNotFoundError
for an unrecorded exact binding, OwnershipConflictError for occupied resources or
changed/released claims, ActionConflictError for reused IDs/changed outcomes,
CleanupIncompleteError for insufficient release evidence, JournalClosedError,
and JournalDatabaseError (JournalCorruptionError subclass) for fail-closed storage.
No schema migration/reset, distributed locking, multi-host arbitration, or
exactly-once process execution is promised. A crash after intent requires B3
reconciliation. Scans retain history and are intentionally sized for v1 usage.
"""

import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
from contextlib import contextmanager

from .store import _validate_json, _unique_object, _reject_constant, StoreValidationError
from .model import _duration_ps, _validate_utc


_OWNER_FIELDS = {"run_id", "bundle_id", "server_id", "gpu_id", "observation_sha256", "generation"}
_BINDING_FIELDS = _OWNER_FIELDS - {"generation"}
_RESOURCE_FIELDS = {"gpu_uuid", "cpus", "ipc_keys", "flow", "run_dir"}
_MATCH_FIELDS = {"source_ip", "destination_ip", "source_port", "destination_port", "protocol"}
_OUTCOMES = {"UNKNOWN", "APPLIED", "REJECTED", "NOT_APPLIED"}
_CREATE_TABLE = "CREATE TABLE owners (identity TEXT PRIMARY KEY NOT NULL, document TEXT NOT NULL)"


class JournalError(Exception):
    """Base for host journal failures."""


class JournalValidationError(JournalError, ValueError):
    """Invalid strict JSON or internal interface shape."""


class OwnershipConflictError(JournalError):
    """Claim conflicts with recorded ownership; no mutation occurred."""


class OwnerNotFoundError(JournalError):
    """No record for this exact owner binding."""


class ActionConflictError(JournalError):
    """Action ID missing, rebound, or resolved differently."""


class CleanupIncompleteError(JournalError):
    """Release lacks complete verified ownership/cleanup evidence."""


class JournalClosedError(JournalError):
    """Operation after close."""


class JournalDatabaseError(JournalError):
    """Database unavailable, unknown schema, or SQLite failure; never reset."""


class JournalCorruptionError(JournalDatabaseError):
    """Invalid durable content; no empty-state fallback."""


def _json(value):
    try:
        _validate_json(value, "journal", set())
        return json.dumps(value, sort_keys=True, ensure_ascii=True,
                          separators=(",", ":"), allow_nan=False)
    except (StoreValidationError, ValueError, RecursionError) as error:
        raise JournalValidationError(str(error)) from error


def _keys(value, fields):
    if type(value) is not dict or set(value) != fields:
        raise JournalValidationError("missing/unknown object fields")


def _text(value):
    if type(value) is not str or not value or "\x00" in value:
        raise JournalValidationError("expected nonempty scalar string without NUL")


def _integer(value, maximum=None):
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        raise JournalValidationError("expected nonnegative bounded integer, not bool")


def _owner(value):
    _json(value)
    _keys(value, _OWNER_FIELDS)
    for name in _OWNER_FIELDS - {"gpu_id"}:
        _text(value[name])
    _integer(value["gpu_id"])
    if not re.fullmatch(r"[0-9a-f]{64}", value["observation_sha256"]):
        raise JournalValidationError("observation_sha256 must be lowercase SHA256")
    return json.loads(_json(value))


def _resources(value):
    value = json.loads(_json(value))
    _keys(value, _RESOURCE_FIELDS)
    _text(value["gpu_uuid"])
    for name in ("cpus", "ipc_keys"):
        if type(value[name]) is not list or not value[name]:
            raise JournalValidationError(name + " must be nonempty list")
    logical = set()
    for cpu in value["cpus"]:
        _keys(cpu, {"logical_cpu", "socket_id", "core_id"})
        for number in cpu.values():
            _integer(number)
        if cpu["logical_cpu"] in logical:
            raise JournalValidationError("duplicate logical CPU")
        logical.add(cpu["logical_cpu"])
    value["cpus"].sort(key=lambda cpu: cpu["logical_cpu"])
    for key in value["ipc_keys"]:
        _integer(key, 0xffffffff)
        if key == 0:
            raise JournalValidationError("IPC_PRIVATE is not an exclusive key")
    if len(set(value["ipc_keys"])) != len(value["ipc_keys"]):
        raise JournalValidationError("duplicate IPC key")
    value["ipc_keys"].sort()
    flow = value["flow"]
    _keys(flow, {"physical_port", "match"})
    _text(flow["physical_port"])
    if type(flow["match"]) is not dict or set(flow["match"]) - _MATCH_FIELDS:
        raise JournalValidationError("invalid flow matcher")
    match = {}
    for name, item in flow["match"].items():
        if item is None or item == "*":
            continue
        if name.endswith("_port"):
            _integer(item, 65535)
            if item == 0:
                raise JournalValidationError("flow port must be positive")
        elif name.endswith("_ip"):
            _text(item)
            try:
                item = str(ipaddress.IPv4Address(item))
            except ValueError as error:
                raise JournalValidationError("flow requires IPv4 address") from error
        else:
            _text(item)
            item = item.upper()
            if item not in ("UDP", "TCP"):
                raise JournalValidationError("flow protocol must be UDP/TCP")
        match[name] = item
    flow["match"] = match
    _text(value["run_dir"])
    path = Path(value["run_dir"])
    if not path.is_absolute():
        raise JournalValidationError("run_dir must be absolute")
    try:
        value["run_dir"] = str(path.resolve())
    except (OSError, RuntimeError) as error:
        raise JournalValidationError("cannot canonicalize run_dir") from error
    return value


def _command(owner, value):
    value = json.loads(_json(value))
    if type(value) is not dict:
        raise JournalValidationError("command must be object")
    operation = value.get("operation")
    if operation not in ("prepare", "start", "stop"):
        raise JournalValidationError("only mutating MS-06 commands have intents")
    fields = _BINDING_FIELDS | {"expected_generation", "action_id", "operation"}
    if operation == "start":
        fields |= {"utc_start", "duration_seconds"}
    if operation == "stop":
        fields |= {"mode", "pending_action_ids"}
        # MS-06 stop includes pending IDs; allow omission for internal cleanup.
        if "pending_action_ids" not in value:
            fields.remove("pending_action_ids")
    _keys(value, fields)
    if type(value["gpu_id"]) is not int or any(value[k] != owner[k] for k in _BINDING_FIELDS):
        raise JournalValidationError("command owner mismatch")
    _text(value["action_id"])
    generation = value["expected_generation"]
    if generation != owner["generation"] and not (generation is None and operation != "start"):
        raise JournalValidationError("command generation mismatch")
    if operation == "start":
        # Preserve the controller's original Observation strings, including
        # picosecond duration precision. Validation must not normalize intent.
        try:
            _validate_utc(value["utc_start"], "utc_start")
            _duration_ps(value["duration_seconds"], "duration_seconds")
        except ValueError as error:
            raise JournalValidationError("invalid Observation schedule") from error
    if operation == "stop":
        if value["mode"] not in ("graceful", "force"):
            raise JournalValidationError("invalid stop mode")
        pending = value.get("pending_action_ids", [])
        if type(pending) is not list:
            raise JournalValidationError("pending_action_ids must be list")
        for action_id in pending:
            _text(action_id)
        if len(set(pending)) != len(pending):
            raise JournalValidationError("duplicate pending action ID")
    return value


def _report(owner, action_id, value):
    value = json.loads(_json(value))
    if type(value) is not dict:
        raise JournalValidationError("report must be object")
    if (type(value.get("gpu_id")) is not int
            or any(value.get(k) != owner[k] for k in _OWNER_FIELDS)):
        raise JournalValidationError("report owner/action mismatch")
    _text(value.get("action_id"))
    if type(value.get("outcome")) is not str or value["outcome"] not in _OUTCOMES:
        raise JournalValidationError("invalid action outcome")
    if value["action_id"] != action_id:
        pending = value.get("action_outcomes")
        if type(pending) is not dict or action_id not in pending:
            raise JournalValidationError("query does not resolve requested action")
        if type(pending[action_id]) is not str or pending[action_id] not in _OUTCOMES:
            raise JournalValidationError("invalid pending action outcome")
    return value


def _outcome(action_id, report):
    return report["outcome"] if report["action_id"] == action_id else report["action_outcomes"][action_id]


def _conflict(left, right):
    a, b = left["resources"], right["resources"]
    # Enumeration changes cannot authorize a second live owner of the same slot.
    if (left["owner"]["server_id"], left["owner"]["gpu_id"]) == (
            right["owner"]["server_id"], right["owner"]["gpu_id"]):
        return True
    if a["gpu_uuid"] == b["gpu_uuid"] or set(a["ipc_keys"]) & set(b["ipc_keys"]):
        return True
    if {c["logical_cpu"] for c in a["cpus"]} & {c["logical_cpu"] for c in b["cpus"]}:
        return True
    if {(c["socket_id"], c["core_id"]) for c in a["cpus"]} & {
            (c["socket_id"], c["core_id"]) for c in b["cpus"]}:
        return True
    p, q = Path(a["run_dir"]), Path(b["run_dir"])
    if p == q or p in q.parents or q in p.parents:
        return True
    f, g = a["flow"], b["flow"]
    if f["physical_port"] == g["physical_port"]:
        if all(f["match"][k] == g["match"][k] for k in set(f["match"]) & set(g["match"])):
            return True
    return False


def _cleanup(owner, resources, value):
    try:
        value = json.loads(_json(value))
        _keys(value, {"owner", "resources", "processes_gone", "descendants_gone", "resources_gone"})
        if _owner(value["owner"]) != owner or _resources(value["resources"]) != resources:
            raise JournalValidationError("cleanup owner/resources mismatch")
        if any(value[k] is not True for k in ("processes_gone", "descendants_gone", "resources_gone")):
            raise JournalValidationError("cleanup is incomplete")
        value["resources"] = resources
        return value
    except JournalValidationError as error:
        raise CleanupIncompleteError(str(error)) from error


class HostJournal:
    def __init__(self, path):
        self._connection = None
        try:
            self.path = Path(path).absolute()
            if "\x00" in str(self.path):
                raise ValueError("embedded null byte")
        except (TypeError, ValueError) as error:
            raise JournalValidationError("invalid database path") from error
        try:
            try:
                descriptor = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                new = False
            else:
                os.close(descriptor)
                new = True
            self._connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True,
                                               isolation_level=None, timeout=10)
            if new:
                with self._transaction():
                    self._connection.execute(_CREATE_TABLE)
                    self._connection.execute("PRAGMA user_version = 1")
            with self._transaction():
                objects = self._connection.execute(
                    "SELECT type, name, sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'"
                ).fetchall()
                version = self._connection.execute("PRAGMA user_version").fetchone()[0]
                if version != 1 or objects != [("table", "owners", _CREATE_TABLE)]:
                    raise JournalDatabaseError("unknown host journal schema/version")
                if self._connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise JournalCorruptionError("host journal integrity check failed")
                self._read_all()
        except BaseException as error:
            self.close()
            if isinstance(error, (sqlite3.Error, OSError)):
                raise JournalDatabaseError("cannot open host journal: {}".format(error)) from error
            raise

    @contextmanager
    def _transaction(self, write=True):
        if self._connection is None:
            raise JournalClosedError("host journal is closed")
        try:
            self._connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield
            self._connection.commit()
        except BaseException as error:
            try:
                self._connection.rollback()
            except sqlite3.Error as rollback_error:
                self.close()
                raise JournalDatabaseError("journal rollback failed") from rollback_error
            if isinstance(error, sqlite3.Error):
                raise JournalDatabaseError("host journal transaction failed: {}".format(error)) from error
            raise

    def _read_all(self):
        records = {}
        seen_actions = set()
        try:
            for identity, payload in self._connection.execute("SELECT identity, document FROM owners"):
                record = json.loads(payload, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
                _json(record)
                _keys(record, {"owner", "resources", "released", "cleanup_evidence", "actions"})
                binding = _owner(record["owner"])
                if identity != _json(binding) or _resources(record["resources"]) != record["resources"]:
                    raise JournalValidationError("noncanonical stored claim")
                if type(record["released"]) is not bool or type(record["actions"]) is not dict:
                    raise JournalValidationError("invalid stored status/actions")
                for action_id, action in record["actions"].items():
                    _keys(action, {"command", "outcome", "report"})
                    cmd = _command(binding, action["command"])
                    if action_id != cmd["action_id"] or action_id in seen_actions:
                        raise JournalValidationError("invalid/duplicate stored action identity")
                    seen_actions.add(action_id)
                    if action["report"] is None:
                        if action["outcome"] != "UNKNOWN":
                            raise JournalValidationError("intent without report must be UNKNOWN")
                    elif _outcome(action_id, _report(binding, action_id, action["report"])) != action["outcome"]:
                        raise JournalValidationError("stored outcome mismatch")
                if record["released"]:
                    _cleanup(binding, record["resources"], record["cleanup_evidence"])
                    if any(a["outcome"] == "UNKNOWN" for a in record["actions"].values()):
                        raise JournalValidationError("released unresolved action")
                elif record["cleanup_evidence"] is not None:
                    raise JournalValidationError("active claim has release evidence")
                if not record["released"] and any(
                        not prior["released"] and _conflict(record, prior) for prior in records.values()):
                    raise JournalValidationError("conflicting stored active claims")
                records[identity] = record
        except (ValueError, TypeError, RecursionError, CleanupIncompleteError) as error:
            raise JournalCorruptionError("invalid host journal document: {}".format(error)) from error
        return records

    def _find(self, records, owner):
        record = records.get(_json(owner))
        if record is None:
            raise OwnerNotFoundError("exact owner binding not recorded")
        return record

    def _save(self, record):
        self._connection.execute("UPDATE owners SET document = ? WHERE identity = ?",
                                 (_json(record), _json(record["owner"])))

    def claim(self, owner, resources):
        owner, resources = _owner(owner), _resources(resources)
        with self._transaction():
            records = self._read_all()
            identity = _json(owner)
            if identity in records:
                record = records[identity]
                if record["released"] or record["resources"] != resources:
                    raise OwnershipConflictError("changed/released owner cannot claim")
            else:
                record = dict(owner=owner, resources=resources, released=False,
                              cleanup_evidence=None, actions={})
                if any(not prior["released"] and _conflict(record, prior) for prior in records.values()):
                    raise OwnershipConflictError("host resource already claimed")
                self._connection.execute("INSERT INTO owners VALUES (?, ?)", (identity, _json(record)))
        return record

    def load(self, owner):
        owner = _owner(owner)
        with self._transaction(write=False):
            return self._find(self._read_all(), owner)

    def begin_action(self, owner, command):
        owner = _owner(owner)
        command = _command(owner, command)
        action_id = command["action_id"]
        with self._transaction():
            records = self._read_all()
            record = self._find(records, owner)
            for prior in records.values():
                if action_id in prior["actions"]:
                    action = prior["actions"][action_id]
                    if prior["owner"] != owner or _json(action["command"]) != _json(command):
                        raise ActionConflictError("action ID already binds different content/owner")
                    return dict(created=False, action=action)
            if record["released"]:
                raise OwnershipConflictError("released owner cannot begin new action")
            action = dict(command=command, outcome="UNKNOWN", report=None)
            record["actions"][action_id] = action
            self._save(record)
        return dict(created=True, action=action)

    def finish_action(self, owner, action_id, report):
        owner = _owner(owner)
        _json(action_id)
        _text(action_id)
        report = _report(owner, action_id, report)
        with self._transaction():
            record = self._find(self._read_all(), owner)
            action = record["actions"].get(action_id)
            if action is None:
                raise ActionConflictError("no intent for this owner/action")
            if action["outcome"] != "UNKNOWN":
                if _json(action["report"]) != _json(report):
                    raise ActionConflictError("resolved report is immutable")
            else:
                action.update(outcome=_outcome(action_id, report), report=report)
                self._save(record)
        return action

    def release(self, owner, cleanup_evidence):
        owner = _owner(owner)
        with self._transaction():
            record = self._find(self._read_all(), owner)
            evidence = _cleanup(owner, record["resources"], cleanup_evidence)
            if any(a["outcome"] == "UNKNOWN" for a in record["actions"].values()):
                raise CleanupIncompleteError("unresolved intents require reconciliation before release")
            if not record["released"]:
                record.update(released=True, cleanup_evidence=evidence)
                self._save(record)
        return record

    def close(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None
