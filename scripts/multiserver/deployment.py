"""Pure deployment and same-host isolation validation."""

import copy
import ipaddress
import re
from pathlib import PurePosixPath


_PROFILE_KEYS = (
    "server_id", "gpu_id", "cpu", "numa", "rings", "ring_counts", "flow",
    "receiver", "storage", "paths", "reservation", "execution", "consumer",
)
_CPU_ROLES = ("receiver", "unpack", "gpu", "workers", "sink", "control")
_MATCH_FIELDS = {
    "destination_ip", "destination_port", "source_ip", "source_port", "protocol",
}
_RING_KEYS = ("raw_key", "compute_key", "output_key")
_RING_COUNT_KEYS = ("raw", "compute", "output")
_HEX_KEY_RE = re.compile("^0[xX][0-9a-fA-F]{1,8}$")
_MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_WIRE_PROFILE_NAME = "project-vdif-v1"
_KNOWN_WIRE_PROFILES = {"packet_formats/frontend.example-v1.json", _WIRE_PROFILE_NAME}


def _error(code, path, detail):
    raise ValueError("{}: {}: {}".format(code, path, detail))


def _object(value, path):
    if not isinstance(value, dict):
        _error("INVALID_TYPE", path, "expected object")
    return value


def _exact_keys(value, required, path):
    _object(value, path)
    required = set(required)
    missing = sorted(required - set(value))
    if missing:
        _error("MISSING_FIELD", path + "." + missing[0], "required field")
    unknown = sorted((key for key in value if key not in required), key=lambda item: str(item))
    if unknown:
        _error("UNKNOWN_FIELD", path + "." + str(unknown[0]), "unknown field")


def _string(value, path):
    if not isinstance(value, str) or not value:
        _error("INVALID_STRING", path, "expected non-empty string")
    if any(ord(char) == 0 for char in value):
        _error("INVALID_STRING", path, "NUL is not allowed")
    return value


def _integer(value, path, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int):
        _error("INVALID_INTEGER", path, "expected integer")
    if value < minimum:
        _error("INTEGER_RANGE", path, "must be at least {}".format(minimum))
    return value


def _boolean(value, path):
    if not isinstance(value, bool):
        _error("INVALID_BOOLEAN", path, "expected boolean")
    return value


def _normalize_mac(value, path):
    _string(value, path)
    if not _MAC_RE.fullmatch(value):
        _error("INVALID_MAC", path, "expected six-octet colon-separated MAC address")
    return value.lower()


def _integer_or_unknown(value, path):
    if value == "unknown":
        return value
    return _integer(value, path)


def _nonempty_list(value, path):
    if not isinstance(value, list) or not value:
        _error("INVALID_LIST", path, "expected a non-empty array")
    return value


def _numeric_id(value, path):
    _string(value, path)
    if not value.isdigit():
        _error("INVALID_IDENTIFIER", path, "expected a decimal logical/device identifier")
    return int(value)


def _parse_ring_key(value, path):
    _string(value, path)
    if not _HEX_KEY_RE.fullmatch(value):
        _error("INVALID_RING_KEY", path, "expected 0x-prefixed uint32 key")
    return int(value, 16)


def _validate_posix_relative(value, path):
    _string(value, path)
    if chr(92) in value:
        _error("PATH_ESCAPE", path, "host paths must use POSIX separators")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or ".." in parsed.parts or not parsed.parts:
        _error("PATH_ESCAPE", path, "path must remain below feature_root")
    return parsed


def _validate_feature_root(value):
    _string(value, "feature_root")
    if chr(92) in value or not PurePosixPath(value).is_absolute():
        _error("PATH_ROOT", "feature_root", "must be an absolute POSIX path")
    if ".." in PurePosixPath(value).parts:
        _error("PATH_ROOT", "feature_root", "must not contain traversal")


