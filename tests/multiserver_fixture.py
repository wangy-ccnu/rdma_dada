"""Small, deterministic inputs for the multi-server configuration tests."""

def global_example():
    """Return a fresh seven-channel global observation input."""
    return {
        "schema_version": 1,
        "observation": {
            "observation_id": "multi-server-layout-example",
            "utc_start": "2026-09-12-00:00:00",
            "duration_seconds": "1.048576",
            "station_ids": [1000, 1001, 1002, 1003],
            "npol": 1,
            "sample_interval_ps": 1_000_000,
        },
        "channels": {
            "first_channel_id": 100,
            "nchan": 7,
            "first_center_frequency_hz": 1_200_000_000,
            "channel_spacing_hz": 1_000_000,
        },
        "wire": {
            "profile": "packet_formats/frontend.example-v1.json",
            "samples_per_packet": 512,
        },
        "time_grid": {"samples_per_block": 16_384},
        "processing": {
            "weights": {
                "path": "weights/global-f7-p1-a4-b2.npy",
                "order": "FPAB2",
                "weights_id": "example-global-weights-f7-p1-a4-b2",
                "weights_scale": "0.0078125",
                "compute_mode": "FP32",
            },
            "conversion_scale": "0.0078125",
            "product": "POWER",
            "integration": {"length": 128, "operation": "MEAN"},
        },
        "assignments": [
            {
                "server_id": "node-a",
                "gpu_id": 0,
                "first_channel_id": 100,
                "nchan": 4,
                "deployment_profile": "example-node-a-gpu0",
            },
            {
                "server_id": "node-b",
                "gpu_id": 0,
                "first_channel_id": 104,
                "nchan": 3,
                "deployment_profile": "example-node-b-gpu0",
            },
        ],
        "monitoring": {
            "report_interval_ms": 1000,
            "rate_window_ms": 5000,
            "heartbeat_timeout_ms": 5000,
        },
    }


def deployment_example():
    """Return explicit, non-runnable deployment profiles for two fake hosts."""
    return {
        "schema_version": 1,
        "feature_root": "/var/lib/rdma-dada/examples",
        "profiles": {
            "example-node-a-gpu0": {
                "server_id": "node-a",
                "gpu_id": 0,
                "cpu": {
                    "receiver": [0],
                    "unpack": [1],
                    "gpu": [2],
                    "workers": [3, 4],
                    "sink": [5],
                    "control": [12],
                },
                "numa": {
                    "receiver": 0,
                    "unpack": 0,
                    "gpu": 0,
                    "workers": 0,
                    "sink": 0,
                    "control": 1,
                },
                "rings": {
                    "raw_key": "0x1001",
                    "compute_key": "0x1002",
                    "output_key": "0x1003",
                },
                "ring_counts": {"raw": 8, "compute": 8, "output": 8},
                "flow": {
                    "interface": "eth0",
                    "physical_port": "port-a",
                    "match": {
                        "destination_ip": "192.0.2.10",
                        "destination_port": 10000,
                        "protocol": "UDP",
                    },
                },
                "paths": {
                    "run_dir": "runs/node-a-gpu0",
                    "log_dir": "logs/node-a-gpu0",
                },
                "reservation": {
                    "host_ram_bytes": 1_073_741_824,
                    "gpu_vram_bytes": 2_147_483_648,
                },
                "execution": {
                    "worker_count": 2,
                    "cuda_mode": "STAGED_PIPELINE",
                    "cuda_slots": 2,
                    "polling": "BUSY",
                    "receiver_poll_batch": 32,
                    "receiver_wr_num": 64,
                    "unpack_window_blocks": 2,
                    "unpack_queue_capacity": 64,
                },
                "consumer": {
                    "type": "DADA_DBNULL",
                    "destination": "output-ring",
                },
            },
            "example-node-b-gpu0": {
                "server_id": "node-b",
                "gpu_id": 0,
                "cpu": {
                    "receiver": [0],
                    "unpack": [1],
                    "gpu": [2],
                    "workers": [3, 4],
                    "sink": [5],
                    "control": [12],
                },
                "numa": {
                    "receiver": 0,
                    "unpack": 0,
                    "gpu": 0,
                    "workers": 0,
                    "sink": 0,
                    "control": 1,
                },
                "rings": {
                    "raw_key": "0x1001",
                    "compute_key": "0x1002",
                    "output_key": "0x1003",
                },
                "ring_counts": {"raw": 8, "compute": 8, "output": 8},
                "flow": {
                    "interface": "eth0",
                    "physical_port": "port-b",
                    "match": {
                        "destination_ip": "192.0.2.11",
                        "destination_port": 10001,
                        "protocol": "UDP",
                    },
                },
                "paths": {
                    "run_dir": "runs/node-b-gpu0",
                    "log_dir": "logs/node-b-gpu0",
                },
                "reservation": {
                    "host_ram_bytes": 1_073_741_824,
                    "gpu_vram_bytes": 2_147_483_648,
                },
                "execution": {
                    "worker_count": 2,
                    "cuda_mode": "STAGED_PIPELINE",
                    "cuda_slots": 2,
                    "polling": "BUSY",
                    "receiver_poll_batch": 32,
                    "receiver_wr_num": 64,
                    "unpack_window_blocks": 2,
                    "unpack_queue_capacity": 64,
                },
                "consumer": {
                    "type": "DADA_DBNULL",
                    "destination": "output-ring",
                },
            },
        },
    }


def inventory_example():
    """Return a fake observed inventory; it is never a production discovery map."""
    def cpu_map():
        result = {
            str(logical): {
                "socket_id": 0,
                "core_id": logical if logical < 6 else logical - 6,
                "numa_node": 0,
                "smt_siblings": [logical, logical + 6] if logical < 6 else [logical - 6, logical],
            }
            for logical in range(12)
        }
        for logical in range(12, 20):
            result[str(logical)] = {
                "socket_id": 1,
                "core_id": logical - 12,
                "numa_node": 1,
                "smt_siblings": [logical],
            }
        return result

    def host(physical_host_id, alias, gpu_uuid, port, destination):
        gpus = {
            "0": {
                "uuid": gpu_uuid,
                "vram_bytes": 16 * 1024 * 1024 * 1024,
                "numa_node": 0,
            }
        }
        if physical_host_id == "fake-host-a":
            gpus["1"] = {
                "uuid": gpu_uuid + "-1",
                "vram_bytes": 16 * 1024 * 1024 * 1024,
                "numa_node": 1,
            }
        return {
            "physical_host_id": physical_host_id,
            "aliases": [alias, physical_host_id],
            "ram_bytes": 16 * 1024 * 1024 * 1024,
            "cpus": cpu_map(),
            "gpus": gpus,
            "nics": {
                "eth0": {
                    "physical_port": port,
                    "mtu": 9000,
                    "transport_overhead_bytes": 28,
                    "ethernet_frame_overhead_bytes": 18,
                    "supported_match_fields": [
                        "destination_ip",
                        "destination_port",
                        "protocol",
                        "source_ip",
                        "source_port",
                    ],
                }
            },
            "wire_profiles": ["project-vdif-v1"],
            "documentation_address": destination,
        }

    return {
        "schema_version": 1,
        "hosts": [
            host("fake-host-a", "node-a", "GPU-FAKE-A", "port-a", "192.0.2.10"),
            host("fake-host-b", "node-b", "GPU-FAKE-B", "port-b", "192.0.2.11"),
        ],
    }
