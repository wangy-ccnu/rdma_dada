import copy
import json
import math
import pathlib
import re
import unittest

from multiserver_fixture import deployment_example, global_example, inventory_example
from scripts.multiserver.deployment import validate_deployment
from scripts.multiserver.model import validate_global


def use_second_gpu_on_node_a(deployments, source):
    profile = deployments["profiles"]["example-node-b-gpu0"]
    profile["server_id"] = "node-a"
    profile["gpu_id"] = 1
    profile["cpu"] = {
        "receiver": [13],
        "unpack": [14],
        "gpu": [15],
        "workers": [16, 17],
        "sink": [18],
        "control": [19],
    }
    profile["numa"] = {
        "receiver": 1, "unpack": 1, "gpu": 1, "workers": 1, "sink": 1, "control": 1,
    }
    profile["paths"] = {"run_dir": "runs/node-a-gpu1", "log_dir": "logs/node-a-gpu1"}
    profile["rings"] = {"raw_key": "0x1101", "compute_key": "0x1102", "output_key": "0x1103"}
    profile["flow"]["physical_port"] = "port-a"
    profile["receiver"] = {
        "device": "mlx5_fake_a",
        "destination_mac": "02:00:00:00:00:0a",
    }
    source["assignments"][1].update({"server_id": "node-a", "gpu_id": 1})