def _validate_global_shape(global_plan):
    _object(global_plan, "global_plan")
    assignments = global_plan.get("assignments")
    if not isinstance(assignments, list) or not assignments:
        _error("INVALID_ASSIGNMENTS", "global_plan.assignments", "expected validated assignments")
    wire = _object(global_plan.get("wire"), "global_plan.wire")
    _string(wire.get("profile"), "global_plan.wire.profile")
    _integer(wire.get("samples_per_packet"), "global_plan.wire.samples_per_packet", 1)
    observation = _object(global_plan.get("observation"), "global_plan.observation")
    npol = _integer(observation.get("npol"), "global_plan.observation.npol", 1)
    if npol not in (1, 2):
        _error("INVALID_ENUM", "global_plan.observation.npol", "must be 1 or 2")
    time_grid = _object(global_plan.get("time_grid"), "global_plan.time_grid")
    _integer(time_grid.get("samples_per_block"), "global_plan.time_grid.samples_per_block", 1)
    for index, assignment in enumerate(assignments):
        path = "global_plan.assignments.{}".format(index)
        _exact_keys(assignment, ("server_id", "gpu_id", "first_channel_id", "nchan", "deployment_profile"), path)
        _string(assignment["server_id"], path + ".server_id")
        _integer(assignment["gpu_id"], path + ".gpu_id")
        _integer(assignment["nchan"], path + ".nchan", 1)
        _string(assignment["deployment_profile"], path + ".deployment_profile")


