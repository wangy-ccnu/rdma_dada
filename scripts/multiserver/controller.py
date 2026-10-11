"""Offline observation control with durable intent and an injected host adapter.

One serialized owner calls this interface. No process, network, ring or GPU
operations are implemented here. Reconciliation never replays prepare/start.
"""

import copy
import datetime
import hashlib
import json
import re
import uuid
from typing import Tuple

from .model import validate_global
from .store import RunNotFoundError, RunStore, StoreValidationError


_STATES = {"NEW", "PREPARING", "READY", "STARTING", "RUNNING", "DRAINING",
           "COMPLETED", "INTERRUPTED", "FAILED"}
_TERMINAL = {"COMPLETED", "INTERRUPTED", "FAILED"}
_OUTCOMES = {"APPLIED", "REJECTED", "UNKNOWN", "NOT_APPLIED"}
_EVIDENCE = {"target_data_complete", "output_published", "eod_complete"}
_RANK = {state: index for index, state in enumerate(
    ("NEW", "PREPARING", "READY", "STARTING", "RUNNING", "DRAINING"))}


class ControllerError(ValueError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__("{}: {}".format(code, detail))


def _text(value, field):
    if type(value) is not str or not value:
        raise ControllerError("INVALID_STRING", field + ": expected nonempty string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ControllerError("INVALID_STRING", field + ": expected Unicode scalars") from error
    return value


def _integer(value, field, minimum=0):
    if type(value) is not int or value < minimum:
        raise ControllerError("INVALID_INTEGER", field + ": invalid integer")
    return value


def _canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _key(instance) -> Tuple[str, int]:
    return instance["server_id"], instance["gpu_id"]


def _identity(instance):
    return {"server_id": instance["server_id"], "gpu_id": instance["gpu_id"]}


class ObservationController:
    def __init__(self, db_path, host_adapter, *, utc_now, monotonic_ns,
                 heartbeat_timeout_ns, drain_timeout_ns):
        self.heartbeat_timeout_ns = _integer(heartbeat_timeout_ns, "heartbeat_timeout_ns", 1)
        self.drain_timeout_ns = _integer(drain_timeout_ns, "drain_timeout_ns", 1)
        self._utc_now = utc_now
        self._monotonic_ns = monotonic_ns
        self._host = host_adapter
        self._seen = {}
        self._unreachable = set()
        self._drain_since = {}
        self._last_tick = None
        self._utc()
        self._mono()
        self._store = RunStore(db_path)

    def _utc(self):
        value = self._utc_now()
        if (not isinstance(value, datetime.datetime) or value.tzinfo is None
                or value.utcoffset() != datetime.timedelta(0)):
            raise ControllerError("INVALID_CLOCK", "utc_now must return aware UTC datetime")
        return value

    def _mono(self):
        value = _integer(self._monotonic_ns(), "monotonic_ns")
        if self._last_tick is not None and value < self._last_tick:
            raise ControllerError("INVALID_CLOCK", "monotonic_ns moved backwards")
        self._last_tick = value
        return value

    def _save(self, record):
        return self._store.save(record["run_id"], record["revision"], record["snapshot"])

    def register_run(self, run_id: str, bundle: dict, global_document: dict) -> dict:
        _text(run_id, "run_id")
        try:
            global_doc = validate_global(global_document)
            if type(bundle) is not dict or type(bundle.get("schema_version")) is not int:
                raise ControllerError("INVALID_BINDING", "expected bundle schema version 1")
            unsigned = copy.deepcopy(bundle)
            bundle_id = unsigned.pop("bundle_id")
            if bundle["schema_version"] != 1 or bundle_id != hashlib.sha256(_canonical(unsigned)).hexdigest():
                raise ControllerError("INVALID_BINDING", "bundle identity mismatch")
            if (bundle["observation_id"] != global_doc["observation"]["observation_id"]
                    or bundle["inputs"]["global-observation.json"] != hashlib.sha256(_canonical(global_doc)).hexdigest()
                    or bundle["time_grid"] != global_doc["time_grid"]
                    or bundle["monitoring"] != global_doc["monitoring"]):
                raise ControllerError("INVALID_BINDING", "bundle/global document mismatch")
            assignments = {_key(item): item for item in global_doc["assignments"]}
            instances = []
            seen = set()
            for source in bundle["instances"]:
                key = (_text(source["server_id"], "server_id"), _integer(source["gpu_id"], "gpu_id"))
                if key in seen or source["assignment"] != assignments.get(key):
                    raise ControllerError("INVALID_BINDING", "duplicate or mismatched assignment")
                seen.add(key)
                digest = source["observation_sha256"]
                if type(digest) is not str or not re.fullmatch("[0-9a-f]{64}", digest):
                    raise ControllerError("INVALID_BINDING", "invalid observation hash")
                instances.append(dict(_identity(source), assignment=copy.deepcopy(source["assignment"]),
                                      observation_sha256=digest, state="NEW", generation=None,
                                      report=None, cleanup="NOT_REQUIRED", possibly_owned=False,
                                      failed=False, errors=[]))
            if seen != set(assignments):
                raise ControllerError("INVALID_BINDING", "participant set mismatch")
            binding = {"bundle": copy.deepcopy(bundle), "global_document": global_doc}
            try:
                record = self._store.load(run_id)
            except RunNotFoundError:
                self._store.create(run_id, {
                    "binding": binding, "instances": instances, "requests": {}, "actions": {},
                    "stop_intent": None, "abort_startup": False, "running_observed": False,
                })
            else:
                if record["snapshot"]["binding"] != binding:
                    raise ControllerError("BINDING_CONFLICT", "run {!r} is immutable".format(run_id))
        except (KeyError, TypeError, ValueError, StoreValidationError) as error:
            if isinstance(error, ControllerError):
                raise
            raise ControllerError("INVALID_BINDING", str(error)) from error
        return self.status(run_id)

    @staticmethod
    def _request(request):
        if type(request) is not dict:
            raise ControllerError("INVALID_REQUEST", "expected command object")
        required = {"schema_version", "request_id", "run_id", "operation"}
        operation = request.get("operation")
        if operation == "stop":
            required.add("mode")
        if set(request) != required or operation not in ("prepare", "start", "stop"):
            raise ControllerError("INVALID_REQUEST", "unknown/missing command fields or operation")
        if type(request["schema_version"]) is not int or request["schema_version"] != 1:
            raise ControllerError("INVALID_REQUEST", "expected schema_version 1")
        _text(request["request_id"], "request_id")
        _text(request["run_id"], "run_id")
        if operation == "stop" and request["mode"] not in ("graceful", "force"):
            raise ControllerError("INVALID_REQUEST", "invalid stop mode")
        return copy.deepcopy(request)

    def _fresh(self, run_id, instance, now):
        marker = (run_id, _key(instance))
        timestamp = self._seen.get(marker)
        age = None if timestamp is None else now - timestamp
        return marker not in self._unreachable and age is not None and age < self.heartbeat_timeout_ns, age

    def _ready(self, run_id, document, now, *, dispatched=False, dispatch_target=None):
        for instance in document["instances"]:
            allowed = instance["state"] == "READY"
            if dispatched and instance["state"] in ("STARTING", "RUNNING"):
                allowed = any(action["operation"] == "start" and action["outcome"] == "APPLIED"
                              and tuple(action["instance"]) == _key(instance)
                              for action in document["actions"].values())
            if dispatch_target == _key(instance) and instance["report"] is not None:
                allowed = instance["report"]["state"] == "READY"
            if not allowed or not self._fresh(run_id, instance, now)[0]:
                return False
        return True

    @staticmethod
    def _clean(instance, document):
        if instance["possibly_owned"] or instance["cleanup"] not in ("CONFIRMED", "NOT_REQUIRED"):
            return False
        if instance["state"] == "NEW":
            return document["stop_intent"] is not None or document["abort_startup"]
        if instance["state"] not in _TERMINAL:
            return False
        return (instance["state"] != "COMPLETED" or document["stop_intent"] is not None
                or document["abort_startup"] or all(instance["report"]["evidence"].values()))

    def status(self, run_id: str) -> dict:
        record = self._store.load(run_id)
        document = record["snapshot"]
        now = self._mono()
        instances = copy.deepcopy(document["instances"])
        unknown, failed = [], []
        for instance in instances:
            fresh, age = self._fresh(run_id, instance, now)
            instance.update(fresh=fresh, age_ns=age)
            if not fresh:
                unknown.append(_identity(instance))
            if instance["failed"]:
                failed.append(_identity(instance))
        finished = all(self._clean(instance, document) for instance in instances)
        result = None
        if finished:
            phase = "FINISHED"
            if document["stop_intent"] is not None:
                result = "INTERRUPTED"
            elif document["abort_startup"]:
                result = "FAILED"
            elif failed:
                result = "PARTIAL_FAILED" if any(i["state"] == "COMPLETED" for i in instances) else "FAILED"
            else:
                result = "PASS" if all(i["state"] == "COMPLETED" for i in instances) else "FAILED"
        elif document["stop_intent"] is not None or document["abort_startup"]:
            phase = "DRAINING"
        elif (any(a["operation"] == "start" for a in document["actions"].values())
              and any(i["state"] in ("NEW", "PREPARING", "READY", "STARTING") for i in instances)):
            phase = "STARTING"
        elif document["running_observed"]:
            phase = "RUNNING" if any(i["state"] == "RUNNING" for i in instances) else "DRAINING"
        elif any(a["operation"] == "start" for a in document["actions"].values()):
            phase = "STARTING"
        elif self._ready(run_id, document, now):
            phase = "READY"
        elif document["actions"]:
            phase = "PREPARING"
        else:
            phase = "NEW"
        return {
            "run_id": run_id, "revision": record["revision"],
            "bundle_id": document["binding"]["bundle"]["bundle_id"],
            "observation_id": document["binding"]["bundle"]["observation_id"],
            "phase": phase, "health": "DEGRADED" if failed else "UNKNOWN" if unknown else "OK",
            "result": result, "instances": instances, "failed_instances": failed,
            "unknown_instances": unknown, "stop_intent": copy.deepcopy(document["stop_intent"]),
            "missing_frequency": [dict(_identity(i), assignment=copy.deepcopy(i["assignment"]),
                                       data_gap_start_sample=None, data_gap_end_sample=None)
                                  for i in instances if i["failed"]],
        }

    def _new_action(self, document, run_id, instance, operation, request_id, *, mode=None, reason="request"):
        identity = [run_id, _key(instance), operation, request_id, mode, reason, len(document["actions"])]
        action_id = "action-" + hashlib.sha256(_canonical(identity)).hexdigest()
        document["actions"][action_id] = {
            "instance": list(_key(instance)), "operation": operation, "mode": mode,
            "reason": reason, "request_id": request_id, "dispatch": "INTENT", "outcome": "UNKNOWN",
        }
        if request_id in document["requests"]:
            document["requests"][request_id]["action_ids"].append(action_id)
        return action_id

    def _operation_result(self, run_id, request_id):
        document = self._store.load(run_id)["snapshot"]
        request = document["requests"][request_id]
        actions = [document["actions"][key] for key in request["action_ids"]]
        if any(action["outcome"] in ("REJECTED", "NOT_APPLIED")
               and action["dispatch"] != "CANCELLED" for action in actions):
            outcome = "REJECTED"
        elif any(action["dispatch"] == "CANCELLED" for action in actions):
            outcome = "CANCELLED"
        elif any(action["dispatch"] != "RESOLVED" or action["outcome"] == "UNKNOWN" for action in actions):
            outcome = "UNKNOWN"
        else:
            outcome = "APPLIED"
        return {"operation_id": request["operation_id"], "request_id": request_id,
                "outcome": outcome, "status": self.status(run_id)}

    def execute(self, request: dict) -> dict:
        request = self._request(request)
        run_id, request_id, operation = request["run_id"], request["request_id"], request["operation"]
        record = self._store.load(run_id)
        document = record["snapshot"]
        existing = document["requests"].get(request_id)
        if existing is not None:
            if existing["request"] != request:
                raise ControllerError("REQUEST_CONFLICT", "request_id reused with different content")
            return self._operation_result(run_id, request_id)
        if operation in ("prepare", "start") and (document["stop_intent"] or document["abort_startup"]):
            raise ControllerError("STOPPED", "run cannot prepare/start after stop or startup failure")
        if operation == "prepare" and any(a["operation"] == "prepare" for a in document["actions"].values()):
            raise ControllerError("ALREADY_PREPARED", "a new request cannot replay prepare")
        if operation == "start":
            if any(a["operation"] == "start" for a in document["actions"].values()):
                raise ControllerError("ALREADY_STARTED", "a new request cannot replay start")
            if not self._ready(run_id, document, self._mono()):
                raise ControllerError("NOT_READY", "all participants must have fresh READY reports")
            start = datetime.datetime.strptime(document["binding"]["global_document"]["observation"]["utc_start"],
                                               "%Y-%m-%d-%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
            if self._utc() > start:
                raise ControllerError("MISSED_START", "configured UTC start has passed")
        document["requests"][request_id] = {
            "request": request, "operation_id": "operation-" + hashlib.sha256(_canonical(request)).hexdigest(),
            "action_ids": [],
        }
        if operation == "stop":
            finished = all(self._clean(instance, document) for instance in document["instances"])
            if not finished and (document["stop_intent"] is None or request["mode"] == "force"):
                document["stop_intent"] = {"mode": request["mode"], "request_id": request_id}
            now = self._mono()
            for instance in document["instances"]:
                if instance["possibly_owned"]:
                    self._drain_since.setdefault((run_id, _key(instance)), now)
        else:
            for instance in document["instances"]:
                self._new_action(document, run_id, instance, operation, request_id)
        record = self._save(record)
        for action_id in list(record["snapshot"]["requests"][request_id]["action_ids"]):
            self._dispatch(run_id, action_id)
        self._advance_cleanup(run_id)
        return self._operation_result(run_id, request_id)

    @staticmethod
    def _instance(document, key):
        return next(i for i in document["instances"] if _key(i) == tuple(key))

    def _command(self, run_id, document, instance, action_id, operation, mode=None):
        command = dict(_identity(instance), run_id=run_id, bundle_id=document["binding"]["bundle"]["bundle_id"],
                       observation_sha256=instance["observation_sha256"], expected_generation=instance["generation"],
                       action_id=action_id, operation=operation)
        if operation == "start":
            observation = document["binding"]["global_document"]["observation"]
            command.update(utc_start=observation["utc_start"], duration_seconds=observation["duration_seconds"])
        if operation == "stop":
            command["mode"] = mode
            command["pending_action_ids"] = [key for key, action in document["actions"].items()
                                             if tuple(action["instance"]) == _key(instance)
                                             and action["dispatch"] in ("INTENT", "UNKNOWN")]
        return command

    def _dispatch(self, run_id, action_id, *, retry_cleanup=False):
        record = self._store.load(run_id)
        document = record["snapshot"]
        action = document["actions"][action_id]
        operation = action["operation"]
        if action["dispatch"] != "INTENT" and not (
                retry_cleanup and operation == "stop" and (
                    action["dispatch"] == "UNKNOWN" or
                    (action["dispatch"] == "RESOLVED" and action["outcome"] == "NOT_APPLIED"))):
            return
        instance = self._instance(document, action["instance"])
        if operation in ("prepare", "start"):
            suppressed = document["stop_intent"] is not None or document["abort_startup"]
            if operation == "start" and not self._ready(run_id, document, self._mono(), dispatched=True):
                suppressed = True
            if suppressed:
                action["dispatch"] = "CANCELLED"
                self._save(record)
                return
            if operation == "start":
                start = datetime.datetime.strptime(document["binding"]["global_document"]["observation"]["utc_start"],
                                                   "%Y-%m-%d-%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
                if self._utc() > start:
                    document["abort_startup"] = True
                    action["dispatch"] = "CANCELLED"
                    self._save(record)
                    return
            instance["state"] = "PREPARING" if operation == "prepare" else "STARTING"
            instance["possibly_owned"] = True
            instance["cleanup"] = "UNKNOWN"
        elif instance["state"] not in _TERMINAL:
            instance["state"] = "DRAINING"
        action.update(dispatch="UNKNOWN", outcome="UNKNOWN")
        self._save(record)
        now = self._mono()
        # Persistence may have blocked, or an authorized stop may have arrived
        # at the dispatch boundary. Recheck current intent, freshness and UTC.
        record = self._store.load(run_id)
        document = record["snapshot"]
        action = document["actions"][action_id]
        instance = self._instance(document, action["instance"])
        if operation in ("prepare", "start"):
            suppressed = document["stop_intent"] is not None or document["abort_startup"]
            if operation == "start":
                suppressed = suppressed or not self._ready(
                    run_id, document, now, dispatched=True, dispatch_target=_key(instance))
                if self._utc() > start:
                    document["abort_startup"] = True
                    suppressed = True
            if suppressed:
                action["dispatch"] = "CANCELLED"
                self._save(record)
                return
        command = self._command(run_id, document, instance, action_id, operation, action["mode"])
        try:
            response = self._host.send(_key(instance), copy.deepcopy(command))
        except (TimeoutError, ConnectionError, OSError) as error:
            self._unreachable.add((run_id, _key(instance)))
            record = self._store.load(run_id)
            record["snapshot"]["actions"][action_id]["error"] = str(error)
            self._save(record)
            return
        self._accept(run_id, command, response)

    def _validate_report(self, document, instance, command, report):
        fields = {"run_id", "bundle_id", "server_id", "gpu_id", "observation_sha256", "action_id",
                  "outcome", "state", "generation", "sequence", "cleanup", "evidence", "error", "action_outcomes"}
        if type(report) is not dict or set(report) != fields:
            raise ControllerError("INVALID_REPORT", "report has missing/extra fields")
        if (type(report["gpu_id"]) is not int
                or any(report[name] != command[name] for name in
                       ("run_id", "bundle_id", "server_id", "gpu_id", "observation_sha256", "action_id"))):
            raise ControllerError("REPORT_IDENTITY", "report does not match command binding")
        _integer(report["sequence"], "report.sequence", 1)
        if (type(report["outcome"]) is not str or report["outcome"] not in _OUTCOMES
                or type(report["state"]) is not str or report["state"] not in _STATES):
            raise ControllerError("INVALID_REPORT", "unknown outcome or lifecycle state")
        generation = report["generation"]
        if generation is not None:
            _text(generation, "report.generation")
        elif report["state"] not in ("NEW", "FAILED", "INTERRUPTED"):
            raise ControllerError("REPORT_GENERATION", "active instance requires generation")
        if instance["generation"] is not None and generation != instance["generation"]:
            raise ControllerError("REPORT_GENERATION", "changed generation requires explicit resolution")
        if report["cleanup"] not in ("PENDING", "CONFIRMED", "UNKNOWN"):
            raise ControllerError("INVALID_REPORT", "invalid cleanup status")
        if (type(report["evidence"]) is not dict or set(report["evidence"]) != _EVIDENCE
                or any(type(value) is not bool for value in report["evidence"].values())):
            raise ControllerError("INVALID_REPORT", "terminal evidence must contain three booleans")
        if report["error"] is not None:
            _text(report["error"], "report.error")
        if type(report["action_outcomes"]) is not dict:
            raise ControllerError("INVALID_REPORT", "action_outcomes must be object")
        permitted = set(command.get("pending_action_ids", []))
        if (set(report["action_outcomes"]) - permitted
                or any(type(v) is not str or v not in _OUTCOMES for v in report["action_outcomes"].values())):
            raise ControllerError("INVALID_REPORT", "unrequested action outcome")
        observation = {name: copy.deepcopy(report[name]) for name in
                       ("run_id", "bundle_id", "server_id", "gpu_id", "observation_sha256",
                        "state", "generation", "sequence", "cleanup", "evidence", "error")}
        previous = instance["report"]
        if previous is not None and report["sequence"] == previous["sequence"] and observation != previous:
            raise ControllerError("REPORT_SEQUENCE", "contradictory report at identical sequence")
        if previous is not None and report["sequence"] < previous["sequence"]:
            return observation, False
        newer = previous is None or report["sequence"] > previous["sequence"]
        state = report["state"]
        outcomes = report["action_outcomes"].copy()
        if command["operation"] != "status":
            outcomes[command["action_id"]] = report["outcome"]
        # Equal-sequence envelopes may resolve actions, so they must satisfy
        # the same action/state and start-authority checks as newer reports.
        if state in ("STARTING", "RUNNING", "COMPLETED") and any(
                document["actions"][action_id]["operation"] == "start"
                and outcome in ("REJECTED", "NOT_APPLIED")
                for action_id, outcome in outcomes.items()):
            raise ControllerError("ACTION_STATE_CONFLICT", "active state contradicts unapplied start")
        if state in ("STARTING", "RUNNING", "COMPLETED") and not any(
                action["operation"] == "start" and tuple(action["instance"]) == _key(instance)
                and action["dispatch"] in ("UNKNOWN", "RESOLVED")
                and action["outcome"] not in ("REJECTED", "NOT_APPLIED")
                for action in document["actions"].values()):
            raise ControllerError("UNAUTHORIZED_START", "no dispatched start intent for participant")
        if newer:
            if instance["state"] in _TERMINAL and state != instance["state"]:
                raise ControllerError("TERMINAL_REGRESSION", "terminal lifecycle cannot regress")
            if (instance["state"] not in _TERMINAL and state not in _TERMINAL
                    and _RANK[state] < _RANK[instance["state"]]
                    and report["outcome"] not in ("REJECTED", "NOT_APPLIED")
                    and not any(value == "NOT_APPLIED" for value in report["action_outcomes"].values())):
                raise ControllerError("STATE_REGRESSION", "lifecycle cannot regress")
        return observation, newer

    def _accept(self, run_id, command, report):
        record = self._store.load(run_id)
        document = record["snapshot"]
        instance = self._instance(document, (command["server_id"], command["gpu_id"]))
        observation, newer = self._validate_report(document, instance, command, report)
        if instance["report"] is not None and report["sequence"] < instance["report"]["sequence"]:
            return  # stale reports supply no action, progress or cleanup facts
        outcomes = report["action_outcomes"].copy()
        if command["operation"] != "status":
            outcomes[command["action_id"]] = report["outcome"]
        for action_id, outcome in outcomes.items():
            action = document["actions"][action_id]
            if tuple(action["instance"]) != _key(instance):
                raise ControllerError("REPORT_IDENTITY", "action belongs to a different participant")
            if action["dispatch"] == "RESOLVED" and outcome not in ("UNKNOWN", action["outcome"]):
                raise ControllerError("ACTION_CONFLICT", "action outcome changed")
            if outcome == "UNKNOWN":
                continue
            action.update(outcome=outcome, dispatch="RESOLVED")
            if action["operation"] in ("prepare", "start") and outcome in ("REJECTED", "NOT_APPLIED"):
                if outcome == "NOT_APPLIED" and document["stop_intent"] is not None:
                    action["dispatch"] = "CANCELLED"
                    continue
                document["abort_startup"] = True
                instance["failed"] = True
                detail = report["error"] or "{} {}".format(action["operation"], outcome)
                if detail not in instance["errors"]:
                    instance["errors"].append(detail)
        if newer:
            if instance["generation"] is None and report["generation"] is not None:
                if command["operation"] != "prepare" and not any(a["operation"] == "prepare" and a["outcome"] == "APPLIED"
                           and tuple(a["instance"]) == _key(instance) for a in document["actions"].values()):
                    raise ControllerError("REPORT_GENERATION", "generation needs matching applied prepare")
                instance["generation"] = report["generation"]
            instance.update(state=report["state"], report=observation, cleanup=report["cleanup"])
            if report["state"] in _TERMINAL or report["state"] == "NEW":
                if report["cleanup"] == "CONFIRMED":
                    instance["possibly_owned"] = False
            if report["state"] == "RUNNING":
                document["running_observed"] = True
            unexpected_interruption = (report["state"] == "INTERRUPTED"
                                       and document["stop_intent"] is None
                                       and not document["abort_startup"])
            if report["state"] == "FAILED" or unexpected_interruption:
                instance["failed"] = True
                detail = report["error"] or ("host reported unexpected INTERRUPTED"
                                             if unexpected_interruption else "host reported FAILED")
                if detail not in instance["errors"]:
                    instance["errors"].append(detail)
                if not document["running_observed"]:
                    document["abort_startup"] = True
        # Preserve nested stop writes: this record was loaded after the adapter
        # returned, rather than saving the pre-dispatch document over them.
        if document != self._store.load(run_id)["snapshot"]:
            self._save(record)
        if newer:
            marker = (run_id, _key(instance))
            self._seen[marker] = self._mono()
            self._unreachable.discard(marker)

    def _advance_cleanup(self, run_id):
        keys = [_key(i) for i in self._store.load(run_id)["snapshot"]["instances"]]
        for key in keys:
            record = self._store.load(run_id)
            document = record["snapshot"]
            instance = self._instance(document, key)
            if not instance["possibly_owned"] or self._clean(instance, document):
                continue
            intent = document["stop_intent"]
            if intent is not None:
                mode, reason, request_id = intent["mode"], "human", intent["request_id"]
                since = self._drain_since.setdefault((run_id, key), self._mono())
                if mode == "graceful" and self._mono() - since >= self.drain_timeout_ns:
                    mode = "force"
            elif document["abort_startup"]:
                mode, reason, request_id = "force", "rollback", None
            elif instance["failed"]:
                mode, reason, request_id = "force", "failed-instance", None
            else:
                continue
            existing = next((action_id for action_id, action in document["actions"].items()
                             if tuple(action["instance"]) == key and action["operation"] == "stop"
                             and action["mode"] == mode and action["reason"] == reason), None)
            if existing is None:
                existing = self._new_action(document, run_id, instance, "stop", request_id, mode=mode, reason=reason)
                self._save(record)
            self._dispatch(run_id, existing, retry_cleanup=True)

    def reconcile(self, run_id: str) -> dict:
        keys = [_key(i) for i in self._store.load(run_id)["snapshot"]["instances"]]
        for key in keys:
            document = self._store.load(run_id)["snapshot"]
            instance = self._instance(document, key)
            command = self._command(run_id, document, instance, "query-" + uuid.uuid4().hex, "status")
            command["pending_action_ids"] = [action_id for action_id, action in document["actions"].items()
                                             if tuple(action["instance"]) == key
                                             and action["dispatch"] in ("INTENT", "UNKNOWN")]
            try:
                response = self._host.send(key, copy.deepcopy(command))
            except (TimeoutError, ConnectionError, OSError):
                self._unreachable.add((run_id, key))
                continue
            self._accept(run_id, command, response)
        self._advance_cleanup(run_id)
        return self.status(run_id)

    def close(self) -> None:
        self._store.close()
