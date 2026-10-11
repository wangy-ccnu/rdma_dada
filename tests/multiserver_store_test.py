import copy
import pathlib
import sqlite3
import tempfile
import unittest

from scripts.multiserver.store import (
    RevisionConflictError,
    RunExistsError,
    RunNotFoundError,
    RunStore,
    StoreClosedError,
    StoreCorruptionError,
    StoreDatabaseError,
    StoreValidationError,
    UnsupportedStoreVersionError,
)


class RunStoreTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = pathlib.Path(directory.name) / "run records?#.sqlite"

    def open_store(self):
        store = RunStore(self.path)
        self.addCleanup(store.close)
        return store

    def assert_record(self, record, revision, snapshot, run_id="run-a"):
        self.assertEqual(record, {
            "run_id": run_id, "revision": revision, "snapshot": snapshot,
        })

    def assert_reopened(self, revision, snapshot):
        store = self.open_store()
        self.assert_record(store.load("run-a"), revision, snapshot)
        store.close()

    def test_create_reopen_preserves_snapshot(self):
        snapshot = {
            "binding": {"bundle_id": "bundle-a", "servers": ["node-a", "节点二"]},
            "requests": [{"request_id": "prepare-1", "pending": True}],
            "progress": None, "count": 2 ** 80, "ratio": 0.125,
            "run_id": "snapshot-local-id", "revision": 99,
        }
        store = self.open_store()
        self.assert_record(store.create("run-a", snapshot), 1, snapshot)
        store.close()
        self.assert_reopened(1, snapshot)

    def test_save_increments_revision_and_reopens(self):
        store = self.open_store()
        store.create("run-a", {"state": "NEW"})
        self.assert_record(store.save("run-a", 1, {"state": "READY"}), 2, {"state": "READY"})
        self.assert_record(store.save("run-a", 2, {"state": "RUNNING"}), 3, {"state": "RUNNING"})
        store.close()
        self.assert_reopened(3, {"state": "RUNNING"})

    def test_duplicate_run_cannot_overwrite(self):
        store = self.open_store()
        store.create("run-a", {"state": "NEW"})
        with self.assertRaises(RunExistsError) as raised:
            store.create("run-a", {"state": "REPLACED"})
        self.assertEqual(raised.exception.run_id, "run-a")
        store.close()
        self.assert_reopened(1, {"state": "NEW"})

    def test_revision_conflict_preserves_newer_state(self):
        first = self.open_store()
        first.create("run-a", {"state": "NEW"})
        second = self.open_store()
        second.save("run-a", 1, {"state": "READY"})
        with self.assertRaises(RevisionConflictError) as raised:
            first.save("run-a", 1, {"state": "STALE"})
        error = raised.exception
        self.assertEqual((error.run_id, error.expected_revision, error.actual_revision),
                         ("run-a", 1, 2))
        first.close()
        second.close()
        self.assert_reopened(2, {"state": "READY"})

    def test_failed_transaction_leaves_previous_snapshot(self):
        store = self.open_store()
        store.create("run-a", {"state": "NEW"})
        store.create("witness", {"changed": False})
        # RAISE(FAIL) leaves earlier statement changes pending in SQLite;
        # RunStore must roll back both the snapshot and the trigger's side effect.
        with sqlite3.connect(self.path) as database:
            database.execute("""
                CREATE TRIGGER fail_save AFTER UPDATE ON run_documents
                WHEN NEW.run_id = 'run-a'
                BEGIN
                    UPDATE run_documents SET revision = revision + 1 WHERE run_id = 'witness';
                    SELECT RAISE(FAIL, 'injected write failure');
                END
            """)
        with self.assertRaisesRegex(StoreDatabaseError, "injected write failure"):
            store.save("run-a", 1, {"state": "READY"})
        self.assert_record(store.load("run-a"), 1, {"state": "NEW"})
        self.assert_record(store.load("witness"), 1, {"changed": False}, "witness")
        with sqlite3.connect(self.path) as database:
            database.execute("DROP TRIGGER fail_save")
        store.close()
        self.assert_reopened(1, {"state": "NEW"})

    def test_failed_commit_rolls_back_and_store_remains_usable(self):
        store = self.open_store()
        store.create("run-a", {"state": "NEW"})
        reader = sqlite3.connect(self.path)
        self.addCleanup(reader.close)
        reader.execute("BEGIN")
        reader.execute("SELECT snapshot FROM run_documents").fetchall()
        # A real reader permits BEGIN IMMEDIATE and UPDATE, but blocks COMMIT
        # in SQLite's rollback-journal mode until the writer's busy timeout.
        with self.assertRaisesRegex(StoreDatabaseError, "locked"):
            store.save("run-a", 1, {"state": "UNCOMMITTED"})
        reader.rollback()
        self.assert_record(store.load("run-a"), 1, {"state": "NEW"})
        self.assert_record(store.save("run-a", 1, {"state": "READY"}), 2, {"state": "READY"})
        store.close()
        self.assert_reopened(2, {"state": "READY"})

    def test_nonfinite_snapshot_rejected(self):
        store = self.open_store()
        store.create("run-a", {"state": "NEW"})
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(StoreValidationError):
                    store.create("invalid", {"nested": [value]})
                with self.assertRaises(StoreValidationError):
                    store.save("run-a", 1, {"nested": [value]})
                with self.assertRaises(RunNotFoundError):
                    store.load("invalid")
        store.close()
        self.assert_reopened(1, {"state": "NEW"})

    def test_non_json_types_and_keys_rejected_without_coercion(self):
        store = self.open_store()
        store.create("run-a", {"state": "NEW"})
        invalid = [
            None, [], "object", {1: "integer key"}, {"nested": {False: "bool key"}},
            {"tuple": (1, 2)}, {"bytes": b"payload"}, {"set": {1, 2}},
            {"object": object()},
        ]
        for snapshot in invalid:
            with self.subTest(snapshot=snapshot):
                with self.assertRaises(StoreValidationError):
                    store.create("invalid", snapshot)
                with self.assertRaises(StoreValidationError):
                    store.save("run-a", 1, snapshot)
        store.close()
        self.assert_reopened(1, {"state": "NEW"})

    def test_surrogate_keys_rejected_by_create_and_save_before_write(self):
        store = self.open_store()
        store.create("run-a", {"state": "NEW"})
        store.save("run-a", 1, {"state": "READY"})
        for index, key in enumerate(("\ud800", "\udfff", "\ud83d\ude00", "prefix\ud800tail")):
            snapshot = {"nested": {key: "payload"}}
            for operation in ("create", "save"):
                run_id = "{}-{}".format(operation, index)
                if operation == "save":
                    store.create(run_id, {"state": "NEW"})
                    store.save(run_id, 1, {"state": "READY"})
                before = self.path.read_bytes()
                with self.subTest(key=ascii(key), operation=operation):
                    with self.assertRaisesRegex(StoreValidationError, "snapshot"):
                        if operation == "create":
                            store.create(run_id, snapshot)
                        else:
                            store.save(run_id, 2, snapshot)
                    if operation == "create":
                        with self.assertRaises(RunNotFoundError):
                            store.load(run_id)
                    else:
                        self.assert_record(store.load(run_id), 2, {"state": "READY"}, run_id)
                    self.assertEqual(self.path.read_bytes(), before)
        store.close()
        self.assert_reopened(2, {"state": "READY"})

    def test_surrogate_values_rejected_by_create_and_save_before_write(self):
        store = self.open_store()
        store.create("run-a", {"state": "NEW"})
        store.save("run-a", 1, {"state": "READY"})
        for index, value in enumerate(("\ud800", "\udfff", "\ud83d\ude00", "prefix\udfff尾")):
            snapshot = {"nested": [{"value": value}]}
            for operation in ("create", "save"):
                run_id = "{}-{}".format(operation, index)
                if operation == "save":
                    store.create(run_id, {"state": "NEW"})
                    store.save(run_id, 1, {"state": "READY"})
                before = self.path.read_bytes()
                with self.subTest(value=ascii(value), operation=operation):
                    with self.assertRaisesRegex(StoreValidationError, "snapshot"):
                        if operation == "create":
                            store.create(run_id, snapshot)
                        else:
                            store.save(run_id, 2, snapshot)
                    if operation == "create":
                        with self.assertRaises(RunNotFoundError):
                            store.load(run_id)
                    else:
                        self.assert_record(store.load(run_id), 2, {"state": "READY"}, run_id)
                    self.assertEqual(self.path.read_bytes(), before)
        store.close()
        self.assert_reopened(2, {"state": "READY"})

    def test_surrogate_pair_key_collision_rejected_before_write(self):
        store = self.open_store()
        store.create("run-a", {"state": "NEW"})
        for index, mapping in enumerate(({"\ud83d\ude00": "pair", "😀": "scalar"},
                                         {"😀": "scalar", "\ud83d\ude00": "pair"})):
            snapshot = {"nested": mapping}
            for operation in ("create", "save"):
                run_id = "{}-{}".format(operation, index)
                if operation == "save":
                    store.create(run_id, {"state": "NEW"})
                before = self.path.read_bytes()
                with self.subTest(keys=[ascii(key) for key in mapping], operation=operation):
                    with self.assertRaises(StoreValidationError):
                        if operation == "create":
                            store.create(run_id, snapshot)
                        else:
                            store.save(run_id, 1, snapshot)
                    if operation == "create":
                        with self.assertRaises(RunNotFoundError):
                            store.load(run_id)
                    else:
                        self.assert_record(store.load(run_id), 1, {"state": "NEW"}, run_id)
                    self.assertEqual(self.path.read_bytes(), before)
        store.close()
        self.assert_reopened(1, {"state": "NEW"})

    def test_unicode_scalars_are_preserved_exactly_by_create_save_reopen(self):
        store = self.open_store()
        snapshot = {
            "😀": "正常中文😀", "é": "composed", "e\u0301": "decomposed",
            "boundary": ["\ud7ff", "\ue000", "\U0010ffff"],
        }
        self.assert_record(store.create("run-a", snapshot), 1, snapshot)
        self.assert_record(store.save("run-a", 1, snapshot), 2, snapshot)
        store.close()
        self.assert_reopened(2, snapshot)

    def test_cycles_rejected_but_shared_json_values_allowed(self):
        store = self.open_store()
        cyclic = {}
        cyclic["self"] = cyclic
        cyclic_list = []
        cyclic_list.append(cyclic_list)
        for snapshot in (cyclic, {"list": cyclic_list}):
            with self.assertRaises(StoreValidationError):
                store.create("invalid", snapshot)
        shared = [1, {"ok": True}]
        self.assert_record(store.create("run-a", {"a": shared, "b": shared}), 1,
                           {"a": [1, {"ok": True}], "b": [1, {"ok": True}]})

    def test_input_and_result_mutation_cannot_change_persisted_state(self):
        store = self.open_store()
        snapshot = {"nested": [{"state": "NEW"}]}
        before = copy.deepcopy(snapshot)
        created = store.create("run-a", snapshot)
        snapshot["nested"][0]["state"] = "INPUT_MUTATED"
        created["snapshot"]["nested"][0]["state"] = "RESULT_MUTATED"
        loaded = store.load("run-a")
        self.assert_record(loaded, 1, before)
        loaded["snapshot"]["nested"].append("LOAD_MUTATED")
        update = {"nested": [{"state": "READY"}]}
        saved = store.save("run-a", 1, update)
        update["nested"].clear()
        saved["snapshot"]["nested"].clear()
        store.close()
        self.assert_reopened(2, {"nested": [{"state": "READY"}]})

    def test_invalid_run_ids_rejected_by_all_operations(self):
        store = self.open_store()
        for run_id in ("", None, 1, True, [], {}):
            with self.subTest(run_id=run_id):
                for call in (lambda: store.create(run_id, {}), lambda: store.load(run_id),
                             lambda: store.save(run_id, 1, {})):
                    with self.assertRaises(StoreValidationError):
                        call()

    def test_invalid_expected_revisions_do_not_mutate_state(self):
        store = self.open_store()
        store.create("run-a", {"state": "NEW"})
        for revision in (True, False, 0, -1, 1.0, "1", None):
            with self.subTest(revision=revision):
                with self.assertRaises(StoreValidationError):
                    store.save("run-a", revision, {"state": "READY"})
        # A large positive integer is valid input but cannot match this revision.
        with self.assertRaises(RevisionConflictError):
            store.save("run-a", 2 ** 100, {})
        store.close()
        self.assert_reopened(1, {"state": "NEW"})

    def test_missing_run_is_typed_and_save_does_not_create(self):
        store = self.open_store()
        for call in (lambda: store.load("missing"), lambda: store.save("missing", 1, {})):
            with self.assertRaises(RunNotFoundError) as raised:
                call()
            self.assertEqual(raised.exception.run_id, "missing")
        store.close()
        with self.assertRaises(RunNotFoundError):
            self.open_store().load("missing")

    def test_run_ids_are_sql_values_and_records_are_isolated(self):
        store = self.open_store()
        run_id = "'; DROP TABLE run_documents; --"
        store.create("run-a", {"state": "NEW"})
        self.assert_record(store.create(run_id, {"state": "OTHER"}), 1,
                           {"state": "OTHER"}, run_id)
        store.save(run_id, 1, {"state": "UPDATED"})
        store.close()
        reopened = self.open_store()
        self.assert_record(reopened.load(run_id), 2, {"state": "UPDATED"}, run_id)
        self.assert_record(reopened.load("run-a"), 1, {"state": "NEW"})

    def test_close_is_idempotent_and_operations_report_closed_store(self):
        store = self.open_store()
        store.create("run-a", {})
        store.close()
        store.close()
        for call in (lambda: store.create("other", {}), lambda: store.load("run-a"),
                     lambda: store.save("run-a", 1, {})):
            with self.assertRaises(StoreClosedError):
                call()
        self.assert_reopened(1, {})

    def test_unsupported_database_version_rejected(self):
        store = self.open_store()
        store.create("run-a", {"state": "NEW"})
        store.close()
        with sqlite3.connect(self.path) as database:
            database.execute("PRAGMA user_version = 99")
        before = self.path.read_bytes()
        with self.assertRaises(UnsupportedStoreVersionError) as raised:
            self.open_store()
        self.assertEqual(raised.exception.version, 99)
        self.assertEqual(self.path.read_bytes(), before)

    def test_unrelated_database_is_preserved(self):
        with sqlite3.connect(self.path) as database:
            database.execute("CREATE TABLE unrelated (payload TEXT)")
            database.execute("INSERT INTO unrelated VALUES ('keep me')")
        before = self.path.read_bytes()
        with self.assertRaises(StoreDatabaseError):
            self.open_store()
        self.assertEqual(self.path.read_bytes(), before)

    def test_unknown_version_one_schema_is_preserved(self):
        with sqlite3.connect(self.path) as database:
            database.execute("PRAGMA user_version = 1")
            database.execute("CREATE TABLE run_documents (run_id TEXT, revision INTEGER, snapshot TEXT)")
            database.execute("INSERT INTO run_documents VALUES ('run-a', 1, '{}')")
        before = self.path.read_bytes()
        with self.assertRaises(StoreDatabaseError):
            self.open_store()
        self.assertEqual(self.path.read_bytes(), before)

    def test_sqlite_like_unknown_table_is_not_ignored(self):
        store = self.open_store()
        store.create("run-a", {"state": "NEW"})
        store.close()
        with sqlite3.connect(self.path) as database:
            database.execute("CREATE TABLE sqliteXunrelated (payload TEXT)")
        before = self.path.read_bytes()
        with self.assertRaises(StoreDatabaseError):
            self.open_store()
        self.assertEqual(self.path.read_bytes(), before)

    def test_nonempty_nul_run_id_is_preserved(self):
        store = self.open_store()
        self.assert_record(store.create("\x00run", {"state": "NEW"}), 1,
                           {"state": "NEW"}, "\x00run")
        store.save("\x00run", 1, {"state": "READY"})
        store.close()
        self.assert_record(self.open_store().load("\x00run"), 2,
                           {"state": "READY"}, "\x00run")

    def test_invalid_database_paths_raise_typed_validation_error(self):
        for path in (None, 1, "invalid\x00path"):
            with self.subTest(path=path):
                with self.assertRaises(StoreValidationError):
                    RunStore(path)

    def test_corrupt_and_empty_existing_files_are_preserved(self):
        for content in (b"not a SQLite database\x00", b""):
            with self.subTest(content=content):
                self.path.write_bytes(content)
                with self.assertRaises(StoreDatabaseError):
                    self.open_store()
                self.assertEqual(self.path.read_bytes(), content)

    def test_corrupt_snapshot_is_not_loaded_or_overwritten(self):
        store = self.open_store()
        store.create("run-a", {"state": "NEW"})
        malformed = ('{"state":', '[]', '{"x": NaN}', '{"x": 1e999}',
                     '{"x": 1, "x": 2}', '{"nested": {"x": 1, "x": 2}}')
        for payload in malformed:
            with self.subTest(payload=payload):
                with sqlite3.connect(self.path) as database:
                    database.execute("UPDATE run_documents SET snapshot = ? WHERE run_id = ?",
                                     (payload, "run-a"))
                with self.assertRaises(StoreCorruptionError):
                    store.load("run-a")
                with self.assertRaises(StoreCorruptionError):
                    store.save("run-a", 1, {"state": "REPLACED"})
                with sqlite3.connect(self.path) as database:
                    self.assertEqual(database.execute(
                        "SELECT revision, snapshot FROM run_documents WHERE run_id = 'run-a'"
                    ).fetchone(), (1, payload))

    def test_database_open_failure_has_path_context(self):
        path = self.path.parent / "missing-parent" / "runs.sqlite"
        with self.assertRaises(StoreDatabaseError) as raised:
            RunStore(path)
        self.assertIn(str(path), str(raised.exception))


if __name__ == "__main__":
    unittest.main()
