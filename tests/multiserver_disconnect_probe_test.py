import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import multiserver_disconnect_probe as probe


class DisconnectProbeTest(unittest.TestCase):
    def setUp(self):
        self._boot_patch = patch.object(probe, "_boot_id", return_value="mock-boot")
        self._boot_patch.start()
        self.addCleanup(self._boot_patch.stop)

    def _call(self, *args):
        output = StringIO()
        with redirect_stdout(output):
            rc = probe.main(list(args))
        payload = json.loads(output.getvalue())
        return rc, payload

    def test_fixed_fixture_survives_parent_exit_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "ms07b-disconnect-probe-test-source-TEST-retain"
            started = time.monotonic()
            identities = {}

            def fake_identity(pid):
                identities.setdefault(pid, {"pid": pid, "pid_starttime_ticks": pid + 100, "uid": os.getuid(), "boot_id": "mock-boot"})
                return identities[pid] if time.monotonic() - started < 1.2 else None

            with patch.object(probe, "_proc_available", return_value=True), patch.object(probe, "_proc_identity", side_effect=fake_identity):
                rc, launched = self._call(
                    "launch",
                    "--scope-dir", str(scope),
                    "--source-thread-id", "TEST-SOURCE",
                    "--task-id", "ms07b-test",
                    "--retention", "retain",
                    "--duration-seconds", "1",
                )
                self.assertEqual(rc, 0)
                self.assertEqual(launched["state"], "RUNNING")
                self.assertEqual(launched["scope"]["source_thread_id"], "TEST-SOURCE")
                self.assertTrue(scope.joinpath("scope.json").is_file())
                rc, inspected = self._call("inspect", "--scope-dir", str(scope))
                self.assertEqual(rc, 0)
                self.assertEqual(inspected["state"], "RUNNING")
                rc, still_running = self._call("cleanup", "--scope-dir", str(scope))
                self.assertNotEqual(rc, 0)
                self.assertEqual(still_running["error"], "STILL_RUNNING")
                self.assertTrue(scope.joinpath("probe.json").exists())
                time.sleep(1.3)
                rc, cleaned = self._call("cleanup", "--scope-dir", str(scope))
            self.assertEqual(rc, 0)
            self.assertEqual(cleaned["cleanup"], "PASS")
            self.assertTrue(scope.joinpath("cleanup.json").is_file())
            self.assertFalse(scope.joinpath("probe.json").exists())

    def test_cleanup_rejects_identity_mismatch_without_deleting_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "ms07b-disconnect-probe-test-source-TEST-retain"
            scope.mkdir()
            probe._write_scope_marker(scope, "TEST-SOURCE", "ms07b-test", "retain")
            record = probe._record_template(scope, "TEST-SOURCE", "ms07b-test", "retain")
            record.update({"pid": os.getpid(), "pid_starttime_ticks": 100, "uid": os.getuid()})
            probe._write_json(scope / "probe.json", record)
            mismatched = {"pid": os.getpid(), "pid_starttime_ticks": 999, "uid": os.getuid(), "boot_id": "other-boot", "state": "Z"}
            with patch.object(probe, "_proc_available", return_value=True), patch.object(probe, "_proc_identity", return_value=mismatched):
                rc, payload = self._call("cleanup", "--scope-dir", str(scope))
            self.assertNotEqual(rc, 0)
            self.assertEqual(payload["error"], "IDENTITY_MISMATCH")
            self.assertTrue(scope.joinpath("probe.json").exists())

    def test_cleanup_rejects_corrupt_record_and_scope_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "ms07b-disconnect-probe-test-source-TEST-retain"
            scope.mkdir()
            probe._write_json(scope / "scope.json", {"scope_marker": "wrong"})
            (scope / "probe.json").write_text("not-json", encoding="utf-8")
            rc, payload = self._call("cleanup", "--scope-dir", str(scope))
            self.assertNotEqual(rc, 0)
            self.assertEqual(payload["error"], "CORRUPT_RECORD")
            self.assertTrue(scope.joinpath("probe.json").exists())

    def test_rejects_symlink_and_invalid_pid_before_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "ms07b-disconnect-probe-real-retain"
            real.mkdir()
            probe._write_scope_marker(real, "TEST-SOURCE", "ms07b-test", "retain")
            outside = root / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            (real / "probe.json").symlink_to(outside)
            rc, payload = self._call("cleanup", "--scope-dir", str(real))
            self.assertNotEqual(rc, 0)
            self.assertEqual(payload["error"], "INVALID_PATH")
            symlink_scope = root / "ms07b-disconnect-probe-link-retain"
            symlink_scope.symlink_to(real, target_is_directory=True)
            rc, payload = self._call("cleanup", "--scope-dir", str(symlink_scope))
            self.assertNotEqual(rc, 0)
            self.assertEqual(payload["error"], "INVALID_PATH")

    def test_rejects_bool_or_small_pid_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "ms07b-disconnect-probe-test-source-TEST-retain"
            scope.mkdir()
            probe._write_scope_marker(scope, "TEST-SOURCE", "ms07b-test", "retain")
            record = probe._record_template(scope, "TEST-SOURCE", "ms07b-test", "retain")
            record.update({"pid": True, "pid_starttime_ticks": 1, "uid": 1000})
            probe._write_json(scope / "probe.json", record)
            rc, payload = self._call("cleanup", "--scope-dir", str(scope))
            self.assertNotEqual(rc, 0)
            self.assertEqual(payload["error"], "CORRUPT_RECORD")

    def test_linux_boot_id_requires_proc(self):
        self._boot_patch.stop()
        with patch.object(probe, "_proc_available", return_value=False):
            with self.assertRaises(probe.ProbeError) as raised:
                probe._boot_id()
        self.assertEqual(raised.exception.code, "PROC_UNKNOWN")

    def test_malformed_proc_stat_is_unknown_not_exited(self):
        with patch.object(probe, "_proc_available", return_value=True), patch.object(probe.Path, "read_text", return_value="bad"):
            with self.assertRaises(probe.ProbeError) as raised:
                probe._proc_identity(12345)
        self.assertEqual(raised.exception.code, "PROC_UNKNOWN")

    def test_cleanup_rejects_cleanup_json_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "ms07b-disconnect-probe-test-source-TEST-retain"
            scope.mkdir()
            probe._write_scope_marker(scope, "TEST-SOURCE", "ms07b-test", "retain")
            record = probe._record_template(scope, "TEST-SOURCE", "ms07b-test", "retain")
            record.update({"pid": os.getpid(), "pid_starttime_ticks": 100, "uid": os.getuid()})
            probe._write_json(scope / "probe.json", record)
            outside = Path(tmp) / "outside-cleanup.json"
            outside.write_text("{}", encoding="utf-8")
            (scope / "cleanup.json").symlink_to(outside)
            with patch.object(probe, "_proc_available", return_value=True), patch.object(probe, "_proc_identity", return_value=None):
                rc, payload = self._call("cleanup", "--scope-dir", str(scope))
            self.assertNotEqual(rc, 0)
            self.assertEqual(payload["error"], "INVALID_PATH")
            self.assertEqual(outside.read_text(encoding="utf-8"), "{}")


if __name__ == "__main__":
    unittest.main()
