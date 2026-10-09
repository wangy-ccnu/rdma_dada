"""Pure adapter rejection tests and explicit-binary compiler integration."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.multiserver.bundle import build_bundle
from scripts.multiserver.compiler import resolve_instance
from multiserver_fixture import write_example_inputs, write_weight_fixture
from multiserver_deployment_test import use_second_gpu_on_node_a


BINARY = None  # Set only by the explicit integration entry point below.


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AdapterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ms05-unit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        inputs = write_example_inputs(self.root / "inputs")
        self.bundle = self.root / "bundle"
        self.plan = build_bundle(inputs[0], inputs[1], self.bundle, inventory_path=inputs[2])
        self.instance = self.bundle / self.plan["instances"][0]["path"]
        self.output = self.root / "resolved"
        self.compiler = Path(sys.executable).resolve()

    def reject(self, code, completed=None, side_effect=None):
        with patch("scripts.multiserver.compiler.subprocess.run",
                   return_value=completed, side_effect=side_effect):
            with self.assertRaisesRegex(ValueError, code) as caught:
                resolve_instance(self.instance, self.compiler, self.output)
        self.assertFalse(self.output.exists())
        return caught.exception

    def test_missing_binary_creates_no_output(self):
        with self.assertRaisesRegex(ValueError, "COMPILER_BINARY"):
            resolve_instance(self.instance, self.root / "missing", self.output)
        self.assertFalse(self.output.exists())

    def test_non_executable_binary(self):
        path = self.root / "not-executable"
        path.write_bytes(b"not executable")
        with self.assertRaisesRegex(ValueError, "COMPILER_BINARY"):
            resolve_instance(self.instance, path, self.output)

    def test_existing_empty_or_populated_output_is_preserved(self):
        self.output.mkdir()
        for populated in (False, True):
            if populated:
                (self.output / "sentinel").write_text("keep")
            with self.assertRaisesRegex(ValueError, "DEST_EXISTS"):
                resolve_instance(self.instance, self.compiler, self.output)
            self.assertTrue(self.output.is_dir())
        self.assertEqual((self.output / "sentinel").read_text(), "keep")

    def test_output_inside_bundle_instance_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "OUTPUT_OVERLAP"):
            resolve_instance(self.instance, self.compiler, self.instance / "resolved")

    def test_output_anywhere_in_portable_bundle_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "OUTPUT_OVERLAP"):
            resolve_instance(self.instance, self.compiler, self.bundle / "resolved")

    def test_missing_resource_is_a_value_error(self):
        (self.instance / "weights.npy").unlink()
        with self.assertRaisesRegex(ValueError, "RESOURCE_IO"):
            resolve_instance(self.instance, self.compiler, self.output)

    def test_replaced_workspace_is_never_written_through(self):
        foreign = self.root / "foreign"
        foreign.mkdir()
        (foreign / "resolution.json").write_text("foreign evidence")
        def replace_workspace(argv, **kwargs):
            workspace = Path(argv[-1]).parent
            workspace.rename(self.root / "retained-workspace")
            workspace.symlink_to(foreign, target_is_directory=True)
            return subprocess.CompletedProcess(argv, 1, "", "failed")
        error = self.reject("STAGING_OWNERSHIP", side_effect=replace_workspace)
        self.assertEqual((foreign / "resolution.json").read_text(), "foreign evidence")
        self.assertEqual(error.details["returncode"], 1)

    def test_failed_check_is_rejected(self):
        report = self.valid_report()
        report["checks"][0]["status"] = "FAIL"
        self.reject("COMPILER_REPORT", subprocess.CompletedProcess([], 0, json.dumps(report), ""))

    @staticmethod
    def valid_report():
        return {"valid": True, "schema_version": 1, "config_id": "a" * 64,
                "geometry_id": "b" * 64, "checks": [{"name": n, "status": "PASS"}
                for n in ("observation_schema", "wire_contract", "checked_geometry", "identity_verification", "header_transform")]}

    def invalid_artifact_result(self, argv, **kwargs):
        # Deliberately invalid artifact response; never used as success evidence.
        directory = Path(argv[-1])
        directory.mkdir()
        report = self.valid_report()
        (directory / "validation_report.json").write_text(json.dumps(report))
        for name in ("resolved_observation.json", "ring_plan.json"):
            (directory / name).write_text(json.dumps({"config_id": "a" * 64, "geometry_id": "b" * 64}))
        for name in ("raw.header", "unpacked.header", "converted.header", "beamformed.header", "output.header"):
            (directory / name).write_bytes(b"\0" * 4096)
        lines = [sha(p) + "  " + p.name + "\n" for p in sorted(directory.iterdir())]
        (directory / "MANIFEST.sha256").write_text("".join(lines))
        (directory / "raw.header").write_bytes(b"bad")
        return subprocess.CompletedProcess(argv, 0, json.dumps(report), "")

    def test_tampered_artifact_manifest_is_rejected(self):
        self.reject("COMPILER_ARTIFACT", side_effect=self.invalid_artifact_result)

    def test_symlink_output_is_rejected_without_touching_target(self):
        target = self.root / "target"
        target.mkdir()
        self.output.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "SYMLINK"):
            resolve_instance(self.instance, self.compiler, self.output)
        self.assertEqual(list(target.iterdir()), [])

    def test_nonzero_preserves_argv_exit_and_stderr(self):
        error = self.reject("COMPILER_EXIT", subprocess.CompletedProcess([], 7, "out", "bad geometry\n"))
        evidence = error.details
        self.assertEqual(evidence["returncode"], 7)
        self.assertEqual(evidence["stderr"], "bad geometry\n")
        self.assertEqual(evidence["stdout"], "out")
        self.assertEqual(evidence["argv"][:3], [str(self.compiler), "--config", str(self.instance / "observation.json")])
        self.assertEqual(evidence["argv"][3], "--output-dir")
        record = json.loads((Path(evidence["evidence_directory"]) / "resolution.json").read_text())
        self.assertFalse(record["ok"])
        self.assertEqual(record["stderr"], "bad geometry\n")
        self.assertEqual(record["compiler"]["sha256"], sha(self.compiler))

    def test_launch_failure_is_structured(self):
        error = self.reject("COMPILER_LAUNCH", side_effect=OSError("exec format error"))
        self.assertIsNone(error.details["returncode"])
        self.assertIn("exec format error", error.details["stderr"])

    def test_invalid_json_or_nonfinite_report(self):
        for report in ("not json", "[]", '{"valid":true,"x":NaN}', '{"valid":true,"valid":false}'):
            with self.subTest(report=report):
                self.reject("COMPILER_REPORT", subprocess.CompletedProcess([], 0, report, "diagnostic"))

    def test_false_missing_or_numeric_valid_is_rejected(self):
        for report in ({"valid": False}, {}, {"valid": 1}, {"valid": True}):
            with self.subTest(report=report):
                self.reject("COMPILER_REPORT", subprocess.CompletedProcess([], 0, json.dumps(report), ""))

    def test_missing_artifacts_is_not_success(self):
        report = self.valid_report()
        self.reject("COMPILER_ARTIFACT", subprocess.CompletedProcess([], 0, json.dumps(report), ""))

    def test_cli_resolve_failure_is_json(self):
        result = subprocess.run([sys.executable, "-B", "scripts/multiserver_compile.py", "resolve",
                                 "--instance-dir", str(self.instance), "--compiler", str(self.root / "missing"),
                                 "--output", str(self.output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertFalse(report["ok"])
        self.assertEqual(report["error"]["code"], "COMPILER_BINARY")

    def test_cli_preserves_compiler_failure_details(self):
        from scripts.multiserver_compile import main
        from contextlib import redirect_stdout
        import io
        stream = io.StringIO()
        with patch("scripts.multiserver.compiler.subprocess.run", return_value=subprocess.CompletedProcess([], 9, "", "invalid shape")), redirect_stdout(stream):
            status = main(["resolve", "--instance-dir", str(self.instance), "--compiler", str(self.compiler), "--output", str(self.output)])
        self.assertEqual(status, 1)
        failure = json.loads(stream.getvalue())
        self.assertEqual(failure["compiler_failure"]["returncode"], 9)
        self.assertEqual(failure["compiler_failure"]["stderr"], "invalid shape")

    def test_input_hash_io_failure_is_structured(self):
        with patch("scripts.multiserver.compiler._sha_file", side_effect=OSError("input not readable")):
            with self.assertRaisesRegex(ValueError, "RESOLVE_IO"):
                resolve_instance(self.instance, self.compiler, self.output)

@unittest.skipUnless(BINARY, "real compiler integration requires explicit BINARY entry point")
class CompilerIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ms05-real-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.inputs = write_example_inputs(self.root / "inputs")

    def build(self, *, npol=1, dtype="|i1", version=1, duration=None, same_host=False):
        self.npol, self.dtype, self.version = npol, dtype, version
        g, d, i = self.inputs
        document = json.loads(g.read_text())
        document["observation"]["npol"] = npol
        document["observation"]["station_ids"] = [1003, 1001, 1000, 1002]
        if npol == 2:
            document["processing"]["product"] = "COHERENCY"
        if duration:
            document["observation"]["duration_seconds"] = duration
        profiles = json.loads(d.read_text())
        if same_host:
            use_second_gpu_on_node_a(profiles, document)
        g.write_text(json.dumps(document))
        d.write_text(json.dumps(profiles))
        self.payload = write_weight_fixture(g.parent / document["processing"]["weights"]["path"],
                                            (7, npol, 4, 2, 2), dtype, version=version)
        self.bundle = self.root / "bundle"
        self.plan = build_bundle(g, d, self.bundle, inventory_path=i)

    def resolve(self, index):
        instance = self.bundle / self.plan["instances"][index]["path"]
        before = {p.relative_to(self.bundle).as_posix(): sha(p) for p in self.bundle.rglob("*") if p.is_file()}
        output = self.root / ("resolved-" + str(index))
        result = resolve_instance(instance, BINARY, output)
        after = {p.relative_to(self.bundle).as_posix(): sha(p) for p in self.bundle.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertTrue(result["ok"])
        self.assertTrue(result["published"])
        self.assertFalse(result["runtime_ready"])
        self.assertEqual(result["compiler"]["sha256"], sha(BINARY))
        self.assertEqual(result["inputs"][str(instance / "observation.json")], sha(instance / "observation.json"))
        self.assertEqual(result["inputs"][str(instance / "weights.npy")], sha(instance / "weights.npy"))
        self.assertEqual(result["inputs"][str(instance / "wire-profile.json")], sha(instance / "wire-profile.json"))
        self.assertEqual(result["report"]["inputs"]["observation"], str(instance / "observation.json"))
        self.assertEqual(result["report"]["inputs"]["wire_profile"], str(instance / "wire-profile.json"))
        for name, digest in result["artifacts"].items():
            self.assertEqual(sha(output / name), digest)
        resolved = json.loads((output / "resolved_observation.json").read_text())
        source = json.loads(resolved["source_json"])
        self.assertEqual(resolved["source_path"], str(instance / "observation.json"))
        self.assertEqual(source["processing"]["modules"][0]["weights_file"], str(instance / "weights.npy"))
        self.assertEqual(source["observation"]["station_ids"], [1003,1001,1000,1002])
        self.assertEqual(source["blocks"]["groups_per_block"], 32)
        self.assertEqual(resolved["resolved"]["samples_per_block"], 16384)
        self.assertEqual(resolved["resolved"]["group_period_ps"] * 32, 16384000000)
        self.assertEqual(resolved["resolved"]["output_samples_per_block"], 128)
        self.assertEqual((instance / "weights.npy").read_bytes()[6:8], bytes((self.version,0)))
        item_bytes = 1 if self.dtype == "|i1" else 2
        bytes_per_f = 1 * self.npol * 4 * 2 * 2 * item_bytes
        start,end = (0,4) if index == 0 else (4,7)
        expected = self.payload[start*bytes_per_f:end*bytes_per_f]
        self.assertEqual((instance / "weights.npy").read_bytes()[-len(expected):], expected)
        for name, order in (("raw.header", "TFP"), ("unpacked.header", "ATFP"), ("converted.header", "TFPA"), ("beamformed.header", "TFPB")):
            values = dict(line.split(None,1) for line in (output/name).read_bytes().split(b"\0",1)[0].decode().splitlines() if line.strip())
            self.assertEqual(values["ORDER"], order)
            self.assertEqual(values["CONFIG_ID"], result["report"]["config_id"])
            self.assertEqual(values["GEOMETRY_ID"], result["report"]["geometry_id"])
        header = dict(line.split(None, 1) for line in (output / "output.header").read_bytes().split(b"\0", 1)[0].decode().splitlines() if line.strip())
        return result, resolved, header

    def test_power_f4_f3_geometry_headers_weights_and_common_grid(self):
        self.build()
        identities = []
        for idx, f, first, compute, record, raw, power in ((0,4,100,524288,4128,528384,4096), (1,3,104,393216,3104,397312,3072)):
            with self.subTest(f=f):
                result, resolved, header = self.resolve(idx)
                derived = result["report"]["derived"]
                self.assertEqual([derived[k] for k in ("compute_block_bytes", "raw_record_bytes", "raw_block_bytes", "group_period_ps", "expected_groups")], [compute,record,raw,512000000,2048])
                self.assertEqual(header["NCHAN"], str(f))
                self.assertEqual(header["FIRST_CHANNEL_ID"], str(first))
                self.assertEqual(header["OUTPUT_BLOCK_BYTES"], str(power))
                self.assertEqual(header["TSAMP"], "128")
                self.assertEqual(header["ORDER"], "TFPB")
                self.assertEqual(header["NBEAM"], "2")
                self.assertEqual(header["NPOL"], "1")
                self.assertEqual(header["CONFIG_ID"], result["report"]["config_id"])
                self.assertEqual(header["GEOMETRY_ID"], result["report"]["geometry_id"])
                identities.append(result["report"]["geometry_id"])
        self.assertNotEqual(*identities)

    def test_p2_coherency_has_four_products(self):
        self.build(npol=2)
        for idx, expected in ((0,16384), (1,12288)):
            result, resolved, header = self.resolve(idx)
            self.assertEqual(header["OUTPUT_BLOCK_BYTES"], str(expected))
            self.assertEqual(header["NPRODUCT"], "4")
            self.assertEqual(header["PRODUCTS"], "AA,BB,AB_REAL,AB_IMAG")
            self.assertEqual(header["TSAMP"], "128")
            self.assertEqual(header["ORDER"], "TFBS")
            self.assertEqual(header["POL_LABELS"], "X,Y")

    def test_same_host_gpu0_gpu1(self):
        self.build(same_host=True)
        for idx in (0,1):
            result, resolved, header = self.resolve(idx)
            self.assertEqual(result["report"]["valid"], True)
            self.assertEqual(header["CUDA_DEVICE"], str(idx))

    def test_destination_race_does_not_replace_foreign_directory(self):
        from scripts.multiserver.bundle import _publish_exclusive
        self.build()
        instance = self.bundle / self.plan["instances"][0]["path"]
        output = self.root / "race-output"
        def collision(source, destination):
            destination.mkdir()
            (destination / "foreign").write_text("keep")
            _publish_exclusive(source, destination)
        with patch("scripts.multiserver.compiler._publish_exclusive", side_effect=collision):
            with self.assertRaisesRegex(ValueError, "DEST_EXISTS") as caught:
                resolve_instance(instance, BINARY, output)
        self.assertEqual((output / "foreign").read_text(), "keep")
        self.assertEqual({p.name for p in output.iterdir()}, {"foreign"})
        self.assertFalse(caught.exception.details["ok"])
        evidence = Path(caught.exception.details["evidence_directory"])
        self.assertFalse(json.loads((evidence / "resolution.json").read_text())["ok"])

    @staticmethod
    def fail_workspace_cleanup(path):
        if path.name.startswith(".multiserver-resolve-"):
            raise PermissionError("injected owned workspace cleanup failure")
        raise AssertionError("unexpected rmdir path: " + str(path))

    def test_published_output_cleanup_failure_returns_persisted_warning(self):
        self.build()
        instance = self.bundle / self.plan["instances"][0]["path"]
        output = self.root / "cleanup-warning"
        with patch.object(Path, "rmdir", self.fail_workspace_cleanup):
            result = resolve_instance(instance, BINARY, output)
        self.assertTrue(result["ok"])
        self.assertTrue(result["published"])
        self.assertEqual(result["evidence_directory"], str(output))
        self.assertEqual(result["cleanup_warning"]["code"], "RESOLVE_CLEANUP")
        self.assertIn("injected owned workspace cleanup failure", result["cleanup_warning"]["message"])
        self.assertTrue(Path(result["cleanup_warning"]["directory"]).is_dir())
        self.assertEqual(json.loads((output / "resolution.json").read_text()), result)
        for name, digest in result["artifacts"].items():
            self.assertEqual(sha(output / name), digest)

    def test_cli_published_cleanup_warning_is_successful_json(self):
        from scripts.multiserver_compile import main
        from contextlib import redirect_stdout
        import io
        self.build()
        instance = self.bundle / self.plan["instances"][0]["path"]
        output = self.root / "cli-cleanup-warning"
        stream = io.StringIO()
        with patch.object(Path, "rmdir", self.fail_workspace_cleanup), redirect_stdout(stream):
            status = main(["resolve", "--instance-dir", str(instance), "--compiler", str(BINARY), "--output", str(output)])
        self.assertEqual(status, 0)
        result = json.loads(stream.getvalue())
        self.assertTrue(result["ok"])
        self.assertTrue(result["published"])
        self.assertEqual(result["evidence_directory"], str(output))
        self.assertEqual(result["cleanup_warning"]["code"], "RESOLVE_CLEANUP")
        self.assertEqual(json.loads((output / "resolution.json").read_text()), result)

    def test_cleanup_warning_write_failure_reports_published_output(self):
        from scripts.multiserver_compile import main
        from contextlib import redirect_stdout
        import io
        self.build()
        instance = self.bundle / self.plan["instances"][0]["path"]
        output = self.root / "cleanup-warning-write-error"
        with patch.object(Path, "rmdir", self.fail_workspace_cleanup), patch("scripts.multiserver.compiler.os.replace", side_effect=PermissionError("injected evidence replacement failure")):
            with self.assertRaisesRegex(ValueError, "RESOLVE_CLEANUP_EVIDENCE") as caught:
                resolve_instance(instance, BINARY, output)
        result = caught.exception.details
        self.assertTrue(result["published"])
        self.assertEqual(result["output"], str(output))
        self.assertEqual(result["evidence_directory"], str(output))
        self.assertEqual(result["cleanup_warning"]["code"], "RESOLVE_CLEANUP")
        self.assertIn("injected evidence replacement failure", result["diagnostic_error"])
        self.assertTrue(json.loads((output / "resolution.json").read_text())["published"])
        for name, digest in result["artifacts"].items():
            self.assertEqual(sha(output / name), digest)
        cli_output = self.root / "cli-cleanup-warning-write-error"
        stream = io.StringIO()
        with patch.object(Path, "rmdir", self.fail_workspace_cleanup), patch("scripts.multiserver.compiler.os.replace", side_effect=PermissionError("injected evidence replacement failure")), redirect_stdout(stream):
            status = main(["resolve", "--instance-dir", str(instance), "--compiler", str(BINARY), "--output", str(cli_output)])
        self.assertEqual(status, 1)
        failure = json.loads(stream.getvalue())
        self.assertEqual(failure["error"]["code"], "RESOLVE_CLEANUP_EVIDENCE")
        self.assertTrue(failure["compiler_failure"]["published"])
        self.assertEqual(failure["compiler_failure"]["output"], str(cli_output))
        self.assertTrue(json.loads((cli_output / "resolution.json").read_text())["published"])

    def test_input_change_during_compilation_is_rejected(self):
        self.build()
        instance = self.bundle / self.plan["instances"][0]["path"]
        output = self.root / "changed-input"
        run = subprocess.run
        def mutate(argv, **kwargs):
            completed = run(argv, **kwargs)
            weights = instance / "weights.npy"
            raw = weights.read_bytes()
            weights.write_bytes(raw[:-1] + bytes((raw[-1] ^ 1,)))
            return completed
        with patch("scripts.multiserver.compiler.subprocess.run", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "COMPILER_INPUT_CHANGED"):
                resolve_instance(instance, BINARY, output)
        self.assertFalse(output.exists())

    def test_valid_partial_tail(self):
        self.build(duration="0.016896")  # 33 packet groups, one full block + 512-sample tail.
        for idx in (0,1):
            result, resolved, header = self.resolve(idx)
            self.assertEqual(result["report"]["derived"]["expected_groups"], 33)
            self.assertEqual(header["TRANSFER_SIZE"], "4224" if idx == 0 else "3168")

    def test_incompatible_duration_rejected_by_existing_compiler(self):
        self.build()
        instance = self.bundle / self.plan["instances"][0]["path"]
        path = instance / "observation.json"
        document = json.loads(path.read_text())
        document["observation"]["duration_seconds"] = "0.016896000001"
        path.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, "COMPILER_EXIT") as caught:
            resolve_instance(instance, BINARY, self.root / "invalid")
        self.assertIn("integer number of groups", caught.exception.details["stderr"])
        self.assertFalse((self.root / "invalid").exists())

    def test_npy_v1_v2_int8_compiles(self):
        for version in (1,2):
            dtype = "|i1"
            with self.subTest(version=version, dtype=dtype):
                self.build(dtype=dtype, version=version)
                for idx in (0,1):
                    self.resolve(idx)
                shutil.rmtree(self.bundle)
                for idx in (0,1):
                    shutil.rmtree(self.root / ("resolved-" + str(idx)))

    def test_int16_v1_v2_is_preserved_and_rejected_for_ci8_input(self):
        # Existing metadata reader accepts int16; full CI8 Observation requires matching width.
        for version in (1,2):
            with self.subTest(version=version):
                self.build(dtype="<i2", version=version)
                for idx in (0,1):
                    instance = self.bundle / self.plan["instances"][idx]["path"]
                    with self.assertRaisesRegex(ValueError, "COMPILER_EXIT") as caught:
                        resolve_instance(instance, BINARY, self.root / ("invalid-" + str(idx)))
                    self.assertIn("weight dtype must match antenna input width", caught.exception.details["stderr"])
                    self.assertFalse((self.root / ("invalid-" + str(idx))).exists())
                shutil.rmtree(self.bundle)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--unit":
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(AdapterTest)
    elif len(sys.argv) == 2:
        BINARY = Path(sys.argv[1]).resolve()
        if not BINARY.is_file():
            raise SystemExit("explicit compiler binary does not exist: " + str(BINARY))
        # Decorator was evaluated before argv; integration is explicitly enabled here.
        CompilerIntegrationTest.__unittest_skip__ = False
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(CompilerIntegrationTest)
    else:
        raise SystemExit("usage: multiserver_compile_test.py --unit | BINARY (required; no fallback)")
    sys.exit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)
