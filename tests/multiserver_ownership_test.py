"""B1 contract tests: real SQLite, temporary paths, no hardware or signals."""

import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from scripts.multiserver.ownership import (
    HostJournal, JournalClosedError, JournalCorruptionError, JournalDatabaseError,
    JournalValidationError, OwnershipConflictError, OwnerNotFoundError,
    ActionConflictError, CleanupIncompleteError,
)


def owner(index=0, **changes):
    value = dict(run_id="run-{}".format(index), bundle_id="bundle-a",
                 server_id="node-a", gpu_id=index, observation_sha256="a" * 64,
                 generation="generation-1")
    value.update(changes)
    return value


def resources(root, index=0):
    return dict(gpu_uuid="GPU-fake-{}".format(index),
                cpus=[dict(logical_cpu=index, socket_id=0, core_id=index)],
                ipc_keys=[100 + index],
                flow=dict(physical_port="port-0", match=dict(
                    destination_ip="192.0.2.1", destination_port=8000 + index,
                    protocol="UDP")),
                run_dir=str(root / "runs" / "run-{}".format(index)))


def command(binding, action_id="prepare-1", operation="prepare", **changes):
    value = {k: v for k, v in binding.items() if k != "generation"}
    value.update(expected_generation=binding["generation"], action_id=action_id,
                 operation=operation)
    value.update(changes)
    return value


def report(binding, action_id="prepare-1", outcome="APPLIED"):
    value = dict(binding, action_id=action_id, outcome=outcome, state="READY",
                 sequence=1, cleanup="PENDING", error=None, action_outcomes={},
                 evidence=dict(target_data_complete=False, output_published=False,
                               eod_complete=False))
    return value


def cleanup(binding, claims):
    return dict(owner=copy.deepcopy(binding), resources=copy.deepcopy(claims),
                processes_gone=True, descendants_gone=True, resources_gone=True)


def compete(path, binding, claims, barrier):
    journal = HostJournal(path)
    try:
        barrier.wait(timeout=10)
        journal.claim(binding, claims)
        return "claimed"
    except OwnershipConflictError:
        return "conflict"
    finally:
        journal.close()


def compete_action(path, binding, barrier):
    journal = HostJournal(path)
    try:
        barrier.wait(timeout=10)
        return journal.begin_action(binding, command(binding))["created"]
    finally:
        journal.close()


class HostJournalTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.path = self.root / "host journal?#.sqlite"
        self.owner = owner()
        self.resources = resources(self.root)

    def open(self):
        journal = HostJournal(self.path)
        self.addCleanup(journal.close)
        return journal

    def claimed(self):
        journal = self.open()
        journal.claim(self.owner, self.resources)
        return journal

    def test_claim_is_durable_idempotent_and_returns_independent_data(self):
        journal = self.claimed()
        record = journal.load(self.owner)
        self.assertEqual(record, dict(owner=self.owner, resources=self.resources,
                                     released=False, cleanup_evidence=None, actions={}))
        self.assertEqual(journal.claim(self.owner, self.resources), record)
        record["resources"]["ipc_keys"].append(999)
        journal.close()
        self.assertEqual(self.open().load(self.owner)["resources"], self.resources)

    def test_concurrent_conflicting_claims_have_one_winner(self):
        self.open().close()
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(compete, self.path, owner(i), self.resources, barrier)
                       for i in range(2)]
            outcomes = [future.result(timeout=30) for future in futures]
        self.assertCountEqual(outcomes, ["claimed", "conflict"])

    def test_each_resource_conflict_rolls_back_whole_claim(self):
        journal = self.claimed()
        for field in ("gpu_uuid", "cpus", "ipc_keys", "flow", "run_dir"):
            other = resources(self.root, 1)
            other[field] = copy.deepcopy(self.resources[field])
            with self.subTest(resource=field):
                with self.assertRaises(OwnershipConflictError):
                    journal.claim(owner(1), other)
                with self.assertRaises(OwnerNotFoundError):
                    journal.load(owner(1))
        self.assertFalse(journal.claim(owner(1), resources(self.root, 1))["released"])

    def test_smt_siblings_conflict_even_with_distinct_logical_cpu(self):
        journal = self.claimed()
        other = resources(self.root, 1)
        other["cpus"] = [dict(logical_cpu=32, socket_id=0, core_id=0)]
        with self.assertRaises(OwnershipConflictError):
            journal.claim(owner(1), other)
        other["cpus"][0]["socket_id"] = 1
        journal.claim(owner(1), other)

    def test_logical_cpu_conflict_cannot_be_hidden_by_wrong_physical_mapping(self):
        journal = self.claimed()
        other = resources(self.root, 1)
        other["cpus"][0]["logical_cpu"] = 0
        with self.assertRaises(OwnershipConflictError):
            journal.claim(owner(1), other)

    def test_wildcard_flow_and_nested_or_symlink_run_paths_conflict(self):
        journal = self.claimed()
        for field in ("flow", "nested", "symlink"):
            other = resources(self.root, 1)
            if field == "flow":
                other["flow"]["match"] = {"destination_ip": "192.0.2.1"}
            elif field == "nested":
                other["run_dir"] = self.resources["run_dir"] + "/child"
            else:
                alias = self.root / "alias"
                alias.symlink_to(self.root / "runs", target_is_directory=True)
                other["run_dir"] = str(alias / "run-0")
            with self.subTest(field=field):
                with self.assertRaises(OwnershipConflictError):
                    journal.claim(owner(1), other)

    def test_owner_binding_and_resource_changes_cannot_adopt_live_claim(self):
        journal = self.claimed()
        changes = dict(run_id="foreign", bundle_id="foreign", server_id="alias",
                       gpu_id=1, observation_sha256="b" * 64, generation="next")
        for field, value in changes.items():
            foreign = dict(self.owner, **{field: value})
            with self.subTest(field=field):
                with self.assertRaises(OwnerNotFoundError):
                    journal.load(foreign)
                with self.assertRaises(OwnershipConflictError):
                    journal.claim(foreign, self.resources)
                with self.assertRaises(OwnerNotFoundError):
                    journal.release(foreign, cleanup(foreign, self.resources))
        changed = copy.deepcopy(self.resources)
        changed["ipc_keys"] = [999]
        with self.assertRaises(OwnershipConflictError):
            journal.claim(self.owner, changed)

    def test_gpu_slot_is_exclusive_even_with_a_changed_uuid(self):
        journal = self.claimed()
        with self.assertRaises(OwnershipConflictError):
            journal.claim(owner(1, gpu_id=0), resources(self.root, 1))

    def test_action_intent_reopens_unknown_and_duplicate_does_not_authorize_replay(self):
        journal = self.claimed()
        cmd = command(self.owner, expected_generation=None)
        action = dict(command=cmd, outcome="UNKNOWN", report=None)
        self.assertEqual(journal.begin_action(self.owner, cmd),
                         dict(created=True, action=action))
        journal.close()
        reopened = self.open()
        self.assertEqual(reopened.load(self.owner)["actions"], {"prepare-1": action})
        self.assertEqual(reopened.begin_action(self.owner, cmd),
                         dict(created=False, action=action))

    def test_process_exit_preserves_unknown_until_explicit_same_sequence_resolution(self):
        # No multiprocessing semaphores/shared memory and no signal cleanup.
        code = ("import json, sys; from scripts.multiserver.ownership import HostJournal; "
                "j=HostJournal(sys.argv[1]); o,r,c=json.loads(sys.argv[2]); "
                "j.claim(o,r); j.begin_action(o,c); j.close()")
        result = subprocess.run([sys.executable, "-B", "-c", code, str(self.path),
                                 json.dumps([self.owner, self.resources, command(self.owner)])],
                                cwd=str(Path(__file__).resolve().parents[1]),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        journal = self.open()
        cmd = command(self.owner)
        self.assertFalse(journal.begin_action(self.owner, cmd)["created"])
        uncertain = report(self.owner, outcome="UNKNOWN")
        journal.finish_action(self.owner, "prepare-1", uncertain)
        resolved = dict(uncertain, outcome="APPLIED")
        journal.finish_action(self.owner, "prepare-1", resolved)
        self.assertEqual(journal.load(self.owner)["actions"]["prepare-1"]["report"]["sequence"], 1)
        self.assertTrue(journal.release(self.owner, cleanup(self.owner, self.resources))["released"])

    def test_query_envelope_can_resolve_pending_action_without_new_sequence(self):
        journal = self.claimed()
        cmd = command(self.owner)
        journal.begin_action(self.owner, cmd)
        journal.finish_action(self.owner, "prepare-1", report(self.owner, outcome="UNKNOWN"))
        query = report(self.owner, action_id="status-query-2", outcome="APPLIED")
        query["action_outcomes"] = {"prepare-1": "NOT_APPLIED"}
        resolved = journal.finish_action(self.owner, "prepare-1", query)
        self.assertEqual(resolved["outcome"], "NOT_APPLIED")
        self.assertEqual(resolved["report"], query)
        self.assertEqual(resolved["report"]["sequence"], 1)
        self.assertFalse(journal.begin_action(self.owner, cmd)["created"])
        journal.close()
        self.assertEqual(self.open().load(self.owner)["actions"]["prepare-1"], resolved)

    def test_corrupt_null_stored_action_raises_typed_corruption(self):
        journal = self.claimed()
        journal.begin_action(self.owner, command(self.owner))
        with sqlite3.connect(str(self.path)) as connection:
            record = journal.load(self.owner)
            record["actions"] = {"prepare-1": None}
            connection.execute("UPDATE owners SET document = ?", (json.dumps(record),))
        with self.assertRaises(JournalCorruptionError):
            journal.load(self.owner)

    def test_concurrent_same_action_authorizes_only_one_execution(self):
        journal = self.claimed()
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(compete_action, self.path, self.owner, barrier) for _ in range(2)]
            outcomes = [future.result(timeout=30) for future in futures]
        self.assertCountEqual(outcomes, [True, False])
        self.assertEqual(journal.load(self.owner)["actions"]["prepare-1"]["outcome"], "UNKNOWN")

    def test_original_start_schedule_and_stop_mode_are_durable_content(self):
        journal = self.claimed()
        start = command(self.owner, "start-1", "start", utc_start="2026-10-16-00:00:00",
                        duration_seconds="8.000000000001")
        journal.begin_action(self.owner, start)
        self.assertEqual(journal.load(self.owner)["actions"]["start-1"]["command"], start)
        with self.assertRaises(ActionConflictError):
            journal.begin_action(self.owner, dict(start, duration_seconds="9"))
        started = dict(report(self.owner, "start-1"), state="STARTING")
        journal.finish_action(self.owner, "start-1", started)
        stop = command(self.owner, "stop-1", "stop", mode="force", pending_action_ids=["start-1"])
        journal.begin_action(self.owner, stop)
        with self.assertRaises(ActionConflictError):
            journal.begin_action(self.owner, dict(stop, mode="graceful"))
        stopped = dict(report(self.owner, "stop-1"), state="INTERRUPTED", cleanup="CONFIRMED")
        journal.finish_action(self.owner, "stop-1", stopped)
        self.assertTrue(journal.release(self.owner, cleanup(self.owner, self.resources))["released"])

    def test_invalid_observation_schedule_is_rejected_before_intent(self):
        journal = self.claimed()
        start = command(self.owner, "invalid-start", "start",
                        utc_start="2026-10-16-00:00:00", duration_seconds="1.048576")
        changes = [dict(utc_start="2026-10-16T00:00:00Z"),
                   dict(utc_start="2026-02-30-00:00:00"),
                   dict(utc_start="2026-1-16-00:00:00"),
                   dict(duration_seconds=1.048576), dict(duration_seconds=True),
                   dict(duration_seconds="0"), dict(duration_seconds="1e0"),
                   dict(duration_seconds="0.0000000000001"),
                   dict(duration_seconds="18446745")]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(JournalValidationError):
                journal.begin_action(self.owner, dict(start, **change))
        self.assertEqual(journal.load(self.owner)["actions"], {})

    def test_sqlite_abort_leaves_prior_claim_and_actions_unchanged(self):
        journal = self.claimed()
        before = journal.load(self.owner)
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute("CREATE TRIGGER abort_write BEFORE UPDATE ON owners "
                               "BEGIN SELECT RAISE(ABORT, 'fake disk write failure'); END")
        with self.assertRaises(JournalDatabaseError):
            journal.begin_action(self.owner, command(self.owner))
        self.assertEqual(journal.load(self.owner), before)
        with self.assertRaises(JournalDatabaseError):
            journal.release(self.owner, cleanup(self.owner, self.resources))
        self.assertEqual(journal.load(self.owner), before)
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute("DROP TRIGGER abort_write")
        self.assertTrue(journal.begin_action(self.owner, command(self.owner))["created"])

    def test_canonical_claim_order_and_wildcards_are_idempotent(self):
        journal = self.open()
        claims = copy.deepcopy(self.resources)
        claims["cpus"].append(dict(logical_cpu=32, socket_id=0, core_id=0))
        claims["ipc_keys"].append(101)
        claims["flow"]["match"]["source_ip"] = "*"
        claims["flow"]["match"]["protocol"] = "udp"
        first = journal.claim(self.owner, claims)
        claims["cpus"].reverse()
        claims["ipc_keys"].reverse()
        claims["flow"]["match"]["source_ip"] = None
        self.assertEqual(journal.claim(self.owner, claims), first)
        self.assertEqual(first["resources"]["ipc_keys"], [100, 101])
        self.assertNotIn("source_ip", first["resources"]["flow"]["match"])

    def test_finished_action_returns_recorded_report_and_rejects_changed_content(self):
        journal = self.claimed()
        cmd, result = command(self.owner), report(self.owner)
        journal.begin_action(self.owner, cmd)
        action = journal.finish_action(self.owner, "prepare-1", result)
        self.assertEqual(action, dict(command=cmd, outcome="APPLIED", report=result))
        self.assertEqual(journal.finish_action(self.owner, "prepare-1", result), action)
        self.assertEqual(journal.begin_action(self.owner, cmd), dict(created=False, action=action))
        with self.assertRaises(ActionConflictError):
            journal.begin_action(self.owner, dict(cmd, operation="stop", mode="force"))
        with self.assertRaises(ActionConflictError):
            journal.finish_action(self.owner, "prepare-1", dict(result, sequence=2))
        journal.close()
        self.assertEqual(self.open().load(self.owner)["actions"]["prepare-1"], action)

    def test_unknown_report_needs_explicit_finish_to_resolve(self):
        journal = self.claimed()
        cmd = command(self.owner)
        journal.begin_action(self.owner, cmd)
        journal.finish_action(self.owner, "prepare-1", report(self.owner, outcome="UNKNOWN"))
        self.assertEqual(journal.begin_action(self.owner, cmd)["action"]["outcome"], "UNKNOWN")
        action = journal.finish_action(self.owner, "prepare-1", report(self.owner, outcome="NOT_APPLIED"))
        self.assertEqual(action["outcome"], "NOT_APPLIED")

    def test_action_identity_isolation_and_global_id_collision(self):
        journal = self.claimed()
        journal.claim(owner(1), resources(self.root, 1))
        journal.begin_action(self.owner, command(self.owner))
        with self.assertRaises(ActionConflictError):
            journal.begin_action(owner(1), command(owner(1)))
        with self.assertRaises(ActionConflictError):
            journal.finish_action(owner(1), "prepare-1", report(owner(1)))
        for field, value in dict(run_id="foreign", generation="foreign", gpu_id=True,
                                 observation_sha256="b" * 64, action_id="other").items():
            with self.subTest(field=field):
                with self.assertRaises(JournalValidationError):
                    journal.finish_action(self.owner, "prepare-1",
                                          dict(report(self.owner), **{field: value}))
        with self.assertRaises(JournalValidationError):
            journal.begin_action(self.owner, command(self.owner, expected_generation="stale"))

    def test_actions_require_claim_and_status_is_not_a_mutating_intent(self):
        journal = self.open()
        with self.assertRaises(OwnerNotFoundError):
            journal.begin_action(self.owner, command(self.owner))
        journal.claim(self.owner, self.resources)
        with self.assertRaises(JournalValidationError):
            journal.begin_action(self.owner, command(self.owner, operation="status"))

    def test_release_requires_complete_bound_cleanup_and_resolved_actions(self):
        journal = self.claimed()
        good = cleanup(self.owner, self.resources)
        bad = [{}, dict(good, processes_gone=False), dict(good, descendants_gone=False),
               dict(good, resources_gone=False), dict(good, resources_gone=1),
               dict(good, owner=owner(1)), dict(good, resources=resources(self.root, 1)),
               dict(good, lease_expired=True)]
        for evidence in bad:
            with self.subTest(evidence=evidence):
                with self.assertRaises(CleanupIncompleteError):
                    journal.release(self.owner, evidence)
                self.assertFalse(journal.load(self.owner)["released"])
        journal.begin_action(self.owner, command(self.owner))
        with self.assertRaises(CleanupIncompleteError):
            journal.release(self.owner, good)
        journal.finish_action(self.owner, "prepare-1", report(self.owner))
        self.assertTrue(journal.release(self.owner, good)["released"])

    def test_release_reuse_preserves_history_without_resurrecting_owner(self):
        journal = self.claimed()
        cmd = command(self.owner)
        journal.begin_action(self.owner, cmd)
        journal.finish_action(self.owner, "prepare-1", report(self.owner))
        evidence = cleanup(self.owner, self.resources)
        released = journal.release(self.owner, evidence)
        self.assertEqual(journal.release(self.owner, evidence), released)
        self.assertTrue(released["released"])
        self.assertEqual(released["cleanup_evidence"], evidence)
        self.assertEqual(journal.begin_action(self.owner, cmd)["action"]["outcome"], "APPLIED")
        with self.assertRaises(OwnershipConflictError):
            journal.claim(self.owner, self.resources)
        with self.assertRaises(OwnershipConflictError):
            journal.begin_action(self.owner, command(self.owner, "new-action"))
        next_owner = dict(self.owner, generation="generation-2")
        journal.claim(next_owner, self.resources)
        journal.close()
        reopened = self.open()
        self.assertTrue(reopened.load(self.owner)["released"])
        self.assertFalse(reopened.load(next_owner)["released"])

    def test_corrupt_empty_or_unknown_database_is_never_replaced(self):
        for contents in (b"", b"not sqlite\x00data"):
            self.path.write_bytes(contents)
            with self.assertRaises(JournalDatabaseError):
                self.open()
            self.assertEqual(self.path.read_bytes(), contents)
            self.path.unlink()
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute("PRAGMA user_version = 999")
        before = self.path.read_bytes()
        with self.assertRaises(JournalDatabaseError):
            self.open()
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_stored_document_blocks_reopen_and_new_claim(self):
        journal = self.claimed()
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute("UPDATE owners SET document = ?", ('{"owner":1,"owner":2}',))
        before = self.path.read_bytes()
        with self.assertRaises(JournalCorruptionError):
            journal.claim(owner(1), resources(self.root, 1))
        journal.close()
        with self.assertRaises(JournalCorruptionError):
            self.open()
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_json_and_resource_inputs_do_not_write(self):
        journal = self.claimed()
        cycle = {}
        cycle["self"] = cycle
        for invalid in (float("nan"), float("inf"), {1: "coerced"}, (1,), "\ud800", cycle):
            with self.subTest(value=repr(invalid)):
                with self.assertRaises(JournalValidationError):
                    journal.begin_action(self.owner, command(self.owner, extra=invalid))
        for field, value in dict(gpu_id=True, generation=None, observation_sha256="bad").items():
            with self.assertRaises(JournalValidationError):
                journal.load(dict(self.owner, **{field: value}))
        for field, value in dict(ipc_keys=[0], run_dir="relative", cpus=[], gpu_uuid="").items():
            with self.assertRaises(JournalValidationError):
                journal.claim(owner(1), dict(resources(self.root, 1), **{field: value}))
        self.assertEqual(journal.load(self.owner)["actions"], {})

    def test_close_is_idempotent_and_never_releases_claims(self):
        journal = self.claimed()
        journal.close()
        journal.close()
        with self.assertRaises(JournalClosedError):
            journal.load(self.owner)
        with self.assertRaises(OwnershipConflictError):
            self.open().claim(owner(1), self.resources)


if __name__ == "__main__":
    unittest.main()
