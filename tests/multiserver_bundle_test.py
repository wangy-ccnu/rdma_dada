import copy
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import multiserver_deployment_test as deployment_tests
import scripts.multiserver.bundle as bundle

from multiserver_fixture import write_example_inputs, write_weight_fixture
from multiserver_deployment_test import use_second_gpu_on_node_a
from scripts.multiserver.bundle import build_bundle
from scripts.multiserver.weights import slice_weights


ROOT = pathlib.Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def tree_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class BundleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # macOS tempfile uses /var, a system alias of /private/var. Supply the
        # actual directory so symlink-rejection tests target user-owned links.
        self.root = pathlib.Path(self.temp.name).resolve()
        self.g, self.d, self.i = write_example_inputs(self.root / "inputs")
        self.output = self.root / "bundle"

    def build(self, output=None):
        return build_bundle(self.g, self.d, output or self.output, inventory_path=self.i)

    def invalid(self, code):
        with self.assertRaisesRegex(ValueError, code):
            self.build()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".multiserver-*")), [])

    def test_deterministic_complete_bytes_and_id_in_two_output_roots(self):
        before = tree_bytes(self.g.parent)
        first = self.build()
        second = self.build(self.root / "different-output")
        self.assertEqual(first, second)
        self.assertEqual(tree_bytes(self.output), tree_bytes(self.root / "different-output"))
        self.assertEqual(tree_bytes(self.g.parent), before)
        self.assertEqual(read_json(self.output / "bundle.json"), first)
        self.assertFalse(first["status"]["runtime_ready"])
        self.assertIn("fresh_host_readiness", first["status"]["unverified_prerequisites"])
        self.assertEqual(read_json(self.output / "global-observation.json"), read_json(self.g))
        self.assertEqual(read_json(self.output / "deployments.json"), read_json(self.d))
        self.assertEqual(read_json(self.output / "inventory.json"), read_json(self.i))
        files = tree_bytes(self.output)
        manifest = files.pop("MANIFEST.sha256").decode().splitlines()
        self.assertEqual(len(manifest), len(files))
        for line in manifest:
            digest, name = line.split("  ", 1)
            self.assertEqual(digest, hashlib.sha256(files[name]).hexdigest())
        self.assertEqual(manifest, sorted(manifest, key=lambda line: line.split("  ", 1)[1]))

    def test_observation_mapping_tail_frequency_and_weights(self):
        result = self.build()
        for instance, expected in zip(result["instances"], [(4, 100, 1_298_500_000, 0), (3, 104, 1_302_000_000, 4)]):
            nchan, first, center, offset = expected
            directory = self.output / instance["path"]
            observation = read_json(directory / "observation.json")
            schema_check = deployment_tests.DeploymentTest()
            schema_check._schema = read_json(ROOT / "config/observation-v1.schema.json")
            schema_check.assert_schema_node(schema_check._schema, observation)
            self.assertEqual(observation["observation"]["nchan"], nchan)
            self.assertEqual(observation["observation"]["first_channel_id"], first)
            self.assertEqual(observation["observation"]["station_ids"], [1000, 1001, 1002, 1003])
            self.assertEqual(observation["metadata"], {"telescope": "CA", "center_frequency_hz": center, "bandwidth_hz": nchan * 1_000_000})
            self.assertEqual(observation["blocks"]["groups_per_block"], 32)
            self.assertEqual(observation["wire"]["profile"], "wire-profile.json")
            self.assertEqual([m["type"] for m in observation["processing"]["modules"]], ["beamform", "power", "integrate"])
            self.assertEqual(instance["weights"]["source_shape"], [7, 1, 4, 2, 2])
            self.assertEqual(instance["weights"]["first_f"], offset)
            self.assertEqual(instance["weights"]["shape"], [nchan, 1, 4, 2, 2])
            self.assertEqual((directory / "wire-profile.json").read_bytes(), (self.g.parent / "packet_formats/frontend.example-v1.json").read_bytes())
            payload = (self.g.parent / "weights/global-f7-p1-a4-b2.npy").read_bytes()[-112:]
            self.assertEqual((directory / "weights.npy").read_bytes()[-nchan * 16:], payload[offset * 16:(offset + nchan) * 16])
            profile = read_json(self.d)["profiles"][instance["deployment_profile"]]
            self.assertEqual(instance["deployment"]["cpu"], profile["cpu"])
            self.assertEqual(instance["deployment"]["flow"], profile["flow"])
            self.assertEqual(observation["receiver"]["device"], profile["receiver"]["device"])
            self.assertEqual(observation["storage"], profile["storage"])
            self.assertEqual(observation["rings"], profile["rings"])
            self.assertEqual(observation["processing"]["cuda_pipeline"], {"mode": "STAGED_PIPELINE", "inflight_blocks": 2})
        self.assertEqual(result["frequency"]["full_band_center_hz"], 1_300_000_000)
        self.assertEqual(result["monitoring"], read_json(self.g)["monitoring"])

    def test_json_key_order_is_canonical_but_changed_science_changes_identity(self):
        original = self.build()["bundle_id"]
        global_doc = read_json(self.g)
        self.g.write_text(json.dumps(dict(reversed(list(global_doc.items()))), indent=4))
        self.assertEqual(self.build(self.root / "reordered")["bundle_id"], original)
        global_doc["metadata"]["telescope"] = "OTHER"
        write_json(self.g, global_doc)
        self.assertNotEqual(self.build(self.root / "science-change")["bundle_id"], original)

    def test_input_resources_are_relative_to_global_file_not_current_directory(self):
        g, d, i = write_example_inputs(self.root / "second-input-root")
        first = self.build()
        second = build_bundle(g, d, self.root / "second-bundle", inventory_path=i)
        self.assertEqual(first, second)

    def test_coherency_beam_count_and_station_order_come_from_inputs(self):
        global_doc = read_json(self.g)
        global_doc["observation"].update({"npol": 2, "station_ids": [1003, 1001, 1000, 1002]})
        global_doc["processing"]["product"] = "COHERENCY"
        write_json(self.g, global_doc)
        write_weight_fixture(self.g.parent / "weights/global-f7-p1-a4-b2.npy", (7, 2, 4, 3, 2), "<i2")
        result = self.build()
        self.assertEqual(result["weights"]["source_shape"], [7, 2, 4, 3, 2])
        observation = read_json(self.output / result["instances"][0]["path"] / "observation.json")
        self.assertEqual(observation["observation"]["station_ids"], [1003, 1001, 1000, 1002])
        self.assertEqual(observation["processing"]["modules"][1], {"type": "stokes"})

    def test_changed_weights_wire_and_profile_change_identity(self):
        original = self.build()["bundle_id"]
        weight = self.g.parent / "weights/global-f7-p1-a4-b2.npy"
        before = weight.read_bytes()
        weight.write_bytes(before[:-1] + bytes([before[-1] ^ 1]))
        self.assertNotEqual(self.build(self.root / "weight-change")["bundle_id"], original)
        weight.write_bytes(before)
        wire = self.g.parent / "packet_formats/frontend.example-v1.json"
        wire.write_bytes(wire.read_bytes() + b"\n")
        wire_id = self.build(self.root / "wire-change")["bundle_id"]
        self.assertNotEqual(wire_id, original)
        deployment = read_json(self.d)
        deployment["profiles"]["example-node-a-gpu0"]["execution"]["cuda_slots"] = 3
        write_json(self.d, deployment)
        self.assertNotEqual(self.build(self.root / "profile-change")["bundle_id"], wire_id)

    def test_same_host_two_gpu_paths_are_distinct(self):
        global_doc, deployment = read_json(self.g), read_json(self.d)
        use_second_gpu_on_node_a(deployment, global_doc)
        write_json(self.g, global_doc)
        write_json(self.d, deployment)
        result = self.build()
        self.assertEqual([i["path"] for i in result["instances"]], ["instances/node-a/gpu-0", "instances/node-a/gpu-1"])
        self.assertTrue((self.output / "instances/node-a/gpu-1/observation.json").is_file())

    def test_full_fpa_mismatch_refused_before_publication(self):
        for shape in [(8, 1, 4, 2, 2), (7, 2, 4, 2, 2), (7, 1, 3, 2, 2)]:
            with self.subTest(shape=shape):
                write_weight_fixture(self.g.parent / "weights/global-f7-p1-a4-b2.npy", shape, "|i1")
                self.invalid("WEIGHTS_GLOBAL_SHAPE")

    def test_invalid_beam_axis_is_refused(self):
        weight = self.g.parent / "weights/global-f7-p1-a4-b2.npy"
        weight.write_bytes(weight.read_bytes().replace(b"(7, 1, 4, 2, 2)", b"(7, 1, 4, 0, 2)"))
        self.invalid("NPY_SHAPE")

    def test_output_count_cannot_be_silently_dropped(self):
        deployment = read_json(self.d)
        deployment["profiles"]["example-node-a-gpu0"]["ring_counts"]["output"] = 9
        write_json(self.d, deployment)
        self.invalid("OUTPUT_RING_COUNT_UNSUPPORTED")

    def test_source_filters_and_nonconcrete_destinations_are_refused(self):
        baseline = read_json(self.d)
        for field, value, code in [("source_ip", "192.0.2.20", "SOURCE_FILTER_UNSUPPORTED"), ("source_port", 1234, "SOURCE_FILTER_UNSUPPORTED"), ("destination_ip", "*", "DESTINATION_REQUIRED"), ("destination_port", None, "DESTINATION_REQUIRED"), ("protocol", "TCP", "PROTOCOL_UNSUPPORTED")]:
            deployment = copy.deepcopy(baseline)
            deployment["profiles"]["example-node-a-gpu0"]["flow"]["match"][field] = value
            write_json(self.d, deployment)
            with self.subTest(field=field):
                self.invalid(code)

    def test_source_wildcards_remain_in_policy(self):
        deployment = read_json(self.d)
        deployment["profiles"]["example-node-a-gpu0"]["flow"]["match"].update({"source_ip": "*", "source_port": None})
        write_json(self.d, deployment)
        result = self.build()
        self.assertEqual(result["instances"][0]["deployment"]["flow"]["match"]["source_ip"], "*")
        observation = read_json(self.output / result["instances"][0]["path"] / "observation.json")
        self.assertNotIn("source_ip", observation["receiver"])

    def test_traversal_identifier_is_refused(self):
        g, d, i = read_json(self.g), read_json(self.d), read_json(self.i)
        g["assignments"][0]["server_id"] = "../escape"
        d["profiles"]["example-node-a-gpu0"]["server_id"] = "../escape"
        i["hosts"][0]["aliases"][0] = "../escape"
        write_json(self.g, g)
        write_json(self.d, d)
        write_json(self.i, i)
        self.invalid("UNSAFE_IDENTIFIER")

    def test_resource_traversal_and_symlink_are_refused(self):
        g = read_json(self.g)
        g["wire"]["profile"] = "../outside.json"
        write_json(self.g, g)
        self.invalid("RESOURCE_PATH")
        g["wire"]["profile"] = "packet_formats/frontend.example-v1.json"
        write_json(self.g, g)
        wire = self.g.parent / g["wire"]["profile"]
        copy_path = self.root / "outside-wire.json"
        copy_path.write_bytes(wire.read_bytes())
        wire.unlink()
        wire.symlink_to(copy_path)
        self.invalid("SYMLINK")

    def test_existing_nonempty_destination_and_symlink_preserved(self):
        self.output.mkdir()
        sentinel = self.output / "keep"
        sentinel.write_bytes(b"owned elsewhere")
        with self.assertRaisesRegex(ValueError, "DEST_EXISTS"):
            self.build()
        self.assertEqual(sentinel.read_bytes(), b"owned elsewhere")
        alias = self.root / "alias"
        alias.symlink_to(self.output, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "SYMLINK"):
            self.build(alias)

    def test_existing_empty_destination_is_supported(self):
        self.output.mkdir()
        self.build()
        self.assertTrue((self.output / "MANIFEST.sha256").is_file())

    def test_second_instance_failure_leaves_no_partial_bundle(self):
        calls = []
        def fail_second(*args):
            calls.append(args)
            if len(calls) == 2:
                raise ValueError("INJECTED_SECOND_SLICE_FAILURE")
            return slice_weights(*args)
        with mock.patch("scripts.multiserver.bundle.slice_weights", side_effect=fail_second):
            self.invalid("INJECTED_SECOND_SLICE_FAILURE")
        self.assertEqual(len(calls), 2)

    def test_racing_empty_destination_is_not_overwritten(self):
        publish = bundle._publish_exclusive
        inode = []
        def race(stage, destination):
            destination.mkdir()
            inode.append(destination.stat().st_ino)
            publish(stage, destination)
        with mock.patch.object(bundle, "_publish_exclusive", side_effect=race):
            with self.assertRaisesRegex(ValueError, "DEST_EXISTS"):
                self.build()
        self.assertEqual(self.output.stat().st_ino, inode[0])
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(list(self.root.glob(".multiserver-*")), [])

    def test_output_parent_symlink_is_refused(self):
        alias = self.root / "parent-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "SYMLINK"):
            self.build(alias / "bundle")
        self.assertFalse(self.output.exists())

    def test_weight_change_between_instances_prevents_publication(self):
        calls = []
        def change_after_first(*args):
            evidence = slice_weights(*args)
            calls.append(args)
            if len(calls) == 1:
                weight = pathlib.Path(args[0])
                content = weight.read_bytes()
                weight.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
            return evidence
        with mock.patch.object(bundle, "slice_weights", side_effect=change_after_first):
            self.invalid("RESOURCE_CHANGED")

    def test_staged_weights_must_match_slice_evidence_before_publish(self):
        def corrupt_slice(*args):
            evidence = slice_weights(*args)
            local = pathlib.Path(args[1])
            content = local.read_bytes()
            local.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
            return evidence
        with mock.patch.object(bundle, "slice_weights", side_effect=corrupt_slice):
            self.invalid("SLICE_HASH_MISMATCH")

    def test_cleanup_io_failure_has_structured_diagnostic(self):
        with mock.patch.object(bundle, "slice_weights", side_effect=ValueError("SLICE_FAILURE")):
            with mock.patch.object(bundle.shutil, "rmtree", side_effect=OSError("cleanup refused")):
                with self.assertRaisesRegex(ValueError, "BUNDLE_CLEANUP"):
                    self.build()
        self.assertFalse(self.output.exists())

    def test_invalid_json_duplicate_keys_and_nonfinite_fail(self):
        for content in ["{", '{"schema_version":1,"schema_version":1}', '{"bad":NaN}', '{"bad":1e999}']:
            self.g.write_text(content)
            with self.subTest(content=content):
                self.invalid("JSON_")

    def test_missing_resources_and_unsupported_wire_refused(self):
        wire = self.g.parent / "packet_formats/frontend.example-v1.json"
        wire.unlink()
        self.invalid("RESOURCE_IO")
        write_json(wire, {"schema_version": 2, "format_id": "unsupported"})
        self.invalid("WIRE_UNSUPPORTED")

    def test_cli_success_and_structured_failures(self):
        cli = [sys.executable, "-B", str(ROOT / "scripts/multiserver_compile.py")]
        args = ["compile", "--global", str(self.g), "--deployments", str(self.d), "--inventory", str(self.i), "--output", str(self.output)]
        success = subprocess.run(cli + args, capture_output=True, text=True, cwd=self.root)
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(json.loads(success.stdout)["bundle_id"], read_json(self.output / "bundle.json")["bundle_id"])
        failure = subprocess.run(cli + args, capture_output=True, text=True)
        self.assertNotEqual(failure.returncode, 0)
        self.assertEqual(json.loads(failure.stdout)["error"]["code"], "DEST_EXISTS")
        missing = subprocess.run(cli + ["compile"], capture_output=True, text=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(json.loads(missing.stdout)["error"]["code"], "CLI_ARGUMENT")
        version = subprocess.run(cli + ["--version"], capture_output=True, text=True)
        self.assertEqual(version.returncode, 0)
        self.assertIn("multiserver-bundle-v1", version.stdout)
        no_inventory = subprocess.run(cli + args[:5] + args[7:], capture_output=True, text=True)
        self.assertNotEqual(no_inventory.returncode, 0)
        self.assertEqual(json.loads(no_inventory.stdout)["error"]["code"], "CLI_ARGUMENT")
        resolve = subprocess.run(cli + ["resolve"], capture_output=True, text=True)
        self.assertNotEqual(resolve.returncode, 0)
        self.assertEqual(json.loads(resolve.stdout)["error"]["code"], "CLI_ARGUMENT")


if __name__ == "__main__":
    unittest.main()