class DeploymentTest(unittest.TestCase):
    def checked(self, deployments=None, inventory=None, source=None):
        return validate_deployment(
            validate_global(source or global_example()),
            deployments or deployment_example(),
            inventory or inventory_example(),
        )

    def assert_invalid(self, deployments, inventory, code, source=None):
        with self.assertRaisesRegex(ValueError, r"\b" + code + r"\b"):
            self.checked(deployments, inventory, source)

    def test_explicit_receiver_and_nic_mapping_is_preserved(self):
        checked = self.checked()
        receiver = checked["instances"][0]["receiver"]
        self.assertEqual(receiver["device"], "mlx5_fake_a")
        self.assertEqual(receiver["destination_mac"], "02:00:00:00:00:0a")

    def test_unknown_receiver_device_is_rejected(self):
        deployments = deployment_example()
        deployments["profiles"]["example-node-a-gpu0"]["receiver"]["device"] = "mlx5_missing"
        self.assert_invalid(deployments, inventory_example(), "RDMA_DEVICE_MISMATCH")

    def test_receiver_mac_and_physical_port_must_match_inventory(self):
        deployments = deployment_example()
        deployments["profiles"]["example-node-a-gpu0"]["receiver"]["destination_mac"] = "02:00:00:00:00:ff"
        self.assert_invalid(deployments, inventory_example(), "NIC_MAC_MISMATCH")
        deployments = deployment_example()
        deployments["profiles"]["example-node-a-gpu0"]["receiver"]["destination_mac"] = "not-a-mac"
        self.assert_invalid(deployments, inventory_example(), "INVALID_MAC")
        deployments = deployment_example()
        deployments["profiles"]["example-node-a-gpu0"]["receiver"]["destination_mac"] = "02:00:00:00:00:0A"
        self.assertEqual(len(self.checked(deployments, inventory_example())["instances"]), 2)
        deployments = deployment_example()
        deployments["profiles"]["example-node-a-gpu0"]["flow"]["physical_port"] = "port-wrong"
        self.assert_invalid(deployments, inventory_example(), "NIC_PORT_MISMATCH")

    def test_verbs_port_two_is_explicitly_unsupported(self):
        inventory = inventory_example()
        inventory["hosts"][0]["nics"]["eth0"]["verbs_port"] = 2
        self.assert_invalid(deployment_example(), inventory, "UNSUPPORTED_VERBS_PORT")

    def test_unused_verbs_port_two_does_not_reject_selected_port_one(self):
        inventory = inventory_example()
        unused = copy.deepcopy(inventory["hosts"][0]["nics"]["eth0"])
        unused.update({
            "physical_port": "port-unused",
            "rdma_device": "mlx5_fake_unused",
            "verbs_port": 2,
            "mac_address": "02:00:00:00:00:0c",
        })
        inventory["hosts"][0]["nics"]["eth1"] = unused
        before = copy.deepcopy(inventory)
        checked = self.checked(inventory=inventory)
        self.assertEqual(len(checked["instances"]), 2)
        self.assertEqual(checked["instances"][0]["flow"]["interface"], "eth0")
        self.assertEqual(inventory, before)

    def test_selected_second_interface_verbs_port_two_is_unsupported(self):
        deployments = deployment_example()
        inventory = inventory_example()
        second = copy.deepcopy(inventory["hosts"][0]["nics"]["eth0"])
        second.update({
            "physical_port": "port-second",
            "rdma_device": "mlx5_fake_second",
            "verbs_port": 2,
            "mac_address": "02:00:00:00:00:0c",
        })
        inventory["hosts"][0]["nics"]["eth1"] = second
        profile = deployments["profiles"]["example-node-a-gpu0"]
        profile["flow"].update({"interface": "eth1", "physical_port": "port-second"})
        profile["receiver"].update({
            "device": "mlx5_fake_second",
            "destination_mac": "02:00:00:00:00:0c",
        })
        with self.assertRaisesRegex(ValueError, r"UNSUPPORTED_VERBS_PORT.*example-node-a-gpu0.*eth1"):
            self.checked(deployments, inventory)

    def test_non_disabled_storage_settings_are_refused(self):
        for field, value, code in (
            ("enabled", True, "STORAGE_ENABLED_UNSUPPORTED"),
            ("blocks_per_file", 1, "STORAGE_BLOCKS_UNSUPPORTED"),
            ("direct_io", True, "DIRECT_IO_UNSUPPORTED"),
        ):
            deployments = deployment_example()
            deployments["profiles"]["example-node-a-gpu0"]["storage"][field] = value
            with self.subTest(field=field):
                self.assert_invalid(deployments, inventory_example(), code)

    def test_gpu_zero_on_different_hosts_is_not_a_collision(self):
        checked = self.checked()
        self.assertEqual(len(checked["instances"]), 2)
        self.assertFalse(checked["status"]["runtime_ready"])

    def test_two_gpus_on_one_host_are_valid_when_uuid_and_resources_are_distinct(self):
        deployments = deployment_example()
        deployments["profiles"]["example-node-b-gpu0"] = copy.deepcopy(
            deployments["profiles"]["example-node-a-gpu0"]
        )
        deployments["profiles"]["example-node-b-gpu0"].update({"gpu_id": 1})
        deployments["profiles"]["example-node-b-gpu0"]["paths"] = {
            "run_dir": "runs/node-a-gpu1",
            "log_dir": "logs/node-a-gpu1",
        }
        deployments["profiles"]["example-node-b-gpu0"]["cpu"] = {
            "receiver": [13],
            "unpack": [14],
            "gpu": [15],
            "workers": [16, 17],
            "sink": [18],
            "control": [19],
        }
        deployments["profiles"]["example-node-b-gpu0"]["numa"] = {
            "receiver": 1,
            "unpack": 1,
            "gpu": 1,
            "workers": 1,
            "sink": 1,
            "control": 1,
        }
        deployments["profiles"]["example-node-b-gpu0"]["rings"] = {
            "raw_key": "0x1101",
            "compute_key": "0x1102",
            "output_key": "0x1103",
        }
        deployments["profiles"]["example-node-b-gpu0"]["flow"]["match"] = {
            "destination_ip": "192.0.2.12",
            "destination_port": 10002,
            "protocol": "UDP",
        }
        source = global_example()
        source["assignments"][1].update(
            {"server_id": "node-a", "gpu_id": 1, "deployment_profile": "example-node-b-gpu0"}
        )
        checked = self.checked(deployments, inventory_example(), source)
        self.assertEqual(len(checked["host_claims"]["fake-host-a"]["gpu_uuids"]), 2)

    def test_cross_numa_gpu_worker_choice_is_preserved_but_unverified(self):
        deployments = deployment_example()
        source = global_example()
        use_second_gpu_on_node_a(deployments, source)
        inventory = inventory_example()
        inventory["hosts"][0]["gpus"]["1"]["numa_node"] = 0
        checked = self.checked(deployments, inventory, source)
        self.assertIn("gpu_numa_affinity", checked["status"]["unverified_prerequisites"])

    def test_duplicate_gpu_uuid_on_same_physical_host_is_rejected(self):
        deployments = deployment_example()
        deployments["profiles"]["example-node-b-gpu0"]["server_id"] = "node-a"
        deployments["profiles"]["example-node-b-gpu0"]["gpu_id"] = 1
        deployments["profiles"]["example-node-b-gpu0"]["receiver"] = {
            "device": "mlx5_fake_a",
            "destination_mac": "02:00:00:00:00:0a",
        }
        inventory = inventory_example()
        inventory["hosts"][0]["gpus"]["1"] = copy.deepcopy(inventory["hosts"][0]["gpus"]["0"])
        source = global_example()
        source["assignments"][1].update(
            {"server_id": "node-a", "gpu_id": 1, "deployment_profile": "example-node-b-gpu0"}
        )
        self.assert_invalid(deployments, inventory, "DUPLICATE_GPU_UUID", source)

    def test_logical_cpu_conflict_is_rejected(self):
        deployments = deployment_example()
        source = global_example()
        use_second_gpu_on_node_a(deployments, source)
        deployments["profiles"]["example-node-b-gpu0"]["cpu"]["receiver"] = [0]
        deployments["profiles"]["example-node-b-gpu0"]["numa"]["receiver"] = 0
        self.assert_invalid(deployments, inventory_example(), "CPU_LOGICAL_CONFLICT", source)

    def test_smt_physical_core_conflict_is_rejected(self):
        deployments = deployment_example()
        source = global_example()
        use_second_gpu_on_node_a(deployments, source)
        deployments["profiles"]["example-node-b-gpu0"]["cpu"]["receiver"] = [6]
        deployments["profiles"]["example-node-b-gpu0"]["numa"]["receiver"] = 0
        self.assert_invalid(deployments, inventory_example(), "CPU_PHYSICAL_CONFLICT", source)

    def test_control_cpu_must_not_consume_data_path_core(self):
        deployments = deployment_example()
        deployments["profiles"]["example-node-a-gpu0"]["cpu"]["control"] = [3]
        self.assert_invalid(deployments, inventory_example(), "CONTROL_CPU_CONFLICT", None)

    def test_default_control_cpu_is_on_a_distinct_physical_core(self):
        checked = self.checked()
        self.assertNotIn("CONTROL_CPU_PHYSICAL_CONFLICT", checked["status"]["unverified_prerequisites"])

    def test_control_smt_sibling_of_data_cpu_is_rejected(self):
        deployments = deployment_example()
        deployments["profiles"]["example-node-a-gpu0"]["cpu"]["control"] = [7]
        deployments["profiles"]["example-node-a-gpu0"]["numa"]["control"] = 0
        self.assert_invalid(deployments, inventory_example(), "CONTROL_CPU_PHYSICAL_CONFLICT")

    def test_cpu_member_type_has_stable_value_error(self):
        deployments = deployment_example()
        deployments["profiles"]["example-node-a-gpu0"]["cpu"]["receiver"] = [{}]
        self.assert_invalid(deployments, inventory_example(), "INVALID_INTEGER")

    def test_missing_cpu_and_wrong_numa_are_rejected(self):
        inventory = inventory_example()
        del inventory["hosts"][0]["cpus"]["1"]
        inventory["hosts"][0]["cpus"]["7"]["smt_siblings"] = [7]
        self.assert_invalid(deployment_example(), inventory, "CPU_NOT_IN_INVENTORY")
        inventory = inventory_example()
        inventory["hosts"][0]["cpus"]["3"]["numa_node"] = 1
        self.assert_invalid(deployment_example(), inventory, "NUMA_MISMATCH")

    def test_worker_count_must_match_worker_cpu_list(self):
        deployments = deployment_example()
        deployments["profiles"]["example-node-a-gpu0"]["execution"]["worker_count"] = 3
        self.assert_invalid(deployments, inventory_example(), "WORKER_COUNT_MISMATCH")

    def test_receiver_wr_num_is_bounded_by_records_per_block(self):
        deployments = deployment_example()
        source = global_example()
        for profile in deployments["profiles"].values():
            profile["execution"].update({"receiver_poll_batch": 32, "receiver_wr_num": 128})
        before = copy.deepcopy(deployments)
        checked = self.checked(deployments, inventory_example(), source)
        self.assertEqual(len(checked["instances"]), 2)
        self.assertEqual(deployments, before)

        for profile_name in deployments["profiles"]:
            invalid = copy.deepcopy(deployments)
            invalid["profiles"][profile_name]["execution"]["receiver_wr_num"] = 129
            before = copy.deepcopy(invalid)
            with self.subTest(profile_name=profile_name):
                self.assert_invalid(invalid, inventory_example(), "RECEIVER_WR_RANGE", source)
            self.assertEqual(invalid, before)

    def test_receiver_wr_num_uses_station_count_not_channel_count(self):
        source = global_example()
        source["observation"]["station_ids"] = [1000, 1001]
        for profile_name in deployment_example()["profiles"]:
            deployments = deployment_example()
            deployments["profiles"][profile_name]["execution"].update(
                {"receiver_poll_batch": 32, "receiver_wr_num": 65}
            )
            with self.subTest(profile_name=profile_name):
                self.assert_invalid(deployments, inventory_example(), "RECEIVER_WR_RANGE", source)

    def test_ring_keys_collide_across_all_stages_on_same_host(self):
        deployments = deployment_example()
        source = global_example()
        use_second_gpu_on_node_a(deployments, source)
        deployments["profiles"]["example-node-b-gpu0"]["rings"]["output_key"] = "0X1001"
        self.assert_invalid(deployments, inventory_example(), "RING_KEY_CONFLICT", source)

    def test_ring_key_spelling_is_normalized_but_different_hosts_are_isolated(self):
        deployments = deployment_example()
        deployments["profiles"]["example-node-b-gpu0"]["rings"] = {
            "raw_key": "0X1001",
            "compute_key": "0x1002",
            "output_key": "0x1003",
        }
        self.assertEqual(len(self.checked(deployments)["instances"]), 2)

    def test_wildcard_and_narrow_flow_overlap_is_rejected(self):
        deployments = deployment_example()
        source = global_example()
        use_second_gpu_on_node_a(deployments, source)
        deployments["profiles"]["example-node-b-gpu0"]["flow"]["match"] = {
            "destination_ip": "192.0.2.10",
            "protocol": "UDP",
        }
        self.assert_invalid(deployments, inventory_example(), "FLOW_OVERLAP", source)

    def test_disjoint_flows_are_accepted(self):
        deployments = deployment_example()
        source = global_example()
        use_second_gpu_on_node_a(deployments, source)
        self.assertEqual(len(self.checked(deployments, inventory_example(), source)["instances"]), 2)

    def test_same_path_on_independent_hosts_is_accepted(self):
        deployments = deployment_example()
        deployments["profiles"]["example-node-b-gpu0"]["paths"] = {
            "run_dir": "runs/shared-name",
            "log_dir": "logs/shared-name",
        }
        deployments["profiles"]["example-node-a-gpu0"]["paths"] = {
            "run_dir": "runs/shared-name",
            "log_dir": "logs/shared-name",
        }
        self.assertEqual(len(self.checked(deployments)["instances"]), 2)

    def test_flow_aliases_of_one_physical_port_still_overlap(self):
        deployments = deployment_example()
        source = global_example()
        use_second_gpu_on_node_a(deployments, source)
        inventory = inventory_example()
        inventory["hosts"][0]["nics"]["eth1"] = copy.deepcopy(inventory["hosts"][0]["nics"]["eth0"])
        deployments["profiles"]["example-node-b-gpu0"]["flow"]["interface"] = "eth1"
        deployments["profiles"]["example-node-b-gpu0"]["flow"]["match"] = copy.deepcopy(
            deployments["profiles"]["example-node-a-gpu0"]["flow"]["match"]
        )
        self.assert_invalid(deployments, inventory, "FLOW_OVERLAP", source)

    def test_host_alias_conflict_is_rejected(self):
        inventory = inventory_example()
        inventory["hosts"][1]["aliases"].append("node-a")
        self.assert_invalid(deployment_example(), inventory, "HOST_ALIAS_CONFLICT")

    def test_path_escape_and_parent_overlap_are_rejected(self):
        deployments = deployment_example()
        deployments["profiles"]["example-node-a-gpu0"]["paths"]["run_dir"] = "../outside"
        self.assert_invalid(deployments, inventory_example(), "PATH_ESCAPE")
        deployments = deployment_example()
        source = global_example()
        use_second_gpu_on_node_a(deployments, source)
        deployments["profiles"]["example-node-b-gpu0"]["paths"]["run_dir"] = "runs"
        self.assert_invalid(deployments, inventory_example(), "PATH_OVERLAP", source)

    def test_aggregate_ram_and_per_gpu_vram_overcommit_are_rejected(self):
        inventory = inventory_example()
        inventory["hosts"][0]["ram_bytes"] = 1_500_000_000
        deployments = deployment_example()
        deployments["profiles"]["example-node-b-gpu0"]["server_id"] = "node-a"
        deployments["profiles"]["example-node-b-gpu0"]["gpu_id"] = 1
        inventory["hosts"][0]["gpus"]["1"] = {
            "uuid": "GPU-FAKE-A-1",
            "vram_bytes": 16 * 1024 * 1024 * 1024,
            "numa_node": 1,
        }
        source = global_example()
        use_second_gpu_on_node_a(deployments, source)
        deployments["profiles"]["example-node-b-gpu0"]["reservation"]["host_ram_bytes"] = 1_000_000_000
        self.assert_invalid(deployments, inventory, "HOST_RAM_OVERCOMMIT", source)
        inventory = inventory_example()
        inventory["hosts"][0]["gpus"]["0"]["vram_bytes"] = 1
        self.assert_invalid(deployment_example(), inventory, "GPU_VRAM_OVERCOMMIT")

    def test_disk_destination_must_be_below_feature_root(self):
        deployments = deployment_example()
        deployments["profiles"]["example-node-a-gpu0"]["consumer"] = {
            "type": "DADA_DBDISK",
            "destination": "/etc/escape",
        }
        self.assert_invalid(deployments, inventory_example(), "PATH_ESCAPE")

    def test_disk_destination_cannot_overlap_a_claimed_path_on_same_host(self):
        deployments = deployment_example()
        deployments["profiles"]["example-node-a-gpu0"]["consumer"] = {
            "type": "DADA_DBDISK",
            "destination": "runs/node-a-gpu0",
        }
        self.assert_invalid(deployments, inventory_example(), "PATH_OVERLAP")

    def test_mtu_exact_boundary_passes_and_oversize_fails(self):
        deployments = deployment_example()
        inventory = inventory_example()
        # 32-byte Project header + 512 * 4 * 1 * 2 CI8 bytes + 28-byte IP/UDP.
        inventory["hosts"][0]["nics"]["eth0"]["mtu"] = 4156
        inventory["hosts"][1]["nics"]["eth0"]["mtu"] = 3132
        self.assertEqual(len(self.checked(deployments, inventory)["instances"]), 2)
        inventory["hosts"][1]["nics"]["eth0"]["mtu"] = 3131
        self.assert_invalid(deployments, inventory, "MTU_OVERSIZE")

    def test_unknown_transport_facts_remain_unverified(self):
        inventory = inventory_example()
        inventory["hosts"][0]["nics"]["eth0"]["transport_overhead_bytes"] = "unknown"
        checked = self.checked(inventory=inventory)
        self.assertFalse(checked["status"]["runtime_ready"])
        self.assertIn("transport_overhead", checked["status"]["unverified_prerequisites"])

    def test_wait_polling_is_preserved_but_unverified(self):
        deployments = deployment_example()
        deployments["profiles"]["example-node-a-gpu0"]["execution"]["polling"] = "WAIT"
        checked = self.checked(deployments)
        self.assertIn("polling_mode", checked["status"]["unverified_prerequisites"])

    def test_unknown_wire_path_does_not_inherit_known_geometry(self):
        source = global_example()
        source["wire"]["profile"] = "made-up/frontend.example-v1.json"
        checked = self.checked(source=source)
        self.assertIn("wire_profile", checked["status"]["unverified_prerequisites"])
        self.assertIsNone(checked["instances"][0]["derived_packet"]["record_bytes"])

    def test_unknown_flow_capability_remains_unverified(self):
        inventory = inventory_example()
        inventory["hosts"][0]["nics"]["eth0"]["supported_match_fields"] = "unknown"
        checked = self.checked(inventory=inventory)
        self.assertIn("flow_capabilities", checked["status"]["unverified_prerequisites"])

    def test_unknown_fields_and_non_finite_numeric_values_are_rejected(self):
        deployments = deployment_example()
        deployments["profiles"]["example-node-a-gpu0"]["unexpected"] = True
        self.assert_invalid(deployments, inventory_example(), "UNKNOWN_FIELD")
        deployments = deployment_example()
        deployments["profiles"]["example-node-a-gpu0"]["execution"]["cuda_slots"] = math.inf
        self.assert_invalid(deployments, inventory_example(), "INVALID_INTEGER")
        inventory = inventory_example()
        inventory["hosts"][0]["ram_bytes"] = float("nan")
        self.assert_invalid(deployment_example(), inventory, "INVALID_INTEGER")

    def test_ip_protocol_and_direct_cuda_contracts_are_rejected(self):
        deployments = deployment_example()
        deployments["profiles"]["example-node-a-gpu0"]["flow"]["match"]["destination_ip"] = "not-an-ip"
        self.assert_invalid(deployments, inventory_example(), "INVALID_IP")
        deployments = deployment_example()
        deployments["profiles"]["example-node-a-gpu0"]["flow"]["match"]["protocol"] = "TCPP"
        self.assert_invalid(deployments, inventory_example(), "INVALID_PROTOCOL")
        deployments = deployment_example()
        deployments["profiles"]["example-node-a-gpu0"]["execution"]["cuda_mode"] = "SYNCHRONOUS_DIRECT"
        deployments["profiles"]["example-node-a-gpu0"]["execution"]["cuda_slots"] = 3
        self.assert_invalid(deployments, inventory_example(), "DIRECT_CUDA_SLOTS")

    def test_missing_profile_or_inventory_is_rejected(self):
        deployments = deployment_example()
        del deployments["profiles"]["example-node-a-gpu0"]
        self.assert_invalid(deployments, inventory_example(), "MISSING_PROFILE")
        inventory = inventory_example()
        del inventory["hosts"][0]
        self.assert_invalid(deployment_example(), inventory, "HOST_NOT_IN_INVENTORY")

    def test_inputs_are_not_mutated_and_result_is_independent(self):
        source = global_example()
        deployments = deployment_example()
        inventory = inventory_example()
        before = (copy.deepcopy(source), copy.deepcopy(deployments), copy.deepcopy(inventory))
        checked = self.checked(deployments, inventory, source)
        self.assertEqual((source, deployments, inventory), before)
        checked["instances"][0]["cpu"]["receiver"].append(99)
        self.assertNotIn(99, deployments["profiles"]["example-node-a-gpu0"]["cpu"]["receiver"])

    def test_schema_and_example_have_matching_fields(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        schema = json.loads((root / "config/multiserver-deployment-v1.schema.json").read_text())
        example = json.loads((root / "config/deployments/multiserver.example.json").read_text())
        self.assertEqual(set(schema["properties"]), set(example))
        self.assertEqual(set(schema["required"]), set(example))
        self.assert_fields_match(schema, example)
        self.assert_schema_patterns_compile(schema)
        self._schema = schema
        self.assert_profile_matches_definition(schema, example["profiles"]["example-node-a-gpu0"])

    def test_schema_match_values_enforce_null_oneof_and_port_bounds(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        self._schema = json.loads((root / "config/multiserver-deployment-v1.schema.json").read_text())
        ipv4 = self._schema["$defs"]["ipv4_value"]
        port = self._schema["$defs"]["port_value"]
        for value in ("192.0.2.1", "*", None):
            self.assert_schema_node(ipv4, value)
        with self.assertRaises(AssertionError):
            self.assert_schema_node(ipv4, "not-an-ip")
        for value in (1, 65535, "*", None):
            self.assert_schema_node(port, value)
        for value in (0, 70000):
            with self.assertRaises(AssertionError):
                self.assert_schema_node(port, value)

    def assert_schema_patterns_compile(self, node):
        if isinstance(node, dict):
            if "pattern" in node:
                re.compile(node["pattern"])
            for child in node.values():
                self.assert_schema_patterns_compile(child)
        elif isinstance(node, list):
            for child in node:
                self.assert_schema_patterns_compile(child)

    def assert_profile_matches_definition(self, schema, profile):
        self.assert_schema_node(schema["properties"]["profiles"]["additionalProperties"], profile)

    def assert_schema_node(self, schema, value):
        if "$ref" in schema:
            self.assert_schema_node(self._definition(schema["$ref"]), value)
            return
        if "oneOf" in schema:
            matches = []
            for candidate in schema["oneOf"]:
                try:
                    self.assert_schema_node(candidate, value)
                    matches.append(candidate)
                except AssertionError:
                    pass
            self.assertEqual(len(matches), 1)
            return
        if "const" in schema:
            self.assertEqual(value, schema["const"])
        if "enum" in schema:
            self.assertIn(value, schema["enum"])
        schema_type = schema.get("type")
        if schema_type == "null":
            self.assertIsNone(value)
        elif schema_type == "object":
            self.assertIsInstance(value, dict)
            self.assertTrue(set(schema.get("required", [])).issubset(set(value)))
            if schema.get("additionalProperties") is False:
                self.assertTrue(set(value).issubset(set(schema.get("properties", {}))))
            for name, child in schema.get("properties", {}).items():
                if name in value:
                    self.assert_schema_node(child, value[name])
            if isinstance(schema.get("additionalProperties"), dict):
                for name, item in value.items():
                    if name not in schema.get("properties", {}):
                        self.assert_schema_node(schema["additionalProperties"], item)
        elif schema_type == "array":
            self.assertIsInstance(value, list)
            for item in value:
                self.assert_schema_node(schema["items"], item)
        elif schema_type == "string":
            self.assertIsInstance(value, str)
            if "pattern" in schema:
                self.assertIsNotNone(re.fullmatch(schema["pattern"], value))
        elif schema_type == "integer":
            self.assertIsInstance(value, int)
            self.assertNotIsInstance(value, bool)
        if schema_type in ("integer", "number"):
            if "minimum" in schema:
                self.assertGreaterEqual(value, schema["minimum"])
            if "maximum" in schema:
                self.assertLessEqual(value, schema["maximum"])

    def _definition(self, ref):
        # Definitions are resolved from the root schema stored on the test.
        return self._schema["$defs"][ref.split("/")[-1]]

    def assert_fields_match(self, schema_node, example_node):
        if schema_node.get("type") == "object" and "properties" in schema_node:
            self.assertIsInstance(example_node, dict)
            self.assertEqual(set(schema_node["properties"]), set(example_node))
            self.assertEqual(set(schema_node.get("required", [])), set(example_node))
            for name, child in schema_node["properties"].items():
                self.assert_fields_match(child, example_node[name])
        if schema_node.get("type") == "array" and "items" in schema_node:
            self.assertIsInstance(example_node, list)
            for item in example_node:
                self.assert_fields_match(schema_node["items"], item)


if __name__ == "__main__":
    unittest.main()