def _validate_inventory(inventory):
    _exact_keys(inventory, ("schema_version", "hosts"), "inventory")
    _integer(inventory["schema_version"], "inventory.schema_version", 1)
    if inventory["schema_version"] != 1:
        _error("UNSUPPORTED_SCHEMA", "inventory.schema_version", "must be 1")
    hosts = _nonempty_list(inventory["hosts"], "inventory.hosts")
    host_by_alias = {}
    normalized = []
    for index, host in enumerate(hosts):
        path = "inventory.hosts.{}".format(index)
        _exact_keys(
            host,
            ("physical_host_id", "aliases", "ram_bytes", "cpus", "gpus", "nics", "wire_profiles", "documentation_address"),
            path,
        )
        physical = _string(host["physical_host_id"], path + ".physical_host_id")
        aliases = _nonempty_list(host["aliases"], path + ".aliases")
        alias_set = set()
        for alias_index, alias in enumerate(aliases):
            alias = _string(alias, "{}.aliases.{}".format(path, alias_index))
            if alias in alias_set:
                _error("HOST_ALIAS_CONFLICT", path + ".aliases", "alias is repeated")
            alias_set.add(alias)
            previous = host_by_alias.get(alias)
            if previous is not None and previous != physical:
                _error("HOST_ALIAS_CONFLICT", path + ".aliases", "alias maps to multiple physical hosts")
            host_by_alias[alias] = physical
        if physical not in alias_set:
            _error("HOST_ALIAS_CONFLICT", path + ".aliases", "physical_host_id must be an alias")
        ram = _integer(host["ram_bytes"], path + ".ram_bytes", 1)

        cpus = _object(host["cpus"], path + ".cpus")
        normalized_cpus = {}
        for raw_logical, cpu in cpus.items():
            logical = _numeric_id(raw_logical, path + ".cpus." + str(raw_logical))
            cpu_path = path + ".cpus." + str(raw_logical)
            if logical in normalized_cpus:
                _error("CPU_ID_ALIAS_CONFLICT", cpu_path, "logical CPU identifier is repeated")
            _exact_keys(cpu, ("socket_id", "core_id", "numa_node", "smt_siblings"), cpu_path)
            sibling_ids = [_integer(item, cpu_path + ".smt_siblings") for item in _nonempty_list(cpu["smt_siblings"], cpu_path + ".smt_siblings")]
            if logical not in sibling_ids:
                _error("CPU_TOPOLOGY_INCOMPLETE", cpu_path + ".smt_siblings", "must include logical CPU")
            normalized_cpus[logical] = {
                "socket_id": _integer(cpu["socket_id"], cpu_path + ".socket_id"),
                "core_id": _integer(cpu["core_id"], cpu_path + ".core_id"),
                "numa_node": _integer(cpu["numa_node"], cpu_path + ".numa_node"),
                "smt_siblings": tuple(sibling_ids),
            }
        for logical, cpu in normalized_cpus.items():
            for sibling in cpu["smt_siblings"]:
                if sibling not in normalized_cpus:
                    _error("CPU_TOPOLOGY_INCOMPLETE", path + ".cpus.{}".format(logical), "SMT sibling is absent")

        gpus = _object(host["gpus"], path + ".gpus")
        normalized_gpus = {}
        gpu_uuids = set()
        for raw_gpu, gpu in gpus.items():
            gpu_id = _numeric_id(raw_gpu, path + ".gpus." + str(raw_gpu))
            gpu_path = path + ".gpus." + str(raw_gpu)
            if gpu_id in normalized_gpus:
                _error("GPU_ID_ALIAS_CONFLICT", gpu_path, "GPU identifier is repeated")
            _exact_keys(gpu, ("uuid", "vram_bytes", "numa_node"), gpu_path)
            uuid = _string(gpu["uuid"], gpu_path + ".uuid")
            if uuid in gpu_uuids:
                _error("DUPLICATE_GPU_UUID", gpu_path + ".uuid", "GPU UUID is repeated on physical host")
            gpu_uuids.add(uuid)
            normalized_gpus[gpu_id] = {
                "uuid": uuid,
                "vram_bytes": _integer(gpu["vram_bytes"], gpu_path + ".vram_bytes", 1),
                "numa_node": _integer(gpu["numa_node"], gpu_path + ".numa_node"),
            }

        nics = _object(host["nics"], path + ".nics")
        normalized_nics = {}
        for interface, nic in nics.items():
            nic_path = path + ".nics." + str(interface)
            _string(interface, path + ".nics.interface")
            _exact_keys(
                nic,
                (
                    "physical_port", "rdma_device", "verbs_port", "mac_address", "mtu",
                    "transport_overhead_bytes", "ethernet_frame_overhead_bytes", "supported_match_fields",
                ),
                nic_path,
            )
            normalized_nics[interface] = {
                "physical_port": _string(nic["physical_port"], nic_path + ".physical_port"),
                "rdma_device": _string(nic["rdma_device"], nic_path + ".rdma_device"),
                "verbs_port": _integer(nic["verbs_port"], nic_path + ".verbs_port", 1),
                "mac_address": _normalize_mac(nic["mac_address"], nic_path + ".mac_address"),
                "mtu": _integer(nic["mtu"], nic_path + ".mtu", 68),
                "transport_overhead_bytes": _integer_or_unknown(nic["transport_overhead_bytes"], nic_path + ".transport_overhead_bytes"),
                "ethernet_frame_overhead_bytes": _integer_or_unknown(nic["ethernet_frame_overhead_bytes"], nic_path + ".ethernet_frame_overhead_bytes"),
                "supported_match_fields": (
                    "unknown" if nic["supported_match_fields"] == "unknown" else
                    tuple(_string(item, nic_path + ".supported_match_fields") for item in _nonempty_list(nic["supported_match_fields"], nic_path + ".supported_match_fields"))
                ),
            }
        wire_profiles = _nonempty_list(host["wire_profiles"], path + ".wire_profiles")
        normalized.append({
            "physical_host_id": physical,
            "aliases": tuple(alias_set),
            "ram_bytes": ram,
            "cpus": normalized_cpus,
            "gpus": normalized_gpus,
            "nics": normalized_nics,
            "wire_profiles": tuple(_string(item, path + ".wire_profiles") for item in wire_profiles),
        })
    by_physical = {host["physical_host_id"]: host for host in normalized}
    if len(by_physical) != len(normalized):
        _error("DUPLICATE_PHYSICAL_HOST", "inventory.hosts", "physical_host_id is repeated")
    return by_physical, host_by_alias


