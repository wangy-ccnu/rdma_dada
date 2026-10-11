import copy
import datetime
import hashlib
import json
import pathlib
import tempfile
import unittest
import multiserver_deployment_test as deployment_tests

from multiserver_fixture import write_example_inputs
from scripts.multiserver.bundle import build_bundle
from scripts.multiserver.controller import ControllerError, ObservationController
from scripts.multiserver.store import RunStore


ROOT = pathlib.Path(__file__).resolve().parents[1]
KEYS = [("node-a", 0), ("node-b", 0)]
EVIDENCE = {"target_data_complete": True, "output_published": True, "eod_complete": True}


def reidentify(bundle):
    document = copy.deepcopy(bundle)
    document.pop("bundle_id", None)
    bundle["bundle_id"] = hashlib.sha256((json.dumps(
        document, sort_keys=True, ensure_ascii=True, allow_nan=False,
        separators=(",", ":")) + "\n").encode("utf-8")).hexdigest()


class FakeClock:
    def __init__(self):
        self.utc = datetime.datetime(2026, 9, 11, 23, 59, tzinfo=datetime.timezone.utc)
        self.tick = 1000
        self.hook = None

    def utc_now(self):
        return self.utc

    def monotonic_ns(self):
        if self.hook is not None:
            self.hook()
        return self.tick


class FakeHosts:
    """Real stateful fake; no OS/process/transport effects."""
    def __init__(self, bundle):
        self.bundle = bundle
        self.hosts = {
            (instance["server_id"], instance["gpu_id"]): {
                "state": "NEW", "generation": None, "sequence": 0,
                "cleanup": "CONFIRMED", "evidence": dict.fromkeys(EVIDENCE, False),
                "error": None, "observation_sha256": instance["observation_sha256"],
            } for instance in bundle["instances"]
        }
        self.calls = []
        self.applied = []
        self.actions = {}
        self.reject = set()
        self.unreachable = set()
        self.freeze_sequence = set()
        self.before = {}
        self.after = {}
        self.transform = {}
        self.auto_drain = True
        self.before_start_states = []

    def set_state(self, key, state, *, cleanup="PENDING", evidence=None, error=None):
        self.hosts[key].update(state=state, cleanup=cleanup, error=error)
        if evidence is not None:
            self.hosts[key]["evidence"] = copy.deepcopy(evidence)

    def send(self, key, command):
        self.calls.append((key, copy.deepcopy(command)))
        operation = command["operation"]
        pair = (key, operation)
        hook = self.before.pop(pair, None)
        if hook is not None:
            hook(command)
        if key in self.unreachable:
            raise TimeoutError("fake host unreachable")
        host = self.hosts[key]
        action_id = command["action_id"]
        if operation == "start":
            self.before_start_states.append([self.hosts[item]["state"] for item in KEYS])
        if operation != "status" and action_id not in self.actions:
            if pair in self.reject:
                self.actions[action_id] = "REJECTED"
            else:
                self.actions[action_id] = "APPLIED"
                self.applied.append((key, operation, action_id))
                if operation == "prepare":
                    host.update(state="READY", generation="generation-1", cleanup="PENDING")
                elif operation == "start":
                    host.update(state="RUNNING", cleanup="PENDING")
                elif operation == "stop":
                    if command["mode"] == "force" or self.auto_drain:
                        state = "FAILED" if host["state"] == "FAILED" else "INTERRUPTED"
                        host.update(state=state, cleanup="CONFIRMED")
                    else:
                        host.update(state="DRAINING", cleanup="PENDING")
        if key not in self.freeze_sequence:
            host["sequence"] += 1
        response = copy.deepcopy(host)
        response.update({
            "run_id": command["run_id"], "bundle_id": command["bundle_id"],
            "server_id": key[0], "gpu_id": key[1], "action_id": action_id,
            "outcome": "APPLIED" if operation == "status" else self.actions[action_id],
            "action_outcomes": {pending: self.actions.get(pending, "NOT_APPLIED")
                                for pending in command.get("pending_action_ids", [])},
        })
        if response["outcome"] == "REJECTED":
            response["error"] = "definite fake rejection"
        transform = self.transform.get(pair)
        if transform is not None:
            response = transform(response)
        hook = self.after.pop(pair, None)
        if hook is not None:
            hook(command)
        return response


class ObservationControllerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls.directory.name).resolve()
        global_path, deployments, inventory = write_example_inputs(root / "inputs")
        cls.global_document = json.loads(global_path.read_text())
        cls.bundle = build_bundle(global_path, deployments, root / "bundle", inventory_path=inventory)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = pathlib.Path(directory.name) / "runs.sqlite"
        self.clock = FakeClock()
        self.hosts = FakeHosts(self.bundle)
        self.controller = self.open_controller()

    def open_controller(self, **overrides):
        arguments = dict(utc_now=self.clock.utc_now, monotonic_ns=self.clock.monotonic_ns,
                         heartbeat_timeout_ns=100, drain_timeout_ns=200)
        arguments.update(overrides)
        controller = ObservationController(self.db, self.hosts, **arguments)
        self.addCleanup(controller.close)
        return controller

    def register(self):
        return self.controller.register_run("run-a", self.bundle, self.global_document)

    def request(self, operation, request_id=None, mode=None):
        request = {"schema_version": 1, "run_id": "run-a",
                   "request_id": request_id or operation + "-1", "operation": operation}
        if mode is not None:
            request["mode"] = mode
        return request

    def prepare(self):
        self.register()
        return self.controller.execute(self.request("prepare"))

    def start(self):
        self.prepare()
        return self.controller.execute(self.request("start"))

    def commands(self, operation):
        return [(key, command) for key, command in self.hosts.calls
                if command["operation"] == operation]

    def durable(self):
        store = RunStore(self.db)
        try:
            return store.load("run-a")
        finally:
            store.close()

    def test_real_two_shard_registration_is_new_and_binding_is_immutable(self):
        status = self.register()
        self.assertEqual((status["phase"], status["result"]), ("NEW", None))
        self.assertEqual([instance["assignment"]["nchan"] for instance in status["instances"]], [4, 3])
        self.assertEqual([instance["state"] for instance in status["instances"]], ["NEW", "NEW"])
        self.assertEqual(self.hosts.calls, [])
        before = self.durable()
        self.register()
        self.assertEqual(self.durable(), before)

    def test_prepare_all_ready_before_start_and_original_schedule_is_sent(self):
        result = self.prepare()
        self.assertEqual(result["status"]["phase"], "READY")
        result = self.controller.execute(self.request("start"))
        self.assertEqual(result["status"]["phase"], "RUNNING")
        self.assertEqual(self.hosts.before_start_states[0], ["READY", "READY"])
        self.assertEqual([key for key, _ in self.commands("start")], KEYS)
        for key, command in self.commands("start"):
            self.assertEqual(command["utc_start"], "2026-09-12-00:00:00")
            self.assertEqual(command["duration_seconds"], "1.048576")
            self.assertEqual(command["run_id"], "run-a")
            self.assertEqual(command["bundle_id"], self.bundle["bundle_id"])
            self.assertEqual((command["server_id"], command["gpu_id"]), key)
            self.assertEqual(command["expected_generation"], "generation-1")
            self.assertEqual(command["observation_sha256"], next(
                item["observation_sha256"] for item in self.bundle["instances"]
                if (item["server_id"], item["gpu_id"]) == key))

    def test_completion_requires_data_output_eod_and_cleanup_facts(self):
        self.start()
        for missing in list(EVIDENCE) + ["cleanup"]:
            with self.subTest(missing=missing):
                facts = EVIDENCE.copy()
                if missing in facts:
                    facts[missing] = False
                cleanup = "PENDING" if missing == "cleanup" else "CONFIRMED"
                for key in KEYS:
                    self.hosts.set_state(key, "COMPLETED", cleanup=cleanup, evidence=facts)
                status = self.controller.reconcile("run-a")
                self.assertNotEqual(status["phase"], "FINISHED")
                self.assertIsNone(status["result"])
        for key in KEYS:
            self.hosts.set_state(key, "COMPLETED", cleanup="CONFIRMED", evidence=EVIDENCE)
        status = self.controller.reconcile("run-a")
        self.assertEqual((status["phase"], status["result"]), ("FINISHED", "PASS"))

    def test_duplicate_commands_keep_operation_identity_and_send_no_second_action(self):
        self.register()
        for operation, mode in (("prepare", None), ("start", None), ("stop", "graceful")):
            request = self.request(operation, mode=mode)
            first = self.controller.execute(request)
            count = len(self.hosts.calls)
            second = self.controller.execute(copy.deepcopy(request))
            self.assertEqual(first["operation_id"], second["operation_id"])
            self.assertEqual(len(self.hosts.calls), count)
        self.assertEqual(self.controller.status("run-a")["result"], "INTERRUPTED")

    def test_reopen_invalidates_freshness_until_fresh_queries(self):
        self.start()
        self.controller.close()
        self.clock.tick = 0
        self.controller = self.open_controller()
        status = self.controller.status("run-a")
        self.assertEqual(status["health"], "UNKNOWN")
        self.assertEqual(len(status["unknown_instances"]), 2)
        self.assertEqual(len(self.commands("start")), 2)
        self.assertEqual(self.controller.reconcile("run-a")["health"], "OK")
        self.assertEqual(len(self.commands("start")), 2)

    def test_invalid_requests_rejected_without_state_or_host_mutation(self):
        self.register()
        invalid = [None, [], {}, self.request("launch"), self.request("prepare", mode="force"),
                   self.request("stop"), self.request("stop", mode="other")]
        for field, value in (("schema_version", True), ("schema_version", 2),
                             ("request_id", ""), ("request_id", True),
                             ("run_id", ""), ("run_id", False), ("request_id", "\ud800")):
            request = self.request("prepare")
            request[field] = value
            invalid.append(request)
        for field in ("targets", "command", "extra"):
            invalid.append(dict(self.request("prepare"), **{field: "caller-controlled"}))
        before = self.durable()
        for request in invalid:
            with self.subTest(request=ascii(request)):
                with self.assertRaises(ControllerError):
                    self.controller.execute(request)
                self.assertEqual(self.durable(), before)
                self.assertEqual(self.hosts.calls, [])

    def test_changed_same_id_request_fails_without_new_action(self):
        self.prepare()
        before = self.durable()
        with self.assertRaisesRegex(ControllerError, "REQUEST_CONFLICT"):
            self.controller.execute(self.request("start", "prepare-1"))
        self.assertEqual(self.durable(), before)
        self.assertEqual(self.commands("start"), [])

    def test_registration_checks_bundle_hash_global_hash_and_duplicate_instances(self):
        for edit, resign in ((lambda b: b.update(observation_id="wrong"), False),
                             (lambda b: b["inputs"].update({"global-observation.json": "0" * 64}), True),
                             (lambda b: b["instances"].__setitem__(1, copy.deepcopy(b["instances"][0])), True),
                             (lambda b: b["instances"][0].update(gpu_id=True), True),
                             (lambda b: b["instances"][0]["assignment"].update(nchan=3), True)):
            bundle = copy.deepcopy(self.bundle)
            edit(bundle)
            if resign:
                reidentify(bundle)
            with self.subTest(bundle_id=bundle["bundle_id"]):
                with self.assertRaises(ControllerError):
                    self.controller.register_run("invalid", bundle, self.global_document)
        self.register()
        bundle = copy.deepcopy(self.bundle)
        bundle["instances"][0]["observation_sha256"] = "0" * 64
        reidentify(bundle)
        before = self.durable()
        with self.assertRaisesRegex(ControllerError, "BINDING_CONFLICT"):
            self.controller.register_run("run-a", bundle, self.global_document)
        self.assertEqual(self.durable(), before)

    def test_start_refuses_static_new_or_stale_ready(self):
        self.register()
        with self.assertRaisesRegex(ControllerError, "NOT_READY"):
            self.controller.execute(self.request("start"))
        self.controller.execute(self.request("prepare"))
        self.clock.tick += 101
        before = self.durable()
        with self.assertRaisesRegex(ControllerError, "NOT_READY"):
            self.controller.execute(self.request("start"))
        self.assertEqual(self.durable(), before)
        self.assertEqual(self.commands("start"), [])

    def test_missed_schedule_refuses_start_and_elapsed_end_is_not_completion(self):
        self.prepare()
        self.clock.utc = datetime.datetime(2026, 9, 12, 0, 0, 1, tzinfo=datetime.timezone.utc)
        with self.assertRaisesRegex(ControllerError, "MISSED_START"):
            self.controller.execute(self.request("start"))
        self.assertEqual(self.commands("start"), [])
        self.clock.utc = datetime.datetime(2026, 9, 11, 23, 59, tzinfo=datetime.timezone.utc)
        self.controller.execute(self.request("start"))
        self.clock.utc += datetime.timedelta(days=1)
        status = self.controller.reconcile("run-a")
        self.assertEqual((status["phase"], status["result"]), ("RUNNING", None))

    def test_intent_and_uncertainty_are_durable_before_adapter_call(self):
        self.register()
        observed = []
        def check(command):
            snapshot = self.durable()["snapshot"]
            action = snapshot["actions"][command["action_id"]]
            self.assertEqual(action["dispatch"], "UNKNOWN")
            self.assertEqual(action["outcome"], "UNKNOWN")
            self.assertIn("prepare-1", snapshot["requests"])
            observed.append(command["action_id"])
        self.hosts.before[(KEYS[0], "prepare")] = check
        self.controller.execute(self.request("prepare"))
        self.assertEqual(len(observed), 1)

    def test_lost_start_reply_reconciles_after_reopen_without_second_start(self):
        self.prepare()
        def lose_reply(command):
            raise TimeoutError("reply lost after application")
        self.hosts.after[(KEYS[1], "start")] = lose_reply
        result = self.controller.execute(self.request("start"))
        self.assertEqual(result["outcome"], "UNKNOWN")
        self.assertEqual(len(self.commands("start")), 2)
        self.controller.close()
        self.clock.tick = 0
        self.controller = self.open_controller()
        self.assertEqual(self.controller.reconcile("run-a")["phase"], "RUNNING")
        retry = self.controller.execute(self.request("start"))
        self.assertEqual(retry["operation_id"], result["operation_id"])
        self.assertEqual(retry["outcome"], "APPLIED")
        self.assertEqual(len(self.commands("start")), 2)

    def test_crash_before_host_application_is_not_blindly_replayed(self):
        self.prepare()
        def crash(command):
            raise SystemExit("injected crash before host applies start")
        self.hosts.before[(KEYS[1], "start")] = crash
        with self.assertRaises(SystemExit):
            self.controller.execute(self.request("start"))
        self.controller.close()
        self.clock.tick = 0
        self.controller = self.open_controller()
        status = self.controller.reconcile("run-a")
        self.assertEqual(status["result"], "FAILED")
        self.assertEqual(len(self.commands("start")), 2)
        self.assertEqual(len([action for action in self.hosts.applied if action[1] == "start"]), 1)

    def test_crash_after_host_application_reconciles_without_duplicate_launch(self):
        self.prepare()
        def crash(command):
            raise SystemExit("injected crash after host applies start")
        self.hosts.after[(KEYS[1], "start")] = crash
        with self.assertRaises(SystemExit):
            self.controller.execute(self.request("start"))
        self.controller.close()
        self.clock.tick = 0
        self.controller = self.open_controller()
        self.assertEqual(self.controller.reconcile("run-a")["phase"], "RUNNING")
        self.assertEqual(len(self.commands("start")), 2)

    def test_prepare_rejection_rolls_back_only_possibly_owned_resources(self):
        self.hosts.reject.add((KEYS[1], "prepare"))
        self.prepare()
        self.assertEqual([key for key, _ in self.commands("stop")], [KEYS[0]])
        status = self.controller.status("run-a")
        self.assertEqual((status["phase"], status["result"]), ("FINISHED", "FAILED"))
        self.assertEqual(self.commands("start"), [])

    def test_partial_start_rejection_retains_unknown_rollback_target(self):
        self.prepare()
        self.hosts.reject.add((KEYS[1], "start"))
        self.hosts.after[(KEYS[1], "start")] = lambda command: self.hosts.unreachable.add(KEYS[0])
        self.controller.execute(self.request("start"))
        status = self.controller.status("run-a")
        self.assertNotEqual(status["phase"], "FINISHED")
        self.assertIsNone(status["result"])
        self.assertIn({"server_id": "node-a", "gpu_id": 0}, status["unknown_instances"])
        self.assertEqual([key for key, _ in self.commands("stop")], KEYS)

    def test_runtime_failure_cleans_only_failed_shard_and_retains_missing_frequency(self):
        self.start()
        self.hosts.set_state(KEYS[0], "FAILED", error="worker failed")
        status = self.controller.reconcile("run-a")
        self.assertEqual((status["phase"], status["health"], status["result"]), ("RUNNING", "DEGRADED", None))
        self.assertEqual([key for key, _ in self.commands("stop")], [KEYS[0]])
        self.assertEqual(status["missing_frequency"][0]["assignment"]["nchan"], 4)
        self.assertIsNone(status["missing_frequency"][0]["data_gap_start_sample"])
        self.hosts.set_state(KEYS[1], "COMPLETED", cleanup="CONFIRMED", evidence=EVIDENCE)
        status = self.controller.reconcile("run-a")
        self.assertEqual(status["result"], "PARTIAL_FAILED")
        self.assertEqual(len(status["failed_instances"]), 1)
        self.assertEqual(status["instances"][0]["errors"], ["worker failed"])

    def test_control_disconnection_sends_no_stop_and_never_claims_completion(self):
        self.start()
        self.hosts.unreachable.update(KEYS)
        status = self.controller.reconcile("run-a")
        self.assertEqual((status["phase"], status["health"], status["result"]), ("RUNNING", "UNKNOWN", None))
        self.assertEqual(self.commands("stop"), [])

    def test_human_stop_during_prepare_suppresses_later_prepare_and_all_starts(self):
        self.register()
        self.hosts.after[(KEYS[0], "prepare")] = lambda command: self.controller.execute(
            self.request("stop", mode="force"))
        self.controller.execute(self.request("prepare"))
        self.assertEqual([key for key, _ in self.commands("prepare")], [KEYS[0]])
        self.assertEqual(self.commands("start"), [])
        self.assertEqual(self.controller.status("run-a")["result"], "INTERRUPTED")

    def test_human_stop_during_start_targets_entire_observation_and_suppresses_pending_start(self):
        self.prepare()
        self.hosts.after[(KEYS[0], "start")] = lambda command: self.controller.execute(
            self.request("stop", mode="force"))
        self.controller.execute(self.request("start"))
        self.assertEqual([key for key, _ in self.commands("start")], [KEYS[0]])
        self.assertEqual([key for key, _ in self.commands("stop")], KEYS)
        self.assertEqual(self.controller.status("run-a")["result"], "INTERRUPTED")

    def test_graceful_deadline_forces_only_unclean_owned_instances(self):
        self.start()
        self.hosts.auto_drain = False
        self.controller.execute(self.request("stop", mode="graceful"))
        self.hosts.set_state(KEYS[0], "INTERRUPTED", cleanup="CONFIRMED")
        self.clock.tick += 199
        self.controller.reconcile("run-a")
        self.assertEqual([c["mode"] for _, c in self.commands("stop")], ["graceful", "graceful"])
        self.clock.tick += 1
        self.assertEqual(self.controller.reconcile("run-a")["result"], "INTERRUPTED")
        self.assertEqual([(key, c["mode"]) for key, c in self.commands("stop")],
                         [(KEYS[0], "graceful"), (KEYS[1], "graceful"), (KEYS[1], "force")])

    def test_reopen_drain_gets_fresh_bounded_window_without_old_monotonic_times(self):
        self.start()
        self.hosts.auto_drain = False
        self.controller.execute(self.request("stop", mode="graceful"))
        self.controller.close()
        self.clock.tick = 0
        self.controller = self.open_controller()
        self.controller.reconcile("run-a")
        self.clock.tick = 199
        self.controller.reconcile("run-a")
        self.assertEqual(len(self.commands("stop")), 2)
        self.clock.tick = 200
        self.assertEqual(self.controller.reconcile("run-a")["result"], "INTERRUPTED")
        self.assertEqual([c["mode"] for _, c in self.commands("stop")],
                         ["graceful", "graceful", "force", "force"])

    def test_identical_sequence_never_renews_freshness_and_status_is_read_only(self):
        self.start()
        before = self.durable()
        calls = len(self.hosts.calls)
        self.controller.status("run-a")
        self.assertEqual((self.durable(), len(self.hosts.calls)), (before, calls))
        self.hosts.freeze_sequence.update(KEYS)
        self.clock.tick += 101
        status = self.controller.reconcile("run-a")
        self.assertEqual(status["health"], "UNKNOWN")
        self.assertEqual(self.commands("stop"), [])

    def test_wrong_identity_or_generation_never_replaces_current_snapshot(self):
        self.start()
        for field, wrong, code in (("run_id", "wrong", "REPORT_IDENTITY"),
                                   ("bundle_id", "wrong", "REPORT_IDENTITY"),
                                   ("server_id", "wrong", "REPORT_IDENTITY"),
                                   ("gpu_id", True, "REPORT_IDENTITY"),
                                   ("observation_sha256", "0" * 64, "REPORT_IDENTITY"),
                                   ("generation", "generation-2", "REPORT_GENERATION")):
            before = self.durable()
            self.hosts.transform[(KEYS[0], "status")] = lambda reply, f=field, w=wrong: dict(reply, **{f: w})
            with self.subTest(field=field):
                with self.assertRaisesRegex(ControllerError, code):
                    self.controller.reconcile("run-a")
                self.assertEqual(self.durable(), before)

    def test_contradictory_same_sequence_and_terminal_regression_are_rejected(self):
        self.start()
        self.hosts.freeze_sequence.add(KEYS[0])
        self.hosts.set_state(KEYS[0], "FAILED", error="contradiction")
        with self.assertRaisesRegex(ControllerError, "REPORT_SEQUENCE"):
            self.controller.reconcile("run-a")
        self.hosts.freeze_sequence.clear()
        for key in KEYS:
            self.hosts.set_state(key, "COMPLETED", cleanup="CONFIRMED", evidence=EVIDENCE)
        self.assertEqual(self.controller.reconcile("run-a")["result"], "PASS")
        self.hosts.set_state(KEYS[0], "RUNNING")
        with self.assertRaisesRegex(ControllerError, "TERMINAL_REGRESSION"):
            self.controller.reconcile("run-a")

    def test_ready_peer_becoming_stale_before_dispatch_blocks_later_start(self):
        self.prepare()
        def age_peer(command):
            self.clock.tick += 101
        self.hosts.after[(KEYS[0], "start")] = age_peer
        self.controller.execute(self.request("start"))
        self.assertEqual([key for key, _ in self.commands("start")], [KEYS[0]])
        self.assertEqual(self.controller.status("run-a")["health"], "UNKNOWN")

    def test_policy_and_clocks_require_explicit_valid_types(self):
        for name, value in (("heartbeat_timeout_ns", True), ("heartbeat_timeout_ns", 0),
                            ("drain_timeout_ns", -1), ("drain_timeout_ns", 1.0),
                            ("monotonic_ns", lambda: True), ("monotonic_ns", lambda: -1),
                            ("utc_now", lambda: datetime.datetime(2026, 1, 1))):
            with self.subTest(name=name, value=value):
                with self.assertRaises(ControllerError):
                    self.open_controller(**{name: value})

    def test_arming_acknowledgement_allows_remaining_ready_peer_to_arm(self):
        self.prepare()
        def armed(reply):
            self.hosts.set_state(KEYS[0], "STARTING")
            return dict(reply, state="STARTING")
        self.hosts.transform[(KEYS[0], "start")] = armed
        self.controller.execute(self.request("start"))
        self.assertEqual([key for key, _ in self.commands("start")], KEYS)

    def test_stale_sequence_cannot_resolve_action_as_not_applied_or_trigger_rollback(self):
        self.prepare()
        def lose_reply(command):
            raise TimeoutError("lost")
        self.hosts.after[(KEYS[1], "start")] = lose_reply
        self.controller.execute(self.request("start"))
        pending = [key for key, action in self.durable()["snapshot"]["actions"].items()
                   if action["operation"] == "start" and action["instance"] == list(KEYS[1])][0]
        self.hosts.transform[(KEYS[1], "status")] = lambda reply: dict(
            reply, sequence=2, state="STARTING", action_outcomes={pending: "UNKNOWN"})
        self.controller.reconcile("run-a")
        self.hosts.transform[(KEYS[1], "status")] = lambda reply: dict(
            reply, sequence=1, state="READY", action_outcomes={pending: "NOT_APPLIED"})
        status = self.controller.reconcile("run-a")
        self.assertEqual(self.commands("stop"), [])
        self.assertFalse(self.durable()["snapshot"]["abort_startup"])
        self.assertIsNone(status["result"])

    def test_prepare_unknown_result_records_generation_without_claiming_action_applied(self):
        self.register()
        self.hosts.transform[(KEYS[0], "prepare")] = lambda reply: dict(reply, outcome="UNKNOWN")
        result = self.controller.execute(self.request("prepare"))
        self.assertEqual(result["outcome"], "UNKNOWN")
        self.assertEqual(result["status"]["instances"][0]["generation"], "generation-1")
        self.assertEqual(self.controller.reconcile("run-a")["phase"], "READY")
        self.assertEqual(self.controller.execute(self.request("prepare"))["outcome"], "APPLIED")

    def test_human_stop_does_not_require_normal_target_completion_for_already_clean_terminal(self):
        self.start()
        for key in KEYS:
            self.hosts.set_state(key, "COMPLETED", cleanup="CONFIRMED",
                                evidence=dict.fromkeys(EVIDENCE, False))
        self.controller.reconcile("run-a")
        result = self.controller.execute(self.request("stop", mode="force"))
        self.assertEqual(result["status"]["result"], "INTERRUPTED")
        self.assertEqual(self.commands("stop"), [])

    def test_crash_after_intent_before_adapter_is_reconciled_not_replayed(self):
        self.prepare()
        def crash_at_intent():
            if any(action["operation"] == "start" and action["dispatch"] == "UNKNOWN"
                   for action in self.durable()["snapshot"]["actions"].values()):
                self.clock.hook = None
                raise SystemExit("crash before adapter call")
        self.clock.hook = crash_at_intent
        with self.assertRaises(SystemExit):
            self.controller.execute(self.request("start"))
        self.assertEqual(self.commands("start"), [])
        self.controller.close()
        self.clock.tick = 0
        self.controller = self.open_controller()
        self.assertEqual(self.controller.reconcile("run-a")["result"], "FAILED")
        self.assertEqual(self.commands("start"), [])

    def test_stop_after_intent_before_adapter_suppresses_first_start(self):
        self.prepare()
        def stop_at_intent():
            if any(action["operation"] == "start" and action["dispatch"] == "UNKNOWN"
                   for action in self.durable()["snapshot"]["actions"].values()):
                self.clock.hook = None
                self.controller.execute(self.request("stop", mode="force"))
        self.clock.hook = stop_at_intent
        self.controller.execute(self.request("start"))
        self.assertEqual(self.commands("start"), [])
        self.assertEqual(self.controller.status("run-a")["result"], "INTERRUPTED")
        self.assertEqual(self.controller.status("run-a")["failed_instances"], [])

    def test_freshness_and_schedule_rechecked_after_durable_intent(self):
        self.prepare()
        def age_at_intent():
            if any(action["operation"] == "start" and action["dispatch"] == "UNKNOWN"
                   for action in self.durable()["snapshot"]["actions"].values()):
                self.clock.hook = None
                self.clock.tick += 101
        self.clock.hook = age_at_intent
        self.controller.execute(self.request("start"))
        self.assertEqual(self.commands("start"), [])

    def test_missed_utc_after_intent_suppresses_first_start(self):
        self.prepare()
        def miss_at_intent():
            if any(action["operation"] == "start" and action["dispatch"] == "UNKNOWN"
                   for action in self.durable()["snapshot"]["actions"].values()):
                self.clock.hook = None
                self.clock.utc = datetime.datetime(2026, 9, 12, 0, 0, 1, tzinfo=datetime.timezone.utc)
        self.clock.hook = miss_at_intent
        self.controller.execute(self.request("start"))
        self.assertEqual(self.commands("start"), [])
        self.assertEqual(self.controller.status("run-a")["result"], "FAILED")

    def test_unhashable_report_fields_raise_typed_error_without_mutation(self):
        self.start()
        for field in ("state", "outcome", "cleanup"):
            self.hosts.transform[(KEYS[0], "status")] = lambda reply, f=field: dict(reply, **{f: []})
            before = self.durable()
            with self.subTest(field=field):
                with self.assertRaises(ControllerError):
                    self.controller.reconcile("run-a")
                self.assertEqual(self.durable(), before)

    def test_command_schema_matches_runtime_positive_and_negative_cases(self):
        schema = json.loads((ROOT / "config/multiserver-command-v1.schema.json").read_text())
        checker = deployment_tests.DeploymentTest()
        checker._schema = schema
        cases = [(self.request("prepare"), True), (self.request("start"), True),
                 (self.request("stop", mode="graceful"), True), (self.request("stop", mode="force"), True),
                 (self.request("start", mode="force"), False), (self.request("stop"), False),
                 (self.request("stop", mode="invalid"), False), (self.request("launch"), False),
                 (dict(self.request("prepare"), targets=[]), False)]
        for field, value in (("schema_version", True), ("schema_version", 0),
                             ("request_id", ""), ("request_id", False), ("request_id", "\ud800"),
                             ("run_id", 1), ("run_id", "")):
            request = self.request("prepare")
            request[field] = value
            cases.append((request, False))
        self.register()
        for request, valid in cases:
            with self.subTest(request=ascii(request)):
                if valid:
                    checker.assert_schema_node(schema, request)
                else:
                    with self.assertRaises(AssertionError):
                        checker.assert_schema_node(schema, request)
                    with self.assertRaises(ControllerError):
                        self.controller.execute(request)
        # Valid operations run in lifecycle order through the public interface.
        self.controller.execute(self.request("prepare"))
        self.controller.execute(self.request("start"))
        self.controller.execute(self.request("stop", mode="graceful"))

    def test_missing_run_and_changed_global_input_are_rejected(self):
        from scripts.multiserver.store import RunNotFoundError
        with self.assertRaises(RunNotFoundError):
            self.controller.execute(self.request("prepare"))
        changed = copy.deepcopy(self.global_document)
        changed["observation"]["utc_start"] = "2026-09-13-00:00:00"
        with self.assertRaises(ControllerError):
            self.controller.register_run("run-a", self.bundle, changed)

    def test_stop_before_prepare_is_noop_but_later_start_is_forbidden(self):
        self.register()
        result = self.controller.execute(self.request("stop", mode="force"))
        self.assertEqual(result["status"]["result"], "INTERRUPTED")
        self.assertEqual(self.hosts.calls, [])
        with self.assertRaisesRegex(ControllerError, "STOPPED"):
            self.controller.execute(self.request("prepare"))

    def test_repeated_cleanup_retries_reuse_action_identity_and_force_never_downgrades(self):
        self.start()
        self.hosts.unreachable.add(KEYS[1])
        self.controller.execute(self.request("stop", mode="force"))
        unknown = self.commands("stop")[-1][1]["action_id"]
        self.assertIsNone(self.controller.status("run-a")["result"])
        self.hosts.unreachable.clear()
        self.controller.reconcile("run-a")
        self.assertEqual(self.commands("stop")[-1][1]["action_id"], unknown)
        self.controller.execute(self.request("stop", "stop-2", mode="graceful"))
        self.assertEqual(self.controller.status("run-a")["stop_intent"]["mode"], "force")
        self.assertEqual(self.controller.status("run-a")["result"], "INTERRUPTED")

    def test_host_cannot_claim_started_or_completed_without_authorized_start(self):
        self.prepare()
        for state in ("STARTING", "RUNNING", "COMPLETED"):
            self.hosts.transform[(KEYS[0], "status")] = lambda reply, value=state: dict(
                reply, state=value, cleanup="CONFIRMED", evidence=EVIDENCE)
            before = self.durable()
            with self.subTest(state=state):
                with self.assertRaisesRegex(ControllerError, "UNAUTHORIZED_START"):
                    self.controller.reconcile("run-a")
                self.assertEqual(self.durable(), before)

    def test_uncertain_first_start_does_not_claim_run_fully_running_or_replay_peer(self):
        self.prepare()
        def lose_reply(command):
            raise TimeoutError("first reply lost")
        self.hosts.after[(KEYS[0], "start")] = lose_reply
        self.controller.execute(self.request("start"))
        status = self.controller.reconcile("run-a")
        self.assertEqual(status["phase"], "STARTING")
        self.assertEqual([instance["state"] for instance in status["instances"]], ["RUNNING", "READY"])
        self.assertEqual([key for key, _ in self.commands("start")], [KEYS[0]])
        self.assertIsNone(status["result"])

    def test_stop_after_confirmed_completion_preserves_historical_result(self):
        self.start()
        for key in KEYS:
            self.hosts.set_state(key, "COMPLETED", cleanup="CONFIRMED", evidence=EVIDENCE)
        self.assertEqual(self.controller.reconcile("run-a")["result"], "PASS")
        result = self.controller.execute(self.request("stop", mode="force"))
        self.assertEqual(result["status"]["result"], "PASS")
        self.assertEqual(self.commands("stop"), [])

    def test_human_stop_preserves_prior_runtime_errors(self):
        self.start()
        self.hosts.set_state(KEYS[0], "FAILED", error="worker failed")
        self.controller.reconcile("run-a")
        result = self.controller.execute(self.request("stop", mode="graceful"))
        self.assertEqual(result["status"]["result"], "INTERRUPTED")
        self.assertEqual(result["status"]["instances"][0]["errors"], ["worker failed"])

    def test_unicode_scalar_request_id_matches_schema_and_runtime(self):
        self.register()
        request = self.request("prepare", "准备😀")
        schema = json.loads((ROOT / "config/multiserver-command-v1.schema.json").read_text())
        checker = deployment_tests.DeploymentTest()
        checker._schema = schema
        checker.assert_schema_node(schema, request)
        result = self.controller.execute(request)
        self.assertEqual(result["request_id"], "准备😀")
        self.assertEqual(result["status"]["phase"], "READY")

    def test_impossible_start_outcome_cannot_roll_back_running_observation(self):
        self.prepare()
        def lose_reply(command):
            raise TimeoutError("reply lost")
        self.hosts.after[(KEYS[1], "start")] = lose_reply
        self.controller.execute(self.request("start"))
        before = self.durable()
        first_report = copy.deepcopy(before["snapshot"]["instances"][0]["report"])
        self.hosts.transform[(KEYS[0], "status")] = lambda reply: dict(reply, **first_report)
        self.hosts.transform[(KEYS[1], "status")] = lambda reply: dict(
            reply, state="RUNNING", action_outcomes=dict.fromkeys(reply["action_outcomes"], "NOT_APPLIED"))
        with self.assertRaisesRegex(ControllerError, "ACTION_STATE_CONFLICT"):
            self.controller.reconcile("run-a")
        self.assertEqual(self.durable(), before)
        self.assertEqual(self.commands("stop"), [])

    def test_unexpected_interruption_is_local_failure_while_peer_continues(self):
        self.start()
        self.hosts.set_state(KEYS[0], "INTERRUPTED", cleanup="CONFIRMED",
                             error="worker interrupted externally")
        status = self.controller.reconcile("run-a")
        self.assertEqual((status["phase"], status["health"], status["result"]),
                         ("RUNNING", "DEGRADED", None))
        self.assertEqual(status["failed_instances"], [{"server_id": "node-a", "gpu_id": 0}])
        self.assertEqual(status["instances"][0]["state"], "INTERRUPTED")
        self.assertEqual(status["instances"][0]["errors"], ["worker interrupted externally"])
        self.assertEqual(status["missing_frequency"][0]["assignment"]["nchan"], 4)
        self.assertEqual(self.commands("stop"), [])
        self.assertFalse(self.durable()["snapshot"]["abort_startup"])
        self.hosts.set_state(KEYS[1], "COMPLETED", cleanup="CONFIRMED", evidence=EVIDENCE)
        status = self.controller.reconcile("run-a")
        self.assertEqual((status["phase"], status["health"], status["result"]),
                         ("FINISHED", "DEGRADED", "PARTIAL_FAILED"))
        self.assertEqual(status["instances"][0]["errors"], ["worker interrupted externally"])
        self.assertEqual(len(status["missing_frequency"]), 1)

    def test_all_unexpected_interruptions_finish_failed_without_completion_evidence(self):
        self.start()
        for key in KEYS:
            self.hosts.set_state(key, "INTERRUPTED", cleanup="CONFIRMED",
                                 evidence=dict.fromkeys(EVIDENCE, False))
        status = self.controller.reconcile("run-a")
        self.assertEqual((status["phase"], status["health"], status["result"]),
                         ("FINISHED", "DEGRADED", "FAILED"))
        self.assertEqual(len(status["failed_instances"]), 2)
        self.assertEqual(len(status["missing_frequency"]), 2)
        for instance in status["instances"]:
            self.assertEqual(instance["errors"], ["host reported unexpected INTERRUPTED"])
        self.assertEqual(self.commands("stop"), [])

    def test_unexpected_interruption_with_pending_cleanup_stops_only_that_shard(self):
        self.start()
        self.hosts.set_state(KEYS[0], "INTERRUPTED", cleanup="PENDING", error="external stop")
        status = self.controller.reconcile("run-a")
        self.assertEqual([key for key, _ in self.commands("stop")], [KEYS[0]])
        self.assertEqual((status["phase"], status["health"], status["result"]),
                         ("RUNNING", "DEGRADED", None))
        self.assertEqual(status["instances"][1]["state"], "RUNNING")
        self.assertEqual(status["instances"][0]["cleanup"], "CONFIRMED")
        self.assertEqual(status["instances"][0]["errors"], ["external stop"])

    def pending_start_with_frozen_running_reports(self):
        self.prepare()
        def lose_reply(command):
            raise TimeoutError("reply lost")
        self.hosts.after[(KEYS[1], "start")] = lose_reply
        self.controller.execute(self.request("start"))
        pending = self.commands("start")[-1][1]["action_id"]
        self.hosts.transform[(KEYS[1], "status")] = lambda reply: dict(
            reply, action_outcomes={pending: "UNKNOWN"})
        self.assertEqual(self.controller.reconcile("run-a")["phase"], "RUNNING")
        self.assertEqual(self.durable()["snapshot"]["actions"][pending]["outcome"], "UNKNOWN")
        reports = [copy.deepcopy(instance["report"])
                   for instance in self.durable()["snapshot"]["instances"]]
        self.hosts.transform[(KEYS[0], "status")] = lambda reply: dict(reply, **reports[0])
        return pending, reports[1]

    def assert_equal_sequence_start_conflict(self, outcome):
        pending, observation = self.pending_start_with_frozen_running_reports()
        self.hosts.transform[(KEYS[1], "status")] = lambda reply: dict(
            dict(reply, **observation), action_outcomes={pending: outcome})
        before = self.durable()
        with self.assertRaisesRegex(ControllerError, "ACTION_STATE_CONFLICT"):
            self.controller.reconcile("run-a")
        self.assertEqual(self.durable(), before)
        self.assertFalse(self.durable()["snapshot"]["abort_startup"])
        self.assertEqual(self.commands("stop"), [])
        self.assertEqual([instance["state"] for instance in self.controller.status("run-a")["instances"]],
                         ["RUNNING", "RUNNING"])

    def test_equal_sequence_unapplied_start_cannot_roll_back_running_peers(self):
        self.assert_equal_sequence_start_conflict("NOT_APPLIED")

    def test_equal_sequence_rejected_start_cannot_roll_back_running_peers(self):
        self.assert_equal_sequence_start_conflict("REJECTED")

    def test_equal_sequence_applied_start_resolves_without_renewing_freshness(self):
        pending, observation = self.pending_start_with_frozen_running_reports()
        self.hosts.transform[(KEYS[1], "status")] = lambda reply: dict(
            dict(reply, **observation), action_outcomes={pending: "APPLIED"})
        self.clock.tick += 101
        status = self.controller.reconcile("run-a")
        self.assertEqual((status["phase"], status["health"], status["result"]),
                         ("RUNNING", "UNKNOWN", None))
        self.assertEqual(self.durable()["snapshot"]["actions"][pending]["outcome"], "APPLIED")
        self.assertEqual(self.controller.execute(self.request("start"))["outcome"], "APPLIED")
        self.assertEqual(len(self.commands("start")), 2)
        self.assertEqual(self.commands("stop"), [])

if __name__ == "__main__":
    unittest.main()
