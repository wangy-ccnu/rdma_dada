import copy
import json
import math
import pathlib
import unittest

from multiserver_fixture import global_example
from scripts.multiserver.model import validate_global


class GlobalPlanTest(unittest.TestCase):
    def assert_invalid(self, source, code):
        with self.assertRaisesRegex(ValueError, r"\b" + code + r"\b"):
            validate_global(source)

    def test_station_order_and_common_time_are_preserved(self):
        source = global_example()
        before = copy.deepcopy(source)
        plan = validate_global(source)

        self.assertEqual(source, before)
        self.assertEqual(plan["observation"]["station_ids"], [1000, 1001, 1002, 1003])
        self.assertEqual(plan["time_grid"]["samples_per_block"], 16384)
        self.assertEqual([a["nchan"] for a in plan["assignments"]], [4, 3])
        self.assertIsNot(plan, source)
        self.assertIsNot(plan["observation"], source["observation"])
        plan["observation"]["station_ids"].append(9999)
        self.assertEqual(source["observation"]["station_ids"], [1000, 1001, 1002, 1003])

    def test_overlapping_assignment_is_rejected(self):
        source = global_example()
        source["assignments"][1]["first_channel_id"] = 103
        self.assert_invalid(source, "CHANNEL_OVERLAP")

    def test_missing_channel_is_rejected(self):
        source = global_example()
        source["channels"]["nchan"] = 8
        source["assignments"][1]["first_channel_id"] = 105
        self.assert_invalid(source, "CHANNEL_GAP")

    def test_assignment_ranges_must_cover_exact_global_selection(self):
        source = global_example()
        source["assignments"][0]["first_channel_id"] = 101
        self.assert_invalid(source, "CHANNEL_GAP")

    def test_duplicate_station_is_rejected(self):
        source = global_example()
        source["observation"]["station_ids"][1] = 1000
        self.assert_invalid(source, "DUPLICATE_STATION")

    def test_same_gpu_key_is_unique_but_gpu_zero_on_other_server_is_valid(self):
        valid = validate_global(global_example())
        self.assertEqual([(a["server_id"], a["gpu_id"]) for a in valid["assignments"]],
                         [("node-a", 0), ("node-b", 0)])
        source = global_example()
        source["assignments"][1]["server_id"] = "node-a"
        self.assert_invalid(source, "DUPLICATE_INSTANCE")

    def test_allocation_sizes_must_be_balanced(self):
        source = global_example()
        source["assignments"][0]["nchan"] = 5
        source["assignments"][1].update({"first_channel_id": 105, "nchan": 2})
        self.assert_invalid(source, "ALLOCATION_IMBALANCE")

    def test_local_channel_count_zero_or_256_is_rejected(self):
        for nchan in (0, 256):
            source = global_example()
            source["assignments"][0]["nchan"] = nchan
            if nchan == 256:
                source["assignments"][1].update({"first_channel_id": 356, "nchan": 0})
            self.assert_invalid(source, "INVALID_NCHAN")

    def test_channel_id_overflow_is_rejected(self):
        source = global_example()
        source["channels"].update({"first_channel_id": 65535, "nchan": 2})
        self.assert_invalid(source, "CHANNEL_ID_RANGE")

    def test_assignment_channel_id_overflow_is_rejected(self):
        source = global_example()
        source["assignments"][1].update({"first_channel_id": 65535, "nchan": 2})
        self.assert_invalid(source, "CHANNEL_ID_RANGE")

    def test_samples_and_integration_length_must_divide_block(self):
        source = global_example()
        source["processing"]["integration"]["length"] = 127
        self.assert_invalid(source, "TIME_GRID_INTEGRATION_DIVISIBILITY")
        source = global_example()
        source["time_grid"]["samples_per_block"] = 16385
        self.assert_invalid(source, "TIME_GRID_PACKET_DIVISIBILITY")

    def test_block_and_integration_time_products_fit_uint64(self):
        source = global_example()
        source["processing"]["integration"]["length"] = source["time_grid"]["samples_per_block"]
        source["observation"]["sample_interval_ps"] = ((1 << 64) - 1) // 16384
        packet_period_ps = source["observation"]["sample_interval_ps"] * 512
        whole, fraction = divmod(packet_period_ps, 1_000_000_000_000)
        source["observation"]["duration_seconds"] = "{}.{}".format(whole, "{:012d}".format(fraction))
        self.assertEqual(validate_global(source)["processing"]["integration"]["length"], 16384)

    def test_block_time_overflow_is_rejected(self):
        source = global_example()
        source["time_grid"]["samples_per_block"] = 1 << 63
        self.assert_invalid(source, "TIME_GRID_RANGE")

    def test_duration_must_be_a_whole_packet_group(self):
        source = global_example()
        source["observation"]["duration_seconds"] = "1.048577"
        self.assert_invalid(source, "DURATION_GROUP_DIVISIBILITY")

    def test_duration_uses_decimal_not_binary_float(self):
        source = global_example()
        source["observation"]["duration_seconds"] = "0.000512"
        self.assertEqual(validate_global(source)["observation"]["duration_seconds"], "0.000512")
        source["observation"]["duration_seconds"] = 1.048576
        self.assert_invalid(source, "INVALID_DURATION")

    def test_frequency_mapping_rejects_non_integral_local_center(self):
        source = global_example()
        source["channels"]["channel_spacing_hz"] = 1
        self.assert_invalid(source, "FREQUENCY_NON_INTEGRAL")

    def test_frequency_mapping_is_independent_of_sample_interval(self):
        source = global_example()
        source["observation"]["sample_interval_ps"] = 999_999
        source["observation"]["duration_seconds"] = "1.048574951424"
        self.assertEqual(validate_global(source)["observation"]["sample_interval_ps"], 999999)

    def test_invalid_weight_metadata_is_rejected(self):
        for field, value in (("order", "FAPB2"), ("weights_id", ""),
                             ("weights_scale", "NaN"), ("compute_mode", "FP16")):
            source = global_example()
            source["processing"]["weights"][field] = value
            self.assert_invalid(source, "INVALID_WEIGHTS")

    def test_product_and_polarization_contract_is_rejected(self):
        source = global_example()
        source["processing"]["product"] = "UNKNOWN"
        self.assert_invalid(source, "INVALID_PRODUCT")
        source = global_example()
        source["processing"]["product"] = "COHERENCY"
        self.assert_invalid(source, "PRODUCT_POLARIZATION")
        source = global_example()
        source["observation"]["npol"] = 2
        self.assert_invalid(source, "PRODUCT_POLARIZATION")

    def test_coherency_p2_sum_and_tf32_are_valid(self):
        source = global_example()
        source["observation"]["npol"] = 2
        source["processing"]["product"] = "COHERENCY"
        source["processing"]["integration"]["operation"] = "SUM"
        source["processing"]["weights"]["compute_mode"] = "TF32"
        self.assertEqual(validate_global(source), source)

    def test_missing_invalid_and_deep_unknown_fields_are_rejected(self):
        source = global_example()
        del source["monitoring"]
        self.assert_invalid(source, "MISSING_FIELD")
        source = global_example()
        source["observation"]["utc_start"] = "2026-02-30-00:00:00"
        self.assert_invalid(source, "INVALID_UTC_START")
        for section, field in (("weights", "unexpected"), ("integration", "unexpected")):
            source = global_example()
            source["processing"][section][field] = True
            self.assert_invalid(source, "UNKNOWN_FIELD")
        source = global_example()
        source["assignments"][0]["unexpected"] = True
        self.assert_invalid(source, "UNKNOWN_FIELD")

    def test_unknown_fields_are_rejected(self):
        source = global_example()
        source["unexpected"] = True
        self.assert_invalid(source, "UNKNOWN_FIELD")
        source = global_example()
        source["channels"]["spacing_hz"] = 1
        self.assert_invalid(source, "UNKNOWN_FIELD")

    def test_integer_fields_reject_bool_and_invalid_types(self):
        cases = [
            ("observation", "sample_interval_ps", True),
            ("channels", "first_channel_id", 1.0),
            ("wire", "samples_per_packet", "512"),
            ("time_grid", "samples_per_block", False),
            ("processing", "integration", {"length": True, "operation": "MEAN"}),
            ("monitoring", "rate_window_ms", math.inf),
        ]
        for section, field, value in cases:
            source = global_example()
            source[section][field] = value
            self.assert_invalid(source, "INVALID_INTEGER")

    def test_schema_and_example_have_the_same_declared_fields(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        schema = json.loads((root / "config/global-observation-v1.schema.json").read_text())
        example = json.loads((root / "config/global-observation.example.json").read_text())

        self.assertEqual(set(schema["properties"]), set(example))
        self.assertEqual(set(schema["required"]), set(example))
        self.assert_schema_fields_match(schema, example)
        self.assertEqual(validate_global(example), example)

    def assert_schema_fields_match(self, schema_node, example_node):
        if schema_node.get("type") == "object" and "properties" in schema_node:
            self.assertIsInstance(example_node, dict)
            self.assertEqual(set(schema_node["properties"]), set(example_node))
            self.assertEqual(set(schema_node.get("required", [])), set(example_node))
            for name, child_schema in schema_node["properties"].items():
                self.assert_schema_fields_match(child_schema, example_node[name])
        if schema_node.get("type") == "array" and "items" in schema_node:
            self.assertIsInstance(example_node, list)
            for child in example_node:
                self.assert_schema_fields_match(schema_node["items"], child)


if __name__ == "__main__":
    unittest.main()