def _validate_profile(profile, path):
    _exact_keys(profile, _PROFILE_KEYS, path)
    _string(profile["server_id"], path + ".server_id")
    _integer(profile["gpu_id"], path + ".gpu_id")
    cpu = _object(profile["cpu"], path + ".cpu")
    _exact_keys(cpu, _CPU_ROLES, path + ".cpu")
    for role in _CPU_ROLES:
        values = _nonempty_list(cpu[role], path + ".cpu." + role)
        seen = set()
        for index, logical in enumerate(values):
            _integer(logical, "{}.cpu.{}.{}".format(path, role, index))
            if logical in seen:
                _error("CPU_PROFILE_CONFLICT", path + ".cpu." + role, "logical CPU is repeated")
            seen.add(logical)
    numa = _object(profile["numa"], path + ".numa")
    _exact_keys(numa, _CPU_ROLES, path + ".numa")
    for role in _CPU_ROLES:
        _integer(numa[role], path + ".numa." + role)
    rings = _object(profile["rings"], path + ".rings")
    _exact_keys(rings, _RING_KEYS, path + ".rings")
    normalized_rings = {key: _parse_ring_key(rings[key], path + ".rings." + key) for key in _RING_KEYS}
    ring_counts = _object(profile["ring_counts"], path + ".ring_counts")
    _exact_keys(ring_counts, _RING_COUNT_KEYS, path + ".ring_counts")
    for key in _RING_COUNT_KEYS:
        _integer(ring_counts[key], path + ".ring_counts." + key, 2 if key == "raw" else 1)
    flow = _object(profile["flow"], path + ".flow")
    _exact_keys(flow, ("interface", "physical_port", "match"), path + ".flow")
    _string(flow["interface"], path + ".flow.interface")
    _string(flow["physical_port"], path + ".flow.physical_port")
    match = _object(flow["match"], path + ".flow.match")
    for field, value in match.items():
        if field not in _MATCH_FIELDS:
            _error("UNKNOWN_FIELD", path + ".flow.match." + str(field), "unsupported matching field")
        if value not in ("*", None):
            if field.endswith("port"):
                _integer(value, path + ".flow.match." + field, 1)
                if value > 65535:
                    _error("INTEGER_RANGE", path + ".flow.match." + field, "port exceeds uint16")
            else:
                _string(value, path + ".flow.match." + field)
    _normalize_match(match, path + ".flow.match")
    receiver = _object(profile["receiver"], path + ".receiver")
    _exact_keys(receiver, ("device", "destination_mac"), path + ".receiver")
    _string(receiver["device"], path + ".receiver.device")
    _normalize_mac(receiver["destination_mac"], path + ".receiver.destination_mac")
    storage = _object(profile["storage"], path + ".storage")
    _exact_keys(storage, ("enabled", "blocks_per_file", "direct_io"), path + ".storage")
    if _boolean(storage["enabled"], path + ".storage.enabled"):
        _error(
            "STORAGE_ENABLED_UNSUPPORTED",
            path + ".storage.enabled",
            "disk storage is disabled for this milestone",
        )
    if _integer(storage["blocks_per_file"], path + ".storage.blocks_per_file") != 0:
        _error(
            "STORAGE_BLOCKS_UNSUPPORTED",
            path + ".storage.blocks_per_file",
            "nonzero blocks_per_file is unsupported while storage is disabled",
        )
    if _boolean(storage["direct_io"], path + ".storage.direct_io"):
        _error(
            "DIRECT_IO_UNSUPPORTED",
            path + ".storage.direct_io",
            "direct I/O is unsupported while storage is disabled",
        )
    paths = _object(profile["paths"], path + ".paths")
    _exact_keys(paths, ("run_dir", "log_dir"), path + ".paths")
    for field in ("run_dir", "log_dir"):
        _validate_posix_relative(paths[field], path + ".paths." + field)
    reservation = _object(profile["reservation"], path + ".reservation")
    _exact_keys(reservation, ("host_ram_bytes", "gpu_vram_bytes"), path + ".reservation")
    _integer(reservation["host_ram_bytes"], path + ".reservation.host_ram_bytes", 1)
    _integer(reservation["gpu_vram_bytes"], path + ".reservation.gpu_vram_bytes", 1)
    execution = _object(profile["execution"], path + ".execution")
    _exact_keys(
        execution,
        (
            "worker_count", "cuda_mode", "cuda_slots", "polling",
            "receiver_poll_batch", "receiver_wr_num", "unpack_window_blocks",
            "unpack_queue_capacity",
        ),
        path + ".execution",
    )
    worker_count = _integer(execution["worker_count"], path + ".execution.worker_count", 1)
    if worker_count != len(cpu["workers"]):
        _error("WORKER_COUNT_MISMATCH", path + ".execution.worker_count", "must equal len(cpu.workers)")
    if execution["cuda_mode"] not in ("SYNCHRONOUS_DIRECT", "STAGED_PIPELINE"):
        _error("INVALID_ENUM", path + ".execution.cuda_mode", "unsupported CUDA mode")
    cuda_slots = _integer(execution["cuda_slots"], path + ".execution.cuda_slots", 1)
    if cuda_slots > 4:
        _error("INTEGER_RANGE", path + ".execution.cuda_slots", "must be at most four")
    if execution["cuda_mode"] == "SYNCHRONOUS_DIRECT" and cuda_slots != 1:
        _error("DIRECT_CUDA_SLOTS", path + ".execution.cuda_slots", "direct CUDA requires one slot")
    if execution["polling"] not in ("BUSY", "WAIT"):
        _error("INVALID_ENUM", path + ".execution.polling", "unsupported polling mode")
    poll_batch = _integer(execution["receiver_poll_batch"], path + ".execution.receiver_poll_batch", 1)
    receiver_wr_num = _integer(execution["receiver_wr_num"], path + ".execution.receiver_wr_num", 1)
    if poll_batch > receiver_wr_num:
        _error("RECEIVER_POLL_BATCH_RANGE", path + ".execution.receiver_poll_batch", "must not exceed receiver WR depth")
    _integer(execution["unpack_window_blocks"], path + ".execution.unpack_window_blocks", 2)
    _integer(execution["unpack_queue_capacity"], path + ".execution.unpack_queue_capacity", 1)
    consumer = _object(profile["consumer"], path + ".consumer")
    _exact_keys(consumer, ("type", "destination"), path + ".consumer")
    if consumer["type"] not in ("DADA_DBNULL", "DADA_DBDISK"):
        _error("INVALID_ENUM", path + ".consumer.type", "unsupported consumer")
    destination = _string(consumer["destination"], path + ".consumer.destination")
    if consumer["type"] == "DADA_DBDISK":
        _validate_posix_relative(destination, path + ".consumer.destination")
    return normalized_rings


def _normalize_match(match, path):
    normalized = {}
    for field, value in match.items():
        if value in ("*", None):
            normalized[field] = value
        elif field in ("destination_port", "source_port"):
            normalized[field] = value
        elif field in ("destination_ip", "source_ip"):
            try:
                normalized[field] = str(ipaddress.ip_address(value))
            except ValueError:
                _error("INVALID_IP", path + "." + field, "expected an IPv4 address")
            if ":" in normalized[field]:
                _error("INVALID_IP", path + "." + field, "IPv6 is not supported")
        elif field == "protocol":
            protocol = value.upper()
            if protocol not in ("UDP", "TCP"):
                _error("INVALID_PROTOCOL", path + "." + field, "expected UDP or TCP")
            normalized[field] = protocol
    return normalized


def _flow_values_overlap(left, right):
    for field in set(left) | set(right):
        left_value, right_value = left.get(field), right.get(field)
        if left_value in ("*", None) or right_value in ("*", None):
            continue
        if left_value != right_value:
            return False
    return True


def validate_deployment(global_plan, profiles, inventory):
    """Validate deployment claims without performing runtime resource actions."""
    _validate_global_shape(global_plan)
    _exact_keys(profiles, ("schema_version", "feature_root", "profiles"), "deployment")
    _integer(profiles["schema_version"], "deployment.schema_version", 1)
    if profiles["schema_version"] != 1:
        _error("UNSUPPORTED_SCHEMA", "deployment.schema_version", "must be 1")
    _validate_feature_root(profiles["feature_root"])
    profile_map = _object(profiles["profiles"], "deployment.profiles")
    if not profile_map:
        _error("INVALID_PROFILES", "deployment.profiles", "expected at least one profile")
    validated_rings = {}
    for name, profile in profile_map.items():
        _string(name, "deployment.profiles." + str(name))
        validated_rings[name] = _validate_profile(profile, "deployment.profiles." + str(name))

    hosts, host_by_alias = _validate_inventory(inventory)
    assignments = global_plan["assignments"]
    wire_profile = global_plan["wire"]["profile"]
    samples_per_packet = global_plan["wire"]["samples_per_packet"]
    samples_per_block = global_plan["time_grid"]["samples_per_block"]
    npol = global_plan["observation"]["npol"]
    unverified = {
        "fresh_host_readiness",
        "compiler_memory_budget",
        "algorithm_memory_demand",
        "filesystem_realpath_ownership",
    }
    resolved, host_claims = [], {}
    used_paths, used_rings, used_flows, used_gpus, ram_totals = [], {}, {}, {}, {}

    for index, assignment in enumerate(assignments):
        assignment_path = "global_plan.assignments.{}".format(index)
        profile_name = assignment.get("deployment_profile")
        if profile_name not in profile_map:
            _error("MISSING_PROFILE", assignment_path + ".deployment_profile", "profile is not declared")
        profile = profile_map[profile_name]
        profile_path = "deployment.profiles." + str(profile_name)
        if profile["server_id"] != assignment.get("server_id") or profile["gpu_id"] != assignment.get("gpu_id"):
            _error("PROFILE_INSTANCE_MISMATCH", profile_path, "server_id/gpu_id differs from global assignment")
        records_per_block = (samples_per_block // samples_per_packet) * len(
            global_plan["observation"]["station_ids"]
        )
        receiver_wr_num = profile["execution"]["receiver_wr_num"]
        if receiver_wr_num > records_per_block:
            _error(
                "RECEIVER_WR_RANGE",
                profile_path + ".execution.receiver_wr_num",
                "must not exceed records_per_block ({})".format(records_per_block),
            )
        physical_host = host_by_alias.get(assignment["server_id"])
        if physical_host is None:
            _error("HOST_NOT_IN_INVENTORY", assignment_path + ".server_id", "no physical host matches server alias")
        host = hosts[physical_host]
        gpu_id = assignment["gpu_id"]
        if gpu_id not in host["gpus"]:
            _error("GPU_NOT_IN_INVENTORY", assignment_path + ".gpu_id", "GPU id is absent on physical host")
        gpu = host["gpus"][gpu_id]
        instance_key = (physical_host, gpu_id)
        if instance_key in used_gpus:
            _error("GPU_ID_CONFLICT", assignment_path + ".gpu_id", "GPU slot is assigned more than once on host")
        if gpu["uuid"] in used_gpus.get((physical_host, "uuids"), set()):
            _error("DUPLICATE_GPU_UUID", assignment_path + ".gpu_id", "GPU UUID is assigned more than once on host")
        used_gpus.setdefault((physical_host, "uuids"), set()).add(gpu["uuid"])
        used_gpus[instance_key] = profile_name

        data_path_cpus = set(profile["cpu"]["receiver"] + profile["cpu"]["unpack"] + profile["cpu"]["gpu"] + profile["cpu"]["workers"] + profile["cpu"]["sink"])
        if data_path_cpus & set(profile["cpu"]["control"]):
            _error("CONTROL_CPU_CONFLICT", profile_path + ".cpu.control", "control CPU consumes data-path core")
        all_profile_cpus = set()
        for role in _CPU_ROLES:
            role_cpus = set(profile["cpu"][role])
            if all_profile_cpus & role_cpus:
                _error("CPU_PROFILE_CONFLICT", profile_path + ".cpu." + role, "logical CPU is shared within instance")
            all_profile_cpus.update(role_cpus)
        cpu_claims = []
        for role in _CPU_ROLES:
            for logical in profile["cpu"][role]:
                if logical not in host["cpus"]:
                    _error("CPU_NOT_IN_INVENTORY", profile_path + ".cpu." + role, "logical CPU is absent")
                cpu_info = host["cpus"][logical]
                if cpu_info["numa_node"] != profile["numa"][role]:
                    _error("NUMA_MISMATCH", profile_path + ".numa." + role, "CPU NUMA does not match declaration")
                cpu_claims.append((logical, (cpu_info["socket_id"], cpu_info["core_id"])))
        data_physical = {
            physical for logical, physical in cpu_claims
            if logical in data_path_cpus
        }
        control_physical = {
            physical for logical, physical in cpu_claims
            if logical in set(profile["cpu"]["control"])
        }
        if data_physical & control_physical:
            _error(
                "CONTROL_CPU_PHYSICAL_CONFLICT",
                profile_path + ".cpu.control",
                "control CPU consumes data-path physical core",
            )
        if gpu["numa_node"] != profile["numa"]["gpu"]:
            unverified.add("gpu_numa_affinity")
        claim_logical = {logical for logical, _physical in cpu_claims}
        claim_physical = {physical for _logical, physical in cpu_claims}
        prior_claim = host_claims.get(physical_host)
        if prior_claim is not None:
            if claim_logical & set(prior_claim["logical_cpus"]):
                _error("CPU_LOGICAL_CONFLICT", profile_path + ".cpu", "logical CPU is shared across instances")
            if claim_physical & {tuple(item) for item in prior_claim["physical_cores"]}:
                _error("CPU_PHYSICAL_CONFLICT", profile_path + ".cpu", "physical core/SMT sibling is shared")

        normalized_rings = validated_rings[profile_name]
        if profile["execution"]["polling"] == "WAIT":
            unverified.add("polling_mode")
        for stage, numeric_key in normalized_rings.items():
            if (physical_host, numeric_key) in used_rings:
                _error("RING_KEY_CONFLICT", profile_path + ".rings." + stage, "ring key is shared on host")
            used_rings[(physical_host, numeric_key)] = (profile_name, stage)
        path_claims = [
            ("paths." + field, profile["paths"][field])
            for field in ("run_dir", "log_dir")
        ]
        if profile["consumer"]["type"] == "DADA_DBDISK":
            path_claims.append(("consumer.destination", profile["consumer"]["destination"]))
        for path_field, raw_path in path_claims:
            relative = _validate_posix_relative(raw_path, profile_path + "." + path_field)
            path_value = PurePosixPath(profiles["feature_root"]).joinpath(relative)
            for prior_host, prior_path, _prior_instance in used_paths:
                if prior_host != physical_host:
                    continue
                if path_value == prior_path or prior_path in path_value.parents or path_value in prior_path.parents:
                    _error("PATH_OVERLAP", profile_path + "." + path_field, "task-owned paths overlap")
            used_paths.append((physical_host, path_value, profile_name))

        nic = host["nics"].get(profile["flow"]["interface"])
        if nic is None:
            _error("NIC_NOT_IN_INVENTORY", profile_path + ".flow.interface", "interface is absent")
        if nic["verbs_port"] != 1:
            _error(
                "UNSUPPORTED_VERBS_PORT",
                profile_path + ".flow.interface",
                "selected interface {} has verbs port {}; this runtime supports verbs port 1 only".format(
                    profile["flow"]["interface"], nic["verbs_port"],
                ),
            )
        if profile["receiver"]["device"] != nic["rdma_device"]:
            _error(
                "RDMA_DEVICE_MISMATCH",
                profile_path + ".receiver.device",
                "receiver device is not the selected inventory NIC's RDMA device",
            )
        if _normalize_mac(profile["receiver"]["destination_mac"], profile_path + ".receiver.destination_mac") != nic["mac_address"]:
            _error(
                "NIC_MAC_MISMATCH",
                profile_path + ".receiver.destination_mac",
                "receiver MAC differs from the selected inventory NIC",
            )
        if nic["physical_port"] != profile["flow"]["physical_port"]:
            _error("NIC_PORT_MISMATCH", profile_path + ".flow.physical_port", "physical port differs from inventory")
        match = profile["flow"]["match"]
        normalized_match = _normalize_match(match, profile_path + ".flow.match")
        if nic["supported_match_fields"] == "unknown":
            unverified.add("flow_capabilities")
        else:
            for field in match:
                if field not in nic["supported_match_fields"]:
                    _error("FLOW_UNSUPPORTED_FIELD", profile_path + ".flow.match." + field, "NIC does not support matcher")
        flow_key = (physical_host, profile["flow"]["physical_port"])
        for prior_match, _prior_instance in used_flows.get(flow_key, []):
            if _flow_values_overlap(prior_match, normalized_match):
                _error("FLOW_OVERLAP", profile_path + ".flow.match", "receive rules overlap on physical port")
        used_flows.setdefault(flow_key, []).append((copy.deepcopy(normalized_match), profile_name))

        if wire_profile in _KNOWN_WIRE_PROFILES:
            if _WIRE_PROFILE_NAME not in host["wire_profiles"]:
                unverified.add("wire_profile_inventory")
            record_bytes = 32 + samples_per_packet * assignment["nchan"] * npol * 2
            if nic["transport_overhead_bytes"] == "unknown":
                unverified.add("transport_overhead")
                ip_packet_bytes = None
            else:
                ip_packet_bytes = record_bytes + nic["transport_overhead_bytes"]
                if ip_packet_bytes > nic["mtu"]:
                    _error("MTU_OVERSIZE", profile_path + ".flow", "IP packet exceeds declared L3 MTU")
        else:
            unverified.add("wire_profile")
            record_bytes, ip_packet_bytes = None, None
        if nic["ethernet_frame_overhead_bytes"] == "unknown":
            unverified.add("ethernet_frame_overhead")
        ethernet_frame_bytes = None
        if ip_packet_bytes is not None and nic["ethernet_frame_overhead_bytes"] != "unknown":
            ethernet_frame_bytes = ip_packet_bytes + nic["ethernet_frame_overhead_bytes"]

        ram_totals[physical_host] = ram_totals.get(physical_host, 0) + profile["reservation"]["host_ram_bytes"]
        if ram_totals[physical_host] > host["ram_bytes"]:
            _error("HOST_RAM_OVERCOMMIT", profile_path + ".reservation.host_ram_bytes", "host RAM reservation exceeds capacity")
        if profile["reservation"]["gpu_vram_bytes"] > gpu["vram_bytes"]:
            _error("GPU_VRAM_OVERCOMMIT", profile_path + ".reservation.gpu_vram_bytes", "GPU VRAM reservation exceeds capacity")
        claim = host_claims.setdefault(physical_host, {"gpu_uuids": [], "logical_cpus": [], "physical_cores": [], "ring_keys": []})
        claim["gpu_uuids"].append(gpu["uuid"])
        claim["logical_cpus"].extend(sorted(claim_logical))
        claim["physical_cores"].extend(sorted(claim_physical))
        claim["ring_keys"].extend(sorted(normalized_rings.values()))
        instance = copy.deepcopy(profile)
        instance.update({
            "deployment_profile": profile_name,
            "physical_host_id": physical_host,
            "gpu_uuid": gpu["uuid"],
            "derived_packet": {
                "record_bytes": record_bytes,
                "ip_packet_bytes": ip_packet_bytes,
                "ethernet_frame_bytes": ethernet_frame_bytes,
                "l3_mtu": nic["mtu"],
                "ethernet_frame_overhead_bytes": nic["ethernet_frame_overhead_bytes"],
            },
        })
        resolved.append(instance)

    for claim in host_claims.values():
        claim["gpu_uuids"] = list(claim["gpu_uuids"])
        claim["logical_cpus"] = sorted(set(claim["logical_cpus"]))
        claim["physical_cores"] = [list(item) for item in sorted(set(tuple(item) for item in claim["physical_cores"]))]
        claim["ring_keys"] = sorted(set(claim["ring_keys"]))
    return {
        "schema_version": 1,
        "feature_root": profiles["feature_root"],
        "instances": resolved,
        "host_claims": copy.deepcopy(host_claims),
        "status": {
            "static_valid": True,
            "runtime_ready": False,
            "unverified_prerequisites": sorted(unverified),
        },
    }
